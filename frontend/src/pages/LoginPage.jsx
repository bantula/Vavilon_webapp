import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import config from '../config'

/**
 * Format an access window for display.
 * e.g. { startDate: "2026-10-06", endDate: "2026-10-08" }
 *   → "October 6–8, 2026"  (same month)
 *   → "October 30 – November 2, 2026"  (different months, same year)
 */
function formatWindow(w) {
  const start = new Date(w.startDate + 'T12:00:00') // noon to avoid DST edge
  const end = new Date(w.endDate + 'T12:00:00')
  const opts = { month: 'long', day: 'numeric' }

  if (start.getFullYear() === end.getFullYear()) {
    const year = start.getFullYear()
    if (start.getMonth() === end.getMonth()) {
      return `${start.toLocaleString('en-US', { month: 'long' })} ${start.getDate()}–${end.getDate()}, ${year}`
    }
    return `${start.toLocaleString('en-US', opts)} – ${end.toLocaleString('en-US', opts)}, ${year}`
  }
  return `${start.toLocaleString('en-US', { ...opts, year: 'numeric' })} – ${end.toLocaleString('en-US', { ...opts, year: 'numeric' })}`
}

// ── Sub-views ──────────────────────────────────────────────────────────────

function NoAccessView({ guide, scheduledWindows, onBack }) {
  const { firstName, lastName, phone } = guide
  const fullName = `${firstName} ${lastName}`

  const emailSubject = encodeURIComponent('Someone wants to use webapp without access')
  const emailBody = encodeURIComponent(`Username: ${guide.username || fullName}\nPhone: ${phone}`)
  const mailtoHref = `mailto:andrejbantulic@gmail.com?subject=${emailSubject}&body=${emailBody}`

  const futureDates = scheduledWindows.length > 0

  return (
    <div className="login-outcome">
      <div className="login-outcome-icon login-outcome-icon--warn">
        <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
          <path d="M16 3L2 28h28L16 3z" stroke="#d97706" strokeWidth="2" strokeLinejoin="round" fill="#fef3c7"/>
          <line x1="16" y1="13" x2="16" y2="20" stroke="#d97706" strokeWidth="2.5" strokeLinecap="round"/>
          <circle cx="16" cy="24" r="1.25" fill="#d97706"/>
        </svg>
      </div>

      <h2 className="login-outcome-title">Access not available today</h2>

      <p className="login-outcome-body">
        Your username is valid, but your agency has not told us that you were supposed to be on tour,
        or there was an error in our system.
      </p>
      <p className="login-outcome-body">
        The app owner has been notified and should resolve this issue immediately.
        Thank you for your patience.
      </p>

      {futureDates && (
        <div className="login-scheduled-dates">
          <p className="login-scheduled-label">Your scheduled tour dates:</p>
          <ul className="login-scheduled-list">
            {scheduledWindows.map((w, i) => (
              <li key={i}>{formatWindow(w)}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="login-outcome-actions">
        <a className="button button-primary" href={mailtoHref}>
          Contact app owner
        </a>
        <button className="button button-secondary" onClick={onBack}>
          Try again
        </button>
      </div>
    </div>
  )
}

function NotFoundView({ onBack }) {
  return (
    <div className="login-outcome">
      <div className="login-outcome-icon login-outcome-icon--error">
        <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
          <circle cx="16" cy="16" r="13" stroke="#dc2626" strokeWidth="2" fill="#fee2e2"/>
          <line x1="11" y1="11" x2="21" y2="21" stroke="#dc2626" strokeWidth="2.5" strokeLinecap="round"/>
          <line x1="21" y1="11" x2="11" y2="21" stroke="#dc2626" strokeWidth="2.5" strokeLinecap="round"/>
        </svg>
      </div>

      <h2 className="login-outcome-title">Username not found</h2>

      <p className="login-outcome-body">
        Your agency can create an account and we will email the username to you.
        If you believe you should have an account, ask your agency to contact us.
      </p>

      <div className="login-outcome-actions">
        <button className="button button-primary" onClick={onBack}>
          Try again
        </button>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────

function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Outcome states
  const [outcome, setOutcome] = useState(null) // null | 'no_access' | 'not_found'
  const [outcomeData, setOutcomeData] = useState(null)

  const handleBack = () => {
    setOutcome(null)
    setOutcomeData(null)
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimmed = username.trim()
    if (!trimmed) return

    setLoading(true)
    setError('')

    try {
      // Step 1: Check credentials
      const authRes = await fetch(`${config.apiUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: trimmed })
      })
      const authData = await authRes.json()

      // Outcome C: username not found
      if (!authData.success) {
        setOutcome('not_found')
        setLoading(false)
        return
      }

      // Outcome B: username valid but no access today
      if (!authData.access) {
        setOutcomeData({ guide: authData.guide, scheduledWindows: authData.scheduledWindows })
        setOutcome('no_access')
        setLoading(false)
        return
      }

      // Outcome A: valid + has access — create session and navigate
      const sessionRes = await fetch(`${config.apiUrl}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
      const sessionData = await sessionRes.json()

      if (!sessionData.success) {
        setError('Failed to create session. Please try again.')
        setLoading(false)
        return
      }

      navigate(`/speaker/${sessionData.session.sessionId}`, {
        state: {
          session: sessionData.session,
          guide: authData.guide
        }
      })
    } catch (err) {
      console.error('Login error:', err)
      setError('Failed to connect to server. Please check your connection.')
      setLoading(false)
    }
  }

  // Show outcome views
  if (outcome === 'not_found') return (
    <div className="container">
      <div className="card">
        <div className="header"><h1>Vavilon</h1><p>Guide Login</p></div>
        <NotFoundView onBack={handleBack} />
      </div>
    </div>
  )

  if (outcome === 'no_access') return (
    <div className="container">
      <div className="card">
        <div className="header"><h1>Vavilon</h1><p>Guide Login</p></div>
        <NoAccessView
          guide={outcomeData.guide}
          scheduledWindows={outcomeData.scheduledWindows}
          onBack={handleBack}
        />
      </div>
    </div>
  )

  // Default: login form
  return (
    <div className="container">
      <div className="card">
        <div className="header">
          <h1>Vavilon</h1>
          <p>Guide Login</p>
        </div>

        {error && <div className="error-message">{error}</div>}

        <form onSubmit={handleSubmit} style={{ marginTop: '30px' }}>
          <div style={{ marginBottom: '20px' }}>
            <label className="login-label" htmlFor="username">
              Username
            </label>
            <input
              id="username"
              type="text"
              className="input"
              placeholder="e.g. john.doe.1234"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoCapitalize="none"
              autoCorrect="off"
              autoComplete="username"
              spellCheck={false}
              disabled={loading}
            />
          </div>

          <button
            type="submit"
            className="button button-primary"
            disabled={loading || !username.trim()}
          >
            {loading ? 'Checking...' : 'Log in'}
          </button>
        </form>

        <p className="login-hint">
          Usernames are issued by your agency and governed by scheduled tour dates.
        </p>
      </div>
    </div>
  )
}

export default LoginPage
