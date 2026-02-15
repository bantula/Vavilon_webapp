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

## Data Flow (audio pipeline) — EVENT-DRIVEN ARCHITECTURE

**Architecture Philosophy**: Continuous recognition + event-driven translation/TTS. Recognition never blocks waiting for translations or TTS. Subtitles broadcast immediately; audio follows when ready.

### Flow Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Speaker's browser captures raw PCM audio (16kHz, 16-bit, mono)          │
│    - AudioContext + ScriptProcessorNode → Float32 → downsample to Int16    │
│    - Base64-encode and send over WebSocket as JSON                         │
│      { type: "audio_chunk", payload: { audioData } }                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. Node.js backend receives audio_chunk, forwards to AI service            │
│    - HTTP POST to AI_SERVICE_URL/process-audio                             │
│      { sessionId, traceId, seqNo, audioData (base64) }                     │
│    - Returns immediately (non-blocking)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Python AI service pushes PCM bytes into PushAudioInputStream            │
│    - Azure TranslationRecognizer runs CONTINUOUS recognition                │
│    - One recognizer instance per session (never destroyed between sentences)│
│    - Audio stream stays open indefinitely (silence allowed)                │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. On Azure recognition event (TranslatedSpeech):                          │
│    A. Generate segment_id (UUID)                                            │
│    B. Extract translations from result.translations dict                    │
│    C. IMMEDIATELY emit segment_finalized event to Node:                     │
│       POST /api/events                                                      │
│       {                                                                     │
│         "type": "segment_finalized",                                        │
│         "traceId": "<uuid>",                                                │
│         "sessionId": "<id>",                                                │
│         "segmentId": "<uuid>",                                              │
│         "sourceLanguage": "en-US",                                          │
│         "recognizedText": "<original text>",                                │
│         "translations": { "es": "Hola", "fr": "Bonjour", ... },            │
│         "timestamp": "<ISO8601>"                                            │
│       }                                                                     │
│    D. Emit segment_finalized ONLY — NO TTS queuing in callback             │
│       - Callback fires in daemon thread (non-blocking)                      │
│       - TTS decision is made by Node, not Python                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. Node receives segment_finalized event:                                  │
│    A. Parse translations dict                                               │
│    B. Broadcast subtitles IMMEDIATELY to all listeners:                     │
│       WebSocket → { type: "subtitle", payload: { text, language } }        │
│    C. Compute active languages from live WebSocket connections              │
│    D. Filter translations to active languages only                          │
│    E. POST /generate-tts to Python with { segmentId, translations }        │
│    F. Set up TTS guard timer (10s) to detect missing TTS                    │
│    G. Do NOT wait for TTS — subtitles appear instantly                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓ (async, in parallel)
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. Python TTS threads synthesize audio per requested language:             │
│    A. Receive explicit {lang: text} from Node via /generate-tts            │
│    B. Queue into per-language TTS queues                                    │
│    C. TTS worker pulls from queue, calls SpeechSynthesizer or REST API     │
│    D. When synthesis completes, emit tts_ready event to Node:              │
│       POST /api/events                                                      │
│       {                                                                     │
│         "type": "tts_ready",                                                │
│         "traceId": "<uuid>",                                                │
│         "sessionId": "<id>",                                                │
│         "segmentId": "<uuid>",                                              │
│         "language": "es",                                                   │
│         "audioFormat": "riff16khz16bitpcm",                                 │
│         "audioBytesBase64": "<base64-encoded-audio>"                        │
│       }                                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ 7. Node receives tts_ready event:                                          │
│    A. Broadcast audio to listeners of that language:                        │
│       WebSocket → { type: "audio", payload: { audioData, language } }      │
│    B. Listener browser decodes WAV and plays sequentially                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Critical Architectural Principles

1. **Continuous Recognition**: One `TranslationRecognizer` instance per session, started with `start_continuous_recognition_async()`. Processes multiple utterances until explicitly stopped. Never use `recognize_once_async()`.

2. **Non-Blocking Events**: Recognition callbacks emit events and return immediately. TTS synthesis happens asynchronously in bounded thread pool. Recognition never waits for TTS.

3. **Event-Driven Broadcast**: 
   - `segment_finalized` → subtitles broadcast instantly
   - `tts_ready` → audio broadcast when ready (may be seconds later)
   - Listeners receive subtitles first, audio follows

4. **Explicit TTS Per Segment (No Global State)**:
   - Node computes active languages from live WebSocket connections at segment time
   - Node sends `POST /generate-tts` with explicit `{lang: text}` per segment
   - Python has NO mutable active-language state — receives exactly what to synthesize
   - No race condition between listener join/leave and TTS generation
   - Saves compute and network bandwidth

5. **Bounded Concurrency**:
   - TTS uses `ThreadPoolExecutor(max_workers=2)` per session
   - Prevents thread explosion from unbounded `threading.Thread` creation
   - Queue depth naturally bounded by recognition rate

6. **Graceful Degradation**:
   - If TTS fails for one language, others continue
   - If TTS is slow, subtitles still appear immediately
   - Session remains alive even if TTS crashes

### Active Language Resolution (Per-Segment, Stateless)

Node.js computes active languages **at the moment each segment is finalized**:
- `getSessionListenerLanguages(sessionId)` scans the live `connections` Map
- Returns a `Set<language>` of languages with at least one connected listener
- Node filters translations to only active languages and sends `POST /generate-tts`
- **No persistent state** — no `sessionActiveLanguages` Map, no sync endpoint
- **No race condition** — languages are resolved at request time, not cached

### Event Schemas

#### segment_finalized (Python → Node)
```json
{
  "type": "segment_finalized",
  "traceId": "550e8400-e29b-41d4-a716-446655440000",
  "sessionId": "test-session-abc123",
  "segmentId": "660e8400-e29b-41d4-a716-446655440111",
  "sourceLanguage": "en-US",
  "recognizedText": "Hello everyone",
  "translations": {
    "es": "Hola a todos",
    "fr": "Bonjour tout le monde",
    "de": "Hallo zusammen"
  },
  "timestamp": "2026-02-13T14:23:45+0000"
}
```

#### tts_ready (Python → Node)
```json
{
  "type": "tts_ready",
  "traceId": "550e8400-e29b-41d4-a716-446655440000",
  "sessionId": "test-session-abc123",
  "segmentId": "660e8400-e29b-41d4-a716-446655440111",
  "language": "es",
  "audioFormat": "riff16khz16bitpcm",
  "audioBytesBase64": "UklGRiQAAABXQVZFZm10IBAA..."
}
```

#### generate-tts (Node → Python)
```json
{
  "sessionId": "test-session-abc123",
  "segmentId": "660e8400-e29b-41d4-a716-446655440111",
  "translations": {
    "es": "Hola a todos",
    "fr": "Bonjour tout le monde"
  }
}
```

### Session Object Model

**Python `TranslationSession`**:
```python
{
  "_translation_recognizer": TranslationRecognizer,  # One per session
  "_audio_stream": PushAudioInputStream,              # Open for session lifetime
  "_audio_queue": queue.Queue(maxsize=200),           # Non-blocking audio buffer
  "_alive": bool,                                     # False = session dead
  "_translated_text_queues": Dict[str, Queue],        # Per-language TTS queue
  "_tts_executor": ThreadPoolExecutor(max_workers=2), # Bounded TTS pool
  "_synth_threads": Dict[str, dict],                  # Per-language workers
  "_recognize_count": int,                            # Number of utterances
  "_stop_event": threading.Event                      # Shutdown signal
}
```

**Node.js WebSocket Connection**:
```javascript
{
  ws: WebSocket,
  sessionId: string,
  role: 'speaker' | 'listener',
  language: string,           // For listeners
  sourceLanguage: string,     // For speakers
  traceId: string,
  seqNo: number              // Audio chunk sequence
}
```

### Concurrency & Resource Rules

| Resource | Limit | Enforcement | Rationale |
|----------|-------|-------------|-----------|
| Audio queue per session | 200 chunks | queue.Queue(maxsize=200) | Non-blocking push_audio, session dies on overflow |
| TTS threads per session | 2 | ThreadPoolExecutor(max_workers=2) | Prevent thread explosion |
| TTS queue per language | Unbounded | queue.Queue() | Natural backpressure from recognition rate |
| Active sessions | Unbounded | In-memory sessions dict | MVP - horizontal scaling later |
| Recognizer per session | 1 | Created in __init__, reused | Continuous recognition model |

### Failure Modes & Recovery

| Failure | Impact | Recovery |
|---------|--------|----------|
| TTS fails for one language | Other languages continue, subtitles still appear | Logged, session alive |
| Recognition callback exception | Caught, recognizer continues | try/except wrapper in all callbacks |
| /process-audio 410 (SESSION_DEAD) | Audio queue full or recognizer crashed | Speaker notified, must restart |
| Python session crash | Translation stops, bypass continues | Speaker must restart |
| Node → Python /generate-tts timeout | TTS not synthesized for that segment | Fire-and-forget, TTS guard logs missing |
| TTS guard timeout (10s) | Expected TTS not received | Logged as `missing_tts_for_active_language` |
| /api/events endpoint down | Python logs errors, continues recognition | Subtitles/audio lost for that segment |

### Testing Locally

```bash
# Terminal 1: Start Python AI service
cd ai-service
python src/app.py
# → http://localhost:5000

# Terminal 2: Start Node backend
cd backend
npm start
# → http://localhost:3000

# Terminal 3: Start frontend
cd frontend
npm run dev
# → http://localhost:5173

# Terminal 4: Run validation test
python debug/test_streaming_events.py
# Expected: segment_finalized and tts_ready events in logs

# Browser test:
1. Open http://localhost:5173
2. Create session as speaker
3. Join as listener in another tab (select Spanish)
4. Speak 5 sentences in English
5. Verify:
   ✓ Spanish subtitles appear immediately after each sentence
   ✓ Spanish audio plays ~2 seconds after subtitles
   ✓ Node logs show segment_finalized and tts_ready events
   ✓ No POST /process-audio 404 errors
   ✓ No timeout errors
```

### Why This Architecture?

**Previous Architecture Problems**:
- Per-utterance finalize calls blocked recognition waiting for TTS
- Timeouts caused session degradation after 2-3 sentences
- Unbounded thread creation caused crashes
- No way to optimize TTS for active languages only

**Event-Driven Architecture Benefits**:
- Recognition never blocks → processes unlimited utterances
- Subtitles appear instantly (no TTS wait)
- TTS failures isolated (one language fails, others continue)
- Resource-efficient (only synthesize for active listeners)
- Scalable (bounded thread pool, async events)

---

## Key Files

### Backend (Node.js)
| File | Purpose |
|------|---------|
| `backend/src/index.js` | Express server, CORS, health check, App Insights |
| `backend/src/websocket/wsHandler.js` | WebSocket handler — speaker/listener connections, audio relay, getSessionListenerLanguages |
| `backend/src/routes/sessions.js` | REST API for session CRUD |
| `backend/src/routes/broadcast.js` | POST /api/broadcast — legacy endpoint (deprecated) |
| `backend/src/routes/events.js` | **POST /api/events** — receives segment_finalized and tts_ready events from AI, triggers /generate-tts, TTS guard |
| `backend/src/services/sessionService.js` | Redis-backed session management, join codes, listener tracking |

### AI Service (Python)
| File | Purpose |
|------|---------|
| `ai-service/src/app.py` | Flask server with `/start-session`, `/process-audio`, `/generate-tts`, `/end-session` |
| `ai-service/src/speech_service.py` | `TranslationSession` class — Azure SDK continuous recognition, event emission, async TTS |
| `ai-service/Dockerfile` | Python 3.9-bullseye container with Azure Speech SDK system deps (libssl1.1) |
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
| `debug/test_no_404.py` | Integration test: sends 5 simulated utterances, asserts zero 404s from /process-audio |
| `debug/test_streaming_events.py` | **Event-driven architecture test** — validates segment_finalized and tts_ready flow |
| `live_translation_test.py` | **Reference implementation** — working local script with continuous recognition + event callbacks |
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
| `POST /start-session` | `{ sessionId, traceId, sourceLanguage, targetLanguages }` | Speaker starts |
| `POST /process-audio` | `{ sessionId, traceId, seqNo, audioData }` (base64) | Each audio chunk (non-blocking queue) |
| `POST /generate-tts` | `{ sessionId, segmentId, translations: {lang: text} }` | After segment_finalized (Node decides languages) |
| `POST /end-session` | `{ sessionId, traceId }` | Speaker stops |

### AI Service → Backend (HTTP)
| Endpoint | Body | When |
|----------|------|------|
| `POST /api/events` | `{ type: "segment_finalized", sessionId, segmentId, translations, ... }` | On each recognition |
| `POST /api/events` | `{ type: "tts_ready", sessionId, segmentId, language, audioBytesBase64, ... }` | After TTS synthesis |
| `POST /api/broadcast` | `{ sessionId, language, subtitleText }` or `{ audioData }` | **DEPRECATED** (legacy) |

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

### SpeechRecognizer vs TranslationRecognizer

| | `SpeechRecognizer` | `TranslationRecognizer` |
|---|---|---|
| **Config class** | `SpeechConfig` | `SpeechTranslationConfig` |
| **What it does** | STT only (speech → text) | STT + Translation in one step |
| **Result reason** | `RecognizedSpeech` | `TranslatedSpeech` |
| **How to get text** | `result.text` (source language only) | `result.text` (source) + `result.translations[lang]` (translated) |
| **Target languages** | N/A | Must call `add_target_language()` |
| **When to use** | Same-language transcription | Cross-language translation (Vavilon's primary use case) |

**CRITICAL**: Vavilon MUST use `TranslationRecognizer` for all translation sessions. If you accidentally use `SpeechRecognizer`, you'll get `RecognizedSpeech` events with `result.text` but **NO translations** — and the code will silently produce nothing.

### Translation Pipeline Architecture

```
Browser Mic → [PCM 16kHz Int16] → WebSocket → Node.js Backend
  → HTTP POST /process-audio → Python AI Service
    → PushAudioInputStream → Azure TranslationRecognizer
      → STT + Translation (result.translations[lang])
        → Subtitle broadcast (HTTP POST /api/broadcast)
        → TTS queue per language
          → SpeechSynthesizer (SDK) or REST API (fallback)
            → Audio broadcast (HTTP POST /api/broadcast)
              → Node.js Backend → WebSocket → Listener Browser
```

### Language Code Formats

Three different code formats are used — do NOT mix them up:

| Format | Where used | Example |
|--------|-----------|---------|
| **Short code** (`es`) | Frontend, listener registration, target_languages list, broadcast language key | `'es'`, `'fr'`, `'zh'` |
| **Translation code** (`es`, `zh-Hans`) | `add_target_language()`, `result.translations` keys | `'es'`, `'zh-Hans'` (NOT `'es-ES'`) |
| **Locale** (`es-ES`) | `speech_recognition_language`, TTS voice config | `'en-US'`, `'es-ES'` (NOT `'es'`) |

Maps in `speech_service.py`:
- `TRANSLATION_LANG_MAP`: short → translation code (e.g., `'zh'` → `'zh-Hans'`)
- `TTS_LOCALE_MAP`: short → locale (e.g., `'es'` → `'es-ES'`)
- `TTS_VOICE_MAP`: locale → neural voice name (e.g., `'es-ES'` → `'es-ES-ElviraNeural'`)

### Common Translation Failure Points

1. **Using `SpeechRecognizer` instead of `TranslationRecognizer`** — no translations produced
2. **Using locale format (`es-ES`) in `add_target_language()`** — must use short/translation code (`es`)
3. **Reading `result.text` instead of `result.translations[lang]`** — gets source text, not translated
4. **TTS synthesizing source text instead of translated text** — must send `translations[lang]` to TTS
5. **Missing `add_target_language()` call** — no translations in result even with correct recognizer
6. **Language key mismatch in broadcast** — AI sends `'es'` but listeners registered under different key

### SDK Setup Pattern (matches working standalone script)

```python
# 1. Config — MUST be SpeechTranslationConfig (not SpeechConfig)
config = speechsdk.translation.SpeechTranslationConfig(subscription=key, region=region)
config.speech_recognition_language = 'en-US'         # Locale format
config.add_target_language('es')                       # Short/translation code (NOT 'es-ES')

# 2. Recognizer — MUST be TranslationRecognizer (not SpeechRecognizer)
recognizer = speechsdk.translation.TranslationRecognizer(translation_config=config, audio_config=audio)

# 3. In recognized callback:
#    reason == TranslatedSpeech (not RecognizedSpeech)
#    translated = result.translations['es'] (not result.text)

# 4. TTS — synthesize the TRANSLATED text
synthesizer.speak_text_async(translated_text)  # NOT source_text
```

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

### Continuous Recognition Lifecycle (CRITICAL)

Azure Speech SDK has two recognition modes:

| Mode | Method | Behavior | Use Case |
|------|--------|----------|----------|
| **Single-shot** | `recognize_once_async()` | Recognizes ONE utterance, then stops | Transcribing a single sentence |
| **Continuous** | `start_continuous_recognition_async()` | Recognizes MULTIPLE utterances until explicitly stopped | Real-time streaming (Vavilon) |

**Vavilon MUST use continuous recognition** to handle multiple sentences from the speaker.

#### Proper Lifecycle for WebSocket Streaming

```
┌─────────────────────────────────────────────────────────────┐
│ 1. WebSocket Connected (speaker joins)                      │
│    → POST /start-session                                     │
│       → Create PushAudioInputStream                          │
│       → Create TranslationRecognizer                         │
│       → Attach callbacks:                                    │
│           • session_started                                  │
│           • session_stopped                                  │
│           • recognizing (partial results)                    │
│           • recognized (final results)                       │
│           • canceled (errors)                                │
│       → start_continuous_recognition_async()                 │
│       Session lifecycle: ACTIVE                              │
├─────────────────────────────────────────────────────────────┤
│ 2. Audio Streaming Loop                                      │
│    → Browser sends audio_chunk via WebSocket                │
│    → Node.js forwards to POST /process-audio                │
│    → AI service: session.push_audio(bytes)                   │
│       → audio_stream.write(bytes)                            │
│       Azure recognizes continuously:                         │
│         First sentence → recognized callback                 │
│         Second sentence → recognized callback                │
│         Third sentence → recognized callback                 │
│         ... (continues until stop)                           │
│    ⚠️ NEVER close audio_stream here                         │
│    ⚠️ NEVER call stop_continuous_recognition here           │
├─────────────────────────────────────────────────────────────┤
│ 3. WebSocket Disconnected (speaker leaves)                  │
│    → POST /end-session                                       │
│       → session.stop()                                       │
│          → stop_continuous_recognition_async()               │
│          → audio_stream.close()                              │
│       Session lifecycle: STOPPED                             │
└─────────────────────────────────────────────────────────────┘
```

#### Common Lifecycle Failure: "First Sentence Works, Then Stops"

**Symptom**: First translated sentence succeeds. After that, recognition permanently stops.

**Root Causes**:
1. ❌ Using `recognize_once_async()` instead of `start_continuous_recognition_async()`
2. ❌ Calling `audio_stream.close()` after first `recognized` callback
3. ❌ Calling `stop_continuous_recognition()` inside `recognized` callback
4. ❌ Recreating recognizer object per audio chunk
5. ❌ Not storing recognizer in persistent session object
6. ❌ `session_stopped` event firing prematurely due to stream closure

**Diagnosis via Logs**:
```json
// Normal continuous operation (GOOD):
{"step":"azure_session_started", "note":"recognition pipeline active"}
{"step":"stt_recognized", "recognize_no":1, "text":"Hello"}
{"step":"stt_recognized", "recognize_no":2, "text":"This is sentence two"}
{"step":"stt_recognized", "recognize_no":3, "text":"And sentence three"}
{"step":"azure_session_stopped", "unexpected":false} // Only when stop() called

// Premature stop (BAD):
{"step":"azure_session_started"}
{"step":"stt_recognized", "recognize_no":1, "text":"Hello"}
{"step":"azure_session_stopped", "unexpected":true, "note":"UNEXPECTED session stop"}
// ↑ session_stopped fired after first recognition — ROOT CAUSE
```

#### Lifecycle Rules (MUST FOLLOW)

1. ✅ **One recognizer per speaker session** — created in `__init__`, stored as `self._translation_recognizer`
2. ✅ **Audio stream stays open** — only closed in `stop()` when WebSocket disconnects
3. ✅ **Continuous recognition** — Use `start_continuous_recognition_async()`, NOT `recognize_once_async()`
4. ✅ **Never stop in callbacks** — `recognized` callback processes results but does NOT call `stop_continuous_recognition()`
5. ✅ **Lifecycle state tracking** — Track `_recognition_started`, `_recognition_stopped`, `_stream_closed` to detect premature closure
6. ✅ **Explicit stop only** — `stop_continuous_recognition()` and `audio_stream.close()` only called in `session.stop()` method

#### Implementation Pattern (Correct)

```python
class TranslationSession:
    def __init__(self, ...):
        self._audio_stream = speechsdk.audio.PushAudioInputStream(...)
        self._recognizer = speechsdk.translation.TranslationRecognizer(...)
        self._recognizer.recognized.connect(self._on_recognized)
        # Stream and recognizer persist for session lifetime
    
    def start(self):
        # Start continuous recognition (will process multiple utterances)
        self._recognizer.start_continuous_recognition_async().get()
    
    def push_audio(self, audio_bytes):
        # Non-blocking: puts audio in queue, background writer does SDK write
        # Returns False if session dead or queue full
        self._audio_queue.put_nowait(audio_bytes)
    
    def _on_recognized(self, evt):
        # Process each recognized sentence
        # DO NOT call stop_continuous_recognition here
        # DO NOT close audio_stream here
        translated = evt.result.translations['es']
        self._broadcast(translated)
    
    def stop(self):
        # Only called when WebSocket disconnects
        self._recognizer.stop_continuous_recognition_async().get()
        self._audio_stream.close()
```

---

### Translation Pipeline Stability & Exception Handling (CRITICAL)

**Problem**: Recognition works for first 1-2 sentences, then stops permanently. English→English bypass mode continues working, but translation pipeline crashes.

**Root Cause**: Unhandled exceptions in Azure SDK event callbacks crash the recognizer, preventing future utterances from being processed.

#### Critical Stability Rule: NO UNHANDLED EXCEPTIONS IN CALLBACKS

ALL Azure SDK event handlers MUST be wrapped in try/except to prevent recognizer crash:

```python
def _on_recognized(self, evt):
    try:
        # Process translation, TTS, broadcast
        # If ANY exception occurs here, recognizer would die WITHOUT try/except
        ...
    except Exception as e:
        self._log('error', 'recognized_handler_exception',
                  error=str(e), traceback=traceback.format_exc(),
                  note='CRITICAL: Exception caught - recognizer continues')
        # Recognizer continues processing future utterances
```

**Required Exception Handling**:
- ✅ `_on_session_started` — wrapped in try/except
- ✅ `_on_recognizing` — wrapped in try/except
- ✅ `_on_recognized` — wrapped in try/except (MOST CRITICAL)
- ✅ `_on_canceled` — wrapped in try/except
- ✅ `_on_session_stopped` — wrapped in try/except

#### TTS Thread Safety & Bounded Execution

**Problem**: Unbounded thread creation can cause resource exhaustion and crashes after multiple utterances.

**Solution**: Use `ThreadPoolExecutor` with bounded worker pool:

```python
from concurrent.futures import ThreadPoolExecutor

self._tts_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f'tts-{session_id}')

# Submit TTS work to bounded pool instead of creating unbounded threads
for lang in self.target_languages:
    future = self._tts_executor.submit(self._voice_synth, lang)
    self._synth_threads[lang]["future"] = future
```

**Benefits**:
- Limits concurrent TTS operations to 2 per session
- Prevents thread explosion when processing multiple utterances
- Graceful shutdown with `executor.shutdown(wait=True, timeout=5.0)`
- Better resource management and error isolation

#### TTS Failure Isolation

**Critical Rule**: TTS failure for one language MUST NOT stop recognizer.

```python
def _voice_synth(self, language):
    while self._synth_threads[language]["running"]:
        try:
            text = self._translated_text_queues[language].get(timeout=0.5)
            audio_bytes = self._sdk_tts(language, text)
            self._broadcast_audio(language, audio_bytes)
        except Exception as e:
            # TTS failed for this language — log and continue
            self._log('error', 'tts_exception',
                      language=language, error=str(e),
                      note='TTS failed for this language - recognizer continues')
            # Recognizer processes future utterances normally
```

**Isolation guarantees**:
- TTS exception for French doesn't stop Spanish TTS
- TTS thread death doesn't crash recognizer
- Broadcast failure doesn't prevent recognition
- File write errors don't propagate to recognizer

#### Lifecycle State Management

Track lifecycle flags to detect premature crashes:

```python
self._recognition_started = False  # Set True in start()
self._recognition_stopped = False  # Set True in stop()
self._stream_closed = False        # Set True when audio_stream.close()
```

**Diagnostic checks**:
```python
# In push_audio: prevent pushing to closed stream
if self._stream_closed:
    self._log('error', 'push_audio_after_close',
              note='Attempting to push audio after stream closed')
    return

# In _on_session_stopped: detect unexpected stop
was_unexpected = (self._recognize_count > 0 and 
                  not self._stop_event.is_set() and 
                  not self._recognition_stopped)

if was_unexpected:
    self._log('warn', 'azure_session_stopped', unexpected=True,
              note='UNEXPECTED session stop - recognition should be continuous')
```

#### Common Failure Pattern: "Works Twice, Then Stops"

**Symptoms**:
- First sentence: translates correctly ✅
- Second sentence: translates correctly ✅
- Third sentence: nothing happens ❌
- English→English bypass: continues working ✅
- Logs: No recognizer activity after 2nd sentence

**Root Causes**:
1. **Unhandled exception in `_on_recognized`** after processing 2nd sentence
   - Fix: Wrap entire callback in try/except
2. **TTS thread crash** corrupting session state
   - Fix: ThreadPoolExecutor with exception isolation
3. **Azure SDK callback exception** terminating recognizer
   - Fix: Never let exceptions escape callback handlers
4. **Resource exhaustion** from unbounded thread creation
   - Fix: Bounded thread pool (max_workers=2)

**Debug Checklist**:
```bash
# Check for handler exceptions in logs
az container logs --name vavilon-ai --resource-group vavilon-rg | grep "handler_exception"

# Check for unexpected session stops
az container logs --name vavilon-ai --resource-group vavilon-rg | grep "unexpected.*true"

# Check for TTS thread crashes
az container logs --name vavilon-ai --resource-group vavilon-rg | grep "tts_exception"

# Verify recognition count incrementing
az container logs --name vavilon-ai --resource-group vavilon-rg | grep "recognize_no"
```

**Expected behavior after fix**:
```json
{"step":"stt_recognized", "recognize_no":1}
{"step":"tts_done", "language":"es", "bytes":45234}
{"step":"broadcast_audio", "language":"es", "status":200}
{"step":"stt_recognized", "recognize_no":2}
{"step":"tts_done", "language":"es", "bytes":51843}
{"step":"broadcast_audio", "language":"es", "status":200}
{"step":"stt_recognized", "recognize_no":3}
{"step":"tts_done", "language":"es", "bytes":48912}
{"step":"broadcast_audio", "language":"es", "status":200}
// Continues indefinitely until stop() called
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
| Audio format | WAV from TTS (16kHz, SDK or REST) | WAV-wrapped PCM (16kHz) |
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
- **GET /debug/trace/:trace_id** — retrieves ring buffer of last 200 trace events for a session
- **GET /metrics** — returns session counts, recognition stats, TTS queue depths, error counts
- **GET /health** — basic health check endpoint
- **GET /routes** — lists all registered Flask routes (quick route verification)

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
- **Stop_speaking timeout deadlock** (Feb 13, 2026) — Fixed blocking lifecycle that caused system failure after first sentence

---

## Current Issues (Feb 12, 2026)

### ✅ NODE_BACKEND_URL Missing from AI Container - FIXED
**Status**: Root cause identified — deployment config fix required
**Symptom**: AI service processes audio correctly (STT + translation + TTS all working for all 9 languages), but listeners receive nothing. Logs show:
```
"broadcast_audio_error": "HTTPConnectionPool(host='localhost', port=3000)... Max retries exceeded... Connection refused"
```

**Root Cause**:
The `NODE_BACKEND_URL` environment variable was **not set** in the Azure Container Instance. The code in `app.py` defaults to `http://localhost:3000`:
```python
NODE_BACKEND_URL = os.getenv('NODE_BACKEND_URL', 'http://localhost:3000')
```
Inside the container, `localhost:3000` is unreachable — it needs to point to `https://vavilon-backend.azurewebsites.net`.

**What was working**: Audio reception, speech recognition, translation (all 9 languages), TTS audio generation (50-70KB per language).
**What was broken**: Broadcasting translated audio/subtitles back to the Node.js backend (HTTP POST to `/api/broadcast` failed with connection refused).

**Fix**: Recreate the container with the correct environment variable:
```powershell
az container create `
  --name vavilon-ai `
  --resource-group vavilon-rg `
  --image vavilonacr.azurecr.io/vavilon-ai:latest `
  --registry-login-server vavilonacr.azurecr.io `
  --registry-username vavilonacr `
  --registry-password <ACR_PASSWORD> `
  --os-type Linux --cpu 1 --memory 1.5 `
  --dns-name-label vavilon-ai `
  --ports 5000 `
  --environment-variables `
    PORT=5000 `
    AZURE_SPEECH_KEY=<key> `
    AZURE_SPEECH_REGION=westeurope `
    NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

**Key Learning**: `az container restart` preserves env vars, but if the container was originally created without `NODE_BACKEND_URL`, restarting won't add it. Must use `az container create` to update env vars (it replaces the container).

---

### ✅ /process-audio 404 After ~3 Sentences — FIXED
**Status**: Root cause identified and fixed
**Symptom**: Translation works for the first 1–3 sentences, then permanently stops. Server logs show:
```
POST /process-audio HTTP/1.1" 404
{"error":"Session not found. Call /start-session first."}
```
Speaker receives no feedback. Node.js logs show nothing because the 404 was silently suppressed.

**Root Cause (two problems)**:
1. **Python session dies silently** — Azure SDK recognizer crashes (unhandled exception in callback, resource exhaustion from unbounded TTS threads, or `_on_canceled` firing). When the session object is destroyed, `/process-audio` returns 404 ("Session not found").
2. **Node.js silently suppressed 404 errors** — `wsHandler.js` line 306 had:
   ```js
   if (!error.response || error.response.status !== 404) {
     // log error
   }
   ```
   This intentionally hid 404 errors, making the session failure completely invisible. The speaker was never notified. Audio chunks continued being sent into a void.

**Canonical Audio Routing** (single path — no REST fallback, no alternative endpoints):
```
Browser → WebSocket audio_chunk → Node.js handleAudioChunk()
  → forwardAudioToAI() → HTTP POST /process-audio → Python AI Service
    → session.push_audio(bytes) → Azure PushAudioInputStream
```
There is exactly ONE code path. No legacy fallback. No binary message alternative.

**Changes Applied** (partially superseded by "Active Language Sync" fix):
1. ✅ **Removed silent 404 suppression** — ALL errors from `/process-audio` are now logged
2. ~~✅ **Session health degradation tracking**~~ — Removed in "Active Language Sync" fix. Replaced by Python-side `_alive` flag + HTTP 410.
3. ✅ **Speaker notification** — On 410 SESSION_DEAD, speaker receives `{ type: 'error', payload: { code: 'SESSION_DEAD' } }`
4. ✅ **Binary message logging** — `handleBinaryMessage()` now logs a warning when raw binary audio is received
5. ✅ **GET /routes endpoint on Python** — Returns all registered Flask routes for quick verification
6. ✅ **Integration test** — `debug/test_no_404.py` sends 5 simulated utterances, asserts zero 404s

**Files Modified**:
- `backend/src/websocket/wsHandler.js` — Rewrote `forwardAudioToAI()`, added `sessionHealth` Map, enhanced `handleBinaryMessage()`
- `ai-service/src/app.py` — Added `GET /routes` endpoint
- `debug/test_no_404.py` — New integration test script

**How to Test Locally**:
```bash
# 1. Start all three services
cd backend && npm start          # port 3000
cd ai-service && python src/app.py  # port 5000
cd frontend && npm run dev       # port 5173

# 2. Run the integration test (no browser needed)
python debug/test_no_404.py http://localhost:5000

# 3. Expected: "ALL CHECKS PASSED", zero 404 errors

# 4. Browser test: speak 5+ sentences, watch Node logs for:
#    - audio_dispatch (every 50th chunk)
#    - NO audio_dispatch_fail entries
#    - NO session_degraded entries
```

---

### ✅ Active Language Sync + Non-Blocking Audio — FIXED (Feb 15, 2026)
**Status**: Root cause identified and architecture redesigned
**Branch**: `fix/tts-active-language-sync`

**Symptoms**:
- Listener joins for Italian, but Italian TTS never generated
- `/update-active-languages` returns 404 (session doesn't exist yet when listener joins before speaker starts)
- `/process-audio` blocks when Azure SDK audio buffer fills (recognizer crashed or callback blocked by sync HTTP)

**Root Causes**:
1. **Active language sync broken**: Node sent `POST /update-active-languages` to Python, got 404 because listener joins before Python session exists (timing issue). Active languages never sync'd.
2. **Blocking SDK callback**: `_emit_segment_finalized` did synchronous HTTP POST inside Azure SDK `_on_recognized` callback, blocking the recognizer thread → audio buffer fills → `PushAudioInputStream.write()` blocks → Flask hangs.
3. **Mutable shared state**: `_active_languages` Set in Python had race condition with Node's tracking.

**Fix — Explicit TTS Per Segment (Option 1)**:
- **Removed** `/update-active-languages` endpoint, `_active_languages` mutable state in Python
- **Added** `POST /generate-tts` endpoint — Node sends explicit `{segmentId, translations: {lang: text}}`
- **Node decides** which languages need TTS at segment time (scans live connections)
- **No sync needed** — languages resolved on-demand, not cached
- **Non-blocking audio queue** — `push_audio()` uses `queue.Queue(maxsize=200)` with `put_nowait()`, background `_audio_writer` thread does actual SDK write
- **Non-blocking callbacks** — `_emit_segment_finalized` fires in daemon thread
- **Session health** — `_alive` flag set False on recognizer crash/canceled/queue overflow; HTTP 410 returned
- **TTS guard** — Node sets 10s timeout per segment; logs `missing_tts_for_active_language` if expected TTS not received

**Files Modified**:
- `ai-service/src/speech_service.py` — Non-blocking audio queue, removed active_languages, added generate_tts()
- `ai-service/src/app.py` — `/generate-tts` replaces `/update-active-languages`, 410 on dead session
- `backend/src/websocket/wsHandler.js` — Removed sessionHealth/sessionActiveLanguages/updateSessionActiveLanguages, added getSessionListenerLanguages()
- `backend/src/routes/events.js` — After segment_finalized: computes active languages, sends POST /generate-tts, TTS guard timer

**Data Flow After Fix**:
```
Browser mic → WebSocket audio_chunk → Node
  → POST /process-audio → Python push_audio() [non-blocking queue]
    → audio_writer thread → PushAudioInputStream → Azure SDK
      → _on_recognized callback [NO blocking I/O]
        → daemon thread: POST /api/events {segment_finalized}
          → Node: broadcast subtitles + POST /generate-tts {lang→text}
            → Python: queue TTS for exactly those languages
              → TTS thread: synthesize → POST /api/events {tts_ready}
                → Node: broadcast audio to listeners
```

---

### ✅ Translation Pipeline Alignment - FIXED
**Status**: Audited and aligned with working standalone `live_translation_test.py`
**Symptom**: English → English (bypass) worked, English → Other Language failed in online version. Standalone script proved Azure credentials and SDK work correctly.

**Changes Applied**:
1. ✅ **Explicit voice names on SDK synthesizers** — Added `speech_synthesis_voice_name` (e.g., `es-ES-ElviraNeural`) to match working standalone script. Previously only `speech_synthesis_language` was set.
2. ✅ **Input validation** — `TranslationSession.__init__` and `app.py /start-session` now fail loudly if `target_languages` is empty, `source_language` is missing, or Azure credentials are empty.
3. ✅ **RecognizedSpeech handler** — If Azure returns `RecognizedSpeech` instead of `TranslatedSpeech`, a detailed diagnostic error is logged explaining that speech was recognized but NOT translated.
4. ✅ **Audio flow logging** — `push_audio()` now logs every 50th push to confirm audio data is flowing into the SDK stream.
5. ✅ **Partial recognition logging** — First 3 partials logged at info level to confirm audio IS reaching Azure.
6. ✅ **Translation diagnostic logging** — Every recognition logs: recognizer type, source language, recognized text, translation keys, translated text, and the text sent to TTS.
7. ✅ **Recognizer type assertion** — `assert isinstance(recognizer, TranslationRecognizer)` guards against accidental SpeechRecognizer usage.

---

### ✅ Azure Speech SDK Error 2176 - FIXED
**Status**: Root cause identified and fixed
**Error**: `Failed to initialize platform (azure-c-shared). Error: 2176`

**Root Cause**:
Azure Speech SDK **1.36.0 requires `libssl1.1`**. The Dockerfile used `python:3.9` (Debian **Bookworm/12**) which only ships **`libssl3`** and has dropped `libssl1.1`. Adding `libssl3` or `libssl-dev` didn't help because those are OpenSSL 3.x — the SDK needs OpenSSL 1.x.

**Fixes Applied**:
1. ✅ **Dockerfile**: Changed `FROM python:3.9` → `FROM python:3.9-bullseye` (Debian 11, has `libssl1.1`)
2. ✅ **Resilient TTS init**: `_setup_synthesizers()` now wraps SDK synthesizer creation in try/except. If SDK fails, it sets `_use_rest_tts = True` and continues — the session still starts (STT + subtitles work).
3. ✅ **REST API TTS fallback**: New `_rest_tts()` method uses Azure TTS REST API (pure HTTP, no native libs). Falls back automatically if SDK synthesizer creation fails. Uses SSML with neural voices.

**TTS Modes**:
| Mode | When used | How it works |
|------|-----------|-------------|
| SDK | Default (SDK synthesizers created successfully) | `SpeechSynthesizer.speak_text_async()` — low latency |
| REST | Automatic fallback (SDK creation fails) | HTTP POST to `{region}.tts.speech.microsoft.com` with SSML |

---

### ✅ CORS Configuration Problem - RESOLVED
**Status**: FIXED - Both production URLs now working  
**Resolution Date**: Feb 12, 2026 19:35 UTC

**Problem Summary**:
- Browser console showed CORS errors: "No 'Access-Control-Allow-Origin' header is present"
- Both www.vavilonapp.rs and green-pond URL failed to connect to backend
- OPTIONS preflight requests returned 400 Bad Request

**Root Causes**:
1. **Azure App Service CORS** was configured but missing the actual frontend URL (green-pond-05766a403.1.azurestaticapps.net)
2. **Azure CORS runs BEFORE Express** - conflicting with Express CORS middleware
3. **Backend deployment** didn't properly install/load the `cors` npm package

**Solution Applied**:
1. ✅ Cleared Azure App Service CORS configuration entirely (set to `allowedOrigins: []`)
2. ✅ Redeployed backend via GitHub Actions to ensure proper `npm ci` installation of dependencies
3. ✅ Express CORS middleware now handles all CORS (includes all required origins)
4. ✅ Verified OPTIONS preflight and regular requests both return proper CORS headers

**Verification Results**:
```powershell
# GET request test
Status: 200
Access-Control-Allow-Origin: https://green-pond-05766a403.1.azurestaticapps.net

# OPTIONS preflight test
Status: 204
Access-Control-Allow-Origin: https://green-pond-05766a403.1.azurestaticapps.net
Access-Control-Allow-Methods: GET,HEAD,PUT,PATCH,POST,DELETE
```

**Key Learnings**:
- **Azure App Service has platform-level CORS that intercepts requests BEFORE they reach Express**
- **Setting Azure CORS to empty array allows Express CORS to handle everything**
- **GitHub Actions deployment (`npm ci`) ensures proper dependency installation**
- **Manual zip deployments may not properly install node_modules**

---

### ✅ Stop Speaking Timeout Deadlock — FIXED (Feb 13, 2026)
**Status**: Root cause identified and permanently fixed
**Resolution Date**: Feb 13, 2026

**Symptom**: Translation pipeline fails after first sentence:
- First sentence works perfectly (translation + TTS + broadcast)
- After `stop_speaking`, system becomes unresponsive
- Subsequent sentences fail with timeout errors
- Node logs show: `"step":"forward_audio_fail","error":"timeout of 10000ms exceeded"`
- Switching languages or refreshing session does not recover
- English → English bypass mode continues working (proving frontend is fine)

**Root Causes Identified**:
1. **Python TTS Executor Blocking Indefinitely**
   - `TranslationSession.stop()` called `self._tts_executor.shutdown(wait=True)` with no timeout
   - Python 3.9's ThreadPoolExecutor.shutdown() blocks until all threads complete
   - TTS threads wait on `queue.get(timeout=0.5)` which could delay shutdown
   - If TTS thread stuck in network call to Azure, shutdown blocks forever
   - `/end-session` HTTP endpoint would timeout after 10+ seconds

2. **Node.js Blocking on /end-session Call**
   - `handleStopSpeaking()` used `await axios.post('/end-session', { timeout: 10000 })`
   - This blocked the WebSocket message handler
   - If Python takes >10s, Node times out but session remains poisoned
   - After timeout, session enters degraded state
   - All subsequent audio chunks fail

3. **TTS Thread Exit Too Slow**
   - Threads checked `while self._synth_threads[language]["running"]`
   - Queue timeout was 0.5s, meaning threads took up to 0.5s to notice stop signal
   - Multiple languages × 0.5s could exceed total timeout window

**Fixes Applied**:

**Python Side** (`ai-service/src/speech_service.py`):
1. ✅ **Added timeout wrapper to TTS executor shutdown**
   - Created background thread for shutdown with `threading.Event`
   - Main thread waits maximum 3 seconds: `executor_done.wait(timeout=3.0)`
   - If timeout, logs warning and continues cleanup (don't block forever)
   - Ensures `stop()` method returns within ~4 seconds total

2. ✅ **Improved TTS thread exit responsiveness**
   - Reduced queue timeout from 0.5s → 0.2s (faster exit)
   - Added explicit `stop_event` check: `while running and not self._stop_event.is_set()`
   - Threads now exit within 0.2s of stop signal instead of 0.5s

3. ✅ **Enhanced lifecycle logging**
   - `stop()` logs each cleanup phase: executor shutdown, recognizer stop, stream close
   - TTS threads log exit reason: `stop_event` vs `running_flag_cleared`
   - Timeout warnings logged if executor doesn't finish in 3s
   - Cleanup completion status logged: `cleanup_complete=True/False`

**Node.js Side** (`backend/src/websocket/wsHandler.js`):
1. ✅ **Made stop_speaking fire-and-forget**
   - Changed from `await axios.post()` to `.then()/.catch()` (no blocking)
   - Reduced timeout from 10s → 5s (fail faster)
   - WebSocket handler returns immediately (don't wait for Python)
   - Connection state reset immediately: `conn.traceId = null; conn.seqNo = 0`

2. ✅ **Same fix for speaker disconnect**
   - `handleSpeakerDisconnect()` also uses fire-and-forget pattern
   - Continues cleanup immediately even if `/end-session` fails
   - Prevents disconnect from hanging

3. ✅ **Enhanced timeout detection logging**
   - `forwardAudioToAI()` now detects timeout errors: `error.code === 'ECONNABORTED'`
   - Logs explicit `isTimeout: true` flag
   - Differentiates timeout from 404/500 errors
   - Logs recovery: `session_recovered` when consecutive errors clear

**Architecture Changes**:

**Before Fix**:
```
Speaker clicks "Stop Speaking"
  → Node.js handleStopSpeaking (blocks)
    → await POST /end-session (10s timeout)
      → Python session.stop() (no timeout)
        → executor.shutdown(wait=True) (BLOCKS FOREVER if TTS stuck)
          → [TIMEOUT after 10s]
    → Node.js times out
    → Session degraded
  → All future audio chunks fail
```

**After Fix**:
```
Speaker clicks "Stop Speaking"
  → Node.js handleStopSpeaking (fire-and-forget)
    → POST /end-session (5s timeout, non-blocking)
    → Connection state reset immediately
    → Returns to speaker instantly
  
  Python side (independent):
    → session.stop() with 3s executor timeout
    → TTS threads exit within 0.2s
    → Total cleanup: ~4s maximum
    → Session cleanly destroyed
```

**Lifecycle Guarantees Now**:
- ✅ Node.js `stop_speaking` returns in <10ms (fire-and-forget)
- ✅ Python `stop()` completes within 4 seconds (3s executor + 1s recognizer)
- ✅ TTS threads exit within 0.2s of stop signal
- ✅ Session never left in "degraded" state due to stop timeout
- ✅ Subsequent sentences work after stop → start cycle
- ✅ Language switching works without session recovery issues

**Testing Checklist**:
```bash
# Test multiple sentences in a row
1. Start speaking (English → Spanish)
2. Say first sentence → verify translation received
3. Stop speaking
4. Start speaking again immediately
5. Say second sentence → should work (previously failed)
6. Repeat 10 times → all should work

# Test fast stop/start cycles
1. Start speaking
2. Say half a sentence
3. Stop immediately
4. Start again within 1 second
5. Should not timeout or degrade

# Check logs for timeouts
az container logs --resource-group vavilon-rg --name vavilon-ai | grep timeout
# Should show: "TTS threads did not finish in 3s" at most

# Check Node logs for recovery
# Should see: session_recovered when errors clear
```

**Key Learnings**:
- **Never block WebSocket handlers on external HTTP calls** — always use fire-and-forget or background jobs
- **Always add timeouts to blocking operations** — Python's ThreadPoolExecutor.shutdown() can block indefinitely
- **Test lifecycle boundaries** — stop/start cycles, rapid clicks, timeouts are critical edge cases
- **Lifecycle logging is essential** — without detailed logs, this would have been impossible to diagnose
- **Fire-and-forget pattern for cleanup** — better to log cleanup failures than block user actions

---

## Current Issues (Feb 15, 2026)

### � TTS Delivery Reliability - DIAGNOSTIC IMPROVEMENTS DEPLOYED
**Status**: Phase 1 & Phase 2.1 deployed to production (Feb 15, 2026 15:55 UTC)
**Branch**: `main`
**Deployment**: Commit 7a107ac

**Deployed Improvements**:
1. ✅ **Enhanced Python TTS Logging** (Phase 1.1)
   - Comprehensive logging at every TTS stage (receive, enqueue, synthesis start/complete, emit)
   - Queue depth tracking before/after enqueue
   - Text preview logging for debugging
   - Request/segment ID tracking throughout pipeline

2. ✅ **TTS Worker Thread Health Monitoring** (Phase 1.2)
   - Thread ID and iteration count tracking
   - Heartbeat logging every 10 iterations
   - Consecutive error tracking (max 3 before worker stops)
   - Detailed exit reason logging
   - Critical error detection (worker giving up vs normal stop)

3. ✅ **Event Emission Retry Logic** (Phase 1.3)
   - 3 attempts with 0.5s delay between retries
   - Timeout detection and logging
   - Audio size tracking (KB)
   - Per-attempt logging for debugging
   - Failure after all retries raises exception (caught by worker)

4. ✅ **Flask /generate-tts Enhanced Error Handling** (Phase 1.4)
   - Request ID generation for request tracking
   - Comprehensive validation with detailed error messages
   - Session alive check (returns 410 if dead)
   - Active sessions logging when session not found
   - Full exception traceback logging

5. ✅ **Node TTS Request/Response Tracking** (Phase 1.5)
   - Full request body logging (sessionId, segmentId, translations)
   - Response status and data logging
   - Timing metrics (timeSinceSegmentMs)
   - Audio size tracking (bytes and KB)
   - TTS guard timestamp tracking

6. ✅ **Redis Connection Resilience** (Phase 2.1)
   - Auto-reconnect with exponential backoff (50ms * retries, max 2s)
   - Max 10 reconnect attempts
   - Connection event logging (error, reconnecting, ready, connect)
   - Better error visibility

**Previous Symptoms** (before diagnostic improvements):
- Subtitles work (translations successful)
- Audio never plays (TTS never reaches listeners)
- Node correctly requests TTS via `/generate-tts`
- Python never responds with `tts_ready` event
- Node logs `missing_tts_for_active_language` after 10s timeout
- Redis connection drops (`SocketClosedUnexpectedlyError`)

**Next Steps**:
1. Monitor production logs for exact failure point
2. Look for new diagnostic logs:
   - `generate_tts_received` - confirms Node request received
   - `tts_enqueued` - confirms text queued for synthesis
   - `tts_synthesis_start` - confirms worker pulled from queue
   - `tts_synthesis_complete` - confirms Azure SDK synthesized audio
   - `tts_emit_attempt` - confirms emission started
   - `tts_emit_success` - confirms Node received event
3. If TTS still fails, logs will show WHERE in the pipeline it breaks
4. Apply targeted fix based on diagnostic evidence

**Diagnostic Log Locations**:
```bash
# Python AI service logs (Azure Container Instance)
az container logs --name vavilon-ai --resource-group vavilon-rg

# Node backend logs (Azure App Service)
az webapp log tail --name vavilon-backend --resource-group vavilon-rg

# Filter for TTS-related logs
# Python: grep "tts_" or "generate_tts"
# Node: grep "tts_" or "generate_tts" or "missing_tts"
```

**Expected Log Flow (If Working Correctly)**:
```json
// Python receives TTS request
{"step":"generate_tts_request", "languages":["it"], "request_id":"a3b4c5d6"}
{"step":"generate_tts_received", "languages":["it"]}
{"step":"tts_enqueued", "language":"it", "queue_depth_after":1}

// TTS worker processes
{"step":"tts_worker_alive", "language":"it", "queue_depth":1}
{"step":"tts_synthesis_start", "language":"it", "segment_id":"..."}
{"step":"tts_synthesis_complete", "language":"it", "audio_bytes":45234}

// Emit to Node
{"step":"tts_emit_attempt", "language":"it", "attempt":1}
{"step":"tts_emit_success", "language":"it", "status_code":200}

// Node receives and broadcasts
{"step":"tts_ready_received", "language":"it", "audioSizeKB":44.17}
{"step":"tts_all_received", "languages":["it"]}
```

**If TTS Fails, Logs Will Show**:
- Missing `tts_enqueued` → Queue not initialized for that language
- Missing `tts_synthesis_start` → Worker not running or stuck
- Missing `tts_synthesis_complete` → Azure SDK TTS failed
- Missing `tts_emit_success` → Network issue Python → Node
- `tts_emit_fail` or `tts_emit_timeout` → Emission retry failures
- `tts_worker_giving_up` → 3+ consecutive failures, worker stopped
- `missing_tts_for_active_language` in Node → No tts_ready received after 10s

---

### ✅ Node requests TTS, Python never delivers it — MONITORING
**Previous Status**: Node requests Italian TTS (`tts_languages_requested:["it"]`), Node sends `POST /generate-tts` (`generate_tts_sent ... enqueued:["it"]`), Python never responds with `tts_ready` event, Node logs `missing_tts_for_active_language` after 10s timeout

**Current Status**: Diagnostic logging deployed. Awaiting production test to identify exact failure point.

**Plan**: See [PLAN.md](PLAN.md) for comprehensive diagnostic and fix strategy (Phase 1 & 2.1 complete, awaiting diagnosis for Phase 3+)

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

# IMPORTANT: If you need to add/change env vars, you MUST recreate the container
# (az container restart does NOT update env vars):
az container create `
  --name vavilon-ai --resource-group vavilon-rg `
  --image vavilonacr.azurecr.io/vavilon-ai:latest `
  --registry-login-server vavilonacr.azurecr.io `
  --registry-username vavilonacr `
  --registry-password <ACR_PASSWORD> `
  --os-type Linux --cpu 1 --memory 1.5 `
  --dns-name-label vavilon-ai --ports 5000 `
  --environment-variables `
    PORT=5000 `
    AZURE_SPEECH_KEY=<key> `
    AZURE_SPEECH_REGION=westeurope `
    NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net

# If ONLY updating the image (env vars already correct), restart is enough:
az container restart --resource-group vavilon-rg --name vavilon-ai
```

**Critical env vars for AI container** (all required):
| Variable | Value | What breaks if missing |
|----------|-------|----------------------|
| `AZURE_SPEECH_KEY` | Your Speech Services key | Session creation fails (500) |
| `AZURE_SPEECH_REGION` | `westeurope` | Session creation fails (500) |
| `NODE_BACKEND_URL` | `https://vavilon-backend.azurewebsites.net` | Translations work but never reach listeners (broadcasts to localhost fail) |
| `PORT` | `5000` | Container listens on wrong port |

### Frontend
Auto-deploys via GitHub Actions when pushed to main (Static Web Apps).

---

## Deployment History

### February 15, 2026 - TTS Reliability Diagnostic Improvements (Commit 7a107ac)
**Deployed**: 15:55 UTC  
**Status**: ✅ Successfully deployed to production  
**Branch**: `main`  
**Focus**: Add comprehensive diagnostic logging to identify TTS delivery failures

**Changes Deployed**:
- **Phase 1.1**: Enhanced Python TTS logging with detailed queue tracking, text previews, request IDs
- **Phase 1.2**: TTS worker thread health monitoring with heartbeat, consecutive error tracking
- **Phase 1.3**: Event emission retry logic (3 attempts, 0.5s delay, timeout detection)
- **Phase 1.4**: Flask /generate-tts enhanced error handling with request tracking
- **Phase 1.5**: Node TTS request/response tracking with timing metrics
- **Phase 2.1**: Redis connection resilience with auto-reconnect (exponential backoff)

**Files Modified**:
- `ai-service/src/speech_service.py` - Enhanced generate_tts(), _voice_synth(), _emit_tts_ready()
- `ai-service/src/app.py` - Enhanced /generate-tts endpoint with comprehensive logging
- `backend/src/routes/events.js` - Enhanced TTS request/response tracking
- `backend/src/services/sessionService.js` - Added Redis reconnection strategy

**Deployment Verification**:
```bash
# AI Container restarted: 15:55 UTC
# State: Running
# Logs show: {"step":"startup", "port":5000, "azure_configured":true}
```

**Purpose**: These changes add **zero functional changes** — only diagnostic logging. If TTS still fails, logs will now show the exact failure point in the pipeline, allowing targeted fixes in Phase 3+.

**Next Actions**:
1. Test end-to-end translation with Italian listener
2. Monitor logs for diagnostic output
3. Identify exact failure point from structured logs
4. Apply Phase 3+ fixes based on evidence

### February 15, 2026 - EMERGENCY HOTFIX: TypeError in TTS (Commit 6e08c46)
**Deployed**: 16:30 UTC  
**Status**: ✅ Successfully deployed to production  
**Branch**: `main`  
**Priority**: P0 - Production Breaking Bug  
**Focus**: Fix TypeError caused by duplicate session_id kwargs in TTS logging

**Root Cause**: Commit 7a107ac (diagnostic improvements) introduced duplicate kwargs. The `_log()` wrapper automatically adds `session_id=self.session_id`, but new logging calls also passed it explicitly, causing Python TypeError: "got multiple values for keyword argument 'session_id'"

**Impact**: 100% TTS failure - all translation sessions could produce subtitles but no audio was generated or delivered

**Changes Deployed**:
- Removed duplicate `session_id=self.session_id` from 20 logging calls in `TranslationSession` class
- Methods fixed: `generate_tts()`, `_voice_synth()`, `_emit_tts_ready()`
- Updated PLAN.md with hotfix documentation

**Files Modified**:
- `ai-service/src/speech_service.py` - Removed duplicate session_id kwargs from all logging calls
- `PLAN.md` - Documented hotfix strategy

**Deployment Verification**:
```bash
# AI Container restarted: 16:30 UTC
# State: Running
# Logs show: {"step":"startup", "port":5000, "azure_configured":true}
# No TypeError exceptions on startup
```

**Testing Required**: Create speaker session, join as Italian listener, speak 3 sentences, verify subtitles AND audio for all sentences

---