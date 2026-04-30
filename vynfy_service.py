import httpx
from typing import Dict, Any, List, Optional

class VynfyService:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://sms.vynfy.com"
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    # --- SMS ---
    async def check_sms_balance(self) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/v1/check/balance", headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def check_sms_status(self, task_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/api/v1/status/{task_id}", headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def send_sms(self, sender: str, recipients: List[str], message: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "sender": sender,
            "recipients": recipients,
            "message": message
        }
        if metadata:
            payload["metadata"] = metadata
            
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/api/v1/send", json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def schedule_sms(self, sender: str, recipients: List[str], message: str, schedule_time: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "sender": sender,
            "recipients": recipients,
            "message": message,
            "schedule_time": schedule_time
        }
        if metadata:
            payload["metadata"] = metadata
            
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/schedule/v1/send", json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    # --- OTP ---
    async def generate_otp(self, number: str, sender_id: str, message: str, 
                         medium: str = "sms", otp_type: str = "numeric", 
                         expiry: int = 5, length: int = 6) -> Dict[str, Any]:
        payload = {
            "number": number,      # Fallback
            "recipient": number,   # Main as per docs
            "sender_id": sender_id,
            "message": message,
            "medium": medium,
            "otp_type": otp_type,
            "expiry": expiry,
            "length": length
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/otp/generate", json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def verify_otp(self, number: str, code: str) -> Dict[str, Any]:
        payload = {
            "number": number,      # Fallback
            "recipient": number,   # Main as per docs
            "code": code
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/otp/verify", json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def check_otp_balance(self) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/otp/balance", headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def check_otp_status(self, otp_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/otp/status/{otp_id}", headers=self.headers)
            response.raise_for_status()
            return response.json()

    # --- Sender ID ---
    async def register_sender_id(self, sender_name: str, purpose: str) -> Dict[str, Any]:
        payload = {
            "sender_name": sender_name,
            "purpose": purpose
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}/api/v1/sender/id/register", json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def check_sender_id_status(self, sender_name: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            params = {"sender_name": sender_name}
            
            # Try path 1: /sender/id/status
            response = await client.get(f"{self.base_url}/sender/id/status", params=params, headers=self.headers)
            if response.status_code == 200:
                return response.json()
                
            logger.info(f"Path /sender/id/status returned {response.status_code}, trying fallback...")
            
            # Try path 2: /api/v1/sender/id/status
            response = await client.get(f"{self.base_url}/api/v1/sender/id/status", params=params, headers=self.headers)
            if response.status_code == 200:
                return response.json()
                
            # If both fail, log the body of the first failure (usually the most accurate)
            logger.warning(f"Sender ID '{sender_name}' not found on any path. Last response: {response.text}")
            response.raise_for_status()
            return response.json()
