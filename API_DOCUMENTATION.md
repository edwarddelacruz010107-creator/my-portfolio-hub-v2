# API DOCUMENTATION — Portfolio CMS v5.0

**Version:** 5.0  
**Last Updated:** June 15, 2026  
**Status:** Production Ready

---

## TABLE OF CONTENTS

1. [Authentication](#authentication)
2. [Error Handling](#error-handling)
3. [Rate Limiting](#rate-limiting)
4. [Pagination](#pagination)
5. [Endpoints](#endpoints)
6. [Webhooks](#webhooks)
7. [Examples](#examples)

---

## AUTHENTICATION

### API Key Authentication

All API requests require authentication via bearer token in the Authorization header.

```bash
Authorization: Bearer pk_live_<api_key>
```

API keys are:
- Unique per tenant
- Encrypted in database
- Can be rotated
- Only shown once after generation
- Scoped to single tenant

### Obtaining an API Key

1. Login to Portal
2. Settings → API Keys
3. Click "Generate New Key"
4. Copy the key immediately (cannot be retrieved later)
5. Store securely

### Revoking an API Key

1. Settings → API Keys
2. Click "Revoke" next to the key
3. Key becomes inactive immediately
4. Existing API calls will fail

---

## ERROR HANDLING

### Standard Error Response

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": {}
  }
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK — Request succeeded |
| 201 | Created — Resource created |
| 204 | No Content — Success with no body |
| 400 | Bad Request — Invalid input |
| 401 | Unauthorized — Missing/invalid auth |
| 403 | Forbidden — Insufficient permissions |
| 404 | Not Found — Resource not found |
| 409 | Conflict — Resource conflict |
| 429 | Too Many Requests — Rate limited |
| 500 | Server Error — Internal error |
| 503 | Service Unavailable — Maintenance |

### Error Codes

| Code | Status | Description |
|------|--------|-------------|
| INVALID_INPUT | 400 | Input validation failed |
| MISSING_FIELD | 400 | Required field missing |
| INVALID_FORMAT | 400 | Invalid data format |
| AUTH_FAILED | 401 | Authentication failed |
| TOKEN_EXPIRED | 401 | Auth token expired |
| INSUFFICIENT_PERMISSION | 403 | Not authorized for action |
| RESOURCE_NOT_FOUND | 404 | Resource doesn't exist |
| RESOURCE_CONFLICT | 409 | Resource conflict |
| RATE_LIMITED | 429 | Too many requests |
| INTERNAL_ERROR | 500 | Server error |

---

## RATE LIMITING

### Rate Limit Headers

All responses include rate limit information:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1624512345
```

### Rate Limits by Endpoint

| Endpoint | Limit |
|----------|-------|
| POST /auth/login | 5 per 15 min |
| POST /auth/register | 3 per 30 min |
| POST /auth/password-reset | 3 per 30 min |
| POST /api/* (general) | 100 per hour |
| GET /api/* (general) | 100 per hour |
| POST /webhooks/* | 200 per minute |

### Handling Rate Limits

If rate limited (429 response):
1. Wait until X-RateLimit-Reset timestamp
2. Retry the request
3. Implement exponential backoff

```python
import time
import requests

def api_request_with_retry(url, max_retries=3):
    for attempt in range(max_retries):
        response = requests.get(url)
        
        if response.status_code == 429:
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            wait_time = reset_time - time.time()
            if wait_time > 0:
                time.sleep(wait_time + 1)
            continue
        
        return response
    
    raise Exception("Max retries exceeded")
```

---

## PAGINATION

### Pagination Parameters

```
GET /api/portfolios?page=1&per_page=20&sort=-created_at
```

| Parameter | Type | Default | Max |
|-----------|------|---------|-----|
| page | integer | 1 | - |
| per_page | integer | 20 | 100 |
| sort | string | -created_at | - |

### Pagination Response

```json
{
  "success": true,
  "data": [...],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_items": 127,
    "total_pages": 7,
    "has_next": true,
    "has_prev": false
  }
}
```

### Sorting

Sort by column with prefix:
- `+` ascending (default)
- `-` descending

```
?sort=name          # Sort by name ascending
?sort=-created_at   # Sort by created_at descending
?sort=name,-price   # Multiple sorts
```

---

## ENDPOINTS

### Authentication

#### POST /auth/register
Register a new account

**Request:**
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "user_id": 123,
    "email": "user@example.com",
    "tenant_slug": "auto-generated-slug"
  }
}
```

**Errors:**
- `INVALID_EMAIL` - Email invalid or already registered
- `WEAK_PASSWORD` - Password doesn't meet requirements
- `RATE_LIMITED` - Too many registration attempts

---

#### POST /auth/login
Login to account

**Request:**
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "user_id": 123,
    "email": "user@example.com",
    "tenant_slug": "tenant-slug",
    "session_token": "sess_..."
  }
}
```

**Errors:**
- `INVALID_CREDENTIALS` - Email or password incorrect
- `RATE_LIMITED` - Too many login attempts
- `ACCOUNT_LOCKED` - Account locked for security

---

#### POST /auth/password-reset
Request password reset

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Reset link sent to email"
}
```

---

#### POST /auth/password-reset/{token}
Reset password with token

**Request:**
```json
{
  "password": "NewSecurePassword123!"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Password reset successful"
}
```

---

#### POST /auth/logout
Logout current session

**Response:**
```json
{
  "success": true,
  "message": "Logged out successfully"
}
```

---

### Portfolio Management

#### GET /api/portfolios
List all portfolios

**Query Parameters:**
```
?page=1&per_page=20&sort=-created_at&search=keyword
```

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "name": "My Portfolio",
      "slug": "my-portfolio",
      "description": "...",
      "created_at": "2026-06-15T10:30:00Z",
      "updated_at": "2026-06-15T11:45:00Z"
    }
  ],
  "pagination": {...}
}
```

---

#### POST /api/portfolios
Create new portfolio

**Request:**
```json
{
  "name": "My Portfolio",
  "description": "Portfolio description",
  "color_scheme": "blue"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "id": 1,
    "name": "My Portfolio",
    "slug": "my-portfolio",
    "created_at": "2026-06-15T10:30:00Z"
  }
}
```

---

#### GET /api/portfolios/{id}
Get portfolio details

**Response:**
```json
{
  "success": true,
  "data": {
    "id": 1,
    "name": "My Portfolio",
    "slug": "my-portfolio",
    "description": "...",
    "projects": [...],
    "services": [...],
    "created_at": "2026-06-15T10:30:00Z"
  }
}
```

---

#### PUT /api/portfolios/{id}
Update portfolio

**Request:**
```json
{
  "name": "Updated Portfolio Name",
  "description": "New description"
}
```

**Response:**
```json
{
  "success": true,
  "data": {...}
}
```

---

#### DELETE /api/portfolios/{id}
Delete portfolio

**Response:**
```json
{
  "success": true,
  "message": "Portfolio deleted"
}
```

---

### Projects

#### GET /api/portfolios/{portfolio_id}/projects
List projects in portfolio

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "portfolio_id": 1,
      "title": "Project Title",
      "description": "...",
      "images": [...],
      "created_at": "2026-06-15T10:30:00Z"
    }
  ]
}
```

---

#### POST /api/portfolios/{portfolio_id}/projects
Create new project

**Request:**
```json
{
  "title": "My Project",
  "description": "Project description",
  "technologies": ["React", "Node.js"],
  "link": "https://example.com"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "id": 1,
    "portfolio_id": 1,
    "title": "My Project",
    "slug": "my-project",
    "created_at": "2026-06-15T10:30:00Z"
  }
}
```

---

### Subscriptions

#### GET /api/subscriptions
Get current subscription

**Response:**
```json
{
  "success": true,
  "data": {
    "id": 1,
    "tenant_id": 1,
    "plan": "Pro",
    "billing_cycle": "monthly",
    "status": "active",
    "started_at": "2026-06-15T10:30:00Z",
    "expires_at": "2026-07-15T10:30:00Z"
  }
}
```

---

#### POST /api/subscriptions/checkout
Create checkout session

**Request:**
```json
{
  "plan": "Pro",
  "billing_cycle": "monthly"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "checkout_url": "https://checkout.paymongo.com/...",
    "session_id": "cs_123...",
    "expires_in": 3600
  }
}
```

---

#### POST /api/subscriptions/cancel
Cancel subscription

**Response:**
```json
{
  "success": true,
  "message": "Subscription cancelled"
}
```

---

### Webhooks

#### POST /webhooks/paymongo
PayMongo payment webhook

**Headers:**
```
Paymongo-Signature: <hmac-sha256-hex>
Content-Type: application/json
```

**Request Body:**
```json
{
  "data": {
    "id": "evt_123...",
    "attributes": {
      "type": "payment.paid",
      "metadata": {
        "tenant_id": "1",
        "subscription_id": "1"
      }
    }
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Event processed"
}
```

**Webhook Events:**
- `payment.paid` - Payment successful
- `payment.failed` - Payment failed
- `checkout_session.payment.paid` - Checkout completed
- `subscription.created` - Subscription created
- `subscription.updated` - Subscription updated
- `subscription.cancelled` - Subscription cancelled
- `subscription.expired` - Subscription expired

---

## WEBHOOKS

### Webhook Security

All PayMongo webhooks are signed with HMAC-SHA256.

**Verification:**
```python
import hmac
import hashlib

def verify_signature(payload, signature, secret):
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.lower())
```

### Webhook Handling

1. Verify signature
2. Check idempotency (event_id)
3. Process event
4. Return 200 immediately (async processing)

**Best Practices:**
- Always verify signature
- Handle events idempotently (event_id)
- Process asynchronously
- Return 200 immediately
- Log all events
- Implement retry logic on your end

---

## EXAMPLES

### cURL Examples

#### Authentication
```bash
curl -H "Authorization: Bearer pk_live_abc123" \
  https://api.example.com/api/portfolios
```

#### Create Portfolio
```bash
curl -X POST https://api.example.com/api/portfolios \
  -H "Authorization: Bearer pk_live_abc123" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Portfolio",
    "description": "A great portfolio"
  }'
```

#### List with Pagination
```bash
curl "https://api.example.com/api/portfolios?page=2&per_page=50" \
  -H "Authorization: Bearer pk_live_abc123"
```

---

### Python Examples

```python
import requests

BASE_URL = "https://api.example.com"
API_KEY = "pk_live_abc123"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# List portfolios
response = requests.get(
    f"{BASE_URL}/api/portfolios",
    headers=headers
)
portfolios = response.json()

# Create portfolio
response = requests.post(
    f"{BASE_URL}/api/portfolios",
    headers=headers,
    json={
        "name": "My Portfolio",
        "description": "Description"
    }
)
new_portfolio = response.json()

# Update portfolio
response = requests.put(
    f"{BASE_URL}/api/portfolios/1",
    headers=headers,
    json={"name": "Updated Name"}
)
updated = response.json()

# Delete portfolio
response = requests.delete(
    f"{BASE_URL}/api/portfolios/1",
    headers=headers
)
```

---

### JavaScript Examples

```javascript
const API_KEY = 'pk_live_abc123';
const BASE_URL = 'https://api.example.com';

const headers = {
  'Authorization': `Bearer ${API_KEY}`,
  'Content-Type': 'application/json'
};

// List portfolios
async function listPortfolios() {
  const response = await fetch(`${BASE_URL}/api/portfolios`, {
    headers
  });
  return response.json();
}

// Create portfolio
async function createPortfolio(data) {
  const response = await fetch(`${BASE_URL}/api/portfolios`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data)
  });
  return response.json();
}

// Update portfolio
async function updatePortfolio(id, data) {
  const response = await fetch(`${BASE_URL}/api/portfolios/${id}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(data)
  });
  return response.json();
}

// Delete portfolio
async function deletePortfolio(id) {
  const response = await fetch(`${BASE_URL}/api/portfolios/${id}`, {
    method: 'DELETE',
    headers
  });
  return response.json();
}
```

---

### Webhook Verification Example

```python
import hmac
import hashlib
import json
from flask import request

@app.route('/webhooks/paymongo', methods=['POST'])
def handle_webhook():
    payload = request.get_data()
    signature = request.headers.get('Paymongo-Signature')
    
    # Verify signature
    secret = app.config['PAYMONGO_WEBHOOK_SECRET']
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(expected, signature.lower()):
        return {'error': 'Invalid signature'}, 401
    
    # Process event
    event = json.loads(payload)
    event_id = event['data']['id']
    event_type = event['data']['attributes']['type']
    
    # TODO: Handle event
    
    return {'success': True}
```

---

## SUPPORT

For API support:
- Email: api-support@yourdomain.com
- Slack: #api-support
- Status Page: status.yourdomain.com

---

**API Version:** 5.0  
**Last Updated:** June 15, 2026  
**Status:** ✅ Production Ready
