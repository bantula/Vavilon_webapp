const WebSocket = require('ws');
const axios = require('axios');
const { getAllActiveSessions, getSession, endSession } = require('./sessionService');

const AI_SERVICE_URL = process.env.AI_SERVICE_URL || 'http://localhost:5000';

const WATCHDOG_INTERVAL_MS    = 5 * 60 * 1000;      // run every 5 minutes
const GHOST_AUDIO_LIMIT_MS    = 3 * 60 * 60 * 1000; // kill session if no audio for 3h
const MAX_SESSION_DURATION_MS = 12 * 60 * 60 * 1000; // kill session after 12h
const ORPHAN_IDLE_LIMIT_MS    = 30 * 60 * 1000;      // kill Redis-only session after 30 min idle

function slog(level, step, fields = {}) {
  console.log(JSON.stringify({
    ts: new Date().toISOString(),
    level,
    component: 'watchdog',
    step,
    ...fields
  }));
}

/**
 * Notify the AI service to end a session.
 * Fire-and-forget with logging. Never throws — Redis cleanup must always proceed.
 */
async function notifyAiEndSession(sessionId, traceId, reason) {
  try {
    await axios.post(`${AI_SERVICE_URL}/end-session`, {
      sessionId,
      traceId: traceId || null,
      graceful: false
    }, { timeout: 5000 });
    slog('info', 'ai_end_session_sent', { sessionId, reason });
  } catch (err) {
    slog('warn', 'ai_end_session_fail', { sessionId, reason, error: err.message });
  }
}

/**
 * Force-end a session regardless of speaker/listener state:
 * 1. Send 'session_ended' to speaker WS, then terminate it
 * 2. Send 'speaker_disconnected' to all listener WSs, then terminate them
 * 3. Call AI /end-session (fire-and-forget)
 * 4. Delete Redis session keys
 *
 * ws.terminate() fires the 'close' event which calls handleDisconnect(),
 * so connections are removed from the Map on the next event loop tick.
 */
async function forceEndSession(connections, sessionId, reason) {
  slog('info', 'force_end_start', { sessionId, reason });

  let speakerTraceId = null;
  let listenersNotified = 0;

  for (const [, conn] of connections) {
    if (conn.sessionId !== sessionId) continue;

    if (conn.role === 'speaker') {
      speakerTraceId = conn.traceId;
      if (conn.ws.readyState === WebSocket.OPEN) {
        try {
          conn.ws.send(JSON.stringify({ type: 'session_ended', payload: { reason } }));
        } catch (_) { /* ignore send errors before terminate */ }
        conn.ws.terminate();
      }
    } else if (conn.role === 'listener') {
      if (conn.ws.readyState === WebSocket.OPEN) {
        try {
          conn.ws.send(JSON.stringify({ type: 'speaker_disconnected', payload: { reason } }));
        } catch (_) {}
        conn.ws.terminate();
        listenersNotified++;
      }
    }
  }

  slog('info', 'force_end_broadcast', { sessionId, listenersNotified });

  await notifyAiEndSession(sessionId, speakerTraceId, reason);
  await endSession(sessionId);

  slog('info', 'force_end_complete', { sessionId, reason });
}

/**
 * Single watchdog tick — runs every WATCHDOG_INTERVAL_MS.
 */
async function watchdogTick(connections) {
  const now = Date.now();
  const checkedSessions = new Set();
  let killed = 0;

  slog('info', 'tick_start', { connectionCount: connections.size });

  // Part A: check active speaker connections (in-memory)
  for (const [, conn] of connections) {
    if (conn.role !== 'speaker' || !conn.sessionId) continue;
    const sessionId = conn.sessionId;
    if (checkedSessions.has(sessionId)) continue;
    checkedSessions.add(sessionId);

    // Fetch session from Redis to get createdAt
    let session;
    try {
      session = await getSession(sessionId);
    } catch (err) {
      slog('warn', 'tick_session_fetch_fail', { sessionId, error: err.message });
      continue;
    }

    // Redis expired but WebSocket is still alive (Scenario 8)
    if (!session) {
      slog('warn', 'tick_redis_expired_ws_alive', { sessionId });
      await forceEndSession(connections, sessionId, 'redis_expired');
      killed++;
      continue;
    }

    const sessionAge = now - new Date(session.createdAt).getTime();

    // Check 1: max session duration (12h)
    if (sessionAge > MAX_SESSION_DURATION_MS) {
      slog('warn', 'tick_max_duration', {
        sessionId,
        ageHours: (sessionAge / 3600000).toFixed(2)
      });
      await forceEndSession(connections, sessionId, 'max_duration_exceeded');
      killed++;
      continue;
    }

    // Check 2: ghost session — no audio for 3h
    // If lastAudioAt is null, speaker connected but never spoke; measure idle from createdAt
    const idleMs = conn.lastAudioAt
      ? now - conn.lastAudioAt
      : now - new Date(session.createdAt).getTime();

    if (idleMs > GHOST_AUDIO_LIMIT_MS) {
      slog('warn', 'tick_ghost_session', {
        sessionId,
        idleHours: (idleMs / 3600000).toFixed(2),
        hadAudio: conn.lastAudioAt !== null
      });
      await forceEndSession(connections, sessionId, 'audio_inactivity_timeout');
      killed++;
      continue;
    }
  }

  // Part B: scan Redis for sessions with no active WS at all
  // (e.g. Node restarted mid-session, speaker walked away without clean disconnect)
  let redisSessions = [];
  try {
    redisSessions = await getAllActiveSessions();
  } catch (err) {
    slog('warn', 'tick_redis_scan_fail', { error: err.message });
  }

  const activeSessionIds = new Set(
    [...connections.values()].filter(c => c.sessionId).map(c => c.sessionId)
  );

  for (const session of redisSessions) {
    if (activeSessionIds.has(session.id)) continue; // has live WS, already checked above

    const age = now - new Date(session.createdAt).getTime();
    if (age > ORPHAN_IDLE_LIMIT_MS) {
      slog('warn', 'tick_orphan_session', {
        sessionId: session.id,
        ageMinutes: (age / 60000).toFixed(1)
      });
      await forceEndSession(connections, session.id, 'orphan_no_connections');
      killed++;
    }
  }

  slog('info', 'tick_complete', {
    activeSpeakerSessionsChecked: checkedSessions.size,
    redisSessionsScanned: redisSessions.length,
    sessionKilled: killed
  });
}

/**
 * Start the watchdog. Call from index.js after setupWebSocket().
 * @param {Map} connections - direct reference to wsHandler's live connections Map
 */
function startWatchdog(connections) {
  slog('info', 'watchdog_start', {
    intervalMinutes: WATCHDOG_INTERVAL_MS / 60000,
    ghostLimitHours: GHOST_AUDIO_LIMIT_MS / 3600000,
    maxDurationHours: MAX_SESSION_DURATION_MS / 3600000,
    orphanLimitMinutes: ORPHAN_IDLE_LIMIT_MS / 60000
  });

  const interval = setInterval(async () => {
    try {
      await watchdogTick(connections);
    } catch (err) {
      // Watchdog must never crash the process
      slog('error', 'tick_uncaught', { error: err.message, stack: err.stack });
    }
  }, WATCHDOG_INTERVAL_MS);

  interval.unref(); // don't prevent graceful Node shutdown
}

module.exports = { startWatchdog };
