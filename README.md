# 🔥 Lucifer - Autonomous AI Assistant

## A Voice-Activated JARVIS-Inspired Desktop Assistant with PCE Orchestration

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Google Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=for-the-badge&logo=googlebard&logoColor=white)](https://deepmind.google/gemini)
[![Anthropic Claude](https://img.shields.io/badge/Claude-D97706?style=for-the-badge&logo=anthropic&logoColor=white)](https://anthropic.com)
[![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=for-the-badge&logo=selenium&logoColor=white)](https://www.selenium.dev)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

Welcome to **Lucifer** — a hyper-capable, voice-controlled AI assistant modeled after Tony Stark's JARVIS but darker, sharper, and more autonomous. It listens for a wake word, understands intent, plans complex tasks, executes them via browser automation and web research, and responds with speech — all through your terminal.

> [!NOTE]
> 💡 **A Note from the Author:**
> This project is a **prototype** built entirely for **learning purposes**. It was developed to explore AI-assisted workflows, voice interfaces, browser automation, and multi-agent orchestration. Feel free to fork it, break it, and build upon it!

---

## 🛠️ Technology Stack

This application is built on a modular Python architecture with voice, browser, and AI integration:

*   **Python 3.9+** — Core language with async/await concurrency
*   **SpeechRecognition + PyAudio** — Voice wake word detection & Google Web Speech API transcription
*   **pyttsx3** — Offline text-to-speech engine (SAPI5 on Windows)
*   **Selenium + undetected-chromedriver** — Browser automation for web searches, YouTube, scraping
*   **Google Gemini (`gemini-2.0-flash`)** — Planning, verification, and conversational intelligence
*   **Anthropic Claude** — Research synthesis and intent parsing
*   **OpenAI** — Alternative LLM backend for intent routing
*   **ScrapingDog API** — SERP scraping and deep webpage extraction
*   **BeautifulSoup + newspaper3k + readability** — HTML parsing and article extraction
*   **SQLite** — Context management, research cache, execution logging
*   **Colorama + PyFiglet** — Terminal aesthetics with ASCII banners

---

## 🌟 Key Features

1.  **Voice-First Interaction**:
    *   **Wake Word**: Say "Hey Lucifer" to activate — no hands needed
    *   **Continuous Listening**: Background microphone monitoring with Google Speech API
    *   **Hotkey Activation**: Press `F9` for instant voice command mode
    *   **Text Fallback**: Type commands directly in the terminal at any time
2.  **PCE Orchestration (Plan → Context → Execute)**:
    *   **Planning Phase**: Gemini breaks complex commands into atomic subtasks (SEARCH, OPEN_URL, YOUTUBE, SCRAPE, SPEAK, SCREENSHOT, WAIT)
    *   **Context Phase**: SQLite-backed conversation history (last 10 turns) with user preferences
    *   **Execution Phase**: Runs steps with 30-second timeout, self-verification via Gemini, retry on failure, and result synthesis
3.  **Browser Automation**:
    *   **YouTube Playback**: Search and play videos automatically
    *   **URL Navigation**: Open any website with shortname resolution (e.g., "open reddit")
    *   **Web Scraping**: Deep scrape pages with BeautifulSoup and newspaper3k
4.  **Autonomous Research**:
    *   5-step pipeline: query formulation → SERP scraping → deep scrape → Claude synthesis → speech output
    *   SQLite caching with 1-hour TTL and rate limiting (10 calls/min)
5.  **JARVIS Persona**:
    *   Sardonic, confident, and proactive personality
    *   Conversational smalltalk with witty fallback responses
    *   Addresses you as "Sir" or "Boss"
6.  **System Automation**:
    *   Screenshot capture, volume control, time/date queries
    *   Session persistence with rolling log files

---

## 📸 Screen Gallery

| 🖥️ Terminal Banner & System Checks | 🎤 Voice Activation & Command Processing |
| :---: | :---: |
| ![Terminal Banner](assets/terminal-banner.png) | ![Voice Command](assets/voice-command.png) |

| 🤖 PCE Orchestrator Execution | 📊 System Report & Dashboard |
| :---: | :---: |
| ![Orchestrator](assets/orchestrator.png) | ![System Report](assets/system-report.png) |

> *Note: Screenshots are placeholders. Run the project to see the actual terminal UI.*

---

## 🚀 How to Run Locally

> **Requirements**: Python 3.9+, microphone, Chrome browser, and an internet connection.

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/AniketDaiya/Luciferbot.git
    cd Luciferbot
    ```

2.  **Navigate to the Project Directory**:
    ```bash
    cd "Lucifer (personal voicebot)"
    ```

3.  **Set Up a Virtual Environment (Recommended)**:
    ```bash
    python -m venv venv
    .\venv\Scripts\activate    # Windows
    # source venv/bin/activate # Linux/Mac
    ```

4.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

5.  **Configure API Keys**:
    Create a `.env` file in the project directory:
    ```env
    GEMINI_API_KEY=your_gemini_api_key
    ANTHROPIC_API_KEY=your_anthropic_api_key
    OPENAI_API_KEY=your_openai_api_key
    SCRAPINGDOG_API_KEY=your_scrapingdog_api_key
    ```
    You can also edit `lucifer_config.json` to set keys and preferences directly.

6.  **Run Setup (Optional but Recommended)**:
    ```bash
    python lucifer_setup.py
    ```

7.  **Launch Lucifer**:
    ```bash
    python lucifer_main.py
    ```

8.  **Start Talking**:
    - Say **"Lucifer"** followed by your command (e.g., "Lucifer, search for AI news")
    - Or press **F9** and speak
    - Or just **type** commands in the terminal

---

## 🐛 Known Limitations & Architecture

*   **Microphone Required**: Voice features need a working microphone and internet (Google Speech API).
*   **Windows-First**: Optimized for Windows with SAPI5 TTS; Linux/Mac may need minor config tweaks.
*   **API Key Dependent**: Full functionality requires Gemini, Anthropic, OpenAI, and/or ScrapingDog API keys.
*   **No Authentication**: No user login or session security — designed for local, personal use.
*   **No Persistent Backend**: All data (context, cache, logs) stored locally in SQLite files.
*   **Rate Limits**: Free-tier API quotas may cause fallback to local witty responses.

---

## ⚠️ Important Notes

> [!IMPORTANT]
> **This is a prototype built for learning purposes.** The codebase explores AI agent architectures, voice interfaces, and browser automation. It is not production-ready and may have rough edges. Feel free to experiment, extend, and improve it!

**Troubleshooting Tips:**
- **Speech not working?** Check your microphone permissions and internet connection.
- **Chrome not opening?** Set `chrome_path` in `lucifer_config.json` to your Chrome executable.
- **API quota exceeded?** Lucifer falls back to local witty responses automatically.
- **Unicode errors on Windows?** The script auto-configures UTF-8 encoding.

---

## 🤝 Let's Connect & Be Friends!

I built this to learn and experiment. I'd love your feedback, suggestions, and contributions!

*   **GitHub**: [@AniketDaiya](https://github.com/AniketDaiya) 🚀
*   **LinkedIn**: [in/aniket-daiya-1473b93a3](https://www.linkedin.com/in/aniket-daiya-1473b93a3/) 💼

*If you like this project, drop a ⭐️ on the repo!*
