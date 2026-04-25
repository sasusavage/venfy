# Venfy API Bridge & Tenant Manager

A powerful, standalone microservice that acts as a multi-tenant bridge for the **Vynfy SMS & OTP API**. It allows you to manage multiple internal applications, track their usage, set limits, and route webhooks automatically.

## 🚀 Features
- **Multi-Tenant Support:** Create multiple applications with unique API keys.
- **API Proxy:** Matches Vynfy's API structure exactly (SMS v1 & OTP).
- **Usage Limits:** Set hard limits for SMS and OTP per application.
- **Webhook Routing:** Centralized webhook listener that forwards delivery reports to the correct application based on message ID.
- **Management Dashboard:** A beautiful light-mode UI to manage tenants and view master balances.
- **Coolify Ready:** Includes Dockerfile for easy deployment.

## 🛠️ Setup & Installation

### 1. Environment Variables
Create a `.env` file in the root directory:
```env
PORT=3000
VYNFY_API_KEY=your_vynfy_api_key
MASTER_KEY=your_secure_admin_key
```

### 2. Run Locally
```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python main.py
```
Visit `http://localhost:3000` to access the dashboard.

### 3. Deploy on Coolify
1. Connect your GitHub repository to Coolify.
2. Select **Dockerfile** as the build pack.
3. Set the Environment Variables in the Coolify dashboard.
4. **Crucial:** Add a persistent volume for the SQLite database if you want to keep your tenant data across restarts:
   - Destination: `/app/venfy_bridge.db`

## 📡 API Endpoints

The bridge mimics the Vynfy API exactly. Your applications just need to change their `Base URL` and `X-API-Key`.

### SMS
- `POST /api/v1/send`
- `POST /schedule/v1/send`
- `GET /api/v1/check/balance`
- `GET /api/v1/status/{task_id}`

### OTP
- `POST /otp/generate`
- `POST /otp/verify`
- `GET /otp/balance`
- `GET /otp/status/{otp_id}`

### Webhooks
Point your Vynfy global webhook to:
`https://your-bridge-url.com/webhooks/vynfy`

## 🛡️ Security
- Admin endpoints (`/admin/*`) are protected by the `X-Admin-Key` header.
- Tenant endpoints are protected by their specific `X-API-Key`.

## 📦 Tech Stack
- **Backend:** Python (FastAPI)
- **Database:** SQLite (Multi-tenant tracking)
- **Frontend:** Vanilla JS/CSS (Light Mode)
- **Deployment:** Docker / Coolify
