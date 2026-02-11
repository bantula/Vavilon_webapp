import { useState, useEffect, useRef } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { QRCodeSVG } from 'qrcode.react'
import config from '../config'

function SpeakerPage() {
  const { sessionId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()

  const [session, setSession] = useState(location.state?.session || null)
  const [isRecording, setIsRecording] = useState(false)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState('')

  const wsRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioStreamRef = useRef(null)

  useEffect(() => {
    // Fetch session details if not passed via navigation
    if (!session) {
      fetchSessionDetails()
    }

    // Setup WebSocket connection
    setupWebSocket()

    // Fetch stats periodically
    const statsInterval = setInterval(fetchStats, 5000)

    return () => {
      cleanup()
      clearInterval(statsInterval)
    }
  }, [sessionId])

  const fetchSessionDetails = async () => {
    try {
      const response = await fetch(`${config.apiUrl}/api/sessions/${sessionId}`)
      const data = await response.json()

      if (data.success) {
        setSession(data.session)
      } else {
        setError('Session not found')
      }
    } catch (err) {
      console.error('Error fetching session:', err)
      setError('Failed to load session')
    }
  }

  const fetchStats = async () => {
    try {
      const response = await fetch(`${config.apiUrl}/api/sessions/${sessionId}/stats`)
      const data = await response.json()

      if (data.success) {
        setStats(data.stats)
      }
    } catch (err) {
      console.error('Error fetching stats:', err)
    }
  }

  const setupWebSocket = () => {
    const wsUrl = config.getWebSocketUrl()
    console.log('Connecting to WebSocket:', wsUrl)

    wsRef.current = new WebSocket(wsUrl)

    wsRef.current.onopen = () => {
      console.log('WebSocket connected')

      // Send speaker join message
      wsRef.current.send(JSON.stringify({
        type: 'speaker_join',
        payload: { sessionId }
      }))
    }

    wsRef.current.onmessage = (event) => {
      const message = JSON.parse(event.data)
      handleWebSocketMessage(message)
    }

    wsRef.current.onerror = (error) => {
      console.error('WebSocket error:', error)
      setError('Connection error')
    }

    wsRef.current.onclose = () => {
      console.log('WebSocket closed')
    }
  }

  const handleWebSocketMessage = (message) => {
    const { type, payload } = message

    switch (type) {
      case 'speaker_joined':
        console.log('Successfully joined as speaker')
        break

      case 'error':
        setError(payload.message)
        break

      default:
        console.log('Unknown message type:', type)
    }
  }

  const startRecording = async () => {
    try {
      // Request microphone access
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioStreamRef.current = stream

      // Create MediaRecorder
      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: 'audio/webm'
      })
      mediaRecorderRef.current = mediaRecorder

      // Handle audio data
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
          // Send audio chunk to backend
          const reader = new FileReader()
          reader.onloadend = () => {
            const base64Audio = reader.result.split(',')[1]
            wsRef.current.send(JSON.stringify({
              type: 'audio_chunk',
              payload: { audioData: base64Audio }
            }))
          }
          reader.readAsDataURL(event.data)
        }
      }

      // Start recording with 1 second chunks
      mediaRecorder.start(1000)
      setIsRecording(true)
      setError('')

    } catch (err) {
      console.error('Error accessing microphone:', err)
      setError('Microphone access denied')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
    }

    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach(track => track.stop())
      audioStreamRef.current = null
    }
  }

  const endSession = async () => {
    stopRecording()

    try {
      await fetch(`${config.apiUrl}/api/sessions/${sessionId}`, {
        method: 'DELETE'
      })

      navigate('/')
    } catch (err) {
      console.error('Error ending session:', err)
    }
  }

  const cleanup = () => {
    stopRecording()

    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({
        type: 'speaker_disconnect'
      }))
      wsRef.current.close()
    }
  }

  if (!session) {
    return (
      <div className="container">
        <div className="card">
          <div className="loading">Loading session...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="container">
      <div className="card">
        <div className="header">
          <h1>Speaker View</h1>
          <p>Share this code with your audience</p>
        </div>

        {error && (
          <div className="error-message">{error}</div>
        )}

        <div className="session-info">
          <div style={{ textAlign: 'center' }}>
            <p style={{ fontSize: '0.9rem', color: '#666', marginBottom: '8px' }}>
              Session Code
            </p>
            <div className="session-code">{session.joinCode}</div>
          </div>

          <div className="qr-code">
            <QRCodeSVG
              value={session.joinUrl || `${window.location.origin}/join?code=${session.joinCode}`}
              size={200}
              level="M"
            />
          </div>

          <p style={{ textAlign: 'center', fontSize: '0.9rem', color: '#666' }}>
            Scan to join
          </p>
        </div>

        {stats && (
          <div className="status status-success">
            {stats.totalListeners} listener{stats.totalListeners !== 1 ? 's' : ''} connected
            {stats.languageBreakdown && Object.keys(stats.languageBreakdown).length > 0 && (
              <div className="listener-count">
                {Object.entries(stats.languageBreakdown).map(([lang, count]) => (
                  <span key={lang} style={{ marginRight: '16px' }}>
                    {lang.toUpperCase()}: {count}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="audio-controls">
          {!isRecording ? (
            <button
              className="button button-primary"
              onClick={startRecording}
              style={{ maxWidth: '300px' }}
            >
              Start Speaking
            </button>
          ) : (
            <button
              className="button button-danger"
              onClick={stopRecording}
              style={{ maxWidth: '300px' }}
            >
              Stop Speaking
            </button>
          )}
        </div>

        {isRecording && (
          <div className="status status-warning">
            Recording... Your speech is being translated in real-time
          </div>
        )}

        <button
          className="button button-secondary"
          onClick={endSession}
          style={{ marginTop: '20px' }}
        >
          End Session
        </button>
      </div>
    </div>
  )
}

export default SpeakerPage