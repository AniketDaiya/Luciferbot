import os
import sys
import queue
import logging
import threading
import subprocess
import platform

logger = logging.getLogger("LuciferTTS")
logger.setLevel(logging.INFO)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(levelname)s [TTS]: %(message)s'))
    logger.addHandler(console_handler)

# Global variables for speech configuration (can be updated dynamically)
_voice_speed = 150
_voice_volume = 0.8

class TTSEngine:
    """
    Centralized, thread-safe, non-blocking, and cleanly interruptible TTS engine.
    Uses isolated OS subprocesses to execute SAPI5 speech on Windows,
    completely avoiding multi-threaded COM apartment collisions or stuck loops.
    """

    def __init__(self):
        self.queue = queue.Queue()
        self.running = True
        self.current_process = None
        self._is_speaking_event = threading.Event()
        
        # Start background loop
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _worker_loop(self):
        while self.running:
            try:
                # Wait for speech request
                text = self.queue.get(timeout=0.2)
                
                # Set speaking states
                self._is_speaking_event.set()
                logger.info(f"Speaking: {text}")
                
                # Execute in an isolated Python subprocess
                # This guarantees SAPI5 has a fresh COM environment and can be cleanly killed
                cmd = [
                    sys.executable,
                    "-c",
                    f"import pyttsx3; e=pyttsx3.init(); e.setProperty('rate', {_voice_speed}); e.setProperty('volume', {_voice_volume}); e.say({repr(text)}); e.runAndWait()"
                ]
                
                try:
                    self.current_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    self.current_process.wait()
                except Exception as e:
                    logger.error(f"Error executing speech process: {e}")
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in TTS background worker: {e}")
            finally:
                self.current_process = None
                self._is_speaking_event.clear()
                try:
                    self.queue.task_done()
                except ValueError:
                    pass

    def speak(self, text: str) -> None:
        """Queue text to be spoken asynchronously."""
        if not text or not text.strip():
            return
        # Clean string to avoid command line argument issues
        text_clean = text.strip()
        self.queue.put(text_clean)

    def interrupt(self) -> None:
        """Instantly stop all speech output and clear the queue."""
        logger.info("Interrupting speech output...")
        
        # Clear queue
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except (queue.Empty, ValueError):
                break
        
        # Terminate active speaking process
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.kill()
            except Exception as e:
                logger.warning(f"Error terminating speech subprocess: {e}")
            self.current_process = None
            
        self._is_speaking_event.clear()

    def is_speaking(self) -> bool:
        """Check if engine is currently speaking."""
        return self._is_speaking_event.is_set()

    def close(self) -> None:
        """Clean up resources on shutdown."""
        self.running = False
        self.interrupt()

    def shutdown(self) -> None:
        """Alias for close."""
        self.close()

# Singleton TTS engine instance
tts = TTSEngine()
