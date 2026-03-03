# Payten Payment Integration — Setup & Testing Guide

## Overview

Agencies subscribe on the marketing website (`vavilonsolutions.rs/pricing`).
After payment, their status is set in Redis and guide access is gated against it.
Card data never touches this server — Payten's hosted payment page handles it.

---

## Redis Keys Written by This Integration

```
agency:{normalised_agency_name}     JSON — subscription state
payten_order:{orderId}              string — reverse lookup (90-day TTL)
```

Agency name normalisation: trimmed → lowercase → spaces replaced with `_`
Example: `"City Tours Belgrade"` → key `agency:city_tours_belgrade`

Agency record fields:

| Field | Values |
|---|---|
| `status` | `pending` · `trialing` · `active` · `past_due` · `cancelled` |
| `trialEnd` | ISO 8601 string or `null` |
| `currentPeriodEnd` | ISO 8601 string or `null` |
| `orderId` | `VAV-{uuid}` of the initial order |
| `email` | billing email |

---

## Environment Variables

Add to `backend/.env`:

```env
# Payten API — provided by your Payten merchant account
PAYTEN_API_URL=https://api.payten.com           # confirm endpoint with Payten
PAYTEN_API_KEY=your_api_key_here

# Webhook signature secret — set after configuring webhook in Payten dashboard
PAYTEN_WEBHOOK_SECRET=your_webhook_secret_here

# URLs used in payment session creation
WEBSITE_URL=http://localhost:3001               # marketing website origin
NODE_BACKEND_URL=http://localhost:3000          # this server (for notificationUrl)
```

---

## Payten Dashboard Setup

### 1. Create merchant account

Contact Payten (https://payten.com) or your acquiring bank to open an e-commerce
merchant account. They will provide:
- API credentials (`PAYTEN_API_KEY`)
- API base URL (`PAYTEN_API_URL`)
- Integration documentation specific to your account type

### 2. Configure recurring payments

Recurring billing must be enabled on your merchant account. Payten stores the
card token on the initial `PURCHASE` and charges it automatically on each cycle.
Ask your Payten account manager to enable:
- Recurring / subscription billing
- 7-day trial period support (or handle trial end manually via webhook)

### 3. Configure webhook (notification URL)

In the Payten merchant portal, set the notification URL to:

```
Production:  https://vavilon-backend.azurewebsites.net/api/payten/webhook
Local dev:   use ngrok or similar — see section below
```

Events to receive:
- `PURCHASE` + `APPROVED` → trial started
- `RECURRING` + `APPROVED` → monthly charge succeeded
- `RECURRING` + `DECLINED` → charge failed
- `CANCELLED` → subscription cancelled

Copy the webhook signing secret shown in the portal → `PAYTEN_WEBHOOK_SECRET`.

### 4. Verify API field names

The request body in `src/routes/payten.js` uses common field names, but Payten's
exact schema depends on your account type and API version. Cross-check these fields
with the documentation provided by Payten:

| Field used in code | Verify name with Payten |
|---|---|
| `amount` | smallest currency unit (cents) or decimal? |
| `recurringPayment` | exact field name for enabling recurring |
| `trialPeriodDays` | exact field name for trial length |
| `paymentUrl` | field name in the response that holds the HPP URL |
| `x-payten-signature` | header name for webhook signature |

Update `src/routes/payten.js` based on your actual documentation.

---

## Linking Guides to Agencies

Guides must have an `agencyName` field in their Redis record to be granted access.
Add a column to the CSV import file:

```csv
name,surname,username,email,phone,access_start_date,access_end_date,agency_name
Andrej,Bantulic,andrej.bantulic.1234,email@example.com,+381...,2026-01-01,2027-01-01,city_tours_belgrade
```

Then update `backend/scripts/import-guides.js` to read and store `agency_name`
(normalised to match the `agency:{name}` Redis key) in each guide record.

Until a guide has `agencyName` set, their login will return `reason: 'subscription_required'`
and they will see the "Access not available today" screen on the web app.

---

## Local Development with ngrok

Payten cannot reach `localhost` directly. Use ngrok to expose the local backend:

```bash
# Install: https://ngrok.com/download
ngrok http 3000
# Prints: Forwarding https://abc123.ngrok.io -> http://localhost:3000
```

Set in `.env`:
```env
NODE_BACKEND_URL=https://abc123.ngrok.io
```

Set the Payten notification URL to:
```
https://abc123.ngrok.io/api/payten/webhook
```

---

## Testing the Payment Flow

### Happy path

1. Start the backend: `npm run dev`
2. Open `http://localhost:3001/pricing` on the marketing website
3. Enter agency name and email → click **Start Free Trial**
4. You are redirected to Payten's hosted payment page
5. Enter a test card (provided by Payten for sandbox mode)
6. Payten calls `POST /api/payten/webhook` with `status: APPROVED`
7. Check Redis: `GET agency:your_agency_name` → should show `status: "trialing"`

### Verify Redis state

```bash
redis-cli
> GET agency:city_tours_belgrade
> GET payten_order:VAV-<uuid>
```

### Simulate recurring success

Payten's sandbox typically provides a test endpoint or dashboard tool to trigger
recurring charge events. Alternatively, send a test webhook manually:

```bash
curl -X POST http://localhost:3000/api/payten/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "orderId": "VAV-<your-test-uuid>",
    "status": "APPROVED",
    "transactionType": "RECURRING",
    "amount": "15000",
    "currency": "EUR"
  }'
```

Agency status should become `active`.

### Simulate payment failure

```bash
curl -X POST http://localhost:3000/api/payten/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "orderId": "VAV-<your-test-uuid>",
    "status": "DECLINED",
    "transactionType": "RECURRING",
    "amount": "15000",
    "currency": "EUR"
  }'
```

Agency status → `past_due`. Guide logins for that agency are denied.

### Check subscription status via API

```bash
curl http://localhost:3000/api/subscription-status/city_tours_belgrade
# { "status": "trialing", "trialEnd": "...", "currentPeriodEnd": null, "email": "..." }
```

---

## Production Deployment (Azure)

Add to App Service → Configuration → Application settings:

```
PAYTEN_API_URL         https://api.payten.com
PAYTEN_API_KEY         <your production key>
PAYTEN_WEBHOOK_SECRET  <your webhook signing secret>
WEBSITE_URL            https://www.vavilonsolutions.rs
NODE_BACKEND_URL       https://vavilon-backend.azurewebsites.net
```

---

## Website Environment Variable

Add to the marketing website (`Vavilon_website/.env.local`):

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:3000
```

For production (Azure Static Web Apps configuration):
```
NEXT_PUBLIC_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```
