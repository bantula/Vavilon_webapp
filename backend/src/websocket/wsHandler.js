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

function setupWebSocket(server) {
  const wss = new WebSocket.Server({ server, path: '/ws' });

  wss.on('connection', (ws) => {
    const connectionId = uuidv4();
    console.log(`WebSocket connected: ${connectionId}`);

    connections.set(connectionId, {
      ws,
      sessionId: null,
      role: null,
      language: null
    });

    ws.on('message', async (message) => {
      try {
        const data = JSON.parse(message);
        await handleMessage(connectionId, data);
      } catch (error) {
        // Not JSON = binary audio data from speaker
        handleBinaryMessage(connectionId, message);
      }
    });

    ws.on('close', () => {
      handleDisconnect(connectionId);
    });

    ws.on('error', (error) => {
      console.error(`WebSocket error (${connectionId}):`, error.message);
      handleDisconnect(connectionId);
    });
  });

  console.log('WebSocket server ready');
}

async function handleMessage(connectionId, data) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  switch (data.type) {
    case 'speaker_join':
      await handleSpeakerJoin(connectionId, data.payload);
      break;
    case 'listener_join':
      await handleListenerJoin(connectionId, data.payload);
      break;
    case 'audio_chunk':
      await handleAudioChunk(connectionId, data.payload);
      break;
    case 'start_speaking':
      await handleStartSpeaking(connectionId, data.payload);
      break;
    case 'stop_speaking':
      await handleStopSpeaking(connectionId);
      break;
    case 'speaker_disconnect':
      await handleSpeakerDisconnect(connectionId);
      break;
    default:
      console.warn(`Unknown message type: ${data.type}`);
  }
}

function handleBinaryMessage(connectionId, buffer) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;
  forwardAudioToAI(conn.sessionId, buffer);
}

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

  sendMessage(connectionId, {
    type: 'speaker_joined',
    payload: {
      sessionId,
      supportedLanguages: session.supportedLanguages
    }
  });

  console.log(`Speaker joined session: ${sessionId}`);
}

async function handleListenerJoin(connectionId, payload) {
  const { sessionId, joinCode, language } = payload;
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

  sendMessage(connectionId, {
    type: 'listener_joined',
    payload: { sessionId: session.id, language }
  });

  console.log(`Listener joined session: ${session.id} (${language})`);
}

/**
 * Speaker clicked "Start Speaking" - tell AI service to create a
 * persistent TranslationSession with continuous recognition.
 */
async function handleStartSpeaking(connectionId, payload) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  const { sourceLanguage, targetLanguages } = payload;

  try {
    await axios.post(`${AI_SERVICE_URL}/start-session`, {
      sessionId: conn.sessionId,
      sourceLanguage: sourceLanguage || 'en-US',
      targetLanguages: targetLanguages || ['es', 'fr', 'de']
    }, { timeout: 10000 });

    console.log(`AI session started for ${conn.sessionId}`);

    sendMessage(connectionId, {
      type: 'speaking_started',
      payload: {}
    });
  } catch (error) {
    console.error('Error starting AI session:', error.message);
    sendError(connectionId, 'Failed to start translation session');
  }
}

/**
 * Speaker clicked "Stop Speaking" - tell AI service to stop.
 */
async function handleStopSpeaking(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  try {
    await axios.post(`${AI_SERVICE_URL}/end-session`, {
      sessionId: conn.sessionId
    }, { timeout: 10000 });

    console.log(`AI session ended for ${conn.sessionId}`);
  } catch (error) {
    console.error('Error ending AI session:', error.message);
  }
}

/**
 * Forward audio chunk (JSON base64) to AI service.
 */
async function handleAudioChunk(connectionId, payload) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  forwardAudioToAI(conn.sessionId, payload.audioData);
}

/**
 * Forward audio data to the AI service's /process-audio endpoint.
 * The AI service pushes it into the PushAudioInputStream for
 * continuous recognition.
 */
async function forwardAudioToAI(sessionId, audioData) {
  try {
    const base64Audio = typeof audioData === 'string'
      ? audioData
      : audioData.toString('base64');

    await axios.post(`${AI_SERVICE_URL}/process-audio`, {
      sessionId,
      audioData: base64Audio
    }, { timeout: 10000 });
  } catch (error) {
    // Don't spam logs - only log non-404 errors (404 = session not started yet)
    if (!error.response || error.response.status !== 404) {
      console.error('Error forwarding audio:', error.message);
    }
  }
}

/**
 * Broadcast translated audio and/or subtitles to listeners.
 * Called by the AI service via POST /api/broadcast.
 */
async function broadcastToListeners(sessionId, language, audioData, subtitleText) {
  const listenerIds = await getListenersByLanguage(sessionId, language);

  listenerIds.forEach(connectionId => {
    const conn = connections.get(connectionId);
    if (conn && conn.ws.readyState === WebSocket.OPEN) {
      if (subtitleText) {
        sendMessage(connectionId, {
          type: 'subtitle',
          payload: { text: subtitleText, language }
        });
      }
      if (audioData) {
        sendMessage(connectionId, {
          type: 'audio',
          payload: { audioData, language }
        });
      }
    }
  });

  if (listenerIds.length > 0) {
    console.log(`Broadcast to ${listenerIds.length} listeners (${language})`);
  }
}

async function handleSpeakerDisconnect(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  // Stop AI session
  try {
    await axios.post(`${AI_SERVICE_URL}/end-session`, {
      sessionId: conn.sessionId
    }, { timeout: 5000 });
  } catch (error) {
    // ignore
  }

  await setSpeakerConnected(conn.sessionId, false);

  // Notify listeners
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

  console.log(`Speaker disconnected from session: ${conn.sessionId}`);
}

async function handleDisconnect(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  if (conn.role === 'speaker') {
    await handleSpeakerDisconnect(connectionId);
  } else if (conn.role === 'listener') {
    await removeListener(conn.sessionId, connectionId, conn.language);
  }

  connections.delete(connectionId);
}

function sendMessage(connectionId, message) {
  const conn = connections.get(connectionId);
  if (conn && conn.ws.readyState === WebSocket.OPEN) {
    conn.ws.send(JSON.stringify(message));
  }
}

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
