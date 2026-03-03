const express = require('express');
const router = express.Router();
const { checkAccess, getAgencySubscription } = require('../services/authService');

/**
 * POST /api/auth/login
 *
 * Body: { username: string }
 *
 * Outcomes:
 *  A. { success: true, access: true,  guide: { firstName, lastName, username } }
 *  B. { success: true, access: false, guide: { firstName, lastName, phone }, scheduledWindows: [...] }
 *  C. { success: false, error: 'not_found' }
 *  D. { success: true, access: false, guide: {...}, scheduledWindows: [], reason: 'subscription_required' }
 */
router.post('/login', async (req, res) => {
  const { username } = req.body || {};

  if (!username || typeof username !== 'string' || !username.trim()) {
    return res.status(400).json({ success: false, error: 'username_required' });
  }

  const normalized = username.trim().toLowerCase();

  try {
    const result = await checkAccess(normalized);

    if (!result.found) {
      return res.json({ success: false, error: 'not_found' });
    }

    if (!result.hasAccessToday) {
      return res.json({
        success: true,
        access: false,
        guide: {
          firstName: result.guide.firstName,
          lastName: result.guide.lastName,
          phone: result.guide.phone
        },
        scheduledWindows: result.scheduledWindows
      });
    }

    // Check agency subscription — guides must belong to an agency with an
    // active or trialing subscription. The agencyName field is set when a
    // guide is imported via CSV after their agency has subscribed.
    const agencyName = result.guide.agencyName || null;
    const agency = agencyName ? await getAgencySubscription(agencyName) : null;
    const subStatus = agency?.status || null;

    if (subStatus !== 'trialing' && subStatus !== 'active') {
      return res.json({
        success: true,
        access: false,
        guide: {
          firstName: result.guide.firstName,
          lastName: result.guide.lastName,
          phone: result.guide.phone || null,
        },
        scheduledWindows: [],
        reason: 'subscription_required',
      });
    }

    return res.json({
      success: true,
      access: true,
      guide: {
        firstName: result.guide.firstName,
        lastName: result.guide.lastName,
        username: result.guide.username
      }
    });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ success: false, error: 'server_error' });
  }
});

module.exports = router;
