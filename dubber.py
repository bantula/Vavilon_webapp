import os
import numpy as np
from dotenv import load_dotenv
import time
import threading
import queue
import wave
import io
import json
import simpleaudio as sa
import azure.cognitiveservices.speech as speechsdk
from babellore.core.auxiliary_functions import *
from babellore.stream.audio_interface import UdpAudioStreamer
from babellore.logging.logging_config import get_audio_logger, get_speech_logger
from babellore.config.settings_manager import SettingsManager

class AudioDubber:
    def __init__(self, clients_file_path="config/clients.json"):
        """
        Initializes the AudioDubber instance responsible for translating and broadcasting speech.
    
        Parameters:
            clients_file_path: Path to the JSON file storing client metadata (default: 'Audio-Dubber/clients.json').
        
        ## Internal Variables:
        
            _brodcast, _playback: Flags indicating whether audio should be broadcasted and/or played locally.
            _translation_recognizer: Azure TranslationRecognizer object for speech-to-text and translation.
            _synthesizers: Dict of SpeechSynthesizer objects, one per target language.
            _translation_thread: Thread handling real-time translation from source speech.
            _synth_threads: Dict of threads synthesizing speech for each language.Each entry is a dict consisting of a thread and a stop flag.
            _recognised_text_queue: Queue of recognized (source) text waiting to be translated.
            _translated_text_queues: Dict of queues for translated text, per language.
            _done_event: Event signaling that all processing is finished.
            _stop_event: Event used to request all threads to terminate.
            _clients_file_path: Filepath to the client configuration JSON.
            _source_language: Language code of the speaker's input language.
            _target_languages: List of language codes for translation output.
            _clients: Dict of clients loaded from the configuration file.
            _brodcast_packet_size: Size (in bytes) of each audio chunk sent over the network.
        """
        # Load settings configuration
        self.settings = SettingsManager()
        
        # Load environment variables for Azure Speech service.
        load_dotenv()
        self._speech_key = str(os.getenv('SPEECH_KEY'))
        self._speech_region = str(os.getenv('SPEECH_REGION'))
        
        # Initialize loggers
        self.logger = get_audio_logger()
        self.speech_logger = get_speech_logger()
        self.logger.info("AudioDubber initialized with clients file: %s", clients_file_path)
        
        if not self._speech_key or not self._speech_region:
            self.logger.error("Missing Azure credentials - SPEECH_KEY: %s, SPEECH_REGION: %s", 
                            self._speech_key, self._speech_region)
            raise ValueError("SPEECH_KEY and SPEECH_REGION must be set in the environment variables.")
        
        self._brodcast = False
        self._playback = False
        
        self._translation_recognizer = None
        self._synthesizers = {}
        
        self._translation_thread = None
        self._synth_threads = {}
        
        self._recognised_text_queue = queue.Queue()
        self._translated_text_queues = {}
        self._done_event = threading.Event()
        self._stop_event = threading.Event()
        
        self._clients_file_path = clients_file_path
        self._source_language = None
        self._target_languages =  None
        self._clients = None
        
        # Load network configuration from settings
        self._brodcast_packet_size = self.settings.get('network', 'udp_packet_size', 2048)
        
        self.logger.info("AudioDubber initialized with clients file: %s", clients_file_path)
    
    
    def set_clients_file_path(self, file_path):
        """
        Sets the path to the clients file and loads the clients.

        Parameters:
            file_path (str): Path to the JSON file containing client information.
        """
        if not isinstance(file_path, str):
            raise TypeError("file_path must be a string.")
        self._clients_file_path = file_path
    
    
    def stop_dubbing(self):
        """
        Stop the current audio dubbing process by setting the stop event.
        This will signal all threads to terminate gracefully.
        """
        self.logger.info("Stopping audio dubbing...")
        self._stop_event.set()
        
        # Get thread stop timeouts from settings
        translation_timeout = self.settings.get('azure', 'translation_timeout', 3.0)
        synthesis_timeout = self.settings.get('azure', 'synthesis_timeout', 2.0)
        
        # Stop the translation thread
        if self._translation_thread and self._translation_thread.is_alive():
            self.logger.info(f"Waiting for translation thread to stop (timeout: {translation_timeout}s)...")
            self._translation_thread.join(timeout=translation_timeout)
        
        # Wait for synthesis threads to finish
        if hasattr(self, '_synth_threads'):
            for lang, thread_info in self._synth_threads.items():
                if thread_info and "thread" in thread_info and thread_info["thread"].is_alive():
                    self.logger.info(f"Waiting for synthesis thread for {lang} to stop (timeout: {synthesis_timeout}s)...")
                    thread_info["thread"].join(timeout=synthesis_timeout)
        
        # Stop broadcast threads for all clients
        if hasattr(self, '_clients') and self._clients:
            for mac, client in self._clients.items():
                if "interface" in client and hasattr(client["interface"], "kill_thread"):
                    try:
                        self.logger.info(f"Stopping broadcast thread for client {client.get('name', mac)}...")
                        client["interface"].kill_thread()
                    except Exception as e:
                        self.logger.warning(f"Failed to stop broadcast thread for {client.get('name', mac)}: {e}")
        
        # Properly cleanup speech resources to release microphone
        if hasattr(self, '_translation_recognizer') and self._translation_recognizer:
            try:
                self.logger.info("Cleaning up speech recognizer...")
                self._translation_recognizer.stop_continuous_recognition()
                self._translation_recognizer = None
            except:
                pass
        
        # Reset events for next start
        if hasattr(self, '_stop_event'):
            self._stop_event.clear()
        if hasattr(self, '_done_event'):
            self._done_event.clear()
        
        # Clear synthesis thread references
        if hasattr(self, '_synth_threads'):
            for lang in list(self._synth_threads.keys()):
                if self._synth_threads[lang] and "running" in self._synth_threads[lang]:
                    self._synth_threads[lang]["running"] = False
        
        # Force garbage collection to release audio resources
        import gc
        gc.collect()
        
        self.logger.info("Audio dubbing stopped and resources released.")
        
        # Add visual separator for dubbing session end
        self.logger.info("=" * 80)
        self.logger.info("🔴 AUDIO DUBBING SESSION ENDED")
        self.logger.info("=" * 80)
    
    
    def _load_clients(self):
        '''
        Loads client information from a JSON file and initializes UdpAudioStreamer objects for each client. 
        It initializes the translation threads for each needed language.
        '''
        if self._clients_file_path:
            with open(self._clients_file_path, 'r', encoding='utf-8') as f:
                clients = json.load(f)
                
                # Clean up existing broadcast threads before reinitializing
                if hasattr(self, '_clients') and self._clients:
                    self.logger.info("Cleaning up existing broadcast threads...")
                    for mac, existing_client in self._clients.items():
                        if "interface" in existing_client and hasattr(existing_client["interface"], "kill_thread"):
                            try:
                                existing_client["interface"].kill_thread()
                                self.logger.info(f"Stopped existing broadcast thread for client {existing_client.get('name', mac)}")
                            except Exception as e:
                                self.logger.warning(f"Failed to stop existing broadcast thread for {existing_client.get('name', mac)}: {e}")
                
                # Clean up existing synthesis threads before reinitializing
                if hasattr(self, '_synth_threads') and self._synth_threads:
                    cleanup_timeout = self.settings.get('azure', 'synthesis_timeout', 2.0) / 2  # Use half the synthesis timeout for cleanup
                    self.logger.info("Cleaning up existing synthesis threads...")
                    for lang, thread_info in self._synth_threads.items():
                        if thread_info and "running" in thread_info:
                            thread_info["running"] = False
                            if "thread" in thread_info and thread_info["thread"].is_alive():
                                self.logger.info(f"Stopping existing synthesis thread for language {lang} (timeout: {cleanup_timeout}s)")
                                thread_info["thread"].join(timeout=cleanup_timeout)
                
                self._clients = {client["mac"]: client for client in clients}
                # Initialise UdpAudioStreamer object for each client.
                for client in self._clients.values():
                    if "ip" in client:
                        client["interface"] = UdpAudioStreamer(client["ip"], self._brodcast_packet_size)
                        # Initialise brodcast threads for each client.
                        client["brodcast_thread"] = threading.Thread(target=client["interface"]._transmit, daemon=True)
                        client["brodcast_thread"].start()
                        self.logger.info(f"Initialized UdpAudioStreamer for client {client['name']} at IP {client['ip']}")
                    else:
                        client["interface"] = None
                        self.logger.warning(f"Client {client['name']} does not have an IP address specified. Skipping UdpAudioStreamer initialization.")
                # Make a list of needed languages.
                self._target_languages = {client["language"] for client in clients}
                self.logger.info(f"Loaded target languages: {self._target_languages}")
        
            # Start voice synthesis for each language in a separate thread.
            # TODO: Add a way to pick the voice for a language or pick a default voice. Maybe add a file that holds what is the best voice for each langugage.    
            for lang in self._target_languages:
                self.logger.debug(f"LANGUAGE STRING TYPE: {type(lang)}")
                self.logger.info(f"Started voice synthesis thread for language {lang}") 
                # Crate a queue for the language
                self._translated_text_queues[lang] = queue.Queue()
                
                # Create a synthesizer that returns audio data in memory.
                speech_config = speechsdk.SpeechConfig(subscription=self._speech_key, region=self._speech_region)
                #speech_config.speech_synthesis_voice_name = self._voices[lang] if lang in self._voices else "en-US-JennyNeural"  # Default voice if not specified.
                speech_config.speech_synthesis_language = lang
                self._synthesizers[lang] = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)  
                self._synth_threads[lang] = {}
                self._synth_threads[lang]["thread"] = threading.Thread(target=self._voice_synth, args=(lang,), daemon=True)
                self._synth_threads[lang]["running"] = True
                self._synth_threads[lang]["thread"].start()
        
        else:
            self.logger.error(f"No file at {self._clients_file_path}. Please set a valid path using set_clients_file_path().")    
            
        
        
    def update_clients(self, clients_file_path=None):
        """
        Updates the clients by reloading the clients file. 
        This method can be called to refresh the list of clients and their languages.
        Removes unneeded languages from self._recogniser and adds new languages if needed.
        It also initializes the UdpAudioStreamer for each new client and deletes the unneeded ones.
        It deletes and starts new threads for old/new languages.
        
        Parameters:
            clients_file_path
        """
        
        if clients_file_path is None: 
            clients_file_path = self._clients_file_path
        
        if clients_file_path:
            with open(clients_file_path, 'r', encoding='utf-8') as f:
                loaded_clients = {client["mac"]: client for client in json.load(f)}
                if self._clients is None:
                    self._load_clients()
                    return
                # Add new clients and delete unneeded ones.
                for client in list(self._clients.values()):
                    mac = client["mac"]
                    if mac not in loaded_clients:
                        # If the client is not in the new list, remove it.
                        self.logger.info(f"Removing client {client['name']} with MAC {mac} as it is no longer in the new list.")
                        if "brodcast_thread" in client:
                            # Stop the brodcast thread for the client.
                            client["interface"].kill_thread()
                        del self._clients[mac]
                    else:
                        # If the client is in the new list, update its information.
                        # If the ip address changed reinitialize the UdpAudioStreamer.
                        loaded_client = loaded_clients[mac]
                        if client["ip"] != loaded_client["ip"]:
                            self.logger.info(f"Updating client {client['name']} with MAC {mac} to new IP {loaded_client['ip']}.")
                            if "brodcast_thread" in client:
                                # Stop the brodcast thread for the client.
                                client["interface"].kill_thread()
                            loaded_client["interface"] = UdpAudioStreamer(loaded_client["ip"], self._brodcast_packet_size)
                            loaded_client["brodcast_thread"] = threading.Thread(target=loaded_client["interface"]._transmit, daemon=True)
                            loaded_client["brodcast_thread"].start()
                            self._clients[mac] = loaded_client
                        # If the language changed, update it.
                        if client["language"] != loaded_client["language"]:
                            self.logger.info(f"Updating client {client['name']} with MAC {mac} to new language {loaded_client['language']}.")
                            client["language"] = loaded_client["language"]
                            
                
                # Add new clients that are not in the current list.
                for mac, loaded_client in loaded_clients.items():
                    if mac not in self._clients.keys():
                        self.logger.info(f"Adding new client {loaded_client['name']} with MAC {mac}.")
                        self._clients[mac] = loaded_client
                        self._clients[mac]["interface"] = UdpAudioStreamer(self._clients[mac]["ip"], self._brodcast_packet_size)
                        self._clients[mac]["brodcast_thread"] = threading.Thread(target=self._clients[mac]["interface"]._transmit, daemon=True)
                        self._clients[mac]["brodcast_thread"].start()
                        
                # Update the target languages.
                self._target_languages = {client["language"] for client in self._clients.values()}
                # Update the recogniser with the new languages.
                if self._translation_recognizer is not None:
                    # Remove languages that are no longer needed.
                    for lang in list(self._translation_recognizer.target_languages):
                        if lang not in self._target_languages:
                            self.logger.info(f"Removing language {lang} from recogniser as it is no longer needed.")
                            self._translation_recognizer.remove_target_language(lang)
                    # Add new languages that are needed.
                    for lang in self._target_languages:
                        if lang not in self._translation_recognizer.target_languages:
                            self.logger.info(f"Adding language {lang} to recogniser.")
                            self._translation_recognizer.add_target_language(lang)
                            
                    # Print the updated target languages.
                    self.logger.info(f"Updated target languages: {self._target_languages}")
                else:
                    self.logger.warning("No recogniser initialized. Please initialize the recogniser before updating clients.")
                 
                    
                # Kill synth threads that synthesize languages that are no longer needed.
                for lang, item in self._synth_threads.items():
                    if lang not in self._target_languages:
                        self.logger.info(f"Removing synth thread for language {lang} as it is no longer needed.")
                        self._synth_threads[lang]["running"] = False
                        # Wait for the thread to finish.
                        item["thread"].join(timeout=0.5)
                        # del self._synth_threads[lang]
                        
                        
                # Add queues for new languages and remove unneeded ones.
                for lang in self._target_languages:
                    if lang not in self._translated_text_queues:
                        self.logger.info(f"Adding new translated text queue for language {lang}.")
                        self._translated_text_queues[lang] = queue.Queue()
                    else:
                        self.logger.debug(f"Keeping existing translated text queue for language {lang}.")
                        
                for lang in list(self._translated_text_queues.keys()):
                    if lang not in self._target_languages:
                        self.logger.info(f"Removing translated text queue for language {lang} as it is no longer needed.")
                        del self._translated_text_queues[lang]
                        
                        
                # Start synth threads for new languages that are needed.
                for lang in self._target_languages:
                    if lang not in self._synth_threads.keys():
                        # If the language is not in the synth threads, start a new thread for it.
                        self.logger.info(f"Starting synth thread for new language {lang}.")
                        self._synth_threads[lang] = {}
                        self._synth_threads[lang]["thread"] = threading.Thread(target=self._voice_synth, args=(lang,), daemon=True)
                        self._synth_threads[lang]["running"] = True
                        self._synth_threads[lang]["thread"].start()
                        # Create a synthesizer that returns audio data in memory.
                        speech_config = speechsdk.SpeechConfig(subscription=self._speech_key, region=self._speech_region)
                        #speech_config.speech_synthesis_voice_name = self._voices[lang] if lang in self._voices else "en-US-JennyNeural"  # Default voice if not specified.
                        speech_config.speech_synthesis_language = lang
                        self._synthesizers[lang] = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
                        
            self.logger.info("Clients updated successfully.")
               
                        
        
        else:
            self.logger.error(f"No file at {clients_file_path}. Please set a valid path using set_clients_file_path().")
        
        
    def update_client_languages(self, language_mappings):
        """
        Update the languages for specific clients without full client reload.
        This is more efficient than update_clients() for language-only changes.
        
        Args:
            language_mappings (dict): Dictionary mapping client MAC addresses to new languages
                                     {'mac_address': 'new_language_code'}
        """
        if not language_mappings:
            self.logger.warning("No language mappings provided")
            return False
            
        if not self._clients:
            self.logger.warning("No clients loaded, call _load_clients() first")
            return False
            
        try:
            updated_clients = []
            
            # Update client languages in memory
            for mac_address, new_language in language_mappings.items():
                if mac_address in self._clients:
                    old_language = self._clients[mac_address].get('language', 'unknown')
                    self._clients[mac_address]['language'] = new_language
                    updated_clients.append({
                        'mac': mac_address,
                        'name': self._clients[mac_address].get('name', 'Unknown'),
                        'old_language': old_language,
                        'new_language': new_language
                    })
                    self.logger.info(f"Updated {self._clients[mac_address].get('name', 'Unknown')} ({mac_address}): {old_language} → {new_language}")
            
            if not updated_clients:
                self.logger.warning("No matching clients found in memory")
                return False
            
            # Update target languages set
            old_target_languages = self._target_languages.copy()
            self._target_languages = {client["language"] for client in self._clients.values()}
            
            self.logger.info(f"Target languages updated: {old_target_languages} → {self._target_languages}")
            
            # Update translation recognizer if active
            if self._translation_recognizer is not None:
                # Remove languages that are no longer needed
                for lang in old_target_languages:
                    if lang not in self._target_languages:
                        self.logger.info(f"Removing language {lang} from recognizer")
                        try:
                            self._translation_recognizer.remove_target_language(lang)
                        except Exception as e:
                            self.logger.warning(f"Could not remove language {lang}: {e}")
                
                # Add new languages that are needed
                for lang in self._target_languages:
                    if lang not in old_target_languages:
                        self.logger.info(f"Adding language {lang} to recognizer")
                        try:
                            self._translation_recognizer.add_target_language(lang)
                        except Exception as e:
                            self.logger.warning(f"Could not add language {lang}: {e}")
                
                self.logger.info(f"Recognizer updated with target languages: {self._target_languages}")
            
            # Update synthesis queues for new languages
            for lang in self._target_languages:
                if lang not in self._translated_text_queues:
                    self.logger.info(f"Adding translated text queue for new language {lang}")
                    self._translated_text_queues[lang] = queue.Queue()
            
            # Remove queues for languages no longer needed
            for lang in list(self._translated_text_queues.keys()):
                if lang not in self._target_languages:
                    self.logger.info(f"Removing translated text queue for language {lang}")
                    del self._translated_text_queues[lang]
            
            # Handle synthesis threads for new/removed languages
            # Stop threads for languages no longer needed
            for lang in list(self._synth_threads.keys()):
                if lang not in self._target_languages:
                    self.logger.info(f"Stopping synthesis thread for language {lang}")
                    if lang in self._synth_threads and "running" in self._synth_threads[lang]:
                        self._synth_threads[lang]["running"] = False
                        # Wait for thread to finish
                        if "thread" in self._synth_threads[lang]:
                            self._synth_threads[lang]["thread"].join(timeout=1.0)
            
            # Start threads for new languages
            for lang in self._target_languages:
                if lang not in self._synth_threads or not self._synth_threads[lang].get("running", False):
                    self.logger.info(f"Starting synthesis thread for language {lang}")
                    
                    # Create synthesizer configuration
                    speech_config = speechsdk.SpeechConfig(subscription=self._speech_key, region=self._speech_region)
                    speech_config.speech_synthesis_language = lang
                    audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=False)
                    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
                    
                    # Set up synthesis thread
                    self._synth_threads[lang] = {
                        "running": True,
                        "synthesizer": synthesizer,
                        "thread": threading.Thread(target=self._voice_synth, args=(lang,), daemon=True)
                    }
                    self._synth_threads[lang]["thread"].start()
            
            self.logger.info(f"Successfully updated {len(updated_clients)} client language(s)")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating client languages: {e}")
            return False
        
        
    def dub_audio_live(self, source_language, silence_timeout_ms=500, playback=False, brodcast=False):
        """
        Starts the live audio dubbing process, which includes recognizing speech from the microphone,
        translating it, and synthesizing the translated text back to audio.
        Oprionaly play back the audio to the speakers.
        Optionaly brodcast audio to users written in clients.json.

        Parameters:
            source_language (str): The language spoken by the user (e.g., "en-US").
            silence_timeout_ms (int): Timeout in milliseconds for segment recognition.
            playback (bool): If True, plays back the synthesized audio.
            brodcast (UdpAudioStreamer): Optional brodcast interface for sending audio data.
        """
        
        # Add visual separator for live audio dubbing session
        self.logger.info("=" * 80)
        self.logger.info("🎙️ LIVE AUDIO DUBBING SESSION STARTING")
        self.logger.info("=" * 80)
        
        # Initialize/reset threading events for clean start
        if not hasattr(self, '_stop_event') or self._stop_event is None:
            self._stop_event = threading.Event()
        if not hasattr(self, '_done_event') or self._done_event is None:
            self._done_event = threading.Event()
        
        # Ensure events are cleared for fresh start
        self._stop_event.clear()
        self._done_event.clear()
        
        # Load clients and target languages.
        self._load_clients()
        self._playback = playback
        self._brodcast = brodcast

        # Validate silence_timeout_ms.
        if not isinstance(silence_timeout_ms, int):
            raise TypeError("silence_timeout_ms must be an integer.")
        if silence_timeout_ms < 0:
            raise ValueError("silence_timeout_ms must be non-negative.")
        self.logger.info(f"Setting silence timeout to {silence_timeout_ms} ms")
        
        # Initialize queues and events.
        self._recognized_text_queue = queue.Queue()
        self._translated_text_queues = {lang: queue.Queue() for lang in self._target_languages}
        self._done_event.clear()
        self._stop_event.clear()

        # Start translation in a separate thread.
        self._translation_thread = threading.Thread(target=self._translate_live_audio, args=(source_language, silence_timeout_ms), daemon=True)
        self._translation_thread.start()
        
        # voice_names = []
        # for lang in self._target_languages:
        #     voice_name = pick_voice_interface(lang, self._speech_key, self._speech_region)
        #     voice_names.append(voice_name)
        # # Create a map where for voices["en-US"] = "en-US-JennyNeural", for example.
        # voices = {lang: voice for lang, voice in zip(self._target_languages, voice_names)}

        

        # Wait for threads to finish.
        self._translation_thread.join()
        for item in self._synth_threads.values():
            item["thread"].join()
        # Signal that the dubbing is complete.
        self._done_event.set()
        self.logger.info("Live audio dubbing complete.")
        
    
    def dub_audio_from_file(self, source_language, audio_file_path, silence_timeout_ms=500, playback=False, brodcast=False):
        """
        Translates audio from a file and synthesizes the translated text back to audio.
        Optionaly play back the audio to the speakers.
        Optionaly brodcast audio to users written in clients.json.

        Parameters:
            source_language (str): The language spoken in the audio file (e.g., "en-US").
            audio_file_path (str): Path to the audio file to be translated.
            silence_timeout_ms (int): Timeout in milliseconds for segment recognition.
            playback (bool): If True, plays back the synthesized audio.
            brodcast (UdpAudioStreamer): Optional brodcast interface for sending audio data.
        """
        
        # Add visual separator for file audio dubbing session
        self.logger.info("=" * 80)
        self.logger.info("📁 FILE AUDIO DUBBING SESSION STARTING")
        self.logger.info("=" * 80)
        
        # Load clients and target languages.
        self._load_clients()
        self._playback = playback
        self._brodcast = brodcast
        
        # Validate silence_timeout_ms.
        if not isinstance(silence_timeout_ms, int):
            raise TypeError("silence_timeout_ms must be an integer.")
        if silence_timeout_ms < 0:
            raise ValueError("silence_timeout_ms must be non-negative.")
        self.logger.info(f"Setting silence timeout to {silence_timeout_ms} ms")
        
        # Initialize queues and events.
        self._recognized_text_queue = queue.Queue()
        self._translated_text_queues = {lang: queue.Queue() for lang in self._target_languages}
        self._done_event.clear()
        self._stop_event.clear()

        # Start translation in a separate thread.
        self._translation_thread = threading.Thread(target=self._translate_audio_file, args=(source_language, audio_file_path, silence_timeout_ms), daemon=True)
        self._translation_thread.start()

        # Wait for threads to finish.
        self._translation_thread.join()
        for item in self._synth_threads.values():
            item["thread"].join()
        
        # Signal that the dubbing is complete.
        self._done_event.set()
        self.logger.info("Audio file dubbing complete.")
    
    
    def _translate_live_audio(self, source_language, silence_timeout_ms=500):
        """
        Performs continuous translation from live microphone audio stream.
        As segments are recognized, the recognized text and its translation are enqueued.
        Target languages are stored in self._target_languages

        Parameters:
            source_language (str): The language spoken by the user (e.g., "en-US").
            silence_timeout_ms (int): Timeout in milliseconds for segment recognition.
        """        
        # Set up translation configuration.
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=self._speech_key, region=self._speech_region)
        translation_config.speech_recognition_language = source_language
        
        # Add target languages for translation.
        for lang in self._target_languages:
            translation_config.add_target_language(lang)


        # Use the USB microphone (Card 2) specifically
        try:
            self.logger.info("Configuring audio for USB microphone (Card 2)...")
            # On Orange Pi, allow extra time for USB audio initialization
            self.logger.info("Waiting for USB audio device to initialize...")
            time.sleep(2)
            
            # Try to use specific USB audio device
            os.environ['ALSA_PCM_CARD'] = '2'
            os.environ['ALSA_PCM_DEVICE'] = '0'
            
            # Try multiple methods to access USB microphone
            audio_config = None
            device_configured = False
            
            # Method 1: Try ALSA device names in order of preference
            # Orange Pi optimized order - default first for better compatibility
            device_names = [
                "default",           # System default (works best on Orange Pi)
                "usb_mic",           # Our custom ALSA device
                "plughw:2,0",        # Hardware with conversion
                "hw:2,0",            # Direct hardware
            ]
            
            for device_name in device_names:
                try:
                    if device_name == "default":
                        audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
                    else:
                        audio_config = speechsdk.audio.AudioConfig(device_name=device_name)
                    self.logger.info(f"✓ USB microphone configured using: {device_name}")
                    device_configured = True
                    break
                except Exception as e:
                    self.logger.warning(f"✗ Failed to configure with {device_name}: {str(e)[:100]}")
                    continue
            
            if not device_configured:
                self.logger.warning("All device configuration methods failed, using default...")
                audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
            
            self.logger.info("USB microphone audio configuration successful")
        except Exception as e:
            self.logger.error(f"Error configuring USB microphone: {e}")
            self.logger.info("Falling back to default audio configuration...")
            try:
                audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
            except AttributeError:
                raise AttributeError("AudioConfig.from_default_microphone_input() not found. ")

        # Skip volume scaling - not available in this SDK version
        self.logger.debug("Skipping audio input volume scaling (not supported in this SDK version)")

        # Set silence timeout for segment recognition.
        if silence_timeout_ms is not None:
            if not isinstance(silence_timeout_ms, int):
                raise TypeError("silence_timeout_ms must be an integer.")
            elif silence_timeout_ms < 0:
                raise ValueError("silence_timeout_ms must be non-negative.")
            else:
                self.logger.info(f"Setting silence timeout to {silence_timeout_ms} ms")
                translation_config.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, str(silence_timeout_ms))

        # Create a TranslationRecognizer for continuous recognition.
        # Add retry logic for USB microphone device access
        max_retries = self.settings.get('azure', 'max_translation_retries', 3)
        retry_delay = self.settings.get('azure', 'retry_delay', 2.0)
        retry_count = 0
        
        self.logger.info("Starting microphone initialization with %d retry attempts", max_retries)
        
        while retry_count < max_retries:
            try:
                self.logger.debug("Attempting to create TranslationRecognizer (attempt %d/%d)", retry_count + 1, max_retries)
                self.logger.info(f"Attempting to create TranslationRecognizer (attempt {retry_count + 1}/{max_retries})...")
                self._translation_recognizer = speechsdk.translation.TranslationRecognizer(
                    translation_config=translation_config, audio_config=audio_config)
                self.logger.info("TranslationRecognizer created successfully on attempt %d", retry_count + 1)
                self.logger.info("✓ TranslationRecognizer created successfully!")
                break
                
            except Exception as e:
                if "SPXERR_MIC_NOT_AVAILABLE" in str(e) or "0xe" in str(e):
                    retry_count += 1
                    self.logger.warning("Microphone not available (attempt %d/%d): %s", retry_count, max_retries, str(e))
                    self.logger.warning(f"✗ Microphone not available (attempt {retry_count}/{max_retries})")
                    
                    if retry_count < max_retries:
                        self.logger.info(f"Waiting {retry_delay} seconds before retry")
                        self.logger.info(f"Waiting {retry_delay} seconds before retry...")
                        time.sleep(retry_delay)
                        
                        # Try to release any existing audio resources
                        try:
                            if hasattr(self, '_translation_recognizer') and self._translation_recognizer:
                                self._translation_recognizer = None
                        except:
                            pass
                            
                        # Force garbage collection to release resources
                        import gc
                        gc.collect()
                        
                        self.logger.info("Retrying with fresh audio configuration...")
                        # Recreate audio config for retry
                        if retry_count == 2:
                            # On second retry, try fallback to default device
                            self.logger.info("Trying fallback to default audio device...")
                            audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
                    else:
                        self.logger.error("All retry attempts failed.")
                        raise e
                else:
                    # Different error, don't retry
                    raise e

        def recognized_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
                if self._recognized_text_queue is not None:
                    self._recognized_text_queue.put(evt.result.text)
                
                # Log recognized speech to dedicated speech log
                self.speech_logger.info("RECOGNIZED [%s]: %s", self._source_language, evt.result.text)
                
                if self._translated_text_queues is not None:
                    for lang in self._target_languages:
                        if lang in evt.result.translations:
                            self._translated_text_queues[lang].put(evt.result.translations[lang])
                            # Log each translation to dedicated speech log
                            self.speech_logger.info("TRANSLATED [%s -> %s]: %s", 
                                                   self._source_language, lang, evt.result.translations[lang])
                    

        def session_stopped_cb(evt):
            self.logger.debug("Speech session stopped: %s", evt)
            self._done_event.set()

        def canceled_cb(evt):
            self.logger.warning("Speech recognition canceled: %s", evt)
            self._done_event.set()

        # Connect callbacks.
        self._translation_recognizer.recognized.connect(recognized_cb)
        self._translation_recognizer.session_stopped.connect(session_stopped_cb)
        self._translation_recognizer.canceled.connect(canceled_cb)

        # Start continuous recognition.
        self._translation_recognizer.start_continuous_recognition_async().get()
        self.logger.info("Live translation started. Speak into your microphone...")

        # Run until stop_event is set.
        while not self._stop_event.is_set():
            time.sleep(0.5)
            #clients() # TESTING: Update clients and target languages.

        # Stop recognition.
        self._translation_recognizer.stop_continuous_recognition_async().get()

        self._done_event.set()
        return 
    
    
    def _translate_audio_file(self, source_language, audio_file_path, silence_timeout_ms=500):
        """
        Translates audio from a file and enqueues the recognized text and its translations.
        
        Parameters:
            source_language (str): The language spoken in the audio file (e.g., "en-US").
            audio_file_path (str): Path to the audio file to be translated.
            silence_timeout_ms (int): Timeout in milliseconds for segment recognition.
        """
        
        # Set up translation configuration.
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=self._speech_key, region=self._speech_region)
        translation_config.speech_recognition_language = source_language
        
        # Add target languages for translation.
        for lang in self._target_languages:
            translation_config.add_target_language(lang)

        # Create an AudioConfig from the specified audio file.
        audio_config = speechsdk.audio.AudioConfig(filename=audio_file_path)

        # Set silence timeout for segment recognition.
        if silence_timeout_ms is not None:
            if not isinstance(silence_timeout_ms, int):
                raise TypeError("silence_timeout_ms must be an integer.")
            elif silence_timeout_ms < 0:
                raise ValueError("silence_timeout_ms must be non-negative.")
            else:
                self.logger.info(f"Setting silence timeout to {silence_timeout_ms} ms")
                translation_config.set_property(speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, str(silence_timeout_ms))

        # Boost gain with configurable volume scaling
        try:
            volume_scaling = str(self.settings.get('audio', 'volume_scaling', 3.0))
            translation_config.set_property(speechsdk.PropertyId.Speech_AudioInputVolumeScaling, volume_scaling)
            self.logger.info(f"Audio input volume scaling set to: {volume_scaling}")
        except AttributeError:
            self.logger.warning("AudioConfig.from_default_microphone_input() not found. Skipping gain boost.")

        # Create a TranslationRecognizer for file-based recognition.
        self._translation_recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config, audio_config=audio_config)

        def recognized_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
                if self._recognized_text_queue is not None:
                    self._recognized_text_queue.put(evt.result.text)
                
                # Log recognized speech to dedicated speech log
                self.speech_logger.info("FILE_RECOGNIZED [%s]: %s", self._source_language, evt.result.text)
                
                if self._translated_text_queues is not None:
                    for lang in self._target_languages:
                        if lang in evt.result.translations:
                            self._translated_text_queues[lang].put(evt.result.translations[lang])
                            # Log each translation to dedicated speech log
                            self.speech_logger.info("FILE_TRANSLATED [%s -> %s]: %s", 
                                                   self._source_language, lang, evt.result.translations[lang])
                    

        def session_stopped_cb(evt):
            self.logger.debug("File speech session stopped: %s", evt)
            self._done_event.set()

        def canceled_cb(evt):
            self.logger.warning("File speech recognition canceled: %s", evt)
            self._done_event.set()
        # Connect callbacks.
        self._translation_recognizer.recognized.connect(recognized_cb)
        self._translation_recognizer.session_stopped.connect(session_stopped_cb)
        self._translation_recognizer.canceled.connect(canceled_cb)
        # Start recognition from the audio file.
        self._translation_recognizer.start_continuous_recognition_async().get()
        self.logger.info(f"File translation started for {audio_file_path}. Processing audio...")
        # Run until stop_event is set.
        while not self._stop_event.is_set():
            time.sleep(0.5)
            #clients() # TESTING: Update clients and target languages.
        # Stop recognition.
        self._translation_recognizer.stop_continuous_recognition_async().get()
        self._done_event.set()
        self.logger.info(f"File translation complete for {audio_file_path}.")
        
        
        
    
    
    def _voice_synth(self, language, write_file=False, output_path=None):
        '''
        Synthesizes text from the apropriate translated text queue and plays it back or brodcasts it to appropriate devices.
        
        Parameters:
            language (str): eg. 'en-US' The language the synthesizer is synthesizing.
            write_file (bool): If True, writes the synthesized audio to a file.
            output_path (str): Path to the output file if write_file is True.
        '''
        
        complete_synthesys = None
        
        # Create a speech config with specified subscription key and service region.
        
        # if voice_name:
        #     print(f"[Using voice]: {voice_name}")
        #     speech_config.speech_synthesis_voice_name = voice_name

        
        
        while self._synth_threads[language]["running"]:
            if self._done_event.is_set() and self._translated_text_queues:
                break
            
            try:
                # Wait for a text segment from the queue (with timeout to allow stop_event check).
                text = self._translated_text_queues[language].get(timeout=0.5)
            except queue.Empty:
                continue
            except KeyError:
                self.logger.warning(f"No text segments in queue for language {language}.")
                continue

            result = self._synthesizers[language].speak_text_async(text).get()
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                #print("[Synthesis completed, playing audio]...")
                audio_bytes = result.audio_data
                
                # Send audio data over UDP.
                if self._brodcast:
                    # Send the audio data to all clients with the specified language.
                    for client in self._clients.values():
                        if client["language"] == language and client["interface"] is not None:
                            try:
                                client["interface"].send_data(audio_bytes)
                            except Exception as e:
                                self.logger.error(f"Error sending audio to {client['name']}: {e}")
                    
                if self._playback:
                    try:    
                        # Play the synthesized audio (expected to be in WAV format) using simpleaudio.
                        self.logger.debug("Playing audio...")
                        with wave.open(io.BytesIO(audio_bytes), 'rb') as wav_file:
                            wave_obj = sa.WaveObject.from_wave_read(wav_file)
                            play_obj = wave_obj.play()                 
                            play_obj.wait_done()  # Wait until playback finishes.
                    except Exception as e:
                        self.logger.error(f"Error during audio playback: {e}")
                
                if write_file:
                    if complete_synthesys is None:
                        complete_synthesys = audio_bytes
                    else:
                        complete_synthesys += audio_bytes
                
            else:
                self.logger.error(f"Synthesis failed for '{text}'. Reason: {result.reason}")
            self._translated_text_queues[language].task_done()    
        
        # If write_file is True write the audio it to a file.
        if write_file and complete_synthesys is not None:
            self._write_synthesized_audio(complete_synthesys, output_path)


    def _write_synthesized_audio(self, audio_data, output_path):
        
        # Write the synthesized audio data to a WAV file.
        with wave.open(output_path, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(audio_data)
        self.logger.info(f"Synthesized audio written to {output_path}")
