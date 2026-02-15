import azure.cognitiveservices.speech as speechsdk
import base64
import html
import json
import os
import queue
import threading
import uuid
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
        self._alive = True  # False when session is dead (audio writer stuck, canceled, etc.)

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
        # Lock for thread-safe dynamic worker creation
        self._worker_lock = threading.Lock()

        # Non-blocking audio queue: push_audio() never blocks Flask
        self._audio_queue: queue.Queue = queue.Queue(maxsize=500)  # Increased from 200 to 500 (Phase 2 fix)

        self._log('info', 'init',
                  source=source_language,
                  targets=list(self.target_languages),
                  region=region,
                  target_count=len(self.target_languages),
                  tts_max_workers=2)

        self._setup_recognizer()
        self._setup_synthesizers()

        # Start background audio writer thread
        self._audio_writer_thread = threading.Thread(
            target=self._audio_writer, daemon=True,
            name=f'audio-writer-{session_id}')
        self._audio_writer_thread.start()

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

    @property
    def alive(self):
        """True if session is healthy. False if audio writer stuck, recognizer crashed, etc."""
        return self._alive

    def push_audio(self, audio_bytes: bytes):
        """Non-blocking: puts audio in queue. Background writer does actual SDK write."""
        if not self._alive or self._stream_closed:
            return False

        try:
            self._audio_queue.put_nowait(audio_bytes)
            self._push_count += 1

            if self._push_count % 50 == 1:
                self._log('info', 'audio_queued',
                          push_no=self._push_count,
                          chunk_bytes=len(audio_bytes),
                          queue_size=self._audio_queue.qsize(),
                          stream_open=not self._stream_closed)
            return True
        except queue.Full:
            self._alive = False
            self._log('error', 'audio_queue_full',
                      queue_maxsize=self._audio_queue.maxsize,
                      note='Audio writer blocked — marking session dead')
            return False

    def _audio_writer(self):
        """Background thread: pulls from queue, writes to PushAudioInputStream."""
        self._log('info', 'audio_writer_start')
        while self._alive and not self._stream_closed and not self._stop_event.is_set():
            try:
                audio_bytes = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not self._alive or self._stream_closed:
                break

            try:
                self._audio_stream.write(audio_bytes)
                self._total_bytes_pushed += len(audio_bytes)

                if self._total_bytes_pushed % (16000 * 2 * 5) < len(audio_bytes):
                    total_sec = round(self._total_bytes_pushed / (16000 * 2), 1)
                    self._log('info', 'audio_pushed',
                              total_bytes=self._total_bytes_pushed,
                              total_audio_seconds=total_sec,
                              queue_depth=self._audio_queue.qsize())
            except Exception as e:
                self._alive = False
                self._log('error', 'audio_write_error', error=str(e),
                          note='SDK write failed — marking session dead')
                break

        self._log('info', 'audio_writer_exit',
                  total_bytes=self._total_bytes_pushed,
                  alive=self._alive)

    def _ensure_tts_worker(self, language: str):
        """
        Dynamically create TTS worker thread for a language if it doesn't exist.
        Thread-safe: uses lock to prevent race conditions when multiple requests
        arrive simultaneously for the same new language.
        """
        # Fast path: worker already exists (no lock needed for read)
        if language in self._synth_threads and self._synth_threads[language]["running"]:
            return True
        
        # Slow path: need to create worker (acquire lock)
        with self._worker_lock:
            # Double-check: another thread may have created it while we waited
            if language in self._synth_threads and self._synth_threads[language]["running"]:
                return True
            
            self._log('info', 'dynamic_worker_creation_start',
                      language=language,
                      note='Creating TTS worker for language that joined mid-session')
            
            try:
                # Create queue
                if language not in self._translated_text_queues:
                    self._translated_text_queues[language] = queue.Queue()
                    self._log('info', 'tts_queue_created', language=language)
                
                # Create synthesizer (if using SDK mode)
                if not self._use_rest_tts and language not in self._synthesizers:
                    speech_config = speechsdk.SpeechConfig(
                        subscription=self.speech_key,
                        region=self.region
                    )
                    locale = self.TTS_LOCALE_MAP.get(language, 'en-US')
                    voice_name = self.TTS_VOICE_MAP.get(locale, 'en-US-JennyNeural')
                    
                    speech_config.speech_synthesis_language = locale
                    speech_config.speech_synthesis_voice_name = voice_name
                    speech_config.set_speech_synthesis_output_format(
                        speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
                    )
                    
                    self._synthesizers[language] = speechsdk.SpeechSynthesizer(
                        speech_config=speech_config,
                        audio_config=None
                    )
                    self._log('info', 'tts_synthesizer_created',
                              language=language, locale=locale, voice=voice_name)
                
                # Create worker thread
                self._synth_threads[language] = {
                    "running": True,
                    "future": None
                }
                future = self._tts_executor.submit(self._voice_synth, language)
                self._synth_threads[language]["future"] = future
                
                self._log('info', 'dynamic_worker_created',
                          language=language,
                          worker_count=len(self._synth_threads),
                          note='New TTS worker started successfully')
                
                return True
            
            except Exception as e:
                self._log('error', 'dynamic_worker_creation_failed',
                          language=language,
                          error=str(e),
                          traceback=traceback.format_exc())
                return False

    def generate_tts(self, segment_id: str, translations: dict):
        """
        Queue TTS for explicit languages. Called by Node after segment_finalized.
        No global active-language state — Node sends exactly which languages need TTS.
        Dynamically creates TTS workers for new languages that join mid-session.
        """
        enqueued = []
        
        self._log('info', 'generate_tts_received',
                  segment_id=segment_id,
                  translations_count=len(translations),
                  requested_languages=list(translations.keys()))
        
        for lang, text in translations.items():
            # Ensure TTS worker exists for this language (creates if missing)
            if not self._ensure_tts_worker(lang):
                self._log('error', 'tts_worker_creation_failed',
                          language=lang,
                          segment_id=segment_id,
                          note='Could not create TTS worker - skipping this language')
                continue
            
            try:
                queue_depth = self._translated_text_queues[lang].qsize()
                self._translated_text_queues[lang].put({
                    'segment_id': segment_id,
                    'text': text
                })
                enqueued.append(lang)
                
                self._log('info', 'tts_enqueued',
                          segment_id=segment_id,
                          language=lang,
                          text_preview=text[:50],
                          queue_depth_before=queue_depth,
                          queue_depth_after=queue_depth + 1)
            except Exception as e:
                self._log('error', 'tts_enqueue_fail',
                          segment_id=segment_id,
                          language=lang,
                          error=str(e),
                          traceback=traceback.format_exc())
        
        self._log('info', 'generate_tts_complete',
                  segment_id=segment_id,
                  enqueued_count=len(enqueued),
                  enqueued_languages=enqueued)
        
        return enqueued

    def stop(self, graceful=False):
        """
        Stop the translation session.
        Uses timeouts to prevent blocking indefinitely.
        Returns quickly even if cleanup isn't complete.
        
        Args:
            graceful: If True, closes stream first and waits for pending segments
                     before stopping recognizer. If False, stops immediately.
        """
        self._log('info', 'stopping',
                  total_bytes=self._total_bytes_pushed,
                  recognize_count=self._recognize_count,
                  lifecycle_state='stopping',
                  graceful=graceful,
                  note='Graceful stop (close stream first)' if graceful else 'Explicit stop() called - ending session')

        # Mark dead first to unblock push_audio and audio writer
        self._alive = False

        # Signal all threads to stop
        self._stop_event.set()

        # GRACEFUL STOP: Close stream first to signal end-of-input to Azure
        # This allows Azure to finalize any pending segments
        if graceful and not self._stream_closed:
            try:
                self._log('info', 'graceful_stop_closing_stream',
                          note='Closing audio stream to signal end-of-input to Azure')
                self._audio_stream.close()
                self._stream_closed = True
                self._log('info', 'graceful_stop_stream_closed',
                          note='Stream closed - Azure will finalize pending segments')
                
                # Wait for Azure to finalize pending segments
                # Azure continuous recognition will emit final segment_finalized 
                # events after stream close. Typical time: 200-800ms.
                grace_period_sec = 2.0
                self._log('info', 'graceful_stop_waiting',
                          wait_seconds=grace_period_sec,
                          note='Waiting for Azure to finalize pending segments')
                time.sleep(grace_period_sec)
                self._log('info', 'graceful_stop_wait_complete',
                          note='Grace period complete - proceeding with recognizer stop')
            except Exception as e:
                self._log('error', 'graceful_stop_stream_close_fail', 
                          error=str(e),
                          note='Stream close failed - continuing with normal stop')

        # Wait for audio writer to exit (checks _alive every 0.5s)
        if self._audio_writer_thread.is_alive():
            self._audio_writer_thread.join(timeout=2.0)

        # Stop TTS threads first
        for lang, info in self._synth_threads.items():
            info["running"] = False

        # Shutdown TTS thread pool with timeout (don't block forever)
        # Note: Python 3.9 doesn't have timeout parameter on shutdown()
        # We use threading.Timer to ensure we don't block indefinitely
        executor_done = threading.Event()
        
        def shutdown_executor():
            try:
                self._tts_executor.shutdown(wait=True)
                executor_done.set()
            except Exception as e:
                self._log('error', 'tts_executor_shutdown_fail', error=str(e))
                executor_done.set()
        
        shutdown_thread = threading.Thread(target=shutdown_executor, daemon=True)
        shutdown_thread.start()
        
        self._log('info', 'shutting_down_tts_executor',
                  timeout_seconds=3.0,
                  note='Waiting for TTS threads to finish')
        
        # Wait up to 3 seconds for shutdown to complete
        if not executor_done.wait(timeout=3.0):
            self._log('warn', 'tts_executor_shutdown_timeout',
                      note='TTS threads did not finish in 3s - continuing anyway')
        else:
            self._log('info', 'tts_executor_shutdown_complete')

        # Stop recognizer with timeout
        if self._translation_recognizer and not self._recognition_stopped:
            try:
                self._log('info', 'stopping_recognition',
                          method='stop_continuous_recognition_async',
                          note='Stopping Azure recognizer')
                # Azure SDK doesn't support timeout on this call, but it's usually fast
                self._translation_recognizer.stop_continuous_recognition_async().get()
                self._recognition_stopped = True
                self._log('info', 'recognition_stopped')
            except Exception as e:
                self._log('error', 'stop_recognizer_fail', error=str(e),
                          note='Recognizer stop failed but continuing cleanup')

        # Close audio stream (if not already closed in graceful stop)
        if not self._stream_closed:
            try:
                self._log('info', 'closing_audio_stream')
                self._audio_stream.close()
                self._stream_closed = True
                self._log('info', 'audio_stream_closed')
            except Exception as e:
                self._log('error', 'close_stream_fail', error=str(e))

        self._log('info', 'stopped', lifecycle_state='stopped',
                  cleanup_complete=self._recognition_stopped and self._stream_closed)

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
                segment_id = str(uuid.uuid4())

                translations_dict = evt.result.translations
                translation_keys = list(translations_dict.keys()) if translations_dict else []

                self._log('info', 'stt_recognized',
                          recognizer_type=type(self._translation_recognizer).__name__,
                          source_language=self.source_language,
                          recognized_text=source_text[:100],
                          reason=reason_name,
                          translation_keys=translation_keys,
                          target_languages=list(self.target_languages),
                          recognize_no=self._recognize_count,
                          segment_id=segment_id)
                self._trace({
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'step': 'stt',
                    'text': source_text[:100],
                    'translation_keys': translation_keys,
                    'recognize_no': self._recognize_count,
                    'segment_id': segment_id
                })

                elapsed_ms = int((time.time() - t0) * 1000)
                self._latency('stt_latencies', elapsed_ms)

                # Build translations map with short language codes
                translations = {}
                for lang in self.target_languages:
                    trans_code = self.TRANSLATION_LANG_MAP.get(lang, lang)

                    if trans_code in translations_dict:
                        translated = translations_dict[trans_code]
                        translations[lang] = translated
                        self._metric('translate_calls')

                        self._log('info', 'translated',
                                  language=lang, trans_code=trans_code,
                                  source_text=source_text[:60],
                                  translated_text=translated[:100],
                                  segment_id=segment_id)
                        self._trace({
                            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                            'step': 'translate',
                            'language': lang,
                            'text': translated[:100],
                            'segment_id': segment_id
                        })
                    else:
                        self._log('error', 'translation_missing',
                                  language=lang,
                                  trans_code=trans_code,
                                  available_keys=translation_keys,
                                  source_text=source_text[:60],
                                  segment_id=segment_id,
                                  diagnostic=(
                                      f"Expected key '{trans_code}' not found in "
                                      f"translations dict. Available: {translation_keys}. "
                                      f"Verify add_target_language('{trans_code}') was called."))

                # Emit segment_finalized in daemon thread — NEVER block SDK callback
                # Node will determine active languages and call /generate-tts
                threading.Thread(
                    target=self._emit_segment_finalized,
                    args=(segment_id, source_text, translations),
                    daemon=True
                ).start()

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
            self._alive = False  # Mark dead so push_audio stops
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
            
            if was_unexpected:
                self._alive = False  # Mark dead so push_audio stops

            self._log('warn' if was_unexpected else 'info',
                      'azure_session_stopped',
                      lifecycle_event='session_stopped',
                      recognition_count=self._recognize_count,
                      stop_called=self._stop_event.is_set(),
                      recognition_stopped=self._recognition_stopped,
                      unexpected=was_unexpected,
                      alive=self._alive,
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
        Pulls translated text from queue, synthesizes audio, emits tts_ready.
        Catches ALL exceptions to prevent silent thread death.
        Supports SDK and REST API modes.
        """
        thread_id = threading.get_ident()
        mode = 'rest' if self._use_rest_tts else 'sdk'
        
        self._log('info', 'tts_worker_started',
                  language=language,
                  thread_id=thread_id,
                  mode=mode,
                  thread_pool='bounded_executor')
        
        consecutive_errors = 0
        max_consecutive_errors = 3
        iteration_count = 0

        while self._synth_threads[language]["running"] and not self._stop_event.is_set():
            iteration_count += 1
            
            # Log heartbeat every 10 iterations
            if consecutive_errors == 0 and iteration_count % 10 == 0:
                self._log('debug', 'tts_worker_alive',
                          language=language,
                          thread_id=thread_id,
                          queue_depth=self._translated_text_queues[language].qsize(),
                          iterations=iteration_count)
            
            try:
                # Reduced timeout for faster exit on stop
                item = self._translated_text_queues[language].get(timeout=0.2)
            except queue.Empty:
                # Check stop_event more frequently
                if self._stop_event.is_set():
                    break
                continue

            # Extract segment_id and text from queue item
            if isinstance(item, dict):
                segment_id = item.get('segment_id', 'unknown')
                text = item.get('text', '')
            else:
                # Fallback for backward compatibility
                segment_id = 'legacy'
                text = item

            t0 = time.time()
            try:
                self._metric('tts_calls')
                locale = self.TTS_LOCALE_MAP.get(language, 'unknown')
                
                self._log('info', 'tts_synthesis_start',
                          segment_id=segment_id,
                          language=language,
                          mode=mode,
                          text_length=len(text),
                          text_preview=text[:50],
                          tts_locale=locale,
                          note='Synthesizing TRANSLATED text (not English original)')

                if self._use_rest_tts:
                    audio_bytes = self._rest_tts(language, text)
                else:
                    audio_bytes = self._sdk_tts(language, text)

                elapsed_ms = int((time.time() - t0) * 1000)
                self._latency('tts_latencies', elapsed_ms)

                if not audio_bytes or len(audio_bytes) == 0:
                    self._log('error', 'tts_empty_audio',
                              segment_id=segment_id,
                              language=language,
                              mode=mode,
                              elapsed_ms=elapsed_ms,
                              note='Synthesizer returned no audio data')
                    consecutive_errors += 1
                    self._metric('errors_total')
                    continue

                self._log('info', 'tts_synthesis_complete',
                          segment_id=segment_id,
                          language=language,
                          mode=mode,
                          audio_bytes=len(audio_bytes),
                          duration_ms=elapsed_ms)
                
                self._trace({
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'step': 'tts',
                    'language': language,
                    'mode': mode,
                    'bytes': len(audio_bytes),
                    'elapsed_ms': elapsed_ms,
                    'segment_id': segment_id
                })

                if self.debug:
                    self._save_tts_debug(language, audio_bytes)

                audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                
                # Emit tts_ready event - may raise exception
                try:
                    self._emit_tts_ready(
                        segment_id=segment_id,
                        language=language,
                        audio_b64=audio_b64
                    )
                    consecutive_errors = 0  # Reset on success
                except Exception as emit_error:
                    consecutive_errors += 1
                    self._log('error', 'tts_emit_fail',
                              segment_id=segment_id,
                              language=language,
                              error=str(emit_error),
                              consecutive_errors=consecutive_errors,
                              traceback=traceback.format_exc())
                    
                    if consecutive_errors >= max_consecutive_errors:
                        self._log('critical', 'tts_worker_giving_up',
                                  language=language,
                                  note=f'Failed {max_consecutive_errors} times - stopping worker')
                        break

            except Exception as e:
                # CRITICAL: catch all so the thread never dies
                # TTS failure for one language should NOT stop recognizer
                consecutive_errors += 1
                self._log('error', 'tts_worker_exception',
                          segment_id=segment_id,
                          language=language,
                          mode=mode,
                          error=str(e),
                          error_type=type(e).__name__,
                          consecutive_errors=consecutive_errors,
                          traceback=traceback.format_exc(),
                          note='TTS failed for this language - recognizer continues for future utterances')
                self._metric('errors_total')
                
                if consecutive_errors >= max_consecutive_errors:
                    self._log('critical', 'tts_worker_crashed',
                              language=language,
                              note=f'{max_consecutive_errors} consecutive errors - worker dead')
                    break
            finally:
                try:
                    self._translated_text_queues[language].task_done()
                except ValueError:
                    pass

        # Thread exiting - log the reason
        exit_reason = 'stop_event' if self._stop_event.is_set() else 'running_flag_cleared'
        self._log('info', 'tts_worker_stopped',
                  language=language,
                  thread_id=thread_id,
                  exit_reason=exit_reason,
                  stop_event_set=self._stop_event.is_set(),
                  running_flag=self._synth_threads[language]["running"],
                  consecutive_errors=consecutive_errors,
                  total_iterations=iteration_count,
                  note='TTS thread exiting cleanly')

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

    # ── Event emission to Node.js ──────────────────────────────

    def _emit_segment_finalized(self, segment_id: str, source_text: str, translations: dict):
        """
        Emit segment_finalized event when Azure recognizes a complete utterance.
        Node will immediately broadcast subtitles to all listeners.
        """
        try:
            event = {
                'type': 'segment_finalized',
                'traceId': self.trace_id,
                'sessionId': self.session_id,
                'segmentId': segment_id,
                'sourceLanguage': self.source_language,
                'recognizedText': source_text,
                'translations': translations,  # {'es': 'Hola', 'fr': 'Bonjour', ...}
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z')
            }
            
            resp = requests.post(
                f'{self.node_backend_url}/api/events',
                json=event,
                timeout=5
            )
            
            if resp.status_code != 200:
                self._log('warn', 'segment_finalized_fail',
                          segment_id=segment_id, status=resp.status_code,
                          body=resp.text[:200])
            else:
                self._log('info', 'segment_finalized_sent',
                          segment_id=segment_id,
                          translation_count=len(translations))
        except Exception as e:
            self._log('error', 'segment_finalized_error',
                      segment_id=segment_id, error=str(e))

    def _emit_tts_ready(self, segment_id: str, language: str, audio_b64: str):
        """
        Emit tts_ready event when TTS synthesis completes.
        Node will broadcast audio to listeners of that language.
        Implements retry logic for resilience.
        """
        event = {
            'type': 'tts_ready',
            'traceId': self.trace_id,
            'sessionId': self.session_id,
            'segmentId': segment_id,
            'language': language,
            'audioFormat': 'riff16khz16bitpcm',
            'audioBytesBase64': audio_b64
        }
        
        url = f'{self.node_backend_url}/api/events'
        max_retries = 2
        retry_delay = 0.5
        audio_size_kb = len(base64.b64decode(audio_b64)) / 1024
        
        for attempt in range(max_retries + 1):
            try:
                self._log('info', 'tts_emit_attempt',
                          segment_id=segment_id,
                          language=language,
                          attempt=attempt + 1,
                          audio_size_kb=round(audio_size_kb, 2),
                          url=url)
                
                resp = requests.post(url, json=event, timeout=5.0)
                
                self._log('info', 'tts_emit_success',
                          segment_id=segment_id,
                          language=language,
                          status_code=resp.status_code,
                          attempt=attempt + 1,
                          b64_len=len(audio_b64))
                
                self._trace({
                    'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'step': 'tts_ready',
                    'segment_id': segment_id,
                    'language': language,
                    'status': resp.status_code,
                    'b64_len': len(audio_b64)
                })

                if resp.status_code != 200:
                    self._log('error', 'tts_ready_rejected',
                              segment_id=segment_id,
                              language=language,
                              status=resp.status_code,
                              body=resp.text[:300])
                    self._metric('errors_total')
                
                return  # Success
            
            except requests.exceptions.Timeout as e:
                self._log('error', 'tts_emit_timeout',
                          segment_id=segment_id,
                          language=language,
                          attempt=attempt + 1,
                          max_retries=max_retries,
                          error=str(e))
                
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    self._metric('errors_total')
            
            except Exception as e:
                self._log('error', 'tts_emit_error',
                          segment_id=segment_id,
                          language=language,
                          attempt=attempt + 1,
                          max_retries=max_retries,
                          error=str(e),
                          error_type=type(e).__name__,
                          traceback=traceback.format_exc())
                
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    self._metric('errors_total')
        
        # All retries failed
        raise Exception(f'Failed to emit tts_ready after {max_retries + 1} attempts')
    
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
