const { client, scanKeys } = require('../redisClient');

/**
 * Get today's date as YYYY-MM-DD in server local time.
 * Access windows compare against this date.
 */
function todayLocalDate() {
  return new Date().toLocaleDateString('en-CA'); // returns YYYY-MM-DD
}

/**
 * Store a guide in Redis. If the guide did not previously exist,
 * set createdAt so the caller can detect new accounts.
 *
 * @param {object} guideData - { firstName, lastName, username, email, phone, accessWindows }
 * @returns {object} { guide, isNew }
 */
async function upsertGuide(guideData) {
  const key = `guide:${guideData.username}`;
  const existing = await client.get(key);
  const isNew = !existing;

  const guide = {
    ...(existing ? JSON.parse(existing) : {}),
    ...guideData,
    updatedAt: new Date().toISOString(),
    ...(isNew ? { createdAt: new Date().toISOString() } : {})
  };

  await client.set(key, JSON.stringify(guide));
  console.log(`✓ Guide ${isNew ? 'created' : 'updated'}: ${guideData.username}`);
  return { guide, isNew };
}

/**
 * Retrieve a guide by username.
 */
async function getGuide(username) {
  const data = await client.get(`guide:${username}`);
  return data ? JSON.parse(data) : null;
}

/**
 * List all guides (scans Redis for guide:* keys).
 */
async function listGuides() {
  const keys = await scanKeys('guide:*');
  if (!keys.length) return [];

  const values = await Promise.all(keys.map(k => client.get(k)));
  return values.map(v => JSON.parse(v));
}

/**
 * Check whether a guide has access today.
 *
 * @param {string} username
 * @returns {{ found: boolean, guide?: object, hasAccessToday?: boolean, scheduledWindows?: Array }}
 */
async function checkAccess(username) {
  const guide = await getGuide(username);

  if (!guide) {
    return { found: false };
  }

  const today = todayLocalDate();
  const windows = guide.accessWindows || [];

  const hasAccessToday = windows.some(
    w => today >= w.startDate && today <= w.endDate
  );

  return {
    found: true,
    guide,
    hasAccessToday,
    scheduledWindows: windows
  };
}

/**
 * Retrieve an agency's subscription record by normalised agency name.
 * Used by the auth route to gate guide access by subscription status.
 */
async function getAgencySubscription(agencyName) {
  const data = await client.get(`agency:${agencyName}`);
  return data ? JSON.parse(data) : null;
}

module.exports = { upsertGuide, getGuide, listGuides, checkAccess, getAgencySubscription };
