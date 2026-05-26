# =============================================================================
# LUCIFER VOICE WAKE MODULE
# =============================================================================
# Requirements (as specified):
#   - Wake word: "hey lucifer" (case-insensitive, partial match allowed)
#   - Libraries: SpeechRecognition, PyAudio, pyttsx3
#   - Platform: Windows (with Linux compatibility via minor config)
#   - Continuous background listening using speech_recognition with Google Web Speech API
#   - Wake word detection: play activation sound ("Online") and enter ACTIVE LISTENING mode
#   - Active mode: capture full command with 6-second timeout
#   - Pass raw text string to route_command(text) function
#   - Return to passive listening after routing
#   - Handle errors gracefully: mic not found, no internet, timeout - speak errors aloud
#   - DEACTIVATE phrase: "Lucifer sleep" to return to idle mode
#   - Log every captured command to lucifer_session.log with timestamp
# =============================================================================

import sys
import logging
import signal
import atexit
from datetime import datetime
from typing import Optional

import speech_recognition as sr
import pyttsx3


# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
# Wake and deactivation phrases (case-insensitive matching handled in code)
WAKE_PHRASE = "hey lucifer"
WAKE_SHORT = "lucifer"  # Also respond to just "lucifer"
DEACTIVATE_PHRASE = "lucifer sleep"

# Listening timeouts (in seconds) - FASTER for responsiveness
LISTEN_TIMEOUT = 2          # Quick timeout for wake word detection (passive mode)
COMMAND_TIMEOUT = 8         # More time for command capture (active mode)
PHRASE_TIME_LIMIT = 4      # Shorter phrase limit for faster response

# Log file configuration
LOG_FILE = "lucifer_session.log"

# TTS configuration
TTS_RATE = 150              # Speech rate (words per minute)
TTS_VOLUME = 0.8            # Volume level (0.0 to 1.0)


# =============================================================================
# LOGGING SETUP
# =============================================================================
# Configure logging to both file and console output
# File: lucifer_session.log with timestamps
# Console: INFO level for visibility during development
def setup_logging() -> logging.Logger:
    """
    Initialize logging system with file and console handlers.
    Returns configured logger instance.
    """
    logger = logging.getLogger("LuciferVoice")

    # Prevent duplicate handlers if module is re-imported
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # File handler: log to lucifer_session.log
    file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler: for development visibility
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    return logger


# Initialize logger at module level
logger = setup_logging()


# =============================================================================
# TEXT-TO-SPEECH ENGINE WRAPPER
# =============================================================================
from lucifer_tts import tts as _tts

class TTSEngine:
    """
    Wrapper around pyttsx3 for text-to-speech functionality.
    Delegates completely to the circular-import-proof lucifer_tts singleton.
    """

    def __init__(self):
        pass

    def speak(self, text: str) -> None:
        _tts.speak(text)

    def close(self) -> None:
        _tts.close()


# =============================================================================
# VOICE RECOGNITION ENGINE
# =============================================================================
class VoiceEngine:
    """
    Main voice recognition engine handling passive and active listening modes.
    Manages microphone input, speech recognition, and state transitions.
    """

    def __init__(self):
        """Initialize recognizer, microphone, and TTS engine."""
        # Speech recognition components
        self.recognizer = sr.Recognizer()
        self.microphone: Optional[sr.Microphone] = None

        # State management
        self.is_active = False          # Active listening mode flag
        self.running = True             # Main loop control flag

        # Component initialization
        self.tts = TTSEngine()
        self._initialize_microphone()

        # Register cleanup handlers
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _initialize_microphone(self) -> None:
        """
        Initialize microphone and calibrate for ambient noise.
        This improves recognition accuracy in the current environment.
        """
        try:
            self.microphone = sr.Microphone()

            # Open microphone context and adjust for ambient noise
            with self.microphone as source:
                logger.info("Calibrating microphone for ambient noise...")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
                logger.info("Microphone calibrated successfully")

        except sr.UnknownValueError as e:
            logger.error(f"Microphone not found or not accessible: {e}")
            self.tts.speak("Microphone not found")
            self.microphone = None

        except AttributeError as e:
            logger.error(f"PyAudio not installed properly: {e}")
            self.tts.speak("Audio driver not installed")
            self.microphone = None

        except Exception as e:
            logger.error(f"Microphone initialization failed: {e}")
            self.tts.speak("Microphone initialization failed")
            self.microphone = None

    def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self.running = False
        self.tts.close()
        logger.info("Voice engine shutdown complete")

    def _signal_handler(self, signum, frame) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._cleanup()
        sys.exit(0)

    def _check_phrase_in_text(self, phrase: str, text: str) -> bool:
        """
        Check if a phrase exists in text (case-insensitive, partial match).

        Args:
            phrase: Phrase to search for
            text: Text to search in

        Returns:
            True if phrase found, False otherwise
        """
        return phrase.lower() in text.lower()

    def _log_command(self, text: str, mode: str) -> None:
        """
        Log captured command to session log with timestamp.

        Args:
            text: The captured text
            mode: Listening mode ('passive' or 'active')
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] [{mode.upper()}] {text}"
        logger.info(f"COMMAND LOG: {log_entry}")

    # -------------------------------------------------------------------------
    # PASSIVE LISTENING - Wake Word Detection
    # -------------------------------------------------------------------------
    def listen_for_wake_word(self) -> bool:
        """
        Continuously listen for wake word in passive mode.
        Uses non-blocking listen with timeout to allow periodic checks.
        Also responds to just "lucifer" as a shorter wake word.

        Returns:
            True if wake word detected, False otherwise
        """
        if self.microphone is None:
            logger.error("Cannot listen - microphone not available")
            self.tts.speak("Microphone not available")
            return False

        # Print listening status (noisy log to let user know it's active)
        print("🎤 Listening for 'hey lucifer'...")

        try:
            # Listen with short timeout for quick response
            with self.microphone as source:
                audio = self.recognizer.listen(
                    source,
                    timeout=LISTEN_TIMEOUT,
                    phrase_time_limit=PHRASE_TIME_LIMIT
                )

            # Attempt recognition using Google Web Speech API
            text = self.recognizer.recognize_google(audio).lower()
            print(f"👂 Heard: '{text}'")
            logger.info(f"Heard (passive): '{text}'")

            # Check for wake word - full phrase OR just "lucifer"
            if (self._check_phrase_in_text(WAKE_PHRASE, text) or 
                self._check_phrase_in_text(WAKE_SHORT, text)):
                self._log_command(text, 'passive')
                print("✅ WAKE WORD DETECTED!")
                return True
            else:
                logger.debug(f"Wake word not matched: '{text}'")
                return False

        except sr.WaitTimeoutError:
            # Normal timeout - no speech detected
            return False

        except sr.UnknownValueError:
            # Speech was detected but not recognizable
            logger.debug("Unrecognized speech in passive mode")
            return False

        except sr.RequestError as e:
            # Network/API issues
            logger.error(f"Google Speech API error: {e}")
            self.tts.speak("Network error occurred")
            return False

        except Exception as e:
            logger.error(f"Unexpected error in wake word detection: {e}")
            return False

    # -------------------------------------------------------------------------
    # ACTIVE LISTENING - Command Capture
    # -------------------------------------------------------------------------
    def listen_for_command(self) -> Optional[str]:
        """
        Listen for user command in active mode with extended timeout.

        Returns:
            Captured command text, or None if timeout/no speech
        """
        if self.microphone is None:
            logger.error("Cannot listen - microphone not available")
            return None

        logger.info("Active listening: waiting for command...")

        try:
            with self.microphone as source:
                audio = self.recognizer.listen(
                    source,
                    timeout=COMMAND_TIMEOUT,
                    phrase_time_limit=COMMAND_TIMEOUT + 2
                )

            # Recognize using Google Web Speech API
            text = self.recognizer.recognize_google(audio)
            logger.info(f"Heard (active): '{text}'")

            # Log the command
            self._log_command(text, 'active')
            return text

        except sr.WaitTimeoutError:
            logger.warning("Command capture timeout - no speech detected")
            self.tts.speak("Timeout - command not captured")
            return None

        except sr.UnknownValueError:
            logger.warning("Could not understand the command")
            self.tts.speak("Could not understand")
            return None

        except sr.RequestError as e:
            logger.error(f"Google Speech API error during command: {e}")
            self.tts.speak("Network error")
            return None

        except Exception as e:
            logger.error(f"Unexpected error in command capture: {e}")
            return None

    # -------------------------------------------------------------------------
    # MAIN LISTENING LOOPS
    # -------------------------------------------------------------------------
    def run_passive_loop(self) -> None:
        """
        Main passive listening loop.
        Continuously listens for wake word until running flag is False.
        """
        logger.info("Starting passive listening loop")

        while self.running and not self.is_active:
            if self.listen_for_wake_word():
                # Wake word detected - enter active mode
                self.is_active = True
                logger.info("Wake word detected - entering ACTIVE mode")

                # Play activation sound/announcement
                self.tts.speak("Online")

                # Handle active listening
                self.run_active_loop()

        logger.info("Passive listening loop ended")

    def run_active_loop(self) -> None:
        """
        Active listening loop - captures commands until:
        - Deactivation phrase is spoken
        - Running flag becomes False
        - Error occurs requiring return to passive
        """
        logger.info("Entered ACTIVE listening mode")

        while self.running and self.is_active:
            # Capture command
            command = self.listen_for_command()

            if command is None:
                # Timeout or error - return to passive mode
                logger.info("Returning to passive mode due to timeout")
                self.is_active = False
                break

            # Check for deactivation phrase
            if self._check_phrase_in_text(DEACTIVATE_PHRASE, command):
                logger.info("Deactivation phrase detected")
                self.tts.speak("Going to sleep")
                self.is_active = False
                break

            # Route command to handler
            logger.info(f"Routing command: '{command}'")
            try:
                route_command(command)
            except Exception as e:
                logger.error(f"Error in command routing: {e}")
                self.tts.speak("Error processing command")

        logger.info("Exited ACTIVE listening mode")

    def start(self) -> None:
        """
        Start the voice recognition system.
        Entry point for the module.
        """
        logger.info("=" * 50)
        logger.info("Lucifer Voice Wake Module Starting")
        logger.info(f"Wake Phrase: '{WAKE_PHRASE}'")
        logger.info(f"Deactivate Phrase: '{DEACTIVATE_PHRASE}'")
        logger.info(f"Log File: {LOG_FILE}")
        logger.info("=" * 50)

        # Announce startup
        self.tts.speak("Lucifer voice activated")

        # Start passive listening loop
        # This is the main blocking loop
        self.run_passive_loop()


# =============================================================================
# COMMAND ROUTING (INTEGRATED WITH lucifer_intent.py)
# =============================================================================
try:
    from lucifer_intent import route_command as intent_route_command
    _INTENT_AVAILABLE = True
except ImportError:
    _INTENT_AVAILABLE = False


def route_command(text: str) -> None:
    """
    Route captured command to the intent parser (lucifer_intent.py).

    This integrates the two-layer router (FastMatcher + Claude API) for
    intent classification and command dispatch.

    Args:
        text: Raw command text captured from voice input
    """
    logger.info(f"[ROUTE] Processing command: '{text}'")

    if _INTENT_AVAILABLE:
        try:
            intent_route_command(text, tts_speak=None)
        except Exception as e:
            logger.error(f"Intent routing failed: {e}")
            print(f"[LUCIFER] Command received: {text}")
    else:
        print(f"[LUCIFER] Command received: {text}")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    """
    Main entry point for Lucifer voice wake module.
    Creates VoiceEngine instance and starts listening.
    """
    try:
        # Create and start the voice engine
        engine = VoiceEngine()
        engine.start()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")

    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        print(f"Fatal error: {e}")

    finally:
        logger.info("Lucifer voice module terminated")