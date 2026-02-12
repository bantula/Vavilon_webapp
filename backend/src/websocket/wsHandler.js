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
const connections = new Map(); // connectionId -> { ws, sessionId, role, language, sourceLanguage, traceId, seqNo }

// Structured log helper
function slog(level, component, step, fields = {}) {
  const entry = {
    ts: new Date().toISOString(),
    level,
    component,
    step,
    ...fields
  };
  console.log(JSON.stringify(entry));
}

/**
 * Create a WAV (RIFF) header for raw PCM data.
 */
function createWavHeader(dataLength, sampleRate = 16000, bitsPerSample = 16, channels = 1) {
  const header = Buffer.alloc(44);
  const byteRate = sampleRate * channels * bitsPerSample / 8;
  const blockAlign = channels * bitsPerSample / 8;

  header.write('RIFF', 0);
  header.writeUInt32LE(36 + dataLength, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); // PCM format
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write('data', 36);
  header.writeUInt32LE(dataLength, 40);

  return header;
}

function setupWebSocket(server) {
  const wss = new WebSocket.Server({ server, path: '/ws' });

  wss.on('connection', (ws) => {
    const connectionId = uuidv4();
    slog('info', 'node', 'ws_connect', { connectionId });

    connections.set(connectionId, {
      ws,
      sessionId: null,
      role: null,
      language: null,
      sourceLanguage: null,
      traceId: null,
      seqNo: 0
    });

    ws.on('message', async (message) => {
      try {
        const data = JSON.parse(message);
        await handleMessage(connectionId, data);
      } catch (error) {
        handleBinaryMessage(connectionId, message);
      }
    });

    ws.on('close', () => {
      handleDisconnect(connectionId);
    });

    ws.on('error', (error) => {
      slog('error', 'node', 'ws_error', { connectionId, error: error.message });
      handleDisconnect(connectionId);
    });
  });

  slog('info', 'node', 'ws_ready', { path: '/ws' });
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
      slog('warn', 'node', 'unknown_msg', { connectionId, type: data.type });
  }
}

function handleBinaryMessage(connectionId, buffer) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;
  forwardAudioToAI(conn.sessionId, buffer, conn);
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

  slog('info', 'node', 'speaker_join', { sessionId, connectionId });
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

  slog('info', 'node', 'listener_join', { sessionId: session.id, connectionId, language });
}

/**
 * Speaker clicked "Start Speaking" — generate trace_id, tell AI service.
 */
async function handleStartSpeaking(connectionId, payload) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  const { sourceLanguage, targetLanguages } = payload;

  // Generate trace_id for this speaking session
  conn.sourceLanguage = sourceLanguage || 'en-US';
  conn.traceId = uuidv4();
  conn.seqNo = 0;

  slog('info', 'node', 'start_speaking', {
    sessionId: conn.sessionId,
    traceId: conn.traceId,
    sourceLanguage: conn.sourceLanguage,
    targetLanguages: targetLanguages || ['es', 'fr', 'de']
  });

  try {
    const resp = await axios.post(`${AI_SERVICE_URL}/start-session`, {
      sessionId: conn.sessionId,
      traceId: conn.traceId,
      sourceLanguage: conn.sourceLanguage,
      targetLanguages: targetLanguages || ['es', 'fr', 'de']
    }, { timeout: 15000 });

    slog('info', 'node', 'ai_session_started', {
      sessionId: conn.sessionId,
      traceId: conn.traceId,
      aiResponse: resp.data
    });

    sendMessage(connectionId, {
      type: 'speaking_started',
      payload: { traceId: conn.traceId }
    });
  } catch (error) {
    const errData = error.response?.data || error.message;
    slog('error', 'node', 'ai_session_start_fail', {
      sessionId: conn.sessionId,
      traceId: conn.traceId,
      error: errData
    });
    sendError(connectionId, 'Failed to start translation session');
  }
}

/**
 * Speaker clicked "Stop Speaking".
 */
async function handleStopSpeaking(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  slog('info', 'node', 'stop_speaking', {
    sessionId: conn.sessionId,
    traceId: conn.traceId,
    totalChunks: conn.seqNo
  });

  try {
    await axios.post(`${AI_SERVICE_URL}/end-session`, {
      sessionId: conn.sessionId,
      traceId: conn.traceId
    }, { timeout: 10000 });
  } catch (error) {
    slog('error', 'node', 'ai_session_end_fail', {
      sessionId: conn.sessionId,
      error: error.message
    });
  }
}

/**
 * Forward audio chunk to AI + bypass to same-language listeners.
 */
async function handleAudioChunk(connectionId, payload) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  conn.seqNo++;

  // Forward to AI service for translation
  forwardAudioToAI(conn.sessionId, payload.audioData, conn);

  // Bypass: stream raw audio directly to same-language listeners
  if (conn.sourceLanguage) {
    const sourceShort = conn.sourceLanguage.split('-')[0];
    broadcastBypassAudio(conn.sessionId, sourceShort, payload.audioData);
  }
}

/**
 * Forward audio to AI service with trace metadata.
 */
async function forwardAudioToAI(sessionId, audioData, conn) {
  try {
    const base64Audio = typeof audioData === 'string'
      ? audioData
      : audioData.toString('base64');

    const seqNo = conn ? conn.seqNo : 0;
    const traceId = conn ? conn.traceId : null;

    // Log every 50th chunk to avoid log spam
    if (seqNo % 50 === 1) {
      slog('debug', 'node', 'forward_audio', {
        sessionId,
        traceId,
        seqNo,
        bytes: Buffer.from(base64Audio, 'base64').length
      });
    }

    await axios.post(`${AI_SERVICE_URL}/process-audio`, {
      sessionId,
      traceId,
      seqNo,
      audioData: base64Audio
    }, { timeout: 10000 });
  } catch (error) {
    if (!error.response || error.response.status !== 404) {
      slog('error', 'node', 'forward_audio_fail', {
        sessionId,
        error: error.response?.data || error.message,
        status: error.response?.status
      });
    }
  }
}

/**
 * Bypass mode: wrap raw PCM in WAV header and stream directly to
 * listeners whose selected language matches the speaker's source language.
 */
function broadcastBypassAudio(sessionId, language, pcmBase64) {
  const listeners = [];
  for (const [connId, conn] of connections) {
    if (conn.sessionId === sessionId &&
        conn.role === 'listener' &&
        conn.language === language &&
        conn.ws.readyState === WebSocket.OPEN) {
      listeners.push(conn.ws);
    }
  }

  if (listeners.length === 0) return;

  const pcmBuffer = Buffer.from(pcmBase64, 'base64');
  const wavHeader = createWavHeader(pcmBuffer.length);
  const wavBuffer = Buffer.concat([wavHeader, pcmBuffer]);
  const wavBase64 = wavBuffer.toString('base64');

  const message = JSON.stringify({
    type: 'bypass_audio',
    payload: { audioData: wavBase64 }
  });

  for (const ws of listeners) {
    ws.send(message);
  }
}

/**
 * Broadcast translated audio/subtitles to listeners.
 * Called by AI service via POST /api/broadcast.
 */
async function broadcastToListeners(sessionId, language, audioData, subtitleText) {
  const listenerIds = await getListenersByLanguage(sessionId, language);

  slog('info', 'node', 'broadcast', {
    sessionId,
    language,
    listenerCount: listenerIds.length,
    hasAudio: !!audioData,
    audioLen: audioData ? audioData.length : 0,
    hasSubtitle: !!subtitleText,
    subtitleText: subtitleText ? subtitleText.substring(0, 80) : null
  });

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
    } else {
      slog('warn', 'node', 'broadcast_skip', {
        connectionId,
        reason: 'not_open',
        state: conn?.ws.readyState
      });
    }
  });
}

async function handleSpeakerDisconnect(connectionId) {
  const conn = connections.get(connectionId);
  if (!conn) return;

  try {
    await axios.post(`${AI_SERVICE_URL}/end-session`, {
      sessionId: conn.sessionId,
      traceId: conn.traceId
    }, { timeout: 5000 });
  } catch (error) {
    // ignore
  }

  await setSpeakerConnected(conn.sessionId, false);

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

  slog('info', 'node', 'speaker_disconnect', { sessionId: conn.sessionId, traceId: conn.traceId });
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
