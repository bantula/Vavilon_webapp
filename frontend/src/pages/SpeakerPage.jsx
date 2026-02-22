import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { QRCodeSVG } from 'qrcode.react'
import config from '../config'

/**
 * Downsample Float32 audio from source sample rate to 16kHz
 * and convert to Int16 PCM (what Azure Speech SDK expects).
 */
function downsampleToInt16(float32Array, sourceSampleRate) {
  const targetRate = 16000
  const ratio = sourceSampleRate / targetRate
  const newLength = Math.floor(float32Array.length / ratio)
  const int16Array = new Int16Array(newLength)

  for (let i = 0; i < newLength; i++) {
    const srcIndex = Math.floor(i * ratio)
    // Clamp to [-1, 1] then scale to Int16 range
    const sample = Math.max(-1, Math.min(1, float32Array[srcIndex]))
    int16Array[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF
  }

  return int16Array
}

function SpeakerPage() {
  const { sessionId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()

  const [session, setSession] = useState(location.state?.session || null)
  const guide = location.state?.guide || null

  const [isRecording, setIsRecording] = useState(false)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState('')
  const [sourceLanguage, setSourceLanguage] = useState('en-US')

  const wsRef = useRef(null)
  const audioContextRef = useRef(null)
  const processorRef = useRef(null)
  const sourceNodeRef = useRef(null)
  const mediaStreamRef = useRef(null)

  const SOURCE_LANGUAGES = [
    { code: 'en-US', name: 'English' },
    { code: 'es-ES', name: 'Spanish' },
    { code: 'fr-FR', name: 'French' },
    { code: 'de-DE', name: 'German' },
    { code: 'it-IT', name: 'Italian' },
    { code: 'pt-PT', name: 'Portuguese' },
    { code: 'ru-RU', name: 'Russian' },
    { code: 'zh-CN', name: 'Chinese' },
    { code: 'ja-JP', name: 'Japanese' },
    { code: 'ar-SA', name: 'Arabic' }
  ]

  // All target languages except the source
  const getTargetLanguages = useCallback(() => {
    const sourceShort = sourceLanguage.split('-')[0]
    return ['en','es','fr','de','it','pt','ru','zh','ja','ar']
      .filter(l => l !== sourceShort)
  }, [sourceLanguage])

  useEffect(() => {
    if (!session) fetchSessionDetails()
    setupWebSocket()
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
      if (data.success) setSession(data.session)
      else setError('Session not found')
    } catch (err) {
      console.error('Error fetching session:', err)
      setError('Failed to load session')
    }
  }

  const fetchStats = async () => {
    try {
      const response = await fetch(`${config.apiUrl}/api/sessions/${sessionId}/stats`)
      const data = await response.json()
      if (data.success) setStats(data.stats)
    } catch (err) {
      // ignore
    }
  }

  const setupWebSocket = () => {
    const wsUrl = config.getWebSocketUrl()
    wsRef.current = new WebSocket(wsUrl)

    wsRef.current.onopen = () => {
      wsRef.current.send(JSON.stringify({
        type: 'speaker_join',
        payload: { sessionId }
      }))
    }

    wsRef.current.onmessage = (event) => {
      const message = JSON.parse(event.data)
      if (message.type === 'error') setError(message.payload.message)
    }

    wsRef.current.onerror = () => setError('Connection error')
    wsRef.current.onclose = () => console.log('WebSocket closed')
  }

  /**
   * Start capturing raw PCM audio from the microphone.
   * Uses AudioContext + ScriptProcessorNode to get Float32 samples,
   * then downsamples to 16kHz Int16 (what Azure Speech SDK expects).
   */
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true
        }
      })
      mediaStreamRef.current = stream

      // Create AudioContext (browser may give us 44100 or 48000Hz)
      const audioContext = new (window.AudioContext || window.webkitAudioContext)()
      audioContextRef.current = audioContext

      const source = audioContext.createMediaStreamSource(stream)
      sourceNodeRef.current = source

      // ScriptProcessorNode captures raw PCM samples
      // Buffer size 4096 at 48kHz ≈ 85ms chunks
      const processor = audioContext.createScriptProcessor(4096, 1, 1)
      processorRef.current = processor

      processor.onaudioprocess = (e) => {
        if (wsRef.current?.readyState !== WebSocket.OPEN) return

        const float32Data = e.inputBuffer.getChannelData(0)

        // Downsample to 16kHz Int16 PCM (Azure Speech SDK format)
        const int16Data = downsampleToInt16(float32Data, audioContext.sampleRate)

        // Send as base64 to match the existing JSON protocol
        const bytes = new Uint8Array(int16Data.buffer)
        let binary = ''
        for (let i = 0; i < bytes.length; i++) {
          binary += String.fromCharCode(bytes[i])
        }
        const base64Audio = btoa(binary)

        wsRef.current.send(JSON.stringify({
          type: 'audio_chunk',
          payload: { audioData: base64Audio }
        }))
      }

      source.connect(processor)
      processor.connect(audioContext.destination)

      // Tell backend to start the AI translation session
      wsRef.current.send(JSON.stringify({
        type: 'start_speaking',
        payload: {
          sourceLanguage,
          targetLanguages: getTargetLanguages()
        }
      }))

      setIsRecording(true)
      setError('')
    } catch (err) {
      console.error('Error accessing microphone:', err)
      setError('Microphone access denied')
    }
  }

  const stopRecording = () => {
    // Disconnect audio processing
    if (processorRef.current) {
      processorRef.current.disconnect()
      processorRef.current = null
    }
    if (sourceNodeRef.current) {
      sourceNodeRef.current.disconnect()
      sourceNodeRef.current = null
    }
    if (audioContextRef.current) {
      audioContextRef.current.close()
      audioContextRef.current = null
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop())
      mediaStreamRef.current = null
    }

    // Tell backend to stop the AI session
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'stop_speaking' }))
    }

    setIsRecording(false)
  }

  const endSession = async () => {
    stopRecording()
    try {
      await fetch(`${config.apiUrl}/api/sessions/${sessionId}`, { method: 'DELETE' })
    } catch (err) {
      console.error('Error ending session:', err)
    }
    navigate('/')
  }

  const handleLogOut = () => {
    cleanup()
    navigate('/')
  }

  const cleanup = () => {
    stopRecording()
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'speaker_disconnect' }))
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
        {/* Guide welcome banner */}
        {guide && (
          <div className="speaker-guide-bar">
            <span className="speaker-guide-welcome">
              Welcome, {guide.firstName} {guide.lastName}!
            </span>
            <button className="speaker-logout-btn" onClick={handleLogOut}>
              Log out
            </button>
          </div>
        )}

        <div className="header">
          <h1>Speaker View</h1>
          <p>Share this code with your audience</p>
        </div>

        {error && <div className="error-message">{error}</div>}

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

        {!isRecording && (
          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontWeight: '500' }}>
              I am speaking in:
            </label>
            <select
              className="select"
              value={sourceLanguage}
              onChange={(e) => setSourceLanguage(e.target.value)}
            >
              {SOURCE_LANGUAGES.map(lang => (
                <option key={lang.code} value={lang.code}>{lang.name}</option>
              ))}
            </select>
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
