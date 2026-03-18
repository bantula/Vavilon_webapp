#!/usr/bin/env node
/**
 * import-guides.js
 *
 * Reads a CSV file and imports/updates tour guide records in Redis.
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
 * The script merges all date ranges for a given username into one guide record.
 *
 * Example CSV:
 *   name,surname,username,email,phone,access_start_date,access_end_date
 *   John,Doe,john.doe.1234,john@agency.com,+381601234567,2026-10-06,2026-10-08
 *   John,Doe,john.doe.1234,john@agency.com,+381601234567,2026-10-15,2026-10-17
 *   Jane,Smith,jane.smith.5678,jane@agency.com,+381609876543,2026-10-06,2026-10-08
 *
 * Environment variables (same as backend):
 *   REDIS_URL      - Azure Redis hostname (without protocol or port)
 *   REDIS_PASSWORD - Redis password
 */

const fs = require('fs');
const path = require('path');
const redis = require('redis');

const csvPath = process.argv[2] || path.join(__dirname, '..', 'data', 'guides.csv');

// ── Redis ──────────────────────────────────────────────────────────────────
const client = redis.createClient({
  url: process.env.REDIS_URL ? `redis://${process.env.REDIS_URL}:6380` : 'redis://localhost:6379',
  password: process.env.REDIS_PASSWORD,
  socket: {
    tls: !!process.env.REDIS_URL,
    rejectUnauthorized: false
  }
});

client.on('error', (err) => console.error('Redis error:', err.message));

// ── CSV parsing (no external dependencies) ────────────────────────────────
function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
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

// ── Main ──────────────────────────────────────────────────────────────────
async function main() {
  if (!fs.existsSync(csvPath)) {
    console.error(`CSV file not found: ${csvPath}`);
    console.error('Copy guides_template.csv to guides.csv, fill in the data, then run this script.');
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

  // Merge rows with same username
  const guideMap = new Map();
  for (const row of rows) {
    const username = row.username.toLowerCase();
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
        firstName: row.name,
        lastName: row.surname,
        username,
        email: row.email,
        phone: row.phone,
        accessWindows: []
      });
    }

    guideMap.get(username).accessWindows.push({
      startDate: row.access_start_date,
      endDate: row.access_end_date
    });
  }

  if (guideMap.size === 0) {
    console.error('No valid guide rows found in CSV.');
    process.exit(1);
  }

  await client.connect();
  console.log(`\nImporting ${guideMap.size} guide(s)...\n`);

  let created = 0;
  let updated = 0;

  for (const [username, guide] of guideMap) {
    const key = `guide:${username}`;
    const existing = await client.get(key);
    const isNew = !existing;

    const record = {
      ...(existing ? JSON.parse(existing) : {}),
      ...guide,
      updatedAt: new Date().toISOString(),
      ...(isNew ? { createdAt: new Date().toISOString() } : {})
    };

    await client.set(key, JSON.stringify(record));

    const status = isNew ? '+ CREATED' : '~ UPDATED';
    const windows = guide.accessWindows.map(w => `${w.startDate} → ${w.endDate}`).join(', ');
    console.log(`  ${status}  ${username}  (${guide.firstName} ${guide.lastName})  [${windows}]`);

    if (isNew) created++; else updated++;
  }

  await client.quit();

  console.log(`\n✓ Done: ${created} created, ${updated} updated.`);
  if (created > 0) {
    console.log('\n⚠️  Remember to send welcome emails to newly created guides with their username.');
  }
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
