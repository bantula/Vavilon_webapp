import azure.cognitiveservices.speech as speechsdk
import base64
import html
import json
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import time
import traceback
from typing import Dict, Callable, Optional


def _slog(level, step, **kwargs):
    entry = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'level': level,
        'component': 'azure',
        'step': step,
        **kwargs
    }
    print(json.dumps(entry), flush=True)


class TranslationSession:
    """
    Persistent translation session:
    - PushAudioInputStream for streaming PCM from the browser
    - TranslationRecognizer with continuous recognition (STT + Translation)
    - SpeechSynthesizer per target language for TTS
    """

    TRANSLATION_LANG_MAP = {
        'en': 'en', 'es': 'es', 'fr': 'fr', 'de': 'de', 'it': 'it',
        'pt': 'pt', 'ru': 'ru', 'zh': 'zh-Hans', 'ja': 'ja', 'ar': 'ar'
    }

    TTS_LOCALE_MAP = {
        'en': 'en-US', 'es': 'es-ES', 'fr': 'fr-FR', 'de': 'de-DE',
        'it': 'it-IT', 'pt': 'pt-PT', 'ru': 'ru-RU', 'zh': 'zh-CN',
        'ja': 'ja-JP', 'ar': 'ar-SA'
    }

    # Neural voice names for REST API TTS fallback
    TTS_VOICE_MAP = {
        'en-US': 'en-US-JennyNeural',
        'es-ES': 'es-ES-ElviraNeural',
        'fr-FR': 'fr-FR-DeniseNeural',
        'de-DE': 'de-DE-KatjaNeural',
        'it-IT': 'it-IT-ElsaNeural',
        'pt-PT': 'pt-PT-RaquelNeural',
        'ru-RU': 'ru-RU-SvetlanaNeural',
        'zh-CN': 'zh-CN-XiaoxiaoNeural',
        'ja-JP': 'ja-JP-NanamiNeural',
        'ar-SA': 'ar-SA-ZariyahNeural',
    }

    def __init__(self, session_id: str, trace_id: str,
                 speech_key: str, region: str,
                 source_language: str, target_languages: list,
                 node_backend_url: str,
                 debug: bool = False,
                 metric_fn: Optional[Callable] = None,
                 latency_fn: Optional[Callable] = None,
                 trace_log_fn: Optional[Callable] = None):
        self.session_id = session_id
        self.trace_id = trace_id
        self.speech_key = speech_key
        self.region = region
        self.source_language = source_language
        self.target_languages = set(target_languages)
        self.node_backend_url = node_backend_url
        self.debug = debug
        self._metric = metric_fn or (lambda *a, **kw: None)
        self._latency = latency_fn or (lambda *a, **kw: None)
        self._trace_log = trace_log_fn or (lambda *a, **kw: None)

        self._stop_event = threading.Event()
        self._total_bytes_pushed = 0
        self._recognize_count = 0
        self._push_count = 0
        self._partial_count = 0
        self._use_rest_tts = False  # True = REST API fallback for TTS
        self._recognition_started = False
        self._recognition_stopped = False
        self._stream_closed = False

        # ── Validate inputs (fail loudly) ────────────────────────
        if not self.speech_key:
            raise ValueError(f"[{session_id}] AZURE_SPEECH_KEY is empty — cannot create session")
        if not self.region:
            raise ValueError(f"[{session_id}] AZURE_SPEECH_REGION is empty — cannot create session")
        if not self.target_languages:
            raise ValueError(f"[{session_id}] target_languages is empty — no translation targets provided")
        if not self.source_language:
            raise ValueError(f"[{session_id}] source_language is empty — cannot configure recognizer")

        # Audio input stream: PCM 16kHz 16-bit mono
        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000,
            bits_per_sample=16,
            channels=1
        )
        self._audio_stream = speechsdk.audio.PushAudioInputStream(
            stream_format=audio_format
        )

        self._translation_recognizer = None
        self._translated_text_queues: Dict[str, queue.Queue] = {}
        self._synthesizers: Dict[str, speechsdk.SpeechSynthesizer] = {}
        self._synth_threads: Dict[str, dict] = {}
        # Bounded thread pool for TTS: max 2 concurrent per session
        self._tts_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f'tts-{session_id}')

        self._log('info', 'init',
                  source=source_language,
                  targets=list(self.target_languages),
                  region=region,
                  target_count=len(self.target_languages),
                  tts_max_workers=2)

        self._setup_recognizer()
        self._setup_synthesizers()

    # ── Logging helpers ─────────────────────────────────────────

    def _log(self, level, step, **kwargs):
        _slog(level, step, session_id=self.session_id,
              trace_id=self.trace_id, **kwargs)

    def _trace(self, entry):
        entry['session_id'] = self.session_id
        self._trace_log(self.trace_id, entry)

    # ── Setup ───────────────────────────────────────────────────

    def _setup_recognizer(self):
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=self.speech_key,
            region=self.region
        )
        translation_config.speech_recognition_language = self.source_language

        added_langs = []
        for lang in self.target_languages:
            trans_code = self.TRANSLATION_LANG_MAP.get(lang, lang)
            translation_config.add_target_language(trans_code)
            added_langs.append(f"{lang}->{trans_code}")

        self._log('info', 'recognizer_config',
                  config_type='SpeechTranslationConfig',
                  recognition_language=self.source_language,
                  target_languages_added=added_langs,
                  note='Using TranslationRecognizer (NOT SpeechRecognizer)')

        # Responsive segmentation
        translation_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "500"
        )

        audio_config = speechsdk.audio.AudioConfig(stream=self._audio_stream)

        self._translation_recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config
        )

        # Assert recognizer type — guard against accidental SpeechRecognizer usage
        assert isinstance(self._translation_recognizer,
                          speechsdk.translation.TranslationRecognizer), \
            (f"CRITICAL: Expected TranslationRecognizer, got "
             f"{type(self._translation_recognizer).__name__}")

        self._log('info', 'recognizer_created',
                  recognizer_type=type(self._translation_recognizer).__name__,
                  source_language=self.source_language,
                  target_count=len(self.target_languages))

        # Connect ALL callbacks for full visibility
        self._translation_recognizer.recognizing.connect(self._on_recognizing)
        self._translation_recognizer.recognized.connect(self._on_recognized)
        self._translation_recognizer.canceled.connect(self._on_canceled)
        self._translation_recognizer.session_started.connect(self._on_session_started)
        self._translation_recognizer.session_stopped.connect(self._on_session_stopped)

    def _setup_synthesizers(self):
        # Create queues first (needed regardless of TTS mode)
        for lang in self.target_languages:
            self._translated_text_queues[lang] = queue.Queue()

        # Try SDK-based synthesizers first
        try:
            for lang in self.target_languages:
                speech_config = speechsdk.SpeechConfig(
                    subscription=self.speech_key,
                    region=self.region
                )
                locale = self.TTS_LOCALE_MAP.get(lang, 'en-US')
                voice_name = self.TTS_VOICE_MAP.get(locale, 'en-US-JennyNeural')

                # Set BOTH language and voice name (matches working standalone script)
                speech_config.speech_synthesis_language = locale
                speech_config.speech_synthesis_voice_name = voice_name

                # RIFF WAV 16kHz 16-bit mono — smaller than 24kHz, stays well under
                # the Express 5MB body limit even for long sentences
                speech_config.set_speech_synthesis_output_format(
                    speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
                )

                self._synthesizers[lang] = speechsdk.SpeechSynthesizer(
                    speech_config=speech_config,
                    audio_config=None
                )
                self._log('info', 'synth_init', language=lang, locale=locale,
                          voice=voice_name, mode='sdk',
                          format='Riff16Khz16BitMonoPcm')

        except Exception as e:
            # SDK TTS failed (e.g. Error 2176 / missing libssl1.1).
            # Fall back to Azure TTS REST API (pure HTTP, no native libs).
            self._log('error', 'sdk_synth_fail', error=str(e),
                      traceback=traceback.format_exc())
            self._log('info', 'switching_to_rest_tts',
                      msg='SDK SpeechSynthesizer failed, using REST API fallback')
            self._use_rest_tts = True
            self._synthesizers = {}

        # Start TTS worker threads regardless of mode (managed by executor)
        for lang in self.target_languages:
            self._synth_threads[lang] = {
                "running": True,
                "future": None  # Will be set when submitted to executor
            }
            # Submit to bounded thread pool instead of creating unbounded threads
            future = self._tts_executor.submit(self._voice_synth, lang)
            self._synth_threads[lang]["future"] = future

    # ── Session lifecycle ───────────────────────────────────────

    def start(self):
        self._log('info', 'starting_recognition',
                  mode='continuous',
                  method='start_continuous_recognition_async',
                  note='Continuous recognition - will process multiple utterances')
        self._translation_recognizer.start_continuous_recognition_async().get()
        self._recognition_started = True
        self._log('info', 'recognition_started',
                  lifecycle_state='active',
                  note='Recognizer now listening continuously until stop() called')

    def push_audio(self, audio_bytes: bytes):
        if self._stream_closed:
            self._log('error', 'push_audio_after_close',
                      bytes=len(audio_bytes),
                      note='Attempting to push audio after stream closed')
            return

        self._audio_stream.write(audio_bytes)
        self._total_bytes_pushed += len(audio_bytes)
        self._push_count += 1

        # Log every 50th push to confirm audio data is flowing into the SDK
        if self._push_count % 50 == 1:
            total_sec = round(self._total_bytes_pushed / (16000 * 2), 1)
            self._log('info', 'audio_pushed',
                      push_no=self._push_count,
                      chunk_bytes=len(audio_bytes),
                      total_bytes=self._total_bytes_pushed,
                      total_audio_seconds=total_sec,
                      recognition_active=self._recognition_started and not self._recognition_stopped,
                      stream_open=not self._stream_closed)

    def stop(self):
        self._log('info', 'stopping',
                  total_bytes=self._total_bytes_pushed,
                  recognize_count=self._recognize_count,
                  lifecycle_state='stopping',
                  note='Explicit stop() called - ending session')
        self._stop_event.set()

        for lang, info in self._synth_threads.items():
            info["running"] = False

        # Shutdown TTS thread pool gracefully
        try:
            self._log('info', 'shutting_down_tts_executor')
            self._tts_executor.shutdown(wait=True)
            self._log('info', 'tts_executor_shutdown_complete')
        except Exception as e:
            self._log('error', 'tts_executor_shutdown_fail', error=str(e))

        if self._translation_recognizer and not self._recognition_stopped:
            try:
                self._log('info', 'stopping_recognition',
                          method='stop_continuous_recognition_async')
                self._translation_recognizer.stop_continuous_recognition_async().get()
                self._recognition_stopped = True
                self._log('info', 'recognition_stopped')
            except Exception as e:
                self._log('error', 'stop_recognizer_fail', error=str(e))

        if not self._stream_closed:
            try:
                self._log('info', 'closing_audio_stream')
                self._audio_stream.close()
                self._stream_closed = True
                self._log('info', 'audio_stream_closed')
            except Exception as e:
                self._log('error', 'close_stream_fail', error=str(e))

        self._log('info', 'stopped', lifecycle_state='stopped')

    # ── Recognition callbacks ───────────────────────────────────

    def _on_session_started(self, evt):
        try:
            self._log('info', 'azure_session_started',
                      lifecycle_event='session_started',
                      recognition_active=self._recognition_started,
                      note='Azure SDK session started - recognition pipeline active')
            self._trace({
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'step': 'azure_session_started'
            })
        except Exception as e:
            self._log('error', 'session_started_handler_exception',
                      error=str(e), traceback=traceback.format_exc(),
                      note='CRITICAL: Exception in _on_session_started handler')

    def _on_recognizing(self, evt):
        """Partial/interim recognition — proves audio is reaching Azure."""
        try:
            if evt.result.text:
                self._partial_count += 1
                # First 3 partials at info level to confirm Azure is receiving audio
                if self._partial_count <= 3:
                    self._log('info', 'recognizing_partial',
                              partial_no=self._partial_count,
                              text=evt.result.text[:80],
                              note='Audio IS reaching Azure and being recognized')
                elif self._partial_count % 20 == 0:
                    self._log('debug', 'recognizing_partial',
                              partial_no=self._partial_count,
                              text=evt.result.text[:80])
        except Exception as e:
            self._log('error', 'recognizing_handler_exception',
                      error=str(e), traceback=traceback.format_exc(),
                      note='CRITICAL: Exception in _on_recognizing handler - continuing')

    def _on_recognized(self, evt):
        # CRITICAL: Wrap entire handler in try/except to prevent recognizer crash
        try:
            t0 = time.time()

            reason = evt.result.reason
            reason_name = str(reason)

            if reason == speechsdk.ResultReason.TranslatedSpeech:
                source_text = evt.result.text
                if not source_text.strip():
                    return

                self._recognize_count += 1
                self._metric('stt_calls')

                translations = evt.result.translations
                translation_keys = list(translations.keys()) if translations else []

                self._log('info', 'stt_recognized',
                          recognizer_type=type(self._translation_recognizer).__name__,
                          source_language=self.source_language,
                          recognized_text=source_text[:100],
                          reason=reason_name,
                          translation_keys=translation_keys,
                          target_languages=list(self.target_languages),
                          recognize_no=self._recognize_count)
                self._trace({
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'step': 'stt',
                    'text': source_text[:100],
                    'translation_keys': translation_keys,
                    'recognize_no': self._recognize_count
                })

                elapsed_ms = int((time.time() - t0) * 1000)
                self._latency('stt_latencies', elapsed_ms)

                for lang in self.target_languages:
                    trans_code = self.TRANSLATION_LANG_MAP.get(lang, lang)

                    if trans_code in translations:
                        translated = translations[trans_code]
                        self._metric('translate_calls')

                        self._log('info', 'translated',
                                  language=lang, trans_code=trans_code,
                                  source_text=source_text[:60],
                                  translated_text=translated[:100],
                                  tts_input_text=translated[:60])
                        self._trace({
                            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                            'step': 'translate',
                            'language': lang,
                            'text': translated[:100]
                        })

                        self._translated_text_queues[lang].put(translated)
                        self._broadcast_subtitle(lang, translated)
                    else:
                        self._log('error', 'translation_missing',
                                  language=lang,
                                  trans_code=trans_code,
                                  available_keys=translation_keys,
                                  source_text=source_text[:60],
                                  diagnostic=(
                                      f"Expected key '{trans_code}' not found in "
                                      f"translations dict. Available: {translation_keys}. "
                                      f"Verify add_target_language('{trans_code}') was called."))

            elif reason == speechsdk.ResultReason.RecognizedSpeech:
                # RecognizedSpeech (not TranslatedSpeech) means Azure recognized
                # the speech but did NOT translate it. This is a critical diagnostic.
                self._log('error', 'recognized_but_NOT_translated',
                          recognized_text=evt.result.text[:100] if evt.result.text else '',
                          reason=reason_name,
                          recognizer_type=type(self._translation_recognizer).__name__,
                          source_language=self.source_language,
                          target_languages=list(self.target_languages),
                          diagnostic=(
                              "ResultReason is RecognizedSpeech, NOT TranslatedSpeech. "
                              "Azure recognized speech but produced NO translations. "
                              "Causes: (1) SpeechRecognizer used instead of TranslationRecognizer, "
                              "(2) add_target_language() not called, "
                              "(3) language codes in wrong format (use 'es' not 'es-ES')."))
                self._metric('errors_total')

            elif reason == speechsdk.ResultReason.NoMatch:
                self._log('warn', 'no_match',
                          reason=reason_name,
                          no_match_reason=str(evt.result.no_match_details.reason)
                          if hasattr(evt.result, 'no_match_details') else 'unknown')
            else:
                self._log('warn', 'recognized_unexpected',
                          reason=reason_name,
                          text=evt.result.text[:100] if evt.result.text else '')
        
        except Exception as e:
            # CRITICAL: Never let exception crash the recognizer
            self._log('error', 'recognized_handler_exception',
                      error=str(e),
                      traceback=traceback.format_exc(),
                      recognize_count=self._recognize_count,
                      note='CRITICAL: Exception in _on_recognized handler - recognizer continues')
            self._metric('errors_total')

    def _on_canceled(self, evt):
        try:
            cancellation = evt.cancellation_details
            self._log('error', 'recognition_canceled',
                      lifecycle_event='canceled',
                      reason=str(cancellation.reason),
                      error_code=str(cancellation.error_code) if hasattr(cancellation, 'error_code') else '',
                      error_details=cancellation.error_details if cancellation.error_details else '',
                      recognition_count=self._recognize_count,
                      note='CRITICAL: Recognition canceled - check error_details for root cause')
            self._metric('errors_total')
            self._trace({
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'step': 'canceled',
                'reason': str(cancellation.reason),
                'details': cancellation.error_details or ''
            })
        except Exception as e:
            self._log('error', 'canceled_handler_exception',
                      error=str(e), traceback=traceback.format_exc(),
                      note='CRITICAL: Exception in _on_canceled handler')
            self._metric('errors_total')

    def _on_session_stopped(self, evt):
        try:
            # This callback fires when Azure's recognition session ends.
            # In continuous recognition, this should NOT fire unless:
            # 1. stop_continuous_recognition() was explicitly called
            # 2. The audio stream was closed
            # 3. An error occurred
            # If this fires unexpectedly (recognize_count > 0 but stop() not called),
            # it indicates a problem.
            was_unexpected = (self._recognize_count > 0 and 
                              not self._stop_event.is_set() and 
                              not self._recognition_stopped)
            
            self._log('warn' if was_unexpected else 'info',
                      'azure_session_stopped',
                      lifecycle_event='session_stopped',
                      recognition_count=self._recognize_count,
                      stop_called=self._stop_event.is_set(),
                      recognition_stopped=self._recognition_stopped,
                      unexpected=was_unexpected,
                      note=('UNEXPECTED session stop - recognition should be continuous' 
                            if was_unexpected else 
                            'Normal session_stopped after explicit stop()'))
        except Exception as e:
            self._log('error', 'session_stopped_handler_exception',
                      error=str(e), traceback=traceback.format_exc(),
                      note='CRITICAL: Exception in _on_session_stopped handler')

    # ── TTS synthesis thread ────────────────────────────────────

    def _voice_synth(self, language):
        """
        Per-language TTS thread (bounded via ThreadPoolExecutor).
        Pulls translated text from queue, synthesizes audio, broadcasts.
        Catches ALL exceptions to prevent silent thread death.
        Supports SDK and REST API modes.
        """
        mode = 'rest' if self._use_rest_tts else 'sdk'
        self._log('info', 'synth_thread_start',
                  language=language,
                  mode=mode,
                  thread_pool='bounded_executor')

        while self._synth_threads[language]["running"]:
            try:
                text = self._translated_text_queues[language].get(timeout=0.5)
            except queue.Empty:
                continue

            t0 = time.time()
            try:
                self._metric('tts_calls')
                locale = self.TTS_LOCALE_MAP.get(language, 'unknown')
                self._log('info', 'tts_start', language=language,
                          mode=mode, tts_input_text=text[:60],
                          tts_locale=locale,
                          note='Synthesizing TRANSLATED text (not English original)')

                if self._use_rest_tts:
                    audio_bytes = self._rest_tts(language, text)
                else:
                    audio_bytes = self._sdk_tts(language, text)

                elapsed_ms = int((time.time() - t0) * 1000)
                self._latency('tts_latencies', elapsed_ms)

                if not audio_bytes or len(audio_bytes) == 0:
                    self._log('error', 'tts_empty', language=language,
                              mode=mode, text=text[:60], elapsed_ms=elapsed_ms)
                    self._metric('errors_total')
                    continue

                self._log('info', 'tts_done', language=language,
                          mode=mode, bytes=len(audio_bytes),
                          elapsed_ms=elapsed_ms)
                self._trace({
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'step': 'tts',
                    'language': language,
                    'mode': mode,
                    'bytes': len(audio_bytes),
                    'elapsed_ms': elapsed_ms
                })

                if self.debug:
                    self._save_tts_debug(language, audio_bytes)

                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                self._broadcast_audio(language, audio_b64)

            except Exception as e:
                # CRITICAL: catch all so the thread never dies
                # TTS failure for one language should NOT stop recognizer
                self._log('error', 'tts_exception',
                          language=language,
                          mode=mode,
                          error=str(e),
                          traceback=traceback.format_exc(),
                          note='TTS failed for this language - recognizer continues for future utterances')
                self._metric('errors_total')
            finally:
                try:
                    self._translated_text_queues[language].task_done()
                except ValueError:
                    pass

        self._log('info', 'synth_thread_exit', language=language)

    def _sdk_tts(self, language, text):
        """Synthesize speech using Azure Speech SDK (native library)."""
        result = self._synthesizers[language].speak_text_async(text).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return result.audio_data

        self._log('error', 'sdk_tts_fail', language=language,
                  reason=str(result.reason))

        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation = result.cancellation_details
            self._log('error', 'sdk_tts_canceled', language=language,
                      cancel_reason=str(cancellation.reason),
                      error_details=cancellation.error_details or '')

        return None

    def _rest_tts(self, language, text):
        """
        Synthesize speech using Azure TTS REST API.
        Pure HTTP — no native SDK libraries needed. Used as fallback
        when SpeechSynthesizer fails (e.g. Error 2176 / missing libssl).
        """
        locale = self.TTS_LOCALE_MAP.get(language, 'en-US')
        voice = self.TTS_VOICE_MAP.get(locale, 'en-US-JennyNeural')

        url = (f'https://{self.region}.tts.speech.microsoft.com'
               f'/cognitiveservices/v1')

        headers = {
            'Ocp-Apim-Subscription-Key': self.speech_key,
            'Content-Type': 'application/ssml+xml',
            'X-Microsoft-OutputFormat': 'riff-16khz-16bit-mono-pcm',
            'User-Agent': 'vavilon-ai-service',
        }

        safe_text = html.escape(text)
        ssml = (
            f"<speak version='1.0' xml:lang='{locale}'>"
            f"<voice name='{voice}'>{safe_text}</voice>"
            f"</speak>"
        )

        resp = requests.post(url, headers=headers,
                             data=ssml.encode('utf-8'), timeout=15)

        if resp.status_code == 200:
            self._log('debug', 'rest_tts_ok', language=language,
                      bytes=len(resp.content))
            return resp.content  # WAV bytes (RIFF format)
        else:
            self._log('error', 'rest_tts_fail', language=language,
                      status=resp.status_code, body=resp.text[:200])
            self._metric('errors_total')
            return None

    # ── Broadcast to Node.js ────────────────────────────────────

    def _broadcast_subtitle(self, language, text):
        try:
            resp = requests.post(
                f'{self.node_backend_url}/api/broadcast',
                json={
                    'sessionId': self.session_id,
                    'language': language,
                    'subtitleText': text
                },
                timeout=5
            )
            if resp.status_code != 200:
                self._log('warn', 'broadcast_subtitle_fail',
                          language=language, status=resp.status_code,
                          body=resp.text[:200])
        except Exception as e:
            self._log('error', 'broadcast_subtitle_error',
                      language=language, error=str(e))

    def _broadcast_audio(self, language, audio_b64):
        try:
            resp = requests.post(
                f'{self.node_backend_url}/api/broadcast',
                json={
                    'sessionId': self.session_id,
                    'language': language,
                    'audioData': audio_b64
                },
                timeout=10
            )
            self._log('info', 'broadcast_audio',
                      language=language,
                      status=resp.status_code,
                      b64_len=len(audio_b64))
            self._trace({
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'step': 'broadcast_audio',
                'language': language,
                'status': resp.status_code,
                'b64_len': len(audio_b64)
            })

            if resp.status_code != 200:
                self._log('error', 'broadcast_audio_rejected',
                          language=language, status=resp.status_code,
                          body=resp.text[:300])
                self._metric('errors_total')

        except Exception as e:
            self._log('error', 'broadcast_audio_error',
                      language=language, error=str(e))
            self._metric('errors_total')

    # ── Debug file saves ────────────────────────────────────────

    def _save_tts_debug(self, language, audio_bytes):
        if not self.trace_id:
            return
        try:
            trace_dir = f'/tmp/vavilon_traces/{self.trace_id}'
            os.makedirs(trace_dir, exist_ok=True)
            seq = self._recognize_count
            path = f'{trace_dir}/tts_{language}_{seq:04d}.wav'
            with open(path, 'wb') as f:
                f.write(audio_bytes)
            self._log('debug', 'tts_saved', path=path)
        except Exception as e:
            self._log('warn', 'tts_save_fail', error=str(e))
