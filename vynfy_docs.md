# Vynfy API Documentation

## Base URL
`https://sms.vynfy.com`

## Authentication
All requests must include the following header:
`X-API-Key: YOUR_API_KEY`

---

## SMS API (v1)

### 1. Send SMS
**Endpoint:** `POST /api/v1/send`

**Request Body:**
```json
{
  "sender": "YourBrand",
  "recipients": ["233XXXXXXXXX", "233XXXXXXXXX"],
  "message": "Hello world",
  "metadata": { "order_id": "123" }
}
```

**Response:**
```json
{
  "success": true,
  "job_id": "12345678",
  "balance": {
    "deducted": 1,
    "remaining": 99
  }
}
```

### 2. Check SMS Balance
**Endpoint:** `GET /api/v1/check/balance`

**Response:**
```json
{
  "success": true,
  "balance": 99.5
}
```

### 3. Check SMS Status
**Endpoint:** `GET /api/v1/status/{task_id}`

### 4. Schedule SMS
**Endpoint:** `POST /api/v1/schedule`

---

## OTP API

### 1. Generate OTP
**Endpoint:** `POST /otp/generate`

**Request Body:**
```json
{
  "recipient": "233XXXXXXXXX",
  "sender_id": "YourBrand",
  "message": "Your OTP is {otp}",
  "expiry": 5,
  "length": 6
}
```

### 2. Verify OTP
**Endpoint:** `POST /otp/verify`

**Request Body:**
```json
{
  "recipient": "233XXXXXXXXX",
  "code": "123456"
}
```

### 3. Check OTP Balance
**Endpoint:** `GET /otp/balance`

### 4. Check OTP Status
**Endpoint:** `GET /otp/status/{otp_id}`

---

## Sender ID Management

### 1. Register Sender ID
**Endpoint:** `POST /sender/id/register`

**Request Body:**
```json
{
  "sender_name": "MyBrand",
  "purpose": "Transaction alerts"
}
```

### 2. Check Sender ID Status
**Endpoint:** `GET /api/v1/sender/id/status?sender_name=MyBrand`

**Response:**
```json
{
  "success": true,
  "sender_name": "MyBrand",
  "status": "verified"
}
```