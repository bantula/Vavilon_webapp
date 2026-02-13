"""
Test Event-Driven Streaming Architecture
=========================================

Validates the new continuous recognition + event-driven translation/TTS pipeline.

Requirements:
- Node backend running on http://localhost:3000
- Python AI service running on http://localhost:5000
- Azure Speech credentials configured

Usage:
    python debug/test_streaming_events.py

Expected behavior:
- Continuous recognition processes multiple utterances without stopping
- segment_finalized events emitted immediately upon recognition
- tts_ready events emitted after synthesis (only for active languages)
- No POST /process-audio 404 errors
- No blocking or timeouts between utterances
"""

import base64
import json
import requests
import time
import uuid
import wave
import io

# Configuration
BACKEND_URL = 'http://localhost:3000'
AI_SERVICE_URL = 'http://localhost:5000'

# Test parameters
SESSION_ID = f'test-streaming-{uuid.uuid4().hex[:8]}'
TRACE_ID = str(uuid.uuid4())
LISTENER_LANGUAGE = 'es'  # Spanish

def slog(level, step, **kwargs):
    """Structured logging."""
    entry = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'level': level,
        'component': 'test',
        'step': step,
        **kwargs
    }
    print(json.dumps(entry))


def generate_silence_chunk(duration_ms=300, sample_rate=16000):
    """Generate a chunk of silence (all zeros) to simulate audio input."""
    num_samples = int(sample_rate * duration_ms / 1000)
    # PCM 16-bit = 2 bytes per sample
    audio_bytes = bytes(num_samples * 2)
    return base64.b64encode(audio_bytes).decode('utf-8')


def test_session_lifecycle():
    """Test 1: Session creation and cleanup."""
    slog('info', 'test_start', test='session_lifecycle', session_id=SESSION_ID)
    
    # Create session via backend
    resp = requests.post(f'{BACKEND_URL}/api/sessions', json={
        'supportedLanguages': ['en', 'es', 'fr', 'de']
    })
    
    if resp.status_code != 201:
        slog('error', 'session_create_fail', status=resp.status_code, body=resp.text)
        return False
    
    session_data = resp.json()
    join_code = session_data.get('joinCode')
    
    slog('info', 'session_created', session_id=SESSION_ID, join_code=join_code)
    
    # Start AI session
    resp = requests.post(f'{AI_SERVICE_URL}/start-session', json={
        'sessionId': SESSION_ID,
        'traceId': TRACE_ID,
        'sourceLanguage': 'en-US',
        'targetLanguages': ['es', 'fr', 'de']
    }, timeout=15)
    
    if resp.status_code != 200:
        slog('error', 'ai_session_start_fail', status=resp.status_code, body=resp.text)
        return False
    
    slog('info', 'ai_session_started', response=resp.json())
    
    return True


def test_audio_streaming():
    """Test 2: Stream audio chunks without blocking."""
    slog('info', 'test_start', test='audio_streaming', session_id=SESSION_ID)
    
    # Send 10 audio chunks
    for seq_no in range(1, 11):
        audio_chunk = generate_silence_chunk(duration_ms=300)
        
        resp = requests.post(f'{AI_SERVICE_URL}/process-audio', json={
            'sessionId': SESSION_ID,
            'traceId': TRACE_ID,
            'seqNo': seq_no,
            'audioData': audio_chunk
        }, timeout=10)
        
        if resp.status_code == 404:
            slog('error', 'audio_404', seq_no=seq_no, status=404,
                 note='Session not found - continuous recognition may have crashed')
            return False
        
        if resp.status_code != 200:
            slog('error', 'audio_fail', seq_no=seq_no, status=resp.status_code, body=resp.text)
            return False
        
        # Should return immediately without blocking
        if seq_no % 5 == 0:
            slog('info', 'audio_pushed', seq_no=seq_no, status=200)
        
        time.sleep(0.1)  # Simulate microphone timing (~10 chunks/sec)
    
    slog('info', 'audio_streaming_complete', chunks_sent=10)
    return True


def test_event_driven_flow():
    """Test 3: Verify segment_finalized and tts_ready events."""
    slog('info', 'test_start', test='event_driven_flow', session_id=SESSION_ID)
    
    # Note: This test assumes you'll speak real words during the test
    # We can't generate valid speech audio programmatically here
    
    slog('warn', 'manual_test_required',
         note='Speak 2-3 short sentences into your microphone during the next 30 seconds')
    slog('info', 'waiting_for_speech', duration_seconds=30)
    
    time.sleep(30)
    
    # Check trace logs for segment_finalized and tts_ready events
    resp = requests.get(f'{AI_SERVICE_URL}/debug/trace/{TRACE_ID}')
    
    if resp.status_code != 200:
        slog('error', 'trace_fetch_fail', status=resp.status_code)
        return False
    
    trace_data = resp.json()
    logs = trace_data.get('logs', [])
    
    # Count events
    segment_finalized_count = sum(1 for log in logs if log.get('step') == 'stt')
    tts_count = sum(1 for log in logs if log.get('step') == 'tts')
    
    slog('info', 'event_summary',
         segment_finalized_count=segment_finalized_count,
         tts_count=tts_count,
         total_logs=len(logs))
    
    if segment_finalized_count == 0:
        slog('warn', 'no_segments',
             note='No segment_finalized events detected - either no speech or recognition failed')
    
    if tts_count == 0:
        slog('warn', 'no_tts',
             note='No TTS events detected - check if active languages configured')
    
    return segment_finalized_count > 0


def test_cleanup():
    """Test 4: Clean session shutdown."""
    slog('info', 'test_start', test='cleanup', session_id=SESSION_ID)
    
    # End AI session
    resp = requests.post(f'{AI_SERVICE_URL}/end-session', json={
        'sessionId': SESSION_ID,
        'traceId': TRACE_ID
    }, timeout=10)
    
    if resp.status_code != 200:
        slog('error', 'end_session_fail', status=resp.status_code, body=resp.text)
        return False
    
    slog('info', 'session_ended', session_id=SESSION_ID)
    return True


def test_metrics():
    """Test 5: Check metrics for errors."""
    slog('info', 'test_start', test='metrics')
    
    resp = requests.get(f'{AI_SERVICE_URL}/metrics')
    
    if resp.status_code != 200:
        slog('error', 'metrics_fetch_fail', status=resp.status_code)
        return False
    
    metrics = resp.json()
    
    slog('info', 'metrics_snapshot', **metrics)
    
    # Check for errors
    errors_total = metrics.get('errors_total', 0)
    if errors_total > 0:
        slog('warn', 'errors_detected', errors_total=errors_total)
    
    return True


def main():
    """Run all tests."""
    slog('info', 'test_suite_start', 
         test='event_driven_streaming',
         session_id=SESSION_ID,
         trace_id=TRACE_ID)
    
    results = {}
    
    # Run tests in sequence
    tests = [
        ('session_lifecycle', test_session_lifecycle),
        ('audio_streaming', test_audio_streaming),
        ('event_driven_flow', test_event_driven_flow),
        ('cleanup', test_cleanup),
        ('metrics', test_metrics)
    ]
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results[test_name] = 'PASS' if result else 'FAIL'
        except Exception as e:
            slog('error', 'test_exception', test=test_name, error=str(e))
            results[test_name] = 'ERROR'
    
    # Summary
    slog('info', 'test_suite_complete', results=results)
    
    passed = sum(1 for r in results.values() if r == 'PASS')
    total = len(results)
    
    if passed == total:
        slog('info', 'all_tests_passed', passed=passed, total=total)
        print('\n✓ ALL TESTS PASSED')
        return 0
    else:
        slog('error', 'some_tests_failed', passed=passed, total=total)
        print(f'\n✗ {total - passed} TESTS FAILED')
        return 1


if __name__ == '__main__':
    exit(main())
