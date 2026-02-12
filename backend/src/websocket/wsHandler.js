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
const connections = new Map(); // connectionId -> { ws, sessionId, role, language, sourceLanguage }

/**
 * Create a WAV (RIFF) header for raw PCM data.
 * Used by bypass mode to wrap speaker's raw PCM so the browser's
 * decodeAudioData() can decode it without any changes to the listener.
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

  // Store source language for bypass routing
  conn.sourceLanguage = sourceLanguage || 'en-US';

  try {
    await axios.post(`${AI_SERVICE_URL}/start-session`, {
      sessionId: conn.sessionId,
      sourceLanguage: conn.sourceLanguage,
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
 * Forward audio chunk (JSON base64) to AI service,
 * and bypass-stream to same-language listeners.
 */
async function handleAudioChunk(connectionId, payload) {
  const conn = connections.get(connectionId);
  if (!conn || conn.role !== 'speaker') return;

  // Forward to AI service for translation (existing flow)
  forwardAudioToAI(conn.sessionId, payload.audioData);

  // Bypass: stream raw audio directly to same-language listeners
  if (conn.sourceLanguage) {
    const sourceShort = conn.sourceLanguage.split('-')[0]; // 'en-US' → 'en'
    broadcastBypassAudio(conn.sessionId, sourceShort, payload.audioData);
  }
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
 * Bypass mode: wrap raw PCM in a WAV header and stream directly to
 * listeners whose selected language matches the speaker's source language.
 * Uses in-memory connections Map (no Redis roundtrip) for low latency.
 */
function broadcastBypassAudio(sessionId, language, pcmBase64) {
  // Find same-language listeners from in-memory Map (O(n) but fast for <200 conns)
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

  // Wrap PCM in WAV header once for all listeners
  const pcmBuffer = Buffer.from(pcmBase64, 'base64');
  const wavHeader = createWavHeader(pcmBuffer.length);
  const wavBuffer = Buffer.concat([wavHeader, pcmBuffer]);
  const wavBase64 = wavBuffer.toString('base64');

  // Pre-stringify so we don't repeat JSON.stringify per listener
  const message = JSON.stringify({
    type: 'bypass_audio',
    payload: { audioData: wavBase64 }
  });

  for (const ws of listeners) {
    ws.send(message);
  }
}

/**
 * Broadcast translated audio and/or subtitles to listeners.
 * Called by the AI service via POST /api/broadcast.
 */
async function broadcastToListeners(sessionId, language, audioData, subtitleText) {
  const listenerIds = await getListenersByLanguage(sessionId, language);

  console.log(`Broadcasting to ${listenerIds.length} listeners for ${language} in session ${sessionId}`);
  if (audioData) {
    console.log(`  Audio data length: ${audioData.length} chars`);
  }
  if (subtitleText) {
    console.log(`  Subtitle: "${subtitleText}"`);
  }

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
        console.log(`  Sending audio to connection ${connectionId}`);
        sendMessage(connectionId, {
          type: 'audio',
          payload: { audioData, language }
        });
      }
    } else {
      console.log(`  Connection ${connectionId} is not open (state: ${conn?.ws.readyState})`);
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
