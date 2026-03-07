"""
OLX Scraper — Background Jobs

Unified scrape job and sold-detection monitor.
"""

import asyncio
import logging
import time

from telegram.ext import ContextTypes

import db
from admin import notify_admin, record_scrape
from config import CONFIG
from formatters import (
    cheap_price_stats,
    format_cheap_listing,
    format_cheap_product_group,
    format_monitor_listing,
    parse_price,
)
from llm import batch_llm_filter, get_cheap_summaries
from location_filter import filter_by_location
from scraper import fetch_listing_details, scrape_olx, scrape_olx_page

logger = logging.getLogger(__name__)


# ============================================================================
# UNIFIED SCRAPE JOB
# ============================================================================

async def scrape_all(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Single unified scrape job for all 3 modes.

    - monitor: scrape page 1, send new listings directly
    - slopsearch (pending_scrape): paginated scrape + 2-stage LLM filter → reviewing
    - slopsearch (monitoring): scrape page 1, LLM filter new listings → notify
    - cheap (monitoring): scrape page 1 per product, LLM summaries → send with buttons
    """
    logger.info("Running unified scrape cycle...")
    cycle_start = time.time()
    searches = db.get_active_searches()
    errors = []

    for search in searches:
        try:
            mode = search["mode"]
            search_id = search["id"]
            chat_id = search["chat_id"]

            if mode == "monitor":
                await _process_monitor(context, search)
            elif mode == "slopsearch":
                await _process_slopsearch(context, search)
            elif mode == "cheap":
                await _process_cheap(context, search)

        except Exception as e:
            err_msg = f"Error processing '{search.get('name')}' (id={search.get('id')}): {e}"
            logger.error(err_msg)
            errors.append(err_msg)

    duration = time.time() - cycle_start
    record_scrape(duration)

    if errors:
        await notify_admin(context, "\n".join(errors), level="error")


# ============================================================================
# MONITOR MODE
# ============================================================================

async def _process_monitor(context: ContextTypes.DEFAULT_TYPE, search: dict) -> None:
    """Scrape page 1, send all new listings directly. Zero AI."""
    url = search.get("url")
    if not url:
        return

    listings = await asyncio.to_thread(scrape_olx_page, url)
    if not listings:
        return

    search_id = search["id"]
    chat_id = search["chat_id"]
    name = search["name"]
    seen_ids = db.get_seen_ids(search_id)

    new_listings = [l for l in reversed(listings) if l["id"] not in seen_ids]
    if not new_listings:
        return

    db.mark_seen(search_id, [l["id"] for l in new_listings])

    for listing in new_listings:
        db.save_listing(listing)
        db.add_search_listing(search_id, listing["id"], status="sent")

        message = format_monitor_listing(name, listing)
        await context.bot.send_message(
            chat_id=int(chat_id),
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=False,
        )
        await asyncio.sleep(CONFIG.RATE_LIMIT_DELAY)


# ============================================================================
# SLOPSEARCH MODE
# ============================================================================

async def _process_slopsearch(context: ContextTypes.DEFAULT_TYPE, search: dict) -> None:
    """Handle slopsearch: initial scrape or continuous monitoring."""
    status = search["status"]
    search_id = search["id"]
    chat_id = search["chat_id"]
    name = search["name"]
    url = search.get("url")

    if not url:
        return

    if status == "pending_scrape":
        await _slopsearch_initial_scrape(context, search)
    elif status == "monitoring":
        await _slopsearch_monitor(context, search)


async def _slopsearch_initial_scrape(context: ContextTypes.DEFAULT_TYPE, search: dict) -> None:
    """Full paginated scrape + LLM filter for a new slopsearch."""
    search_id = search["id"]
    chat_id = search["chat_id"]
    name = search["name"]

    db.update_search_status(search_id, "scraping")
    logger.info(f"Starting initial scrape for slopsearch: {name}")

    all_listings = await asyncio.to_thread(scrape_olx, search["url"], True)
    logger.info(f"Scraped {len(all_listings)} total listings for {name}")

    if not all_listings:
        db.update_search_status(search_id, "error")
        await context.bot.send_message(
            chat_id=int(chat_id),
            text=f"❌ No listings found for '{name}'. The search URL may be invalid.\nPlease /delete it and /add a new search.",
        )
        return

    # Mark all as seen
    db.mark_seen(search_id, [l["id"] for l in all_listings])

    # Price filter
    max_price = search.get("max_price")
    min_price = search.get("min_price")
    if max_price is not None:
        all_listings = [l for l in all_listings if parse_price(l["price"]) <= max_price]
    if min_price is not None:
        all_listings = [l for l in all_listings if parse_price(l["price"]) >= min_price]

    # Location filter (OLX URL path doesn't actually filter by city)
    all_listings = filter_by_location(
        all_listings, search.get("location"), search.get("location_radius"),
    )

    # Cap LLM input
    to_filter = all_listings[: CONFIG.MAX_INITIAL_FILTER]
    skipped = len(all_listings) - len(to_filter)
    if skipped:
        logger.info(f"Capped LLM filter at {CONFIG.MAX_INITIAL_FILTER} listings ({skipped} skipped)")

    # LLM filter
    keywords = search.get("keywords") or []
    filtered = await batch_llm_filter(to_filter, keywords)

    # Save filtered listings
    for listing in filtered:
        db.save_listing(listing)
        db.add_search_listing(search_id, listing["id"], status="pending")

    db.update_search_status(search_id, "reviewing")

    await context.bot.send_message(
        chat_id=int(chat_id),
        text=(
            f"✅ Scraping complete for '{name}'. "
            f"Found {len(filtered)} potential matches out of {len(to_filter)} checked.\n"
            f"Type /resume to start reviewing."
        ),
    )


async def _slopsearch_monitor(context: ContextTypes.DEFAULT_TYPE, search: dict) -> None:
    """Check for new listings on a monitored slopsearch."""
    search_id = search["id"]
    chat_id = search["chat_id"]
    name = search["name"]

    listings = await asyncio.to_thread(scrape_olx_page, search["url"])
    if not listings:
        return

    seen_ids = db.get_seen_ids(search_id)
    new_listings = [l for l in listings if l["id"] not in seen_ids]
    if not new_listings:
        return

    db.mark_seen(search_id, [l["id"] for l in new_listings])

    # Price filter
    max_price = search.get("max_price")
    min_price = search.get("min_price")
    if max_price is not None:
        new_listings = [l for l in new_listings if parse_price(l["price"]) <= max_price]
    if min_price is not None:
        new_listings = [l for l in new_listings if parse_price(l["price"]) >= min_price]
    if not new_listings:
        return

    # Location filter
    new_listings = filter_by_location(
        new_listings, search.get("location"), search.get("location_radius"),
    )
    if not new_listings:
        return

    # LLM filter
    keywords = search.get("keywords") or []
    matched = await batch_llm_filter(new_listings, keywords)
    if not matched:
        return

    # Save & queue for review
    for listing in matched:
        db.save_listing(listing)
        db.add_search_listing(search_id, listing["id"], status="pending")

    listing_links = "\n".join(
        f"• [{l['title']}]({l['url']}) — {l['price']}" for l in matched
    )
    message = (
        f"🚨 *{len(matched)} new match{'es' if len(matched) > 1 else ''}: {name}*\n\n"
        f"{listing_links}\n\n"
        f"Type /resume to review."
    )
    await context.bot.send_message(
        chat_id=int(chat_id),
        text=message,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    db.update_search_status(search_id, "reviewing")


# ============================================================================
# CHEAP MODE
# ============================================================================

async def _process_cheap(context: ContextTypes.DEFAULT_TYPE, search: dict) -> None:
    """Scrape per-product URLs for cheap mode, send grouped messages per product."""
    if search["status"] != "monitoring":
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import hashlib

    search_id = search["id"]
    chat_id = search["chat_id"]
    name = search["name"]
    max_price = search.get("max_price")
    min_price = search.get("min_price")
    original_query = search.get("original_query") or search.get("name", "")
    search_urls = db.get_search_urls(search_id)

    for url_entry in search_urls:
        product = url_entry.get("product_name", "")
        url = url_entry["url"]

        try:
            listings = await asyncio.to_thread(scrape_olx_page, url)
            if not listings:
                continue

            # Discard OLX extended search results (wrong products mixed in)
            # OLX adds reason=extended_search_* when broadening results beyond the query
            real_count = len(listings)
            listings = [
                l for l in listings
                if "extended_search" not in l.get("url", "")
            ]
            if real_count > 0 and not listings:
                logger.info(f"Cheap: all {real_count} listings for '{product}' are fallback (no local results)")
                continue

            seen_ids = db.get_seen_ids(search_id)
            new_listings = [l for l in listings if l["id"] not in seen_ids]
            if not new_listings:
                continue

            db.mark_seen(search_id, [l["id"] for l in new_listings])

            # For browse URLs, cap to newest 15 per cycle to limit detail-fetching
            is_browse = product.startswith("[browse]")
            if is_browse and len(new_listings) > 15:
                logger.info(f"Cheap: capping browse results from {len(new_listings)} to 15 for '{product}'")
                new_listings = new_listings[:15]

            # Price filter
            if max_price is not None:
                new_listings = [
                    l for l in new_listings if parse_price(l["price"]) <= max_price
                ]
            if min_price is not None:
                new_listings = [
                    l for l in new_listings if parse_price(l["price"]) >= min_price
                ]
            if not new_listings:
                continue

            # Location filter
            new_listings = filter_by_location(
                new_listings, search.get("location"), search.get("location_radius"),
            )
            if not new_listings:
                continue

            # Fetch details in parallel (non-blocking)
            details_list = await asyncio.gather(
                *(asyncio.to_thread(fetch_listing_details, l["url"]) for l in new_listings)
            )

            # AI verdicts (pass/reject + short summary)
            verdicts = await get_cheap_summaries(new_listings, details_list, original_query, product)

            # Filter: keep only passed listings
            passed = [
                (l, d, v) for l, d, v in zip(new_listings, details_list, verdicts)
                if v.get("pass", True)
            ]
            if not passed:
                logger.info(f"Cheap: all {len(new_listings)} listings for '{product}' rejected by LLM")
                continue

            passed_listings = [t[0] for t in passed]
            passed_details = [t[1] for t in passed]
            passed_verdicts = [t[2] for t in passed]

            # Save listings to DB
            for listing, details in zip(passed_listings, passed_details):
                db.save_listing({**listing, **details})

            # Get accepted listings for global stats
            accepted = db.get_search_listings(search_id, status="accepted")
            stats_line = cheap_price_stats(accepted)

            # Build grouped message
            msg_text = format_cheap_product_group(
                name, product, passed_listings, passed_details, passed_verdicts, stats_line,
            )

            # Product hash for callback data (keep it short)
            product_hash = hashlib.md5(product.encode()).hexdigest()[:8]
            listing_ids = ",".join(l["id"] for l in passed_listings)

            keyboard = [[
                InlineKeyboardButton(
                    f"✅ Approve All ({len(passed_listings)})",
                    callback_data=f"ca_{search_id}_{product_hash}",
                ),
                InlineKeyboardButton(
                    "❌ Skip All",
                    callback_data=f"cs_{search_id}_{product_hash}",
                ),
            ]]

            try:
                sent = await context.bot.send_message(
                    chat_id=int(chat_id),
                    text=msg_text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                # Record sent message with all listing IDs for this group
                for listing in passed_listings:
                    db.record_sent_message(
                        str(sent.message_id), chat_id, search_id, listing["id"], product,
                    )
                    db.add_search_listing(search_id, listing["id"], status="sent")
                await asyncio.sleep(CONFIG.RATE_LIMIT_DELAY)
            except Exception as e:
                logger.error(f"Failed to send cheap product group: {e}")

        except Exception as e:
            logger.error(f"Error scraping cheap product '{product}': {e}")


# ============================================================================
# SOLD DETECTION
# ============================================================================

async def detect_sold(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job: detect removed/sold listings across all searches."""
    logger.info("Running sold detection...")
    searches = db.get_active_searches()

    for search in searches:
        search_id = search["id"]
        url = search.get("url")

        if not url:
            # For cheap mode, check each product URL
            search_urls = db.get_search_urls(search_id)
            for url_entry in search_urls:
                await asyncio.to_thread(_check_sold_for_url, search_id, url_entry["url"])
        else:
            await asyncio.to_thread(_check_sold_for_url, search_id, url)


def _check_sold_for_url(search_id: int, url: str) -> None:
    """Check if any tracked listings from this URL have disappeared."""
    try:
        live_listings = scrape_olx_page(url)
        if not live_listings:
            return
        live_ids = {l["id"] for l in live_listings}
        sold = db.mark_active_listings_sold(search_id, live_ids)
        for item in sold:
            logger.info(f"Listing sold: {item.get('title', '?')[:40]}")
    except Exception as e:
        logger.error(f"Error in sold detection for search_id={search_id}: {e}")
