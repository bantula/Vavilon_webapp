#!/usr/bin/env node
/**
 * manage-guides.js — add / remove / list guide accounts.
 *
 * Operates on the source of truth: Azure Blob (container `guides`, blob
 * `guides.json`). Downloads the current list, applies the change, uploads it
 * back, and also refreshes the local backup at backend/data/guides.json.
 *
 * Requires: AZURE_STORAGE_CONNECTION_STRING in the environment.
 * After add/remove, restart the backend so it reloads guides into memory:
 *   az webapp restart -n vavilon-backend -g vavilon-rg
 *
 * Usage:
 *   node scripts/manage-guides.js list
 *
 *   node scripts/manage-guides.js add \
 *     --username john.doe.1234 --first John --last Doe \
 *     --email john@agency.com --phone +381601234567 \
 *     --window 2026-10-06:2026-10-08 [--window 2026-10-15:2026-10-17]
 *
 *   node scripts/manage-guides.js remove --username john.doe.1234 [--username jane.smith.5678]
 */

const fs = require('fs');
const path = require('path');
const { BlobServiceClient } = require('@azure/storage-blob');

const CONTAINER = 'guides';
const BLOB_NAME = 'guides.json';
const LOCAL_FILE = path.join(__dirname, '..', 'data', 'guides.json');

function die(msg) { console.error('✗ ' + msg); process.exit(1); }

// ── Minimal arg parser: collects repeated flags into arrays ──────────────────
function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
      if (out[key] === undefined) out[key] = val;
      else out[key] = [].concat(out[key], val);
    } else {
      out._.push(a);
    }
  }
  return out;
}

function normUser(u) { return String(u || '').trim().toLowerCase(); }

function parseWindows(win) {
  const list = [].concat(win || []).filter(w => w && w !== true);
  return list.map(w => {
    const m = String(w).match(/^(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$/);
    if (!m) die(`Invalid --window "${w}" (expected YYYY-MM-DD:YYYY-MM-DD)`);
    if (m[1] > m[2]) die(`Window start after end in "${w}"`);
    return { startDate: m[1], endDate: m[2] };
  });
}

function container() {
  const cs = process.env.AZURE_STORAGE_CONNECTION_STRING;
  if (!cs) die('AZURE_STORAGE_CONNECTION_STRING is not set');
  return BlobServiceClient.fromConnectionString(cs).getContainerClient(CONTAINER);
}

async function loadGuides(c) {
  const blob = c.getBlockBlobClient(BLOB_NAME);
  try {
    const buf = await blob.downloadToBuffer();
    return JSON.parse(buf.toString('utf8'));
  } catch {
    return []; // no blob yet
  }
}

async function saveGuides(c, guides) {
  guides.sort((a, b) => a.username.localeCompare(b.username));
  const data = JSON.stringify(guides, null, 2);
  await c.createIfNotExists();
  await c.getBlockBlobClient(BLOB_NAME).upload(data, Buffer.byteLength(data), {
    blobHTTPHeaders: { blobContentType: 'application/json' }
  });
  fs.mkdirSync(path.dirname(LOCAL_FILE), { recursive: true });
  fs.writeFileSync(LOCAL_FILE, data + '\n');
}

function printGuide(g) {
  const today = new Date().toLocaleDateString('en-CA');
  const active = (g.accessWindows || []).some(w => today >= w.startDate && today <= w.endDate);
  const windows = (g.accessWindows || []).map(w => `${w.startDate}→${w.endDate}`).join(', ');
  console.log(`  ${active ? '●' : '○'} ${g.username}  (${g.firstName} ${g.lastName})  [${windows}]`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const cmd = args._[0];
  const c = container();
  const guides = await loadGuides(c);
  const byUser = new Map(guides.map(g => [g.username, g]));

  if (cmd === 'list' || !cmd) {
    console.log(`${guides.length} guide(s)  (● = has access today, ○ = not today):`);
    guides.sort((a, b) => a.username.localeCompare(b.username)).forEach(printGuide);
    return;
  }

  if (cmd === 'add') {
    const username = normUser(args.username);
    if (!username) die('--username is required');
    const windows = parseWindows(args.window);
    const existing = byUser.get(username) || {};
    const isNew = !byUser.has(username);
    if (isNew) {
      for (const [flag, val] of [['--first', args.first], ['--last', args.last], ['--email', args.email], ['--phone', args.phone]]) {
        if (!val || val === true) die(`${flag} is required for a new guide`);
      }
      if (!windows.length) die('At least one --window is required for a new guide');
    }
    const guide = {
      ...existing,
      firstName: args.first && args.first !== true ? args.first : existing.firstName,
      lastName:  args.last  && args.last  !== true ? args.last  : existing.lastName,
      username,
      email:     args.email && args.email !== true ? args.email : existing.email,
      phone:     args.phone && args.phone !== true ? args.phone : existing.phone,
      // Adding windows appends to existing ones (dedup identical ranges).
      accessWindows: dedupWindows([...(existing.accessWindows || []), ...windows]),
      updatedAt: new Date().toISOString(),
      createdAt: existing.createdAt || new Date().toISOString(),
    };
    byUser.set(username, guide);
    await saveGuides(c, [...byUser.values()]);
    console.log(`✓ ${isNew ? 'Added' : 'Updated'} ${username}`);
    printGuide(guide);
    return;
  }

  if (cmd === 'remove') {
    const users = [].concat(args.username || []).filter(u => u && u !== true).map(normUser);
    if (!users.length) die('At least one --username is required');
    const removed = [], notFound = [];
    for (const u of users) {
      if (byUser.delete(u)) removed.push(u); else notFound.push(u);
    }
    if (removed.length) await saveGuides(c, [...byUser.values()]);
    if (removed.length) console.log('✓ Removed: ' + removed.join(', '));
    if (notFound.length) console.log('⚠ Not found (skipped): ' + notFound.join(', '));
    console.log(`Remaining: ${byUser.size} guide(s).`);
    return;
  }

  die(`Unknown command "${cmd}". Use: list | add | remove`);
}

function dedupWindows(windows) {
  const seen = new Set();
  return windows.filter(w => {
    const key = `${w.startDate}:${w.endDate}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

main().catch(err => die(err.message));
