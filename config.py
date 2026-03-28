"""
OLX Scraper — Configuration
"""

import json
import os
from dataclasses import dataclass

# ============================================================================
# CONFIGURATION CONSTANTS
# ============================================================================

@dataclass(frozen=True)
class Config:
    """Application configuration constants."""
    # Database
    DB_PATH: str = "data/olx.db"
    DATA_DIR: str = "data"

    # Timing (seconds)
    CHECK_INTERVAL: int = 300       # 5 minutes — unified scrape cycle
    MONITOR_INTERVAL: int = 1200    # 20 minutes — sold detection
    RATE_LIMIT_DELAY: int = 5
    PAGE_SCRAPE_DELAY: int = 3

    # Scraping
    OLX_BASE_URL: str = "https://www.olx.pl"
    REQUEST_TIMEOUT: int = 15
    MAX_SEEN_LISTINGS: int = 100
    MAX_SCRAPE_PAGES: int = 5
    MAX_INITIAL_FILTER: int = 100

    # LLM
    MIN_PRICES_FOR_CONTEXT: int = 3
    BATCH_SIZE: int = 20
    DETAIL_WORKERS: int = 8

    # Cheap mode
    MAX_SENT_MESSAGES: int = 200


CONFIG = Config()

# ============================================================================
# HTTP HEADERS
# ============================================================================

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
LLM_PROXY_URL = os.getenv("LLM_PROXY_URL", "http://host.docker.internal:3000/ask")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

# ============================================================================
# CONVERSATION STATES
# ============================================================================

(
    ASK_MODE,
    ASK_NAME,
    ASK_URL,
    ASK_SLOPSEARCH_QUERY,
    CONFIRM_SLOPSEARCH_QUERY,
    MODIFY_SLOPSEARCH_QUERY,
    ASK_CHEAP_QUERY,
    CONFIRM_CHEAP_QUERY,
    MODIFY_CHEAP_QUERY,
    EDIT_AWAIT_CHANGES,
) = range(10)

# ============================================================================
# CACHED REFERENCE DATA (loaded once at import time)
# ============================================================================

def _load_categories() -> list:
    """Load valid OLX base paths from olx_categories.json (once)."""
    try:
        with open("olx_categories.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return ["oferty"]


def _load_url_context() -> str:
    """Load OLX URL structure reference for LLM prompts (once)."""
    try:
        with open("olx_url_structure.md", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


OLX_CATEGORIES: list = _load_categories()
OLX_URL_CONTEXT: str = _load_url_context()
