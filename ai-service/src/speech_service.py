import azure.cognitiveservices.speech as speechsdk
import base64
import queue
import threading
import requests
import time
from typing import Dict, Set


class TranslationSession:
    """
    A persistent translation session that mirrors dubber.py's architecture:
    - PushAudioInputStream for streaming audio from the browser
    - TranslationRecognizer with continuous recognition (STT + Translation in one step)
    - SpeechSynthesizer per target language for TTS
    """

    # Language codes for Azure Speech Translation (BCP-47 for translation targets)
    TRANSLATION_LANG_MAP = {
        'en': 'en',
        'es': 'es',
        'fr': 'fr',
        'de': 'de',
        'it': 'it',
        'pt': 'pt',
        'ru': 'ru',
        'zh': 'zh-Hans',
        'ja': 'ja',
        'ar': 'ar'
    }

    # Full locale codes for TTS synthesis
    TTS_LOCALE_MAP = {
        'en': 'en-US',
        'es': 'es-ES',
        'fr': 'fr-FR',
        'de': 'de-DE',
        'it': 'it-IT',
        'pt': 'pt-PT',
        'ru': 'ru-RU',
        'zh': 'zh-CN',
        'ja': 'ja-JP',
        'ar': 'ar-SA'
    }

    def __init__(self, session_id: str, speech_key: str, region: str,
                 source_language: str, target_languages: list,
                 node_backend_url: str):
        self.session_id = session_id
        self.speech_key = speech_key
        self.region = region
        self.source_language = source_language
        self.target_languages = set(target_languages)
        self.node_backend_url = node_backend_url

        self._stop_event = threading.Event()

        # Audio input stream that we push browser audio into
        self._audio_stream = speechsdk.audio.PushAudioInputStream(
            stream_format=speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
        )

        # Translation recognizer (STT + Translation combined, like dubber.py)
        self._translation_recognizer = None

        # Per-language text queues and synthesizers (mirrors dubber.py's pattern)
        self._translated_text_queues: Dict[str, queue.Queue] = {}
        self._synthesizers: Dict[str, speechsdk.SpeechSynthesizer] = {}
        self._synth_threads: Dict[str, dict] = {}

        self._setup_recognizer()
        self._setup_synthesizers()

    def _setup_recognizer(self):
        """Setup TranslationRecognizer with continuous recognition - same pattern as dubber.py"""
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=self.speech_key,
            region=self.region
        )
        translation_config.speech_recognition_language = self.source_language

        # Add target languages for translation (like dubber.py line 612-613)
        for lang in self.target_languages:
            trans_code = self.TRANSLATION_LANG_MAP.get(lang, lang)
            translation_config.add_target_language(trans_code)

        # Segmentation silence timeout for responsive recognition
        translation_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "500"
        )

        audio_config = speechsdk.audio.AudioConfig(stream=self._audio_stream)

        self._translation_recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config
        )

        # Connect callbacks (same pattern as dubber.py lines 732-760)
        self._translation_recognizer.recognized.connect(self._on_recognized)
        self._translation_recognizer.canceled.connect(self._on_canceled)
        self._translation_recognizer.session_stopped.connect(self._on_session_stopped)

    def _setup_synthesizers(self):
        """Create one SpeechSynthesizer per target language - same pattern as dubber.py lines 219-227"""
        for lang in self.target_languages:
            self._translated_text_queues[lang] = queue.Queue()

            speech_config = speechsdk.SpeechConfig(
                subscription=self.speech_key,
                region=self.region
            )
            locale = self.TTS_LOCALE_MAP.get(lang, 'en-US')
            speech_config.speech_synthesis_language = locale
            # audio_config=None means we get raw audio bytes back (dubber.py line 223)
            self._synthesizers[lang] = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=None
            )

            self._synth_threads[lang] = {
                "thread": threading.Thread(
                    target=self._voice_synth,
                    args=(lang,),
                    daemon=True
                ),
                "running": True
            }
            self._synth_threads[lang]["thread"].start()

    def start(self):
        """Start continuous recognition - mirrors dubber.py line 763"""
        self._translation_recognizer.start_continuous_recognition_async().get()
        print(f"[Session {self.session_id}] Continuous translation started")

    def push_audio(self, audio_bytes: bytes):
        """Push PCM audio data into the stream for recognition"""
        self._audio_stream.write(audio_bytes)

    def stop(self):
        """Stop the session and clean up resources"""
        print(f"[Session {self.session_id}] Stopping...")
        self._stop_event.set()

        # Stop synthesis threads
        for lang, info in self._synth_threads.items():
            info["running"] = False

        # Stop continuous recognition (dubber.py line 772)
        if self._translation_recognizer:
            try:
                self._translation_recognizer.stop_continuous_recognition_async().get()
            except Exception as e:
                print(f"[Session {self.session_id}] Error stopping recognizer: {e}")

        # Close audio stream
        try:
            self._audio_stream.close()
        except Exception:
            pass

        # Wait for synth threads
        for lang, info in self._synth_threads.items():
            if info["thread"].is_alive():
                info["thread"].join(timeout=2.0)

        print(f"[Session {self.session_id}] Stopped")

    def _on_recognized(self, evt):
        """
        Callback when speech is recognized AND translated.
        This is the key insight from dubber.py (lines 732-746):
        evt.result.translations already contains all translated text!
        No separate translation step needed.
        """
        if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
            source_text = evt.result.text
            if not source_text.strip():
                return

            print(f"[Session {self.session_id}] Recognized: {source_text}")

            # Enqueue translated text for each language (dubber.py lines 741-743)
            for lang in self.target_languages:
                trans_code = self.TRANSLATION_LANG_MAP.get(lang, lang)
                if trans_code in evt.result.translations:
                    translated = evt.result.translations[trans_code]
                    print(f"[Session {self.session_id}] -> {lang}: {translated}")
                    self._translated_text_queues[lang].put(translated)

                    # Immediately broadcast the subtitle text (no need to wait for TTS)
                    self._broadcast_subtitle(lang, translated)

    def _on_canceled(self, evt):
        print(f"[Session {self.session_id}] Recognition canceled: {evt.reason}")
        if evt.reason == speechsdk.CancellationReason.Error:
            print(f"[Session {self.session_id}] Error details: {evt.error_details}")

    def _on_session_stopped(self, evt):
        print(f"[Session {self.session_id}] Recognition session stopped")

    def _voice_synth(self, language):
        """
        Synthesize translated text to audio - directly from dubber.py lines 866-933.
        Pulls text from the queue, synthesizes, and broadcasts the audio.
        """
        while self._synth_threads[language]["running"]:
            try:
                text = self._translated_text_queues[language].get(timeout=0.5)
            except queue.Empty:
                continue

            # Synthesize speech (dubber.py line 899)
            result = self._synthesizers[language].speak_text_async(text).get()

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_bytes = result.audio_data
                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                self._broadcast_audio(language, audio_b64)
                print(f"[Session {self.session_id}] Synthesized {language}: {len(audio_bytes)} bytes")
            else:
                print(f"[Session {self.session_id}] Synthesis failed for {language}: {result.reason}")
                if result.reason == speechsdk.ResultReason.Canceled:
                    cancellation = result.cancellation_details
                    print(f"[Session {self.session_id}] Cancellation reason: {cancellation.reason}")
                    if cancellation.reason == speechsdk.CancellationReason.Error:
                        print(f"[Session {self.session_id}] Error: {cancellation.error_details}")

            self._translated_text_queues[language].task_done()

    def _broadcast_subtitle(self, language, text):
        """Send subtitle text to Node.js backend for broadcasting to listeners"""
        try:
            requests.post(
                f'{self.node_backend_url}/api/broadcast',
                json={
                    'sessionId': self.session_id,
                    'language': language,
                    'subtitleText': text
                },
                timeout=5
            )
        except Exception as e:
            print(f"[Session {self.session_id}] Error broadcasting subtitle: {e}")

    def _broadcast_audio(self, language, audio_b64):
        """Send synthesized audio to Node.js backend for broadcasting to listeners"""
        try:
            requests.post(
                f'{self.node_backend_url}/api/broadcast',
                json={
                    'sessionId': self.session_id,
                    'language': language,
                    'audioData': audio_b64
                },
                timeout=10
            )
        except Exception as e:
            print(f"[Session {self.session_id}] Error broadcasting audio: {e}")
