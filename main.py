import logging
import os
import json
import time
import re
import requests
import google.generativeai as genai
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)

# --- Configuration ---
# Load the bot token from an environment variable for security
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN environment variable set!")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Gemini AI configured successfully")
else:
    logger.warning("GEMINI_API_KEY not set. Price analysis will be disabled.")


# --- Constants ---
# File paths for storing data
DATA_DIR = "data"
USER_SEARCHES_FILE = os.path.join(DATA_DIR, "user_searches.json")
SEEN_LISTINGS_FILE = os.path.join(DATA_DIR, "seen_listings.json")
MARKET_DATA_FILE = os.path.join(DATA_DIR, "market_data.json")

# How often to check for new listings (in seconds)
CHECK_INTERVAL = 300  # 5 minutes
MONITOR_INTERVAL = 1200 # 20 minutes

# Conversation states for adding a search
ASK_URL, ASK_NAME = range(2)



def _parse_price(price_str):
    """Removes currency symbols and text to return a clean float from a price string."""
    if not price_str:
        return 0.0
    try:
        # Remove all non-digit characters except for a comma or dot
        cleaned_str = re.sub(r'[^\d,.]', '', price_str)
        # Replace comma with a dot for float conversion
        cleaned_str = cleaned_str.replace(',', '.')
        return float(cleaned_str)
    except (ValueError, TypeError):
        return 0.0

class TelegramLogHandler(logging.Handler):
    """
    A custom logging handler that sends logs to a specific Telegram chat.
    """
    def __init__(self, bot, chat_id):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id

    def emit(self, record):
        """
        Formats and sends the log record.
        """
        # We use self.format() to get the formatted log message
        log_entry = self.format(record)
        try:
            # We send the log entry to the admin's chat ID
            self.bot.send_message(chat_id=self.chat_id, text=f"<pre>{log_entry}</pre>", parse_mode='HTML')
        except Exception as e:
            # If logging to Telegram fails, we fall back to printing the error
            # to the console to avoid a loop of logging errors.
            print(f"CRITICAL: Failed to send log to Telegram: {e}")
            print(log_entry)





# --- Data Handling ---
def load_data(file_path):
    """Loads data from a JSON file, creating it if it doesn't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data, file_path):
    """Saves data to a JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)

# --- Scraper Logic ---
def _parse_html_cards(soup):
    """
    Fallback function to parse listings directly from HTML cards if JSON-LD fails.
    This is less stable but necessary if the site structure changes.
    """
    listings = []
    # Find all div elements with the attribute data-cy="l-card"
    listing_cards = soup.find_all('div', {'data-cy': 'l-card'})
    logger.info(f"HTML Fallback: Found {len(listing_cards)} listing cards.")

    for card in listing_cards:
        try:
            listing_id = card.get('id')
            if not listing_id:
                continue # Skip cards without an ID

            # Find the title element, which is in an h4 tag
            title_element = card.find('h4')
            title = title_element.text.strip() if title_element else "No Title"

            # Find the price element
            price_element = card.find('p', {'data-testid': 'ad-price'})
            price = price_element.text.strip() if price_element else "No Price"

            # Find the link element and construct the full URL
            link_element = card.find('a')
            if link_element and 'href' in link_element.attrs:
                partial_url = link_element['href']
                # Prepend the domain if the link is relative
                full_url = "https://www.olx.pl" + partial_url if partial_url.startswith('/') else partial_url
            else:
                full_url = None

            if listing_id and title and full_url:
                listings.append({
                    'id': listing_id,
                    'title': title,
                    'price': price,
                    'url': full_url
                })
        except Exception as e:
            logger.error(f"Error parsing an HTML listing card: {e}")
            continue
    return listings

def scrape_olx(url):
    """
    Scrapes an OLX search page using a primary (JSON-LD) and fallback (HTML parsing) method.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Connection': 'keep-alive'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error fetching OLX URL {url}: {e}")
        return []

    soup = BeautifulSoup(response.text, 'lxml')
    listings = []

    # --- Primary Method: Try parsing JSON-LD first ---
    json_ld_script = soup.find('script', {'type': 'application/ld+json'})
    if json_ld_script:
        try:
            data = json.loads(json_ld_script.string)
            # This path might change, but it's a common pattern
            offer_list = data.get("offers", {}).get("offers", [])

            if offer_list:
                logger.info("Successfully found listings via JSON-LD method.")
                for offer in offer_list:
                    # (Code from previous JSON-LD version)
                    listing_url = offer.get("url")
                    if not listing_url: continue

                    id_match = re.search(r'-ID([a-zA-Z0-9]+)\.html', listing_url)
                    listing_id = id_match.group(1) if id_match else listing_url.split('/')[-1]

                    title = re.sub(r'\s+', ' ', offer.get("name", "No Title")).strip()
                    price = f"{offer.get('price', 0)} {offer.get('priceCurrency', 'PLN')}"

                    listings.append({'id': listing_id, 'title': title, 'price': price, 'url': listing_url})
                return listings # Return immediately if JSON-LD was successful

        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"JSON-LD parsing failed: {e}. Attempting HTML fallback.")
    else:
        logger.warning("JSON-LD script not found. Attempting HTML fallback.")


    # --- Fallback Method: Parse HTML cards if JSON-LD fails or is empty ---
    logger.info("Executing HTML fallback parsing logic.")
    listings = _parse_html_cards(soup)
    
    if not listings:
        logger.warning("Fallback HTML parsing also failed to find any listings.")

    return listings


# --- Bot Background Job ---
def check_for_new_listings(context: CallbackContext):
    """The main job that runs periodically to check for new listings."""
    logger.info("Running periodic check for new listings...")
    user_searches = load_data(USER_SEARCHES_FILE)
    seen_listings = load_data(SEEN_LISTINGS_FILE)
    market_data = load_data(MARKET_DATA_FILE) 

    market_data_changed = False

    for chat_id_str, searches in user_searches.items():
        chat_id = int(chat_id_str)
        if not searches:
            continue

        for search_name, search_url in searches.items():
            # Create a unique key for this search to store seen listings
            search_key = f"{chat_id}_{search_name}"
            if search_key not in seen_listings:
                seen_listings[search_key] = []
            
            logger.info(f"Scraping '{search_name}' for chat_id {chat_id}")
            try:
                current_listings = scrape_olx(search_url)
                if not current_listings:
                    logger.warning(f"No listings found for '{search_name}'. Check URL or OLX structure.")
                    continue

                new_listings_found = False
                
                for listing in reversed(current_listings): # Reverse to send oldest new first
                    if listing['id'] not in seen_listings[search_key]:
                        ai_analysis = None
                        if GEMINI_API_KEY:
                            # Get existing market data for this search to provide context
                            existing_market_data = market_data.get(search_key, {})
                            ai_analysis = analyze_listing_price_with_ai(listing, search_name, existing_market_data)
 
                        message = (
                            f"✨ *New Listing Found for '{search_name}'* ✨\n\n"
                            f"*{listing['title']}*\n\n"
                            f"💰 *Price:* {listing['price']}\n"
                            f"🔗 [View Listing]({listing['url']})"
                        )
                        if ai_analysis:
                            message += format_price_analysis_message(ai_analysis)


                        context.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            parse_mode='Markdown',
                            disable_web_page_preview=False
                        )
                        seen_listings[search_key].append(listing['id'])
                        new_listings_found = True


                        if search_key not in market_data:
                            market_data[search_key] = {}
                        
                        current_timestamp = int(time.time())
                        market_data[search_key][listing['id']] = {
                            'title': listing['title'],
                            'initial_price': _parse_price(listing['price']),
                            'url': listing['url'],
                            'status': 'active', # It's active because we just found it
                            'first_seen_timestamp': current_timestamp,
                            'last_seen_timestamp': current_timestamp,
                            'removed_timestamp': None, # Not removed yet
                            'ai_analysis': ai_analysis 
                        }

                        market_data_changed = True

                        time.sleep(5) # Sleep briefly to avoid hitting Telegram rate limits

                if new_listings_found:
                    # Prune old seen listings to keep the file size manageable
                    seen_listings[search_key] = seen_listings[search_key][-100:]
                    save_data(seen_listings, SEEN_LISTINGS_FILE)

            except Exception as e:
                logger.error(f"An error occurred while processing search '{search_name}': {e}")
                context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Error checking your search '{search_name}'. I'll try again later."
                )
                
    if market_data_changed:
        logger.info("New listings found, saving updated market data.")
        save_data(market_data, MARKET_DATA_FILE)


def monitor_existing_listings(context: CallbackContext):
    """
    Periodically checks the status of listings stored in the market_data.json file.
    If a listing is no longer on the site, its status is updated to 'inactive/sold'.
    """
    logger.info("Running periodic check to monitor existing listings...")
    user_searches = load_data(USER_SEARCHES_FILE)
    market_data = load_data(MARKET_DATA_FILE)
    data_was_changed = False

    for chat_id_str, searches in user_searches.items():
        for search_name, search_url in searches.items():
            search_key = f"{chat_id_str}_{search_name}"

            if search_key not in market_data:
                continue # No listings being tracked for this search yet

            logger.info(f"Monitoring listings for search: '{search_name}'")
            try:
                # Get a simple set of all currently active listing IDs from the live page
                live_listings = scrape_olx(search_url)
                live_listing_ids = {item['id'] for item in live_listings}

                if not live_listing_ids:
                    logger.warning(f"Got no live listings for '{search_name}' during monitoring check. Skipping status update for this search.")
                    continue

                # Check our stored listings against the live ones
                for listing_id, listing_data in market_data[search_key].items():
                    # Only check listings that are currently marked as 'active'
                    if listing_data['status'] == 'active':
                        current_timestamp = int(time.time())
                        if listing_id in live_listing_ids:
                            # It's still active, just update its 'last_seen' timestamp
                            market_data[search_key][listing_id]['last_seen_timestamp'] = current_timestamp
                            data_was_changed = True
                        else:
                            # It's gone! Mark as inactive and record when it was removed.
                            logger.info(f"Listing '{listing_data['title']}' ({listing_id}) is no longer active. Marking as inactive/sold.")
                            market_data[search_key][listing_id]['status'] = 'inactive/sold'
                            market_data[search_key][listing_id]['removed_timestamp'] = current_timestamp
                            data_was_changed = True
            except Exception as e:
                logger.error(f"An error occurred while monitoring search '{search_name}': {e}")

    if data_was_changed:
        logger.info("Saving updated market data.")
        save_data(market_data, MARKET_DATA_FILE)


# --- Bot Command Handlers ---
def start(update: Update, context: CallbackContext):
    """Sends a welcome message when the /start command is issued."""
    user = update.effective_user
    update.message.reply_html(
        f"Hi {user.mention_html()}!\n\n"
        "I am your personal OLX notifier bot. I can watch OLX searches for you and notify you about new items.\n\n"
        "Here are the commands you can use:\n"
        "/add - Add a new OLX search URL to watch\n"
        "/list - Show all your current searches\n"
        "/delete - Remove a search\n"
        "/help - Show this message again"
    )

# --- Add Search Conversation ---
def add_search_start(update: Update, context: CallbackContext):
    """Starts the conversation to add a new search."""
    update.message.reply_text("Okay, let's add a new search.\n\nPlease give this search a short, unique name (e.g., 'vintage camera').")
    return ASK_NAME

def ask_name(update: Update, context: CallbackContext):
    """Asks for the URL after getting the name."""
    search_name = update.message.text.strip()
    
    # Check if name is already in use
    chat_id = str(update.message.chat_id)
    user_searches = load_data(USER_SEARCHES_FILE)
    if chat_id in user_searches and search_name in user_searches[chat_id]:
        update.message.reply_text("You already have a search with that name. Please choose another one, or /cancel.")
        return ASK_NAME

    context.user_data['search_name'] = search_name
    update.message.reply_text(f"Great! Now please send me the full OLX.pl search URL you want me to watch for '{search_name}'.")
    return ASK_URL

def add_search_url(update: Update, context: CallbackContext):
    """Saves the search URL and ends the conversation."""
    chat_id = str(update.message.chat_id)
    search_url = update.message.text.strip()
    search_name = context.user_data['search_name']

    if "olx.pl" not in search_url:
        update.message.reply_text("This doesn't look like a valid OLX.pl URL. Please try again or /cancel.")
        return ASK_URL

    user_searches = load_data(USER_SEARCHES_FILE)
    if chat_id not in user_searches:
        user_searches[chat_id] = {}
    
    user_searches[chat_id][search_name] = search_url
    save_data(user_searches, USER_SEARCHES_FILE)

    update.message.reply_text(f"✅ Success! I am now watching for new listings for '{search_name}'.\nI will check every few minutes.")
    
    # Clean up user_data
    del context.user_data['search_name']
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    """Cancels the current conversation."""
    update.message.reply_text("Operation cancelled.")
    if 'search_name' in context.user_data:
        del context.user_data['search_name']
    return ConversationHandler.END


# --- List and Delete Searches ---
def list_searches(update: Update, context: CallbackContext):
    """Lists all active searches for the user."""
    chat_id = str(update.message.chat_id)
    user_searches = load_data(USER_SEARCHES_FILE)

    if chat_id not in user_searches or not user_searches[chat_id]:
        update.message.reply_text("You have no active searches. Use /add to create one.")
        return

    message = "Your active searches:\n\n"
    for name, url in user_searches[chat_id].items():
        message += f"• *{name}*\n"
    
    update.message.reply_text(message, parse_mode='Markdown')

def delete_search_start(update: Update, context: CallbackContext):
    """Shows a list of searches to delete."""
    chat_id = str(update.message.chat_id)
    user_searches = load_data(USER_SEARCHES_FILE)

    if chat_id not in user_searches or not user_searches[chat_id]:
        update.message.reply_text("You have no searches to delete.")
        return
    
    keyboard = []
    for search_name in user_searches[chat_id].keys():
        # Callback data is 'delete_{search_name}'
        keyboard.append([InlineKeyboardButton(f"❌ {search_name}", callback_data=f"delete_{search_name}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Please choose a search to delete:", reply_markup=reply_markup)

def delete_search_callback(update: Update, context: CallbackContext):
    """Handles the button press for deleting a search."""
    query = update.callback_query
    query.answer() # Acknowledge the button press

    # 'delete_{search_name}'
    search_name_to_delete = query.data.split('_', 1)[1]
    chat_id = str(query.message.chat_id)

    user_searches = load_data(USER_SEARCHES_FILE)
    if chat_id in user_searches and search_name_to_delete in user_searches[chat_id]:
        del user_searches[chat_id][search_name_to_delete]
        save_data(user_searches, USER_SEARCHES_FILE)
        
        # Also clean up seen listings for this search
        seen_listings = load_data(SEEN_LISTINGS_FILE)
        search_key = f"{chat_id}_{search_name_to_delete}"
        if search_key in seen_listings:
            del seen_listings[search_key]
            save_data(seen_listings, SEEN_LISTINGS_FILE)

        market_data = load_data(MARKET_DATA_FILE)
        if search_key in market_data:
            del market_data[search_key]
            save_data(market_data, MARKET_DATA_FILE)

        query.edit_message_text(text=f"✅ Search '{search_name_to_delete}' has been deleted.")
    else:
        query.edit_message_text(text="Could not find that search. It might have been already deleted.")


# --- Main Application Setup ---
def main():
    """Start the bot and set up logging."""
    # --- Load Admin Chat ID from environment variable ---
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
    if not ADMIN_CHAT_ID:
        # We don't raise an error, so the bot can run without a log bot if desired.
        logger.warning("ADMIN_CHAT_ID not set. Logs will not be sent to Telegram.")

    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # --- NEW: Set up Telegram logging ---
    if ADMIN_CHAT_ID:
        try:
            # Create an instance of our custom handler
            # We pass the bot instance and the admin's chat ID
            telegram_handler = TelegramLogHandler(bot=updater.bot, chat_id=ADMIN_CHAT_ID)

            # Set the level of logs you want to receive.
            # WARNING, ERROR, and CRITICAL are good choices to avoid spam.
            telegram_handler.setLevel(logging.WARNING)

            # Create a formatter to make the logs look nice
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            telegram_handler.setFormatter(formatter)

            # Add the handler to the root logger
            logging.getLogger().addHandler(telegram_handler)

            logger.warning("Telegram logging handler successfully configured.") # This message will be a test
        except Exception as e:
            logger.error(f"Failed to configure Telegram logging handler: {e}")
    # --- END of new logging section ---

    # Add conversation handler for adding searches
    add_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_search_start)],
        states={
            ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_URL: [MessageHandler(Filters.text & ~Filters.command, add_search_url)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", start))
    dispatcher.add_handler(add_handler)
    dispatcher.add_handler(CommandHandler("list", list_searches))
    dispatcher.add_handler(CommandHandler("delete", delete_search_start))
    dispatcher.add_handler(CallbackQueryHandler(delete_search_callback, pattern='^delete_'))

    # Set up the background job
    job_queue = updater.job_queue
    job_queue.run_repeating(check_for_new_listings, interval=CHECK_INTERVAL, first=10)
   
    # We'll start it after 60 seconds to stagger it from the first job.
    job_queue.run_repeating(monitor_existing_listings, interval=MONITOR_INTERVAL, first=60)

    # Start the Bot
    updater.start_polling()
    logger.info("Bot started and polling for updates...")

    # Run the bot until you press Ctrl-C
    updater.idle()


def analyze_listing_price_with_ai(listing, search_name, market_data_for_search):
    """
    Uses Google Gemini to analyze if a listing's price is reasonable.
    Returns a dict with analysis results or None if AI is not available.
    """
    if not GEMINI_API_KEY:
        return None
    
    try:
        # Prepare market context from previous listings
        market_context = ""
        if market_data_for_search:
            prices = []
            for item_data in market_data_for_search.values():
                if item_data.get('initial_price', 0) > 0:
                    prices.append(item_data['initial_price'])
            
            if len(prices) >= 3:  # Only provide context if we have enough data
                avg_price = sum(prices) / len(prices)
                min_price = min(prices)
                max_price = max(prices)
                market_context = f"""
Market context for '{search_name}':
- Average price from previous listings: {avg_price:.2f} PLN
- Price range: {min_price:.2f} - {max_price:.2f} PLN
- Number of previous listings: {len(prices)}
"""

        # Extract numeric price from the listing
        numeric_price = _parse_price(listing['price'])
        
        # Create the prompt for Gemini
        prompt = f"""
You are a price analysis expert for online marketplace listings. Analyze this OLX.pl listing:

Title: {listing['title']}
Price: {listing['price']} (numeric: {numeric_price} PLN)
Search category: {search_name}

{market_context}

Please analyze if this price is:
1. HIGH (overpriced)
2. FAIR (reasonable market price)  
3. LOW (good deal/underpriced)

Provide your response in this EXACT JSON format:
{{
    "price_assessment": "HIGH|FAIR|LOW",
    "confidence": "HIGH|MEDIUM|LOW",
    "short_analysis": "Brief 1-2 sentence explanation focusing on why the price is high/fair/low. Mention any potential issues or benefits."
}}

Consider factors like:
- Item condition (if mentioned)
- Brand/model (if identifiable)
- Market comparison (if context provided)
- Potential red flags (too cheap might indicate issues, too expensive might be overpriced)
- Typical pricing for this category in Poland

Keep the analysis concise and practical for a buyer.
"""

        # Initialize the model
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        
        # Generate the response
        response = model.generate_content(prompt)
        
        # Parse the JSON response
        try:
            # Extract JSON from the response text
            response_text = response.text.strip()
            # Remove markdown code blocks if present
            if response_text.startswith('```json'):
                response_text = response_text.replace('```json', '').replace('```', '').strip()
            elif response_text.startswith('```'):
                response_text = response_text.replace('```', '').strip()
            
            analysis_result = json.loads(response_text)
            
            # Validate the response format
            required_fields = ['price_assessment', 'confidence', 'short_analysis']
            if all(field in analysis_result for field in required_fields):
                logger.info(f"AI analysis completed for listing: {listing['title'][:50]}...")
                return analysis_result
            else:
                logger.warning(f"AI response missing required fields: {analysis_result}")
                return None
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            logger.error(f"Raw response: {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error during AI price analysis: {e}")
        return None

def format_price_analysis_message(analysis):
    """
    Formats the AI analysis into a readable message part.
    """
    if not analysis:
        return ""
    
    # Choose emoji based on assessment
    emoji_map = {
        "HIGH": "🔴",
        "FAIR": "🟡", 
        "LOW": "🟢"
    }
    
    assessment_emoji = emoji_map.get(analysis['price_assessment'], "⚪")
    confidence = analysis.get('confidence', 'MEDIUM').lower()
    
    analysis_text = f"\n\n{assessment_emoji} **AI Price Analysis** ({confidence} confidence):\n"
    analysis_text += f"*{analysis['short_analysis']}*"
    
    return analysis_text

if __name__ == '__main__':
    main()