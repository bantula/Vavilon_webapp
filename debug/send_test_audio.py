#!/usr/bin/env python3
"""
Vavilon debug tool — send a WAV file through the AI service pipeline.

Usage:
    python debug/send_test_audio.py [options]

    --wav PATH          Path to a 16kHz mono WAV file (default: generates a 1kHz sine tone)
    --ai-url URL        AI service base URL (default: http://localhost:5000)
    --session-id ID     Override session ID (default: test-<timestamp>)
    --source-lang LANG  Source language (default: en-US)
    --targets LANGS     Comma-separated target languages (default: es,fr)
    --chunk-ms MS       Chunk duration in milliseconds (default: 200)
    --pause SEC         Pause between chunks in seconds (default: 0.02)
    --no-cleanup        Don't call /end-session when done
"""

import argparse
import base64
import io
import json
import math
import os
import struct
import sys
import time
import uuid
import wave

import requests


def generate_sine_wav(duration_s=3.0, freq_hz=440.0, sample_rate=16000):
    """Generate a 16kHz 16-bit mono WAV with a sine tone (for testing without a real file)."""
    n_samples = int(sample_rate * duration_s)
    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        val = int(32767 * 0.8 * math.sin(2 * math.pi * freq_hz * t))
        samples.append(struct.pack('<h', val))

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b''.join(samples))
    return buf.getvalue()


def load_wav(path):
    """Load a WAV file and return raw PCM bytes + metadata."""
    with wave.open(path, 'rb') as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        pcm = wf.readframes(n_frames)
    return pcm, channels, sampwidth, framerate


def main():
    parser = argparse.ArgumentParser(description='Vavilon debug: send test audio through the pipeline')
    parser.add_argument('--wav', type=str, default=None, help='Path to 16kHz mono WAV file')
    parser.add_argument('--ai-url', type=str, default='http://localhost:5000', help='AI service URL')
    parser.add_argument('--session-id', type=str, default=None, help='Session ID')
    parser.add_argument('--source-lang', type=str, default='en-US', help='Source language')
    parser.add_argument('--targets', type=str, default='es,fr', help='Comma-separated target languages')
    parser.add_argument('--chunk-ms', type=int, default=200, help='Chunk size in ms')
    parser.add_argument('--pause', type=float, default=0.02, help='Pause between chunks (seconds)')
    parser.add_argument('--no-cleanup', action='store_true', help='Skip /end-session call')
    args = parser.parse_args()

    ai_url = args.ai_url.rstrip('/')
    session_id = args.session_id or f'test-{int(time.time())}'
    trace_id = f'dbg-{uuid.uuid4().hex[:12]}'
    targets = [t.strip() for t in args.targets.split(',')]

    print(f'=== Vavilon Pipeline Test ===')
    print(f'AI service:     {ai_url}')
    print(f'Session ID:     {session_id}')
    print(f'Trace ID:       {trace_id}')
    print(f'Source lang:    {args.source_lang}')
    print(f'Target langs:   {targets}')
    print()

    # ── Step 1: Health check ─────────────────────────────────────────
    print('[1] Health check...')
    try:
        r = requests.get(f'{ai_url}/health', timeout=5)
        health = r.json()
        print(f'    Status: {health.get("status")} | Azure configured: {health.get("azure_configured")} | Debug: {health.get("debug")}')
        if not health.get('azure_configured'):
            print('    WARNING: Azure Speech not configured — STT/TTS will fail!')
    except Exception as e:
        print(f'    FAILED: {e}')
        print('    Is the AI service running?')
        sys.exit(1)

    # ── Step 2: Load or generate audio ───────────────────────────────
    print('[2] Loading audio...')
    if args.wav:
        if not os.path.exists(args.wav):
            print(f'    File not found: {args.wav}')
            sys.exit(1)
        pcm, channels, sampwidth, framerate = load_wav(args.wav)
        print(f'    Loaded: {args.wav}')
        print(f'    Format: {framerate}Hz, {sampwidth*8}-bit, {channels}ch, {len(pcm)} bytes ({len(pcm)/framerate/sampwidth/channels:.1f}s)')
        if framerate != 16000 or channels != 1 or sampwidth != 2:
            print(f'    WARNING: Expected 16kHz 16-bit mono. Azure may not recognize this format.')
    else:
        print('    No --wav provided, generating 3s 440Hz sine tone...')
        wav_data = generate_sine_wav(duration_s=3.0, freq_hz=440.0)
        # Extract raw PCM (skip WAV header)
        with wave.open(io.BytesIO(wav_data), 'rb') as wf:
            pcm = wf.readframes(wf.getnframes())
        framerate = 16000
        sampwidth = 2
        channels = 1
        print(f'    Generated: {len(pcm)} bytes ({len(pcm)/framerate/sampwidth:.1f}s)')
        print('    NOTE: Sine tone won\'t produce speech recognition — use a real WAV for STT testing.')

    # ── Step 3: Start session ────────────────────────────────────────
    print('[3] Starting session...')
    try:
        r = requests.post(f'{ai_url}/start-session', json={
            'sessionId': session_id,
            'traceId': trace_id,
            'sourceLanguage': args.source_lang,
            'targetLanguages': targets,
        }, timeout=15)
        resp = r.json()
        if r.status_code == 200 and resp.get('success'):
            print(f'    Session started. Debug: {resp.get("debug")}')
        else:
            print(f'    FAILED ({r.status_code}): {resp}')
            sys.exit(1)
    except Exception as e:
        print(f'    FAILED: {e}')
        sys.exit(1)

    # ── Step 4: Stream audio chunks ──────────────────────────────────
    chunk_bytes = int(framerate * sampwidth * channels * args.chunk_ms / 1000)
    n_chunks = (len(pcm) + chunk_bytes - 1) // chunk_bytes
    print(f'[4] Streaming {n_chunks} chunks ({args.chunk_ms}ms each, {chunk_bytes} bytes/chunk)...')

    errors = 0
    for seq in range(n_chunks):
        offset = seq * chunk_bytes
        chunk = pcm[offset:offset + chunk_bytes]
        b64 = base64.b64encode(chunk).decode('utf-8')

        try:
            r = requests.post(f'{ai_url}/process-audio', json={
                'sessionId': session_id,
                'traceId': trace_id,
                'seqNo': seq + 1,
                'audioData': b64,
            }, timeout=10)
            if r.status_code != 200:
                errors += 1
                if errors <= 3:
                    print(f'    Chunk {seq+1}: HTTP {r.status_code} — {r.text[:100]}')
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f'    Chunk {seq+1}: ERROR — {e}')

        if (seq + 1) % 25 == 0 or seq == n_chunks - 1:
            print(f'    Sent {seq+1}/{n_chunks} chunks')

        time.sleep(args.pause)

    print(f'    Done. Errors: {errors}/{n_chunks}')

    # ── Step 5: Wait for pipeline to process ─────────────────────────
    print('[5] Waiting 5s for Azure STT + Translation + TTS to process...')
    time.sleep(5)

    # ── Step 6: Check trace & metrics ────────────────────────────────
    print('[6] Checking trace and metrics...')

    try:
        r = requests.get(f'{ai_url}/debug/trace/{trace_id}?n=100', timeout=5)
        trace = r.json()
        logs = trace.get('logs', [])
        print(f'    Trace entries: {trace.get("count", 0)}')

        stt_entries = [l for l in logs if l.get('step') == 'stt']
        translate_entries = [l for l in logs if l.get('step') == 'translate']
        tts_entries = [l for l in logs if l.get('step') == 'tts']
        broadcast_entries = [l for l in logs if l.get('step') == 'broadcast_audio']
        cancel_entries = [l for l in logs if l.get('step') == 'canceled']

        print(f'    STT recognitions:  {len(stt_entries)}')
        for e in stt_entries[:3]:
            print(f'      → "{e.get("text", "")}"')

        print(f'    Translations:      {len(translate_entries)}')
        for e in translate_entries[:5]:
            print(f'      → [{e.get("language")}] "{e.get("text", "")}"')

        print(f'    TTS completions:   {len(tts_entries)}')
        for e in tts_entries[:5]:
            print(f'      → [{e.get("language")}] {e.get("bytes", 0)} bytes, {e.get("elapsed_ms", 0)}ms')

        print(f'    Audio broadcasts:  {len(broadcast_entries)}')
        for e in broadcast_entries[:5]:
            print(f'      → [{e.get("language")}] status={e.get("status")} b64_len={e.get("b64_len", 0)}')

        if cancel_entries:
            print(f'    CANCELLATIONS:     {len(cancel_entries)}')
            for e in cancel_entries:
                print(f'      → reason={e.get("reason")} details={e.get("details", "")[:100]}')

    except Exception as e:
        print(f'    Trace fetch failed: {e}')

    try:
        r = requests.get(f'{ai_url}/metrics', timeout=5)
        m = r.json()
        print(f'\n    Metrics:')
        print(f'      STT calls:       {m.get("stt_calls", 0)}')
        print(f'      Translate calls:  {m.get("translate_calls", 0)}')
        print(f'      TTS calls:       {m.get("tts_calls", 0)}')
        print(f'      Errors total:    {m.get("errors_total", 0)}')
        print(f'      Avg STT latency: {m.get("avg_stt_latency_ms", 0)}ms')
        print(f'      Avg TTS latency: {m.get("avg_tts_latency_ms", 0)}ms')
    except Exception as e:
        print(f'    Metrics fetch failed: {e}')

    # ── Step 7: Verdict ──────────────────────────────────────────────
    print('\n[7] Verdict:')
    all_ok = True

    if len(stt_entries) > 0:
        print('    ✓ STT recognized speech')
    else:
        print('    ✗ STT: No recognition events')
        all_ok = False

    if len(translate_entries) > 0:
        print('    ✓ Translations produced')
    else:
        print('    ✗ Translation: No translation events')
        all_ok = False

    if len(tts_entries) > 0:
        has_audio = any(e.get('bytes', 0) > 0 for e in tts_entries)
        if has_audio:
            print('    ✓ TTS audio bytes > 0')
        else:
            print('    ✗ TTS: All entries have 0 bytes')
            all_ok = False
    else:
        print('    ✗ TTS: No synthesis events')
        all_ok = False

    if len(broadcast_entries) > 0:
        ok_broadcasts = [e for e in broadcast_entries if e.get('status') == 200]
        print(f'    {"✓" if ok_broadcasts else "✗"} Broadcast: {len(ok_broadcasts)}/{len(broadcast_entries)} successful')
        if not ok_broadcasts:
            all_ok = False
    else:
        print('    ✗ Broadcast: No broadcast events')
        all_ok = False

    if cancel_entries:
        print(f'    ✗ Cancellations detected — check Azure key/region')
        all_ok = False

    print(f'\n    {"PASS — full pipeline working!" if all_ok else "FAIL — see above for issues"}')

    # ── Cleanup ──────────────────────────────────────────────────────
    if not args.no_cleanup:
        print('\n[8] Ending session...')
        try:
            r = requests.post(f'{ai_url}/end-session', json={
                'sessionId': session_id,
                'traceId': trace_id,
            }, timeout=10)
            print(f'    {r.json()}')
        except Exception as e:
            print(f'    Failed: {e}')

    print(f'\nTrace ID for further inspection: {trace_id}')
    print(f'  curl {ai_url}/debug/trace/{trace_id}')


if __name__ == '__main__':
    main()
