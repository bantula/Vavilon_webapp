import numpy as np
import queue
import socket
import wave
import time
from babellore.logging.logging_config import get_audio_logger
from babellore.config.settings_manager import SettingsManager

# Initialize logger and settings
logger = get_audio_logger()
settings = SettingsManager()

class UdpAudioStreamer():

    def __init__(self, SEND_IP, CHUNK_SIZE=None):
        UDP_IP_BIND = ""
        self._UDP_IP_SEND = SEND_IP
        
        # Use configuration for network settings
        UDP_PORT_SEND = settings.get('network', 'udp_send_port', 5005)
        _UDP_IP = ""
        self._UDP_PORT_SEND = settings.get('network', 'udp_receive_port', 5006)
        
        # Use provided chunk size or get from configuration
        if CHUNK_SIZE is not None:
            self._CHUNK_SIZE = CHUNK_SIZE
        else:
            self._CHUNK_SIZE = settings.get('network', 'udp_packet_size', 2048)
        
        self.busy = False
        
        self._sock_send = socket.socket(socket.AF_INET, # Internet
                     socket.SOCK_DGRAM) # UDP

        self._queue = queue.Queue()
        self._kill_thread = False

    def send_data(self,arr):
        data = np.frombuffer(arr, dtype=np.int16)
        padding = self._CHUNK_SIZE - (data.size % self._CHUNK_SIZE)
        data = np.pad(data,(0,padding))
        self._queue.put_nowait(data)

    def _transmit(self):
            # Get transmission configuration from settings
            transmission_delay = settings.get('network', 'udp_transmission_delay', 0.080)
            polling_interval = settings.get('network', 'udp_polling_interval', 0.001)
            
            while True:
                if self._queue.empty() == False :
                    current_msg = np.array(self._queue.get_nowait()).reshape((-1,self._CHUNK_SIZE))
                    
                    for i in range(1, current_msg.shape[0]):
                        self._sock_send.sendto(current_msg[i], (self._UDP_IP_SEND, self._UDP_PORT_SEND))
                        time.sleep(transmission_delay)
                else:
                    time.sleep(polling_interval)
                        
                if self._kill_thread:
                    self._queue.queue.clear()
                    self._sock_send.close()
                    logger.debug("Audio streaming thread terminated")
                    return
                
    def kill_thread(self):
        self._kill_thread = True

                                    
if __name__ == "__main__":
    # Example usage
    # Create a wave file to read
    wave_file = wave.open('Audio-Dubber/Test-Output/synthesized_1749217557.wav', 'rb')
    samples = wave_file.getnframes()
    raw = wave_file.readframes(samples)

    test = UdpAudioStreamer("192.168.0.149",2048*4)

    test.send_data(raw)
    logger.debug("Audio queue empty: %s", test._queue.empty())
    test._transmit()
