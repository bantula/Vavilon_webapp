const WebSocket = require('ws');
const { v4: uuidv4 } = require('uuid');
const axios = require('axios');
const {
  getSession,
  addListener,
  removeListener,
  getListenersByLanguage,
  setSpeakerConnected
} = require('../services/sessionService');

const AI_SERVICE_URL = process.env.AI_SERVICE_URL || 'http://localhost:5000';

// Track all WebSocket connections
const connections = new Map(); // connectionId -> { ws, sessionId, role, language }

/**
 * Setup WebSocket server
 */
function setupWebSocket(server) {
  const wss = new WebSocket.Server({ server, path: '/ws' });

  wss.on('connection', (ws) => {
    const connectionId = uuidv4();
    console.log(`WebSocket connected: ${connectionId}`);

    // Initialize connection metadata
    connections.set(connectionId, {
      ws,
      sessionId: null,
      role: null, // 'speaker' or 'listener'
      language: null
    });

    ws.on('message', async (message) => {
      try {
        const data = JSON.parse(message);
        await handleMessage(connectionId, data);
      } catch (error) {
        // If not JSON, might be binary audio data
        handleBinaryMessage(connectionId, message);
      }
    });

    ws.on('close', () => {
      handleDisconnect(connectionId);
    });

    ws.on('error', (error) => {
      console.error(`WebSocket error (${connectionId}):`, error);
      handleDisconnect(connectionId);
    });
  });

  console.log('✓ WebSocket server setup complete');
}

/**
 * Handle incoming WebSocket messages
 */
async function handleMessage(connectionId, data) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  const { type, payload } = data;

  switch (type) {
    case 'speaker_join':
      await handleSpeakerJoin(connectionId, payload);
      break;

    case 'listener_join':
      await handleListenerJoin(connectionId, payload);
      break;

    case 'audio_chunk':
      await handleAudioChunk(connectionId, payload);
      break;

    case 'speaker_disconnect':
      handleSpeakerDisconnect(connectionId);
      break;

    default:
      console.warn(`Unknown message type: ${type}`);
  }
}

/**
 * Handle binary audio data from speaker
 */
function handleBinaryMessage(connectionId, buffer) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  // Forward audio to AI service for processing
  forwardAudioToAI(conn.sessionId, buffer);
}

/**
 * Handle speaker joining a session
 */
async function handleSpeakerJoin(connectionId, payload) {
  const { sessionId } = payload;
  const session = await getSession(sessionId);

  if (!session) {
    sendError(connectionId, 'Session not found');
    return;
  }

  const conn = connections.get(connectionId);
  conn.sessionId = sessionId;
  conn.role = 'speaker';

  await setSpeakerConnected(sessionId, true);

  // Notify speaker of successful join
  sendMessage(connectionId, {
    type: 'speaker_joined',
    payload: {
      sessionId,
      supportedLanguages: session.supportedLanguages
    }
  });

  console.log(`✓ Speaker joined session: ${sessionId}`);
}

/**
 * Handle listener joining a session
 */
async function handleListenerJoin(connectionId, payload) {
  const { sessionId, joinCode, language } = payload;

  // Get session by ID or join code
  const session = await getSession(sessionId || joinCode);

  if (!session) {
    sendError(connectionId, 'Session not found');
    return;
  }

  if (!session.supportedLanguages.includes(language)) {
    sendError(connectionId, 'Language not supported');
    return;
  }

  const conn = connections.get(connectionId);
  conn.sessionId = session.id;
  conn.role = 'listener';
  conn.language = language;

  await addListener(session.id, connectionId, language);

  // Notify listener of successful join
  sendMessage(connectionId, {
    type: 'listener_joined',
    payload: {
      sessionId: session.id,
      language
    }
  });

  console.log(`✓ Listener joined session: ${session.id} (${language})`);
}

/**
 * Handle audio chunk from speaker (JSON-based)
 */
async function handleAudioChunk(connectionId, payload) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  // Forward to AI service
  forwardAudioToAI(conn.sessionId, payload.audioData);
}

/**
 * Forward speaker audio to Python AI service
 */
async function forwardAudioToAI(sessionId, audioData) {
  try {
    // Send audio to AI service for STT + Translation + TTS
    await axios.post(`${AI_SERVICE_URL}/process-audio`, {
      sessionId,
      audioData: audioData.toString('base64')
    }, {
      timeout: 30000 // 30s timeout
    });
  } catch (error) {
    console.error('Error forwarding audio to AI service:', error.message);
  }
}

/**
 * Broadcast translated audio and subtitles to listeners
 * Called by AI service webhook or polling
 */
async function broadcastToListeners(sessionId, language, audioData, subtitleText) {
  const listenerIds = await getListenersByLanguage(sessionId, language);

  listenerIds.forEach(connectionId => {
    const conn = connections.get(connectionId);
    if (conn && conn.ws.readyState === WebSocket.OPEN) {
      // Send subtitle text
      if (subtitleText) {
        sendMessage(connectionId, {
          type: 'subtitle',
          payload: {
            text: subtitleText,
            language
          }
        });
      }

      // Send audio chunk
      if (audioData) {
        sendMessage(connectionId, {
          type: 'audio',
          payload: {
            audioData,
            language
          }
        });
      }
    }
  });

  console.log(`✓ Broadcast to ${listenerIds.length} listeners (${language})`);
}

/**
 * Handle speaker disconnect
 */
async function handleSpeakerDisconnect(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  await setSpeakerConnected(conn.sessionId, false);

  // Notify all listeners that speaker disconnected
  const session = await getSession(conn.sessionId);
  if (session && session.listeners) {
    for (const [language, listeners] of Object.entries(session.listeners)) {
      listeners.forEach(listenerId => {
        sendMessage(listenerId, {
          type: 'speaker_disconnected',
          payload: {}
        });
      });
    }
  }

  console.log(`✓ Speaker disconnected from session: ${conn.sessionId}`);
}

/**
 * Handle connection disconnect
 */
async function handleDisconnect(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  if (conn.role === 'speaker') {
    await handleSpeakerDisconnect(connectionId);
  } else if (conn.role === 'listener') {
    await removeListener(conn.sessionId, connectionId, conn.language);
  }

  connections.delete(connectionId);
  console.log(`✓ Connection closed: ${connectionId}`);
}

/**
 * Send message to a specific connection
 */
function sendMessage(connectionId, message) {
  const conn = connections.get(connectionId);
  if (conn && conn.ws.readyState === WebSocket.OPEN) {
    conn.ws.send(JSON.stringify(message));
  }
}

/**
 * Send error message
 */
function sendError(connectionId, errorMessage) {
  sendMessage(connectionId, {
    type: 'error',
    payload: { message: errorMessage }
  });
}

module.exports = {
  setupWebSocket,
  broadcastToListeners
};