import azure.cognitiveservices.speech as speechsdk
import base64
import json
import os
import queue
import threading
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

        self._log('info', 'init', source=source_language,
                  targets=list(self.target_languages), region=region)

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
            added_langs.append(trans_code)

        self._log('info', 'recognizer_config',
                  recognition_language=self.source_language,
                  target_languages=added_langs)

        # Responsive segmentation
        translation_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "500"
        )

        audio_config = speechsdk.audio.AudioConfig(stream=self._audio_stream)

        self._translation_recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config
        )

        # Connect ALL callbacks for full visibility
        self._translation_recognizer.recognizing.connect(self._on_recognizing)
        self._translation_recognizer.recognized.connect(self._on_recognized)
        self._translation_recognizer.canceled.connect(self._on_canceled)
        self._translation_recognizer.session_started.connect(self._on_session_started)
        self._translation_recognizer.session_stopped.connect(self._on_session_stopped)

    def _setup_synthesizers(self):
        for lang in self.target_languages:
            self._translated_text_queues[lang] = queue.Queue()

            speech_config = speechsdk.SpeechConfig(
                subscription=self.speech_key,
                region=self.region
            )
            locale = self.TTS_LOCALE_MAP.get(lang, 'en-US')
            speech_config.speech_synthesis_language = locale

            # RIFF WAV 16kHz 16-bit mono — smaller than 24kHz, stays well under
            # the Express 5MB body limit even for long sentences
            speech_config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
            )

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

            self._log('info', 'synth_init', language=lang, locale=locale,
                      format='Riff16Khz16BitMonoPcm')

    # ── Session lifecycle ───────────────────────────────────────

    def start(self):
        self._log('info', 'starting_recognition')
        self._translation_recognizer.start_continuous_recognition_async().get()
        self._log('info', 'recognition_started')

    def push_audio(self, audio_bytes: bytes):
        self._audio_stream.write(audio_bytes)
        self._total_bytes_pushed += len(audio_bytes)

    def stop(self):
        self._log('info', 'stopping', total_bytes=self._total_bytes_pushed,
                  recognize_count=self._recognize_count)
        self._stop_event.set()

        for lang, info in self._synth_threads.items():
            info["running"] = False

        if self._translation_recognizer:
            try:
                self._translation_recognizer.stop_continuous_recognition_async().get()
            except Exception as e:
                self._log('error', 'stop_recognizer_fail', error=str(e))

        try:
            self._audio_stream.close()
        except Exception:
            pass

        for lang, info in self._synth_threads.items():
            if info["thread"].is_alive():
                info["thread"].join(timeout=3.0)

        self._log('info', 'stopped')

    # ── Recognition callbacks ───────────────────────────────────

    def _on_session_started(self, evt):
        self._log('info', 'azure_session_started')
        self._trace({
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'step': 'azure_session_started'
        })

    def _on_recognizing(self, evt):
        """Partial/interim recognition — proves audio is reaching Azure."""
        if evt.result.text:
            self._log('debug', 'recognizing_partial', text=evt.result.text[:80])

    def _on_recognized(self, evt):
        t0 = time.time()

        reason = evt.result.reason
        reason_name = str(reason)

        if reason == speechsdk.ResultReason.TranslatedSpeech:
            source_text = evt.result.text
            if not source_text.strip():
                return

            self._recognize_count += 1
            self._metric('stt_calls')
            elapsed_ms = int((time.time() - t0) * 1000)
            self._latency('stt_latencies', elapsed_ms)

            self._log('info', 'stt_recognized',
                      text=source_text[:100],
                      reason=reason_name,
                      recognize_no=self._recognize_count)
            self._trace({
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'step': 'stt',
                'text': source_text[:100],
                'recognize_no': self._recognize_count
            })

            for lang in self.target_languages:
                trans_code = self.TRANSLATION_LANG_MAP.get(lang, lang)
                translations = evt.result.translations

                if trans_code in translations:
                    translated = translations[trans_code]
                    self._metric('translate_calls')

                    self._log('info', 'translated',
                              language=lang, trans_code=trans_code,
                              text=translated[:100])
                    self._trace({
                        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                        'step': 'translate',
                        'language': lang,
                        'text': translated[:100]
                    })

                    self._translated_text_queues[lang].put(translated)
                    self._broadcast_subtitle(lang, translated)
                else:
                    self._log('warn', 'translation_missing',
                              language=lang, trans_code=trans_code,
                              available_keys=list(translations.keys()))

        elif reason == speechsdk.ResultReason.NoMatch:
            self._log('warn', 'no_match',
                      reason=reason_name,
                      no_match_reason=str(evt.result.no_match_details.reason)
                      if hasattr(evt.result, 'no_match_details') else 'unknown')
        else:
            self._log('warn', 'recognized_unexpected',
                      reason=reason_name,
                      text=evt.result.text[:100] if evt.result.text else '')

    def _on_canceled(self, evt):
        cancellation = evt.cancellation_details
        self._log('error', 'recognition_canceled',
                  reason=str(cancellation.reason),
                  error_code=str(cancellation.error_code) if hasattr(cancellation, 'error_code') else '',
                  error_details=cancellation.error_details if cancellation.error_details else '')
        self._metric('errors_total')
        self._trace({
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'step': 'canceled',
            'reason': str(cancellation.reason),
            'details': cancellation.error_details or ''
        })

    def _on_session_stopped(self, evt):
        self._log('info', 'azure_session_stopped')

    # ── TTS synthesis thread ────────────────────────────────────

    def _voice_synth(self, language):
        """
        Per-language TTS thread. Pulls translated text from queue,
        synthesizes audio, broadcasts. Catches ALL exceptions to prevent
        silent thread death.
        """
        self._log('info', 'synth_thread_start', language=language)

        while self._synth_threads[language]["running"]:
            try:
                text = self._translated_text_queues[language].get(timeout=0.5)
            except queue.Empty:
                continue

            t0 = time.time()
            try:
                self._metric('tts_calls')
                self._log('info', 'tts_start', language=language, text=text[:60])

                result = self._synthesizers[language].speak_text_async(text).get()
                elapsed_ms = int((time.time() - t0) * 1000)
                self._latency('tts_latencies', elapsed_ms)

                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    audio_bytes = result.audio_data
                    if len(audio_bytes) == 0:
                        self._log('error', 'tts_empty', language=language,
                                  text=text[:60], elapsed_ms=elapsed_ms)
                        self._metric('errors_total')
                        continue

                    self._log('info', 'tts_done', language=language,
                              bytes=len(audio_bytes), elapsed_ms=elapsed_ms)
                    self._trace({
                        'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                        'step': 'tts',
                        'language': language,
                        'bytes': len(audio_bytes),
                        'elapsed_ms': elapsed_ms
                    })

                    # Save TTS output for debugging
                    if self.debug:
                        self._save_tts_debug(language, audio_bytes)

                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    self._broadcast_audio(language, audio_b64)

                else:
                    self._log('error', 'tts_fail', language=language,
                              reason=str(result.reason), elapsed_ms=elapsed_ms)
                    self._metric('errors_total')

                    if result.reason == speechsdk.ResultReason.Canceled:
                        cancellation = result.cancellation_details
                        self._log('error', 'tts_canceled',
                                  language=language,
                                  cancel_reason=str(cancellation.reason),
                                  error_details=cancellation.error_details or '')

            except Exception as e:
                # CRITICAL: catch all so the thread never dies
                self._log('error', 'tts_exception', language=language,
                          error=str(e), traceback=traceback.format_exc())
                self._metric('errors_total')
            finally:
                try:
                    self._translated_text_queues[language].task_done()
                except ValueError:
                    pass

        self._log('info', 'synth_thread_exit', language=language)

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
