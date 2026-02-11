import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import config from '../config'

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
  const [subtitleHistory, setSubtitleHistory] = useState([])
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  const wsRef = useRef(null)
  const audioContextRef = useRef(null)
  // Queue audio playback so chunks don't overlap
  const audioQueueRef = useRef([])
  const isPlayingRef = useRef(false)

  useEffect(() => {
    return () => cleanup()
  }, [])

  /**
   * Initialize AudioContext on user gesture (required by browsers).
   * Called when joining a session.
   */
  const initAudioContext = () => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()
    }
    // Resume if suspended (browser autoplay policy)
    if (audioContextRef.current.state === 'suspended') {
      audioContextRef.current.resume()
    }
  }

  const handleJoin = async () => {
    if (!joinCode.trim()) {
      setError('Please enter a session code')
      return
    }

    setError('')
    setStatus('Connecting...')

    try {
      const response = await fetch(`${config.apiUrl}/api/sessions/${joinCode}`)
      const data = await response.json()

      if (!data.success) {
        setError('Session not found')
        setStatus('')
        return
      }

      // Init audio context on this user gesture (click)
      initAudioContext()

      setupWebSocket(data.session.id)
    } catch (err) {
      console.error('Error joining session:', err)
      setError('Failed to join session')
      setStatus('')
    }
  }

  const setupWebSocket = (sessionId) => {
    const wsUrl = config.getWebSocketUrl()
    wsRef.current = new WebSocket(wsUrl)

    wsRef.current.onopen = () => {
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

    wsRef.current.onerror = () => {
      setError('Connection error')
      setStatus('')
    }

    wsRef.current.onclose = () => {
      if (isJoined) setStatus('Disconnected from session')
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
        queueAudio(payload.audioData)
        break

      case 'subtitle':
        setCurrentSubtitle(payload.text)
        setSubtitleHistory(prev => [...prev.slice(-4), payload.text])
        break

      case 'speaker_disconnected':
        setStatus('Speaker has disconnected')
        break

      case 'error':
        setError(payload.message)
        setStatus('')
        break
    }
  }

  /**
   * Queue audio for sequential playback.
   * The AI service sends WAV audio (RIFF header + PCM data) as base64.
   * AudioContext.decodeAudioData handles WAV natively.
   */
  const queueAudio = (audioDataBase64) => {
    audioQueueRef.current.push(audioDataBase64)
    if (!isPlayingRef.current) {
      playNextInQueue()
    }
  }

  const playNextInQueue = async () => {
    if (audioQueueRef.current.length === 0) {
      isPlayingRef.current = false
      return
    }

    isPlayingRef.current = true
    const audioDataBase64 = audioQueueRef.current.shift()

    try {
      const ctx = audioContextRef.current
      if (!ctx) return

      // Resume if suspended
      if (ctx.state === 'suspended') await ctx.resume()

      // Decode base64 to ArrayBuffer
      const binaryString = atob(audioDataBase64)
      const bytes = new Uint8Array(binaryString.length)
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i)
      }

      // decodeAudioData handles WAV (RIFF) format natively
      const audioBuffer = await ctx.decodeAudioData(bytes.buffer.slice(0))

      const source = ctx.createBufferSource()
      source.buffer = audioBuffer
      source.connect(ctx.destination)
      source.onended = () => playNextInQueue()
      source.start(0)
    } catch (err) {
      console.error('Error playing audio:', err)
      // Skip this chunk and try the next one
      playNextInQueue()
    }
  }

  const handleLeave = () => {
    cleanup()
    setIsJoined(false)
    setStatus('')
    setCurrentSubtitle('')
    setSubtitleHistory([])
  }

  const cleanup = () => {
    audioQueueRef.current = []
    isPlayingRef.current = false
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
            <div className="status status-success">{status}</div>
          )}

          {error && (
            <div className="error-message">{error}</div>
          )}

          <div className="subtitle-box">
            {subtitleHistory.length > 1 && (
              <div style={{ opacity: 0.5, fontSize: '0.9rem', marginBottom: '8px' }}>
                {subtitleHistory.slice(0, -1).map((text, i) => (
                  <div key={i} className="subtitle-text">{text}</div>
                ))}
              </div>
            )}
            <div className="subtitle-text" style={{ fontSize: '1.3rem' }}>
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

        {error && <div className="error-message">{error}</div>}
        {status && <div className="status status-warning">{status}</div>}

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
