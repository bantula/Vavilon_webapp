/**
 * Service-to-service authentication between the backend and the AI service.
 *
 * A shared secret (SERVICE_SECRET) is sent as the `X-Service-Key` header on
 * every backend→AI call, and required on the AI→backend callback (/api/events).
 * This keeps the internet from abusing the (paid) Azure Speech endpoints or
 * injecting fake subtitles/audio into live sessions.
 *
 * Rollout is fail-open so it can be enabled without downtime:
 *   - Outbound: the header is only attached when SERVICE_SECRET is set.
 *   - Inbound:  enforcement only kicks in once ENFORCE_SERVICE_AUTH=true AND
 *               SERVICE_SECRET is set — so the secret can be provisioned on both
 *               sides first, then enforcement flipped on last.
 */
const SERVICE_SECRET = process.env.SERVICE_SECRET;
const ENFORCE = process.env.ENFORCE_SERVICE_AUTH === 'true';

/** Build headers for an outbound call to the AI service. */
function serviceHeaders(extra = {}) {
  return SERVICE_SECRET ? { ...extra, 'X-Service-Key': SERVICE_SECRET } : { ...extra };
}

/** Express middleware guarding endpoints the AI service calls back into. */
function requireServiceKey(req, res, next) {
  if (!ENFORCE || !SERVICE_SECRET) return next(); // enforcement disabled until configured
  if (req.headers['x-service-key'] !== SERVICE_SECRET) {
    return res.status(401).json({ success: false, error: 'unauthorized' });
  }
  next();
}

module.exports = { serviceHeaders, requireServiceKey };
