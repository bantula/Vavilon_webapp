/**
 * /api/leads  — lead capture from the marketing website pricing section
 *
 * POST /api/leads
 *   Body: { plan, name, surname, email, phone? }
 *   Appends lead to Azure Blob Storage (persistent) + caches in Redis
 *   Returns 201 { ok: true }
 *
 * GET /api/admin/leads
 *   Requires header:  X-Admin-Key: <ADMIN_KEY env var>
 *   Reads from Azure Blob Storage (source of truth), newest-first
 */

const express = require('express');
const { createClient } = require('redis');
const { BlobServiceClient, AppendBlobClient } = require('@azure/storage-blob');
const { v4: uuidv4 } = require('uuid');

const router = express.Router();

// ── Azure Blob Storage ────────────────────────────────────────────────────────

const CONTAINER = 'leads';
const BLOB_NAME  = 'leads.jsonl';

const blobServiceClient = BlobServiceClient.fromConnectionString(
  process.env.AZURE_STORAGE_CONNECTION_STRING
);
const containerClient = blobServiceClient.getContainerClient(CONTAINER);

async function ensureAppendBlob() {
  const appendBlobClient = containerClient.getAppendBlobClient(BLOB_NAME);
  try {
    await appendBlobClient.createIfNotExists();
  } catch (err) {
    console.error('Blob init error:', err.message);
  }
  return appendBlobClient;
}

async function appendLead(lead) {
  const appendBlobClient = containerClient.getAppendBlobClient(BLOB_NAME);
  const line = JSON.stringify(lead) + '\n';
  await appendBlobClient.appendBlock(line, Buffer.byteLength(line));
}

async function readAllLeads() {
  const blobClient = containerClient.getBlobClient(BLOB_NAME);
  const download = await blobClient.download();
  const chunks = [];
  for await (const chunk of download.readableStreamBody) {
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString('utf8');
  return text
    .split('\n')
    .filter(l => l.trim())
    .map(l => JSON.parse(l))
    .reverse(); // newest first
}

// Init blob on startup
ensureAppendBlob().then(() => console.log('✓ Leads blob storage ready'));

// ── Redis client (cache only) ─────────────────────────────────────────────────

const redisClient = createClient({
  url: process.env.REDIS_URL ? `redis://${process.env.REDIS_URL}:6380` : 'redis://localhost:6379',
  password: process.env.REDIS_PASSWORD,
  socket: {
    tls: !!process.env.REDIS_URL,
    rejectUnauthorized: false,
    reconnectStrategy: (retries) => {
      if (retries > 10) return new Error('Redis reconnect limit exceeded');
      return Math.min(retries * 50, 2000);
    },
  },
});

redisClient.on('error', (err) => console.error('Leads Redis error:', err.message));
redisClient.on('ready', () => console.log('✓ Leads Redis client ready'));

(async () => {
  try { await redisClient.connect(); } catch (err) { console.error('Leads Redis connect failed:', err.message); }
})();

// ── Helpers ───────────────────────────────────────────────────────────────────

function validateLead({ plan, name, surname, email }) {
  if (!plan || !['trial', 'professional'].includes(plan)) return 'Invalid plan';
  if (!name || typeof name !== 'string' || !name.trim())          return 'Name is required';
  if (!surname || typeof surname !== 'string' || !surname.trim()) return 'Surname is required';
  if (!email || typeof email !== 'string' || !email.includes('@')) return 'Valid email is required';
  return null;
}

// ── POST /api/leads ───────────────────────────────────────────────────────────

router.post('/leads', async (req, res) => {
  const { plan, name, surname, email, phone } = req.body || {};

  const validationError = validateLead({ plan, name, surname, email });
  if (validationError) return res.status(400).json({ error: validationError });

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
    // Blob Storage is the source of truth
    await appendLead(lead);

    // Also cache in Redis (best-effort — don't fail if Redis is down)
    try {
      await redisClient.set(`lead:${id}`, JSON.stringify(lead));
      await redisClient.lPush('leads:index', id);
    } catch (redisErr) {
      console.warn('Redis cache write failed (lead still saved to blob):', redisErr.message);
    }

    console.log(`Lead captured: ${lead.email} (plan: ${plan})`);
    return res.status(201).json({ ok: true });
  } catch (err) {
    console.error('Failed to save lead:', err.message);
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
    const leads = await readAllLeads();
    return res.json({ leads });
  } catch (err) {
    console.error('Failed to list leads:', err.message);
    return res.status(500).json({ error: 'Could not retrieve leads.' });
  }
});

module.exports = router;
