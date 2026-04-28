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
        fixed_rate DECIMAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Check if fixed_rate exists (for migrations)
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='apps' AND column_name='fixed_rate'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE apps ADD COLUMN fixed_rate DECIMAL DEFAULT 0.0")

    # Messages table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        vynfy_message_id TEXT UNIQUE NOT NULL,
        app_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        recipient TEXT,
        content TEXT,
        vynfy_status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_app FOREIGN KEY (app_id) REFERENCES apps (id)
    )
    ''')

    # Migration for messages table columns
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='messages' AND column_name='recipient'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE messages ADD COLUMN recipient TEXT")
        cursor.execute("ALTER TABLE messages ADD COLUMN content TEXT")
    
    conn.commit()
    cursor.close()
    conn.close()

class Database:
    def __init__(self):
        # We don't initialize on every init, just ensure tables exist
        # init_db() is called in main.py
        pass

    # --- App Management ---
    def create_app(self, name: str, api_key: str, webhook_url: Optional[str] = None, sms_limit: int = 1000, otp_limit: int = 100, fixed_rate: float = 0.0):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO apps (name, api_key, webhook_url, sms_limit, otp_limit, fixed_rate) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (name, api_key, webhook_url, sms_limit, otp_limit, fixed_rate)
            )
            app_id = cursor.fetchone()['id']
            conn.commit()
            return app_id
        finally:
            cursor.close()
            conn.close()

    def update_app(self, app_id: int, updates: Dict[str, Any]):
        if not updates:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            fields = []
            values = []
            for k, v in updates.items():
                fields.append(f"{k} = %s")
                values.append(v)
            values.append(app_id)
            query = f"UPDATE apps SET {', '.join(fields)} WHERE id = %s"
            cursor.execute(query, tuple(values))
            conn.commit()
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
    def store_message(self, vynfy_message_id: str, app_id: int, message_type: str, recipient: Optional[str] = None, content: Optional[str] = None):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO messages (vynfy_message_id, app_id, type, recipient, content) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (vynfy_message_id) DO NOTHING",
                (vynfy_message_id, app_id, message_type, recipient, content)
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

    def get_pending_messages(self, limit: int = 50):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            # Fetch messages that are not in terminal states
            cursor.execute("""
                SELECT * FROM messages 
                WHERE vynfy_status IS NULL 
                OR vynfy_status NOT IN ('delivered', 'failed', 'expired', 'verified')
                LIMIT %s
            """, (limit,))
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    def update_message_status(self, vynfy_message_id: str, status: str):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE messages SET vynfy_status = %s WHERE vynfy_message_id = %s",
                (status, vynfy_message_id)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def reset_app_usage(self, app_id: int):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE apps SET sms_used = 0, otp_used = 0 WHERE id = %s", (app_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def delete_app(self, app_id: int):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            # Delete messages first due to FK
            cursor.execute("DELETE FROM messages WHERE app_id = %s", (app_id,))
            cursor.execute("DELETE FROM apps WHERE id = %s", (app_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()
