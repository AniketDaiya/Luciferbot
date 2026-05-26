# =============================================================================
# LUCIFER INTENT PARSING & COMMAND ROUTING MODULE
# =============================================================================
# Requirements (as specified):
#   - Intent categories: SEARCH, OPEN_URL, YOUTUBE, SYSTEM, CHAT, SHUTDOWN
#   - Two-layer router: Layer 1 (regex/keyword), Layer 2 (Claude API)
#   - Claude model: claude-sonnet-4-20250514
#   - Structured JSON output from Claude: {"intent": "...", "query": "...", "confidence": ...}
#   - Stub handlers for each intent
#   - Confidence scoring with clarification prompt if < 0.6
#   - Integration with lucifer_voice.py route_command()
#   - Production-quality with error handling
# =============================================================================

import os
import re
import json
import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from openai import OpenAI


# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
# Intent categories (for classification)
class Intent(Enum):
    """Supported intent categories."""
    SEARCH = "SEARCH"
    OPEN_URL = "OPEN_URL"
    YOUTUBE = "YOUTUBE"
    SYSTEM = "SYSTEM"
    CHAT = "CHAT"
    SHUTDOWN = "SHUTDOWN"
    UNKNOWN = "UNKNOWN"


# Confidence threshold for clarification
CLARIFICATION_THRESHOLD = 0.6

# Claude model configuration
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Logging
LOG_FILE = "lucifer_session.log"


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> logging.Logger:
    """Initialize logging for intent module."""
    logger = logging.getLogger("LuciferIntent")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # File handler
    file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    return logger


logger = setup_logging()


# =============================================================================
# DATA STRUCTURES
# =============================================================================
@dataclass
class IntentResult:
    """
    Structured result from intent classification.

    Attributes:
        intent: Classified intent (Intent enum)
        query: Extracted query or command
        confidence: Confidence score (0.0 to 1.0)
        raw_response: Raw response if any
    """
    intent: Intent
    query: str
    confidence: float
    raw_response: Optional[str] = None


# =============================================================================
# LAYER 1: FAST REGEX/KEYWORD MATCHER
# =============================================================================
class FastMatcher:
    """
    Layer 1 of the two-layer router.
    Uses regex patterns and keyword matching for obvious intents.
    Returns (intent, query, confidence) tuple or None if no match.
    """

    # Compiled regex patterns for URL detection
    URL_PATTERN = re.compile(
        r'https?://[^\s]+|'
        r'(?:www\.)?[a-zA-Z0-9][a-zA-Z0-9-]*\.(?:com|org|net|io|gov|edu|co)(?:/[^\s]*)?',
        re.IGNORECASE
    )

    # Keyword mappings for intent classification
    INTENT_KEYWORDS = {
        Intent.OPEN_URL: [
            r'\bopen\b', r'\bgo to\b', r'\bvisit\b', r'\bnavigate to\b',
            r'\bload\b', r'\bshow me\b'
        ],
        Intent.YOUTUBE: [
            r'\byoutube\b', r'\bplay\s+.*\s+on\s+youtube\b',
            r'\bwatch\b', r'\bplay\s+(?:some\s+)?(?:music|video)s?\b'
        ],
        Intent.SEARCH: [
            r'\bsearch\b', r'\blook\s+up\b', r'\bfind\b',
            r'\bresearch\b', r'\blookup\b', r'\bwhat is\b',
            r'\bhow (?:do|does|to)\b', r'\bwhen (?:was|is|did)\b'
        ],
        Intent.SYSTEM: [
            r'\bscreenshot\b', r'\bopen\s+(?:notepad|calculator|cmd|powershell)\b',
            r'\bwhat time\b', r'\bwhat date\b', r'\blaunch\b',
            r'\bstart\b', r'\bclose\b', r'\bminimize\b', r'\bmaximize\b'
        ],
        Intent.CHAT: [
            r'\bwhat do you think\b', r'\btell me a joke\b',
            r'\bwho are you\b', r'\bwhat are you\b', r'\bdescribe yourself\b',
            r'\bexplain\b', r'\bwhy\b', r'\bcan you\b', r'\bdo you\b'
        ],
        Intent.SHUTDOWN: [
            r'\bsleep\b', r'\boffline\b', r'\bshut\s*down\b',
            r'\bgo to sleep\b', r'\bstop listening\b', r'\bexit\b'
        ]
    }

    # Compile patterns on class load
    _compiled_patterns: Dict[Intent, list] = {}

    @classmethod
    def _compile_patterns(cls) -> None:
        """Compile all keyword patterns once."""
        if not cls._compiled_patterns:
            for intent, patterns in cls.INTENT_KEYWORDS.items():
                cls._compiled_patterns[intent] = [
                    re.compile(p, re.IGNORECASE) for p in patterns
                ]

    @classmethod
    def match(cls, text: str) -> Optional[Tuple[Intent, str, float]]:
        """
        Attempt to match text against fast patterns.

        Args:
            text: Raw command text

        Returns:
            Tuple of (intent, query, confidence) if matched, None otherwise
        """
        cls._compile_patterns()
        text_lower = text.lower()

        # Special handling for explicit shutdown phrase
        if re.search(r'lucifer\s+sleep', text_lower, re.IGNORECASE):
            logger.info("Fast matcher: SHUTDOWN detected (explicit phrase)")
            return (Intent.SHUTDOWN, text.strip(), 1.0)

        # Check for URL first (highest priority for OPEN_URL)
        url_match = cls.URL_PATTERN.search(text)
        if url_match:
            url = url_match.group()
            if 'youtube' in text_lower and not url.startswith('http'):
                # "open youtube" -> YOUTUBE intent
                return (Intent.YOUTUBE, "youtube", 0.95)
            logger.info(f"Fast matcher: OPEN_URL detected - {url}")
            return (Intent.OPEN_URL, url, 0.95)

        # Check keyword patterns for each intent
        for intent, patterns in cls._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    # Extract query by removing intent keywords
                    query = cls._extract_query(text, intent, pattern.pattern)
                    logger.info(f"Fast matcher: {intent.value} detected via keyword")
                    return (intent, query.strip(), 0.85)

        return None

    @classmethod
    def _extract_query(cls, text: str, intent: Intent, pattern: str) -> str:
        """
        Extract the query portion after removing intent keywords.

        Args:
            text: Full command text
            intent: Detected intent
            pattern: Matched pattern (for context)

        Returns:
            Extracted query string
        """
        text_lower = text.lower()

        # Remove common opening phrases based on intent
        if intent == Intent.OPEN_URL:
            for phrase in ['open ', 'go to ', 'visit ', 'navigate to ', 'load ']:
                if text_lower.startswith(phrase):
                    return text[len(phrase):].strip()

        elif intent == Intent.YOUTUBE:
            # Handle "play X on youtube", "open youtube and search for X", etc.
            patterns_to_remove = [
                r'play\s+.*\s+on\s+youtube',
                r'open\s+youtube\s+and\s+search\s+for',
                r'find\s+.*\s+on\s+youtube',
                r'(?:on|open)\s+youtube'
            ]
            for p in patterns_to_remove:
                match = re.search(p, text_lower)
                if match:
                    return text[match.end():].strip()

        elif intent == Intent.SEARCH:
            for phrase in ['search for ', 'look up ', 'find ', 'research ', 'lookup ']:
                if phrase in text_lower:
                    idx = text_lower.find(phrase) + len(phrase)
                    return text[idx:].strip()

        elif intent == Intent.SYSTEM:
            # Extract the command after the action word
            for phrase in ['take a ', 'open ', 'what time ', 'what date ']:
                if phrase in text_lower:
                    idx = text_lower.find(phrase) + len(phrase)
                    return text[idx:].strip()

        elif intent == Intent.CHAT:
            # Remove question starters
            for phrase in ['what do you think about ', 'tell me a ', 'who are ', 'what are ']:
                if phrase in text_lower:
                    idx = text_lower.find(phrase) + len(phrase)
                    return text[idx:].strip()

        # Fallback: return original text
        return text


# =============================================================================
# LAYER 2: CLAUDE API CLASSIFIER
# =============================================================================
class ClaudeClassifier:
    """
    Layer 2 of the two-layer router.
    Uses Claude API for ambiguous intents, returns structured JSON.
    """

    # System prompt instructing Claude to return only valid JSON
    SYSTEM_PROMPT = """You are an intent classifier for a voice assistant called Lucifer.
Your ONLY task is to classify user commands into one of these intents:
- SEARCH: Looking up information on the web
- OPEN_URL: Opening a specific website or URL
- YOUTUBE: Playing or searching for video/music content
- SYSTEM: Taking a screenshot, opening apps, checking time, system operations
- CHAT: Having a conversation, asking opinions, jokes, general questions about the assistant
- SHUTDOWN: Going offline, sleeping, stopping the assistant

Rules:
1. ALWAYS respond with ONLY valid JSON, never prose
2. Output format: {"intent": "INTENT_NAME", "query": "extracted_search_query_or_url_or_command", "confidence": 0.XX}
3. confidence is a float between 0.0 and 1.0 based on how certain you are
4. For SEARCH: extract the exact search query
5. For OPEN_URL: extract the URL or domain
6. For YOUTUBE: extract the video/music search query
7. For SYSTEM: extract the system command
8. For CHAT: keep the full conversation text
9. For SHUTDOWN: use the full command as query
10. If ambiguous, default to SEARCH or CHAT based on context

Example outputs:
- Input: "what is quantum computing"
  Output: {"intent": "SEARCH", "query": "quantum computing", "confidence": 0.95}

- Input: "open github"
  Output: {"intent": "OPEN_URL", "query": "github.com", "confidence": 0.98}

- Input: "play lofi music"
  Output: {"intent": "YOUTUBE", "query": "lofi music", "confidence": 0.95}

- Input: "take a screenshot"
  Output: {"intent": "SYSTEM", "query": "screenshot", "confidence": 0.98}

- Input: "what do you think about AI"
  Output: {"intent": "CHAT", "query": "what do you think about AI", "confidence": 0.90}"""

    def __init__(self):
        """Initialize Gemini client with API key from environment."""
        self.client = None
        self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialize the Google Gemini client."""
        try:
            import google.generativeai as genai

            # Try to get API key from environment variable
            api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')

            if not api_key:
                logger.warning("No Gemini API key found in environment variables")
                logger.info("Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable")
                return

            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel('gemini-2.0-flash')
            logger.info("Gemini classifier initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            self.client = None

    def classify(self, text: str) -> Optional[IntentResult]:
        """
        Classify the text using Claude API.

        Args:
            text: Raw command text

        Returns:
            IntentResult with classified intent, or None on failure
        """
        if self.client is None:
            logger.error("Gemini client not initialized - cannot classify")
            return None

        try:
            response = self.client.generate_content(
                self.SYSTEM_PROMPT + "\n\nUser command: " + text,
                generation_config={
                    'temperature': 0.1,
                    'max_output_tokens': 200,
                }
            )

            # Extract and parse JSON response
            raw_response = response.text
            logger.debug(f"Gemini raw response: {raw_response}")

            # Clean the response (handle potential markdown code blocks)
            json_str = raw_response.strip()
            if json_str.startswith('```json'):
                json_str = json_str[7:]
            if json_str.startswith('```'):
                json_str = json_str[3:]
            if json_str.endswith('```'):
                json_str = json_str[:-3]
            json_str = json_str.strip()

            # Parse JSON
            parsed = json.loads(json_str)

            # Validate and map intent string to Intent enum
            intent_str = parsed.get('intent', 'UNKNOWN').upper()
            try:
                intent = Intent[intent_str]
            except KeyError:
                logger.warning(f"Unknown intent from Gemini: {intent_str}")
                intent = Intent.UNKNOWN

            result = IntentResult(
                intent=intent,
                query=parsed.get('query', text),
                confidence=float(parsed.get('confidence', 0.5)),
                raw_response=raw_response
            )

            logger.info(f"Gemini classified: {intent.value} (confidence: {result.confidence})")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON response: {e}")
            logger.debug(f"Raw response was: {raw_response if 'raw_response' in locals() else 'N/A'}")
            return None

        except Exception as e:
            logger.error(f"Gemini classification error: {e}")
            return None


# =============================================================================
# COMMAND ROUTER (TWO-LAYER SYSTEM)
# =============================================================================
class CommandRouter:
    """
    Two-layer intent classification and command routing system.

    Layer 1: Fast regex/keyword matcher for obvious intents
    Layer 2: Claude API for ambiguous intents
    """

    def __init__(self, use_claude: bool = True):
        """
        Initialize the command router.

        Args:
            use_claude: Whether to use Layer 2 (Claude) for ambiguous intents
        """
        self.fast_matcher = FastMatcher()
        self.use_claude = use_claude
        self.claude_classifier = ClaudeClassifier() if use_claude else None

    def route(self, text: str, tts_speak: Optional[callable] = None) -> IntentResult:
        """
        Route a command through the two-layer system.

        Args:
            text: Raw command text from voice input
            tts_speak: Optional callback for TTS (for clarification prompts)

        Returns:
            IntentResult with classified intent and extracted query
        """
        logger.info(f"Routing command: '{text}'")

        # Layer 1: Try fast regex/keyword matching
        fast_result = self.fast_matcher.match(text)

        if fast_result:
            intent, query, confidence = fast_result
            result = IntentResult(
                intent=intent,
                query=query,
                confidence=confidence,
                raw_response="fast_matcher"
            )

            # If confidence is high enough, return immediately
            if confidence >= CLARIFICATION_THRESHOLD:
                logger.info(f"Layer 1 match: {intent.value} (confidence: {confidence})")
                return result
            else:
                # Low confidence from fast matcher, try Layer 2
                logger.info("Low confidence from Layer 1, falling to Layer 2")

        # Layer 2: Use Claude for ambiguous cases
        if self.use_claude and self.claude_classifier:
            logger.info("Invoking Layer 2 (Claude classifier)")
            claude_result = self.claude_classifier.classify(text)

            if claude_result:
                # Check if clarification is needed
                if claude_result.confidence < CLARIFICATION_THRESHOLD:
                    clarification_text = self._generate_clarification(claude_result)
                    logger.warning(f"Low confidence ({claude_result.confidence}): {clarification_text}")

                    if tts_speak:
                        tts_speak(clarification_text)

                return claude_result
            else:
                logger.warning("Layer 2 failed, falling back to fast matcher result")

        # Fallback: return FastMatcher result or UNKNOWN
        if fast_result:
            return result

        logger.warning("All classification layers failed, returning UNKNOWN")
        return IntentResult(
            intent=Intent.UNKNOWN,
            query=text,
            confidence=0.0,
            raw_response="fallback"
        )

    def _generate_clarification(self, result: IntentResult) -> str:
        """
        Generate a clarification prompt for low-confidence matches.

        Args:
            result: The low-confidence result

        Returns:
            Clarification question string
        """
        if result.intent == Intent.SEARCH:
            return f"Did you mean to search for {result.query}?"
        elif result.intent == Intent.OPEN_URL:
            return f"Did you mean to open {result.query}?"
        elif result.intent == Intent.YOUTUBE:
            return f"Did you mean to play {result.query} on YouTube?"
        elif result.intent == Intent.SYSTEM:
            return f"Did you mean to {result.query}?"
        else:
            return f"Did you mean: {result.query}?"


# =============================================================================
# INTENT HANDLERS (INTEGRATED WITH lucifer_browser.py & lucifer_research.py)
# =============================================================================
# Try to import browser module, fallback to stubs if unavailable
try:
    from lucifer_browser import (
        handle_search as _browser_search,
        handle_open_url_handler as _browser_open_url,
        handle_youtube_handler as _browser_youtube,
        handle_system_handler as _browser_system,
        close_browser,
        get_page_text,
    )
    _BROWSER_AVAILABLE = True
except ImportError as e:
    _BROWSER_AVAILABLE = False
    _browser_search = None
    _browser_open_url = None
    _browser_youtube = None
    _browser_system = None

# Try to import research module for deep research capability
try:
    from lucifer_research import handle_search_with_research, research
    _RESEARCH_AVAILABLE = True
except ImportError as e:
    _RESEARCH_AVAILABLE = False
    handle_search_with_research = None

# Try to import orchestrator for complex multi-step commands
try:
    from lucifer_orchestrator import orchestrate, PCEOrchestrator
    _ORCHESTRATOR_AVAILABLE = True
except ImportError as e:
    _ORCHESTRATOR_AVAILABLE = False
    orchestrate = None


def handle_search(query: str) -> None:
    """
    Handle SEARCH intent - use research pipeline for in-depth results.

    Args:
        query: Search query string
    """
    logger.info(f"[HANDLER] SEARCH: '{query}'")
    print(f"[LUCIFER] Researching: {query}")

    if _RESEARCH_AVAILABLE and handle_search_with_research:
        handle_search_with_research(query)
    elif _BROWSER_AVAILABLE and _browser_search:
        _browser_search(query)
    else:
        print("[RESEARCH] Research module not available, falling back to basic search")
        if _BROWSER_AVAILABLE and _browser_search:
            _browser_search(query)


def handle_open_url(url: str) -> None:
    """
    Handle OPEN_URL intent - navigate to URL using browser.

    Args:
        url: URL to open
    """
    logger.info(f"[HANDLER] OPEN_URL: '{url}'")
    print(f"[LUCIFER] Opening URL: {url}")

    if _BROWSER_AVAILABLE and _browser_open_url:
        _browser_open_url(url)
    else:
        print("[BROWSER] Browser module not available")


def handle_youtube(query: str) -> None:
    """
    Handle YOUTUBE intent - search/play YouTube content using browser.

    Args:
        query: YouTube search query
    """
    logger.info(f"[HANDLER] YOUTUBE: '{query}'")
    print(f"[LUCIFER] YouTube: {query}")

    if _BROWSER_AVAILABLE and _browser_youtube:
        _browser_youtube(query)
    else:
        print("[BROWSER] Browser module not available")


def handle_system(command: str) -> None:
    """
    Handle SYSTEM intent - execute system operations using browser module.

    Args:
        command: System command string
    """
    logger.info(f"[HANDLER] SYSTEM: '{command}'")
    print(f"[LUCIFER] System command: {command}")

    if _BROWSER_AVAILABLE and _browser_system:
        _browser_system(command)
    else:
        print("[BROWSER] Browser module not available")


def handle_chat(text: str) -> None:
    """
    Handle CHAT intent - conversational response.
    Currently a placeholder - can be extended with LLM integration.

    Args:
        text: User's chat message
    """
    logger.info(f"[HANDLER] CHAT: '{text}'")
    print(f"[LUCIFER] Chat: {text}")
    print("[CHAT] Response not implemented - extend with LLM integration")


def handle_shutdown(command: str) -> None:
    """
    Handle SHUTDOWN intent - stop the assistant and close browser.

    Args:
        command: Shutdown command text
    """
    logger.info(f"[HANDLER] SHUTDOWN: '{command}'")
    print("[LUCIFER] Shutting down...")

    if _BROWSER_AVAILABLE:
        try:
            close_browser()
        except:
            pass


# =============================================================================
# DISPATCHER
# =============================================================================
def dispatch(result: IntentResult, tts_speak: Optional[callable] = None) -> None:
    """
    Dispatch the classified intent to the appropriate handler.

    Args:
        result: IntentResult from classification
        tts_speak: Optional TTS callback for responses
    """
    logger.info(f"Dispatching: {result.intent.value} -> {result.query}")

    handler_map = {
        Intent.SEARCH: handle_search,
        Intent.OPEN_URL: handle_open_url,
        Intent.YOUTUBE: handle_youtube,
        Intent.SYSTEM: handle_system,
        Intent.CHAT: handle_chat,
        Intent.SHUTDOWN: handle_shutdown,
        Intent.UNKNOWN: handle_chat  # Fallback to chat for unknown
    }

    handler = handler_map.get(result.intent, handle_chat)
    handler(result.query)


# =============================================================================
# MAIN ROUTE_COMMAND FUNCTION (INTEGRATION POINT)
# =============================================================================
# Singleton router instance
_router: Optional[CommandRouter] = None


def init_router(use_claude: bool = True, tts_speak: Optional[callable] = None) -> CommandRouter:
    """
    Initialize the command router.

    Args:
        use_claude: Enable Layer 2 Claude classification
        tts_speak: TTS callback for clarification prompts

    Returns:
        Initialized CommandRouter instance
    """
    global _router
    _router = CommandRouter(use_claude=use_claude)
    _router.tts_speak = tts_speak
    return _router


def route_command(text: str, tts_speak: Optional[callable] = None) -> IntentResult:
    """
    Main entry point - integrate with lucifer_voice.py.
    This replaces the stub in lucifer_voice.py.

    Args:
        text: Raw command text from voice input
        tts_speak: Optional TTS callback for clarification

    Returns:
        IntentResult from classification
    """
    global _router

    # Initialize router if not already done
    if _router is None:
        _router = CommandRouter(use_claude=True)

    # Route the command
    result = _router.route(text, tts_speak=tts_speak)

    # Dispatch to appropriate handler
    dispatch(result, tts_speak=tts_speak)

    return result


# =============================================================================
# TEST/DEMO
# =============================================================================
if __name__ == "__main__":
    """
    Demo/test the intent routing system.
    """
    print("=" * 60)
    print("Lucifer Intent Router - Test Mode")
    print("=" * 60)

    # Initialize router
    router = CommandRouter(use_claude=True)

    # Test commands
    test_commands = [
        "search for quantum computing breakthroughs",
        "open github.com",
        "play lo-fi hip hop on youtube",
        "take a screenshot",
        "what do you think about AI",
        "lucifer sleep",
        "look up best Python frameworks 2024",
        "what time is it"
    ]

    print("\n[NOTE] Set ANTHROPIC_API_KEY env var to enable Layer 2]\n")

    for cmd in test_commands:
        print(f"\n--- Command: '{cmd}' ---")
        result = router.route(cmd)
        print(f"Intent: {result.intent.value}")
        print(f"Query: {result.query}")
        print(f"Confidence: {result.confidence}")

    print("\n" + "=" * 60)
    print("Test complete")
    print("=" * 60)