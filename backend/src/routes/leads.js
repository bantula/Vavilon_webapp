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
const { BlobServiceClient } = require('@azure/storage-blob');
const { v4: uuidv4 } = require('uuid');
const { client: redisClient } = require('../redisClient');

const router = express.Router();

// ── Azure Blob Storage ────────────────────────────────────────────────────────

const CONTAINER = 'leads';
const BLOB_NAME  = 'leads.jsonl';

// Lazily build the container client. Doing this at require-time with a missing
// or malformed AZURE_STORAGE_CONNECTION_STRING throws synchronously, which would
// crash the WHOLE backend (auth, sessions, everything) on boot. Lazy init keeps
// any blob misconfiguration contained to the /leads endpoints.
let _containerClient = null;
function getContainerClient() {
  if (!_containerClient) {
    const connStr = process.env.AZURE_STORAGE_CONNECTION_STRING;
    if (!connStr) {
      throw new Error('AZURE_STORAGE_CONNECTION_STRING is not set');
    }
    _containerClient = BlobServiceClient
      .fromConnectionString(connStr)
      .getContainerClient(CONTAINER);
  }
  return _containerClient;
}

async function ensureAppendBlob() {
  const appendBlobClient = getContainerClient().getAppendBlobClient(BLOB_NAME);
  await appendBlobClient.createIfNotExists();
  return appendBlobClient;
}

async function appendLead(lead) {
  const appendBlobClient = getContainerClient().getAppendBlobClient(BLOB_NAME);
  const line = JSON.stringify(lead) + '\n';
  await appendBlobClient.appendBlock(line, Buffer.byteLength(line));
}

async function readAllLeads() {
  const blobClient = getContainerClient().getBlobClient(BLOB_NAME);
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

// Init blob on startup — best effort. A failure here (e.g. missing connection
// string) must NOT take down the process; /leads will surface the error per-request.
ensureAppendBlob()
  .then(() => console.log('✓ Leads blob storage ready'))
  .catch((err) => console.error('Leads blob init skipped:', err.message));

// Redis is a best-effort cache only; the shared client is used.

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
  const adminKey = process.env.ADMIN_KEY || process.env.ADMIN_SECRET;
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
