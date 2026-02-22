import { useNavigate } from 'react-router-dom'

function LandingPage() {
  const navigate = useNavigate()

  return (
    <div className="container">
      <div className="card">
        <div className="header">
          <h1>Vavilon</h1>
          <p>Real-Time Translation for Tours & Events</p>
        </div>

        <div style={{ marginTop: '40px' }}>
          <button
            className="button button-primary"
            onClick={() => navigate('/login')}
          >
            Start a Tour
          </button>

          <button
            className="button button-secondary"
            onClick={() => navigate('/join')}
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
