# Vavilon — Real-Time Translation Platform

**Production URL:** https://www.vavilonapp.rs
**Status:** ✅ Fully Operational in Production
**Last Updated:** March 2026

---

## Overview

Vavilon is a real-time spoken translation web app for tours, museums, and conferences. One speaker broadcasts to multiple listeners; each listener receives live translated speech and subtitles in their chosen language. Web-only, no app installation required. Target capacity: 200 concurrent listeners across 20 languages.

**Deployment Model:** Three-service architecture on Microsoft Azure: React frontend (Static Web Apps), Node.js backend (App Service with Redis session cache), Python AI service (Container Instance with Azure Speech SDK). Continuous delivery via GitHub Actions from main branch.

---

## Architecture

```
Browser (React) ──WebSocket──▶ Node.js backend ──HTTP REST──▶ Python AI service
                 ◀──────────── (Express + WS)   ◀──────────── (Flask + Gunicorn)
                                     │                              │
                               Redis (Sessions)             Azure Speech SDK
                                                         (STT + Translation + TTS)
```

| Service | Stack | Hosting | Deploy |
|---|---|---|---|
| Frontend | React + Vite | Azure Static Web Apps | Auto via GitHub Actions |
| Backend | Node.js / Express | Azure App Service | Manual zip deploy |
| AI Service | Python / Flask / Gunicorn | Azure Container Instance | Manual restart |
| Cache | Redis | Azure Cache for Redis (`vavilon-redis`) | Managed |

**Resource group:** `vavilon-rg`, region: West Europe

**Key service URLs:**
- Frontend: `https://www.vavilonapp.rs`
- Backend: `https://vavilon-backend.azurewebsites.net`
- AI Service: `http://vavilon-ai.westeurope.azurecontainer.io:5000` (internal)
- Docker image: `vavilonacr.azurecr.io/vavilon-ai:latest`

---

## How It Works — Complete System Flow

### 1. Guide Login & Session Creation
```
Guide → POST /api/auth/login {username}
Backend → Redis: GET guide:{username}
Backend checks: does today fall within any accessWindow?
  A. Yes → {success: true, access: true, guide: {firstName, lastName, username}}
  B. No  → {success: true, access: false, scheduledWindows: [...]}
  C. Not found → {success: false, error: "not_found"}

Guide → POST /api/sessions
Backend → Redis: store session:{uuid} + code:{joinCode}
Backend → return {sessionId, joinCode, qrCode, joinUrl}
```

### 2. Speaker Joins via WebSocket
```
Browser → WS: {type: "speaker_join", payload: {sessionId}}
Backend → Redis: setSpeakerConnected = true
Backend → WS: {type: "speaker_joined", payload: {supportedLanguages}}
```

### 3. Listeners Join
```
Browser → WS: {type: "listener_join", payload: {sessionId, language}}
Backend → Redis: addListener(sessionId, connectionId, language)
Backend → WS: {type: "listener_joined"}
```

### 4. Speaker Starts Speaking
```
Browser → WS: {type: "start_speaking", payload: {sourceLanguage, targetLanguages}}
Backend → Python: POST /start-session {sessionId, traceId, sourceLanguage, targetLanguages}
Python: Creates Azure TranslationRecognizer, starts continuous recognition
Backend → WS: {type: "speaking_started", payload: {traceId}}
Backend → All listeners: {type: "guide_speaking_started"}
```

### 5. Audio Streaming (12 chunks/second)
```
Browser → WS: {type: "audio_chunk", payload: {audioData: "base64-pcm"}}
Backend → Python: POST /process-audio {sessionId, traceId, seqNo, audioData}
Python: Pushes to Azure PushAudioInputStream (non-blocking queue, maxsize=200)
Python → Backend: HTTP 200 {success: true}
```

Same-language bypass: if listener's language === speaker's sourceLanguage, raw PCM (wrapped in WAV header) is sent directly to that listener without going through Azure — latency <200ms.

### 6. Recognition & Translation (Azure SDK)
```
Azure SDK: Recognizes speech segment → fires callback
Python callback: Receives translations {"es": "Hola", "it": "Ciao"}
Python → Backend: POST /api/events {type: "segment_finalized", translations, segmentId}
Backend → Redis: getListenersByLanguage(sessionId, language)
Backend → Each listener: WS {type: "subtitle", payload: {text, language}}
Backend → Python: POST /generate-tts {sessionId, segmentId, translations}
```

### 7. TTS Synthesis (Async, Per Language)
```
Python: TTS worker thread per language calls Azure Speech SDK synthesize()
Azure SDK: Returns RIFF 16kHz 16-bit PCM audio bytes
Python → Backend: POST /api/events {type: "tts_ready", language, audioBytesBase64}
Backend → Listener: WS {type: "audio", payload: {audioData, language}}
```

### 8. Speaker Stops Speaking
```
Browser → WS: {type: "stop_speaking"}
Backend → Python: POST /end-session {sessionId, traceId, graceful: true}
Python: Closes audio stream gracefully → Azure finalizes last segment (2s window)
Backend → All listeners: {type: "guide_speaking_stopped"}
conn.traceId = null, conn.seqNo = 0
```

---

## Key Files

| File | Purpose |
|---|---|
| `backend/src/index.js` | Express app entry, mounts routes, starts WebSocket + watchdog |
| `backend/src/websocket/wsHandler.js` | WebSocket connection handling, speaker/listener message routing, audio forwarding |
| `backend/src/services/sessionService.js` | Redis session CRUD (create/get/addListener/removeListener/end) |
| `backend/src/services/watchdogService.js` | Background watchdog: kills ghost/orphan/expired sessions |
| `backend/src/services/authService.js` | Guide login: checkAccess() — validates username + date windows |
| `backend/src/routes/auth.js` | POST /api/auth/login |
| `backend/src/routes/events.js` | POST /api/events — receives segment_finalized and tts_ready from Python |
| `backend/src/routes/sessions.js` | POST /api/sessions, DELETE /api/sessions/:id |
| `backend/data/guides.csv` | Guide accounts and access windows |
| `backend/scripts/import-guides.js` | CLI tool to sync guides.csv → Redis |
| `ai-service/src/app.py` | Flask routes: /start-session, /process-audio, /generate-tts, /end-session |
| `ai-service/src/speech_service.py` | TranslationSession class — Azure Speech SDK integration |
| `ai-service/gunicorn.conf.py` | Gunicorn config: 1 worker, 4 threads, 60s timeout |
| `frontend/src/pages/SpeakerPage.jsx` | Guide speaker UI |
| `frontend/src/pages/ListenerPage.jsx` | Listener UI with chat/subtitle view |
| `frontend/src/pages/GuidePage.jsx` | Guide login page |

---

## AI Service — Production Configuration

Running on **Gunicorn WSGI** (not Flask dev server):
- **1 worker process** — shared memory required for Azure SDK session objects
- **4 threads** (`gthread` worker class) — handles concurrent audio + TTS + callbacks
- **60s timeout** — accommodates Azure SDK long-running operations

```python
# ai-service/gunicorn.conf.py
workers = 1
threads = 4
worker_class = "gthread"
timeout = 60
```

**Why single worker?** Azure SDK objects (`TranslationRecognizer`, `PushAudioInputStream`) cannot be serialized or shared across processes. All session state lives in a `sessions: dict` in memory.

---

## WebSocket Connection Management

**Ping/Pong Heartbeat:**
- Backend pings every 30 seconds
- Connections without a pong response within 45 seconds are terminated
- Browsers handle ping frames automatically at the protocol level

**Session Watchdog** (runs every 5 minutes):
- Ghost sessions: speaker connected but no audio for 3 hours → force end
- Max duration: sessions older than 12 hours → force end
- Orphan sessions: Redis entry exists but no active WebSocket for 30 minutes → force end

---

## Guide Management

Guides are stored in Redis as `guide:{username}` keys. Access is date-window based — no payment check.

**CSV fields:** `name, surname, username, email, phone, access_start_date, access_end_date`

Multiple rows with the same username = multiple access windows (merged into one Redis record).

**Username format:** `firstname.lastname.NNNN` (lowercase, 4-digit random suffix)

**Import / update guides:**
```bash
cd backend
REDIS_URL=vavilon-redis.redis.cache.windows.net \
REDIS_PASSWORD='<redis-primary-key>' \
node scripts/import-guides.js
```

**Admin REST API** (protected by `X-Admin-Key` header):
```http
POST /api/admin/guides   — add or update a single guide
GET  /api/admin/guides   — list all guides
```

---

## Supported Languages

20 languages: `en, es, fr, de, it, pt, ru, zh, ja, ar, sr, mk, bg, hu, ro, hr, sl, sk, pl, uk`

**Language code formats (do NOT mix up):**
- Short codes (`it`, `sr`) → Frontend, Redis keys, broadcast routing
- Translation codes (`zh-Hans`) → Azure `add_target_language()`
- Locale codes (`it-IT`, `sr-RS`) → Azure STT config and TTS voices

---

## Environment Variables

### Backend (Azure App Service)
| Variable | Purpose |
|---|---|
| `REDIS_URL` | Azure Redis hostname (no protocol/port) |
| `REDIS_PASSWORD` | Redis primary key |
| `AI_SERVICE_URL` | Python service URL (`http://vavilon-ai.westeurope.azurecontainer.io:5000`) |
| `FRONTEND_URL` | Used for QR code join links (`https://www.vavilonapp.rs`) |
| `ADMIN_SECRET` | API key for `/api/admin/*` endpoints |

### AI Service (Container Instance)
| Variable | Purpose |
|---|---|
| `AZURE_SPEECH_KEY` | Azure Cognitive Services key |
| `AZURE_SPEECH_REGION` | `westeurope` |
| `NODE_BACKEND_URL` | Backend URL for event callbacks (`https://vavilon-backend.azurewebsites.net`) |
| `PORT` | `5000` |

---

## API Reference

### Backend REST

```http
POST   /api/auth/login              Guide login
POST   /api/sessions                Create session
DELETE /api/sessions/:id            End session
POST   /api/events                  Receive events from Python (segment_finalized, tts_ready)
POST   /api/admin/guides            Add/update guide (requires X-Admin-Key)
GET    /api/admin/guides            List guides (requires X-Admin-Key)
GET    /health                      Health check
```

### AI Service REST

```http
POST /start-session     Create Azure Speech session
POST /process-audio     Push audio chunk to recognition queue
POST /generate-tts      Synthesize TTS for a recognized segment
POST /end-session       Stop recognition (graceful=true closes stream first)
GET  /health            Health check
GET  /sessions          List active session IDs
GET  /metrics           Latency and call counters
```

### WebSocket Message Types

**Browser → Backend:**
```json
{"type": "speaker_join",      "payload": {"sessionId": "..."}}
{"type": "listener_join",     "payload": {"sessionId": "...", "language": "es"}}
{"type": "start_speaking",    "payload": {"sourceLanguage": "en-US", "targetLanguages": ["es","fr"]}}
{"type": "audio_chunk",       "payload": {"audioData": "<base64-pcm>"}}
{"type": "stop_speaking"}
{"type": "speaker_disconnect"}
```

**Backend → Browser:**
```json
{"type": "speaker_joined",          "payload": {"sessionId": "...", "supportedLanguages": [...]}}
{"type": "listener_joined",         "payload": {"sessionId": "...", "language": "es"}}
{"type": "speaking_started",        "payload": {"traceId": "..."}}
{"type": "subtitle",                "payload": {"text": "Hola", "language": "es"}}
{"type": "audio",                   "payload": {"audioData": "<base64-wav>", "language": "es"}}
{"type": "bypass_audio",            "payload": {"audioData": "<base64-wav>"}}
{"type": "guide_speaking_started",  "payload": {}}
{"type": "guide_speaking_stopped",  "payload": {}}
{"type": "speaker_disconnected",    "payload": {}}
{"type": "session_ended",           "payload": {"reason": "..."}}
{"type": "error",                   "payload": {"message": "...", "code": "..."}}
```

---

## Recent Changes (March 2026)

- **Session watchdog** (`watchdogService.js`) — Background service that auto-kills ghost sessions (no audio for 3h), orphan Redis-only sessions (30 min), and sessions exceeding 12h.
- **Ping/pong heartbeat** — WebSocket connections monitored every 30s, stale connections terminated after 45s without pong.
- **`/sessions` debug endpoint** on AI service — lists active Python session IDs for observability.
- **Subscription gate removed** — guide access is purely date-window based. No agency subscription check on login.
- **Guide import script** — `backend/scripts/import-guides.js` syncs `guides.csv` to Redis; multiple rows per username merge into multiple access windows.
- **Payten payment** — agencies subscribe on `vavilonsolutions.rs/pricing`; Redis keys `agency:{name}` store subscription state. Card data never touches this server.
- **Lead capture** — marketing landing page lead form writes to Redis.

## Previous Changes (February 2026)

- **Guide login & access control** — guides log in by username; three outcomes: access granted, access window in future, username not found.
- **20 languages** — added sr, mk, bg, hu, ro, hr, sl, sk, pl, uk (was 10).
- **Same-language bypass** — listeners selecting the speaker's language get raw PCM directly, no translation, <200ms latency.
- **Chat conversation UI** — listener subtitle view redesigned as a chat interface with guide avatar and typing indicator.
- **QR code deep links** — QR codes link directly to `/join?code=ABC123`.
- **End-of-session overlay** — listeners see a thank-you screen when the guide disconnects.
- **Audio-only listening view** — listeners can hide subtitles and use audio only.
- **Gunicorn migration** — replaced Flask dev server with Gunicorn (1 worker, 4 gthread threads, 60s timeout). Solved single-threaded blocking.
- **Graceful session stop** — `end-session` with `graceful=true` closes audio stream before stopping recognizer; Azure finalizes last utterance.

---

## Troubleshooting

### "Failed to start translation session"
The AI container process has died (container shows Running but Flask is frozen).
```bash
az container restart --name vavilon-ai --resource-group vavilon-rg
```

### Backend 500 errors / not responding
```bash
az webapp restart --name vavilon-backend --resource-group vavilon-rg
```

### SESSION_DEAD errors (410 from /process-audio)
Python's in-memory session died (audio queue overflow or recognizer crash). Speaker must stop and restart speaking. Sessions are not persisted in Redis — an AI container restart wipes all active sessions.

### Redis SocketClosedUnexpectedlyError
Transient Azure Cache connection drop. The Redis client has auto-reconnect with exponential backoff (10 attempts, max 2s delay). Usually self-healing; if persistent, check Redis resource in Azure portal.

---

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full runbook.

**TL;DR:**
1. `git push origin main` — frontend auto-deploys (~3 min)
2. Build zip → `az webapp deployment source config-zip` — deploys backend (~3 min)
3. `az container restart --name vavilon-ai --resource-group vavilon-rg` — **always required** after any deployment or when AI service is unresponsive
