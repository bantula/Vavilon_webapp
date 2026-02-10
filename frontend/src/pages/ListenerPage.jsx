import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'

const LANGUAGES = [
  { code: 'en', name: 'English' },
  { code: 'es', name: 'Spanish' },
  { code: 'fr', name: 'French' },
  { code: 'de', name: 'German' },
  { code: 'it', name: 'Italian' },
  { code: 'pt', name: 'Portuguese' },
  { code: 'ru', name: 'Russian' },
  { code: 'zh', name: 'Chinese' },
  { code: 'ja', name: 'Japanese' },
  { code: 'ar', name: 'Arabic' }
]

function ListenerPage() {
  const [searchParams] = useSearchParams()
  const codeFromUrl = searchParams.get('code')

  const [joinCode, setJoinCode] = useState(codeFromUrl || '')
  const [selectedLanguage, setSelectedLanguage] = useState('en')
  const [isJoined, setIsJoined] = useState(false)
  const [currentSubtitle, setCurrentSubtitle] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  const wsRef = useRef(null)
  const audioContextRef = useRef(null)
  const audioQueueRef = useRef([])

  useEffect(() => {
    // Initialize audio context
    audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()

    return () => {
      cleanup()
    }
  }, [])

  const handleJoin = async () => {
    if (!joinCode.trim()) {
      setError('Please enter a session code')
      return
    }

    setError('')
    setStatus('Connecting...')

    // Verify session exists
    try {
      const response = await fetch(`/api/sessions/${joinCode}`)
      const data = await response.json()

      if (!data.success) {
        setError('Session not found')
        setStatus('')
        return
      }

      // Setup WebSocket and join session
      setupWebSocket(data.session.id)

    } catch (err) {
      console.error('Error joining session:', err)
      setError('Failed to join session')
      setStatus('')
    }
  }

  const setupWebSocket = (sessionId) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws`

    wsRef.current = new WebSocket(wsUrl)

    wsRef.current.onopen = () => {
      console.log('WebSocket connected')

      // Send listener join message
      wsRef.current.send(JSON.stringify({
        type: 'listener_join',
        payload: {
          sessionId,
          joinCode,
          language: selectedLanguage
        }
      }))
    }

    wsRef.current.onmessage = (event) => {
      const message = JSON.parse(event.data)
      handleWebSocketMessage(message)
    }

    wsRef.current.onerror = (error) => {
      console.error('WebSocket error:', error)
      setError('Connection error')
      setStatus('')
    }

    wsRef.current.onclose = () => {
      console.log('WebSocket closed')
      if (isJoined) {
        setStatus('Disconnected from session')
      }
    }
  }

  const handleWebSocketMessage = (message) => {
    const { type, payload } = message

    switch (type) {
      case 'listener_joined':
        setIsJoined(true)
        setStatus('Connected - Listening for translations...')
        setError('')
        break

      case 'audio':
        playAudio(payload.audioData)
        break

      case 'subtitle':
        setCurrentSubtitle(payload.text)
        // Clear subtitle after 5 seconds
        setTimeout(() => {
          setCurrentSubtitle(prev => prev === payload.text ? '' : prev)
        }, 5000)
        break

      case 'speaker_disconnected':
        setStatus('Speaker has disconnected')
        break

      case 'error':
        setError(payload.message)
        setStatus('')
        break

      default:
        console.log('Unknown message type:', type)
    }
  }

  const playAudio = async (audioDataBase64) => {
    try {
      // Decode base64 audio
      const binaryString = atob(audioDataBase64)
      const bytes = new Uint8Array(binaryString.length)
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i)
      }

      // Decode audio data
      const audioBuffer = await audioContextRef.current.decodeAudioData(bytes.buffer)

      // Create audio source
      const source = audioContextRef.current.createBufferSource()
      source.buffer = audioBuffer
      source.connect(audioContextRef.current.destination)
      source.start(0)

    } catch (err) {
      console.error('Error playing audio:', err)
    }
  }

  const handleLeave = () => {
    cleanup()
    setIsJoined(false)
    setStatus('')
    setCurrentSubtitle('')
  }

  const cleanup = () => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }

  if (isJoined) {
    return (
      <div className="container">
        <div className="card">
          <div className="header">
            <h1>Listening</h1>
            <p>Language: {LANGUAGES.find(l => l.code === selectedLanguage)?.name}</p>
          </div>

          {status && (
            <div className="status status-success">
              {status}
            </div>
          )}

          {error && (
            <div className="error-message">{error}</div>
          )}

          <div className="subtitle-box">
            <div className="subtitle-text">
              {currentSubtitle || 'Waiting for translation...'}
            </div>
          </div>

          <button
            className="button button-secondary"
            onClick={handleLeave}
            style={{ marginTop: '20px' }}
          >
            Leave Session
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="container">
      <div className="card">
        <div className="header">
          <h1>Join a Tour</h1>
          <p>Enter the session code to start listening</p>
        </div>

        {error && (
          <div className="error-message">{error}</div>
        )}

        {status && (
          <div className="status status-warning">{status}</div>
        )}

        <div style={{ marginTop: '30px' }}>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: '500' }}>
            Session Code
          </label>
          <input
            type="text"
            className="input"
            placeholder="Enter 6-character code"
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
            maxLength={6}
            style={{ textTransform: 'uppercase', letterSpacing: '2px', textAlign: 'center' }}
          />

          <label style={{ display: 'block', marginBottom: '8px', fontWeight: '500' }}>
            Select Language
          </label>
          <select
            className="select"
            value={selectedLanguage}
            onChange={(e) => setSelectedLanguage(e.target.value)}
          >
            {LANGUAGES.map(lang => (
              <option key={lang.code} value={lang.code}>
                {lang.name}
              </option>
            ))}
          </select>

          <button
            className="button button-primary"
            onClick={handleJoin}
            disabled={!joinCode.trim()}
            style={{ marginTop: '10px' }}
          >
            Join Session
          </button>
        </div>
      </div>
    </div>
  )
}

export default ListenerPage