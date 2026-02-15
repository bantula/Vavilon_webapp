# TTS Pipeline Investigation & Fix Plan

**Date:** February 15, 2026 (Post-Hotfix)  
**Status:** TTS Still Failing After TypeError Fix  
**Severity:** 🔴 P0 - TTS non-functional despite hotfix  
**Previous Fix:** Commit 6e08c46 (removed duplicate session_id kwargs)

---

## Current Situation

### What Works ✅
1. **Redis Connection** - No disconnects, stable
2. **Session Lifecycle** - Speaker/listener join, AI session starts normally
3. **Speech Recognition (STT)** - Multiple segments finalized correctly
   - `segment_finalized_received` for each segment
   - `recognizedText` captured accurately ("This is the first section.", etc.)
4. **Translation** - All 9 languages translated, subtitles broadcast successfully
   - `translationCount: 9` for each segment
   - `hasSubtitle: true` for all languages
   - Italian listener receives subtitles
5. **TTS Request Sent** - Node correctly identifies active languages and requests TTS
   - `tts_languages_requested: ["it"]`
   - `generate_tts_sent` with `enqueued: ["it"]`
   - Node → Python `/generate-tts` communication occurs

### What Fails ❌
1. **No `tts_ready` Events Received** - Zero TTS responses across all segments
   - Node waits 10 seconds per segment
   - `missing_tts_for_active_language` timeout for every segment
   - `expectedLanguages: ["it"]`, `receivedLanguages: []`
   - No audio broadcast, user hears nothing
2. **No Visible Python Errors** - Earlier TypeError fixed, but no new error logs visible
   - Previous run: `generate_tts_fail` with TypeError
   - Latest run: No error logs, but still no TTS output
   - Suggests: Silent failure, non-executing workers, or swallowed exceptions
3. **Legacy Audio Dispatch Timeouts** - Separate issue, but adds instability
   - `audio_dispatch_fail` with "timeout of 5000ms exceeded"
   - Continues throughout session, independent of TTS

### Test Results Summary
- **User spoke:** 3 sentences
- **Subtitles seen:** 2 segments
- **Audio heard:** 0
- **TTS requests sent:** 2
- **TTS completions:** 0
- **Timeout warnings:** 2

---

## Root Cause Analysis

### Hypothesis: Python TTS Pipeline Silent Failure
The diagnostic logging from commit 7a107ac should expose the failure point, but we need Python AI service logs to see:
- Are TTS worker threads running?
- Are jobs being dequeued?
- Does Azure TTS SDK fail?
- Does emission to Node backend fail?

**Critical Missing Data:** Python AI service logs not provided in test results

### Potential Failure Points

#### 1. TTS Worker Threads Not Starting
**Symptoms:**
- No `tts_worker_alive` heartbeat logs
- No `tts_synthesis_start` logs
- Queue fills but nothing processes

**Possible Causes:**
- Thread initialization failure
- Exception during thread start swallowed by try/except
- Language-specific worker not created for Italian

**Diagnostic Log Keys:**
```
tts_worker_started (language: it)
tts_worker_alive (periodic heartbeat)
```

#### 2. Azure TTS SDK Failing
**Symptoms:**
- `tts_synthesis_start` appears
- No `tts_synthesis_complete`
- Possible `tts_worker_exception` or silent timeout

**Possible Causes:**
- Azure Speech SDK timeout (no response)
- Invalid voice configuration for Italian
- Network issue reaching Azure TTS API
- SSML parsing error

**Diagnostic Log Keys:**
```
tts_synthesis_start
tts_synthesis_complete OR tts_worker_exception
tts_empty_audio (if SDK returns 0 bytes)
```

#### 3. Emission to Node Backend Failing
**Symptoms:**
- `tts_synthesis_complete` appears (audio generated)
- `tts_emit_attempt` appears
- No `tts_emit_success`, instead `tts_emit_timeout` or `tts_emit_error`

**Possible Causes:**
- Wrong `NODE_BACKEND_URL` in container env vars
- Network firewall blocking Python → Node communication
- Node `/events/tts_ready` endpoint not accepting requests
- Retry logic exhausted (3 attempts × 0.5s delay)

**Diagnostic Log Keys:**
```
tts_emit_attempt (attempt: 1, 2, 3)
tts_emit_success OR tts_emit_timeout OR tts_emit_error
tts_ready_rejected (if Node returns non-200)
```

#### 4. Queue Not Being Processed
**Symptoms:**
- `tts_enqueued` appears
- Queue depth increases
- No `tts_worker_alive` or dequeue activity

**Possible Causes:**
- Worker thread crashed before processing
- Queue object not shared correctly between threads
- Race condition in queue initialization

**Diagnostic Log Keys:**
```
tts_enqueued (queue_depth_after: N)
tts_worker_alive (queue_depth: N)
```

#### 5. Silent Exception Handling
**Symptoms:**
- Some logs appear, then silence
- No explicit error logs
- Worker gives up silently

**Possible Causes:**
- Exception handler too broad, swallows critical errors
- Logging inside exception handler also fails
- Worker thread exits without logging

**Diagnostic Log Keys:**
```
tts_worker_exception
tts_worker_giving_up (after 3 consecutive failures)
tts_worker_crashed
```

---

## Investigation Plan (Phase 1: Gather Evidence)

### Step 1: Retrieve Python AI Service Logs
**Command:**
```bash
az container logs --name vavilon-ai --resource-group vavilon-rg
```

**Look for:**
1. Any logs with `step: "tts_*"` or `step: "generate_tts*"`
2. Worker thread startup logs: `tts_worker_started`
3. Heartbeat logs: `tts_worker_alive`
4. Synthesis logs: `tts_synthesis_start`, `tts_synthesis_complete`
5. Emission logs: `tts_emit_attempt`, `tts_emit_success`
6. Error logs: `tts_worker_exception`, `tts_emit_error`, `generate_tts_fail`
7. Any Python exceptions or tracebacks

### Step 2: Verify Environment Variables
**Command:**
```bash
az container show --name vavilon-ai --resource-group vavilon-rg --query "containers[0].environmentVariables"
```

**Verify:**
- `NODE_BACKEND_URL` = `https://vavilon-backend.azurewebsites.net`
- `AZURE_SPEECH_KEY` exists (value hidden)
- `AZURE_SPEECH_REGION` = `westeurope`

### Step 3: Test Python AI Service Direct TTS Request
**Command:**
```bash
curl -X POST http://50.85.77.14:5000/generate-tts \
  -H "Content-Type: application/json" \
  -d '{
    "sessionId": "test-session-123",
    "segmentId": "test-segment-1",
    "translations": {
      "it": "Ciao, questo è un test."
    }
  }'
```

**Expected Response:**
- 200 OK with `{"status":"ok","enqueued":["it"]}`
- Check Python logs for `generate_tts_received` and subsequent TTS pipeline logs

---

## Fix Strategies (Phase 2: Based on Evidence)

### If Worker Threads Not Starting

**Fix 1: Check Thread Initialization**
- Review `__init__()` method where workers are started
- Add explicit exception logging around `threading.Thread().start()`
- Verify thread daemon mode

**Code Change:**
```python
# In __init__ or start_tts_workers()
try:
    worker_thread = threading.Thread(target=self._voice_synth, args=(lang,), daemon=True)
    worker_thread.start()
    self._log('info', 'tts_worker_started', language=lang, thread_id=worker_thread.ident)
except Exception as e:
    self._log('error', 'tts_worker_start_fail', language=lang, error=str(e), traceback=traceback.format_exc())
```

### If Azure TTS SDK Failing

**Fix 2: Add TTS SDK Timeout**
- Azure SDK might hang indefinitely
- Add explicit timeout to synthesis call
- Implement cancellation token

**Code Change:**
```python
# In _voice_synth()
result = synthesizer.speak_ssml_async(ssml).get(timeout=10.0)  # 10s timeout
if result.reason == speechsdk.ResultReason.Canceled:
    cancellation = result.cancellation_details
    self._log('error', 'tts_sdk_cancelled', reason=cancellation.reason, error=cancellation.error_details)
```

**Fix 3: Test Voice Configuration**
- Verify Italian voice exists: `it-IT-DiegoNeural` or `it-IT-ElsaNeural`
- Check SSML format validity

### If Emission to Node Failing

**Fix 4: Verify NODE_BACKEND_URL**
- Check container environment variables
- Test connectivity from container to Node backend
- Add DNS resolution logging

**Code Change:**
```python
# Before first emission attempt
import socket
try:
    host = urlparse(self.node_backend_url).hostname
    ip = socket.gethostbyname(host)
    self._log('info', 'node_backend_dns', hostname=host, ip=ip)
except Exception as e:
    self._log('error', 'node_backend_dns_fail', error=str(e))
```

**Fix 5: Increase Emission Retry**
- Current: 3 attempts × 0.5s delay
- Increase to: 5 attempts × 1.0s delay

### If Queue Not Being Processed

**Fix 6: Queue Initialization**
- Verify queue created before workers start
- Add queue health check in worker loop

**Code Change:**
```python
# In _voice_synth()
if lang not in self.tts_queues:
    self._log('error', 'tts_queue_missing', language=lang)
    return
```

### If Silent Exception Handling

**Fix 7: Narrow Exception Handlers**
- Replace broad `except Exception` with specific exceptions
- Always log before returning from exception handler
- Add finally blocks to ensure cleanup logging

**Code Change:**
```python
# In critical sections
try:
    # TTS synthesis
    pass
except speechsdk.CancellationError as e:
    self._log('error', 'tts_sdk_cancelled', error=str(e))
    raise  # Re-raise to prevent silent swallowing
except Exception as e:
    self._log('error', 'tts_unknown_error', error=str(e), traceback=traceback.format_exc())
    raise
finally:
    self._log('info', 'tts_attempt_complete', success=False)
```

---

## Execution Plan (After Evidence Gathered)

### Phase 1: Investigation (NOW - Do Not Skip)
1. ✅ Retrieve Python AI service logs from last test
2. ✅ Analyze logs to identify exact failure point
3. ✅ Verify container environment variables
4. ✅ Test direct TTS request to Python service

### Phase 2: Targeted Fix (Based on Evidence)
1. Implement specific fix for identified failure point
2. Add additional safety checks (timeouts, narrower exceptions)
3. Local test if possible
4. Commit with descriptive message

### Phase 3: Deployment
1. Push to GitHub
2. Wait for GitHub Actions (~2-3 min)
3. Restart container: `az container restart --name vavilon-ai --resource-group vavilon-rg`
4. Verify logs show clean startup

### Phase 4: Validation
1. Create English speaker session
2. Join as Italian listener
3. Speak 3 sentences
4. Monitor Python logs in real-time during test
5. Verify subtitles + audio for all 3 sentences
6. Check for any new errors

---

## Success Criteria

### Logs Should Show (Python)
```json
{"step": "generate_tts_received", "languages": ["it"]}
{"step": "tts_enqueued", "language": "it", "queue_depth_after": 1}
{"step": "tts_worker_alive", "language": "it", "queue_depth": 1}
{"step": "tts_synthesis_start", "language": "it"}
{"step": "tts_synthesis_complete", "language": "it", "audio_bytes": 45234}
{"step": "tts_emit_attempt", "language": "it", "attempt": 1}
{"step": "tts_emit_success", "language": "it", "status_code": 200}
```

### Logs Should Show (Node)
```json
{"step": "tts_ready_received", "language": "it", "audioSizeKB": 44.17}
{"step": "tts_all_received", "languages": ["it"]}
```

### User Experience
- ✅ Italian listener sees subtitle (already working)
- ✅ Italian listener hears audio immediately after subtitle
- ✅ All 3 sentences produce both subtitle and audio
- ✅ No timeout warnings in logs

---

## IMMEDIATE NEXT ACTION

**DO NOT PROCEED WITH FIXES UNTIL:**
1. Python AI service logs retrieved and analyzed
2. Exact failure point identified from diagnostic logs
3. Root cause confirmed

**Blind fixes will waste time.** The diagnostic logging invested should tell us exactly where it breaks.
          translations_count=len(translations),
          requested_languages=list(translations.keys()))
```

#### Fix 2: _voice_synth() method
**File**: `ai-service/src/speech_service.py` (lines ~654-809)

Remove `session_id=self.session_id` from these logging calls:
- `tts_worker_started`
- `tts_worker_alive`
- `tts_synthesis_start`
- `tts_empty_audio`
- `tts_synthesis_complete`
- `tts_emit_fail`
- `tts_worker_giving_up`
- `tts_worker_exception`
- `tts_worker_crashed`
- `tts_worker_stopped`

**Count**: ~10 logging calls need fixing in `_voice_synth()`

#### Fix 3: _emit_tts_ready() method
**File**: `ai-service/src/speech_service.py` (lines ~900-978)

Remove `session_id=self.session_id` from these logging calls:
- `tts_emit_attempt`
- `tts_emit_success`
- `tts_emit_timeout`
- `tts_emit_error`

**Count**: ~4 logging calls need fixing in `_emit_tts_ready()`

### Total Changes
- **1 file**: `ai-service/src/speech_service.py`
- **~20 logging calls**: Remove `session_id=self.session_id` from all
- **No functional changes**: Only fix logging kwargs
- **No new features**: Pure bugfix

---

## Testing Plan (5 minutes)

### Test Case: Italian Translation
1. Deploy hotfix to Azure
2. Create speaker session (English)
3. Join as Italian listener
4. Speak 3 sentences clearly
5. **Expected**: Italian subtitles + audio for all 3 sentences

### Success Criteria
- ✅ No TypeError in logs
- ✅ `generate_tts_received` logs appear without errors
- ✅ `tts_synthesis_complete` logs appear with audio_bytes > 0
- ✅ `tts_emit_success` logs appear with status_code=200
- ✅ `tts_ready_received` logs appear in Node
- ✅ Listener hears Italian audio within 5 seconds

### Failure Indicators
- ❌ Any TypeError mentioning "got multiple values"
- ❌ `generate_tts_fail` logs
- ❌ `missing_tts_for_active_language` after 10s

---

## Deployment Steps (10 minutes)

### 1. Fix the Code (3 minutes)
```bash
# Edit ai-service/src/speech_service.py
# Remove all session_id=self.session_id from logging calls

# Commit
git add ai-service/src/speech_service.py PLAN.md
git commit -m "hotfix: remove duplicate session_id kwargs causing TypeError

TTS generation was broken by TypeError: _slog() got multiple values for
keyword argument 'session_id'. The self._log() wrapper already adds
session_id automatically, so explicit session_id= kwargs cause duplication.

Fixed by removing all explicit session_id=self.session_id from logging calls."

# Push to main
git push origin main
```

### 2. Deploy to Azure (5 minutes)
```bash
# Push triggers GitHub Actions (auto-builds Docker image)
# Wait 2-3 minutes for Actions to complete

# Restart AI container to pull latest image
az container restart --name vavilon-ai --resource-group vavilon-rg

# Wait 30 seconds
Start-Sleep -Seconds 30

# Verify running
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"
# Expected: "Running"
```

### 3. Verify Fix (2 minutes)
```bash
# Check logs for startup
az container logs --name vavilon-ai --resource-group vavilon-rg

# Should see: {"step":"startup", "port":5000, "azure_configured":true}
# Should NOT see any TypeErrors
```

---

## Prevention for Future

### Code Review Checklist
- [ ] When adding logging to `TranslationSession` methods, NEVER pass `session_id` explicitly
- [ ] `self._log()` already adds `session_id` and `trace_id` automatically
- [ ] Only pass **additional** context that isn't already in the wrapper
- [ ] Test locally before deploying diagnostic changes

### Safe Logging Pattern
```python
# ✅ CORRECT - self._log adds session_id automatically
self._log('info', 'some_event',
          segment_id=segment_id,
          language=language,
          some_metric=value)

# ❌ WRONG - causes TypeError
self._log('info', 'some_event',
          session_id=self.session_id,  # <-- NEVER DO THIS
          segment_id=segment_id)
```

### Lesson Learned
**Diagnostic improvements can introduce regressions.** Even "safe" logging-only changes need:
1. Local testing before deployment
2. Understanding of helper function signatures
3. Verification that new logs don't crash the code path

---

## Summary

| Aspect | Details |
|--------|---------|
| **Bug Type** | TypeError (duplicate keyword argument) |
| **Severity** | P0 - Complete TTS failure |
| **Introduced** | Commit 7a107ac (Feb 15, 2026 diagnostic improvements) |
| **Root Cause** | Duplicate `session_id` kwarg in logging calls |
| **Fix Complexity** | Low (remove ~20 duplicate kwargs) |
| **Fix Time** | 15 minutes (code + deploy + test) |
| **Impact** | 100% TTS broken → 100% TTS working |
| **Is New?** | YES - regression introduced by diagnostic logging |

---

## Immediate Actions

1. ⏰ **NOW**: Fix duplicate session_id kwargs (3 min)
2. ⏰ **+3min**: Commit and push to main (1 min)
3. ⏰ **+4min**: Wait for GitHub Actions build (3 min)
4. ⏰ **+7min**: Restart AI container (2 min)
5. ⏰ **+9min**: Test end-to-end (3 min)
6. ⏰ **+12min**: Verify logs show success (1 min)

**Total time to fix**: ~15 minutes

---

**Status**: Ready to implement hotfix  
**Priority**: P0 - Deploy immediately  
**Owner**: Development team  
**Target completion**: February 15, 2026 within 15 minutes
