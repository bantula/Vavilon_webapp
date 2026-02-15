# Vavilon Production Readiness Plan

**Created:** February 15, 2026  
**Issue:** Flask development server causing request blocking and missed segments

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
- [ ] Segments 1-5 all finalized
- [ ] No audio_dispatch_fail timeouts
- [ ] Graceful stop logs present
- [ ] TTS audio delivered for all segments
- [ ] Container stable for 10+ minutes after test
- [ ] Memory usage remains under 1GB (check `az container show`)

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

**Estimated Time:** 20 minutes total
- Code changes: 5 min
- Git commit/push: 1 min
- GitHub Actions build: 5-8 min
- Container restart: 1 min
- End-to-end test: 3-5 min

**Go/No-Go Decision Point:** After test in Step 4  
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

**Ready to proceed?**
1. Review this plan
2. Confirm threading approach (vs workers)
3. Execute Step 1-4
4. Report test results

**Alternatively:** Test locally first:
```powershell
cd ai-service
pip install gunicorn
gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 src.app:app
# Test via http://localhost:5000/health
```
