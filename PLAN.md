# Vavilon Production Readiness Plan

**Created:** February 15, 2026  
**Issue:** Flask development server causing request blocking and missed segments

---

## Deployment Status (Updated: February 15, 2026 20:55 UTC)

### ✅ ALL STEPS COMPLETED

- [x] Root cause identified: Flask single-threaded blocking
- [x] Solution designed: Gunicorn with workers=1, threads=4
- [x] Code implemented: `gunicorn.conf.py`, `Dockerfile`, `requirements.txt`
- [x] Git commit: `6b5db08` - "feat: switch to gunicorn with threading for production readiness"
- [x] Pushed to GitHub main branch
- [x] GitHub Actions Docker build completed successfully
- [x] Backend deployed with graceful stop code
- [x] **Fixed CrashLoopBackOff**: Added `src/__init__.py` + import fallback in `app.py` (commit `ce3930f`)
- [x] **Container recreated**: Deleted stuck container, recreated with `az container create`
- [x] **Gunicorn running**: Logs confirm `Starting gunicorn 22.0.0`, worker `gthread`, pid 23
- [x] **Health endpoint responding**: `http://vavilon-ai.westeurope.azurecontainer.io:5000/health` returns OK
- [x] **All routes registered**: `/generate-tts`, `/process-audio`, `/start-session`, `/end-session` all present
- [x] **Backend healthy**: `https://vavilon-backend.azurewebsites.net/health` returns OK

### Previous Issue: CrashLoopBackOff (Exit Code 3) - RESOLVED
- **Root cause**: Gunicorn imports app as `src.app:app` (package-style), but `app.py` had bare `from speech_service import TranslationSession` which only works with direct `python src/app.py` execution
- **Fix**: Added `ai-service/src/__init__.py` + `try/except ImportError` fallback in `app.py`
- **Container was in CrashLoopBackOff** with 22+ starts and kills, exit code 3 (worker boot failure)
- **Resolution**: Deleted stuck container, pushed fix, rebuilt image, recreated container

### NEW: TTS Audio Never Plays — ThreadPoolExecutor Starvation (Feb 15, 2026 21:30 UTC)

**Symptom:** Subtitles work perfectly, but no audio ever plays for any listener.

**Root Cause:** `speech_service.py` line 116 had `ThreadPoolExecutor(max_workers=2)`, but `_setup_synthesizers()` submits infinite-loop TTS workers for ALL target languages (up to 9). Only the first 2 workers ever execute; the rest are queued forever in the executor's internal queue. Text enqueued for other languages is never consumed.

**Evidence from container logs:**
- Only `fr` and `ja` workers running (the first 2 submitted)
- Italian text enqueued (`queue_depth` growing to 7) but never consumed
- Zero `tts_synthesis_start` events in entire session
- Workers for other languages start then immediately exit on session end (never ran)

**Fix:** Changed `max_workers=2` to `max_workers=max(len(target_languages), 2)` so every language gets its own thread.

**Deploy Steps:**
1. Push to main (triggers GitHub Actions Docker build ~5 min)
2. After build completes, restart container:
   ```bash
   az container restart --name vavilon-ai --resource-group vavilon-rg
   ```
3. Wait 30s, verify:
   ```bash
   az container logs --name vavilon-ai --resource-group vavilon-rg
   # Should see: Starting gunicorn 22.0.0
   curl http://vavilon-ai.westeurope.azurecontainer.io:5000/health
   ```

### Remaining: End-to-End Test
- [ ] English speaker + Italian listener
- [ ] Speak 5 sentences rapidly
- [ ] Verify all 5 segments finalized with Italian TTS
- [ ] Verify `tts_synthesis_start` events appear in logs (was zero before fix)
- [ ] Verify no audio_dispatch_fail timeouts
- [ ] Verify TTS guard logs show `tts_all_received` (not `missing_tts_for_active_language`)

---

## Root Cause Analysis

### Problem Summary
1. **Missing Segment 3:** Flask stopped receiving HTTP requests mid-session (at 19:37:42)
2. **Audio Timeout:** Backend audio chunk 151 never reached Python (10s timeout)
3. **No Graceful Stop:** `/end-session` request never reached Flask

### Technical Root Cause

**Flask's built-in development server is single-threaded and synchronous:**

```python
# Current: ai-service/src/app.py line 393
app.run(host='0.0.0.0', port=port, debug=False)
```

**What happens:**
1. Backend sends audio chunks rapidly (every 85ms, ~12 requests/second)
2. Flask processes requests one at a time in a single thread
3. If one request blocks (Azure SDK call, TTS synthesis), all subsequent requests queue
4. Queue fills up → backend times out → session fails

**Evidence from logs:**
- Flask received 125 audio chunks successfully
- Last successful request: 19:37:42
- Backend continued sending until chunk 232 (missed 107 chunks)
- Container resources: 1 CPU, 1.5GB RAM (adequate, not a resource issue)

**Your intuition is CORRECT:** We need `gunicorn` with `--threads`, not `--workers`.

---

## Solution: Production WSGI Server with Threading

### Why Gunicorn with --threads?

**✅ Use `--threads` (threading model):**
- **ONE process** = shared memory space
- Azure SDK objects (TranslationRecognizer, AudioInputStream) are safely shared
- Session state (`sessions` dict) works correctly
- TTS ThreadPoolExecutor works as designed
- Lower memory footprint (1 process vs multiple)

**❌ DON'T use `--workers` (multi-process):**
- Multiple processes = separate memory spaces
- `sessions` dict NOT shared across processes
- Backend could send request to Worker 1, but session lives in Worker 2
- Would require Redis/external state management (unnecessary complexity)

**Recommended Configuration:**
```bash
gunicorn --bind 0.0.0.0:5000 \
         --workers 1 \
         --threads 4 \
         --timeout 60 \
         --worker-class sync \
         --access-logfile - \
         --error-logfile - \
         src.app:app
```

**Why these settings:**
- `--workers 1`: Single process (shared state)
- `--threads 4`: Handle 4 concurrent HTTP requests (audio + TTS + segments + health checks)
- `--timeout 60`: Allow long-running Azure SDK calls (graceful stop takes 2s + TTS synthesis ~1-2s)
- `--worker-class sync`: Standard synchronous worker (no async/gevent complexity needed)
- Logs to stdout/stderr for Azure Container Instance log capture

---

## Implementation Steps

### Step 1: Update Requirements
**File:** `ai-service/requirements.txt`

**Add:**
```
gunicorn==22.0.0
```

### Step 2: Update Dockerfile
**File:** `ai-service/Dockerfile`

**Change:**
```dockerfile
CMD ["python", "src/app.py"]
```

**To:**
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "60", "--worker-class", "sync", "--access-logfile", "-", "--error-logfile", "-", "src.app:app"]
```

**Alternative (cleaner):** Create `gunicorn.conf.py` and use:
```dockerfile
CMD ["gunicorn", "--config", "gunicorn.conf.py", "src.app:app"]
```

### Step 3: Deploy Changes

**Commands:**
```powershell
# From workspace root
git add ai-service/
git commit -m "feat: switch to gunicorn with threading for production readiness"
git push origin main

# Wait for GitHub Actions to build new Docker image (~5 min)
# Monitor: https://github.com/bantula/Vavilon_webapp/actions

# Restart container to pull new image
az container restart --name vavilon-ai --resource-group vavilon-rg
Start-Sleep -Seconds 30
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView.state"
```

### Step 4: Test End-to-End

**Test Scenario:**
1. Create English speaker session
2. Join Italian listener
3. Speak **5 sentences rapidly** (stress test)
4. Stop IMMEDIATELY after sentence 5
5. Verify ALL 5 segments received
6. Verify NO audio_dispatch_fail timeouts

**Expected Logs:**
```json
// Backend
{"step":"stop_speaking","note":"Ending session with graceful stop"}
{"step":"ai_session_ended","graceful":true}

// Python
{"step":"end_session_request","graceful":true}
{"step":"graceful_stop_closing_stream"}
{"step":"graceful_stop_wait_complete"}
{"step":"session_cleanup_complete"}
```

---

## Alternative Approaches (Not Recommended)

### Option A: Flask with Threaded Mode
```python
app.run(host='0.0.0.0', port=port, threaded=True)
```

**Cons:**
- Still a development server (Flask docs warn against production use)
- Less control over thread pool size
- No production-grade features (worker restart, health checks, graceful shutdown)

### Option B: Multiple Workers + Redis Session Store
```bash
gunicorn --workers 4 --bind 0.0.0.0:5000 src.app:app
```

**Cons:**
- Requires Redis for `sessions` dict
- More complex (serialize Azure SDK objects?)
- Higher memory usage (4 processes × 1.5GB each = 6GB)
- Azure Container Instance would need upgrade
- Overkill for current load (~1-5 concurrent sessions)

---

## Risk Assessment

### Low Risk Changes
✅ Adding gunicorn (battle-tested, industry standard)  
✅ Threading model (Python GIL released during I/O operations like Azure SDK calls)  
✅ 4 threads for 1-5 concurrent sessions = large safety margin

### Testing Checklist
- [x] Code changes committed and pushed
- [x] GitHub Actions build completed
- [x] New Docker image in ACR (SHA bb8936228d44...)
- [ ] Container successfully restarted (BLOCKED: stuck in "Updating")
- [ ] Gunicorn startup logs verified
- [ ] Health endpoint responding
- [ ] Segments 1-5 all finalized
- [ ] No audio_dispatch_fail timeouts
- [ ] Graceful stop logs present
- [ ] TTS audio delivered for all segments
- [ ] Container stable for 10+ minutes after test
- [ ] Memory usage remains under 1GB

---

## Rollback Plan

If gunicorn causes issues:

```powershell
# Revert to Flask dev server
git revert HEAD
git push origin main

# Wait for GitHub Actions
az container restart --name vavilon-ai --resource-group vavilon-rg
```

**Or:** Immediately restart with old image:
```powershell
az container show --name vavilon-ai --resource-group vavilon-rg --query "containers[0].image"
# Note the previous image SHA
az container create --resource-group vavilon-rg --name vavilon-ai-old --image vavilonacr.azurecr.io/vavilon-ai@sha256:<OLD_SHA> ...
```

---

## Expected Outcome

**Before (Flask dev server):**
- ❌ 2/3 segments (missed final)
- ❌ Audio timeouts after ~125 chunks
- ❌ Single-threaded blocking
- ❌ Not production-ready

**After (Gunicorn + threading):**
- ✅ 5/5 segments (100% reliability)
- ✅ No audio timeouts (concurrent request handling)
- ✅ 4 concurrent threads (audio, TTS, segments, graceful stop)
- ✅ Production-ready deployment

---

## Timeline

**Original Estimate:** 20 minutes total

**Actual Progress:**
- ✅ Code changes: 5 min (completed)
- ✅ Git commit/push: 1 min (completed)
- ✅ GitHub Actions build: 8 min (completed at 20:20:51 UTC)
- ⏳ Container restart: **7+ minutes and counting** (expected 1-2 min, currently stuck)
- ⚠️ **BLOCKED:** Container deployment issue preventing testing

**Current Status:** Deployment phase blocked due to container stuck in "Updating" state.

**Time Spent So Far:** ~15 minutes (code) + 8 minutes (build) + 7+ minutes (waiting) = 30+ minutes

**Estimated Remaining Time:**
- If container issue resolves: 5-10 min (verification + testing)
- If need to recreate container: 15-20 min (delete + recreate + verify + test)

**Go/No-Go Decision Point:** After container successfully restarts  
**Success Criteria:** All 5 segments received + no timeouts

---

## Questions?

**Q: Why not async (gevent/eventlet)?**  
A: Azure Speech SDK is synchronous. Threading is simpler and sufficient for current load.

**Q: Why only 4 threads?**  
A: Typical concurrent requests: 1 audio chunk + 1 TTS request + 1 segment callback + 1 health check = 4 max.

**Q: What if we scale to 100 concurrent sessions?**  
A: Then we'd need `--workers 4 --threads 8` (32 concurrent requests) + increase container CPU to 4 cores and memory to 4GB. But that's future optimization.

---

## Next Steps

### Immediate Actions Required

**1. Resolve Container Deployment Issue**

The container has been stuck in "Updating" state for 7+ minutes. Try one of these:

**Option A: Wait and monitor**
```powershell
# Check status every 2 minutes
az container show --name vavilon-ai --resource-group vavilon-rg --query "{State:instanceView.state, ProvisioningState:provisioningState}" -o json
```

**Option B: Force delete and recreate** (if stuck for >15 min)
```powershell
# Delete stuck container
az container delete --name vavilon-ai --resource-group vavilon-rg --yes

# Recreate from latest image
az container create \
  --resource-group vavilon-rg \
  --name vavilon-ai \
  --image vavilonacr.azurecr.io/vavilon-ai:latest \
  --registry-login-server vavilonacr.azurecr.io \
  --registry-username <USERNAME> \
  --registry-password <PASSWORD> \
  --dns-name-label vavilon-ai \
  --ports 5000 \
  --cpu 1 \
  --memory 1.5 \
  --environment-variables \
    SPEECH_KEY=<KEY> \
    SPEECH_REGION=westeurope \
    BACKEND_URL=https://vavilon-backend.azurewebsites.net
```

**Option C: Check Azure Portal**
- Navigate to: Portal → Resource Groups → vavilon-rg → vavilon-ai
- Check "Events" tab for detailed error messages
- Check "Containers" tab for pull status

**2. Once Container is Running**
```powershell
# Verify gunicorn startup
az container logs --name vavilon-ai --resource-group vavilon-rg

# Should see:
# [INFO] Starting gunicorn 22.0.0
# [INFO] Listening at: http://0.0.0.0:5000
# [INFO] Using worker: sync
# [INFO] Booting worker with pid: ###

# Test health endpoint
Invoke-WebRequest -Uri "http://vavilon-ai.westeurope.azurecontainer.io:5000/health"
```

**3. End-to-End Testing**
- Navigate to: https://www.vavilonapp.rs
- English speaker → Italian listener
- Speak 5 sentences, stop immediately
- Verify all 5 segments + no timeouts

**4. Monitor Production**
```powershell
# Check container status after 10 minutes
az container show --name vavilon-ai --resource-group vavilon-rg --query "instanceView"

# Check memory usage
az container show --name vavilon-ai --resource-group vavilon-rg --query "containers[0].instanceView.currentState.detailStatus"
```
