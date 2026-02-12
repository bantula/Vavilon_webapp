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
        # Stream audio continuously — do NOT close stream here
        self._audio_stream.write(audio_bytes)
    
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

## Known Issues / TODOs

1. **ScriptProcessorNode is deprecated** — works fine but browsers recommend AudioWorklet. Low priority.
2. **No authentication** — intentional for MVP. Sessions are public with a 6-char join code.
3. **In-memory AI sessions** — if the AI container restarts, active translation sessions are lost.
4. **Express body limit** set to 5MB (`express.json({ limit: '5mb' })`) to allow TTS audio payloads.
5. **After code changes, all 3 services need redeployment** to test end-to-end.
6. **Backend logs show AI service timeouts** — "timeout of 10000ms exceeded" when forwarding audio. Need to verify AI service accessibility from backend.

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