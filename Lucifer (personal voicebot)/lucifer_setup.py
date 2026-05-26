#!/usr/bin/env python
"""
Lucifer Setup Script
Auto-installs requirements, checks system, downloads ChromeDriver,
creates database schema, and runs self-test.
"""

import os
import sys
import json
import subprocess
import platform
import sqlite3
from pathlib import Path


# =============================================================================
# COLORS (disable on Windows to avoid encoding issues)
# =============================================================================
GREEN = RED = YELLOW = CYAN = RESET = ""
try:
    if platform.system() != "Windows":
        from colorama import init, Fore, Style
        init(autoreset=True)
        GREEN = Fore.GREEN
        RED = Fore.RED
        YELLOW = Fore.YELLOW
        CYAN = Fore.CYAN
        RESET = Style.RESET_ALL
except ImportError:
    pass


# =============================================================================
# UTILITIES
# =============================================================================
def print_step(msg):
    print(f"\n{CYAN}>>> {msg}{RESET}")


def print_success(msg):
    print(f"{GREEN}[OK] {msg}{RESET}")


def print_error(msg):
    print(f"{RED}[ERROR] {msg}{RESET}")


def print_warning(msg):
    print(f"{YELLOW}[WARN] {msg}{RESET}")


def run_command(cmd, check=True):
    """Run shell command and return result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=120
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)


# =============================================================================
# REQUIREMENTS INSTALLATION
# =============================================================================
def install_requirements():
    print_step("Installing Python requirements...")

    req_file = "requirements.txt"
    if not os.path.exists(req_file):
        print_error("requirements.txt not found!")
        return False

    # Upgrade pip first
    print("  Upgrading pip...")
    run_command("python -m pip install --upgrade pip", check=False)

    # Install requirements
    print("  Installing packages...")
    success, out, err = run_command(f'pip install -r "{req_file}"')

    if success:
        print_success("Requirements installed")
        return True
    else:
        print_error(f"Installation failed: {err[:200]}")
        return False


# =============================================================================
# SYSTEM CHECKS
# =============================================================================
def check_python_version():
    """Check Python version."""
    print_step("Checking Python version...")
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        print_success(f"Python {version.major}.{version.minor}.{version.micro} - OK")
        return True
    else:
        print_error(f"Python 3.8+ required, found {version.major}.{version.minor}")
        return False


def check_chrome():
    """Check if Chrome is installed."""
    print_step("Checking Google Chrome...")

    chrome_paths = {
        "Windows": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "Linux": [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ],
        "Darwin": [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    }

    system = platform.system()
    paths = chrome_paths.get(system, [])

    for path in paths:
        if os.path.exists(path):
            print_success(f"Chrome found at: {path}")
            return True

    print_warning("Chrome not found in standard locations")
    print("  Please install Chrome or set chrome_path in lucifer_config.json")
    return False


def check_microphone():
    """Check if microphone is available."""
    print_step("Checking microphone...")

    try:
        import speech_recognition as sr
        mic = sr.Microphone()
        with mic as source:
            pass
        print_success("Microphone available")
        return True
    except Exception as e:
        print_warning(f"Microphone check failed: {e}")
        return False


def check_internet():
    """Check internet connection."""
    print_step("Checking internet connection...")

    try:
        import requests
        response = requests.get("https://www.google.com", timeout=5)
        if response.status_code == 200:
            print_success("Internet connected")
            return True
    except:
        pass

    print_warning("No internet connection")
    return False


# =============================================================================
# CHROMEDRIVER SETUP
# =============================================================================
def setup_chromedriver():
    """Download and setup ChromeDriver."""
    print_step("Setting up ChromeDriver...")

    # webdriver-manager should handle this automatically
    # Just verify it can be imported
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium import webdriver

        # This will download driver if needed
        driver_path = ChromeDriverManager().install()
        print_success(f"ChromeDriver ready: {driver_path}")
        return True

    except Exception as e:
        print_warning(f"ChromeDriver setup issue: {e}")
        print("  This will be handled at runtime")
        return True  # Not fatal


# =============================================================================
# DATABASE SETUP
# =============================================================================
def create_database():
    """Create SQLite database schemas."""
    print_step("Creating database schema...")

    # Define tables as separate CREATE statements
    tables = [
        ("conversation_history", """
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """),
        ("execution_plans", """
            CREATE TABLE IF NOT EXISTS execution_plans (
                plan_id TEXT PRIMARY KEY,
                original_command TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                status TEXT DEFAULT 'created',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """),
        ("execution_steps", """
            CREATE TABLE IF NOT EXISTS execution_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                tool TEXT NOT NULL,
                input_text TEXT,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                error TEXT,
                retries INTEGER DEFAULT 0,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES execution_plans(plan_id)
            )
        """),
        ("scraped_data", """
            CREATE TABLE IF NOT EXISTS scraped_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                step_number INTEGER,
                url TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plan_id) REFERENCES execution_plans(plan_id)
            )
        """),
        ("user_preferences", """
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """),
    ]

    # Create context database
    try:
        conn = sqlite3.connect("lucifer_context.db")
        for name, sql in tables:
            conn.execute(sql)
        conn.commit()
        conn.close()
        print_success("Created lucifer_context.db")
    except Exception as e:
        print_error(f"Failed to create lucifer_context.db: {e}")
        return False

    # Create research cache database
    try:
        conn = sqlite3.connect("lucifer_research_cache.db")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_cache (
                cache_key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        print_success("Created lucifer_research_cache.db")
    except Exception as e:
        print_error(f"Failed to create lucifer_research_cache.db: {e}")
        return False

    return True


# =============================================================================
# CONFIG FILE SETUP
# =============================================================================
def create_config():
    """Create default config file."""
    print_step("Checking configuration file...")

    config_file = "lucifer_config.json"

    if os.path.exists(config_file):
        print_success(f"{config_file} already exists")
        return True

    default_config = {
        "wake_word": "hey lucifer",
        "voice_speed": 150,
        "voice_volume": 0.8,
        "tts_engine": "pyttsx3",
        "elevenlabs_key": "",
        "scrapingdog_key": "YOUR_SCRAPINGDOG_API_KEY",
        "anthropic_key": "YOUR_ANTHROPIC_API_KEY",
        "chrome_path": "",
        "log_level": "INFO",
        "user_name": "Sir",
        "hotkey": "f9"
    }

    try:
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=2)
        print_success(f"Created {config_file}")
        print(f"\n  {YELLOW}Please edit {config_file} and add your API keys!{RESET}")
        return True
    except Exception as e:
        print_error(f"Failed to create config: {e}")
        return False


# =============================================================================
# SELF TEST
# =============================================================================
def run_self_test():
    """Run a basic self-test."""
    print_step("Running self-test...")

    test_items = []

    # Test imports
    try:
        import lucifer_voice
        import lucifer_intent
        import lucifer_browser
        import lucifer_research
        import lucifer_orchestrator
        import lucifer_main
        test_items.append(("Module imports", True))
    except Exception as e:
        test_items.append(("Module imports", False))
        print_error(f"Import failed: {e}")

    # Test config loading
    try:
        from lucifer_main import load_config
        config = load_config()
        test_items.append(("Config loading", True))
    except Exception as e:
        test_items.append(("Config loading", False))
        print_error(f"Config failed: {e}")

    # Test database
    try:
        conn = sqlite3.connect("lucifer_context.db")
        conn.execute("SELECT 1").fetchone()
        conn.close()
        test_items.append(("Database", True))
    except Exception as e:
        test_items.append(("Database", False))
        print_error(f"Database failed: {e}")

    # Print results
    print("\n" + "=" * 40)
    print("SELF-TEST RESULTS")
    print("=" * 40)

    all_passed = True
    for name, passed in test_items:
        status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 40)

    if all_passed:
        print_success("All tests passed!")
        return True
    else:
        print_error("Some tests failed - check errors above")
        return False


# =============================================================================
# MAIN
# =============================================================================
def main():
    """Run full setup process."""
    print("\n" + "=" * 60)
    print("LUCIFER SETUP SCRIPT")
    print("=" * 60)

    # Check Python version
    if not check_python_version():
        sys.exit(1)

    # Check system
    check_chrome()
    check_microphone()
    check_internet()

    # Install requirements
    if not install_requirements():
        print_error("Setup failed at requirements installation")
        sys.exit(1)

    # Setup ChromeDriver
    setup_chromedriver()

    # Create database
    if not create_database():
        print_error("Setup failed at database creation")
        sys.exit(1)

    # Create config
    if not create_config():
        print_error("Setup failed at config creation")
        sys.exit(1)

    # Run self-test
    if not run_self_test():
        print_warning("Self-test had issues but you can still try running")

    print("\n" + "=" * 60)
    print(f"{GREEN}SETUP COMPLETE!{RESET}")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Edit lucifer_config.json and add your API keys")
    print("  2. Run: python lucifer_main.py")
    print("  3. Say 'hey lucifer' or press F9 to activate")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()