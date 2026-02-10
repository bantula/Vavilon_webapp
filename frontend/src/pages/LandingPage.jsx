import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

function LandingPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleStartTour = async () => {
    setLoading(true)
    setError('')

    try {
      const response = await fetch('/api/sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        }
      })

      const data = await response.json()

      if (data.success) {
        // Navigate to speaker page with session ID
        navigate(`/speaker/${data.session.sessionId}`, {
          state: { session: data.session }
        })
      } else {
        setError('Failed to create session')
      }
    } catch (err) {
      console.error('Error creating session:', err)
      setError('Failed to connect to server')
    } finally {
      setLoading(false)
    }
  }

  const handleJoinTour = () => {
    navigate('/join')
  }

  return (
    <div className="container">
      <div className="card">
        <div className="header">
          <h1>Vavilon</h1>
          <p>Real-Time Translation for Tours & Events</p>
        </div>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <div style={{ marginTop: '40px' }}>
          <button
            className="button button-primary"
            onClick={handleStartTour}
            disabled={loading}
          >
            {loading ? 'Creating Session...' : 'Start a Tour'}
          </button>

          <button
            className="button button-secondary"
            onClick={handleJoinTour}
            disabled={loading}
          >
            Join a Tour
          </button>
        </div>

        <div style={{ marginTop: '40px', textAlign: 'center', color: '#888', fontSize: '0.9rem' }}>
          <p>Speak once, reach everyone in their language</p>
          <p style={{ marginTop: '8px' }}>Audio + Live Subtitles</p>
        </div>
      </div>
    </div>
  )
}

export default LandingPage