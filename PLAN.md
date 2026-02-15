# Plan: Fix TTS Active Language Sync + Clean Architecture

## Root Cause Analysis

Two bugs, one architectural:

1. **`/update-active-languages` returns 404** — Listener joins BEFORE speaker starts.
   Python session doesn't exist yet → 404. Active languages never sync.
   Result: Italian TTS never queued despite listener being present.

2. **`/process-audio` blocks/times out** — `PushAudioInputStream.write()` can block
   when SDK buffer fills (recognizer crashed or callback thread blocked by sync HTTP).
   `_emit_segment_finalized` does a synchronous HTTP POST *inside* the SDK callback,
   blocking the recognizer thread → audio buffer fills → `write()` blocks → Flask hangs.

3. **Hybrid architecture** — Old per-chunk HTTP (`/process-audio` + degradation tracking)
   coexists with new event-driven flow (`segment_finalized` + `tts_ready`). The mutable
   `_active_languages` state in Python creates a race condition with Node's tracking.

## Fix: Option 1 — Explicit TTS Per Segment (No Global State)

### A. Python `speech_service.py`

1. **Remove** `_active_languages`, `_active_languages_lock`, `update_active_languages()`
2. **Remove** TTS queuing from `_on_recognized` — callback only emits `segment_finalized`
3. **Make `_emit_segment_finalized` non-blocking** — fire in daemon thread, not inline
4. **Add `generate_tts(segment_id, translations)`** — new method called by Node via HTTP
   - Takes explicit `{lang: text}` dict, queues TTS for exactly those languages
   - Logs `languages_enqueued_for_tts`
5. **Add non-blocking `push_audio()`** — audio queue + background writer thread
   - `push_audio()` uses `put_nowait()` → never blocks Flask
   - Background `_audio_writer` thread does actual `_audio_stream.write()`
   - If queue overflows → session marked dead (`_alive = False`)
6. **Add `alive` property** for external health check

### B. Python `app.py`

1. **Remove** `/update-active-languages` endpoint
2. **Add** `POST /generate-tts` endpoint — receives `{sessionId, segmentId, translations}`
3. **Update** `/process-audio` — check `session.alive`, return 410 if dead, auto-cleanup
4. **Make** `/end-session` non-blocking — stop session in background thread

### C. Node `wsHandler.js`

1. **Remove** `sessionHealth` Map + all degradation logic
2. **Remove** `sessionActiveLanguages` Map
3. **Remove** `updateSessionActiveLanguages()` + `setsEqual()`
4. **Simplify** `forwardAudioToAI()` — simple fire-and-forget, no degradation tracking
5. **Export** `getSessionListenerLanguages(sessionId)` — returns Set of languages with listeners
6. **Clean up** handlers — remove sessionHealth.delete(), updateSessionActiveLanguages() calls

### D. Node `events.js`

1. **On `segment_finalized`**: after broadcasting subtitles:
   - Compute active languages from connections
   - Filter translations to active languages only
   - Send `POST AI_SERVICE_URL/generate-tts` with `{sessionId, segmentId, translations}`
   - Log `tts_languages_requested`
2. **On `tts_ready`**: track received languages per segment
3. **Add guard**: 10s timeout, log `missing_tts_for_active_language` if expected TTS not received

### E. Documentation + Git

- Update COPILOT_CONTEXT.md with final architecture
- Branch: `fix/tts-active-language-sync`
- Commit: `fix(streaming): remove old streaming pipeline and fix active language TTS generation`

## Data Flow After Fix

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

## What's Removed
- `/update-active-languages` endpoint (Python)
- `_active_languages` mutable state (Python)
- `sessionHealth` degradation tracking (Node)
- `sessionActiveLanguages` Map (Node)
- `updateSessionActiveLanguages()` (Node)
- All 404 suppression logic (already removed previously)

## Why `/process-audio` Is Kept
Audio MUST reach Python's PushAudioInputStream somehow. The per-chunk HTTP relay
is the simplest transport that works with Flask. The audio queue makes it non-blocking.
A streaming WebSocket or chunked HTTP connection would be ideal but is a larger change
(needs WebSocket server in Python or streaming WSGI). Can be a follow-up.
