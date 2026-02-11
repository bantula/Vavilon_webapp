from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import base64
from dotenv import load_dotenv
from speech_service import TranslationSession

load_dotenv()

app = Flask(__name__)
CORS(app)

AZURE_SPEECH_KEY = os.getenv('AZURE_SPEECH_KEY')
AZURE_SPEECH_REGION = os.getenv('AZURE_SPEECH_REGION')
NODE_BACKEND_URL = os.getenv('NODE_BACKEND_URL', 'http://localhost:3000')

if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
    print("WARNING: AZURE_SPEECH_KEY and AZURE_SPEECH_REGION not set!")

# Active translation sessions (persistent, not per-request)
sessions: dict[str, TranslationSession] = {}


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'service': 'vavilon-ai-service',
        'azure_configured': bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION),
        'active_sessions': len(sessions)
    })


@app.route('/start-session', methods=['POST'])
def start_session():
    """
    Create a persistent TranslationSession with continuous recognition.
    Called once when the speaker starts speaking.
    """
    try:
        data = request.json
        session_id = data.get('sessionId')
        source_language = data.get('sourceLanguage', 'en-US')
        target_languages = data.get('targetLanguages', ['es', 'fr', 'de'])

        if not session_id:
            return jsonify({'error': 'Missing sessionId'}), 400

        # Stop existing session if any
        if session_id in sessions:
            sessions[session_id].stop()
            del sessions[session_id]

        # Create and start the session
        session = TranslationSession(
            session_id=session_id,
            speech_key=AZURE_SPEECH_KEY,
            region=AZURE_SPEECH_REGION,
            source_language=source_language,
            target_languages=target_languages,
            node_backend_url=NODE_BACKEND_URL
        )
        session.start()
        sessions[session_id] = session

        print(f"Session {session_id} started: {source_language} -> {target_languages}")

        return jsonify({
            'success': True,
            'sessionId': session_id,
            'sourceLanguage': source_language,
            'targetLanguages': target_languages
        })

    except Exception as e:
        print(f"Error starting session: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/process-audio', methods=['POST'])
def process_audio():
    """
    Push audio data into an existing session's PushAudioInputStream.
    Called continuously as the speaker sends audio chunks.
    """
    try:
        data = request.json
        session_id = data.get('sessionId')
        audio_data_b64 = data.get('audioData')

        if not session_id or not audio_data_b64:
            return jsonify({'error': 'Missing sessionId or audioData'}), 400

        session = sessions.get(session_id)
        if not session:
            return jsonify({'error': 'Session not found. Call /start-session first.'}), 404

        # Decode and push PCM audio into the continuous recognition stream
        audio_bytes = base64.b64decode(audio_data_b64)
        session.push_audio(audio_bytes)

        return jsonify({'success': True, 'bytes': len(audio_bytes)})

    except Exception as e:
        print(f"Error processing audio: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/end-session', methods=['POST'])
def end_session():
    """Stop a translation session and clean up resources."""
    try:
        data = request.json
        session_id = data.get('sessionId')

        if session_id in sessions:
            sessions[session_id].stop()
            del sessions[session_id]
            print(f"Session {session_id} ended")

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error ending session: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"Vavilon AI Service on port {port}")
    print(f"Azure configured: {bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)}")
    print(f"Node backend: {NODE_BACKEND_URL}")
    app.run(host='0.0.0.0', port=port, debug=False)
