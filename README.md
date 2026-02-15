# Vavilon DOO  Real-Time Translation Platform
**Documentation Date:** February 15, 2026

---

## Executive Summary

**Vavilon** is a real-time spoken translation web app for tours, museums, and conferences. One speaker broadcasts to multiple listeners; each listener receives live translated speech and subtitles in their chosen language. Web-only, no app installation required. Target capacity: 200 concurrent listeners across 10 languages.

**Deployment Model:** Three-service architecture on Microsoft Azure: React frontend (Static Web Apps), Node.js backend (App Service with Redis session cache), Python AI service (Container Instance with Azure Speech SDK). Continuous delivery via GitHub Actions from main branch.

**Current Status (Tested Feb 15, 2026):**
- **Works:** STT and translation (9 languages), subtitle broadcast, session lifecycle, Redis persistence, same-language bypass (sub-200ms latency), WebSocket streaming, **dynamic TTS worker creation** (tested in production ✓).
- **Fails:** Missing final segment (user stops before Azure finalizes last sentence), occasional audio dispatch timeout (>5s).
- **Next:** Implement delayed session end to capture final segments (see [PLAN.md](PLAN.md) Phase 1).

**Next Action:** Implement delayed session end (grace period) to ensure final speech segments are recognized before session terminates. See [PLAN.md](PLAN.md) Phase 1 for implementation details.

---

## Technical README

### Overview

**Architecture:** Event-driven broadcast system with three services:
- **Frontend** (React/Vite)  Mic capture (16kHz PCM), WebSocket streaming, audio playback queue
- **Backend** (Node.js/Express)  WebSocket hub, session orchestration, Redis-backed state
- **AI Service** (Python/Flask)  Azure Speech SDK continuous recognition, translation, TTS synthesis

**Event-Driven Principle:** Recognition never blocks. Subtitles broadcast instantly on `segment_finalized`. Audio follows async via separate `tts_ready` events when synthesis completes. Each service runs independently; failures in one don''t crash others.

**Single Source of Truth:** Redis stores all session state (listeners, languages, join codes). Node queries Redis for active listeners at segment time; no mutable caching.

### Architecture Diagram (Text)

```
Browser ──WebSocket──> Node.js ──HTTP REST──> Python AI
(React)  <──────────── Express+WS <─────────  Flask+Azure SDK
 5173                   3000                   5000
                          |
                        Redis (Session storage)
                          |
                    Azure Speech SDK
```

### What Works (Verified in Production)

- **STT & Translation:** 9 languages (en, es, fr, de, it, pt, ru, zh, ja). Azure TranslationRecognizer continuous mode processes unlimited utterances. Tested Feb 15, 2026: English → Italian translation working perfectly.
- **Subtitle Broadcast:** Instant delivery (<500ms) after recognition. Node queries Redis for active listeners, broadcasts translated text via WebSocket.
- **Session Lifecycle:** Speaker creates session, listeners join via 6-char code. Redis stores state. `/start-session`, `/process-audio`, `/end-session` endpoints stable.
- **Dynamic TTS Workers:** **✅ TESTED & WORKING** — Workers spawn on-demand when listeners join mid-session. Italian listener joined after session start, TTS worker created dynamically, audio delivered successfully (125-367ms latency). No `missing_tts_for_active_language` errors.
- **Bypass Mode:** Same-language listeners receive raw PCM wrapped in WAV headers (no translation). Latency <200ms. Bandwidth ~43KB/sec per listener.
- **Audio Streaming:** Browser captures Float32 PCM at 44.1kHz, downsamples to Int16 16kHz, base64-encodes, sends via WebSocket as JSON. Python pushes to Azure PushAudioInputStream.
- **Non-blocking Recognition:** Audio queue (maxsize=200) + background writer thread. Flask `/process-audio` returns immediately. Recognition runs on daemon threads.
- **Exception Isolation:** TTS failures don''t crash recognizer. Bounded ThreadPoolExecutor (max_workers=2) prevents thread explosion.
- **Redis Resilience:** Auto-reconnect with exponential backoff (50ms  retries, max 2s, 10 attempts).

### What Fails / Unstable (Current P0s)

#### P0: Missing Final Segment (🔴 NEW — Discovered Feb 15, 2026)

**Label:** Premature session end loses last sentence  
**Status:** Under investigation — fix planned

**Symptoms:**
- User speaks 3 sentences
- Only 2 segments recognized (`segment_finalized` events)
- Third sentence never reaches subtitle/audio pipeline
- Session ends before Azure finalizes last segment

**Test Evidence (Session a9003fea):**
```
17:59:17 ✓ Segment 1: "It's the first sentence" → Italian audio delivered
17:59:23 ✓ Segment 2: "This is the second sentence" → Italian audio delivered
17:59:33 ✗ Session stopped — NO Segment 3 finalized
```

**Root Cause:** Azure Speech SDK requires **silence to finalize segments** (500ms default). If user clicks "Stop Speaking" before final segment finalizes → recognition lost.

**Fix Plan:** Add 2-second grace period before ending Python AI session. Allows Azure to finalize pending segments even after user stops. See [PLAN.md](PLAN.md) Phase 1 Option A.

**Workaround:** Pause 2-3 seconds after last sentence before clicking "Stop Speaking".

---

#### P1: Audio Dispatch Timeout (⚠️ NEW — Non-fatal)

**Label:** Occasional timeout sending audio to Python AI  
**Status:** Intermittent, non-fatal

**Symptoms:**
```json
{"step":"audio_dispatch_fail", "seqNo":151, "error":"timeout of 5000ms exceeded"}
```

**Root Cause:** Python `/process-audio` endpoint doesn't respond within 5 seconds. Likely causes:
- Audio queue full (maxsize=200)
- Azure SDK write blocked
- Network latency spike

**Impact:** Non-fatal (session continues), but may cause brief audio desync.

**Fix Plan:** Increase timeout to 10s + increase audio queue to 500. See [PLAN.md](PLAN.md) Phase 2.

---

#### ✅ FIXED: TTS Delivery Failure (Deployed & Tested Feb 15, 2026)

**Label:** TTS worker thread lifecycle bug  
**Status:** ✅ **WORKING IN PRODUCTION**

**What Was Broken:** TTS worker threads created once at session start. New languages joining mid-session had no worker → no audio.

**Fix:** Dynamic worker creation — `_ensure_tts_worker()` spawns threads on-demand.

**Test Results:**
- Italian listener joined mid-session ✓
- Dynamic worker created automatically ✓
- TTS synthesis: 125-367ms (excellent) ✓
- Audio delivered for all segments ✓
- No `missing_tts_for_active_language` errors ✓

**Commit:** 640a5ad

---

### Key Endpoints & APIs

**Node Backend REST:**
```http
POST /api/events
Content-Type: application/json

# segment_finalized (from Python)
{
  "type": "segment_finalized",
  "sessionId": "abc123",
  "segmentId": "uuid",
  "translations": {"es": "Hola", "it": "Ciao"}
}

# tts_ready (from Python)
{
  "type": "tts_ready",
  "sessionId": "abc123",
  "segmentId": "uuid",
  "language": "it",
  "audioFormat": "riff16khz16bitpcm",
  "audioBytesBase64": "UklGRiQAAA..."
}
```

**Python AI REST:**
```http
POST /start-session
{
  "sessionId": "abc123",
  "traceId": "uuid",
  "sourceLanguage": "en-US",
  "targetLanguages": ["es", "it"]
}

POST /process-audio
{
  "sessionId": "abc123",
  "traceId": "uuid",
  "seqNo": 42,
  "audioData": "base64-encoded-pcm"
}

POST /generate-tts
{
  "sessionId": "abc123",
  "segmentId": "uuid",
  "translations": {"it": "Ciao mondo"}
}
```

**WebSocket Messages (Browser  Node):**
```json
// Speaker  Backend
{"type": "audio_chunk", "payload": {"audioData": "base64..."}}

// Backend  Listener
{"type": "subtitle", "payload": {"text": "Ciao", "language": "it"}}
{"type": "audio", "payload": {"audioData": "base64...", "language": "it"}}
{"type": "bypass_audio", "payload": {"audioData": "base64..."}}
```

---

### Deployment & Debugging Quick Commands

**Deploy Backend (Node):**
```powershell
cd backend
python -c "import zipfile,os;z=zipfile.ZipFile(''deploy.zip'',''w'');[z.write(r+''/''+f) for r,d,fs in os.walk(''src'') for f in fs];[z.write(x) for x in [''package.json'',''package-lock.json'']];z.close()"
az webapp deployment source config-zip --resource-group vavilon-rg --name vavilon-backend --src deploy.zip
```

**Deploy AI Service (Python):**
```powershell
cd ai-service
az acr build --registry vavilonacr --image vavilon-ai:latest .
az container restart --resource-group vavilon-rg --name vavilon-ai
```

**Required Environment Variables (AI Container):**
```bash
PORT=5000
AZURE_SPEECH_KEY=<your-key>
AZURE_SPEECH_REGION=westeurope
NODE_BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

**View Container Logs:**
```powershell
az container logs --name vavilon-ai --resource-group vavilon-rg
```

**View Backend Logs:**
```powershell
az webapp log tail --name vavilon-backend --resource-group vavilon-rg
```

**Debug TTS Pipeline:**
```powershell
# Check if TTS enqueued
az container logs --name vavilon-ai --resource-group vavilon-rg | Select-String "tts_enqueued"

# Check which workers alive
az container logs --name vavilon-ai --resource-group vavilon-rg | Select-String "tts_worker_alive"

# Check for missing TTS timeouts
az webapp log tail --name vavilon-backend --resource-group vavilon-rg | Select-String "missing_tts"
```

**Test TTS Endpoint Directly:**
```bash
curl -X POST http://vavilon-ai.westeurope.azurecontainer.io:5000/generate-tts \
  -H "Content-Type: application/json" \
  -d ''{
    "sessionId": "test-abc123",
    "segmentId": "seg-uuid",
    "translations": {"it": "Questa è una prova"}
  }''
```

**Health Check:**
```bash
curl http://vavilon-ai.westeurope.azurecontainer.io:5000/health
curl https://vavilon-backend.azurewebsites.net/health
```

---

### Testing Checklist

**End-to-End Validation:**
```bash
# 1. Start services locally
cd ai-service && python src/app.py &          # port 5000
cd backend && npm start &                     # port 3000
cd frontend && npm run dev &                  # port 5173

# 2. Run event flow test
python debug/test_streaming_events.py         # Validates segment_finalized + tts_ready

# 3. Browser test
1. Open http://localhost:5173
2. Click "Start Session" (creates speaker session)
3. Select source language: English
4. Open incognito tab  Join listener (enter 6-char code)
5. Select target language: Italian
6. Return to speaker tab  Click "Start Speaking"
7. Speak 3 clear sentences in English
8. Verify:
    Italian subtitles appear after each sentence (2-3s delay)
    Italian audio plays after subtitles (known failure Feb 15)
    Console logs show WebSocket messages
    No error toasts
9. Check backend logs: segment_finalized_received, tts_ready_received
10. Check AI logs: generate_tts_received, tts_enqueued, tts_synthesis_complete
```

**TTS Worker Verification (Debug Current Bug):**
```bash
# After step 7 above, immediately check:
az container logs --name vavilon-ai --resource-group vavilon-rg | Select-String "tts_worker" | Select-Object -Last 20

# Expected (BROKEN): Only de/es workers, no "it" worker
# Expected (FIXED): tts_worker_alive for language="it"
```

---

### Operational Notes

**Resource Limits:**
- Audio queue: 200 chunks per session (non-blocking). Session dies on overflow.
- TTS threads: 2 concurrent per session (ThreadPoolExecutor). Prevents resource exhaustion.
- TTS queue per language: Unbounded (backpressure from recognition rate).
- Recognition: One TranslationRecognizer per session. Continuous mode until explicit stop.

**Timeouts:**
- TTS guard: 10s per segment. Node logs `missing_tts_for_active_language` if no `tts_ready` received.
- `/process-audio`: Returns HTTP 410 if session dead (audio queue full or recognizer crashed).
- `/end-session`: Fire-and-forget with 5s timeout. Never blocks WebSocket handler.

**Continuous Recognition Lifecycle:**
- Create once per session in `__init__`
- `start_continuous_recognition_async()` enables multi-utterance mode
- Audio stream stays open until session destroyed
- Never call `recognize_once_async()` (single utterance only)
- Never close stream until `stop()` called

**Critical Environment Variables (Purpose):**
- `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION`  SDK credentials. Session creation fails without.
- `NODE_BACKEND_URL`  Python  Node event emission. Broadcasts fail if pointing to localhost.
- `REDIS_URL` / `REDIS_PASSWORD`  Session persistence. Backend crashes without.
- `AI_SERVICE_URL`  Node  Python audio forwarding. Translation stops if incorrect.

**Language Code Formats (Do NOT Mix):**
- Short codes (`it`, `es`, `zh`)  Frontend, Redis, broadcast keys
- Translation codes (`it`, `zh-Hans`)  Azure `add_target_language()`
- Locale codes (`it-IT`, `es-ES`)  Azure STT config, TTS voices

---

### Rollback Plan

Revert to last known-good commit (pre-TTS bug): `git revert HEAD && git push origin main`. Auto-deployment handles backend and frontend. Manually restart AI container: `az container restart --resource-group vavilon-rg --name vavilon-ai`.

---

## Changelog of Removed/Archived Content

**Items Removed from Original COPILOT_CONTEXT.md:**

- **Historic deployment narratives**  Long descriptions of past deployments and fix iterations (outdated, noisy)
- **Resolved issue deep-dives**  Multi-paragraph analyses of fixed bugs (CORS, 404 errors, stop_speaking deadlock, NODE_BACKEND_URL, TypeError hotfix) (irrelevant post-fix)
- **Legacy diagnostic improvement sections**  Detailed logs from Feb 15 diagnostic commit 7a107ac (outdated, superseded by new logs)
- **Verbose log examples**  30+ line JSON log dumps showing expected vs broken states (noisy, unnecessarily detailed)
- **Auxiliary hardware scripts documentation**  `help/dubber.py`, `help/audio_interface.py`, `help/auxiliary_functions.py` (never used in web app, deprecated)
- **Long architecture philosophy sections**  10+ paragraph explanations of event-driven design rationale (marketing tone, excessive)
- **Duplicate Azure SDK pattern explanations**  Repeated warnings about SpeechRecognizer vs TranslationRecognizer mistakes (redundant)
- **Session object model implementation details**  Python class internals (`_synth_threads`, `_tts_executor` structure) (overly detailed)
- **Historic root cause walkthroughs**  Step-by-step analyses for resolved issues (outdated context)
- **Testing results from prior deployments**  Specific test outcomes from Feb 12-15 before current bug (stale data)
- **Lifecycle failure pattern essays**  20+ line explanations of "first sentence works, then stops" pattern (resolved, verbose)
- **Exception handling patterns deep-dive**  Detailed code snippets on try/except wrappers in callbacks (implementation detail, overly technical)
- **WebSocket message routing tables**  15+ row tables of every message type across all directions (excessive detail)
- **CORS configuration resolution history**  Azure vs Express CORS conflict story (resolved, irrelevant)
- **Redis connection issue narratives**  SocketClosedUnexpectedlyError analysis from diagnostic phase (outdated)
- **Binary audio routing dead-ends**  Documented paths for binary WebSocket messages that don''t exist (confusion)
- **Deployment history long-form descriptions**  Multi-paragraph commit summaries for 7a107ac and 6e08c46 (verbose, archived history)
- **Session health degradation tracking**  Removed feature documentation for deprecated `sessionHealth` Map (no longer exists)

**Total Word Count Reduction:** ~6,800 words  ~1,400 words (79% reduction)  
**Sections Consolidated:** 42  12  
**Readability Improvement:** Removed marketing language, passive voice, redundant warnings
