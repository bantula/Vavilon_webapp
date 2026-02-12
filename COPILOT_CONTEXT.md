# Vavilon — Copilot Context File

Use this file to get up to speed when starting a fresh chat. It covers architecture, current state, data flow, key files, and known issues.

---

## What is Vavilon?

Real-time spoken translation web app for tours/museums/conferences. **One speaker → many listeners** broadcast system. Speaker talks into their phone, listeners each hear translated speech + subtitles in their chosen language. Web-only, no app install.

Target: up to 200 concurrent listeners, 10 languages, demo-ready MVP.

---

## Architecture (3 services)

```
┌─────────────┐   WebSocket    ┌──────────────────┐   HTTP (REST)   ┌─────────────────────┐
│   Frontend   │ ──────────── > │  Node.js Backend │ ──────────── > │  Python AI Service  │
│  (React/Vite)│ < ──────────── │  (Express + WS)  │ < ──────────── │  (Flask + Azure SDK) │
│  port 5173   │                │    port 3000     │                │     port 5000        │
└─────────────┘                └──────────────────┘                └─────────────────────┘
                                      │
                                      ▼
                                   Redis
                              (session storage)
```

### Azure Deployment

| Service | Azure Resource | Region |
|---------|---------------|--------|
| Backend | App Service (`vavilon-backend`) | westeurope |
| AI Service | Container Instance (`vavilon-ai`) | westeurope |
| Frontend | Static Web Apps (GitHub-linked) | auto |
| Sessions | Azure Cache for Redis | westeurope |
| Speech | Azure Cognitive Services Speech | westeurope |
| Domain | vavilonapp.rs (custom) | — |

Resource group: `vavilon-rg`

---

## Data Flow (audio pipeline)

```
1. Speaker's browser captures raw PCM audio (16kHz, 16-bit, mono)
   - AudioContext + ScriptProcessorNode → Float32 → downsample to Int16 @ 16kHz
   - Base64-encode and send over WebSocket as JSON { type: "audio_chunk", payload: { audioData } }

2. Node.js backend receives audio_chunk, forwards to AI service
   - HTTP POST to AI_SERVICE_URL/process-audio with { sessionId, audioData (base64) }

3. Python AI service pushes PCM bytes into PushAudioInputStream
   - Azure TranslationRecognizer does continuous STT + translation in one step
   - On recognition: translated text is queued per target language

4. Per-language SpeechSynthesizer threads synthesize TTS audio
   - Subtitles broadcast immediately via POST to Node backend /api/broadcast
   - Synthesized WAV audio broadcast via POST to Node backend /api/broadcast

5. Node.js backend broadcasts to listeners over WebSocket
   - { type: "subtitle", payload: { text, language } }
   - { type: "audio", payload: { audioData (base64 WAV), language } }

6. Listener's browser decodes WAV with AudioContext.decodeAudioData and plays sequentially
```

---

## Key Files

### Backend (Node.js)
| File | Purpose |
|------|---------|
| `backend/src/index.js` | Express server, CORS, health check, App Insights |
| `backend/src/websocket/wsHandler.js` | WebSocket handler — speaker/listener connections, audio relay |
| `backend/src/routes/sessions.js` | REST API for session CRUD |
| `backend/src/routes/broadcast.js` | POST /api/broadcast — receives translations from AI, sends to listeners |
| `backend/src/services/sessionService.js` | Redis-backed session management, join codes, listener tracking |

### AI Service (Python)
| File | Purpose |
|------|---------|
| `ai-service/src/app.py` | Flask server with `/start-session`, `/process-audio`, `/end-session` |
| `ai-service/src/speech_service.py` | `TranslationSession` class — Azure SDK continuous recognition + TTS |
| `ai-service/Dockerfile` | Python 3.9 slim container with Azure Speech SDK system deps |
| `ai-service/requirements.txt` | flask, flask-cors, azure-cognitiveservices-speech, requests, python-dotenv |

### Frontend (React + Vite)
| File | Purpose |
|------|---------|
| `frontend/src/pages/SpeakerPage.jsx` | Mic capture (raw PCM), source language selector, WebSocket streaming |
| `frontend/src/pages/ListenerPage.jsx` | Join by code, language select, audio queue playback, subtitle display |
| `frontend/src/pages/LandingPage.jsx` | Home page — create session or join |
| `frontend/src/config.js` | API URL + WebSocket URL helper (uses VITE_BACKEND_URL) |

### Other
| File | Purpose |
|------|---------|
| `DEPLOYMENT.md` | Full Azure deployment guide (all steps) |
| `debug/send_test_audio.py` | End-to-end pipeline test script — streams audio chunks, checks trace/metrics |
| `help/dubber.py` | Legacy hardware version code — NOT used in web app, safe to delete |
| `help/audio_interface.py` | Legacy UDP streaming code — NOT used in web app, safe to delete |
| `help/auxiliary_functions.py` | Legacy helper functions — NOT used in web app, safe to delete |

---

## WebSocket Message Types

### Speaker → Backend
| Type | Payload | When |
|------|---------|------|
| `speaker_join` | `{ sessionId }` | On connect |
| `start_speaking` | `{ sourceLanguage, targetLanguages }` | Click "Start Speaking" |
| `audio_chunk` | `{ audioData }` (base64 Int16 PCM) | Continuously while recording |
| `stop_speaking` | `{}` | Click "Stop Speaking" |
| `speaker_disconnect` | `{}` | On page close |

### Backend → AI Service (HTTP)
| Endpoint | Body | When |
|----------|------|------|
| `POST /start-session` | `{ sessionId, sourceLanguage, targetLanguages }` | Speaker starts |
| `POST /process-audio` | `{ sessionId, audioData }` (base64) | Each audio chunk |
| `POST /end-session` | `{ sessionId }` | Speaker stops |

### AI Service → Backend (HTTP)
| Endpoint | Body | When |
|----------|------|------|
| `POST /api/broadcast` | `{ sessionId, language, subtitleText }` | On each recognition |
| `POST /api/broadcast` | `{ sessionId, language, audioData }` (base64 WAV) | After TTS synthesis |

### Backend → Listener
| Type | Payload | When |
|------|---------|------|
| `listener_joined` | `{ sessionId, language }` | Confirmation |
| `subtitle` | `{ text, language }` | Each translated sentence (translation mode) |
| `audio` | `{ audioData, language }` (base64 WAV) | Each synthesized audio (translation mode) |
| `bypass_audio` | `{ audioData }` (base64 WAV-wrapped PCM) | Each raw audio chunk (bypass mode) |
| `speaker_disconnected` | `{}` | Speaker leaves |

---

## Environment Variables

### Backend (.env)
```
PORT=3000
AI_SERVICE_URL=http://localhost:5000        # or https://vavilon-ai.westeurope.azurecontainer.io:5000
FRONTEND_URL=http://localhost:5173
REDIS_URL=rediss://<name>.redis.cache.windows.net:6380
REDIS_PASSWORD=<key>
APPINSIGHTS_INSTRUMENTATIONKEY=<optional>
```

### AI Service (.env)
```
PORT=5000
AZURE_SPEECH_KEY=<key>
AZURE_SPEECH_REGION=westeurope
NODE_BACKEND_URL=http://localhost:3000      # or https://vavilon-backend.azurewebsites.net
```

### Frontend (.env)
```
VITE_BACKEND_URL=http://localhost:3000      # or https://vavilon-backend.azurewebsites.net
```

---

## Azure Speech SDK Patterns (critical)

The AI service uses `azure-cognitiveservices-speech` with this pattern:

1. **PushAudioInputStream** — browser audio is pushed in as raw PCM (16kHz/16-bit/mono)
2. **SpeechTranslationConfig** — combined STT + translation in one recognizer
3. **TranslationRecognizer.start_continuous_recognition_async()** — persistent, not per-chunk
4. **recognized callback** — `evt.result.translations[lang_code]` gives translated text
5. **Per-language SpeechSynthesizer** — `audio_config=None` returns WAV bytes in `result.audio_data`
6. **Threading** — one synthesis thread per target language, pulls from a queue

Language maps in `speech_service.py`:
```python
TRANSLATION_LANG_MAP = {
    'en': 'en', 'es': 'es', 'fr': 'fr', 'de': 'de', 'it': 'it',
    'pt': 'pt', 'ru': 'ru', 'zh': 'zh-Hans', 'ja': 'ja', 'ar': 'ar'
}
TTS_LOCALE_MAP = {
    'en': 'en-US', 'es': 'es-ES', 'fr': 'fr-FR', 'de': 'de-DE',
    'it': 'it-IT', 'pt': 'pt-PT', 'ru': 'ru-RU', 'zh': 'zh-CN',
    'ja': 'ja-JP', 'ar': 'ar-SA'
}
```

---

## Browser Audio Capture (critical)

The speaker page uses **AudioContext + ScriptProcessorNode** (NOT MediaRecorder):
- MediaRecorder outputs WebM/Opus — Azure SDK cannot read this
- AudioContext gives raw Float32 PCM at the browser's native rate (44100 or 48000 Hz)
- `downsampleToInt16()` converts Float32 → Int16 and resamples to 16kHz
- Result is base64-encoded and sent as JSON over WebSocket

---

## CORS Configuration

Backend allows these origins (in `backend/src/index.js`):
```
http://localhost:5173
https://green-pond-05766a403.1.azurestaticapps.net
https://vavilonapp.rs
https://www.vavilonapp.rs
```

---

## Same-Language Voice Bypass Mode

When a listener's language matches the speaker's source language, audio is streamed **directly** from the speaker through the Node.js backend to that listener. No Azure, no AI service, no STT/TTS/translation.

### How it works
```
Speaker mic → audio_chunk → Node backend
                              ├── Forward to AI service (always, for translation pipeline)
                              └── If listener.language == speaker.sourceLanguage.split('-')[0]:
                                    Wrap PCM in WAV header → send as 'bypass_audio' → Listener
```

### Implementation details
- **Backend** (`wsHandler.js`): `handleAudioChunk` calls `broadcastBypassAudio()` alongside `forwardAudioToAI()`
- **Bypass routing** uses in-memory `connections` Map (no Redis roundtrip) for low latency
- **WAV wrapping**: `createWavHeader()` prepends a 44-byte RIFF header so the browser's `decodeAudioData` works
- **Listener** (`ListenerPage.jsx`): `playBypassAudio()` uses `AudioContext.currentTime` scheduling for gapless playback
- **No changes** to AI service, SpeakerPage, session management, or broadcast route
- Bypass listeners see "Live audio — same language as speaker" instead of subtitles
- **Bandwidth**: ~43KB/sec per bypass listener (12 chunks/sec × 3.6KB each)

### Separation
| | Translation Mode | Bypass Mode |
|---|---|---|
| Trigger | listener lang != speaker lang | listener lang == speaker lang |
| Pipeline | Speaker → Backend → AI → Backend → Listener | Speaker → Backend → Listener |
| Audio format | WAV from TTS (24kHz) | WAV-wrapped PCM (16kHz) |
| Message type | `audio` + `subtitle` | `bypass_audio` |
| Latency | ~2-5s (STT + translate + TTS) | ~100-200ms (network only) |
| Playback | Queue-based sequential | Time-scheduled gapless |

---

## Debugging & Monitoring (Added Feb 2026)

### Backend Structured Logging
- **Structured JSON logs** (slog format) in `wsHandler.js`
- **Trace ID generation** for each session to track requests across services
- **Sequence numbers** per audio chunk for debugging lost packets
- **Throttled logging** — logs every 50th chunk to reduce noise
- **Broadcast logging** — tracks subtitle and audio broadcasts to listeners

### AI Service Debug Endpoints
- **POST /start-session** — returns `trace_id` for the session
- **GET /debug/trace/:trace_id** — retrieves ring buffer of last 100 trace events for a session
- **GET /metrics** — returns session counts, recognition stats, TTS queue depths, error counts
- **GET /health** — basic health check endpoint

### AI Service Instrumentation
- **Metrics collection** — tracks active sessions, total recognitions, TTS operations, errors
- **Trace ring buffer** — keeps last 100 events per session (start, recognizing, recognized, synthesizing, broadcast)
- **DEBUG mode** — saves raw audio chunks to disk when `DEBUG=true` environment variable is set
- **Full exception handling** — TTS threads catch all exceptions to prevent silent death
- **Empty audio detection** — logs warning when synthesizer returns empty audio data
- **Partial recognition callback** (`_on_recognizing`) — logs streaming recognition for visibility

### Fixed Issues
- **TTS format changed** to `Riff16Khz16BitMonoPcm` for better compatibility
- **Cancellation handling** fixed to use `evt.cancellation_details` instead of `evt.result`
- **Thread error visibility** — TTS synthesis threads now log all exceptions

---

## Known Issues / TODOs

1. **ScriptProcessorNode is deprecated** — works fine but browsers recommend AudioWorklet. Low priority.
2. **No authentication** — intentional for MVP. Sessions are public with a 6-char join code.
3. **In-memory AI sessions** — if the AI container restarts, active translation sessions are lost.
4. **Express body limit** set to 5MB (`express.json({ limit: '5mb' })`) to allow TTS audio payloads.
5. **After code changes, all 3 services need redeployment** to test end-to-end.

---

## Deployment Cheat Sheet

### Backend (Step 3.3)
```powershell
# Create zip with Python (NOT PowerShell Compress-Archive — it uses backslash paths that break Linux)
cd backend
python -c "import zipfile, os; zf=zipfile.ZipFile('deploy.zip','w',zipfile.ZIP_DEFLATED); [zf.write(os.path.join(r,f), os.path.join(r,f).replace(os.sep,'/')) for r,d,fs in os.walk('src') for f in fs]; [zf.write(x) for x in ['package.json','package-lock.json']]; zf.close()"

# Deploy (use config-zip, NOT "az webapp deploy --type zip")
az webapp deployment source config-zip --resource-group vavilon-rg --name vavilon-backend --src deploy.zip
```

### AI Service (Step 2)
```powershell
cd ai-service
az acr build --registry vavilonacr --image vavilon-ai:latest .
# Restart the container instance to pick up new image
az container restart --resource-group vavilon-rg --name vavilon-ai
```

### Frontend
Auto-deploys via GitHub Actions when pushed to main (Static Web Apps).