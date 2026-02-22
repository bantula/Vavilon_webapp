const express = require('express');
const router = express.Router();
const { upsertGuide, listGuides } = require('../services/authService');

/**
 * Simple secret-key middleware.
 * Set ADMIN_SECRET env var on the server. Pass it as X-Admin-Key header.
 */
function requireAdminKey(req, res, next) {
  const secret = process.env.ADMIN_SECRET;
  if (!secret) {
    // If no secret configured, reject all requests for safety
    return res.status(503).json({ success: false, error: 'admin_not_configured' });
  }
  if (req.headers['x-admin-key'] !== secret) {
    return res.status(401).json({ success: false, error: 'unauthorized' });
  }
  next();
}

router.use(requireAdminKey);

/**
 * POST /api/admin/guides
 *
 * Add or update a tour guide.
 *
 * Body: {
 *   firstName: string,
 *   lastName: string,
 *   username: string,       // "name.surname.1234"
 *   email: string,
 *   phone: string,
 *   accessWindows: [{ startDate: "YYYY-MM-DD", endDate: "YYYY-MM-DD" }]
 * }
 */
router.post('/guides', async (req, res) => {
  const { firstName, lastName, username, email, phone, accessWindows } = req.body || {};

  if (!firstName || !lastName || !username || !email || !phone) {
    return res.status(400).json({
      success: false,
      error: 'missing_fields',
      required: ['firstName', 'lastName', 'username', 'email', 'phone']
    });
  }

  if (!Array.isArray(accessWindows) || accessWindows.length === 0) {
    return res.status(400).json({
      success: false,
      error: 'access_windows_required',
      message: 'Provide at least one { startDate, endDate } window'
    });
  }

  // Validate date formats
  for (const w of accessWindows) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(w.startDate) || !/^\d{4}-\d{2}-\d{2}$/.test(w.endDate)) {
      return res.status(400).json({
        success: false,
        error: 'invalid_date_format',
        message: 'Dates must be YYYY-MM-DD'
      });
    }
    if (w.startDate > w.endDate) {
      return res.status(400).json({
        success: false,
        error: 'invalid_date_range',
        message: 'startDate must be <= endDate'
      });
    }
  }

  try {
    const { guide, isNew } = await upsertGuide({
      firstName,
      lastName,
      username: username.toLowerCase(),
      email,
      phone,
      accessWindows
    });

    res.json({ success: true, guide, isNew });
  } catch (err) {
    console.error('Admin upsert guide error:', err);
    res.status(500).json({ success: false, error: 'server_error' });
  }
});

/**
 * GET /api/admin/guides
 * List all guides.
 */
router.get('/guides', async (req, res) => {
  try {
    const guides = await listGuides();
    res.json({ success: true, guides, count: guides.length });
  } catch (err) {
    console.error('Admin list guides error:', err);
    res.status(500).json({ success: false, error: 'server_error' });
  }
});

module.exports = router;
