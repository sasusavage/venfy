import os, logging, json, uuid
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
import redis
import httpx
from fastapi import FastAPI, Header, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from database import Database
from vynfy_service import VynfyService
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("venfy-bridge")

# Redis Setup
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

db = Database()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing Venfy Bridge...")
    try:
        from database import init_db
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
    yield
    # Shutdown
    logger.info("Shutting down Venfy Bridge...")

app = FastAPI(
    title="Vynfy Bridge Microservice",
    description="A high-scale bridge for Vynfy API with multi-app support and caching",
    version="3.0.0",
    lifespan=lifespan
)

VYNFY_API_KEY = os.getenv("VYNFY_API_KEY")
MASTER_KEY = os.getenv("MASTER_KEY", "venfy_master_secret_2024")

def get_vynfy_service():
    if not VYNFY_API_KEY or VYNFY_API_KEY == "your-api-key-here":
        raise HTTPException(status_code=500, detail="Vynfy API key not configured on server")
    return VynfyService(api_key=VYNFY_API_KEY)

# --- Authentication Dependency ---
async def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header missing")
    
    if x_api_key == MASTER_KEY:
        return {"id": 0, "name": "Admin", "sms_used": 0, "sms_limit": 999999, "otp_used": 0, "otp_limit": 999999}
    
    # Try Cache
    cached = r.get(f"auth:{x_api_key}")
    if cached:
        return json.loads(cached)
        
    app_data = db.get_app_by_api_key(x_api_key)
    if not app_data:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    # Store in Cache for 5 mins
    r.setex(f"auth:{x_api_key}", 300, json.dumps(app_data, default=str))
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
    fixed_rate: float = 0.0

class AppUpdateRequest(BaseModel):
    name: Optional[str] = None
    webhook_url: Optional[str] = None
    sms_limit: Optional[int] = None
    otp_limit: Optional[int] = None
    fixed_rate: Optional[float] = None

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
    
    logger.error(f"Internal Bridge Error: {type(e).__name__} - {err_msg}")
    raise HTTPException(status_code=500, detail=f"Internal Bridge Error: {err_msg}")

@app.get("/health")
def health_check():
    return {"status": "OK", "service": "Vynfy Bridge v3", "redis": r.ping()}

# --- Dashboard serving ---
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_dashboard():
    dashboard_path = os.path.join("static", "index.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Dashboard index.html not found in static folder."

# ======================
# Bridge Admin Endpoints
# ======================

@app.post("/admin/apps", tags=["Admin"])
async def create_app(request: AppCreateRequest, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    api_key = f"vf_{uuid.uuid4().hex}"
    app_id = db.create_app(
        name=request.name,
        api_key=api_key,
        webhook_url=request.webhook_url,
        sms_limit=request.sms_limit,
        otp_limit=request.otp_limit,
        fixed_rate=request.fixed_rate
    )
    return {"id": app_id, "api_key": api_key, "message": "App created successfully"}

@app.patch("/admin/apps/{app_id}", tags=["Admin"])
async def update_app(app_id: int, request: AppUpdateRequest, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    updates = {k: v for k, v in request.dict().items() if v is not None}
    db.update_app(app_id, updates)
    
    # Invalidate Cache
    app_data = db.get_app_by_id(app_id)
    if app_data:
        r.delete(f"auth:{app_data['api_key']}")
        
    return {"message": "App updated successfully"}

@app.delete("/admin/apps/{app_id}", tags=["Admin"])
async def delete_app(app_id: int, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    # Get app info for cache invalidation
    app_data = db.get_app_by_id(app_id)
    if app_data:
        r.delete(f"auth:{app_data['api_key']}")
        
    db.delete_app(app_id)
    return {"message": "App deleted successfully"}

@app.post("/admin/apps/{app_id}/reset", tags=["Admin"])
async def reset_app_usage(app_id: int, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    db.reset_app_usage(app_id)
    
    # Invalidate Cache
    app_data = db.get_app_by_id(app_id)
    if app_data:
        r.delete(f"auth:{app_data['api_key']}")
        
    return {"message": "Usage reset successfully"}

@app.get("/admin/apps", tags=["Admin"])
async def list_apps(x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return db.get_all_apps()

@app.get("/admin/balance", tags=["Admin"])
async def get_master_balance(service: VynfyService = Depends(get_vynfy_service), x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    cached_bal = r.get("master_balance")
    if cached_bal:
        return json.loads(cached_bal)
        
    sms = {}
    otp = {}
    try:
        sms = await service.check_sms_balance()
    except Exception as e:
        logger.error(f"Failed to fetch SMS balance: {str(e)}")

    try:
        otp = await service.check_otp_balance()
    except Exception as e:
        logger.error(f"Failed to fetch OTP balance: {str(e)}")

    def parse_balance(data):
        if not isinstance(data, dict): return data
        main_data = data.get('data') if isinstance(data.get('data'), dict) else data
        balance_obj = main_data.get('balance')
        if isinstance(balance_obj, dict):
            return balance_obj.get('remaining') or balance_obj.get('balance')
        return main_data.get('balance') or main_data.get('remaining') or main_data.get('credit') or main_data.get('amount')

    result = {
        "sms": parse_balance(sms) if parse_balance(sms) is not None else "N/A",
        "otp": parse_balance(otp) if parse_balance(otp) is not None else "N/A"
    }
    r.setex("master_balance", 30, json.dumps(result))
    return result

@app.get("/admin/logs", tags=["Admin"])
async def get_message_logs(limit: int = 50, x_admin_key: str = Header(None)):
    if x_admin_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return db.get_message_logs(limit)

# ======================
# SMS Endpoints
# ======================

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
        if isinstance(recipients, str): recipients = [recipients]

        result = await service.send_sms(
            sender=request.sender,
            recipients=recipients,
            message=request.message,
            metadata=request.metadata
        )
        
        # DEBUG TRACE: Log the actual result from Vynfy
        logger.info(f"Vynfy Raw Response for SMS: {json.dumps(result)}")
        
        data_block = result.get("data") if isinstance(result.get("data"), dict) else result
        job_id = data_block.get("job_id") or data_block.get("message_id") or result.get("job_id")
        
        # Robust Success Check: 
        # 1. success is True
        # 2. status is "success" 
        # 3. or we simply got a job_id (meaning Vynfy accepted it)
        is_success = (result.get("success") is True or 
                     str(result.get("success")).lower() == "true" or 
                     result.get("status") == "success" or 
                     job_id is not None)
        
        if is_success:
            final_job_id = str(job_id) if job_id else f"vinfy_{uuid.uuid4().hex[:8]}"
            logger.info(f"SMS Successfully Sent/Accepted: {final_job_id} for {app_data['name']}")
            
            db.store_message(
                message_id=final_job_id, 
                app_id=app_data['id'], 
                msg_type='sms', 
                recipient=", ".join(recipients), 
                content=request.message
            )
            db.increment_usage(app_data['id'], 'sms', len(recipients))
        else:
            logger.warning(f"SMS might have failed or returned unexpected format for {app_data['name']}: {result}")
                
        return result
    except Exception as e:
        handle_error(e)

# ======================
# Webhook Bridge Logic
# ======================

@app.post("/webhooks/vynfy")
async def vynfy_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})
    message_id = data.get("message_id")
    
    if message_id:
        app_target = db.get_app_by_message_id(message_id)
        if app_target:
            db.update_message_status(message_id, event)
            if app_target['webhook_url']:
                background_tasks.add_task(forward_webhook, app_target['webhook_url'], payload, app_target['name'])
            
    return {"status": "success"}

async def forward_webhook(url: str, payload: dict, app_name: str):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10.0)
    except Exception as e:
        logger.error(f"Failed to forward webhook to {app_name}: {str(e)}")

# ======================
# OTP Endpoints
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
        
        # DEBUG TRACE: Log the actual result from Vynfy
        logger.info(f"Vynfy Raw Response for OTP: {json.dumps(result)}")
        
        data_block = result.get("data") if isinstance(result.get("data"), dict) else result
        otp_id = data_block.get("otp_id") or data_block.get("id") or result.get("otp_id")
        
        is_success = (result.get("success") is True or 
                     str(result.get("success")).lower() == "true" or 
                     result.get("status") == "success" or 
                     otp_id is not None)
        
        if is_success:
            final_otp_id = str(otp_id) if otp_id else f"otp_{uuid.uuid4().hex[:8]}"
            logger.info(f"OTP Successfully Generated/Accepted: {final_otp_id} for {app_data['name']}")
            
            db.store_message(
                message_id=final_otp_id, 
                app_id=app_data['id'], 
                msg_type='otp', 
                recipient=request.number, 
                content=request.message
            )
            db.increment_usage(app_data['id'], 'otp', 1)
        else:
            logger.warning(f"OTP might have failed or returned unexpected format for {app_data['name']}: {result}")
                
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
        return await service.verify_otp(number=request.number, code=request.code)
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

# ======================
# Sender ID Endpoints
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
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"status": "not_found", "purpose": "Sender ID not registered or pending", "sender_name": sender_name}
        handle_error(e)
    except Exception as e:
        handle_error(e)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=3000, reload=True)
