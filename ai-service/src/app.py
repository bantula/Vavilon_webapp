from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import time
import base64
import threading
import traceback
import uuid
from collections import deque
from dotenv import load_dotenv
try:
    from speech_service import TranslationSession
except ImportError:
    from src.speech_service import TranslationSession

load_dotenv()

app = Flask(__name__)
CORS(app)

AZURE_SPEECH_KEY = os.getenv('AZURE_SPEECH_KEY')
AZURE_SPEECH_REGION = os.getenv('AZURE_SPEECH_REGION')
NODE_BACKEND_URL = os.getenv('NODE_BACKEND_URL', 'http://localhost:3000')
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# ── Structured logging ──────────────────────────────────────────────

def slog(level, step, **kwargs):
    entry = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'level': level,
        'component': 'python',
        'step': step,
        **kwargs
    }
    print(json.dumps(entry), flush=True)

# ── Metrics ─────────────────────────────────────────────────────────

_metrics = {
    'stt_calls': 0,
    'translate_calls': 0,
    'tts_calls': 0,
    'errors_total': 0,
    'stt_latencies': deque(maxlen=100),
    'tts_latencies': deque(maxlen=100),
}
_metrics_lock = threading.Lock()

def inc_metric(key, n=1):
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + n

def push_latency(key, ms):
    with _metrics_lock:
        _metrics[key].append(ms)

# ── Trace log ring buffer per trace_id ──────────────────────────────

_trace_logs = {}  # trace_id -> deque(maxlen=200)
_trace_lock = threading.Lock()

def trace_log(trace_id, entry):
    if not trace_id:
        return
    with _trace_lock:
        if trace_id not in _trace_logs:
            _trace_logs[trace_id] = deque(maxlen=200)
        _trace_logs[trace_id].append(entry)


# Active translation sessions
sessions: dict[str, TranslationSession] = {}


if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
    slog('error', 'startup', msg='AZURE_SPEECH_KEY and AZURE_SPEECH_REGION not set!')


# ── Routes ──────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'service': 'vavilon-ai-service',
        'azure_configured': bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION),
        'active_sessions': len(sessions),
        'debug': DEBUG
    })


@app.route('/start-session', methods=['POST'])
def start_session():
    t0 = time.time()
    try:
        data = request.json
        session_id = data.get('sessionId')
        trace_id = data.get('traceId')
        source_language = data.get('sourceLanguage', 'en-US')
        target_languages = data.get('targetLanguages', ['es', 'fr', 'de'])

        if not session_id:
            return jsonify({'error': 'Missing sessionId'}), 400

        if not source_language:
            return jsonify({'error': 'Missing sourceLanguage'}), 400

        if not target_languages or len(target_languages) == 0:
            return jsonify({'error': 'Missing or empty targetLanguages — '
                           'at least one target language is required'}), 400

        slog('info', 'start_session', session_id=session_id, trace_id=trace_id,
             source=source_language, targets=target_languages,
             target_count=len(target_languages),
             azure_key_set=bool(AZURE_SPEECH_KEY),
             azure_region=AZURE_SPEECH_REGION)

        # Stop existing session if any
        if session_id in sessions:
            sessions[session_id].stop()
            del sessions[session_id]

        session = TranslationSession(
            session_id=session_id,
            trace_id=trace_id,
            speech_key=AZURE_SPEECH_KEY,
            region=AZURE_SPEECH_REGION,
            source_language=source_language,
            target_languages=target_languages,
            node_backend_url=NODE_BACKEND_URL,
            debug=DEBUG,
            metric_fn=inc_metric,
            latency_fn=push_latency,
            trace_log_fn=trace_log,
        )
        session.start()
        sessions[session_id] = session

        elapsed = int((time.time() - t0) * 1000)
        slog('info', 'session_started', session_id=session_id, trace_id=trace_id,
             elapsed_ms=elapsed)

        return jsonify({
            'success': True,
            'sessionId': session_id,
            'traceId': trace_id,
            'sourceLanguage': source_language,
            'targetLanguages': target_languages,
            'debug': DEBUG
        })

    except Exception as e:
        inc_metric('errors_total')
        slog('error', 'start_session_fail', error=str(e), error_type=type(e).__name__)
        return jsonify({'error': str(e)}), 500


@app.route('/process-audio', methods=['POST'])
def process_audio():
    try:
        data = request.json
        session_id = data.get('sessionId')
        trace_id = data.get('traceId')
        seq_no = data.get('seqNo', 0)
        audio_data_b64 = data.get('audioData')

        if not session_id or not audio_data_b64:
            return jsonify({'error': 'Missing sessionId or audioData'}), 400

        session = sessions.get(session_id)
        if not session:
            return jsonify({'error': 'Session not found. Call /start-session first.'}), 404

        audio_bytes = base64.b64decode(audio_data_b64)
        n_bytes = len(audio_bytes)

        # Validate PCM16 = even number of bytes
        if n_bytes % 2 != 0:
            inc_metric('errors_total')
            slog('warn', 'audio_odd_bytes', session_id=session_id, trace_id=trace_id,
                 seq_no=seq_no, bytes=n_bytes)

        # Save raw chunk for debugging
        if DEBUG:
            _save_debug_chunk(trace_id, seq_no, audio_bytes)

        # Log every 50th chunk
        if seq_no % 50 == 1:
            slog('debug', 'process_audio', session_id=session_id, trace_id=trace_id,
                 seq_no=seq_no, bytes=n_bytes,
                 sample_rate=16000, encoding='pcm16', channels=1)
            trace_log(trace_id, {
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'step': 'receive',
                'seq_no': seq_no,
                'bytes': n_bytes
            })

        success = session.push_audio(audio_bytes)

        if not success or not session.alive:
            # Session died (audio writer stuck, recognizer crashed)
            sessions.pop(session_id, None)
            return jsonify({'error': 'Session expired or recognizer died',
                            'code': 'SESSION_DEAD'}), 410

        return jsonify({'success': True, 'bytes': n_bytes, 'seqNo': seq_no})

    except Exception as e:
        inc_metric('errors_total')
        slog('error', 'process_audio_fail', error=str(e), error_type=type(e).__name__)
        return jsonify({'error': str(e)}), 500


@app.route('/generate-tts', methods=['POST'])
def generate_tts():
    """
    Node sends explicit translations to synthesize after receiving segment_finalized.
    No global active-language state — Node decides which languages need TTS.
    """
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


@app.route('/end-session', methods=['POST'])
def end_session():
    """
    End a translation session. Stops in background thread to return immediately.
    
    Request body:
        {
            "sessionId": "...",
            "traceId": "...",
            "graceful": true/false  [optional, default: false]
        }
    
    If graceful=true: Closes audio stream first, waits for Azure to finalize 
                      pending segments, then stops recognizer. Ensures 100% of 
                      audio is processed. Adds ~2 seconds to stop time.
    If graceful=false: Stops immediately (legacy behavior).
    """
    try:
        data = request.json
        session_id = data.get('sessionId')
        trace_id = data.get('traceId')
        graceful = data.get('graceful', False)

        if session_id in sessions:
            session = sessions.pop(session_id)
            slog('info', 'session_ending', session_id=session_id, trace_id=trace_id,
                 graceful=graceful,
                 note='Graceful stop (close stream first)' if graceful else 'Stopping session in background thread')
            # Stop in background — don't block the HTTP response
            # Pass graceful parameter to stop method
            threading.Thread(target=lambda: session.stop(graceful=graceful), daemon=True).start()

        return jsonify({'success': True, 'graceful': graceful})

    except Exception as e:
        inc_metric('errors_total')
        slog('error', 'end_session_fail', error=str(e))
        return jsonify({'error': str(e)}), 500


# ── Debug endpoints ─────────────────────────────────────────────────

@app.route('/debug/trace/<trace_id>', methods=['GET'])
def get_trace(trace_id):
    with _trace_lock:
        logs = list(_trace_logs.get(trace_id, []))
    n = request.args.get('n', 50, type=int)
    return jsonify({'traceId': trace_id, 'count': len(logs), 'logs': logs[-n:]})


@app.route('/sessions', methods=['GET'])
def list_sessions():
    """List active session IDs — used for zombie detection and observability."""
    return jsonify({
        'count': len(sessions),
        'session_ids': list(sessions.keys())
    })


@app.route('/routes', methods=['GET'])
def list_routes():
    """List all registered routes for quick verification."""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': sorted(list(rule.methods - {'OPTIONS', 'HEAD'})),
            'path': str(rule)
        })
    return jsonify({
        'service': 'vavilon-ai-service',
        'routes': sorted(routes, key=lambda r: r['path'])
    })


@app.route('/metrics', methods=['GET'])
def get_metrics():
    with _metrics_lock:
        stt_lats = list(_metrics['stt_latencies'])
        tts_lats = list(_metrics['tts_latencies'])
    return jsonify({
        'stt_calls': _metrics.get('stt_calls', 0),
        'translate_calls': _metrics.get('translate_calls', 0),
        'tts_calls': _metrics.get('tts_calls', 0),
        'errors_total': _metrics.get('errors_total', 0),
        'avg_stt_latency_ms': round(sum(stt_lats) / len(stt_lats), 1) if stt_lats else 0,
        'avg_tts_latency_ms': round(sum(tts_lats) / len(tts_lats), 1) if tts_lats else 0,
        'active_sessions': len(sessions),
    })


# ── Debug helpers ───────────────────────────────────────────────────

def _save_debug_chunk(trace_id, seq_no, audio_bytes):
    if not trace_id:
        return
    try:
        trace_dir = f'/tmp/vavilon_traces/{trace_id}'
        os.makedirs(trace_dir, exist_ok=True)
        path = f'{trace_dir}/chunk_{seq_no:06d}.raw'
        with open(path, 'wb') as f:
            f.write(audio_bytes)
    except Exception as e:
        slog('warn', 'debug_save_fail', error=str(e))


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    slog('info', 'startup', port=port,
         azure_configured=bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION),
         node_backend=NODE_BACKEND_URL, debug=DEBUG)
    app.run(host='0.0.0.0', port=port, debug=False)
