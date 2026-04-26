from fastapi import FastAPI, HTTPException, Request, Depends, Header
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import httpx
import os
import uuid
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from vynfy_service import VynfyService
from database import Database, init_db, get_connection
import asyncio

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB on startup
    init_db()
    # Start background polling
    asyncio.create_task(status_polling_loop())
    yield

app = FastAPI(
    title="Vynfy Bridge Microservice",
    description="A bridge for Vynfy API with multi-app support and usage limits",
    version="2.0.0",
    lifespan=lifespan
)

db = Database()

VYNFY_API_KEY = os.getenv("VYNFY_API_KEY")
MASTER_KEY = os.getenv("MASTER_KEY", "venfy_master_secret_2024")

if not VYNFY_API_KEY or VYNFY_API_KEY == "your-api-key-here":
    print("CRITICAL: VYNFY_API_KEY is not properly set in .env")

def get_vynfy_service():
    if not VYNFY_API_KEY or VYNFY_API_KEY == "your-api-key-here":
        raise HTTPException(status_code=500, detail="Vynfy API key not configured on server")
    return VynfyService(api_key=VYNFY_API_KEY)

# --- Authentication Dependency ---
async def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header missing")
    
    # Allow Master Key for dashboard/admin use of these endpoints
    if x_api_key == MASTER_KEY:
        return {"id": 0, "name": "Admin", "sms_used": 0, "sms_limit": 999999, "otp_used": 0, "otp_limit": 999999}
        
    app_data = db.get_app_by_api_key(x_api_key)
    if not app_data:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    return app_data

# --- Models ---
class SmsSendRequest(BaseModel):
    sender: str = Field(..., max_length=11)
    recipients: Any 
    message: str = Field(..., max_length=650)
    metadata: Optional[Dict[str, Any]] = None

class SmsScheduleRequest(SmsSendRequest):
    schedule_time: str

class OtpGenerateRequest(BaseModel):
    number: str
    sender_id: str = Field(..., max_length=11)
    message: str = Field(..., max_length=160)
    medium: str = "sms"
    otp_type: str = "numeric"
    expiry: int = 5
    length: int = 6

class OtpVerifyRequest(BaseModel):
    number: str
    code: str

class AppCreateRequest(BaseModel):
    name: str
    webhook_url: Optional[str] = None
    sms_limit: int = 1000
    otp_limit: int = 100

class SenderIdRegisterRequest(BaseModel):
    sender_name: str = Field(..., max_length=11)
    purpose: str

# --- Error Handler helper ---
def handle_error(e: Exception):
    err_msg = str(e)
    if isinstance(e, httpx.HTTPStatusError):
        try:
            error_details = e.response.json()
            err_msg = f"Vynfy API Error: {error_details}"
        except ValueError:
            err_msg = f"Vynfy API Error ({e.response.status_code}): {e.response.text}"
        raise HTTPException(status_code=e.response.status_code, detail=err_msg)
    
    print(f"Internal Bridge Error: {type(e).__name__} - {err_msg}")
    raise HTTPException(status_code=500, detail=f"Internal Bridge Error: {err_msg}")

@app.get("/health")
def health_check():
    return {"status": "OK", "service": "Vynfy Bridge Microservice"}

# --- Dashboard serving ---
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_dashboard():
    dashboard_path = os.path.join("static", "index.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return """
    <html>
        <body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh;">
            <h1>Venfy Bridge is Running. Dashboard not found at static/index.html</h1>
        </body>
    </html>
    """

@app.get("/health")
def health_check():
    return {"status": "OK", "service": "Vynfy Bridge Microservice"}

# ======================
# Bridge Admin Endpoints
# ======================

@app.post("/admin/apps", tags=["Admin"])
async def create_app(request: AppCreateRequest, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    new_api_key = f"vf_{uuid.uuid4().hex}"
    app_id = db.create_app(
        name=request.name,
        api_key=new_api_key,
        webhook_url=request.webhook_url,
        sms_limit=request.sms_limit,
        otp_limit=request.otp_limit
    )
    return {"app_id": app_id, "api_key": new_api_key, "name": request.name}

@app.get("/admin/apps", tags=["Admin"])
async def list_apps(x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    apps = db.get_all_apps()
    return [dict(app) for app in apps]

@app.post("/admin/apps/{app_id}/reset", tags=["Admin"])
async def reset_app_usage(app_id: int, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    db.reset_app_usage(app_id)
    return {"message": "Usage reset successfully"}

@app.delete("/admin/apps/{app_id}", tags=["Admin"])
async def delete_app(app_id: int, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    db.delete_app(app_id)
    return {"message": "App deleted successfully"}

@app.get("/admin/logs", tags=["Admin"])
async def get_message_logs(limit: int = 50, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT m.*, a.name as app_name 
            FROM messages m 
            JOIN apps a ON m.app_id = a.id 
            ORDER BY m.created_at DESC 
            LIMIT %s
        """, (limit,))
        logs = cursor.fetchall()
        return [dict(log) for log in logs]
    finally:
        cursor.close()
        conn.close()

@app.get("/admin/balance", tags=["Admin"])
async def get_master_balance(service: VynfyService = Depends(get_vynfy_service), x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    sms_val = "Error"
    otp_val = "Error"
    
    try:
        sms = await service.check_sms_balance()
        if isinstance(sms, dict):
            sms_val = sms.get('balance', {}).get('remaining') if isinstance(sms.get('balance'), dict) else sms.get('balance')
    except Exception as e:
        print(f"Failed to fetch SMS balance: {str(e)}")

    try:
        otp = await service.check_otp_balance()
        if isinstance(otp, dict):
            otp_val = otp.get('balance', {}).get('remaining') if isinstance(otp.get('balance'), dict) else otp.get('balance')
    except Exception as e:
        print(f"Failed to fetch OTP balance: {str(e)}")
        
    return {
        "sms": sms_val if sms_val is not None else "N/A",
        "otp": otp_val if otp_val is not None else "N/A"
    }

@app.get("/admin/sync", tags=["Admin"])
async def sync_vynfy_statuses(service: VynfyService = Depends(get_vynfy_service), x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    pending = db.get_pending_messages(limit=20)
    results = []
    
    for msg in pending:
        try:
            msg_id = msg['vynfy_message_id']
            msg_type = msg['type']
            
            if msg_type == 'sms':
                status_data = await service.check_sms_status(msg_id)
                # Note: Vynfy status response structure check
                new_status = status_data.get('data', {}).get('status') or status_data.get('status')
            else:
                status_data = await service.check_otp_status(msg_id)
                new_status = status_data.get('otp', {}).get('status') or status_data.get('status')
            
            if new_status:
                db.update_message_status(msg_id, new_status)
                results.append({"id": msg_id, "status": new_status})
        except Exception as e:
            print(f"Error syncing status for {msg['vynfy_message_id']}: {str(e)}")
            
    return {"synced_count": len(results), "updates": results}

# Background task loop
async def status_polling_loop():
    while True:
        try:
            # Create a service instance manually for the background task
            if VYNFY_API_KEY and VYNFY_API_KEY != "your-api-key-here":
                service = VynfyService(api_key=VYNFY_API_KEY)
                pending = db.get_pending_messages(limit=10)
                for msg in pending:
                    msg_id = msg['vynfy_message_id']
                    if msg['type'] == 'sms':
                        data = await service.check_sms_status(msg_id)
                        status = data.get('data', {}).get('status') or data.get('status')
                    else:
                        data = await service.check_otp_status(msg_id)
                        status = data.get('otp', {}).get('status') or data.get('status')
                    
                    if status:
                        db.update_message_status(msg_id, status)
        except Exception as e:
            print(f"Background sync error: {str(e)}")
        
        await asyncio.sleep(300) # Poll every 5 minutes

# ======================
# SMS Endpoints (Vynfy v1 Match)
# ======================

@app.get("/api/v1/check/balance")
async def check_sms_balance(
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.check_sms_balance()
    except Exception as e:
        handle_error(e)

@app.post("/api/v1/send")
async def send_sms(
    request: SmsSendRequest, 
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    if app_data['sms_used'] >= app_data['sms_limit']:
        raise HTTPException(status_code=402, detail="SMS limit reached for this application")

    try:
        recipients = request.recipients
        if isinstance(recipients, str):
            recipients = [recipients]
            
        result = await service.send_sms(
            sender=request.sender,
            recipients=recipients,
            message=request.message,
            metadata=request.metadata
        )
        
        # Track message mapping for webhooks
        if result.get("success"):
            # Check both 'job_id' and 'message_id' in case Vynfy returns either
            data_block = result.get("data", {})
            job_id = data_block.get("job_id") or data_block.get("message_id")
            
            if job_id:
                print(f"[STORE] Storing job_id: {job_id} for app: {app_data['name']}")
                db.store_message(job_id, app_data['id'], 'sms')
                db.increment_usage(app_data['id'], 'sms', len(recipients))
            else:
                print(f"[STORE WARNING] No job_id found in Vynfy response: {result}")
        
        return result
    except Exception as e:
        handle_error(e)

@app.post("/schedule/v1/send")
async def schedule_sms(
    request: SmsScheduleRequest, 
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    if app_data['sms_used'] >= app_data['sms_limit']:
        raise HTTPException(status_code=402, detail="SMS limit reached for this application")

    try:
        recipients = request.recipients
        if isinstance(recipients, str):
            recipients = [recipients]

        result = await service.schedule_sms(
            sender=request.sender,
            recipients=recipients,
            message=request.message,
            schedule_time=request.schedule_time,
            metadata=request.metadata
        )
        
        if result.get("success") and "data" in result:
            job_id = result["data"].get("job_id")
            if job_id:
                db.store_message(job_id, app_data['id'], 'sms')
                db.increment_usage(app_data['id'], 'sms', len(recipients))
                
        return result
    except Exception as e:
        handle_error(e)

@app.get("/api/v1/status/{task_id}")
async def check_sms_status(
    task_id: str, 
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.check_sms_status(task_id)
    except Exception as e:
        handle_error(e)

# ======================
# OTP Endpoints (Vynfy Match)
# ======================

@app.post("/otp/generate")
async def generate_otp(
    request: OtpGenerateRequest, 
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    if app_data['otp_used'] >= app_data['otp_limit']:
        raise HTTPException(status_code=402, detail="OTP limit reached for this application")

    try:
        result = await service.generate_otp(
            number=request.number,
            sender_id=request.sender_id,
            message=request.message,
            medium=request.medium,
            otp_type=request.otp_type,
            expiry=request.expiry,
            length=request.length
        )
        
        if result.get("success"):
            otp_id = str(result.get("otp_id"))
            if otp_id:
                db.store_message(otp_id, app_data['id'], 'otp')
                db.increment_usage(app_data['id'], 'otp', 1)
                
        return result
    except Exception as e:
        handle_error(e)

@app.post("/otp/verify")
async def verify_otp(
    request: OtpVerifyRequest, 
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.verify_otp(
            number=request.number,
            code=request.code
        )
    except Exception as e:
        handle_error(e)

@app.get("/otp/balance")
async def check_otp_balance(
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.check_otp_balance()
    except Exception as e:
        handle_error(e)

@app.get("/otp/status/{otp_id}")
async def check_otp_status(
    otp_id: str, 
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.check_otp_status(otp_id)
    except Exception as e:
        handle_error(e)

# ======================
# Sender ID Endpoints (Vynfy Match)
# ======================

@app.post("/sender/id/register")
async def register_sender_id(
    request: SenderIdRegisterRequest,
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.register_sender_id(request.sender_name, request.purpose)
    except Exception as e:
        handle_error(e)

@app.get("/sender/id/status")
async def check_sender_id_status(
    sender_name: str,
    service: VynfyService = Depends(get_vynfy_service),
    app_data: Any = Depends(verify_api_key)
):
    try:
        return await service.check_sender_id_status(sender_name)
    except Exception as e:
        handle_error(e)

# ======================
# Webhook Bridge Logic
# ======================

@app.post("/webhooks/vynfy")
async def vynfy_webhook(request: Request):
    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})
    message_id = data.get("message_id")
    
    print(f"[BRIDGE WEBHOOK] Received event: {event} for message: {message_id}")
    
    if message_id:
        app_target = db.get_app_by_message_id(message_id)
        if app_target:
            # Update status in our DB
            db.update_message_status(message_id, event)
            
            if app_target['webhook_url']:
                try:
                    async with httpx.AsyncClient() as client:
                        print(f"Forwarding {event} to {app_target['webhook_url']}")
                        await client.post(app_target['webhook_url'], json=payload, timeout=5.0)
                except Exception as e:
                    print(f"Failed to forward webhook to {app_target['name']}: {str(e)}")
            else:
                print(f"No webhook URL set for app {app_target['name']}, status updated locally only.")
        else:
            print(f"CRITICAL: No target app found for message_id: {message_id}. Check if it was stored correctly.")
            
    return {"status": "success", "message": "Webhook processed by bridge"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=3000, reload=True)
