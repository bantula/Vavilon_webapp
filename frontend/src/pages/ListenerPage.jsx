import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import config from '../config'

const LANGUAGES = [
  { code: 'en', name: '🇬🇧 English' },
  { code: 'es', name: '🇪🇸 Spanish' },
  { code: 'fr', name: '🇫🇷 French' },
  { code: 'de', name: '🇩🇪 German' },
  { code: 'it', name: '🇮🇹 Italian' },
  { code: 'pt', name: '🇵🇹 Portuguese' },
  { code: 'ru', name: '🇷🇺 Russian' },
  { code: 'zh', name: '🇨🇳 Chinese' },
  { code: 'ja', name: '🇯🇵 Japanese' },
  { code: 'ar', name: '🇸🇦 Arabic' },
  { code: 'sr', name: '🇷🇸 Serbian' },
  { code: 'mk', name: '🇲🇰 Macedonian' },
  { code: 'bg', name: '🇧🇬 Bulgarian' },
  { code: 'hu', name: '🇭🇺 Hungarian' },
  { code: 'ro', name: '🇷🇴 Romanian' },
  { code: 'hr', name: '🇭🇷 Croatian' },
  { code: 'sl', name: '🇸🇮 Slovenian' },
  { code: 'sk', name: '🇸🇰 Slovak' },
  { code: 'pl', name: '🇵🇱 Polish' },
  { code: 'uk', name: '🇺🇦 Ukrainian' },
]

/* ── Inline SVG avatars ─────────────────────────────────────────── */

function GuideAvatar() {
  return (
    <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="18" cy="18" r="18" fill="#667eea" />
      {/* Hat brim */}
      <ellipse cx="18" cy="12" rx="11" ry="3" fill="#4f5dd4" />
      {/* Hat top */}
      <rect x="10" y="5" width="16" height="8" rx="3" fill="#5568d3" />
      {/* Face */}
      <circle cx="18" cy="21" r="7" fill="#fde68a" />
      {/* Eyes */}
      <circle cx="15.5" cy="20" r="1" fill="#333" />
      <circle cx="20.5" cy="20" r="1" fill="#333" />
      {/* Smile */}
      <path d="M15 23.5 Q18 26 21 23.5" stroke="#333" strokeWidth="1" strokeLinecap="round" fill="none" />
      {/* Flag stick */}
      <line x1="28" y1="8" x2="28" y2="18" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" />
      {/* Flag */}
      <path d="M28 8 L33 10.5 L28 13 Z" fill="#ef4444" />
    </svg>
  )
}

function TranslatorAvatar() {
  return (
    <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="18" cy="18" r="18" fill="#10b981" />
      {/* Robot head */}
      <rect x="10" y="10" width="16" height="14" rx="4" fill="#e0f2fe" />
      {/* Antenna */}
      <line x1="18" y1="6" x2="18" y2="10" stroke="#e0f2fe" strokeWidth="2" strokeLinecap="round" />
      <circle cx="18" cy="5" r="2" fill="#fbbf24" />
      {/* Eyes */}
      <circle cx="14.5" cy="16" r="2" fill="#3b82f6" />
      <circle cx="21.5" cy="16" r="2" fill="#3b82f6" />
      {/* Eye glints */}
      <circle cx="15.2" cy="15.3" r="0.6" fill="white" />
      <circle cx="22.2" cy="15.3" r="0.6" fill="white" />
      {/* Mouth */}
      <rect x="13" y="20" width="10" height="2" rx="1" fill="#94a3b8" />
      {/* Mouth segments */}
      <line x1="16" y1="20" x2="16" y2="22" stroke="#e0f2fe" strokeWidth="0.5" />
      <line x1="20" y1="20" x2="20" y2="22" stroke="#e0f2fe" strokeWidth="0.5" />
      {/* Ear bolts */}
      <circle cx="8" cy="17" r="2" fill="#94a3b8" />
      <circle cx="28" cy="17" r="2" fill="#94a3b8" />
    </svg>
  )
}

/* ── Feature flag ──────────────────────────────────────────────── */

const SHOW_GUIDE_TYPING = true // Toggle to disable typing indicator

/* ── Typing indicator component ───────────────────────────────── */

function TypingIndicator() {
  return (
    <div className="chat-row chat-row-left typing-indicator" role="status" aria-label="Guide is speaking">
      <div className="chat-avatar"><GuideAvatar /></div>
      <div className="chat-bubble chat-bubble-guide typing-bubble">
        <span className="typing-dot" />
        <span className="typing-dot" />
        <span className="typing-dot" />
      </div>
    </div>
  )
}

/* ── Component ──────────────────────────────────────────────────── */

function ListenerPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const codeFromUrl = searchParams.get('code')

  const [joinCode, setJoinCode] = useState(codeFromUrl || '')
  const [selectedLanguage, setSelectedLanguage] = useState('en')
  const [isJoined, setIsJoined] = useState(false)
  const [chatMessages, setChatMessages] = useState([])
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [isBypassMode, setIsBypassMode] = useState(false)
  const [showIndicator, setShowIndicator] = useState(false)
  const [sessionEnded, setSessionEnded] = useState(false)

  const wsRef = useRef(null)
  const audioContextRef = useRef(null)
  const audioQueueRef = useRef([])
  const isPlayingRef = useRef(false)
  const bypassNextTimeRef = useRef(0)
  const chatEndRef = useRef(null)
  const messageIdRef = useRef(0)
  const guideSpeakingRef = useRef(false)
  const showDebounceRef = useRef(null)
  const reShowTimerRef = useRef(null)

  useEffect(() => {
    return () => cleanup()
  }, [])

  // Auto-scroll to newest message or typing indicator
  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [chatMessages, showIndicator])

  const initAudioContext = () => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()
    }
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

  const clearTypingTimers = useCallback(() => {
    if (showDebounceRef.current) { clearTimeout(showDebounceRef.current); showDebounceRef.current = null }
    if (reShowTimerRef.current) { clearTimeout(reShowTimerRef.current); reShowTimerRef.current = null }
  }, [])

  const stopTypingIndicator = useCallback(() => {
    guideSpeakingRef.current = false
    clearTypingTimers()
    setShowIndicator(false)
  }, [clearTypingTimers])

  const handleWebSocketMessage = useCallback((message) => {
    const { type, payload } = message

    switch (type) {
      case 'listener_joined':
        setIsJoined(true)
        setStatus('')
        setError('')
        break

      case 'audio':
        queueAudio(payload.audioData)
        break

      case 'bypass_audio':
        playBypassAudio(payload.audioData)
        setIsBypassMode(true)
        break

      case 'subtitle': {
        const id = ++messageIdRef.current
        setChatMessages(prev => [...prev, {
          id,
          originalText: payload.originalText || null,
          translatedText: payload.text,
          timestamp: Date.now()
        }])
        // Hide typing indicator on finalization
        if (SHOW_GUIDE_TYPING) {
          clearTypingTimers()
          setShowIndicator(false)
          // Re-show after 1.5s if guide is still speaking
          if (guideSpeakingRef.current) {
            reShowTimerRef.current = setTimeout(() => {
              if (guideSpeakingRef.current) setShowIndicator(true)
            }, 1500)
          }
        }
        break
      }

      case 'guide_speaking_started':
        if (SHOW_GUIDE_TYPING) {
          guideSpeakingRef.current = true
          clearTypingTimers()
          // 300ms debounce to avoid flash for instant finalizations
          showDebounceRef.current = setTimeout(() => {
            if (guideSpeakingRef.current) setShowIndicator(true)
          }, 300)
        }
        break

      case 'guide_speaking_stopped':
        if (SHOW_GUIDE_TYPING) stopTypingIndicator()
        break

      case 'speaker_disconnected':
        bypassNextTimeRef.current = 0
        if (SHOW_GUIDE_TYPING) stopTypingIndicator()
        cleanup()
        setSessionEnded(true)
        break

      case 'error':
        setError(payload.message)
        setStatus('')
        break
    }
  }, [clearTypingTimers, stopTypingIndicator])

  /* ── Audio playback (unchanged logic) ───────────────────────── */

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
      if (!ctx) { playNextInQueue(); return }
      if (ctx.state === 'suspended') await ctx.resume()

      const binaryString = atob(audioDataBase64)
      const bytes = new Uint8Array(binaryString.length)
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i)
      }

      const audioBuffer = await ctx.decodeAudioData(bytes.buffer.slice(0))
      const source = ctx.createBufferSource()
      source.buffer = audioBuffer
      source.connect(ctx.destination)
      source.onended = () => playNextInQueue()
      source.start(0)
    } catch (err) {
      console.error('Error playing audio:', err)
      playNextInQueue()
    }
  }

  const playBypassAudio = async (audioDataBase64) => {
    try {
      const ctx = audioContextRef.current
      if (!ctx) return
      if (ctx.state === 'suspended') await ctx.resume()

      const binaryString = atob(audioDataBase64)
      const bytes = new Uint8Array(binaryString.length)
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i)
      }
      const audioBuffer = await ctx.decodeAudioData(bytes.buffer.slice(0))

      const now = ctx.currentTime
      if (bypassNextTimeRef.current < now) bypassNextTimeRef.current = now
      if (bypassNextTimeRef.current > now + 2) bypassNextTimeRef.current = now

      const source = ctx.createBufferSource()
      source.buffer = audioBuffer
      source.connect(ctx.destination)
      source.start(bypassNextTimeRef.current)
      bypassNextTimeRef.current += audioBuffer.duration
    } catch (err) {
      console.error('Bypass audio error:', err)
    }
  }

  const handleLeave = () => {
    cleanup()
    setIsJoined(false)
    setIsBypassMode(false)
    setShowIndicator(false)
    setSessionEnded(false)
    setStatus('')
    setChatMessages([])
  }

  const handleReturnHome = () => {
    cleanup()
    navigate('/')
  }

  const cleanup = () => {
    audioQueueRef.current = []
    isPlayingRef.current = false
    bypassNextTimeRef.current = 0
    guideSpeakingRef.current = false
    clearTypingTimers()
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }

  /* ── Joined: Chat conversation view ─────────────────────────── */

  if (isJoined) {
    const langName = LANGUAGES.find(l => l.code === selectedLanguage)?.name || selectedLanguage

    return (
      <div className="chat-container">
        {/* Header bar */}
        <div className="chat-header">
          <div className="chat-header-left">
            <GuideAvatar />
            <div>
              <div className="chat-header-title">Live Tour</div>
              <div className="chat-header-lang">{langName}</div>
            </div>
          </div>
          <button className="chat-leave-btn" onClick={handleLeave}>Leave</button>
        </div>

        {error && <div className="chat-error">{error}</div>}

        {/* Chat body */}
        <div className="chat-body">
          {chatMessages.length === 0 && !status && (
            <div className="chat-empty">
              <div className="chat-empty-icon">
                <GuideAvatar />
              </div>
              <p className="chat-empty-title">Waiting for the guide...</p>
              <p className="chat-empty-sub">Translations will appear here as the tour begins</p>
            </div>
          )}

          {status && chatMessages.length === 0 && (
            <div className="chat-status-pill">{status}</div>
          )}

          {isBypassMode ? (
            <div className="chat-bypass">
              <div className="chat-bypass-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M12 3C7.03 3 3 7.03 3 12s4.03 9 9 9 9-4.03 9-9-4.03-9-9-9zm-1 14v-6l5 3-5 3z" fill="#667eea"/></svg>
              </div>
              <span>Live audio — same language as speaker</span>
            </div>
          ) : (
            chatMessages.map((msg) => (
              <div key={msg.id} className="chat-exchange">
                {/* Guide bubble (original speech) */}
                {msg.originalText && (
                  <div className="chat-row chat-row-left">
                    <div className="chat-avatar"><GuideAvatar /></div>
                    <div className="chat-bubble chat-bubble-guide">
                      {msg.originalText}
                    </div>
                  </div>
                )}
                {/* Translation bubble */}
                <div className="chat-row chat-row-right">
                  <div className="chat-bubble chat-bubble-translation">
                    {msg.translatedText}
                  </div>
                  <div className="chat-avatar"><TranslatorAvatar /></div>
                </div>
              </div>
            ))
          )}

          {status && chatMessages.length > 0 && (
            <div className="chat-status-pill">{status}</div>
          )}

          {SHOW_GUIDE_TYPING && showIndicator && !isBypassMode && <TypingIndicator />}

          <div ref={chatEndRef} />
        </div>

        {/* Session ended overlay */}
        {sessionEnded && (
          <div className="session-ended-overlay">
            <div className="session-ended-card">
              <div className="session-ended-icon">
                <svg width="56" height="56" viewBox="0 0 56 56" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <circle cx="28" cy="28" r="28" fill="#667eea" opacity="0.1" />
                  <circle cx="28" cy="28" r="20" fill="#667eea" opacity="0.15" />
                  <path d="M22 28l4 4 8-8" stroke="#667eea" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <h2 className="session-ended-title">Thank you for joining the tour!</h2>
              <p className="session-ended-sub">This session has now ended. We hope you enjoyed the experience.</p>
              <button className="session-ended-btn" onClick={handleReturnHome}>
                Return to Home
              </button>
            </div>
          </div>
        )}
      </div>
    )
  }

  /* ── Join form (pre-join) ───────────────────────────────────── */

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
