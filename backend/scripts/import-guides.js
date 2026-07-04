#!/usr/bin/env node
/**
 * import-guides.js
 *
 * Reads a CSV file and writes the guide records to backend/data/guides.json,
 * merging with any guides already there. If AZURE_STORAGE_CONNECTION_STRING is
 * set, it also uploads the result to Blob Storage (container `guides`,
 * blob `guides.json`) — the source of truth the backend loads at startup.
 *
 * Usage (from the backend/ directory):
 *   node --env-file=.env scripts/import-guides.js [path-to-csv]
 *
 * Default CSV path: ./data/guides.csv
 *
 * CSV Format (required headers, in any order):
 *   name, surname, username, email, phone, access_start_date, access_end_date
 *
 * Multiple access windows per guide: add extra rows with the same username.
 */

const fs = require('fs');
const path = require('path');

const csvPath = process.argv[2] || path.join(__dirname, '..', 'data', 'guides.csv');
const jsonPath = path.join(__dirname, '..', 'data', 'guides.json');

const CONTAINER = 'guides';
const BLOB_NAME = 'guides.json';

// ── CSV parsing (no external dependencies) ────────────────────────────────
function parseCsv(text) {
  // Strip a UTF-8 BOM (common in Excel/Windows exports).
  const clean = text.replace(/^﻿/, '');
  const lines = clean.split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 2) throw new Error('CSV must have a header row and at least one data row');

  const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
  const required = ['name', 'surname', 'username', 'email', 'phone', 'access_start_date', 'access_end_date'];
  for (const r of required) {
    if (!headers.includes(r)) throw new Error(`Missing required column: ${r}`);
  }

  return lines.slice(1).map(line => {
    const values = line.split(',').map(v => v.trim());
    const row = {};
    headers.forEach((h, i) => { row[h] = values[i] || ''; });
    return row;
  });
}

function validateDate(dateStr, label) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    throw new Error(`Invalid ${label}: "${dateStr}" (expected YYYY-MM-DD)`);
  }
}

function loadExisting() {
  if (fs.existsSync(jsonPath)) {
    try { return JSON.parse(fs.readFileSync(jsonPath, 'utf8')); }
    catch { return []; }
  }
  return [];
}

async function uploadToBlob(guides) {
  const connStr = process.env.AZURE_STORAGE_CONNECTION_STRING;
  if (!connStr) {
    console.log('AZURE_STORAGE_CONNECTION_STRING not set — wrote local file only.');
    return;
  }
  const { BlobServiceClient } = require('@azure/storage-blob');
  const container = BlobServiceClient.fromConnectionString(connStr).getContainerClient(CONTAINER);
  await container.createIfNotExists();
  const data = JSON.stringify(guides, null, 2);
  await container.getBlockBlobClient(BLOB_NAME).upload(data, Buffer.byteLength(data), {
    blobHTTPHeaders: { blobContentType: 'application/json' }
  });
  console.log('✓ Uploaded guides.json to Blob.');
}

// ── Main ──────────────────────────────────────────────────────────────────
async function main() {
  if (!fs.existsSync(csvPath)) {
    console.error(`CSV file not found: ${csvPath}`);
    process.exit(1);
  }

  const text = fs.readFileSync(csvPath, 'utf8');
  let rows;
  try {
    rows = parseCsv(text);
  } catch (err) {
    console.error('CSV parse error:', err.message);
    process.exit(1);
  }

  // Start from existing guides so CSV rows update rather than wipe.
  const guideMap = new Map(loadExisting().map(g => [g.username, g]));

  for (const row of rows) {
    const username = row.username.trim().toLowerCase();
    if (!username) { console.warn('Skipping row with empty username'); continue; }

    try {
      validateDate(row.access_start_date, 'access_start_date');
      validateDate(row.access_end_date, 'access_end_date');
    } catch (err) {
      console.error(`Row for ${username}: ${err.message}`);
      process.exit(1);
    }
    if (row.access_start_date > row.access_end_date) {
      console.error(`Row for ${username}: access_start_date must be <= access_end_date`);
      process.exit(1);
    }

    if (!guideMap.has(username)) {
      guideMap.set(username, {
        firstName: row.name, lastName: row.surname, username,
        email: row.email, phone: row.phone, accessWindows: [],
        createdAt: new Date().toISOString()
      });
    }
    const guide = guideMap.get(username);
    guide.firstName = row.name;
    guide.lastName = row.surname;
    guide.email = row.email;
    guide.phone = row.phone;
    guide.updatedAt = new Date().toISOString();
    if (!Array.isArray(guide.accessWindows)) guide.accessWindows = [];
    guide.accessWindows.push({ startDate: row.access_start_date, endDate: row.access_end_date });
  }

  const guides = [...guideMap.values()].sort((a, b) => a.username.localeCompare(b.username));

  fs.mkdirSync(path.dirname(jsonPath), { recursive: true });
  fs.writeFileSync(jsonPath, JSON.stringify(guides, null, 2) + '\n');
  console.log(`✓ Wrote ${guides.length} guide(s) to ${jsonPath}`);

  await uploadToBlob(guides);
  console.log('\n✓ Done. Restart the backend (or it will pick up guides on next start).');
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
