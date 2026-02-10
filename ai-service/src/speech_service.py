import azure.cognitiveservices.speech as speechsdk
import io
import base64
from typing import Dict, List


class SpeechTranslationService:
    """
    Azure Speech SDK wrapper for real-time translation
    Handles: STT -> Translation -> TTS pipeline
    """

    def __init__(self, subscription_key: str, region: str):
        self.subscription_key = subscription_key
        self.region = region

        # Supported target languages (Azure Speech SDK codes)
        self.language_map = {
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

    def process_audio_stream(self, audio_bytes: bytes, session_id: str) -> Dict:
        """
        Process speaker audio:
        1. Speech-to-Text (STT) - recognize original speech
        2. Translation - translate to multiple target languages
        3. Text-to-Speech (TTS) - synthesize translated speech

        Returns:
        {
            'success': True/False,
            'translations': {
                'es': {'text': '...', 'audioData': 'base64...'},
                'fr': {'text': '...', 'audioData': 'base64...'},
                ...
            }
        }
        """
        try:
            # Step 1: Speech-to-Text (STT)
            recognized_text, source_language = self.recognize_speech(audio_bytes)

            if not recognized_text:
                return {
                    'success': False,
                    'error': 'No speech recognized'
                }

            print(f"✓ Recognized ({source_language}): {recognized_text}")

            # Step 2 & 3: Translate and synthesize for each target language
            translations = {}

            # Get target languages (all except source)
            target_languages = [lang for lang in self.language_map.keys()
                                if lang != source_language[:2].lower()]

            for target_lang in target_languages:
                # Translate text
                translated_text = self.translate_text(
                    recognized_text,
                    source_language,
                    target_lang
                )

                # Synthesize speech
                audio_data = self.synthesize_speech(
                    translated_text,
                    target_lang
                )

                translations[target_lang] = {
                    'text': translated_text,
                    'audioData': audio_data
                }

                print(f"✓ Translated to {target_lang}: {translated_text[:50]}...")

            return {
                'success': True,
                'sourceText': recognized_text,
                'sourceLanguage': source_language,
                'translations': translations
            }

        except Exception as e:
            print(f"Error in process_audio_stream: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def recognize_speech(self, audio_bytes: bytes) -> tuple:
        """
        Perform Speech-to-Text using Azure Speech SDK
        Returns: (recognized_text, detected_language)
        """
        try:
            # Create speech config
            speech_config = speechsdk.SpeechConfig(
                subscription=self.subscription_key,
                region=self.region
            )

            # Auto-detect language (supports multiple languages)
            auto_detect_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=list(self.language_map.values())
            )

            # Create audio config from bytes
            audio_stream = speechsdk.audio.PushAudioInputStream()
            audio_stream.write(audio_bytes)
            audio_stream.close()

            audio_config = speechsdk.audio.AudioConfig(stream=audio_stream)

            # Create speech recognizer
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                auto_detect_source_language_config=auto_detect_config,
                audio_config=audio_config
            )

            # Recognize speech
            result = recognizer.recognize_once()

            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                detected_language = result.properties.get(
                    speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult,
                    'en-US'
                )
                return result.text, detected_language
            else:
                print(f"Speech recognition failed: {result.reason}")
                return None, None

        except Exception as e:
            print(f"Error in recognize_speech: {str(e)}")
            return None, None

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate text using Azure Translator
        For MVP, using Azure Speech SDK translation capabilities
        """
        try:
            # Create translation config
            translation_config = speechsdk.translation.SpeechTranslationConfig(
                subscription=self.subscription_key,
                region=self.region
            )

            # Set source language
            translation_config.speech_recognition_language = source_lang

            # Add target language
            target_lang_code = self.language_map.get(target_lang, 'en-US')
            translation_config.add_target_language(target_lang_code)

            # For text-only translation, we use the same approach
            # In production, consider Azure Translator Text API for better text translation

            # For MVP, return simulated translation
            # TODO: Integrate Azure Translator Text API for production
            return f"[{target_lang.upper()}] {text}"

        except Exception as e:
            print(f"Error in translate_text: {str(e)}")
            return text

    def synthesize_speech(self, text: str, language: str) -> str:
        """
        Perform Text-to-Speech using Azure Speech SDK
        Returns: base64-encoded audio data
        """
        try:
            # Create speech config
            speech_config = speechsdk.SpeechConfig(
                subscription=self.subscription_key,
                region=self.region
            )

            # Set target language and voice
            lang_code = self.language_map.get(language, 'en-US')
            speech_config.speech_synthesis_language = lang_code

            # Use default voice for language
            # Map to neural voices for better quality
            voice_map = {
                'en-US': 'en-US-JennyNeural',
                'es-ES': 'es-ES-ElviraNeural',
                'fr-FR': 'fr-FR-DeniseNeural',
                'de-DE': 'de-DE-KatjaNeural',
                'it-IT': 'it-IT-ElsaNeural',
                'pt-PT': 'pt-PT-RaquelNeural',
                'ru-RU': 'ru-RU-SvetlanaNeural',
                'zh-CN': 'zh-CN-XiaoxiaoNeural',
                'ja-JP': 'ja-JP-NanamiNeural',
                'ar-SA': 'ar-SA-ZariyahNeural'
            }

            speech_config.speech_synthesis_voice_name = voice_map.get(
                lang_code,
                'en-US-JennyNeural'
            )

            # Set audio format (MP3 for streaming)
            speech_config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
            )

            # Create synthesizer with null output (we'll capture the audio)
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=None
            )

            # Synthesize speech
            result = synthesizer.speak_text(text)

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                # Encode audio to base64
                audio_base64 = base64.b64encode(result.audio_data).decode('utf-8')
                return audio_base64
            else:
                print(f"Speech synthesis failed: {result.reason}")
                return None

        except Exception as e:
            print(f"Error in synthesize_speech: {str(e)}")
            return None

    def translate_and_synthesize_realtime(
        self,
        audio_stream,
        source_language: str,
        target_languages: List[str]
    ):
        """
        Real-time translation with continuous recognition
        For production use with streaming audio

        This method uses Azure Speech Translation for continuous streaming
        """
        try:
            # Create translation config
            translation_config = speechsdk.translation.SpeechTranslationConfig(
                subscription=self.subscription_key,
                region=self.region
            )

            # Set source language
            translation_config.speech_recognition_language = source_language

            # Add all target languages
            for target_lang in target_languages:
                lang_code = self.language_map.get(target_lang, target_lang)
                translation_config.add_target_language(lang_code)

            # Create audio config from stream
            audio_config = speechsdk.audio.AudioConfig(stream=audio_stream)

            # Create translation recognizer
            recognizer = speechsdk.translation.TranslationRecognizer(
                translation_config=translation_config,
                audio_config=audio_config
            )

            # TODO: Setup event handlers for continuous recognition
            # recognizer.recognized.connect(lambda evt: on_recognized(evt))
            # recognizer.start_continuous_recognition()

            return recognizer

        except Exception as e:
            print(f"Error in translate_and_synthesize_realtime: {str(e)}")
            return None