"""
Live Translation Test — Standalone Azure Speech SDK Demo
=========================================================

Captures live microphone input, translates speech in real time using
Azure Cognitive Services, synthesizes the translation, and plays it
through the speakers.  No browser, no WebSocket, no server needed.

Installation
------------
    pip install azure-cognitiveservices-speech python-dotenv

Environment variables (set before running, or put in a .env file)
-----------------------------------------------------------------
    AZURE_SPEECH_KEY=<your Azure Speech Services key>
    AZURE_SPEECH_REGION=<region, e.g. westeurope>

Usage
-----
    python live_translation_test.py

Press Ctrl+C to stop.
"""

import os
import sys
import time
import threading

try:
    import azure.cognitiveservices.speech as speechsdk
except ImportError:
    print("ERROR: azure-cognitiveservices-speech is not installed.")
    print("Run:  pip install azure-cognitiveservices-speech")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional — env vars can be set externally

# ─── Configuration ────────────────────────────────────────────────────────────
# Change these to switch languages.  The TRANSLATION code is the short BCP-47
# tag that Azure Translation accepts (e.g. "es", "fr", "de", "ru", "zh-Hans").
# The TTS voice must match the target locale.

SOURCE_LANGUAGE = "en-US"

TARGET_LANGUAGE = "es"           # Translation target (BCP-47 short code)
TARGET_LOCALE   = "es-ES"        # TTS locale
TARGET_VOICE    = "es-ES-ElviraNeural"  # Neural TTS voice

# Handy presets — uncomment one block to switch quickly:
# --- French ---
# TARGET_LANGUAGE = "fr"
# TARGET_LOCALE   = "fr-FR"
# TARGET_VOICE    = "fr-FR-DeniseNeural"
# --- German ---
# TARGET_LANGUAGE = "de"
# TARGET_LOCALE   = "de-DE"
# TARGET_VOICE    = "de-DE-KatjaNeural"
# --- Russian ---
# TARGET_LANGUAGE = "ru"
# TARGET_LOCALE   = "ru-RU"
# TARGET_VOICE    = "ru-RU-SvetlanaNeural"
# --- Italian ---
# TARGET_LANGUAGE = "it"
# TARGET_LOCALE   = "it-IT"
# TARGET_VOICE    = "it-IT-ElsaNeural"
# --- Portuguese ---
# TARGET_LANGUAGE = "pt"
# TARGET_LOCALE   = "pt-PT"
# TARGET_VOICE    = "pt-PT-RaquelNeural"
# --- Chinese ---
# TARGET_LANGUAGE = "zh-Hans"
# TARGET_LOCALE   = "zh-CN"
# TARGET_VOICE    = "zh-CN-XiaoxiaoNeural"
# --- Japanese ---
# TARGET_LANGUAGE = "ja"
# TARGET_LOCALE   = "ja-JP"
# TARGET_VOICE    = "ja-JP-NanamiNeural"
# --- Arabic ---
# TARGET_LANGUAGE = "ar"
# TARGET_LOCALE   = "ar-SA"
# TARGET_VOICE    = "ar-SA-ZariyahNeural"
# ──────────────────────────────────────────────────────────────────────────────


def get_credentials():
    """Read and validate Azure credentials from environment."""
    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip()

    if not key:
        print("FATAL: AZURE_SPEECH_KEY environment variable is not set.")
        print("Set it with:  set AZURE_SPEECH_KEY=<your-key>   (Windows)")
        print("          or: export AZURE_SPEECH_KEY=<your-key> (Linux/Mac)")
        sys.exit(1)

    if not region:
        print("FATAL: AZURE_SPEECH_REGION environment variable is not set.")
        print("Set it with:  set AZURE_SPEECH_REGION=westeurope")
        sys.exit(1)

    return key, region


def create_translation_recognizer(key, region):
    """Build a TranslationRecognizer that listens to the default microphone."""
    translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=key,
        region=region,
    )
    translation_config.speech_recognition_language = SOURCE_LANGUAGE
    translation_config.add_target_language(TARGET_LANGUAGE)

    # Use the default system microphone
    audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)

    recognizer = speechsdk.translation.TranslationRecognizer(
        translation_config=translation_config,
        audio_config=audio_config,
    )
    return recognizer


def create_synthesizer(key, region):
    """Build a SpeechSynthesizer that plays through the default speakers."""
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_synthesis_voice_name = TARGET_VOICE

    # Default audio output = system speakers
    audio_output = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_output,
    )
    return synthesizer


def main():
    key, region = get_credentials()

    print("=" * 64)
    print("  VAVILON — Live Translation Test")
    print("=" * 64)
    print(f"  Region          : {region}")
    print(f"  Source language  : {SOURCE_LANGUAGE}")
    print(f"  Target language  : {TARGET_LANGUAGE} ({TARGET_LOCALE})")
    print(f"  TTS voice        : {TARGET_VOICE}")
    print("-" * 64)
    print("  Initializing Azure Speech SDK...")
    print()

    # ── Create SDK objects ────────────────────────────────────────────────────
    recognizer  = create_translation_recognizer(key, region)
    synthesizer = create_synthesizer(key, region)

    print("[OK] TranslationRecognizer created (microphone input)")
    print("[OK] SpeechSynthesizer created (speaker output)")
    print()
    print("Speak into your microphone.  Press Ctrl+C to stop.")
    print("-" * 64)

    # Shared state
    stop_event = threading.Event()
    recognition_count = 0

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_recognizing(evt):
        """Fired for partial / interim results (while still speaking)."""
        if evt.result.reason == speechsdk.ResultReason.TranslatingSpeech:
            partial = evt.result.text
            if partial:
                print(f"  ... hearing: {partial}", end="\r")

    def on_recognized(evt):
        """Fired when a full sentence is recognized + translated."""
        nonlocal recognition_count

        t_start = time.perf_counter()

        if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
            recognized_text = evt.result.text
            translations = evt.result.translations

            t_recognition = time.perf_counter()
            recognition_count += 1

            translated_text = translations.get(TARGET_LANGUAGE, "")

            print()  # clear partial line
            print(f"┌─ Recognition #{recognition_count} ─────────────────────────")
            print(f"│ English : {recognized_text}")
            print(f"│ {TARGET_LOCALE:<7}: {translated_text}")

            recognition_ms = (t_recognition - t_start) * 1000
            print(f"│ Recognition callback time : {recognition_ms:.1f} ms")

            if not translated_text:
                print("│ (no translation — skipping TTS)")
                print("└───────────────────────────────────────────")
                return

            # ── Synthesize ────────────────────────────────────────────────
            t_tts_start = time.perf_counter()

            tts_result = synthesizer.speak_text_async(translated_text).get()

            t_tts_end = time.perf_counter()
            tts_ms = (t_tts_end - t_tts_start) * 1000
            total_ms = (t_tts_end - t_start) * 1000

            if tts_result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                print(f"│ TTS synthesis + playback  : {tts_ms:.1f} ms")
                print(f"│ Total (recognition→audio) : {total_ms:.1f} ms")
                print("│ [PLAYED through speakers]")
            elif tts_result.reason == speechsdk.ResultReason.Canceled:
                cancellation = tts_result.cancellation_details
                print(f"│ TTS CANCELED: {cancellation.reason}")
                print(f"│   Error code : {cancellation.error_code}")
                print(f"│   Details    : {cancellation.error_details}")

            print("└───────────────────────────────────────────")

        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            print("\n  [no speech detected]")

    def on_canceled(evt):
        """Fired when recognition is canceled (error or end of stream)."""
        cancellation = evt.result.cancellation_details
        print()
        print("!" * 64)
        print(f"  RECOGNITION CANCELED")
        print(f"  Reason     : {cancellation.reason}")

        if cancellation.reason == speechsdk.CancellationReason.Error:
            print(f"  Error code : {cancellation.error_code}")
            print(f"  Details    : {cancellation.error_details}")
            print()
            print("  Possible causes:")
            print("    - Invalid AZURE_SPEECH_KEY or AZURE_SPEECH_REGION")
            print("    - Network connectivity issue")
            print("    - Quota exceeded on your Azure Speech resource")

        print("!" * 64)
        stop_event.set()

    def on_session_stopped(evt):
        """Fired when the recognition session ends."""
        print("\n  [session stopped]")
        stop_event.set()

    # ── Wire up events ────────────────────────────────────────────────────────
    recognizer.recognizing.connect(on_recognizing)
    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_session_stopped)

    # ── Start continuous recognition ──────────────────────────────────────────
    recognizer.start_continuous_recognition_async().get()

    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        print("\n")
        print("-" * 64)
        print(f"  Stopping... ({recognition_count} utterances translated)")

    # ── Clean shutdown ────────────────────────────────────────────────────────
    recognizer.stop_continuous_recognition_async().get()
    print("  Recognition stopped.")
    print("  Goodbye!")


if __name__ == "__main__":
    main()
