const express = require('express');
const router = express.Router();
const { broadcastToListeners } = require('../websocket/wsHandler');

/**
 * POST /api/broadcast
 * Receive translated audio and subtitles from AI service
 * Broadcast to all listeners for that language
 */
router.post('/', async (req, res) => {
  try {
    const { sessionId, language, audioData, subtitleText } = req.body;

    if (!sessionId || !language) {
      return res.status(400).json({
        success: false,
        error: 'Missing sessionId or language'
      });
    }

    // Broadcast to all listeners of this language
    await broadcastToListeners(sessionId, language, audioData, subtitleText);

    res.json({
      success: true,
      message: 'Broadcast sent'
    });

  } catch (error) {
    console.error('Error broadcasting:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to broadcast'
    });
  }
});

module.exports = router;