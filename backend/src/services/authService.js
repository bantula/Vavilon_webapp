/**
 * Guide accounts.
 *
 * Source of truth is a single JSON document in Azure Blob Storage
 * (container `guides`, blob `guides.json`), loaded into memory once at startup.
 * There are only a handful of guides that change rarely, so an in-memory copy
 * plus a Blob write on update is plenty — no Redis required.
 *
 * For local development (no AZURE_STORAGE_CONNECTION_STRING), it falls back to
 * backend/data/guides.json and seeds Blob from it when possible.
 */
const fs = require('fs');
const path = require('path');
const { getContainerClient } = require('../blob');

const CONTAINER = 'guides';
const BLOB_NAME = 'guides.json';
const LOCAL_FILE = path.join(__dirname, '..', '..', 'data', 'guides.json');

let guides = new Map(); // username -> guide object
let readyPromise = null;

/** Today's date as YYYY-MM-DD in server local time. */
function todayLocalDate() {
  return new Date().toLocaleDateString('en-CA');
}

async function loadFromBlob() {
  const client = getContainerClient(CONTAINER).getBlockBlobClient(BLOB_NAME);
  const buf = await client.downloadToBuffer();
  return JSON.parse(buf.toString('utf8'));
}

function loadFromLocalFile() {
  if (fs.existsSync(LOCAL_FILE)) {
    return JSON.parse(fs.readFileSync(LOCAL_FILE, 'utf8'));
  }
  return null;
}

async function saveToBlob() {
  const client = getContainerClient(CONTAINER).getBlockBlobClient(BLOB_NAME);
  const data = JSON.stringify([...guides.values()], null, 2);
  await client.upload(data, Buffer.byteLength(data), {
    blobHTTPHeaders: { blobContentType: 'application/json' }
  });
}

async function init() {
  let list = null;
  try {
    await getContainerClient(CONTAINER).createIfNotExists();
    list = await loadFromBlob();
    console.log(`✓ Loaded ${list.length} guide(s) from Blob`);
  } catch (err) {
    console.warn('Guide Blob load failed:', err.message);
    const local = loadFromLocalFile();
    if (local) {
      guides = new Map(local.map(g => [g.username, g]));
      console.log(`✓ Loaded ${local.length} guide(s) from local data/guides.json`);
      // Best-effort seed to Blob so the deployed backend has the data.
      try { await saveToBlob(); console.log('✓ Seeded guides.json to Blob'); }
      catch (e) { console.warn('Could not seed guides to Blob:', e.message); }
      return;
    }
    console.warn('No guide source available — starting with 0 guides');
    list = [];
  }
  guides = new Map((list || []).map(g => [g.username, g]));
}

/** Kick off (or reuse) the one-time load. All reads await this. */
function ready() {
  if (!readyPromise) readyPromise = init();
  return readyPromise;
}

async function upsertGuide(guideData) {
  await ready();
  const key = guideData.username;
  const existing = guides.get(key);
  const isNew = !existing;

  const guide = {
    ...(existing || {}),
    ...guideData,
    updatedAt: new Date().toISOString(),
    ...(isNew ? { createdAt: new Date().toISOString() } : {})
  };

  guides.set(key, guide);
  await saveToBlob();
  console.log(`✓ Guide ${isNew ? 'created' : 'updated'}: ${key}`);
  return { guide, isNew };
}

async function getGuide(username) {
  await ready();
  return guides.get(username) || null;
}

async function listGuides() {
  await ready();
  return [...guides.values()];
}

async function checkAccess(username) {
  await ready();
  const guide = guides.get(username);
  if (!guide) return { found: false };

  const today = todayLocalDate();
  const windows = guide.accessWindows || [];
  const hasAccessToday = windows.some(w => today >= w.startDate && today <= w.endDate);

  return { found: true, guide, hasAccessToday, scheduledWindows: windows };
}

module.exports = { ready, upsertGuide, getGuide, listGuides, checkAccess };
