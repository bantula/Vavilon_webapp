/**
 * /api/leads — lead capture from the marketing website pricing section
 *
 * POST /api/leads
 *   Body: { plan, name, surname, email, phone? }
 *   Appends the lead to Azure Blob Storage (persistent, source of truth)
 *   Returns 201 { ok: true }
 *
 * GET /api/admin/leads
 *   Requires header:  X-Admin-Key: <ADMIN_KEY / ADMIN_SECRET env var>
 *   Reads from Azure Blob Storage, newest-first
 */

const express = require('express');
const { v4: uuidv4 } = require('uuid');
const { getContainerClient } = require('../blob');

const router = express.Router();

// ── Azure Blob Storage ────────────────────────────────────────────────────────

const CONTAINER = 'leads';
const BLOB_NAME  = 'leads.jsonl';

async function ensureAppendBlob() {
  const containerClient = getContainerClient(CONTAINER);
  await containerClient.createIfNotExists();
  const appendBlobClient = containerClient.getAppendBlobClient(BLOB_NAME);
  await appendBlobClient.createIfNotExists();
  return appendBlobClient;
}

async function appendLead(lead) {
  const appendBlobClient = getContainerClient(CONTAINER).getAppendBlobClient(BLOB_NAME);
  const line = JSON.stringify(lead) + '\n';
  await appendBlobClient.appendBlock(line, Buffer.byteLength(line));
}

async function readAllLeads() {
  const blobClient = getContainerClient(CONTAINER).getBlobClient(BLOB_NAME);
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

// Init blob on startup — best effort; a failure must not take down the process.
ensureAppendBlob()
  .then(() => console.log('✓ Leads blob storage ready'))
  .catch((err) => console.error('Leads blob init skipped:', err.message));

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

  const lead = {
    id:        uuidv4(),
    plan,
    name:      name.trim(),
    surname:   surname.trim(),
    email:     email.trim().toLowerCase(),
    phone:     phone ? phone.trim() : null,
    createdAt: new Date().toISOString(),
  };

  try {
    await appendLead(lead);
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
