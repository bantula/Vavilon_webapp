/**
 * Translation sessions — in-memory store.
 *
 * Sessions are short-lived (one per live tour) and only ever used by this single
 * backend instance, so they live in a plain Map instead of Redis. A session that
 * outlives its TTL is treated as gone (and a live tour that survives a backend
 * restart simply has the guide press "Start Speaking" again).
 */
const { v4: uuidv4 } = require('uuid');
const crypto = require('crypto');
const QRCode = require('qrcode');

const SESSION_TTL_MS = 46800 * 1000; // 13h (12h max session + 1h grace)

const sessions = new Map();  // sessionId -> { session, expiresAt }
const codeIndex = new Map(); // joinCode -> sessionId

// Periodically purge expired sessions so the maps don't grow unbounded.
const _sweep = setInterval(() => {
  const now = Date.now();
  for (const [id, entry] of sessions) {
    if (entry.expiresAt <= now) {
      sessions.delete(id);
      codeIndex.delete(entry.session.joinCode);
    }
  }
}, 5 * 60 * 1000);
_sweep.unref();

function _store(session) {
  sessions.set(session.id, { session, expiresAt: Date.now() + SESSION_TTL_MS });
  codeIndex.set(session.joinCode, session.id);
}

function _read(sessionId) {
  const entry = sessions.get(sessionId);
  if (!entry) return null;
  if (entry.expiresAt <= Date.now()) {
    sessions.delete(sessionId);
    codeIndex.delete(entry.session.joinCode);
    return null;
  }
  return entry.session;
}

/**
 * Generate a short 6-character join code using a CSPRNG (crypto).
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

/** Generate a join code that is not currently in use. */
function generateUniqueJoinCode() {
  for (let attempt = 0; attempt < 5; attempt++) {
    const code = generateJoinCode();
    if (!codeIndex.has(code)) return code;
  }
  return generateJoinCode() + generateJoinCode().slice(0, 2);
}

/**
 * Create a new translation session
 */
async function createSession() {
  const sessionId = uuidv4();
  const joinCode = generateUniqueJoinCode();

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

  _store(session);
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
  const byId = _read(idOrCode);
  if (byId) return byId;

  const sessionId = codeIndex.get(idOrCode);
  if (sessionId) return _read(sessionId);

  return null;
}

/**
 * Add listener to session
 */
async function addListener(sessionId, connectionId, language) {
  const session = _read(sessionId);
  if (!session) return false;

  if (!session.listeners[language]) {
    session.listeners[language] = [];
  }
  if (!session.listeners[language].includes(connectionId)) {
    session.listeners[language].push(connectionId);
  }

  console.log(`✓ Listener ${connectionId} joined session ${sessionId} (${language})`);
  return true;
}

/**
 * Remove listener from session
 */
async function removeListener(sessionId, connectionId, language) {
  const session = _read(sessionId);
  if (!session) return;

  if (session.listeners[language]) {
    session.listeners[language] = session.listeners[language].filter(id => id !== connectionId);
    if (session.listeners[language].length === 0) {
      delete session.listeners[language];
    }
  }
  console.log(`✓ Listener ${connectionId} left session ${sessionId}`);
}

/**
 * Get all listener connection IDs for a specific language in a session
 */
async function getListenersByLanguage(sessionId, language) {
  const session = _read(sessionId);
  if (!session || !session.listeners[language]) return [];
  return session.listeners[language];
}

/**
 * Get all languages that have active listeners in a session
 */
async function getSessionListenerLanguages(sessionId) {
  const session = _read(sessionId);
  const languages = new Set();
  if (session && session.listeners) {
    for (const [language, listenerIds] of Object.entries(session.listeners)) {
      if (listenerIds && listenerIds.length > 0) languages.add(language);
    }
  }
  return languages;
}

/**
 * Set speaker connection status
 */
async function setSpeakerConnected(sessionId, connected) {
  const session = _read(sessionId);
  if (session) session.speakerConnected = connected;
}

/**
 * End session and cleanup
 */
async function endSession(sessionId) {
  const entry = sessions.get(sessionId);
  if (!entry) return;
  sessions.delete(sessionId);
  codeIndex.delete(entry.session.joinCode);
  console.log(`✓ Session ended: ${sessionId}`);
}

/**
 * Get session statistics
 */
async function getSessionStats(sessionId) {
  const session = _read(sessionId);
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
 * Get all active sessions (used by watchdog)
 */
async function getAllActiveSessions() {
  const now = Date.now();
  const out = [];
  for (const entry of sessions.values()) {
    if (entry.expiresAt > now) out.push(entry.session);
  }
  return out;
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
