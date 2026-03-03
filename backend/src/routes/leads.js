/**
 * /api/leads  — lead capture from the marketing website pricing section
 *
 * POST /api/leads
 *   Body: { plan, name, surname, email, phone? }
 *   Stores lead:{uuid} in Redis and increments leads:index
 *   Returns 201 { ok: true }
 *
 * GET /api/admin/leads
 *   Requires header:  X-Admin-Key: <ADMIN_KEY env var>
 *   Returns all lead records sorted newest-first
 */

const express = require('express');
const { createClient } = require('redis');
const { v4: uuidv4 } = require('uuid');

const router = express.Router();

// ── Redis client ──────────────────────────────────────────────────────────────

const client = createClient({
  url: process.env.REDIS_URL ? `redis://${process.env.REDIS_URL}:6380` : 'redis://localhost:6379',
  password: process.env.REDIS_PASSWORD,
  socket: {
    tls: process.env.REDIS_URL ? true : false,
    rejectUnauthorized: false,
    reconnectStrategy: (retries) => {
      if (retries > 10) return new Error('Redis reconnect limit exceeded');
      return Math.min(retries * 50, 2000);
    },
  },
});

client.on('error', (err) => console.error('Leads Redis error:', err));
client.on('ready', () => console.log('✓ Leads Redis client ready'));

(async () => {
  try {
    await client.connect();
  } catch (err) {
    console.error('Leads Redis connect failed:', err);
  }
})();

// ── Helpers ───────────────────────────────────────────────────────────────────

function validateLead({ plan, name, surname, email }) {
  if (!plan || !['trial', 'professional'].includes(plan)) return 'Invalid plan';
  if (!name || typeof name !== 'string' || !name.trim())         return 'Name is required';
  if (!surname || typeof surname !== 'string' || !surname.trim()) return 'Surname is required';
  if (!email || typeof email !== 'string' || !email.includes('@')) return 'Valid email is required';
  return null;
}

// ── POST /api/leads ───────────────────────────────────────────────────────────

router.post('/leads', async (req, res) => {
  const { plan, name, surname, email, phone } = req.body || {};

  const validationError = validateLead({ plan, name, surname, email });
  if (validationError) {
    return res.status(400).json({ error: validationError });
  }

  const id = uuidv4();
  const lead = {
    id,
    plan,
    name:      name.trim(),
    surname:   surname.trim(),
    email:     email.trim().toLowerCase(),
    phone:     phone ? phone.trim() : null,
    createdAt: new Date().toISOString(),
  };

  try {
    // Store the record and push id onto the ordered list (newest first via LPUSH)
    await client.set(`lead:${id}`, JSON.stringify(lead));
    await client.lPush('leads:index', id);

    console.log(`Lead captured: ${lead.email} (plan: ${plan})`);
    return res.status(201).json({ ok: true });
  } catch (err) {
    console.error('Failed to save lead:', err);
    return res.status(500).json({ error: 'Could not save. Please try again.' });
  }
});

// ── GET /api/admin/leads ──────────────────────────────────────────────────────

router.get('/admin/leads', async (req, res) => {
  const adminKey = process.env.ADMIN_KEY;
  if (!adminKey || req.headers['x-admin-key'] !== adminKey) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  try {
    const ids   = await client.lRange('leads:index', 0, -1); // newest first
    const leads = await Promise.all(
      ids.map(async (id) => {
        const raw = await client.get(`lead:${id}`);
        return raw ? JSON.parse(raw) : null;
      })
    );

    return res.json({ leads: leads.filter(Boolean) });
  } catch (err) {
    console.error('Failed to list leads:', err);
    return res.status(500).json({ error: 'Could not retrieve leads.' });
  }
});

module.exports = router;
