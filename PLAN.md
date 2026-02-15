# Fix Plan: TTS Not Delivered to Listeners

**Date:** February 15, 2026  
**Status:** Node requests TTS, Python never delivers it  
**Branch:** `fix/tts-delivery-reliability`

---

## Problem Summary

### Observed Behavior
1. ✅ Subtitles work (2 out of 3 sentences shown)
2. ❌ Audio never plays (0 sentences heard)
3. ✅ Node correctly requests Italian TTS (`tts_languages_requested:["it"]`)
4. ✅ Node sends `POST /generate-tts` (`generate_tts_sent ... enqueued:["it"]`)
5. ❌ Python never responds with `tts_ready` event
6. ⚠️ Node logs `missing_tts_for_active_language` after 10s timeout
7. ❌ Redis connection dropped (`SocketClosedUnexpectedlyError`)
8. ❌ Session orphaned (null traceId in cleanup)

### Root Cause Hypothesis
**Primary**: Python `/generate-tts` endpoint or TTS worker thread is failing silently without emitting events back to Node.

**Contributing factors**:
- Redis instability may corrupt session state
- TTS thread exceptions may be swallowed
- Event emission to Node may fail silently
- ThreadPoolExecutor may reject work under load

---

## Phase 1: Diagnostic Improvements (HIGH PRIORITY)

### Goal
Make failures **visible** before attempting fixes. If TTS fails, we MUST know why.

### Tasks

#### 1.1 Enhanced Python TTS Logging
**File:** `ai-service/src/speech_service.py`

Add comprehensive logging at every TTS stage:

```python
def generate_tts(self, segment_id, translations):
    """Node sends explicit translations to synthesize."""
    enqueued = []
    
    slog('info', 'generate_tts_received',
         session_id=self.session_id,
         segment_id=segment_id,
         translations_count=len(translations),
         requested_languages=list(translations.keys()))
    
    for lang, text in translations.items():
        if lang not in self._translated_text_queues:
            slog('warn', 'tts_language_not_init',
                 session_id=self.session_id,
                 language=lang,
                 note='Language not in target_languages - skipping')
            continue
        
        try:
            queue_depth = self._translated_text_queues[lang].qsize()
            self._translated_text_queues[lang].put({
                'segment_id': segment_id,
                'text': text
            })
            enqueued.append(lang)
            
            slog('info', 'tts_enqueued',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=lang,
                 text_preview=text[:50],
                 queue_depth_before=queue_depth,
                 queue_depth_after=queue_depth + 1)
        except Exception as e:
            slog('error', 'tts_enqueue_fail',
                 session_id=self.session_id,
                 language=lang,
                 error=str(e),
                 traceback=traceback.format_exc())
    
    slog('info', 'generate_tts_complete',
         session_id=self.session_id,
         segment_id=segment_id,
         enqueued_count=len(enqueued),
         enqueued_languages=enqueued)
    
    return enqueued
```

#### 1.2 TTS Worker Thread Health Monitoring
**File:** `ai-service/src/speech_service.py`

Track if TTS threads are alive and processing:

```python
def _voice_synth(self, language):
    """TTS worker thread per language."""
    thread_id = threading.get_ident()
    
    slog('info', 'tts_worker_started',
         session_id=self.session_id,
         language=language,
         thread_id=thread_id)
    
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while self._synth_threads[language]["running"] and not self._stop_event.is_set():
        try:
            # Log heartbeat every 10 iterations
            if consecutive_errors == 0:
                slog('debug', 'tts_worker_alive',
                     session_id=self.session_id,
                     language=language,
                     queue_depth=self._translated_text_queues[language].qsize())
            
            item = self._translated_text_queues[language].get(timeout=0.2)
            segment_id = item['segment_id']
            text = item['text']
            
            slog('info', 'tts_synthesis_start',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=language,
                 text_length=len(text),
                 text_preview=text[:50])
            
            start_time = time.time()
            audio_bytes = self._sdk_tts(language, text)
            duration_ms = int((time.time() - start_time) * 1000)
            
            if not audio_bytes:
                slog('error', 'tts_empty_audio',
                     session_id=self.session_id,
                     segment_id=segment_id,
                     language=language,
                     note='Synthesizer returned no audio data')
                continue
            
            slog('info', 'tts_synthesis_complete',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=language,
                 audio_bytes=len(audio_bytes),
                 duration_ms=duration_ms)
            
            # Emit event to Node
            try:
                self._emit_tts_ready(segment_id, language, audio_bytes)
                consecutive_errors = 0  # Reset on success
            except Exception as emit_error:
                consecutive_errors += 1
                slog('error', 'tts_emit_fail',
                     session_id=self.session_id,
                     segment_id=segment_id,
                     language=language,
                     error=str(emit_error),
                     consecutive_errors=consecutive_errors,
                     traceback=traceback.format_exc())
                
                if consecutive_errors >= max_consecutive_errors:
                    slog('critical', 'tts_worker_giving_up',
                         session_id=self.session_id,
                         language=language,
                         note=f'Failed {max_consecutive_errors} times - stopping worker')
                    break
        
        except queue.Empty:
            continue  # Normal timeout
        
        except Exception as e:
            consecutive_errors += 1
            slog('error', 'tts_worker_exception',
                 session_id=self.session_id,
                 language=language,
                 error=str(e),
                 error_type=type(e).__name__,
                 consecutive_errors=consecutive_errors,
                 traceback=traceback.format_exc())
            
            if consecutive_errors >= max_consecutive_errors:
                slog('critical', 'tts_worker_crashed',
                     session_id=self.session_id,
                     language=language,
                     note=f'{max_consecutive_errors} consecutive errors - worker dead')
                break
    
    slog('info', 'tts_worker_stopped',
         session_id=self.session_id,
         language=language,
         thread_id=thread_id,
         stop_reason='stop_event' if self._stop_event.is_set() else 'running_flag_cleared')
```

#### 1.3 Event Emission Retry Logic
**File:** `ai-service/src/speech_service.py`

Make event emission more resilient:

```python
def _emit_tts_ready(self, segment_id, language, audio_bytes):
    """Emit tts_ready event to Node with retry."""
    import base64
    
    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
    
    event = {
        'type': 'tts_ready',
        'traceId': self.trace_id,
        'sessionId': self.session_id,
        'segmentId': segment_id,
        'language': language,
        'audioFormat': 'riff16khz16bitpcm',
        'audioBytesBase64': audio_b64
    }
    
    url = f'{NODE_BACKEND_URL}/api/events'
    max_retries = 2
    retry_delay = 0.5
    
    for attempt in range(max_retries + 1):
        try:
            slog('info', 'tts_emit_attempt',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=language,
                 attempt=attempt + 1,
                 audio_size_kb=len(audio_bytes) / 1024,
                 url=url)
            
            response = requests.post(url, json=event, timeout=5.0)
            
            slog('info', 'tts_emit_success',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=language,
                 status_code=response.status_code,
                 attempt=attempt + 1)
            
            return  # Success
        
        except requests.exceptions.Timeout as e:
            slog('error', 'tts_emit_timeout',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=language,
                 attempt=attempt + 1,
                 max_retries=max_retries,
                 error=str(e))
            
            if attempt < max_retries:
                time.sleep(retry_delay)
        
        except Exception as e:
            slog('error', 'tts_emit_error',
                 session_id=self.session_id,
                 segment_id=segment_id,
                 language=language,
                 attempt=attempt + 1,
                 max_retries=max_retries,
                 error=str(e),
                 error_type=type(e).__name__,
                 traceback=traceback.format_exc())
            
            if attempt < max_retries:
                time.sleep(retry_delay)
    
    # All retries failed
    raise Exception(f'Failed to emit tts_ready after {max_retries + 1} attempts')
```

#### 1.4 Flask /generate-tts Error Handling
**File:** `ai-service/src/app.py`

Ensure endpoint logs ALL failures:

```python
@app.route('/generate-tts', methods=['POST'])
def generate_tts():
    """Node sends explicit translations to synthesize after receiving segment_finalized."""
    request_id = str(uuid.uuid4())[:8]
    
    try:
        data = request.json
        session_id = data.get('sessionId')
        segment_id = data.get('segmentId')
        translations = data.get('translations', {})
        
        slog('info', 'generate_tts_request',
             request_id=request_id,
             session_id=session_id,
             segment_id=segment_id,
             translations_count=len(translations),
             languages=list(translations.keys()))
        
        if not session_id or not segment_id:
            slog('error', 'generate_tts_bad_request',
                 request_id=request_id,
                 missing='sessionId' if not session_id else 'segmentId')
            return jsonify({'error': 'Missing sessionId or segmentId'}), 400
        
        if not translations:
            slog('warn', 'generate_tts_empty',
                 request_id=request_id,
                 session_id=session_id,
                 segment_id=segment_id)
            return jsonify({'error': 'Empty translations — nothing to synthesize'}), 400
        
        session = sessions.get(session_id)
        if not session:
            slog('error', 'generate_tts_session_not_found',
                 request_id=request_id,
                 session_id=session_id,
                 segment_id=segment_id,
                 active_sessions=list(sessions.keys()))
            return jsonify({'error': 'Session not found'}), 404
        
        if not session.alive:
            slog('error', 'generate_tts_session_dead',
                 request_id=request_id,
                 session_id=session_id,
                 segment_id=segment_id)
            return jsonify({'error': 'Session is dead', 'code': 'SESSION_DEAD'}), 410
        
        enqueued = session.generate_tts(segment_id, translations)
        
        slog('info', 'generate_tts_success',
             request_id=request_id,
             session_id=session_id,
             segment_id=segment_id,
             enqueued_count=len(enqueued),
             enqueued=enqueued)
        
        return jsonify({
            'success': True,
            'sessionId': session_id,
            'segmentId': segment_id,
            'enqueued': enqueued,
            'requestId': request_id
        })
    
    except Exception as e:
        inc_metric('errors_total')
        slog('error', 'generate_tts_exception',
             request_id=request_id,
             error=str(e),
             error_type=type(e).__name__,
             traceback=traceback.format_exc())
        return jsonify({'error': str(e), 'requestId': request_id}), 500
```

#### 1.5 Node TTS Request/Response Tracking
**File:** `backend/src/routes/events.js`

Track TTS request lifecycle:

```javascript
// After sending POST /generate-tts
slog('info', 'generate_tts_sent', {
  sessionId,
  segmentId,
  traceId,
  url: `${AI_SERVICE_URL}/generate-tts`,
  requestBody: {
    sessionId,
    segmentId,
    translationsCount: Object.keys(ttsTranslations).length,
    languages: Object.keys(ttsTranslations)
  },
  responseStatus: ttsResponse.status,
  responseData: ttsResponse.data
});

// In tts_ready handler
slog('info', 'tts_ready_received', {
  sessionId,
  segmentId,
  language,
  traceId,
  audioSizeKB: (audioSizeBytes / 1024).toFixed(2),
  timeSinceSegmentMs: Date.now() - segmentTimestamp  // Track latency
});
```

---

## Phase 2: Redis Stability (MEDIUM PRIORITY)

### Goal
Prevent Redis disconnects from corrupting session state.

### Tasks

#### 2.1 Redis Connection Resilience
**File:** `backend/src/services/sessionService.js`

Add connection monitoring and auto-reconnect:

```javascript
const redis = require('redis');

const redisClient = redis.createClient({
  url: process.env.REDIS_URL,
  password: process.env.REDIS_PASSWORD,
  socket: {
    reconnectStrategy: (retries) => {
      if (retries > 10) {
        console.error('Redis reconnect failed after 10 attempts');
        return new Error('Redis reconnect limit exceeded');
      }
      const delay = Math.min(retries * 50, 2000);
      console.log(`Redis reconnecting in ${delay}ms (attempt ${retries})`);
      return delay;
    }
  }
});

redisClient.on('error', (err) => {
  console.error('Redis client error:', err);
});

redisClient.on('reconnecting', () => {
  console.warn('Redis client reconnecting...');
});

redisClient.on('ready', () => {
  console.log('Redis client ready');
});
```

#### 2.2 Critical State in Memory
**Option A:** Keep TTS guard state in-memory (not Redis)

```javascript
// In events.js - pendingTts Map is already in-memory
// But ensure it survives Redis drops:

const pendingTts = new Map();  // sessionId → Set of expected languages

// Don't rely on Redis for TTS guard - keep local
```

**Option B:** Use Redis with expiry + fallback

```javascript
// TTL on Redis keys for TTS guard
await redisClient.setEx(
  `tts:pending:${segmentId}`,
  15,  // 15 second expiry
  JSON.stringify({ sessionId, languages: [...expectedLanguages] })
);
```

---

## Phase 3: TTS Thread Pool Monitoring (LOW PRIORITY)

### Goal
Ensure ThreadPoolExecutor doesn't reject work or stall.

### Tasks

#### 3.1 Executor Health Checks
**File:** `ai-service/src/speech_service.py`

Add periodic health checks:

```python
def get_tts_status(self):
    """Get current TTS worker status for monitoring."""
    status = {}
    for lang, thread_info in self._synth_threads.items():
        status[lang] = {
            'running': thread_info['running'],
            'queue_depth': self._translated_text_queues[lang].qsize(),
            'future_done': thread_info.get('future') and thread_info['future'].done(),
            'future_cancelled': thread_info.get('future') and thread_info['future'].cancelled()
        }
    return status
```

#### 3.2 Flask Debug Endpoint
**File:** `ai-service/src/app.py`

Add endpoint to check TTS worker health:

```python
@app.route('/debug/session/<session_id>/tts-status', methods=['GET'])
def tts_status(session_id):
    """Get TTS worker status for a session."""
    session = sessions.get(session_id)
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    
    return jsonify({
        'sessionId': session_id,
        'alive': session.alive,
        'tts_status': session.get_tts_status()
    })
```

---

## Phase 4: Architecture Improvements (FUTURE)

### Goal
Make TTS delivery more reliable long-term.

### Options

#### Option A: Persistent Job Queue (Redis Queue / Bull)
Instead of in-memory queues, use Redis-backed job queue:
- TTS jobs persist across worker restarts
- Retry logic built-in
- Better observability

#### Option B: Server-Sent Events (SSE) for TTS
Instead of HTTP POST callbacks, use SSE from Python → Node:
- Long-lived connection
- No missed events
- Backpressure handling

#### Option C: Message Queue (RabbitMQ / Azure Service Bus)
Decouple event emission from TTS synthesis:
- Guaranteed delivery
- Replay capability
- Better at-least-once semantics

---

## Testing Plan

### Test 1: Happy Path with Full Logging
1. Start all services with DEBUG logging
2. Create session, join as Italian listener
3. Speak 3 sentences
4. **Expected logs (Python)**:
   ```json
   {"step":"generate_tts_received", "languages":["it"]}
   {"step":"tts_enqueued", "language":"it", "queue_depth_after":1}
   {"step":"tts_worker_alive", "language":"it"}
   {"step":"tts_synthesis_start", "language":"it", "segment_id":"..."}
   {"step":"tts_synthesis_complete", "language":"it", "audio_bytes":45234}
   {"step":"tts_emit_attempt", "language":"it", "attempt":1}
   {"step":"tts_emit_success", "language":"it", "status_code":200}
   ```
5. **Expected logs (Node)**:
   ```json
   {"step":"generate_tts_sent", "languages":["it"], "responseStatus":200}
   {"step":"tts_ready_received", "language":"it", "audioSizeKB":44.17}
   ```
6. **Expected result**: Listener hears all 3 sentences

### Test 2: TTS Failure Isolation
1. Temporarily break TTS for one language (wrong voice name)
2. Join with 2 listeners (broken language + working language)
3. Speak 2 sentences
4. **Expected**: Working language gets audio, broken language logs error but doesn't crash

### Test 3: Redis Disconnect Recovery
1. Start session, join listener
2. Stop Redis container mid-session
3. Restart Redis
4. Speak after Redis recovers
5. **Expected**: Session continues, TTS still works (in-memory state preserved)

### Test 4: High Load (Queue Depth)
1. Join 5 listeners (all different languages)
2. Speak 10 sentences rapidly
3. **Expected**: All languages eventually get all 10 audio segments (queue drains)

---

## Success Criteria

### Phase 1 Complete When:
- ✅ Every TTS stage logged (enqueue, synthesis start, synthesis complete, emit)
- ✅ TTS worker thread health visible in logs
- ✅ If TTS fails, we see exactly WHERE it failed
- ✅ Event emission failures logged with retry attempts

### Phase 2 Complete When:
- ✅ Redis disconnects logged but don't crash session
- ✅ TTS guard state survives Redis drops
- ✅ Auto-reconnect to Redis with exponential backoff

### Phase 3 Complete When:
- ✅ Can query TTS worker status via debug endpoint
- ✅ ThreadPoolExecutor rejections logged

### All Phases Complete When:
- ✅ Listener consistently hears 100% of synthesized audio
- ✅ No `missing_tts_for_active_language` warnings
- ✅ No silent TTS failures
- ✅ Redis drops don't prevent TTS delivery

---

## Implementation Order

1. **Day 1 (2-3 hours)**: Phase 1.1-1.4 — Enhanced logging
2. **Day 2 (1-2 hours)**: Test with full logging, identify exact failure point
3. **Day 3 (2-4 hours)**: Fix identified issue (likely event emission or thread startup)
4. **Day 4 (1-2 hours)**: Phase 2.1 — Redis resilience
5. **Day 5 (1 hour)**: Full regression testing

---

## Rollback Plan

If changes destabilize system:
1. Revert to commit `ec6dce6` (current stable)
2. Deploy Phase 1 logging ONLY (no behavior changes)
3. Collect logs from production to diagnose
4. Fix in separate branch before re-deploying

---

## Open Questions

1. **Does Python `/generate-tts` receive the request?**
   - Check Python logs for `generate_tts_request`
   - If NO → Node-to-Python network issue
   - If YES → TTS worker or emission failing

2. **Do TTS worker threads start?**
   - Check for `tts_worker_started` logs
   - If NO → ThreadPoolExecutor not submitting work
   - If YES but no synthesis logs → Queue empty or get() blocking

3. **Does TTS synthesis succeed?**
   - Check for `tts_synthesis_complete` with audio_bytes > 0
   - If NO → Azure SDK TTS failing (wrong voice, quota, network)
   - If YES → Event emission failing

4. **Does event emission reach Node?**
   - Check for `tts_emit_success` in Python + `tts_ready_received` in Node
   - If Python success but Node never receives → Network/proxy issue
   - If Python fails → Retry logic should catch and log

---

## Next Steps (Immediate)

1. **Deploy Phase 1.1-1.4** to production (logging only, no behavior change)
2. **Run Test 1** with Italian listener
3. **Collect logs** from both Python and Node
4. **Identify exact failure point** from structured logs
5. **Return to this plan** with diagnosis to proceed to targeted fix

---

**Owner:** Development Team  
**Target Completion:** February 18, 2026  
**Review Date:** After Phase 1 logging deployed
