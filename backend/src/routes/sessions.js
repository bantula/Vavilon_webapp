const express = require('express');
const router = express.Router();
const {
  createSession,
  getSession,
  getSessionStats,
  endSession
} = require('../services/sessionService');

/**
 * POST /api/sessions
 * Create a new translation session
 */
router.post('/', async (req, res) => {
  try {
    const session = await createSession();
    res.json({
      success: true,
      session
    });
  } catch (error) {
    console.error('Error creating session:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to create session'
    });
  }
});

/**
 * GET /api/sessions/:idOrCode
 * Get session details by ID or join code
 */
router.get('/:idOrCode', (req, res) => {
  const { idOrCode } = req.params;
  const session = getSession(idOrCode);

  if (!session) {
    return res.status(404).json({
      success: false,
      error: 'Session not found'
    });
  }

  res.json({
    success: true,
    session: {
      id: session.id,
      joinCode: session.joinCode,
      isActive: session.isActive,
      supportedLanguages: session.supportedLanguages
    }
  });
});

/**
 * GET /api/sessions/:id/stats
 * Get session statistics
 */
router.get('/:id/stats', (req, res) => {
  const { id } = req.params;
  const stats = getSessionStats(id);

  if (!stats) {
    return res.status(404).json({
      success: false,
      error: 'Session not found'
    });
  }

  res.json({
    success: true,
    stats
  });
});

/**
 * DELETE /api/sessions/:id
 * End a session
 */
router.delete('/:id', (req, res) => {
  const { id } = req.params;
  endSession(id);

  res.json({
    success: true,
    message: 'Session ended'
  });
});

module.exports = router;