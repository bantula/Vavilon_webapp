# TTS Pipeline Fix - Redis Data Source Sync Issue

**Date:** February 15, 2026  
**Status:**  ROOT CAUSE IDENTIFIED & FIXED  
**Deployed:** Commit e40ca37 - February 15, 2026 @ 17:00 UTC

---

## Root Cause: Data Source Mismatch

**The Bug**: Node backend used TWO DIFFERENT data sources for finding listeners:
- Subtitles: Query Redis  Found listeners 
- TTS decision: Query in-memory Map  Found NONE   

Result: Subtitles delivered, but TTS never requested.

## Fix: Use Redis for Both

Changed events.js to query Redis (single source of truth) instead of memory Map.

**Files Changed:**
- backend/src/services/sessionService.js: Added getSessionListenerLanguages() (Redis-backed)
- backend/src/routes/events.js: Now awaits getSessionListenerLanguages() from Redis

**Deployed:** vavilon-backend running with fix at 17:00 UTC
