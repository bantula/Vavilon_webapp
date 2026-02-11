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
router.get('/:idOrCode', async (req, res) => {
  try {
    const { idOrCode } = req.params;
    const session = await getSession(idOrCode);

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
  } catch (error) {
    console.error('Error getting session:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get session'
    });
  }
});

/**
 * GET /api/sessions/:id/stats
 * Get session statistics
 */
router.get('/:id/stats', async (req, res) => {
  try {
    const { id } = req.params;
    const stats = await getSessionStats(id);

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
  } catch (error) {
    console.error('Error getting session stats:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to get session stats'
    });
  }
});

/**
 * DELETE /api/sessions/:id
 * End a session
 */
router.delete('/:id', async (req, res) => {
  try {
    const { id } = req.params;
    await endSession(id);

    res.json({
      success: true,
      message: 'Session ended'
    });
  } catch (error) {
    console.error('Error ending session:', error);
    res.status(500).json({
      success: false,
      error: 'Failed to end session'
    });
  }
});

module.exports = router;