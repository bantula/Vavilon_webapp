# TTS Worker Thread Lifecycle Bug

## Current Status

**What Works:**
- ✅ Redis queried consistently for active listeners
- ✅ Node correctly computes TTS languages: `["it"]`
- ✅ Node sends POST `/generate-tts` to Python AI
- ✅ Python AI receives request, returns 200 OK with `{"enqueued":["it"]}`
- ✅ Italian TTS added to queue successfully

**What Fails:**
- ❌ NO Italian TTS worker thread exists
- ❌ Enqueued Italian TTS never synthesized
- ❌ Node never receives `tts_ready` events
- ❌ Timeout: `missing_tts_for_active_language` after 10s

## Root Cause: Static Worker Initialization

**Evidence from logs (session `b7a3cdb3-4058-4b3d-a93c-c3950c47dfe3`):**

```
tts_enqueued: language="it", queue_depth_after=2  ✅ Italian TTS enqueued
tts_worker_alive: language="de", queue_depth=0    ❌ Only German worker
tts_worker_alive: language="es", queue_depth=0    ❌ Only Spanish worker
NO Italian worker thread at all!
```

**The Bug:**
TTS worker threads are created ONCE at session start based on initial listener languages. When a listener joins later for a NEW language (Italian), there's no mechanism to spawn a worker thread for that language.

## Investigation Plan

### Phase 1: Confirm Worker Lifecycle
- [x] Verify Python AI logs show TTS successfully enqueued for Italian
- [x] Verify NO Italian worker thread exists (only de/es)
- [ ] Read `speech_service.py`: Where are worker threads initialized?
- [ ] Identify: Is it in `__init__()` or `start_continuous_recognition()`?

### Phase 2: Understand Session/Language Initialization
- [ ] Read `app.py` `/start-session` endpoint
- [ ] Verify: What `target_languages` are passed at session start?
- [ ] Check: Are workers initialized from this initial list only?
- [ ] Determine: Is there dynamic worker creation logic anywhere?

### Phase 3: Design Fix Options

**Option A: Dynamic Worker Creation (Recommended)**
- Modify `generate_tts()` to check if worker thread exists for language
- If missing, spawn new worker thread on-demand
- Advantage: Supports dynamic listener joins
- Risk: Thread lifecycle management complexity

**Option B: Pre-create All Supported Workers**
- Initialize workers for ALL supported languages at session start
- Advantage: Simple, no dynamic logic
- Disadvantage: Resource overhead for unused languages

**Option C: Listener-triggered Worker Creation**
- Create workers when listener joins (via `/session-state` or WebSocket)
- Advantage: Proactive vs reactive
- Disadvantage: Requires Node→Python notification mechanism

### Phase 4: Implement Fix
- [ ] Implement chosen solution in `speech_service.py`
- [ ] Add initialization logging: `tts_worker_created`
- [ ] Ensure thread-safe queue/worker creation
- [ ] Handle edge case: concurrent requests for same new language

### Phase 5: Test & Deploy
- [ ] Test: Start session with German listener only
- [ ] Test: Italian listener joins mid-session
- [ ] Verify: Italian worker thread created dynamically
- [ ] Verify: Italian TTS synthesized and emitted
- [ ] Deploy to Azure Container Instance
- [ ] User test: 3 sentences with dynamic listener join

## Next Steps

Execute Phase 1-3 to understand worker initialization, then implement dynamic worker creation fix.
