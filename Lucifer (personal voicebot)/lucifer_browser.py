# =============================================================================
# LUCIFER BROWSER AUTOMATION MODULE
# =============================================================================
# Requirements (as specified):
# MODULE A - General Browser Automation:
#   - handle_open_url(url): Opens any URL, resolves shortnames (reddit, github, etc.)
#   - handle_system(command): screenshot, open apps, time/date, volume control
#   - get_page_text(url): Pure scraping with BeautifulSoup, return first 2000 chars
# MODULE B - YouTube Control:
#   - handle_youtube(query): Search, click first non-ad video, play
#   - Support: pause, next, close
#   - Use explicit waits (WebDriverWait), never time.sleep()
#   - Handle cookie consent popups automatically
# Both modules:
#   - Single persistent Chrome driver instance
#   - TTS confirmation for actions
#   - Log all actions to lucifer_session.log
#   - ChromeDriver auto-download via webdriver-manager
# =============================================================================

import os
import re
import json
import logging
import subprocess
import platform
from datetime import datetime
from typing import Optional, Tuple
from pathlib import Path

# Selenium and webdriver
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys

# Scraping
from bs4 import BeautifulSoup

# System automation
import pyautogui

# TTS (for confirmation)
import pyttsx3

# Windows volume control
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False


# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
LOG_FILE = "lucifer_session.log"

# Shortname to URL mapping
SHORTNAME_MAP = {
    "reddit": "https://www.reddit.com",
    "github": "https://github.com",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "twitter": "https://twitter.com",
    "x": "https://twitter.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "wiki": "https://www.wikipedia.org",
    "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
    "linkedin": "https://www.linkedin.com",
    "stackoverflow": "https://stackoverflow.com",
    "discord": "https://discord.com",
    "twitch": "https://www.twitch.tv",
    "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "medium": "https://medium.com",
    "notion": "https://www.notion.so",
}

# Webdriver-manager setup for Chrome
CHROME_OPTIONS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--start-maximized",
]


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> logging.Logger:
    """Initialize logging for browser module."""
    logger = logging.getLogger("LuciferBrowser")

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
# TEXT-TO-SPEECH ENGINE
# =============================================================================
from lucifer_tts import tts as _tts

def speak(text: str) -> None:
    """Speak confirmation message."""
    _tts.speak(text)


# =============================================================================
# SINGLETON BROWSER DRIVER
# =============================================================================
class BrowserDriver:
    """
    Singleton Chrome driver instance for persistent browser.
    Uses undetected-chromedriver to avoid bot detection.
    """

    _instance: Optional[uc.Chrome] = None
    _youtube_mode = False

    @classmethod
    def get_driver(cls) -> uc.Chrome:
        """
        Get or create the singleton Chrome driver.

        Returns:
            Chrome driver instance
        """
        if cls._instance is None:
            logger.info("Creating new Chrome driver instance...")
            cls._instance = cls._create_driver()
            logger.info("Chrome driver created successfully")
        return cls._instance

    @classmethod
    def _create_driver(cls) -> uc.Chrome:
        """Create and configure Chrome driver."""
        options = uc.ChromeOptions()

        # Add Chrome options
        for opt in CHROME_OPTIONS:
            options.add_argument(opt)

        # Create driver (auto-downloads ChromeDriver)
        try:
            driver = uc.Chrome(options=options, version_main=None)
            driver.set_page_load_timeout(30)
            driver.implicitly_wait(5)
            logger.info("Undetected ChromeDriver initialized")
            return driver
        except Exception as e:
            logger.error(f"Failed to create Chrome driver: {e}")
            raise

    @classmethod
    def restart(cls) -> None:
        """Restart the browser driver (for stale sessions)."""
        logger.info("Restarting Chrome driver...")
        try:
            if cls._instance:
                cls._instance.quit()
        except:
            pass
        cls._instance = None
        # Create new driver
        cls._instance = cls._create_driver()
        logger.info("Chrome driver restarted")

    @classmethod
    def close(cls) -> None:
        """Close the browser driver."""
        if cls._instance:
            try:
                cls._instance.quit()
                logger.info("Chrome driver closed")
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")
            finally:
                cls._instance = None

    @classmethod
    def handle_cookie_consent(cls, driver) -> None:
        """
        Handle cookie consent popups on various sites.

        Args:
            driver: Selenium WebDriver instance
        """
        try:
            # Common cookie consent button selectors
            consent_selectors = [
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'allow')]",
                "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'okay')]",
                "//button[@id='onetrust-accept-btn-handler']",
                "//button[contains(@class, 'consent')]",
                "//button[contains(@id, 'consent')]",
            ]

            for selector in consent_selectors:
                try:
                    btn = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    btn.click()
                    logger.info("Cookie consent handled")
                    return
                except:
                    continue

        except Exception as e:
            logger.debug(f"Cookie consent handling: {e}")

    @classmethod
    def navigate(cls, url: str) -> None:
        """
        Navigate to URL with cookie consent handling.

        Args:
            url: Target URL
        """
        driver = cls.get_driver()
        driver.get(url)
        cls.handle_cookie_consent(driver)
        logger.info(f"Navigated to: {url}")


# =============================================================================
# MODULE A: GENERAL BROWSER AUTOMATION
# =============================================================================
def resolve_url(input_str: str) -> Tuple[str, bool]:
    """
    Resolve shortnames or bare domains to full URLs.
    Uses fallback to Google "I'm Feeling Lucky" if not found.

    Args:
        input_str: User input (shortname, domain, or URL)

    Returns:
        Tuple of (resolved_url, was_shortname)
    """
    input_lower = input_str.lower().strip()

    # Check if it's already a URL with scheme
    if re.match(r'^https?://', input_lower):
        return (input_str, False)

    # Check shortname map
    if input_lower in SHORTNAME_MAP:
        return (SHORTNAME_MAP[input_lower], True)

    # Check if it looks like a domain
    if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}', input_lower):
        return (f"https://{input_str}", False)

    # Fallback: Google "I'm Feeling Lucky" search
    fallback_url = f"https://www.google.com/search?q={input_str}&btnI"
    logger.info(f"Shortname not found, using fallback: {fallback_url}")
    return (fallback_url, True)


def handle_open_url(url: str) -> None:
    """
    Open any URL in Chrome.
    - Prepend https:// if no scheme
    - Resolve shortnames (reddit, github, etc.)
    - Use fallback Google search for unknown shortnames

    Args:
        url: URL, shortname, or domain
    """
    logger.info(f"handle_open_url called with: {url}")

    # Resolve the URL
    resolved, was_shortname = resolve_url(url)

    # Navigate with error handling and retry
    try:
        BrowserDriver.navigate(resolved)
        speak(f"Opening {url}")
        logger.info(f"Opened URL: {resolved} (shortname: {was_shortname})")
    except Exception as e:
        logger.error(f"Failed to open URL: {e}, retrying...")
        try:
            BrowserDriver.restart()
            BrowserDriver.navigate(resolved)
            speak(f"Opening {url}")
        except Exception as e2:
            logger.error(f"Retry failed: {e2}")
            speak(f"Failed to open {url}")


def handle_system(command: str) -> None:
    """
    Handle OS-level system commands:
    - "take screenshot" → pyautogui screenshot to Desktop
    - "open [app]" → subprocess launch (notepad, calc, explorer, vscode)
    - "what time is it" / "what's today's date" → TTS answer
    - "volume up/down" → Windows volume control (pycaw)

    Args:
        command: System command string
    """
    logger.info(f"handle_system called with: {command}")
    cmd_lower = command.lower().strip()

    # Screenshot
    if "screenshot" in cmd_lower:
        take_screenshot()
        return

    # Time and date
    if "time" in cmd_lower and "what" in cmd_lower:
        tell_time()
        return

    if "date" in cmd_lower and "today" in cmd_lower:
        tell_date()
        return

    # Volume control
    if "volume" in cmd_lower:
        adjust_volume(cmd_lower)
        return

    # Open application
    if cmd_lower.startswith("open "):
        app_name = command[5:].strip()
        open_application(app_name)
        return

    logger.warning(f"Unknown system command: {command}")


def take_screenshot() -> None:
    """Take screenshot and save to Desktop with timestamp."""
    try:
        desktop = Path.home() / "Desktop"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"lucifer_screenshot_{timestamp}.png"
        filepath = desktop / filename

        pyautogui.screenshot().save(str(filepath))
        speak(f"Screenshot saved")
        logger.info(f"Screenshot saved to: {filepath}")
    except Exception as e:
        logger.error(f"Screenshot failed: {e}")
        speak(f"Screenshot failed")


def open_application(app_name: str) -> None:
    """Open application via subprocess."""
    app_map = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "explorer": "explorer.exe",
        "vscode": "code",
        "visual studio code": "code",
        "cmd": "cmd.exe",
        "command prompt": "cmd.exe",
        "powershell": "powershell.exe",
        "browser": "chrome.exe",
        "chrome": "chrome.exe",
        "edge": "msedge.exe",
    }

    app_lower = app_name.lower()
    exe = app_map.get(app_lower)

    if not exe:
        # Try to open directly
        exe = app_name

    try:
        # Use start on Windows to open in new window
        if platform.system() == "Windows":
            subprocess.Popen(["start", "", exe], shell=True)
        else:
            subprocess.Popen([exe])

        speak(f"Opening {app_name}")
        logger.info(f"Opened application: {exe}")
    except Exception as e:
        logger.error(f"Failed to open {app_name}: {e}")
        speak(f"Could not open {app_name}")


def tell_time() -> None:
    """Speak current time."""
    now = datetime.now()
    time_str = now.strftime("%I:%M %p")
    speak(f"The time is {time_str}")
    logger.info(f"Told time: {time_str}")


def tell_date() -> None:
    """Speak today's date."""
    now = datetime.now()
    date_str = now.strftime("%B %d, %Y")
    speak(f"Today's date is {date_str}")
    logger.info(f"Told date: {date_str}")


def adjust_volume(cmd: str) -> None:
    """Adjust system volume on Windows."""
    if not PYCAW_AVAILABLE:
        speak("Volume control not available")
        logger.warning("pycaw not available")
        return

    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        current = volume.GetMasterVolumeLevelScalar()

        if "up" in cmd:
            new_vol = min(1.0, current + 0.1)
            volume.SetMasterVolumeLevelScalar(new_vol, None)
            speak("Volume increased")
            logger.info(f"Volume up: {current} -> {new_vol}")
        elif "down" in cmd:
            new_vol = max(0.0, current - 0.1)
            volume.SetMasterVolumeLevelScalar(new_vol, None)
            speak("Volume decreased")
            logger.info(f"Volume down: {current} -> {new_vol}")

    except Exception as e:
        logger.error(f"Volume control failed: {e}")
        speak("Volume control failed")


def get_page_text(url: str, max_chars: int = 2000) -> str:
    """
    Open page, wait for load, extract visible text using BeautifulSoup.
    Returns first max_chars characters.

    Args:
        url: Target URL
        max_chars: Maximum characters to return (default 2000)

    Returns:
        Extracted text (first 2000 chars)
    """
    logger.info(f"get_page_text called for: {url}")

    try:
        # Resolve URL first
        resolved, _ = resolve_url(url)

        # Navigate
        driver = BrowserDriver.get_driver()
        driver.get(resolved)

        # Wait for page to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Get page source and parse with BeautifulSoup
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements
        for tag in soup(["script", "style"]):
            tag.decompose()

        # Get text
        text = soup.get_text(separator=" ", strip=True)

        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        # Truncate to max_chars
        result = text[:max_chars]

        logger.info(f"Extracted {len(result)} chars from {url}")
        return result

    except Exception as e:
        logger.error(f"get_page_text failed: {e}")
        return f"Error: {str(e)}"


# =============================================================================
# MODULE B: YOUTUBE CONTROL
# =============================================================================
def handle_youtube(query: str) -> None:
    """
    Open YouTube, search for query, click first non-ad video and play.

    Args:
        query: YouTube search query
    """
    logger.info(f"handle_youtube called with: {query}")

    # Check for follow-up commands
    cmd_lower = query.lower().strip()

    if cmd_lower == "pause" or "pause" in cmd_lower:
        youtube_pause()
        return

    if cmd_lower == "next" or "next video" in cmd_lower:
        youtube_next()
        return

    if cmd_lower == "close" or "close youtube" in cmd_lower:
        youtube_close()
        return

    # Regular search and play - with error handling
    try:
        youtube_search_and_play(query)
    except Exception as e:
        logger.error(f"YouTube error: {e}, restarting browser...")
        BrowserDriver.restart()
        try:
            youtube_search_and_play(query)
        except Exception as e2:
            logger.error(f"Retry failed: {e2}")
            speak("Couldn't open YouTube")


def youtube_search_and_play(query: str) -> None:
    """Search YouTube and click first video to play."""
    import urllib.parse
    import time
    
    driver = BrowserDriver.get_driver()

    try:
        # Clean the query - remove common words
        clean_query = query.lower()
        for word in ["on youtube", "youtube", "play"]:
            clean_query = clean_query.replace(word, "")
        clean_query = clean_query.strip()
        
        if not clean_query:
            clean_query = "music"
        
        # Navigate directly to YouTube search URL
        search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_query)}"
        driver.get(search_url)
        
        logger.info(f"YouTube search: {clean_query}")
        print(f"🔍 Searching YouTube for: {clean_query}")
        
        # Wait for page to load
        time.sleep(2)
        
        # Try to click the first video result
        try:
            # Find first video link and click it
            video_xpath = '//ytd-video-renderer//a[@id="video-title"]'
            video_element = driver.find_element("xpath", video_xpath)
            
            # Get video title before clicking
            video_title = video_element.get_attribute("title")
            if not video_title:
                video_title = "video"
            
            # Click it
            video_element.click()
            
            speak(f"Playing {video_title}")
            print(f"▶️ Now playing: {video_title}")
            logger.info(f"Playing YouTube: {video_title}")
            
        except Exception as e:
            # If clicking fails, just leave it on search results
            logger.warning(f"Could not click video: {e}")
            speak(f"Opened YouTube search for {clean_query}")
        
    except Exception as e:
        logger.error(f"YouTube search failed: {e}")
        try:
            driver.get("https://www.youtube.com")
            speak("Opened YouTube")
        except:
            speak("Could not open YouTube")


def youtube_pause() -> None:
    """Pause or unpause YouTube video."""
    try:
        driver = BrowserDriver.get_driver()

        # Send space key to pause/unpause
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.SPACE)

        speak("Video paused" if "paused" not in str(driver.execute_script("return document.querySelector('video.paused')")) else "Video playing")
        logger.info("YouTube pause toggled")

    except Exception as e:
        logger.error(f"YouTube pause failed: {e}")
        speak("Could not pause video")


def youtube_next() -> None:
    """Skip to next YouTube video."""
    try:
        driver = BrowserDriver.get_driver()

        # Try to find and click next button
        # Method 1: Menu button -> next
        try:
            menu_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.dropdown-trigger"))
            )
            menu_btn.click()

            next_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-title='Next']"))
            )
            next_btn.click()
        except:
            # Method 2: Keyboard shortcut (shift+n)
            body = driver.find_element(By.TAG_NAME, "body")
            body.send_keys(Keys.SHIFT + "n")

        speak("Skipping to next video")
        logger.info("YouTube: next video")

    except Exception as e:
        logger.error(f"YouTube next failed: {e}")
        speak("Could not skip video")


def youtube_close() -> None:
    """Close YouTube tab."""
    try:
        driver = BrowserDriver.get_driver()

        # Close current tab if there are multiple
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
        else:
            # Go to blank page
            driver.get("about:blank")

        BrowserDriver._youtube_mode = False
        speak("YouTube closed")
        logger.info("YouTube closed")

    except Exception as e:
        logger.error(f"YouTube close failed: {e}")


# =============================================================================
# INTEGRATION WITH lucifer_intent.py HANDLERS
# =============================================================================
# These functions are called by the intent router in lucifer_intent.py
def handle_search(query: str) -> None:
    """
    Handle SEARCH intent - perform web search using browser.

    Args:
        query: Search query string
    """
    logger.info(f"[BROWSER] SEARCH: '{query}'")
    url = f"https://www.google.com/search?q={query}"
    handle_open_url(url)


def handle_open_url_handler(url: str) -> None:
    """Wrapper for OPEN_URL intent handler."""
    handle_open_url(url)


def handle_youtube_handler(query: str) -> None:
    """Wrapper for YOUTUBE intent handler."""
    handle_youtube(query)


def handle_system_handler(command: str) -> None:
    """Wrapper for SYSTEM intent handler."""
    handle_system(command)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
def close_browser() -> None:
    """Close the browser driver - useful for cleanup."""
    BrowserDriver.close()
    logger.info("Browser closed")


def is_browser_running() -> bool:
    """Check if browser driver is active."""
    return BrowserDriver._instance is not None


# =============================================================================
# MAIN / TEST
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Lucifer Browser Module - Test Mode")
    print("=" * 60)

    # Test URL resolution
    test_urls = ["reddit", "github.com", "https://google.com", "notion"]
    print("\n--- URL Resolution Tests ---")
    for url in test_urls:
        resolved, was_short = resolve_url(url)
        print(f"  {url} -> {resolved} (shortname: {was_short})")

    # Test system commands
    print("\n--- System Commands ---")
    print("  Supported: screenshot, open [app], time, date, volume up/down")

    # Test YouTube
    print("\n--- YouTube Commands ---")
    print("  handle_youtube(query) - search and play")
    print("  youtube_pause() - pause/unpause")
    print("  youtube_next() - next video")
    print("  youtube_close() - close YouTube")

    print("\n" + "=" * 60)
    print("Test complete - import and use in lucifer_intent.py")
    print("=" * 60)