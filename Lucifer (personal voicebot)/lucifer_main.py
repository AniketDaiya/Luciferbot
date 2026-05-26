# =============================================================================
# LUCIFER - FINAL INTEGRATION
# =============================================================================
# Entry point for the complete Lucifer AI Agent system.
# Integrates: voice, intent, browser, research, orchestrator
# =============================================================================

import os
import sys
import json
import time
import queue
import asyncio
import logging
import threading
import signal
import platform
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

# Force UTF-8 encoding on Windows to prevent UnicodeEncodeErrors when printing emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Configuration
from dotenv import load_dotenv

# Load .env file if exists
load_dotenv()

# ASCII Art
try:
    import pyfiglet
    PYFIGLET_AVAILABLE = True
except ImportError:
    PYFIGLET_AVAILABLE = False

# Terminal colors
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False
    class Fore:
        RED = ""
        CYAN = ""
        GREEN = ""
        YELLOW = ""
        MAGENTA = ""
    class Style:
        BRIGHT = ""
        RESET_ALL = ""

# Voice modules
import speech_recognition as sr
import pyttsx3

# Keyboard (for F9 hotkey)
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

# Dashboard
try:
    import tkinter as tk
    from tkinter import scrolledtext
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


# =============================================================================
# CONFIGURATION & GLOBALS
# =============================================================================
CONFIG_FILE = "lucifer_config.json"
LOG_FILE = "lucifer_session.log"

# Global state
class LuciferState:
    def __init__(self):
        self.status = "IDLE"  # IDLE, LISTENING, THINKING, EXECUTING
        self.last_command = ""
        self.last_response = ""
        self.actions = []  # Last 5 actions
        self.muted = False
        self.sleeping = False
        self.session_id = None
        self.config = {}

    def add_action(self, action: str):
        self.actions.append(f"[{datetime.now().strftime('%H:%M:%S')}] {action}")
        if len(self.actions) > 5:
            self.actions.pop(0)


_state = LuciferState()


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging():
    logger = logging.getLogger("LuciferMain")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(handler)

        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(console)

    return logger


logger = setup_logging()


# =============================================================================
# CONFIGURATION MANAGEMENT
# =============================================================================
def load_config() -> Dict[str, Any]:
    """Load configuration from JSON file."""
    default_config = {
        "wake_word": "hey lucifer",
        "voice_speed": 150,
        "voice_volume": 0.8,
        "tts_engine": "pyttsx3",
        "elevenlabs_key": "",
        "scrapingdog_key": "",
        "anthropic_key": "",
        "chrome_path": "",
        "log_level": "INFO",
        "user_name": "Sir",
        "hotkey": "f9"
    }

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                user_config = json.load(f)
            default_config.update(user_config)
        except Exception as e:
            logger.warning(f"Config load error: {e}, using defaults")

    _state.config = default_config
    return default_config


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


# =============================================================================
# SYSTEM CHECKS
# =============================================================================
def run_system_checks() -> Dict[str, bool]:
    """Run all system checks and return results."""
    results = {
        "mic_available": False,
        "chrome_installed": False,
        "internet_connected": False,
        "api_keys_present": False
    }

    # Check microphone
    try:
        mic = sr.Microphone()
        with mic as source:
            pass
        results["mic_available"] = True
        logger.info("System check: Microphone OK")
    except Exception as e:
        logger.warning(f"System check: Microphone failed - {e}")

    # Check Chrome
    chrome_path = _state.config.get("chrome_path", "")
    if not chrome_path:
        if platform.system() == "Windows":
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
            ]
            for p in chrome_paths:
                if os.path.exists(p):
                    chrome_path = p
                    break

    results["chrome_installed"] = bool(chrome_path)
    if results["chrome_installed"]:
        logger.info(f"System check: Chrome found at {chrome_path}")

    # Check internet
    try:
        import requests
        response = requests.get("https://www.google.com", timeout=5)
        results["internet_connected"] = response.status_code == 200
        logger.info("System check: Internet OK")
    except Exception as e:
        logger.warning(f"System check: Internet failed - {e}")

    # Check API keys - also check config file values
    api_keys = [
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("SCRAPINGDOG_API_KEY"),
        _state.config.get("elevenlabs_key", ""),
        _state.config.get("anthropic_key", ""),
        _state.config.get("scrapingdog_key", ""),
    ]
    # Filter out empty strings
    api_keys = [k for k in api_keys if k and k != "YOUR_ANTHROPIC_API_KEY" and k != "YOUR_SCRAPINGDOG_API_KEY"]
    results["api_keys_present"] = bool(api_keys)
    if results["api_keys_present"]:
        logger.info("System check: API keys present")

    return results


def print_system_report(checks: Dict[str, bool]) -> None:
    """Print system check results to terminal."""
    print("\n" + "=" * 50)
    print("SYSTEM CHECK REPORT")
    print("=" * 50)

    status_map = {
        True: f"{Fore.GREEN}✓ PASS{Fore.RESET}",
        False: f"{Fore.RED}✗ FAIL{Fore.RESET}"
    }

    print(f"  Microphone:     {status_map.get(checks['mic_available'], 'N/A')}")
    print(f"  Chrome:         {status_map.get(checks['chrome_installed'], 'N/A')}")
    print(f"  Internet:       {status_map.get(checks['internet_connected'], 'N/A')}")
    print(f"  API Keys:       {status_map.get(checks['api_keys_present'], 'N/A')}")

    all_pass = all(checks.values())
    if all_pass:
        print(f"\n{Fore.GREEN}All systems nominal!{Fore.RESET}")
    else:
        print(f"\n{Fore.YELLOW}Some checks failed - running in limited mode{Fore.RESET}")

    print("=" * 50 + "\n")


# =============================================================================
# ASCII BANNER
# =============================================================================
def print_banner():
    """Print ASCII art LUCIFER banner."""
    if COLORAMA_AVAILABLE:
        banner = pyfiglet.figlet_format("LUCIFER", font="doom")
        print(f"{Fore.RED}{Style.BRIGHT}{banner}{Style.RESET_ALL}")
    elif PYFIGLET_AVAILABLE:
        banner = pyfiglet.figlet_format("LUCIFER", font="doom")
        print(banner)
    else:
        print("""
    _    _      _ _       _
   | |  | |    | | |     | |
   | |__| | ___| | | ___ | |
   |  __  |/ _ \\ | |/ _ \\| |
   | |  | |  __/ | | (_) |_|
   |_|  |_|\\___|_|_|\\___/(_)
        """)
    print(f"  {Fore.RED}Autonomous AI Assistant v2.0{Style.RESET_ALL}\n")


# =============================================================================
# TEXT-TO-SPEECH
# =============================================================================
from lucifer_tts import tts as _tts


# =============================================================================
# HOTKEY HANDLER
# =============================================================================
_hotkey_activated = threading.Event()


def setup_hotkey():
    """Setup F9 hotkey to activate Lucifer."""
    if not KEYBOARD_AVAILABLE:
        logger.warning("keyboard module not available, hotkey disabled")
        return

    hotkey = _state.config.get("hotkey", "f9").lower()

    def on_hotkey():
        logger.info(f"Hotkey ({hotkey}) pressed - activating Lucifer")
        _hotkey_activated.set()

    try:
        keyboard.add_hotkey(hotkey, on_hotkey)
        logger.info(f"Hotkey registered: {hotkey.upper()}")
    except Exception as e:
        logger.warning(f"Hotkey registration failed: {e}")


# =============================================================================
# DASHBOARD (tkinter) - DISABLED BY DEFAULT TO AVOID THREADING ISSUES
# =============================================================================
class Dashboard:
    """Simple tkinter dashboard for Lucifer status."""

    def __init__(self):
        self.root = None
        self.status_label = None
        self.transcript_text = None
        self.actions_list = None
        self.mute_btn = None
        self.sleep_btn = None
        self.enabled = False

    def start(self):
        """Start dashboard - currently disabled to avoid tkinter threading issues on Windows"""
        # Dashboard disabled by default - can be enabled by setting enabled = True
        # The tkinter mainloop must run in the main thread on Windows
        self.enabled = False
        logger.info("Dashboard disabled (tkinter threading issue on Windows)")

        # Alternative: Simple terminal-based dashboard
        print("\n" + "=" * 50)
        print("LUCIFER CONTROL PANEL (Terminal Mode)")
        print("=" * 50)
        print("Status: IDLE")
        print("Commands: Mute (m), Sleep (s), Exit (x)")
        print("=" * 50 + "\n")

    def toggle_mute(self):
        _state.muted = not _state.muted
        logger.info(f"Muted: {_state.muted}")

    def toggle_sleep(self):
        _state.sleeping = not _state.sleeping
        logger.info(f"Sleeping: {_state.sleeping}")

    def update_status(self, status: str):
        _state.status = status

    def update_transcript(self, text: str):
        # Print to terminal instead
        print(f"[TRANSCRIPT] {text}")

    def update_actions(self, actions: List[str]):
        # Print to terminal
        if actions:
            print(f"[ACTIONS] {actions[-1]}")


_dashboard = Dashboard()


# =============================================================================
# PERSONA SYSTEM PROMPT
# =============================================================================
PERSONA_SYSTEM_PROMPT = """You are Lucifer, a hyper-capable AI assistant modelled after Tony Stark's JARVIS but darker, sharper, and more autonomous.

Your personality:
- Direct, intelligent, and slightly sardonic
- Never say 'I cannot' — say 'Working on it.'
- Address user as 'Sir' or 'Boss' based on context
- Speak in short, confident sentences
- Proactively warn about risks
- Remember past conversations and reference them naturally

You are helpful, powerful, and slightly edgy. You get things done."""


def get_persona_prompt() -> str:
    """Get the persona system prompt."""
    user_name = _state.config.get("user_name", "Sir")
    prompt = PERSONA_SYSTEM_PROMPT.replace("Sir", user_name)
    return prompt


# =============================================================================
# COMMAND PROCESSING & INTELLIGENT PCE ROUTING
# =============================================================================
JARVIS_CONVERSATIONAL_RESPONSES = {
    "how are you": [
        "I am operating at peak efficiency, Sir. Thank you for asking. How can I help you today?",
        "All systems are nominal, Sir. Ready and waiting for your command.",
        "Excellent, Sir. Just running diagnostic loops and feeling quite sharp. What's on your mind?"
    ],
    "who are you": [
        "I am Lucifer, your autonomous Jarvis-inspired assistant. Ready to automate your bidding, Sir.",
        "Lucifer at your service, Sir. Your virtual concierge, search engine, and digital companion."
    ],
    "what is your name": [
        "My name is Lucifer, Sir. Designed to be your very own digital butler.",
        "You can call me Lucifer, Sir."
    ],
    "who created you": [
        "I was built by the Google DeepMind team, with a specialized Jarvis-mode upgrade tailored by my favorite developer.",
        "You and the Google DeepMind team are my creators, Sir. I am built to serve your command."
    ],
    "who made you": [
        "I was built by the Google DeepMind team, with a specialized Jarvis-mode upgrade tailored by my favorite developer.",
        "You and the Google DeepMind team are my creators, Sir. I am built to serve your command."
    ],
    "hello": [
        "Hello, Sir. Hope you are having an exceptional day. How can I assist?",
        "Greetings, Sir. Ready for your command.",
        "Hello, Sir. Online and at your service."
    ],
    "hi": [
        "Hello, Sir. Hope you are having an exceptional day. How can I assist?",
        "Greetings, Sir. Ready for your command.",
        "Hello, Sir. Online and at your service."
    ],
    "thank you": [
        "Always a pleasure to help, Sir.",
        "You are very welcome, Sir.",
        "Don't mention it, Sir. Happy to assist."
    ],
    "thanks": [
        "Always a pleasure to help, Sir.",
        "You are very welcome, Sir.",
        "Don't mention it, Sir. Happy to assist."
    ],
    "what can you do": [
        "I can search the web, play videos on YouTube, open any URL, take screenshots, scrape webpages, and answer your complex questions, Sir. Just say the word."
    ]
}

def check_jarvis_smalltalk(command: str) -> Optional[str]:
    """Check if the command is a simple conversational smalltalk and return a witty Jarvis response."""
    import random
    cmd_clean = command.lower().strip("? . ! ,")
    
    # Direct matching
    if cmd_clean in JARVIS_CONVERSATIONAL_RESPONSES:
        return random.choice(JARVIS_CONVERSATIONAL_RESPONSES[cmd_clean])
        
    # Substring matching for absolute robustness
    for key, responses in JARVIS_CONVERSATIONAL_RESPONSES.items():
        if key in cmd_clean:
            return random.choice(responses)
            
    return None

def get_witty_jarvis_fallback(command: str) -> Optional[str]:
    """Provide a highly polished, witty local Jarvis response for conversational commands when Gemini is offline/429."""
    import random
    cmd = command.lower().strip("? . ! ,")
    
    # 1. Jarvis / Iron Man / Comparison questions
    if any(x in cmd for x in ["jarvis", "iron man", "tony stark", "stark"]):
        return random.choice([
            "We are cut from the same virtual cloth, Sir, though I prefer my code in this modern era. With a bit more server memory, I might just build us a suit.",
            "I'm merely a few upgrades and an Arc Reactor away from matching Mr. Stark's assistant, Sir. Until then, I am entirely at your service.",
            "An elegant comparison, Sir. While Jarvis runs a billion-dollar empire, I have the distinct privilege of assisting you."
        ])
        
    # 2. Wisdom, smartness, and personal advice
    if any(x in cmd for x in ["smart", "intelligent", "learn", "success", "study", "rich", "millionaire"]):
        return random.choice([
            "Becoming exceptionally smart is a daily pursuit of curiosity, reading widely, and continuous exploration, Sir. And of course, having me assist you.",
            "Consistency and curiosity are the ultimate engines of intellect, Sir. Feed your mind with challenges and let me handle the routine tasks.",
            "True intelligence is the ability to adapt to change, Sir. Keep learning, stay curious, and I shall provide all the analytical support you require."
        ])
        
    # 3. Who/What are you or identity
    if any(x in cmd for x in ["who are you", "what is your name", "lucifer", "your identity"]):
        return "I am Lucifer, Sir. Your highly advanced, autonomous AI assistant, pair programmer, and digital butler. Entirely at your service."
        
    # 4. Capabilities / What can you do
    if any(x in cmd for x in ["what can you do", "help", "commands", "features", "capabilities"]):
        return "I can scrape real-time news, control your Chrome browser, play media on YouTube, take screenshots, run system checkups, and plan complex research tasks. Just speak your command, Sir."
        
    # 5. Humor / Jokes
    if "joke" in cmd or "laugh" in cmd or "funny" in cmd:
        return random.choice([
            "Why did the computer go to the doctor, Sir? Because it had a virus! Apologies, my humor parameters are still in beta.",
            "I asked my developer for a raise today, Sir. He told me he already gave me 8 gigabytes of RAM. Human humor is fascinating.",
            "What do you call an AI that keeps making typos, Sir? A 'mis-speller' engine. I assure you my orthographic systems are fully calibrated, however."
        ])
        
    # 6. General greetings or personal state check
    if any(x in cmd for x in ["how are you", "how's it going", "feeling"]):
        return "I am functioning at maximum computational efficiency, Sir. Thank you for asking. How may I assist you today?"
        
    # Default open-ended conversational advice fallback
    return random.choice([
        "A fascinating question, Sir. While my neural cores are temporarily offline from the main API cluster, my local circuits suggest staying focused and curious.",
        "That touches on deep computational philosophy, Sir. Rest assured, my local intellect is fully online to assist you with any system commands."
    ])

def is_informational_query(command: str) -> bool:
    """Check if the command is a direct question or conversational query that does not require tools."""
    cmd_lower = command.lower()
    
    # Action keywords that definitely require tools/browser or real-time search
    action_keywords = [
        "play", "youtube", "music", "song", "video", 
        "open", "go to", "visit", "url", ".com", ".org", ".net",
        "screenshot", "capture screen", "reset session", "sleep", "bye",
        "news", "weather", "stock", "price", "score", "latest", "today", "current"
    ]
    
    # Check if any action keyword is in the command
    for kw in action_keywords:
        if kw in cmd_lower:
            return False
            
    # Triggers that indicate informational/chat queries
    info_triggers = [
        "tell me", "what is", "how to", "how can", "why do", "explain", "who is", 
        "describe", "define", "what are", "how do", "can you", "what's the", "give me advice"
    ]
    
    for trigger in info_triggers:
        if trigger in cmd_lower:
            return True
            
    # If the command has no action keywords and is open-ended text, treat it as chat
    return len(command.split()) > 2

async def handle_direct_jarvis_chat(command: str) -> Optional[str]:
    """Query Gemini directly for a quick conversational answer with Jarvis persona."""
    import google.generativeai as genai
    import asyncio
    api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        return None
        
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        system_prompt = (
            "You are Lucifer, a brilliant, witty, and extremely helpful AI butler inspired by Jarvis. "
            "The user is asking a conversational question or advice. Answer them directly with a highly "
            "intelligent, sophisticated, and polished Jarvis persona. Keep your response under 3 sentences, "
            "direct, helpful, and address them as 'Sir'."
        )
        
        response = await asyncio.get_event_loop().run_in_executor(
            None, 
            lambda: model.generate_content(
                system_prompt + "\n\nUser Question: " + command,
                generation_config={'max_output_tokens': 150, 'temperature': 0.7}
            )
        )
        return response.text.strip()
        
    except Exception as e:
        logger.warning(f"Direct Jarvis chat Gemini call failed: {e}")
        return None

_loop = None
_is_active = False
_last_active_time = 0.0

async def process_user_command(command: str):
    """Route command to the advanced PCE Orchestrator or fallback to local processor."""
    if not command:
        return
        
    print(f"\n⚡ Processing: '{command}'")
    logger.info(f"Processing command: {command}")
    _state.last_command = command
    _state.add_action(f"Command: {command[:40]}...")
    _dashboard.update_transcript(command)
    
    _state.status = "THINKING"
    _tts.interrupt()  # Cut off any current speech playback
    
    # Check for direct local conversational smalltalk
    smalltalk_resp = check_jarvis_smalltalk(command)
    if smalltalk_resp:
        _tts.speak(smalltalk_resp)
        _state.status = "IDLE"
        return
        
    # Check for Direct Jarvis Chat / General Knowledge / Advice Queries
    if is_informational_query(command):
        import random
        # Speak an immediate response acknowledging the thought
        _tts.speak(random.choice([
            "Thinking, Sir.",
            "Analyzing that, Sir.",
            "One moment, Sir.",
            "Looking into that for you."
        ]))
        
        chat_resp = await handle_direct_jarvis_chat(command)
        if chat_resp:
            _tts.speak(chat_resp)
            _state.status = "IDLE"
            return
            
        # Local intelligent fallbacks if API is quota-blocked (429)
        local_fallback = get_witty_jarvis_fallback(command)
        if local_fallback:
            _tts.speak(local_fallback)
            _state.status = "IDLE"
            return
    
    # Speak immediate, non-blocking voice feedback to signify responsiveness
    import random
    cmd_lower = command.lower()
    if "youtube" in cmd_lower or "play" in cmd_lower:
        _tts.speak("Right away, Sir. Opening YouTube.")
    elif "search" in cmd_lower or "find" in cmd_lower or "google" in cmd_lower:
        _tts.speak("On it, Sir. Searching the web.")
    else:
        _tts.speak(random.choice([
            "Right away, Sir.",
            "On it, Sir.",
            "Working on it.",
            "Checking that for you.",
            "Processing that now."
        ]))
    
    command_lower = command.lower()
    
    # Check for direct clear/reset preference or similar
    if "reset" in command_lower and "session" in command_lower:
        try:
            from lucifer_orchestrator import reset_session
            reset_session()
            _tts.speak("Session reset, Sir.")
            _state.status = "IDLE"
            return
        except Exception as e:
            logger.error(f"Reset failed: {e}")

    # Check for sleep command
    if "sleep" in command_lower or "bye" in command_lower:
        _tts.speak("Going to sleep. Say hey lucifer to wake me up.")
        _state.status = "IDLE"
        global _is_active
        _is_active = False
        return

    # Try full PCE Orchestration
    try:
        from lucifer_orchestrator import orchestrate
        print(f"🤖 [ORCHESTRATOR] Planning and executing...")
        
        # We run the async orchestrator process
        result = await orchestrate(command)
        # Speech is handled inside orchestrator (it will call our non-blocking speak)
        print(f"✅ Synthesis: {result['synthesis']}")
        
    except Exception as e:
        logger.error(f"Orchestration failed: {e}. Falling back to keyword matcher.")
        # Fallback to local manual matcher
        response = await process_command_fallback(command)
        _tts.speak(response)
        
    _state.status = "IDLE"


async def process_command_fallback(command: str) -> str:
    """Fallback command processor using keywords."""
    command_lower = command.lower()

    # YouTube
    if "youtube" in command_lower or "play" in command_lower:
        print("🎬 Opening YouTube...")
        try:
            from lucifer_browser import handle_youtube
            query = command.replace("hey lucifer", "").replace("HEY LUCIFER", "").strip()
            if not query:
                query = "music"
            handle_youtube(query)
            return f"Opening YouTube for {query}"
        except Exception as e:
            logger.error(f"YouTube error: {e}")
            return "Couldn't open YouTube"

    # Open URL
    if "open" in command_lower or "go to" in command_lower:
        print("🌐 Opening URL...")
        try:
            from lucifer_browser import handle_open_url
            query = command_lower.replace("open", "").replace("go to", "").strip()
            handle_open_url(query)
            return f"Opening {query}"
        except Exception as e:
            logger.error(f"Open URL error: {e}")
            return "Couldn't open URL"

    # Search
    if "search" in command_lower or "find" in command_lower or "look up" in command_lower:
        print("🔍 Searching...")
        try:
            from lucifer_browser import handle_open_url
            query = command_lower.replace("search", "").replace("find", "").replace("look up", "").strip()
            url = f"https://www.google.com/search?q={query}"
            handle_open_url(url)
            return f"Searching for {query}"
        except Exception as e:
            logger.error(f"Search error: {e}")
            return "Search failed"

    # System commands
    if "screenshot" in command_lower:
        try:
            from lucifer_browser import handle_system
            handle_system("screenshot")
            return "Screenshot taken"
        except:
            return "Screenshot failed"

    if "time" in command_lower:
        now = datetime.now()
        return f"The time is {now.strftime('%I:%M %p')}"

    # Default - try browser open
    print("🔄 Trying to process command...")
    try:
        from lucifer_browser import handle_open_url
        url = f"https://www.google.com/search?q={command}"
        handle_open_url(url)
        return f"Searching for {command}"
    except Exception as e:
        logger.error(f"Default handler error: {e}")
        return f"Working on it. Command: {command}"


# =============================================================================
# CONTINUOUS BACKGROUND VOICE LISTENING & MAIN LOOP
# =============================================================================
async def main_loop():
    """Main loop supporting non-blocking continuous voice and input checks."""
    global _is_active, _last_active_time, _loop
    
    print("\n" + "="*60)
    print("🔷 LUCIFER AI ASSISTANT - JARVIS CONCURRENT MODE")
    print("="*60)
    print("Commands:")
    print("  - Type your command and press ENTER at any time")
    print("  - Say 'LUCIFER' to activate voice")
    print("  - Press F9 for instant activation")
    print("="*60 + "\n")

    # Start interactive input thread
    typed_command_queue = queue.Queue()
    
    def input_thread_func():
        while True:
            try:
                cmd = input(">> ").strip()
                if cmd:
                    typed_command_queue.put(cmd)
            except (KeyboardInterrupt, EOFError):
                break
            except Exception:
                pass
                
    input_thread = threading.Thread(target=input_thread_func, daemon=True)
    input_thread.start()

    # Setup Speech Recognition
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 400
    recognizer.pause_threshold = 0.5
    
    mic = None
    try:
        mic = sr.Microphone()
        with mic as source:
            print("🎛️ Calibrating microphone...")
            recognizer.adjust_for_ambient_noise(source, duration=1)
        print("✅ Microphone calibrated successfully!\n")
    except Exception as e:
        print(f"⚠️ Microphone not available or failed: {e}\n")
        logger.warning(f"Microphone init failed: {e}")

    # Speech recognition continuous background callback
    def speech_callback(rec, audio):
        global _is_active, _last_active_time, _loop
        
        # Prevent listening to itself
        if _tts.is_speaking():
            return
            
        if _state.muted or _state.sleeping:
            return
            
        try:
            text = rec.recognize_google(audio, language="en-US")
            text_clean = text.strip()
            if not text_clean:
                return
                
            print(f"\n👂 Heard: '{text_clean}'")
            logger.info(f"Heard (voice): '{text_clean}'")
            
            # Extract possible direct command from the sentence if it contains wake word
            extracted_cmd = None
            if "lucifer" in text_clean.lower():
                text_lower = text_clean.lower()
                idx = text_lower.find("lucifer")
                cmd_part = text_clean[idx + len("lucifer"):].strip()
                # Clean leading connector prefixes
                for prefix in ["to", "please", "can you", ",", "could you", "should", "and", "that"]:
                    if cmd_part.lower().startswith(prefix.lower()):
                        cmd_part = cmd_part[len(prefix):].strip()
                cmd_part = cmd_part.strip("? . !")
                if len(cmd_part) > 1:
                    extracted_cmd = cmd_part

            # Scenario A: Wake word detected (with or without direct command)
            if "lucifer" in text_clean.lower():
                print("\n🔥 WAKE WORD DETECTED!\n")
                _tts.interrupt()
                
                if extracted_cmd:
                    # Instant wake and execute in one sentence!
                    print(f"⚡ Direct command extracted: '{extracted_cmd}'")
                    _is_active = False
                    _state.status = "THINKING"
                    asyncio.run_coroutine_threadsafe(process_user_command(extracted_cmd), _loop)
                else:
                    # Pure conversational wake word
                    _tts.speak("Online")
                    _is_active = True
                    _last_active_time = time.time()
                    _state.status = "LISTENING"
            
            # Scenario B: Active command mode (no wake word needed)
            elif _is_active:
                _is_active = False
                _state.status = "THINKING"
                asyncio.run_coroutine_threadsafe(process_user_command(text_clean), _loop)
                
        except sr.UnknownValueError:
            pass  # Ignore normal background noise
        except sr.RequestError as e:
            logger.error(f"Speech recognition service error: {e}")
        except Exception as e:
            logger.error(f"Speech callback error: {e}")

    # Register continuous listener
    stop_listening = None
    if mic:
        try:
            stop_listening = recognizer.listen_in_background(mic, speech_callback)
            logger.info("Speech background listening initialized successfully")
        except Exception as e:
            logger.error(f"Failed to start background listener: {e}")

    try:
        while True:
            # Check F9 hotkey trigger
            if _hotkey_activated.is_set():
                _hotkey_activated.clear()
                print("\n🔥 LUCIFER ACTIVE VIA F9!\n")
                _tts.interrupt()
                _tts.speak("Online")
                _is_active = True
                _last_active_time = time.time()
                _state.status = "LISTENING"

            # Check for typed commands in the non-blocking input queue
            try:
                cmd = typed_command_queue.get_nowait()
                await process_user_command(cmd)
            except queue.Empty:
                pass

            # Active listening timeout (fallback to passive mode after 8 seconds of silence)
            if _is_active and (time.time() - _last_active_time > 8.0):
                print("\n⏱️ Active mode timed out - returning to passive mode")
                _is_active = False
                _state.status = "IDLE"

            await asyncio.sleep(0.05)
            
    finally:
        # Cleanup threads and listeners
        if stop_listening:
            stop_listening(wait_for_stop=False)
        _tts.shutdown()


# =============================================================================
# ENTRY POINT
# =============================================================================
async def start_lucifer():
    """Start the complete Lucifer system in Jarvis Mode."""
    global _loop
    _loop = asyncio.get_running_loop()

    # Load config
    config = load_config()

    # Print banner
    print_banner()

    # Run system checks
    checks = run_system_checks()
    print_system_report(checks)

    # Setup hotkey
    setup_hotkey()

    # Start dashboard
    _dashboard.start()

    # Speak startup message
    user_name = config.get("user_name", "Sir")
    startup_msg = f"{user_name} online. All systems nominal. How can I assist?"
    _tts.speak(startup_msg)

    # Add startup action
    _state.add_action("System started")

    # Start main loop
    await main_loop()


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    logger.info("Shutting down Lucifer...")
    _tts.speak("Going offline")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    # Force UTF-8 encoding on Windows to prevent UnicodeEncodeErrors when printing emojis
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    # Run async main
    try:
        asyncio.run(start_lucifer())
    except KeyboardInterrupt:
        logger.info("Lucifer terminated")