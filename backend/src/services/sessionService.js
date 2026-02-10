const { v4: uuidv4 } = require('uuid');
const QRCode = require('qrcode');

// In-memory session store (use Redis in production)
const sessions = new Map();

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
    createdAt: new Date(),
    isActive: true,
    speakerConnected: false,
    listeners: new Map(), // languageCode -> Set of connectionIds
    supportedLanguages: ['en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ar']
  };

  sessions.set(sessionId, session);
  sessions.set(joinCode, sessionId); // Map code to ID for quick lookup

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
function getSession(idOrCode) {
  // Check if it's a join code first
  if (sessions.has(idOrCode) && typeof sessions.get(idOrCode) === 'string') {
    const sessionId = sessions.get(idOrCode);
    return sessions.get(sessionId);
  }
  // Otherwise treat as session ID
  return sessions.get(idOrCode);
}

/**
 * Add listener to session
 */
function addListener(sessionId, connectionId, language) {
  const session = sessions.get(sessionId);
  if (!session) return false;

  if (!session.listeners.has(language)) {
    session.listeners.set(language, new Set());
  }

  session.listeners.get(language).add(connectionId);
  console.log(`✓ Listener ${connectionId} joined session ${sessionId} (${language})`);

  return true;
}

/**
 * Remove listener from session
 */
function removeListener(sessionId, connectionId, language) {
  const session = sessions.get(sessionId);
  if (!session) return;

  if (session.listeners.has(language)) {
    session.listeners.get(language).delete(connectionId);

    // Clean up empty language sets
    if (session.listeners.get(language).size === 0) {
      session.listeners.delete(language);
    }
  }

  console.log(`✓ Listener ${connectionId} left session ${sessionId}`);
}

/**
 * Get all listener connection IDs for a specific language in a session
 */
function getListenersByLanguage(sessionId, language) {
  const session = sessions.get(sessionId);
  if (!session || !session.listeners.has(language)) {
    return [];
  }
  return Array.from(session.listeners.get(language));
}

/**
 * Set speaker connection status
 */
function setSpeakerConnected(sessionId, connected) {
  const session = sessions.get(sessionId);
  if (session) {
    session.speakerConnected = connected;
  }
}

/**
 * End session and cleanup
 */
function endSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return;

  session.isActive = false;
  sessions.delete(session.joinCode);
  sessions.delete(sessionId);

  console.log(`✓ Session ended: ${sessionId}`);
}

/**
 * Get session statistics
 */
function getSessionStats(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return null;

  let totalListeners = 0;
  const languageBreakdown = {};

  session.listeners.forEach((listeners, language) => {
    const count = listeners.size;
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