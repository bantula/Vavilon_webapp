const redis = require('redis');

// Redis client (mirrors sessionService.js pattern)
const client = redis.createClient({
  url: process.env.REDIS_URL ? `redis://${process.env.REDIS_URL}:6380` : 'redis://localhost:6379',
  password: process.env.REDIS_PASSWORD,
  socket: {
    tls: process.env.REDIS_URL ? true : false,
    rejectUnauthorized: false,
    reconnectStrategy: (retries) => {
      if (retries > 10) return new Error('Redis reconnect limit exceeded');
      return Math.min(retries * 50, 2000);
    }
  }
});

client.on('error', (err) => console.error('Auth Redis error:', err));
client.on('ready', () => console.log('✓ Auth Redis client ready'));

(async () => {
  try {
    await client.connect();
  } catch (err) {
    console.error('Auth Redis connect failed:', err);
  }
})();

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
  const keys = await client.keys('guide:*');
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

module.exports = { upsertGuide, getGuide, listGuides, checkAccess };
