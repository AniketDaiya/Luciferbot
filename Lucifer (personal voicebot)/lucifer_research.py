# =============================================================================
# LUCIFER AUTONOMOUS RESEARCH ENGINE
# =============================================================================
# Requirements (as specified):
#   - ScrapingDog API for SERP and deep scraping
#   - 5-step pipeline:
#     1. Query formulation (Claude API generates 3 variants)
#     2. Google SERP scraping (ScrapingDog + BeautifulSoup)
#     3. Deep scrape top 3 results (ScrapingDog + newspaper3k/readability)
#     4. Synthesis with Claude API
#     5. Speak and display output
#   - Rate limiting (max 10 calls/minute)
#   - Caching (SQLite, 1 hour TTL)
#   - Error handling for blocked pages
#   - Integration with lucifer_intent.py
# =============================================================================

import os
import re
import json
import time
import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from html import unescape

import requests
from bs4 import BeautifulSoup

# Content extraction
try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False

try:
    from readability import Document
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False

# TTS
import pyttsx3

# Terminal formatting
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table


# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
# API Configuration
SCRAPINGDOG_API_KEY = os.environ.get('SCRAPINGDOG_API_KEY', '')
SCRAPINGDOG_BASE_URL = "https://api.scrapingdog.com/scrape"

# Gemini configuration
GEMINI_MODEL = "gemini-2.0-flash"

# Rate limiting
MAX_CALLS_PER_MINUTE = 10
CALL_INTERVAL = 60 / MAX_CALLS_PER_MINUTE  # 6 seconds between calls

# Caching
CACHE_DB = "lucifer_research_cache.db"
CACHE_TTL_HOURS = 1

# Content limits
MAX_SERP_RESULTS = 5
MAX_DEEP_SCRAPE_URLS = 3
MAX_CONTENT_CHARS = 1500

# Log file
LOG_FILE = "lucifer_session.log"


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging() -> logging.Logger:
    """Initialize logging for research module."""
    logger = logging.getLogger("LuciferResearch")

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
# RATE LIMITER
# =============================================================================
class RateLimiter:
    """
    Thread-safe rate limiter for API calls.
    Limits to MAX_CALLS_PER_MINUTE.
    """

    def __init__(self, calls_per_minute: int = MAX_CALLS_PER_MINUTE):
        self.calls_per_minute = calls_per_minute
        self.interval = 60.0 / calls_per_minute
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        """Wait if necessary to respect rate limit."""
        with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_call = time.time()


# Global rate limiter instance
_rate_limiter = RateLimiter()


# =============================================================================
# CACHE MANAGER (SQLite)
# =============================================================================
class CacheManager:
    """
    SQLite-based cache for research results.
    Stores results for CACHE_TTL_HOURS to avoid repeat scrapes.
    """

    def __init__(self, db_path: str = CACHE_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database and table."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_cache (
                    cache_key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON research_cache(created_at)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Cache init failed: {e}")

    def _get_key(self, query: str, query_type: str) -> str:
        """Generate cache key from query and type."""
        raw = f"{query_type}:{query.lower().strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, query_type: str) -> Optional[str]:
        """Retrieve cached data if not expired."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            key = self._get_key(query, query_type)
            cursor.execute("""
                SELECT data, created_at FROM research_cache
                WHERE cache_key = ?
            """, (key,))

            row = cursor.fetchone()
            conn.close()

            if row:
                data, created_at = row
                created = datetime.fromisoformat(created_at)
                if datetime.now() - created < timedelta(hours=CACHE_TTL_HOURS):
                    logger.info(f"Cache hit: {query_type} - {query[:30]}...")
                    return data

            return None

        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            return None

    def set(self, query: str, query_type: str, data: str) -> None:
        """Store data in cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            key = self._get_key(query, query_type)
            conn.execute("""
                INSERT OR REPLACE INTO research_cache (cache_key, data, created_at)
                VALUES (?, ?, ?)
            """, (key, data, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            logger.debug(f"Cache set: {query_type} - {query[:30]}...")
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")

    def cleanup(self) -> None:
        """Remove expired cache entries."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                DELETE FROM research_cache
                WHERE created_at < datetime('now', '-' || ? || ' hours')
            """, (CACHE_TTL_HOURS,))
            conn.commit()
            conn.close()
            logger.info("Cache cleanup completed")
        except Exception as e:
            logger.warning(f"Cache cleanup failed: {e}")


# Global cache instance
_cache = CacheManager()


# =============================================================================
# DATA STRUCTURES
# =============================================================================
@dataclass
class SearchResult:
    """Single search result from SERP."""
    title: str
    url: str
    snippet: str
    query_variant: str = ""


@dataclass
class ResearchResult:
    """Complete research result from the pipeline."""
    original_query: str
    query_variants: List[str]
    search_results: List[SearchResult]
    scraped_content: List[Dict[str, str]]
    synthesis: str
    timestamp: datetime = field(default_factory=datetime.now)


# =============================================================================
# SCRAPINGDOG CLIENT
# =============================================================================
class ScrapingDogClient:
    """
    Client for ScrapingDog API.
    Handles SERP scraping and deep page fetching.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or SCRAPINGDOG_API_KEY
        self.base_url = SCRAPINGDOG_BASE_URL

        if not self.api_key:
            logger.warning("ScrapingDog API key not configured")
            logger.info("Set SCRAPINGDOG_API_KEY environment variable")

    def scrape_google_serp(self, query: str) -> List[SearchResult]:
        """
        Scrape Google SERP for a query.

        Args:
            query: Search query

        Returns:
            List of SearchResult objects (top 5)
        """
        # Check cache
        cached = _cache.get(query, "serp")
        if cached:
            try:
                results = json.loads(cached)
                if results:  # Only use cached results if they are not empty!
                    return [SearchResult(**r) for r in results]
            except:
                pass

        if not self.api_key:
            logger.info("ScrapingDog key missing - trying Mojeek fallback...")
            results = self._scrape_serp_via_mojeek(query)
            if not results:
                logger.info("Mojeek search empty - trying DuckDuckGo HTML fallback...")
                results = self._scrape_serp_via_ddg_html(query)
            if not results:
                logger.info("DuckDuckGo HTML search empty - falling back to local Chrome Google Search...")
                results = self._scrape_google_serp_via_chrome(query)
            if results:
                _cache.set(query, "serp", json.dumps([vars(r) for r in results]))
            return results

        _rate_limiter.wait()

        url = f"{self.base_url}?api_key={self.api_key}&url=https://www.google.com/search?q={requests.utils.quote(query)}&num=10"

        try:
            logger.info(f"Scrape SERP: {query[:40]}...")
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            results = self._parse_serp(response.text, query)

            if not results:
                logger.warning("ScrapingDog returned 0 results (possible block or CAPTCHA). Trying Mojeek fallback...")
                results = self._scrape_serp_via_mojeek(query)
                if not results:
                    logger.info("Mojeek search empty - trying DuckDuckGo HTML fallback...")
                    results = self._scrape_serp_via_ddg_html(query)
                if not results:
                    logger.info("DuckDuckGo HTML search empty - falling back to local Chrome Google Search...")
                    results = self._scrape_google_serp_via_chrome(query)

            # Cache results ONLY if they are not empty!
            if results:
                _cache.set(query, "serp", json.dumps([vars(r) for r in results]))

            return results

        except requests.RequestException as e:
            logger.error(f"SERP scrape failed: {e}. Trying Mojeek fallback...")
            results = self._scrape_serp_via_mojeek(query)
            if not results:
                logger.info("Mojeek search empty - trying DuckDuckGo HTML fallback...")
                results = self._scrape_serp_via_ddg_html(query)
            if not results:
                logger.info("DuckDuckGo HTML search empty - falling back to local Chrome Google Search...")
                results = self._scrape_google_serp_via_chrome(query)
            if results:
                _cache.set(query, "serp", json.dumps([vars(r) for r in results]))
            return results

    def _scrape_serp_via_mojeek(self, query: str) -> List[SearchResult]:
        """Scrape Mojeek search engine as a fast, free, block-proof fallback."""
        from bs4 import BeautifulSoup
        import urllib.parse
        results = []
        try:
            logger.info(f"Scraping Mojeek for: {query[:40]}...")
            url = f"https://www.mojeek.com/search?q={urllib.parse.quote(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9"
            }
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                logger.warning(f"Mojeek returned status code {response.status_code}")
                return []
                
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Mojeek results are in a list with class 'results'
            # Each result item has tag 'li'
            containers = soup.select('li')
            for container in containers:
                try:
                    title_elem = container.select_one('h2 a.title')
                    if not title_elem:
                        continue
                    title = title_elem.text.strip()
                    url = title_elem.get('href', '')
                    
                    snippet_elem = container.select_one('p.s')
                    snippet = snippet_elem.text.strip() if snippet_elem else ""
                    
                    if title and url.startswith('http'):
                        results.append(SearchResult(
                            title=title[:200],
                            url=url[:500],
                            snippet=snippet[:500],
                            query_variant=query
                        ))
                except Exception as e:
                    logger.debug(f"Mojeek parsing row error: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Mojeek scraping failed: {e}")
            
        return results[:5]

    def _scrape_serp_via_ddg_html(self, query: str) -> List[SearchResult]:
        """Scrape DuckDuckGo HTML search page as a fast, free, block-proof fallback."""
        from bs4 import BeautifulSoup
        import urllib.parse
        results = []
        try:
            logger.info(f"Scraping DuckDuckGo HTML for: {query[:40]}...")
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9"
            }
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                logger.warning(f"DuckDuckGo HTML returned status code {response.status_code}")
                return []
                
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # DuckDuckGo HTML result items are divs with class 'result' or 'web-result'
            containers = soup.select('.result')
            for container in containers[:5]:
                try:
                    title_elem = container.select_one('.result__title a')
                    if not title_elem:
                        continue
                    title = title_elem.text.strip()
                    
                    url = title_elem.get('href', '')
                    # Clean DDG redirect URL if present
                    if "uddg=" in url:
                        parsed = urllib.parse.urlparse(url)
                        queries = urllib.parse.parse_qs(parsed.query)
                        if "uddg" in queries:
                            url = queries["uddg"][0]
                            
                    snippet_elem = container.select_one('.result__snippet')
                    snippet = snippet_elem.text.strip() if snippet_elem else ""
                    
                    if title and url.startswith('http'):
                        results.append(SearchResult(
                            title=title[:200],
                            url=url[:500],
                            snippet=snippet[:500],
                            query_variant=query
                        ))
                except Exception as e:
                    logger.debug(f"DDG parsing row error: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"DuckDuckGo HTML scraping failed: {e}")
            
        return results

    def _scrape_google_serp_via_chrome(self, query: str) -> List[SearchResult]:
        """Scrape Google Search SERP results using Undetected ChromeDriver in the background."""
        from bs4 import BeautifulSoup
        import urllib.parse
        import time
        
        results = []
        try:
            from lucifer_browser import BrowserDriver
            logger.info(f"Scraping Google Search via local Chrome for: {query[:40]}...")
            driver = BrowserDriver.get_driver()
            
            # Navigate to Google Search
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            driver.get(search_url)
            
            # Give it a brief moment to load/render
            time.sleep(2)
            
            # Extract page source
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            
            # Identify search result containers (Google uses 'div.g' mostly)
            containers = soup.select('div.g')
            if not containers:
                # Try alternative selectors
                containers = soup.select('div[data-hveid]')
                
            for container in containers[:5]:
                try:
                    # Title element
                    title_elem = container.select_one('h3')
                    if not title_elem:
                        continue
                    title = title_elem.text.strip()
                    
                    # Link element
                    link_elem = container.find('a', href=True)
                    if not link_elem:
                        continue
                    url = link_elem.get('href', '')
                    
                    # Snippet element
                    snippet_elem = container.select_one('div[style*="webkit-line-clamp"]') or container.select_one('.VwiC3b') or container.select_one('.aGGsV')
                    snippet = snippet_elem.text.strip() if snippet_elem else ""
                    
                    if title and url.startswith('http'):
                        results.append(SearchResult(
                            title=title[:200],
                            url=url[:500],
                            snippet=snippet[:500],
                            query_variant=query
                        ))
                except Exception as e:
                    logger.debug(f"Chrome SERP item parse error: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Chrome SERP scraping failed: {e}")
            
        return results

    def _parse_serp(self, html: str, query: str) -> List[SearchResult]:
        """Parse SERP HTML to extract results."""
        results = []

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Find search result containers
            # Google uses various selectors - try common ones
            selectors = [
                'div.g',
                'div[data-hveid]',
                '.Gx5Zad',
                'div.vJ7R6f',
            ]

            for selector in selectors:
                containers = soup.select(selector)
                if containers:
                    break

            if not containers:
                # Fallback: search for links
                containers = soup.find_all('div', class_=lambda x: x and 'ZINbbc' in str(x))

            for container in containers[:MAX_SERP_RESULTS]:
                try:
                    # Extract title
                    title_elem = container.select_one('h3') or container.select_one('.DKV0Md')
                    title = title_elem.text.strip() if title_elem else ""

                    # Extract URL
                    link_elem = container.find('a', href=True)
                    url = ""
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href.startswith('/url?'):
                            # Extract actual URL from Google redirect
                            for param in href.split('&'):
                                if param.startswith('q='):
                                    url = param[2:]
                                    break
                        elif href.startswith('http'):
                            url = href

                    # Extract snippet
                    snippet_elem = container.select_one('.aGGsV') or container.select_one('.VwiC3b')
                    snippet = snippet_elem.text.strip() if snippet_elem else ""

                    if title and url:
                        results.append(SearchResult(
                            title=title[:200],
                            url=url[:500],
                            snippet=snippet[:500],
                            query_variant=query
                        ))

                except Exception as e:
                    logger.debug(f"Parse error for result: {e}")
                    continue

        except Exception as e:
            logger.error(f"SERP parsing failed: {e}")

        return results

    def deep_scrape(self, url: str) -> str:
        """
        Deep scrape a URL to extract main content.

        Args:
            url: Target URL

        Returns:
            Extracted content (max 1500 chars)
        """
        # Check cache
        cached = _cache.get(url, "deep")
        if cached:
            return cached[:MAX_CONTENT_CHARS]

        # 1. Try fast HTTP request first (takes under 0.2 seconds!)
        fast_content = self._deep_scrape_via_fast_request(url)
        if fast_content:
            _cache.set(url, "deep", fast_content)
            return fast_content[:MAX_CONTENT_CHARS]

        # 2. Fallback if ScrapingDog key is missing
        if not self.api_key:
            logger.info("ScrapingDog key missing - falling back to local Chrome deep scrape...")
            content = self._deep_scrape_via_chrome(url)
            if content:
                _cache.set(url, "deep", content)
            return content[:MAX_CONTENT_CHARS]

        _rate_limiter.wait()

        scrape_url = f"{self.base_url}?api_key={self.api_key}&url={url}"

        try:
            logger.info(f"Deep scraping: {url[:50]}...")
            response = requests.get(scrape_url, timeout=30)
            response.raise_for_status()

            content = self._extract_content(response.text)

            # Cache
            _cache.set(url, "deep", content)

            return content[:MAX_CONTENT_CHARS]

        except requests.RequestException as e:
            logger.error(f"Deep scrape failed for {url}: {e}. Falling back to local Chrome...")
            content = self._deep_scrape_via_chrome(url)
            if content:
                _cache.set(url, "deep", content)
            return content[:MAX_CONTENT_CHARS]

    def _deep_scrape_via_fast_request(self, url: str) -> Optional[str]:
        """Try a fast, non-blocking HTTP request to extract text without launching Chrome."""
        try:
            logger.info(f"Trying fast HTTP deep scrape: {url[:50]}...")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                content = self._extract_content(response.text)
                if content and len(content.strip()) > 100 and "captcha" not in content.lower():
                    logger.info(f"Fast HTTP deep scrape succeeded for: {url[:50]}")
                    return content
        except Exception as e:
            logger.debug(f"Fast HTTP deep scrape failed for {url}: {e}")
        return None

    def _deep_scrape_via_chrome(self, url: str) -> str:
        """Deep scrape a URL's text content using the local Chrome driver."""
        import time
        try:
            from lucifer_browser import BrowserDriver
            logger.info(f"Deep scraping via local Chrome: {url[:50]}...")
            driver = BrowserDriver.get_driver()
            driver.get(url)
            time.sleep(2)  # Wait for page/JS load
            
            html = driver.page_source
            return self._extract_content(html)
        except Exception as e:
            logger.error(f"Chrome deep scrape failed for {url}: {e}")
            return f"[Chrome content extraction failed: {e}]"

    def _extract_content(self, html: str) -> str:
        """Extract main content from HTML using newspaper3k or readability."""
        text = ""

        # Try newspaper3k first
        if NEWSPAPER_AVAILABLE:
            try:
                article = Article(url="")
                article.set_html(html)
                article.parse()
                text = article.text
            except Exception as e:
                logger.debug(f"Newspaper3k extraction failed: {e}")

        # Fallback to readability
        if not text and READABILITY_AVAILABLE:
            try:
                doc = Document(html)
                text = doc.summary()
            except Exception as e:
                logger.debug(f"Readability extraction failed: {e}")

        # Fallback to BeautifulSoup
        if not text:
            try:
                soup = BeautifulSoup(html, 'html.parser')
                # Remove scripts and styles
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                # Get text
                text = soup.get_text(separator='\n', strip=True)
            except Exception as e:
                logger.debug(f"BeautifulSoup extraction failed: {e}")

        # Clean up
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)

        return text.strip()


# =============================================================================
# GEMINI CLIENT
# =============================================================================
class GeminiClient:
    """
    Client for Google Gemini API - used for query formulation and synthesis.
    """

    def __init__(self):
        self.client = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Gemini client."""
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')

        if not api_key:
            logger.warning("No Gemini API key found")
            return

        try:
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel('gemini-2.0-flash')
            logger.info("Gemini client initialized")
        except Exception as e:
            logger.error(f"Gemini init failed: {e}")

    def generate_query_variants(self, query: str) -> List[str]:
        """
        Use Gemini to generate 3 optimized search query variants.

        Args:
            query: Original user query

        Returns:
            List of 3 query variants
        """
        if not self.client:
            # Fallback: return modified versions
            return [
                query,
                f"{query} 2024",
                f"latest {query}"
            ]

        system_prompt = """You are a search query optimizer. Given a user query, generate exactly 3 optimized search query variants that would return better results. Return ONLY a JSON array of strings, nothing else. Examples: "AI breakthroughs" -> ["AI research breakthroughs 2024", "new artificial intelligence discoveries", "frontier AI developments this week"]"""

        user_prompt = f'Generate 3 search query variants for: "{query}"'

        try:
            response = self.client.generate_content(
                system_prompt + "\n\n" + user_prompt,
                generation_config={'max_output_tokens': 100, 'temperature': 0.1}
            )

            # Parse JSON from response
            text = response.text.strip()
            variants = json.loads(text)

            if isinstance(variants, list) and len(variants) >= 3:
                return variants[:3]

        except Exception as e:
            logger.error(f"Query variant generation failed: {e}")

        # Fallback
        return [query, f"{query} 2024", f"latest {query}"]

    def synthesize(self, contents: List[Dict[str, str]], original_query: str, search_results: List[SearchResult] = None) -> str:
        """
        Synthesize scraped content into a brief using Gemini.

        Args:
            contents: List of dicts with 'title', 'url', 'text' keys
            original_query: Original search query
            search_results: Optional list of SearchResult objects

        Returns:
            Synthesized brief text
        """
        if not self.client:
            logger.warning("No Gemini client - using fallback synthesis")
            return self._fallback_synthesis(contents, original_query, search_results)

        # Build content for Gemini
        content_text = "\n\n".join([
            f"Source: {c.get('title', 'Unknown')}\nURL: {c.get('url', '')}\nContent: {c.get('text', '')[:1000]}"
            for c in contents if c.get('text') and not c.get('text').startswith('[Chrome content extraction') and not c.get('text').startswith('[Content extraction')
        ])
        
        # If no deep-scraped content, try to use search snippets for Gemini synthesis!
        if not content_text.strip() and search_results:
            content_text = "\n\n".join([
                f"Source: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}"
                for r in search_results
            ])

        system_prompt = """You are Lucifer, an AI research assistant. Synthesize the following scraped web content or snippets into a clear, concise brief that a busy executive could read in 90 seconds. Structure it as:

1. A 1-sentence summary of the topic
2. The 3 most important findings (numbered)
3. A 1-sentence takeaway

Be direct. No filler. No preamble like "Here's what I found." Start directly with the content."""

        user_prompt = f"Original query: {original_query}\n\nWeb content:\n{content_text}"

        try:
            response = self.client.generate_content(
                system_prompt + "\n\n" + user_prompt,
                generation_config={'max_output_tokens': 800, 'temperature': 0.1}
            )

            return response.text.strip()

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return self._fallback_synthesis(contents, original_query, search_results)

    def _fallback_synthesis(self, contents: List[Dict[str, str]], query: str, search_results: List[SearchResult] = None) -> str:
        """Fallback synthesis without Gemini."""
        # Filter out empty or error messages
        valid_contents = [c for c in contents if c.get('text') and not c.get('text').startswith('[Chrome content extraction') and not c.get('text').startswith('[Content extraction')]
        
        if valid_contents:
            summary = f"I compiled the top deep-scraped reports on {query}, Sir:\n\n"
            for i, c in enumerate(valid_contents[:3], 1):
                title = c.get('title', 'Source')[:80]
                text = c.get('text', '')[:180].strip()
                summary += f"  {i}. {title}: {text}...\n"
            summary += "\nThat summarizes the deep-scraped findings, Sir."
            return summary
            
        # Fallback to search results snippets if deep content is empty
        if search_results:
            summary = f"I retrieved the latest search headlines for {query}, Sir:\n\n"
            for i, r in enumerate(search_results[:3], 1):
                title = r.title[:80]
                snippet = r.snippet[:180].strip()
                if snippet:
                    summary += f"  {i}. {title}: {snippet}\n"
                else:
                    summary += f"  {i}. {title}\n"
            summary += "\nI can open the browser to show you the full details if you wish, Sir."
            return summary

        return f"I performed a search for {query}, Sir, but both Google and my scraping channels returned empty results."


# =============================================================================
# TTS ENGINE
# =============================================================================
from lucifer_tts import tts as _tts

class TTSEngine:
    """TTS for research results routing to the global singleton."""

    def __init__(self):
        pass

    def speak(self, text: str) -> None:
        # Truncate for TTS (avoid too long)
        text_truncated = text[:1000]
        _tts.speak(text_truncated)


# =============================================================================
# DISPLAY OUTPUT
# =============================================================================
_console = Console()


def display_results(result: ResearchResult) -> None:
    """
    Display research results with rich formatting.

    Args:
        result: ResearchResult object
    """
    print("\n")
    _console.print(Panel.fit(
        f"[bold cyan]Research Complete[/bold cyan]\nQuery: {result.original_query}",
        border_style="cyan"
    ))

    # Query variants
    table = Table(title="Query Variants Used", show_header=False)
    table.add_column(justify="left")
    for i, variant in enumerate(result.query_variants, 1):
        table.add_row(f"  {i}. {variant}")
    _console.print(table)

    # Search results
    table = Table(title="Top Search Results", show_header=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Title", style="cyan", width=40)
    table.add_column("URL", style="dim", width=35)

    for i, r in enumerate(result.search_results[:5], 1):
        table.add_row(str(i), r.title[:40], r.url[:35])
    _console.print(table)

    # Synthesis
    _console.print(Panel.fit(
        result.synthesis,
        title="[bold]Synthesis[/bold]",
        border_style="green"
    ))


# =============================================================================
# MAIN RESEARCH PIPELINE
# =============================================================================
class ResearchPipeline:
    """
    Full research pipeline orchestration.
    """

    def __init__(self):
        self.scrape_client = ScrapingDogClient()
        self.gemini_client = GeminiClient()
        self.tts = TTSEngine()

    def run(self, query: str) -> ResearchResult:
        """
        Execute the full research pipeline.

        Args:
            query: Raw user query

        Returns:
            ResearchResult with all data
        """
        logger.info(f"=== Starting research pipeline for: {query} ===")

        # Step 1: Query formulation
        logger.info("Step 1: Generating query variants...")
        query_variants = self._step1_query_formulation(query)

        # Step 2: SERP scraping
        logger.info("Step 2: Scraping SERP results...")
        search_results = self._step2_serp_scrape(query_variants)

        # Step 3: Deep scraping
        logger.info("Step 3: Deep scraping top results...")
        scraped_content = self._step3_deep_scrape(search_results)

        # Step 4: Synthesis
        logger.info("Step 4: Synthesizing results...")
        synthesis = self._step4_synthesis(scraped_content, query, search_results)

        # Step 5: Output
        logger.info("Step 5: Presenting results...")

        result = ResearchResult(
            original_query=query,
            query_variants=query_variants,
            search_results=search_results,
            scraped_content=scraped_content,
            synthesis=synthesis
        )

        return result

    def _step1_query_formulation(self, query: str) -> List[str]:
        """Step 1: Generate optimized query variants."""
        # Check cache first
        cached = _cache.get(query, "variants")
        if cached:
            return json.loads(cached)

        variants = self.gemini_client.generate_query_variants(query)
        _cache.set(query, "variants", json.dumps(variants))
        return variants

    def _step2_serp_scrape(self, query_variants: List[str]) -> List[SearchResult]:
        """Step 2: Scrape Google SERP for each variant."""
        all_results = []
        seen_urls = set()

        for variant in query_variants:
            results = self.scrape_client.scrape_google_serp(variant)

            for r in results:
                if r.url not in seen_urls:
                    all_results.append(r)
                    seen_urls.add(r.url)

            # Limit total results
            if len(all_results) >= MAX_SERP_RESULTS * len(query_variants):
                break

        # Deduplicate and limit
        return all_results[:MAX_SERP_RESULTS * 3]

    def _step3_deep_scrape(self, search_results: List[SearchResult]) -> List[Dict[str, str]]:
        """Step 3: Deep scrape top results."""
        scraped = []

        # Get top URLs - if keyless, only deep scrape the top 1 URL to stay super fast!
        limit = 1 if not self.scrape_client.api_key else MAX_DEEP_SCRAPE_URLS
        top_urls = search_results[:limit]

        for sr in top_urls:
            logger.info(f"Deep scraping: {sr.title[:40]}...")

            content = self.scrape_client.deep_scrape(sr.url)

            scraped.append({
                "title": sr.title,
                "url": sr.url,
                "text": content
            })

        return scraped

    def _step4_synthesis(self, contents: List[Dict[str, str]], query: str, search_results: List[SearchResult] = None) -> str:
        """Step 4: Synthesize content with Claude."""
        return self.gemini_client.synthesize(contents, query, search_results)

    def present(self, result: ResearchResult) -> None:
        """Step 5: Speak and display results."""
        display_results(result)
        self.tts.speak(result.synthesis)


# =============================================================================
# INTEGRATION FUNCTION
# =============================================================================
def research(query: str) -> ResearchResult:
    """
    Main entry point - integrate with lucifer_intent.py.

    Args:
        query: User's research query

    Returns:
        ResearchResult object
    """
    logger.info(f"Research requested: {query}")

    pipeline = ResearchPipeline()
    result = pipeline.run(query)
    pipeline.present(result)

    return result


def handle_search_with_research(query: str) -> None:
    """
    Wrapper for SEARCH intent to use research pipeline.
    Called from lucifer_intent.py
    """
    logger.info(f"[HANDLER] SEARCH (Research): '{query}'")
    research(query)


# =============================================================================
# TEST / DEMO
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Lucifer Research Engine - Test Mode")
    print("=" * 60)

    # Check configuration
    api_key = os.environ.get('SCRAPINGDOG_API_KEY')
    claude_key = os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('CLAUDE_API_KEY')

    print(f"\nConfiguration:")
    print(f"  ScrapingDog API: {'✓ Configured' if api_key else '✗ Not set'}")
    print(f"  Claude API: {'✓ Configured' if claude_key else '✗ Not set'}")

    print("\n" + "=" * 60)
    print("Usage: from lucifer_research import research")
    print("       result = research('your query here')")
    print("=" * 60)

    # Demo run with a simple query
    print("\n[Demo run - checking configuration...]")

    pipeline = ResearchPipeline()

    # Test query formulation
    test_query = "latest AI breakthroughs"
    print(f"\nTest: {test_query}")

    try:
        variants = pipeline._step1_query_formulation(test_query)
        print(f"Variants generated: {variants}")
    except Exception as e:
        print(f"Query formulation error: {e}")

    print("\nTest complete. Configure API keys to run full pipeline.")