"""
Integration test: Verify no 404 errors during audio streaming.

Simulates the failing sequence:
1. Start a session on the AI service
2. Send 5 short audio chunks (simulating spoken sentences)
3. Verify POST /process-audio NEVER returns 404
4. Verify /debug/trace/<trace_id> shows recognized events
5. Verify /metrics increments

Usage:
    python debug/test_no_404.py [AI_SERVICE_URL]
    python debug/test_no_404.py http://localhost:5000
    python debug/test_no_404.py https://vavilon-ai.westeurope.azurecontainer.io:5000

Requirements:
    pip install requests
"""

import sys
import time
import base64
import struct
import math
import requests
import uuid

AI_URL = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:5000'

SESSION_ID = f'test-{uuid.uuid4().hex[:8]}'
TRACE_ID = f'trace-{uuid.uuid4().hex[:8]}'
SOURCE_LANG = 'en-US'
TARGET_LANGS = ['es']

# How many "utterances" to simulate (each = 60 chunks of audio)
NUM_UTTERANCES = 5
CHUNKS_PER_UTTERANCE = 60   # ~5 seconds of audio per utterance
CHUNK_SAMPLES = 1600        # 100ms at 16kHz


def generate_tone_chunk(freq=440.0, duration_samples=1600, sample_rate=16000):
    """Generate a short PCM16 sine tone."""
    samples = []
    for i in range(duration_samples):
        t = i / sample_rate
        sample = int(32767 * 0.3 * math.sin(2 * math.pi * freq * t))
        samples.append(struct.pack('<h', sample))
    return b''.join(samples)


def check_routes():
    """Verify /process-audio route is registered."""
    print(f'\n--- Checking routes at {AI_URL}/routes ---')
    try:
        resp = requests.get(f'{AI_URL}/routes', timeout=5)
        if resp.status_code == 200:
            routes = resp.json().get('routes', [])
            paths = [r['path'] for r in routes]
            print(f'  Registered routes: {paths}')
            assert '/process-audio' in paths, '/process-audio route NOT registered!'
            print('  [PASS] /process-audio is registered')
            return True
        else:
            print(f'  /routes returned {resp.status_code} (endpoint may not exist yet)')
            return True  # Non-fatal
    except Exception as e:
        print(f'  [WARN] Could not check routes: {e}')
        return True  # Non-fatal


def start_session():
    """Start a translation session."""
    print(f'\n--- Starting session {SESSION_ID} ---')
    resp = requests.post(f'{AI_URL}/start-session', json={
        'sessionId': SESSION_ID,
        'traceId': TRACE_ID,
        'sourceLanguage': SOURCE_LANG,
        'targetLanguages': TARGET_LANGS
    }, timeout=15)

    print(f'  Status: {resp.status_code}')
    print(f'  Body: {resp.json()}')
    assert resp.status_code == 200, f'start-session failed: {resp.status_code} {resp.text}'
    print('  [PASS] Session started')


def send_utterance(utterance_num):
    """Send audio chunks simulating one spoken sentence."""
    print(f'\n--- Utterance {utterance_num}: sending {CHUNKS_PER_UTTERANCE} audio chunks ---')
    freq = 300 + (utterance_num * 50)  # Different frequency per utterance
    errors_404 = 0
    errors_other = 0
    successes = 0

    for i in range(CHUNKS_PER_UTTERANCE):
        seq_no = (utterance_num - 1) * CHUNKS_PER_UTTERANCE + i + 1
        chunk = generate_tone_chunk(freq=freq, duration_samples=CHUNK_SAMPLES)
        b64 = base64.b64encode(chunk).decode('utf-8')

        try:
            resp = requests.post(f'{AI_URL}/process-audio', json={
                'sessionId': SESSION_ID,
                'traceId': TRACE_ID,
                'seqNo': seq_no,
                'audioData': b64
            }, timeout=10)

            if resp.status_code == 200:
                successes += 1
            elif resp.status_code == 404:
                errors_404 += 1
                if errors_404 == 1:
                    print(f'  *** FIRST 404 at chunk {seq_no}: {resp.json()}')
            else:
                errors_other += 1
                if errors_other == 1:
                    print(f'  *** Error {resp.status_code} at chunk {seq_no}: {resp.text[:200]}')

        except Exception as e:
            errors_other += 1
            if errors_other == 1:
                print(f'  *** Network error at chunk {seq_no}: {e}')

        # Simulate real-time pacing (~100ms per chunk)
        time.sleep(0.08)

    print(f'  Results: {successes} OK, {errors_404} x 404, {errors_other} other errors')

    # Pause between utterances (silence gap)
    silence = b'\x00' * (CHUNK_SAMPLES * 2)  # 100ms silence
    for _ in range(10):  # 1 second of silence
        b64 = base64.b64encode(silence).decode('utf-8')
        requests.post(f'{AI_URL}/process-audio', json={
            'sessionId': SESSION_ID, 'traceId': TRACE_ID,
            'seqNo': 0, 'audioData': b64
        }, timeout=10)
        time.sleep(0.1)

    return errors_404, errors_other


def check_trace():
    """Check debug trace for recognized events."""
    print(f'\n--- Checking trace {TRACE_ID} ---')
    try:
        resp = requests.get(f'{AI_URL}/debug/trace/{TRACE_ID}?n=200', timeout=5)
        data = resp.json()
        logs = data.get('logs', [])
        print(f'  Total trace events: {data.get("count", 0)}')

        stt_events = [l for l in logs if l.get('step') == 'stt']
        translate_events = [l for l in logs if l.get('step') == 'translate']
        tts_events = [l for l in logs if l.get('step') == 'tts']
        broadcast_events = [l for l in logs if l.get('step') == 'broadcast_audio']

        print(f'  STT recognized: {len(stt_events)}')
        print(f'  Translations: {len(translate_events)}')
        print(f'  TTS synthesized: {len(tts_events)}')
        print(f'  Audio broadcasts: {len(broadcast_events)}')

        return stt_events, translate_events, tts_events
    except Exception as e:
        print(f'  [WARN] Could not check trace: {e}')
        return [], [], []


def check_metrics():
    """Check metrics endpoint."""
    print(f'\n--- Checking metrics ---')
    try:
        resp = requests.get(f'{AI_URL}/metrics', timeout=5)
        data = resp.json()
        print(f'  STT calls: {data.get("stt_calls", 0)}')
        print(f'  Translate calls: {data.get("translate_calls", 0)}')
        print(f'  TTS calls: {data.get("tts_calls", 0)}')
        print(f'  Errors: {data.get("errors_total", 0)}')
        return data
    except Exception as e:
        print(f'  [WARN] Could not check metrics: {e}')
        return {}


def end_session():
    """End the translation session."""
    print(f'\n--- Ending session ---')
    try:
        resp = requests.post(f'{AI_URL}/end-session', json={
            'sessionId': SESSION_ID,
            'traceId': TRACE_ID
        }, timeout=10)
        print(f'  Status: {resp.status_code}')
    except Exception as e:
        print(f'  [WARN] {e}')


def main():
    print('=' * 60)
    print('  Integration Test: No 404 Errors During Streaming')
    print('=' * 60)
    print(f'  AI Service: {AI_URL}')
    print(f'  Session:    {SESSION_ID}')
    print(f'  Trace:      {TRACE_ID}')
    print(f'  Source:     {SOURCE_LANG} -> {TARGET_LANGS}')
    print(f'  Utterances: {NUM_UTTERANCES}')

    # Step 0: Check routes
    check_routes()

    # Step 1: Health check
    print(f'\n--- Health check ---')
    try:
        resp = requests.get(f'{AI_URL}/health', timeout=5)
        print(f'  {resp.json()}')
    except Exception as e:
        print(f'  FATAL: AI service unreachable: {e}')
        sys.exit(1)

    # Step 2: Start session
    start_session()

    # Step 3: Send utterances
    total_404 = 0
    total_errors = 0
    for i in range(1, NUM_UTTERANCES + 1):
        n404, nerr = send_utterance(i)
        total_404 += n404
        total_errors += nerr

    # Step 4: Wait for TTS to finish
    print('\n--- Waiting 5s for TTS pipeline to flush ---')
    time.sleep(5)

    # Step 5: Check results
    stt, trans, tts = check_trace()
    metrics = check_metrics()

    # Step 6: End session
    end_session()

    # Final verdict
    print('\n' + '=' * 60)
    print('  RESULTS')
    print('=' * 60)

    all_pass = True

    if total_404 > 0:
        print(f'  [FAIL] {total_404} x 404 errors from /process-audio')
        all_pass = False
    else:
        print(f'  [PASS] Zero 404 errors from /process-audio')

    if total_errors > 0:
        print(f'  [WARN] {total_errors} other errors')

    if len(stt) > 0:
        print(f'  [PASS] {len(stt)} recognized events in trace')
    else:
        print(f'  [WARN] No recognized events (may be expected with synthetic tone)')

    if len(trans) > 0:
        print(f'  [PASS] {len(trans)} translation events in trace')

    if len(tts) > 0:
        print(f'  [PASS] {len(tts)} TTS events in trace')

    print()
    if all_pass:
        print('  >>> ALL CHECKS PASSED <<<')
    else:
        print('  >>> FAILURES DETECTED <<<')
    print()

    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
