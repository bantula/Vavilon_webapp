# URGENT FIX: TypeError Breaking TTS Generation

**Date:** February 15, 2026  
**Status:** CRITICAL BUG - Production broken  
**Severity:** 🔴 P0 - TTS completely non-functional  
**Introduced:** Commit 7a107ac (diagnostic logging improvements)  
**Branch:** `hotfix/duplicate-session-id`

---

## Problem Summary

### Observed Behavior
1. ✅ Segment recognized and finalized
2. ✅ Node requests Italian TTS correctly (`ttsLanguagesRequested:["it"]`)
3. ✅ Node sends `/generate-tts` to Python
4. ❌ **Python raises TypeError in TTS generation**
5. ❌ TTS job never completes
6. ❌ No `tts_ready` event sent to Node
7. ❌ No audio delivered to listeners

### Error Message
```json
{
  "step": "generate_tts_fail",
  "error": {
    "error": "speech_service._slog() got multiple values for keyword argument 'session_id'",
    "requestId": "74fd42a9"
  }
}
```

### Root Cause
**TypeError in logging calls** - The diagnostic improvements in commit 7a107ac introduced a bug where `session_id` is passed twice to `_slog()`:

1. `self._log()` wrapper automatically adds `session_id=self.session_id`
2. My new logging calls ALSO explicitly pass `session_id=self.session_id`
3. Result: `_slog()` receives `session_id` both as automatic kwarg and explicit kwarg
4. Python raises TypeError: "got multiple values for keyword argument"

**Code Structure**:
```python
# self._log wrapper (line 139-141)
def _log(self, level, step, **kwargs):
    _slog(level, step, session_id=self.session_id,  # <-- Automatically added
          trace_id=self.trace_id, **kwargs)

# My new logging call (BROKEN)
self._log('info', 'generate_tts_received',
          session_id=self.session_id,  # <-- DUPLICATE! Causes TypeError
          segment_id=segment_id, ...)
```

### Impact
- **This is a NEW error** introduced by my diagnostic improvements
- TTS pipeline completely broken (0% audio delivery)
- Affects ALL languages, ALL sessions
- Diagnostic logging backfired - the logging itself broke the feature

### Is This the Same as Before?
**NO.** This is a **regression introduced by commit 7a107ac**:
- **Before my changes**: TTS had mysterious silent failures (unknown cause)
- **After my changes**: TTS has explicit TypeError crash (known cause: duplicate kwarg)
- **My changes made it WORSE** by introducing a new bug that breaks 100% of TTS

---

## Fix Plan (HOTFIX - 15 minutes)

### Strategy
Remove ALL explicit `session_id=self.session_id` from logging calls in speech_service.py, since `self._log()` already adds it automatically.

### Files to Fix
1. `ai-service/src/speech_service.py` - Remove duplicate `session_id` kwargs

### Changes Required

#### Fix 1: generate_tts() method
**File**: `ai-service/src/speech_service.py` (lines ~325-377)

Remove `session_id=self.session_id` from these logging calls:
- `generate_tts_received`
- `tts_language_not_init`
- `tts_enqueued`
- `tts_enqueue_fail`
- `generate_tts_complete`

**Before (BROKEN)**:
```python
self._log('info', 'generate_tts_received',
          session_id=self.session_id,  # <-- REMOVE THIS
          segment_id=segment_id,
          translations_count=len(translations),
          requested_languages=list(translations.keys()))
```

**After (FIXED)**:
```python
self._log('info', 'generate_tts_received',
          segment_id=segment_id,
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
