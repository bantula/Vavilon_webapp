const express = require('express');
const router = express.Router();
const { broadcastToListeners, updateActiveLanguages } = require('../websocket/wsHandler');

/**
 * POST /api/events
 * Receive events from Python AI service:
 * - segment_finalized: recognition complete, broadcast subtitles immediately
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

      console.log(JSON.stringify({
        ts: new Date().toISOString(),
        level: 'info',
        component: 'node',
        step: 'segment_finalized_received',
        sessionId,
        segmentId,
        traceId,
        translationCount: Object.keys(translations).length,
        recognizedText: recognizedText?.substring(0, 60)
      }));

      // Broadcast subtitles to all listeners immediately (per language)
      for (const [lang, text] of Object.entries(translations)) {
        await broadcastToListeners(sessionId, lang, null, text);
      }

      return res.json({
        success: true,
        message: 'Segment finalized, subtitles broadcast',
        segmentId
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

      console.log(JSON.stringify({
        ts: new Date().toISOString(),
        level: 'info',
        component: 'node',
        step: 'tts_ready_received',
        sessionId,
        segmentId,
        traceId,
        language,
        audioBytes: Buffer.from(audioBytesBase64, 'base64').length
      }));

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
