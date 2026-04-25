import psycopg2
from psycopg2.extras import RealDictCursor
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    # psycopg2 expects 'postgresql://' but many services provide 'postgres://'
    url = DATABASE_URL
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Apps table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS apps (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        api_key TEXT UNIQUE NOT NULL,
        webhook_url TEXT,
        sms_limit INTEGER DEFAULT 1000,
        otp_limit INTEGER DEFAULT 100,
        sms_used INTEGER DEFAULT 0,
        otp_used INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Messages table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        vynfy_message_id TEXT UNIQUE NOT NULL,
        app_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        vynfy_status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_app FOREIGN KEY (app_id) REFERENCES apps (id)
    )
    ''')
    
    conn.commit()
    cursor.close()
    conn.close()

class Database:
    def __init__(self):
        # We don't initialize on every init, just ensure tables exist
        # init_db() is called in main.py
        pass

    # --- App Management ---
    def create_app(self, name: str, api_key: str, webhook_url: Optional[str] = None, sms_limit: int = 1000, otp_limit: int = 100):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO apps (name, api_key, webhook_url, sms_limit, otp_limit) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (name, api_key, webhook_url, sms_limit, otp_limit)
            )
            app_id = cursor.fetchone()['id']
            conn.commit()
            return app_id
        finally:
            cursor.close()
            conn.close()

    def get_app_by_api_key(self, api_key: str):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM apps WHERE api_key = %s", (api_key,))
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

    def get_all_apps(self):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM apps ORDER BY created_at DESC")
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    # --- Usage Tracking ---
    def increment_usage(self, app_id: int, usage_type: str, amount: int = 1):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            if usage_type == 'sms':
                cursor.execute("UPDATE apps SET sms_used = sms_used + %s WHERE id = %s", (amount, app_id))
            elif usage_type == 'otp':
                cursor.execute("UPDATE apps SET otp_used = otp_used + %s WHERE id = %s", (amount, app_id))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    # --- Message Mapping ---
    def store_message(self, vynfy_message_id: str, app_id: int, message_type: str):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO messages (vynfy_message_id, app_id, type) VALUES (%s, %s, %s) ON CONFLICT (vynfy_message_id) DO NOTHING",
                (vynfy_message_id, app_id, message_type)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def get_app_by_message_id(self, vynfy_message_id: str):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT apps.* FROM apps 
                JOIN messages ON apps.id = messages.app_id 
                WHERE messages.vynfy_message_id = %s
            """, (vynfy_message_id,))
            return cursor.fetchone()
        finally:
            cursor.close()
            conn.close()
