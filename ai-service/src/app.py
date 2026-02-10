from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import base64
import requests
from dotenv import load_dotenv
from speech_service import SpeechTranslationService

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Initialize Azure Speech Service
AZURE_SPEECH_KEY = os.getenv('AZURE_SPEECH_KEY')
AZURE_SPEECH_REGION = os.getenv('AZURE_SPEECH_REGION')
NODE_BACKEND_URL = os.getenv('NODE_BACKEND_URL', 'http://localhost:3000')

if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
    print("WARNING: Azure Speech credentials not set. Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION environment variables.")

speech_service = SpeechTranslationService(AZURE_SPEECH_KEY, AZURE_SPEECH_REGION)

# Track active sessions
active_sessions = {}


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'service': 'vavilon-ai-service',
        'azure_configured': bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)
    })


@app.route('/process-audio', methods=['POST'])
def process_audio():
    """
    Receive audio from Node backend, perform STT + Translation + TTS
    Then send results back to Node backend for broadcasting
    """
    try:
        data = request.json
        session_id = data.get('sessionId')
        audio_data_b64 = data.get('audioData')

        if not session_id or not audio_data_b64:
            return jsonify({'error': 'Missing sessionId or audioData'}), 400

        # Decode base64 audio
        audio_bytes = base64.b64decode(audio_data_b64)

        # Process audio through Azure Speech SDK
        # STT -> Translation -> TTS
        result = speech_service.process_audio_stream(audio_bytes, session_id)

        if result['success']:
            # Send translated audio and subtitles back to Node backend
            broadcast_translations(session_id, result['translations'])

            return jsonify({
                'success': True,
                'message': 'Audio processed',
                'languages': list(result['translations'].keys())
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Processing failed')
            }), 500

    except Exception as e:
        print(f"Error processing audio: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/start-session', methods=['POST'])
def start_session():
    """
    Initialize a translation session with target languages
    """
    try:
        data = request.json
        session_id = data.get('sessionId')
        source_language = data.get('sourceLanguage', 'en-US')
        target_languages = data.get('targetLanguages', ['es', 'fr', 'de'])

        if not session_id:
            return jsonify({'error': 'Missing sessionId'}), 400

        active_sessions[session_id] = {
            'sourceLanguage': source_language,
            'targetLanguages': target_languages
        }

        return jsonify({
            'success': True,
            'sessionId': session_id,
            'sourceLanguage': source_language,
            'targetLanguages': target_languages
        })

    except Exception as e:
        print(f"Error starting session: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/end-session', methods=['POST'])
def end_session():
    """
    End a translation session
    """
    try:
        data = request.json
        session_id = data.get('sessionId')

        if session_id in active_sessions:
            del active_sessions[session_id]

        return jsonify({
            'success': True,
            'message': 'Session ended'
        })

    except Exception as e:
        print(f"Error ending session: {str(e)}")
        return jsonify({'error': str(e)}), 500


def broadcast_translations(session_id, translations):
    """
    Send translated audio and subtitles back to Node backend
    Node backend will broadcast to appropriate listeners
    """
    try:
        for language, translation_data in translations.items():
            # Call Node backend webhook to broadcast
            requests.post(
                f'{NODE_BACKEND_URL}/api/broadcast',
                json={
                    'sessionId': session_id,
                    'language': language,
                    'audioData': translation_data['audioData'],
                    'subtitleText': translation_data['text']
                },
                timeout=5
            )
    except Exception as e:
        print(f"Error broadcasting translations: {str(e)}")


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"✓ Vavilon AI Service starting on port {port}")
    print(f"✓ Azure Speech configured: {bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)}")
    app.run(host='0.0.0.0', port=port, debug=True)