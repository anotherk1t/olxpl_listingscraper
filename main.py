"""
OLX Poland Listing Scraper - Telegram Bot
Real-time marketplace monitoring with AI-powered price analysis.
"""

import logging
import os
import json
import time
import re
from dataclasses import dataclass
from typing import Optional

import requests
import google.generativeai as genai
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackContext, ConversationHandler, CallbackQueryHandler,
)

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class Config:
    """Application configuration constants."""
    # Paths
    DATA_DIR: str = "data"
    USER_SEARCHES_FILE: str = "data/user_searches.json"
    SEEN_LISTINGS_FILE: str = "data/seen_listings.json"
    MARKET_DATA_FILE: str = "data/market_data.json"
    
    # Timing (seconds)
    CHECK_INTERVAL: int = 300      # 5 minutes
    MONITOR_INTERVAL: int = 1200   # 20 minutes
    RATE_LIMIT_DELAY: int = 5
    
    # Scraping
    OLX_BASE_URL: str = "https://www.olx.pl"
    REQUEST_TIMEOUT: int = 15
    MAX_SEEN_LISTINGS: int = 100
    
    # AI
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"
    MIN_PRICES_FOR_CONTEXT: int = 3


CONFIG = Config()

# HTTP headers for web scraping
HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# Conversation states
ASK_NAME, ASK_URL = range(2)

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class TelegramLogHandler(logging.Handler):
    """Sends log messages to a Telegram chat."""
    
    def __init__(self, bot, chat_id: str):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id

    def emit(self, record):
        try:
            self.bot.send_message(
                chat_id=self.chat_id,
                text=f"<pre>{self.format(record)}</pre>",
                parse_mode='HTML'
            )
        except Exception as e:
            print(f"Failed to send log to Telegram: {e}")


# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Gemini AI enabled")
else:
    logger.warning("Gemini AI disabled (GEMINI_API_KEY not set)")


# ============================================================================
# DATA PERSISTENCE
# ============================================================================

def load_json(file_path: str) -> dict:
    """Load JSON data from file, return empty dict if not found."""
    os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(data: dict, file_path: str) -> None:
    """Save data to JSON file."""
    os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def parse_price(price_str: str) -> float:
    """Extract numeric price from string (e.g., '1 500 zł' -> 1500.0)."""
    if not price_str:
        return 0.0
    try:
        cleaned = re.sub(r'[^\d,.]', '', price_str).replace(',', '.')
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def make_search_key(chat_id: str, search_name: str) -> str:
    """Create unique key for a user's search."""
    return f"{chat_id}_{search_name}"


def get_timestamp() -> int:
    """Get current Unix timestamp."""
    return int(time.time())


# ============================================================================
# WEB SCRAPING
# ============================================================================

def _parse_json_ld(soup: BeautifulSoup) -> list[dict]:
    """Parse listings from JSON-LD structured data."""
    script = soup.find('script', {'type': 'application/ld+json'})
    if not script or not script.string:
        return []
    
    try:
        data = json.loads(script.string)
        offers = data.get("offers", {}).get("offers", [])
        
        listings = []
        for offer in offers:
            url = offer.get("url")
            if not url:
                continue
            
            # Extract ID from URL
            match = re.search(r'-ID([a-zA-Z0-9]+)\.html', url)
            listing_id = match.group(1) if match else url.split('/')[-1]
            
            listings.append({
                'id': listing_id,
                'title': re.sub(r'\s+', ' ', offer.get("name", "")).strip(),
                'price': f"{offer.get('price', 0)} {offer.get('priceCurrency', 'PLN')}",
                'url': url
            })
        
        return listings
    except (json.JSONDecodeError, AttributeError):
        return []


def _parse_html_cards(soup: BeautifulSoup) -> list[dict]:
    """Parse listings from HTML cards (fallback method)."""
    cards = soup.find_all('div', {'data-cy': 'l-card'})
    listings = []
    
    for card in cards:
        try:
            listing_id = card.get('id')
            if not listing_id:
                continue
            
            title_el = card.find('h4')
            price_el = card.find('p', {'data-testid': 'ad-price'})
            link_el = card.find('a', href=True)
            
            if not link_el:
                continue
            
            href = link_el['href']
            url = href if href.startswith('http') else f"{CONFIG.OLX_BASE_URL}{href}"
            
            listings.append({
                'id': listing_id,
                'title': title_el.text.strip() if title_el else "No Title",
                'price': price_el.text.strip() if price_el else "No Price",
                'url': url
            })
        except Exception as e:
            logger.debug(f"Error parsing card: {e}")
    
    return listings


def scrape_olx(url: str) -> list[dict]:
    """Scrape OLX search page for listings."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=CONFIG.REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return []
    
    soup = BeautifulSoup(response.text, 'lxml')
    
    # Try JSON-LD first, fall back to HTML parsing
    listings = _parse_json_ld(soup)
    if listings:
        logger.debug(f"Found {len(listings)} listings via JSON-LD")
        return listings
    
    listings = _parse_html_cards(soup)
    logger.debug(f"Found {len(listings)} listings via HTML fallback")
    return listings


# ============================================================================
# AI PRICE ANALYSIS
# ============================================================================

PRICE_ANALYSIS_PROMPT = """Analyze this OLX.pl marketplace listing:

**Listing:** {title}
**Price:** {price} PLN
**Category:** {category}
{market_context}

Rate the price as HIGH (overpriced), FAIR (market value), or LOW (good deal).
Consider: condition hints in title, brand value, and typical Polish market prices.

Respond ONLY with this JSON:
{{"assessment": "HIGH|FAIR|LOW", "confidence": "HIGH|MEDIUM|LOW", "reason": "One sentence explaining why."}}"""


def _build_market_context(market_data: dict, search_name: str) -> str:
    """Build market context string from historical data."""
    if not market_data:
        return ""
    
    prices = [d['initial_price'] for d in market_data.values() if d.get('initial_price', 0) > 0]
    
    if len(prices) < CONFIG.MIN_PRICES_FOR_CONTEXT:
        return ""
    
    return f"""
**Market Data ({len(prices)} previous listings):**
- Average: {sum(prices) / len(prices):.0f} PLN
- Range: {min(prices):.0f} - {max(prices):.0f} PLN"""


def analyze_price(listing: dict, search_name: str, market_data: dict) -> Optional[dict]:
    """Analyze listing price using Gemini AI."""
    if not GEMINI_API_KEY:
        return None
    
    try:
        prompt = PRICE_ANALYSIS_PROMPT.format(
            title=listing['title'],
            price=parse_price(listing['price']),
            category=search_name,
            market_context=_build_market_context(market_data, search_name)
        )
        
        model = genai.GenerativeModel(CONFIG.GEMINI_MODEL)
        response = model.generate_content(prompt)
        
        # Extract JSON from response
        text = response.text.strip()
        json_match = re.search(r'\{[^}]+\}', text)
        if not json_match:
            return None
        
        result = json.loads(json_match.group())
        
        # Validate required fields
        if all(k in result for k in ('assessment', 'confidence', 'reason')):
            return result
        return None
        
    except Exception as e:
        logger.warning(f"AI analysis failed: {e}")
        return None


def format_analysis(analysis: Optional[dict]) -> str:
    """Format AI analysis for Telegram message."""
    if not analysis:
        return ""
    
    emoji = {'HIGH': '🔴', 'FAIR': '🟡', 'LOW': '🟢'}.get(analysis['assessment'], '⚪')
    confidence = analysis.get('confidence', 'MEDIUM').lower()
    
    return f"\n\n{emoji} *AI Analysis* ({confidence}):\n_{analysis['reason']}_"


# ============================================================================
# BACKGROUND JOBS
# ============================================================================

def check_new_listings(context: CallbackContext) -> None:
    """Periodic job: Check for new listings and notify users."""
    logger.info("Checking for new listings...")
    
    user_searches = load_json(CONFIG.USER_SEARCHES_FILE)
    seen_listings = load_json(CONFIG.SEEN_LISTINGS_FILE)
    market_data = load_json(CONFIG.MARKET_DATA_FILE)
    
    data_changed = False
    
    for chat_id_str, searches in user_searches.items():
        chat_id = int(chat_id_str)
        
        for name, url in searches.items():
            key = make_search_key(chat_id_str, name)
            seen_listings.setdefault(key, [])
            market_data.setdefault(key, {})
            
            try:
                listings = scrape_olx(url)
                if not listings:
                    continue
                
                # Process new listings (oldest first)
                for listing in reversed(listings):
                    if listing['id'] in seen_listings[key]:
                        continue
                    
                    # AI analysis
                    analysis = analyze_price(listing, name, market_data[key])
                    
                    # Send notification
                    message = (
                        f"✨ *New: {name}*\n\n"
                        f"*{listing['title']}*\n\n"
                        f"💰 {listing['price']}\n"
                        f"🔗 [View]({listing['url']})"
                        f"{format_analysis(analysis)}"
                    )
                    
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode='Markdown',
                        disable_web_page_preview=False
                    )
                    
                    # Update tracking data
                    seen_listings[key].append(listing['id'])
                    ts = get_timestamp()
                    market_data[key][listing['id']] = {
                        'title': listing['title'],
                        'initial_price': parse_price(listing['price']),
                        'url': listing['url'],
                        'status': 'active',
                        'first_seen': ts,
                        'last_seen': ts,
                        'analysis': analysis
                    }
                    data_changed = True
                    
                    time.sleep(CONFIG.RATE_LIMIT_DELAY)
                
                # Prune old entries
                seen_listings[key] = seen_listings[key][-CONFIG.MAX_SEEN_LISTINGS:]
                
            except Exception as e:
                logger.error(f"Error processing '{name}': {e}")
    
    if data_changed:
        save_json(seen_listings, CONFIG.SEEN_LISTINGS_FILE)
        save_json(market_data, CONFIG.MARKET_DATA_FILE)


def monitor_listings(context: CallbackContext) -> None:
    """Periodic job: Update status of tracked listings."""
    logger.info("Monitoring listing statuses...")
    
    user_searches = load_json(CONFIG.USER_SEARCHES_FILE)
    market_data = load_json(CONFIG.MARKET_DATA_FILE)
    
    data_changed = False
    
    for chat_id_str, searches in user_searches.items():
        for name, url in searches.items():
            key = make_search_key(chat_id_str, name)
            
            if key not in market_data:
                continue
            
            try:
                live_ids = {item['id'] for item in scrape_olx(url)}
                if not live_ids:
                    continue
                
                ts = get_timestamp()
                for listing_id, data in market_data[key].items():
                    if data['status'] != 'active':
                        continue
                    
                    if listing_id in live_ids:
                        data['last_seen'] = ts
                    else:
                        data['status'] = 'sold'
                        data['removed_at'] = ts
                        logger.info(f"Listing sold: {data['title'][:40]}")
                    
                    data_changed = True
                    
            except Exception as e:
                logger.error(f"Error monitoring '{name}': {e}")
    
    if data_changed:
        save_json(market_data, CONFIG.MARKET_DATA_FILE)


# ============================================================================
# BOT COMMAND HANDLERS
# ============================================================================

def cmd_start(update: Update, context: CallbackContext) -> None:
    """Handle /start and /help commands."""
    update.message.reply_html(
        f"👋 Hi {update.effective_user.mention_html()}!\n\n"
        "I monitor OLX.pl and notify you about new listings.\n\n"
        "<b>Commands:</b>\n"
        "/add - Add a search to monitor\n"
        "/list - View your searches\n"
        "/delete - Remove a search\n"
        "/help - Show this message"
    )


def cmd_add_start(update: Update, context: CallbackContext) -> int:
    """Start the add search conversation."""
    update.message.reply_text(
        "📝 Let's add a new search.\n\n"
        "First, give it a short name (e.g., 'vintage camera'):"
    )
    return ASK_NAME


def cmd_add_name(update: Update, context: CallbackContext) -> int:
    """Handle search name input."""
    name = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    
    searches = load_json(CONFIG.USER_SEARCHES_FILE)
    if chat_id in searches and name in searches[chat_id]:
        update.message.reply_text("⚠️ Name already exists. Choose another or /cancel:")
        return ASK_NAME
    
    context.user_data['search_name'] = name
    update.message.reply_text(f"👍 Now send me the OLX.pl search URL for '{name}':")
    return ASK_URL


def cmd_add_url(update: Update, context: CallbackContext) -> int:
    """Handle search URL input and save."""
    url = update.message.text.strip()
    
    if "olx.pl" not in url:
        update.message.reply_text("⚠️ Invalid URL. Must be from olx.pl. Try again or /cancel:")
        return ASK_URL
    
    chat_id = str(update.message.chat_id)
    name = context.user_data.pop('search_name')
    
    searches = load_json(CONFIG.USER_SEARCHES_FILE)
    searches.setdefault(chat_id, {})[name] = url
    save_json(searches, CONFIG.USER_SEARCHES_FILE)
    
    update.message.reply_text(f"✅ Now monitoring '{name}'!\nI'll check every {CONFIG.CHECK_INTERVAL // 60} minutes.")
    return ConversationHandler.END


def cmd_cancel(update: Update, context: CallbackContext) -> int:
    """Cancel current conversation."""
    context.user_data.pop('search_name', None)
    update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


def cmd_list(update: Update, context: CallbackContext) -> None:
    """List user's active searches."""
    chat_id = str(update.message.chat_id)
    searches = load_json(CONFIG.USER_SEARCHES_FILE).get(chat_id, {})
    
    if not searches:
        update.message.reply_text("No active searches. Use /add to create one.")
        return
    
    message = "📋 *Your searches:*\n\n" + "\n".join(f"• {name}" for name in searches)
    update.message.reply_text(message, parse_mode='Markdown')


def cmd_delete_start(update: Update, context: CallbackContext) -> None:
    """Show delete search buttons."""
    chat_id = str(update.message.chat_id)
    searches = load_json(CONFIG.USER_SEARCHES_FILE).get(chat_id, {})
    
    if not searches:
        update.message.reply_text("No searches to delete.")
        return
    
    keyboard = [[InlineKeyboardButton(f"❌ {name}", callback_data=f"del_{name}")] 
                for name in searches]
    update.message.reply_text("Select a search to delete:", reply_markup=InlineKeyboardMarkup(keyboard))


def callback_delete(update: Update, context: CallbackContext) -> None:
    """Handle delete button press."""
    query = update.callback_query
    query.answer()
    
    name = query.data[4:]  # Remove 'del_' prefix
    chat_id = str(query.message.chat_id)
    key = make_search_key(chat_id, name)
    
    # Remove from all data files
    for file_path in (CONFIG.USER_SEARCHES_FILE, CONFIG.SEEN_LISTINGS_FILE, CONFIG.MARKET_DATA_FILE):
        data = load_json(file_path)
        
        if file_path == CONFIG.USER_SEARCHES_FILE:
            if chat_id in data:
                data[chat_id].pop(name, None)
        else:
            data.pop(key, None)
        
        save_json(data, file_path)
    
    query.edit_message_text(f"✅ Deleted '{name}'")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Initialize and run the bot."""
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Set up Telegram logging for admins
    if ADMIN_CHAT_ID:
        handler = TelegramLogHandler(updater.bot, ADMIN_CHAT_ID)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logging.getLogger().addHandler(handler)
    
    # Conversation handler for adding searches
    add_conv = ConversationHandler(
        entry_points=[CommandHandler('add', cmd_add_start)],
        states={
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, cmd_add_name)],
            ASK_URL: [MessageHandler(Filters.text & ~Filters.command, cmd_add_url)],
        },
        fallbacks=[CommandHandler('cancel', cmd_cancel)],
    )
    
    # Register handlers
    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_start))
    dp.add_handler(add_conv)
    dp.add_handler(CommandHandler("list", cmd_list))
    dp.add_handler(CommandHandler("delete", cmd_delete_start))
    dp.add_handler(CallbackQueryHandler(callback_delete, pattern='^del_'))
    
    # Schedule background jobs
    jq = updater.job_queue
    jq.run_repeating(check_new_listings, interval=CONFIG.CHECK_INTERVAL, first=10)
    jq.run_repeating(monitor_listings, interval=CONFIG.MONITOR_INTERVAL, first=60)
    
    # Start bot
    updater.start_polling()
    logger.info("Bot started")
    updater.idle()


if __name__ == '__main__':
    main()