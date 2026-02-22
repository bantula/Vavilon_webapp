const express = require('express');
const router = express.Router();
const axios = require('axios');
const { broadcastToListeners } = require('../websocket/wsHandler');
const { getSessionListenerLanguages } = require('../services/sessionService');

const AI_SERVICE_URL = process.env.AI_SERVICE_URL || 'http://localhost:5000';

// TTS guard: track expected TTS per segment
// Map<segmentId, { sessionId, expectedLanguages: Set, received: Set, timer: NodeJS.Timeout }>
const pendingTts = new Map();
const TTS_GUARD_TIMEOUT_MS = 10000;

function slog(level, step, fields = {}) {
  const entry = {
    ts: new Date().toISOString(),
    level,
    component: 'node',
    step,
    ...fields
  };
  console.log(JSON.stringify(entry));
}

/**
 * POST /api/events
 * Receive events from Python AI service:
 * - segment_finalized: recognition complete, broadcast subtitles, trigger TTS
 * - tts_ready: TTS synthesis complete, broadcast audio to listeners
 */
router.post('/', async (req, res) => {
  try {
    const event = req.body;
    const eventType = event.type;

    if (!eventType) {
      return res.status(400).json({
        success: false,
        error: 'Missing event type'
      });
    }

    // Handle segment_finalized event
    if (eventType === 'segment_finalized') {
      const { sessionId, segmentId, translations, recognizedText, traceId } = event;

      if (!sessionId || !segmentId || !translations) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields for segment_finalized'
        });
      }

      // Compute active listener languages from Redis session data
      const activeLanguages = await getSessionListenerLanguages(sessionId);

      // Filter translations to only languages with active listeners (subtitles + TTS)
      const activeTranslations = {};
      for (const [lang, text] of Object.entries(translations)) {
        if (activeLanguages.has(lang)) activeTranslations[lang] = text;
      }

      slog('info', 'segment_finalized_received', {
        sessionId,
        segmentId,
        traceId,
        activeLanguages: [...activeLanguages],
        activeTranslations: Object.fromEntries(
          Object.entries(activeTranslations).map(([l, t]) => [l, t.substring(0, 60)])
        ),
        recognizedText: recognizedText?.substring(0, 60)
      });

      // Broadcast subtitles only to languages that have active listeners
      for (const [lang, text] of Object.entries(activeTranslations)) {
        await broadcastToListeners(sessionId, lang, null, text, recognizedText);
      }

      const ttsTranslations = activeTranslations;

      const ttsLanguagesRequested = Object.keys(ttsTranslations);

      slog('info', 'tts_languages_requested', {
        sessionId,
        segmentId,
        traceId,
        ttsLanguagesRequested
      });

      // Request TTS from Python for active languages only
      if (ttsLanguagesRequested.length > 0) {
        // Set up TTS guard before sending request
        const guardTimer = setTimeout(() => {
          const pending = pendingTts.get(segmentId);
          if (pending) {
            const missing = [...pending.expectedLanguages].filter(l => !pending.received.has(l));
            if (missing.length > 0) {
              slog('warn', 'missing_tts_for_active_language', {
                sessionId: pending.sessionId,
                segmentId,
                traceId,
                expectedLanguages: [...pending.expectedLanguages],
                receivedLanguages: [...pending.received],
                missingLanguages: missing,
                timeout_ms: TTS_GUARD_TIMEOUT_MS
              });
            }
            pendingTts.delete(segmentId);
          }
        }, TTS_GUARD_TIMEOUT_MS);

        pendingTts.set(segmentId, {
          sessionId,
          expectedLanguages: new Set(ttsLanguagesRequested),
          received: new Set(),
          timer: guardTimer,
          timestamp: Date.now()
        });

        // Fire-and-forget: don't block the event response
        axios.post(`${AI_SERVICE_URL}/generate-tts`, {
          sessionId,
          segmentId,
          translations: ttsTranslations
        }, { timeout: 5000 })
          .then((resp) => {
            slog('info', 'generate_tts_sent', {
              sessionId,
              segmentId,
              traceId,
              url: `${AI_SERVICE_URL}/generate-tts`,
              requestBody: {
                sessionId,
                segmentId,
                translationsCount: Object.keys(ttsTranslations).length,
                languages: Object.keys(ttsTranslations)
              },
              responseStatus: resp.status,
              responseData: resp.data,
              enqueued: resp.data.enqueued
            });
          })
          .catch((error) => {
            slog('error', 'generate_tts_fail', {
              sessionId,
              segmentId,
              traceId,
              error: error.response?.data || error.message,
              errorStatus: error.response?.status,
              errorCode: error.code
            });
          });
      }

      return res.json({
        success: true,
        message: 'Segment finalized, subtitles broadcast, TTS requested',
        segmentId,
        ttsLanguagesRequested
      });
    }

    // Handle tts_ready event
    if (eventType === 'tts_ready') {
      const { sessionId, segmentId, language, audioBytesBase64, traceId } = event;

      if (!sessionId || !segmentId || !language || !audioBytesBase64) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields for tts_ready'
        });
      }
      
      const audioSizeBytes = Buffer.from(audioBytesBase64, 'base64').length;
      const pending = pendingTts.get(segmentId);
      const timeSinceSegmentMs = pending ? Date.now() - pending.timestamp : null;

      slog('info', 'tts_ready_received', {
        sessionId,
        segmentId,
        traceId,
        language,
        audioBytes: audioSizeBytes,
        audioSizeKB: (audioSizeBytes / 1024).toFixed(2),
        timeSinceSegmentMs
      });

      // Update TTS guard tracking
      if (pending) {
        pending.received.add(language);
        // If all expected TTS received, clear the guard early
        if ([...pending.expectedLanguages].every(l => pending.received.has(l))) {
          clearTimeout(pending.timer);
          pendingTts.delete(segmentId);
          slog('info', 'tts_all_received', {
            sessionId,
            segmentId,
            traceId,
            languages: [...pending.received],
            totalTimeMs: Date.now() - pending.timestamp
          });
        }
      }

      // Broadcast audio to listeners of this language
      await broadcastToListeners(sessionId, language, audioBytesBase64, null);

      return res.json({
        success: true,
        message: 'TTS audio broadcast',
        segmentId,
        language
      });
    }

    // Unknown event type
    return res.status(400).json({
      success: false,
      error: `Unknown event type: ${eventType}`
    });

  } catch (error) {
    console.error('Error handling event:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to handle event'
    });
  }
});

module.exports = router;
