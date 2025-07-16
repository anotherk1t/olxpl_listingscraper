import logging
import os
import json
import time
import requests
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
import hashlib

# --- Configuration ---
# Load the bot token from an environment variable for security
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN environment variable set!")

# --- Constants ---
# File paths for storing data
DATA_DIR = "data"
USER_SEARCHES_FILE = os.path.join(DATA_DIR, "user_searches.json")
SEEN_LISTINGS_FILE = os.path.join(DATA_DIR, "seen_listings.json")

# How often to check for new listings (in seconds)
CHECK_INTERVAL = 300  # 5 minutes

# Conversation states for adding a search
ASK_URL, ASK_NAME = range(2)

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

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


logger = logging.getLogger(__name__)


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
                        message = (
                            f"✨ *New Listing Found for '{search_name}'* ✨\n\n"
                            f"*{listing['title']}*\n\n"
                            f"💰 *Price:* {listing['price']}\n"
                            f"🔗 [View Listing]({listing['url']})"
                        )
                        context.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            parse_mode='Markdown',
                            disable_web_page_preview=False
                        )
                        seen_listings[search_key].append(listing['id'])
                        new_listings_found = True
                        time.sleep(1) # Sleep briefly to avoid hitting Telegram rate limits

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

    # Start the Bot
    updater.start_polling()
    logger.info("Bot started and polling for updates...")

    # Run the bot until you press Ctrl-C
    updater.idle()


if __name__ == '__main__':
    main()