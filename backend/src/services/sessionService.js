const { v4: uuidv4 } = require('uuid');
const crypto = require('crypto');
const QRCode = require('qrcode');
const { client, scanKeys } = require('../redisClient');

/**
 * Generate a short 6-character join code using a CSPRNG (crypto) instead of
 * Math.random(). Unbiased selection over the 32-char alphabet.
 */
function generateJoinCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // Excluding confusing chars
  const bytes = crypto.randomBytes(6);
  let code = '';
  for (let i = 0; i < 6; i++) {
    // 256 % 32 === 0, so modulo introduces no bias for this alphabet.
    code += chars.charAt(bytes[i] % chars.length);
  }
  return code;
}

/**
 * Generate a join code that is not already in use, retrying on collision.
 */
async function generateUniqueJoinCode() {
  for (let attempt = 0; attempt < 5; attempt++) {
    const code = generateJoinCode();
    const exists = await client.get(`code:${code}`);
    if (!exists) return code;
  }
  // Extremely unlikely; fall back to a longer code to guarantee uniqueness.
  return generateJoinCode() + generateJoinCode().slice(0, 2);
}

/**
 * Create a new translation session
 */
async function createSession() {
  const sessionId = uuidv4();
  const joinCode = await generateUniqueJoinCode();

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
    supportedLanguages: ['en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ar',
                         'sr', 'mk', 'bg', 'hu', 'ro', 'hr', 'sl', 'sk', 'pl', 'uk']
  };

  // Store in Redis with 13 hour expiration (12h max session + 1h grace)
  await client.setEx(`session:${sessionId}`, 46800, JSON.stringify(session));
  await client.setEx(`code:${joinCode}`, 46800, sessionId);

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

  await client.setEx(`session:${sessionId}`, 46800, JSON.stringify(session));
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

  await client.setEx(`session:${sessionId}`, 46800, JSON.stringify(session));
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
 * Get all languages that have active listeners in a session
 * @param {string} sessionId - Session ID
 * @returns {Promise<Set<string>>} Set of language codes with active listeners
 */
async function getSessionListenerLanguages(sessionId) {
  const session = await getSession(sessionId);
  const languages = new Set();
  
  if (session && session.listeners) {
    for (const [language, listenerIds] of Object.entries(session.listeners)) {
      if (listenerIds && listenerIds.length > 0) {
        languages.add(language);
      }
    }
  }
  
  return languages;
}

/**
 * Set speaker connection status
 */
async function setSpeakerConnected(sessionId, connected) {
  const session = await getSession(sessionId);
  if (session) {
    session.speakerConnected = connected;
    await client.setEx(`session:${sessionId}`, 46800, JSON.stringify(session));
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

/**
 * Get all active sessions from Redis (used by watchdog)
 */
async function getAllActiveSessions() {
  try {
    const keys = await scanKeys('session:*');
    const sessions = [];
    for (const key of keys) {
      const data = await client.get(key);
      if (data) sessions.push(JSON.parse(data));
    }
    return sessions;
  } catch (err) {
    console.error('Error scanning sessions:', err);
    return [];
  }
}

module.exports = {
  createSession,
  getSession,
  addListener,
  removeListener,
  getListenersByLanguage,
  getSessionListenerLanguages,
  setSpeakerConnected,
  endSession,
  getSessionStats,
  getAllActiveSessions
};