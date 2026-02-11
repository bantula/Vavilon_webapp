const { v4: uuidv4 } = require('uuid');
const QRCode = require('qrcode');
const redis = require('redis');

// Redis client for session storage
const client = redis.createClient({
  url: process.env.REDIS_URL ? `redis://${process.env.REDIS_URL}:6380` : 'redis://localhost:6379',
  password: process.env.REDIS_PASSWORD,
  socket: {
    tls: process.env.REDIS_URL ? true : false,
    rejectUnauthorized: false
  }
});

client.on('error', (err) => console.error('Redis Client Error', err));
client.on('connect', () => console.log('✓ Connected to Redis'));

// Connect to Redis
(async () => {
  try {
    await client.connect();
  } catch (err) {
    console.error('Failed to connect to Redis:', err);
  }
})();

/**
 * Generate a short 6-character join code
 */
function generateJoinCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // Excluding confusing chars
  let code = '';
  for (let i = 0; i < 6; i++) {
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return code;
}

/**
 * Create a new translation session
 */
async function createSession() {
  const sessionId = uuidv4();
  const joinCode = generateJoinCode();

  // Generate QR code
  const joinUrl = `${process.env.FRONTEND_URL || 'http://localhost:5173'}/join?code=${joinCode}`;
  const qrCodeDataUrl = await QRCode.toDataURL(joinUrl);

  const session = {
    id: sessionId,
    joinCode,
    qrCode: qrCodeDataUrl,
    joinUrl,
    createdAt: new Date().toISOString(),
    isActive: true,
    speakerConnected: false,
    listeners: {}, // languageCode -> array of connectionIds
    supportedLanguages: ['en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ar']
  };

  // Store in Redis with 24 hour expiration
  await client.setEx(`session:${sessionId}`, 86400, JSON.stringify(session));
  await client.setEx(`code:${joinCode}`, 86400, sessionId);

  console.log(`✓ Session created: ${sessionId} | Code: ${joinCode}`);

  return {
    sessionId,
    joinCode,
    qrCode: qrCodeDataUrl,
    joinUrl,
    supportedLanguages: session.supportedLanguages
  };
}

/**
 * Get session by ID or join code
 */
async function getSession(idOrCode) {
  try {
    // Try as session ID first
    const sessionData = await client.get(`session:${idOrCode}`);
    if (sessionData) {
      return JSON.parse(sessionData);
    }

    // Try as join code
    const sessionId = await client.get(`code:${idOrCode}`);
    if (sessionId) {
      const session = await client.get(`session:${sessionId}`);
      return session ? JSON.parse(session) : null;
    }

    return null;
  } catch (err) {
    console.error('Error getting session:', err);
    return null;
  }
}

/**
 * Add listener to session
 */
async function addListener(sessionId, connectionId, language) {
  const session = await getSession(sessionId);
  if (!session) return false;

  if (!session.listeners[language]) {
    session.listeners[language] = [];
  }

  if (!session.listeners[language].includes(connectionId)) {
    session.listeners[language].push(connectionId);
  }

  await client.setEx(`session:${sessionId}`, 86400, JSON.stringify(session));
  console.log(`✓ Listener ${connectionId} joined session ${sessionId} (${language})`);

  return true;
}

/**
 * Remove listener from session
 */
async function removeListener(sessionId, connectionId, language) {
  const session = await getSession(sessionId);
  if (!session) return;

  if (session.listeners[language]) {
    session.listeners[language] = session.listeners[language].filter(id => id !== connectionId);

    // Clean up empty language arrays
    if (session.listeners[language].length === 0) {
      delete session.listeners[language];
    }
  }

  await client.setEx(`session:${sessionId}`, 86400, JSON.stringify(session));
  console.log(`✓ Listener ${connectionId} left session ${sessionId}`);
}

/**
 * Get all listener connection IDs for a specific language in a session
 */
async function getListenersByLanguage(sessionId, language) {
  const session = await getSession(sessionId);
  if (!session || !session.listeners[language]) {
    return [];
  }
  return session.listeners[language];
}

/**
 * Set speaker connection status
 */
async function setSpeakerConnected(sessionId, connected) {
  const session = await getSession(sessionId);
  if (session) {
    session.speakerConnected = connected;
    await client.setEx(`session:${sessionId}`, 86400, JSON.stringify(session));
  }
}

/**
 * End session and cleanup
 */
async function endSession(sessionId) {
  const session = await getSession(sessionId);
  if (!session) return;

  await client.del(`session:${sessionId}`);
  await client.del(`code:${session.joinCode}`);

  console.log(`✓ Session ended: ${sessionId}`);
}

/**
 * Get session statistics
 */
async function getSessionStats(sessionId) {
  const session = await getSession(sessionId);
  if (!session) return null;

  let totalListeners = 0;
  const languageBreakdown = {};

  Object.entries(session.listeners).forEach(([language, listeners]) => {
    const count = listeners.length;
    totalListeners += count;
    languageBreakdown[language] = count;
  });

  return {
    sessionId: session.id,
    joinCode: session.joinCode,
    isActive: session.isActive,
    speakerConnected: session.speakerConnected,
    totalListeners,
    languageBreakdown,
    createdAt: session.createdAt
  };
}

module.exports = {
  createSession,
  getSession,
  addListener,
  removeListener,
  getListenersByLanguage,
  setSpeakerConnected,
  endSession,
  getSessionStats
};