# Stripe Payments Testing Guide (Backend)

This guide is for local development with Stripe CLI on Windows PowerShell.

Scope of this guide:
- Configure Stripe keys for backend
- Forward Stripe webhooks to local backend
- Trigger the correct events to test course purchase flow
- Validate webhook signature setup

---

## 1) Required Backend Environment Variables

Add these values in `backend/.env`:

```dotenv
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

Notes:
- `STRIPE_SECRET_KEY` is server-only and must never be exposed to frontend.
- `STRIPE_PUBLISHABLE_KEY` is safe to share with frontend.
- `STRIPE_WEBHOOK_SECRET` must match the secret printed by `stripe listen`.
- `STRIPE_SECRET_KEY` must start with `sk_` (`sk_test_...` in test mode). Do not put a `pk_...` value in `STRIPE_SECRET_KEY`.

---

## 2) Start Backend Locally

```powershell
cd c:\Users\thoma\Desktop\Sentient\backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Webhook destination used in this guide:

- `http://127.0.0.1:8000/billing/webhook`

If backend runs on 8001, replace `8000` with `8001` in all commands.

---

## 3) Stripe CLI Login

```powershell
stripe login
```

This opens browser auth once for your CLI session.

---

## 4) Forward Webhooks to Local Backend

Run in a separate terminal:

```powershell
stripe listen --forward-to http://127.0.0.1:8000/billing/webhook
```

Stripe CLI output includes a line like:

- `Ready! Your webhook signing secret is whsec_xxx`

Copy that value into `STRIPE_WEBHOOK_SECRET` in `backend/.env`, then restart backend.

Optional filter (only receive events you care about now):

```powershell
stripe listen --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,payment_intent.payment_failed,charge.refunded,charge.dispute.created --forward-to http://127.0.0.1:8000/billing/webhook
```

---

## 5) Trigger Test Events from Stripe CLI

Use these commands while `stripe listen` is running.

```powershell
stripe trigger checkout.session.completed
stripe trigger checkout.session.async_payment_succeeded
stripe trigger checkout.session.async_payment_failed
stripe trigger payment_intent.payment_failed
stripe trigger charge.refunded
stripe trigger charge.dispute.created
```

Important:
- `stripe trigger checkout.session.completed` may also emit related events such as `product.created`, `price.created`, `payment_intent.created`, `payment_intent.succeeded`, and `charge.succeeded`.
- This is expected behavior from Stripe fixtures.

Recommended test order:

1. `checkout.session.completed`
2. `checkout.session.async_payment_succeeded`
3. `charge.refunded`
4. `payment_intent.payment_failed`

---

## 6) Validate Webhook Delivery

In the terminal running `stripe listen`, confirm events show:

- `--> checkout.session.completed [evt_...]`
- `<-- [200] POST http://127.0.0.1:8000/billing/webhook`

Status guidance:

- `200`: webhook accepted
- `400`: signature or payload validation issue
- `404`: route not implemented or wrong URL path
- `500`: backend error in webhook handler

---

## 7) Troubleshooting

### A) 404 on webhook

Cause:
- Backend does not yet expose `POST /billing/webhook` or URL is incorrect.

Fix:
- Verify backend route and destination path match exactly.
- Ensure backend is running the latest code with the webhook route.

### B) Signature verification fails

Cause:
- `STRIPE_WEBHOOK_SECRET` not updated from current `stripe listen` session.

Fix:
1. Copy new `whsec_...` from `stripe listen`
2. Update `backend/.env`
3. Restart backend

### C) Events not arriving

Cause:
- `stripe listen` not running or wrong port.

Fix:
- Re-run `stripe listen --forward-to http://127.0.0.1:8000/billing/webhook`

### D) Wrong Stripe account mode

Cause:
- Triggering test events against a different account context.

Fix:
- Run `stripe whoami`
- Re-auth via `stripe login` if needed

---

## 8) Production Webhook Destination

When deployed, set Stripe Dashboard webhook endpoint to:

- `https://<your-backend-domain>/billing/webhook`

And set production `STRIPE_WEBHOOK_SECRET` from that endpoint configuration.

Do not reuse local CLI `whsec_...` for production.
