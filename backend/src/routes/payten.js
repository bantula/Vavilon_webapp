const express = require('express');
const router = express.Router();
const crypto = require('crypto');
const { v4: uuidv4 } = require('uuid');
const axios = require('axios');
const { client: redisClient } = require('../redisClient');

// ─── Redis helpers ─────────────────────────────────────────────────────────

function normalizeAgency(name) {
  return name.trim().toLowerCase().replace(/\s+/g, '_');
}

async function getAgency(agencyName) {
  const data = await redisClient.get(`agency:${agencyName}`);
  return data ? JSON.parse(data) : null;
}

async function saveAgency(agencyName, fields) {
  const existing = await getAgency(agencyName) || {};
  const updated = {
    ...existing,
    ...fields,
    agencyName,
    updatedAt: new Date().toISOString(),
    ...(existing.createdAt ? {} : { createdAt: new Date().toISOString() }),
  };
  await redisClient.set(`agency:${agencyName}`, JSON.stringify(updated));
  return updated;
}

// payten_order:{orderId} → agencyName  (reverse lookup for webhook events)
async function getAgencyByOrderId(orderId) {
  return redisClient.get(`payten_order:${orderId}`);
}

async function linkOrderToAgency(orderId, agencyName) {
  // Keep the mapping for 90 days — long enough to cover any delayed webhook
  await redisClient.set(`payten_order:${orderId}`, agencyName, { EX: 60 * 60 * 24 * 90 });
}

// ─── Signature verification ────────────────────────────────────────────────
//
// Payten signs webhook notifications with HMAC-SHA256.
// The exact fields and concatenation order are specified in your Payten
// merchant documentation. The implementation below follows the most common
// pattern: orderId|status|amount|currency concatenated with the webhook secret.
//
// TODO: Confirm the exact signature format with your Payten account manager.
//
function verifyPaytenSignature(body, receivedSignature) {
  const secret = process.env.PAYTEN_WEBHOOK_SECRET;
  if (!secret) {
    console.warn('PAYTEN_WEBHOOK_SECRET not set — skipping signature check');
    return true;
  }

  const payload = [
    body.orderId   || '',
    body.status    || '',
    body.amount    || '',
    body.currency  || '',
  ].join('|');

  const expected = crypto
    .createHmac('sha256', secret)
    .update(payload)
    .digest('hex')
    .toUpperCase();

  const received = (receivedSignature || '').toUpperCase();

  if (expected.length !== received.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(received));
}

// ─── POST /api/payten/create-payment ──────────────────────────────────────
//
// Creates a Payten hosted payment page (HPP) session for an agency.
// Returns a redirect URL — the frontend sends the user there directly.
// Card data is never seen by this server.
//
// Input:  { agencyName: string, email: string }
// Output: { url: string }
// ─────────────────────────────────────────────────────────────────────────
router.post('/payten/create-payment', async (req, res) => {
  const { agencyName, email } = req.body || {};

  if (!agencyName || typeof agencyName !== 'string' || !agencyName.trim()) {
    return res.status(400).json({ error: 'agency_name_required' });
  }
  if (!email || typeof email !== 'string' || !email.includes('@')) {
    return res.status(400).json({ error: 'valid_email_required' });
  }

  const normalized = normalizeAgency(agencyName);

  try {
    // Block duplicate active subscriptions
    const existing = await getAgency(normalized);
    if (existing?.status === 'trialing' || existing?.status === 'active') {
      return res.status(409).json({ error: 'already_subscribed', status: existing.status });
    }

    const orderId = `VAV-${uuidv4()}`;
    const websiteUrl  = process.env.WEBSITE_URL  || 'http://localhost:3001';
    const backendUrl  = process.env.NODE_BACKEND_URL || 'http://localhost:3000';

    // ── Payten API request ─────────────────────────────────────────────────
    // NOTE: The exact field names, endpoint path, and auth method below must
    // be verified against the Payten merchant documentation provided to you.
    // Common variations: amount in cents vs. decimal, field names in camelCase
    // vs. snake_case, auth via Bearer token vs. Basic auth vs. signed params.
    // ──────────────────────────────────────────────────────────────────────
    const paytenResponse = await axios.post(
      `${process.env.PAYTEN_API_URL}/orders`,
      {
        orderId,
        amount:       '15000',   // €150.00 — verify smallest-unit format with Payten
        currency:     'EUR',
        description:  'Vavilon Unlimited Access',
        language:     'en',
        successUrl:   `${websiteUrl}/pricing/success`,
        failUrl:      `${websiteUrl}/pricing/cancel`,
        cancelUrl:    `${websiteUrl}/pricing/cancel`,
        notificationUrl: `${backendUrl}/api/payten/webhook`,
        email:        email.trim(),
        // Recurring billing — Payten stores the card token on first payment
        // and automatically charges it after the trial. Verify these field
        // names against your Payten integration guide.
        recurringPayment: true,
        trialPeriodDays: 7,
      },
      {
        headers: {
          Authorization: `Bearer ${process.env.PAYTEN_API_KEY}`,
          'Content-Type': 'application/json',
        },
      }
    );

    // Payten returns the HPP URL in paymentUrl (verify field name with docs)
    const redirectUrl = paytenResponse.data.paymentUrl;

    if (!redirectUrl) {
      console.error('Payten response missing paymentUrl:', paytenResponse.data);
      return res.status(500).json({ error: 'payment_creation_failed' });
    }

    // Persist pending state + reverse-lookup before the user leaves
    await saveAgency(normalized, { email: email.trim(), orderId, status: 'pending' });
    await linkOrderToAgency(orderId, normalized);

    res.json({ url: redirectUrl });
  } catch (err) {
    console.error('Payten create-payment error:', err?.response?.data || err.message);
    res.status(500).json({ error: 'payment_creation_failed' });
  }
});

// ─── POST /api/payten/webhook ──────────────────────────────────────────────
//
// Receives signed server-to-server notifications from Payten.
// This is the ONLY place subscription state is updated — never trust the
// browser success redirect.
//
// Payten event types (transactionType field):
//   PURCHASE   → initial payment (sets up recurring)
//   RECURRING  → subsequent monthly charge
//
// Payten status values:
//   APPROVED   → payment succeeded
//   DECLINED   → payment failed
//   CANCELLED  → subscription cancelled
//
// ─────────────────────────────────────────────────────────────────────────
router.post('/payten/webhook', async (req, res) => {
  // Signature is typically in a header or in the body — verify with Payten docs
  const signature = req.headers['x-payten-signature'] || req.body?.signature;

  if (!verifyPaytenSignature(req.body, signature)) {
    console.error('Payten webhook: invalid signature');
    return res.status(400).json({ error: 'invalid_signature' });
  }

  const { orderId, status, transactionType } = req.body || {};

  if (!orderId || !status) {
    return res.status(400).json({ error: 'missing_fields' });
  }

  // Resolve agencyName via order reverse-lookup
  const agencyName = await getAgencyByOrderId(orderId);
  if (!agencyName) {
    // Unknown order — log and acknowledge (prevents Payten from retrying)
    console.warn(`Payten webhook: unknown orderId ${orderId}`);
    return res.json({ received: true });
  }

  try {
    const now = new Date();

    if (transactionType === 'PURCHASE' && status === 'APPROVED') {
      // ── Initial payment succeeded: trial begins ──────────────────────────
      const trialEnd = new Date(now);
      trialEnd.setDate(trialEnd.getDate() + 7);

      await saveAgency(agencyName, {
        status: 'trialing',
        trialEnd: trialEnd.toISOString(),
        currentPeriodEnd: null,
      });
      console.log(`✓ [payten] payment_success → ${agencyName}: trialing until ${trialEnd.toISOString()}`);

    } else if (transactionType === 'RECURRING' && status === 'APPROVED') {
      // ── Recurring charge succeeded: mark active ───────────────────────────
      const periodEnd = new Date(now);
      periodEnd.setDate(periodEnd.getDate() + 30);

      await saveAgency(agencyName, {
        status: 'active',
        currentPeriodEnd: periodEnd.toISOString(),
      });
      console.log(`✓ [payten] recurring_success → ${agencyName}: active until ${periodEnd.toISOString()}`);

    } else if (transactionType === 'RECURRING' && status === 'DECLINED') {
      // ── Recurring charge failed ───────────────────────────────────────────
      await saveAgency(agencyName, { status: 'past_due' });
      console.log(`✗ [payten] recurring_failed → ${agencyName}: past_due`);

    } else if (status === 'CANCELLED') {
      // ── Subscription cancelled ────────────────────────────────────────────
      await saveAgency(agencyName, { status: 'cancelled', currentPeriodEnd: null });
      console.log(`✓ [payten] subscription_cancelled → ${agencyName}: cancelled`);
    }

    // Always acknowledge — prevents Payten from retrying on internal errors
    res.json({ received: true });
  } catch (err) {
    console.error(`Payten webhook handler error for ${agencyName}:`, err.message);
    res.json({ received: true });
  }
});

// ─── GET /api/subscription-status/:agencyName ─────────────────────────────
//
// Returns the subscription state for an agency. Used by the website to check
// status after a successful redirect (informational only — never gate access
// based on this; always use the webhook to set state).
//
router.get('/subscription-status/:agencyName', async (req, res) => {
  const normalized = normalizeAgency(req.params.agencyName);

  try {
    const agency = await getAgency(normalized);
    if (!agency) return res.json({ status: null });

    res.json({
      status:           agency.status           || null,
      trialEnd:         agency.trialEnd         || null,
      currentPeriodEnd: agency.currentPeriodEnd || null,
      email:            agency.email            || null,
    });
  } catch (err) {
    console.error('Subscription status error:', err);
    res.status(500).json({ error: 'server_error' });
  }
});

module.exports = router;
