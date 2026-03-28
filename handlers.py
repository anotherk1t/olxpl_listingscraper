"""
OLX Scraper — Telegram Bot Handlers (python-telegram-bot v21)

All command and callback handlers, fully async.
"""

import contextlib
import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import db
import i18n
from admin import notify_admin
from config import (
    ASK_CHEAP_QUERY,
    ASK_MODE,
    ASK_NAME,
    ASK_SLOPSEARCH_QUERY,
    ASK_URL,
    CONFIG,
    CONFIRM_CHEAP_QUERY,
    CONFIRM_SLOPSEARCH_QUERY,
    EDIT_AWAIT_CHANGES,
    MODIFY_CHEAP_QUERY,
    MODIFY_SLOPSEARCH_QUERY,
    OLX_CATEGORIES,
    OLX_URL_CONTEXT,
)
from formatters import (
    build_cheap_confirmation,
    build_slopsearch_confirmation,
    cheap_price_stats,
    format_market_summary,
    format_review_item,
    parse_price,
)
from llm import run_cheap_feedback_llm, run_cheap_mode_llm, run_slopgest_llm, run_slopsearch_llm
from url_builder import assemble_url, category_browse_url, product_to_url, validate_and_correct_url

logger = logging.getLogger(__name__)


def _get_custom_filters(search: dict) -> dict | None:
    """Extract custom_filters dict from a search row (JSON string or None)."""
    raw = search.get("custom_filters")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _format_price_range(min_price, max_price) -> str:
    """Format a human-readable price range string."""
    if min_price and max_price:
        return f"{int(min_price)}–{int(max_price)} PLN"
    elif max_price:
        return f"Max: {int(max_price)} PLN"
    elif min_price:
        return f"Min: {int(min_price)} PLN"
    return "∞"


def get_lang(update: Update) -> str:
    chat_id = str(update.effective_chat.id)
    lang = db.get_user_language(chat_id)
    if not lang:
        lang = getattr(update.effective_user, "language_code", "en")
        if lang:
            lang = lang.split("-")[0]
    return lang or "en"


# ============================================================================
# BASIC COMMANDS
# ============================================================================


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start and /help"""
    lang = get_lang(update)
    msg = i18n.get_text(lang, "start_msg", mention=update.effective_user.mention_html())
    await update.message.reply_html(msg)


async def cmd_slopgest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/slopgest — AI-powered suggestion report."""
    chat_id = str(update.message.chat_id)
    lang = get_lang(update)
    searches = db.get_searches_by_chat(chat_id)
    if not searches:
        await update.message.reply_text(i18n.get_text(lang, "no_searches_err"))
        return

    msg = await update.message.reply_text(i18n.get_text(lang, "slopgest_thinking"))

    try:
        report = await run_slopgest_llm(chat_id)
    except Exception as e:
        logger.error(f"Slopgest LLM call failed: {e}")
        report = ""

    if not report:
        await msg.edit_text(i18n.get_text(lang, "slopgest_err"))
        return

    # Telegram has a 4096 char limit — split if needed
    if len(report) <= 4096:
        await msg.edit_text(report, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        await msg.delete()
        for i in range(0, len(report), 4096):
            chunk = report[i : i + 4096]
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=chunk,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/language — choose bot language."""
    lang = get_lang(update)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
                InlineKeyboardButton("🇵🇱 Polski", callback_data="lang_pl"),
            ],
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
                InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_uk"),
            ],
        ]
    )
    await update.message.reply_text(i18n.get_text(lang, "language_prompt"), reply_markup=kb)


async def callback_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat_id)
    new_lang = query.data.split("_")[1]
    db.set_user_language(chat_id, new_lang)

    await query.edit_message_text(i18n.get_text(new_lang, "lang_success"))


# ============================================================================
# ADD SEARCH — ENTRY POINT
# ============================================================================


async def cmd_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/add — start add search conversation, prompt for mode."""
    lang = get_lang(update)

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.get_text(lang, "btn_monitor"), callback_data="addmode_monitor")],
            [InlineKeyboardButton(i18n.get_text(lang, "btn_precision"), callback_data="addmode_slopsearch")],
            [InlineKeyboardButton(i18n.get_text(lang, "btn_broad"), callback_data="addmode_cheap")],
        ]
    )
    await update.message.reply_html(i18n.get_text(lang, "add_mode_prompt"), reply_markup=kb)
    return ASK_MODE


async def callback_add_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process mode selection and ask the first question."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)

    mode = query.data.split("_")[1]
    context.user_data["mode"] = mode

    if mode == "monitor":
        await query.edit_message_text(
            f"{i18n.get_text(lang, 'add_name_prompt')}{i18n.get_text(lang, 'cancel_hint')}", parse_mode="HTML"
        )
        return ASK_NAME
    elif mode == "slopsearch":
        await query.edit_message_text(
            f"{i18n.get_text(lang, 'precision_prompt')}{i18n.get_text(lang, 'cancel_hint')}", parse_mode="HTML"
        )
        return ASK_SLOPSEARCH_QUERY
    else:  # cheap
        await query.edit_message_text(
            f"{i18n.get_text(lang, 'broad_prompt')}{i18n.get_text(lang, 'cancel_hint')}", parse_mode="HTML"
        )
        return ASK_CHEAP_QUERY


# ============================================================================
# ADD — MONITOR MODE
# ============================================================================


async def cmd_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle search name input (monitor mode)."""
    lang = get_lang(update)
    name = update.message.text.strip()
    chat_id = str(update.message.chat_id)

    existing = db.get_search_by_name(chat_id, name)
    if existing:
        await update.message.reply_text(i18n.get_text(lang, "name_exists_err"))
        return ASK_NAME

    context.user_data["search_name"] = name
    await update.message.reply_text(i18n.get_text(lang, "add_url_prompt", name=name))
    return ASK_URL


async def cmd_add_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle search URL input (monitor mode) and save."""
    lang = get_lang(update)
    url = update.message.text.strip()

    if "olx.pl" not in url:
        await update.message.reply_text(i18n.get_text(lang, "invalid_url_err"))
        return ASK_URL

    chat_id = str(update.message.chat_id)
    name = context.user_data.pop("search_name")

    db.create_search(chat_id, name, "monitor", url=url)

    await update.message.reply_text(i18n.get_text(lang, "monitor_added", name=name, mins=CONFIG.CHECK_INTERVAL // 60))
    await notify_admin(context, f"📋 Search created: *{name}* (monitor) by `{chat_id}`", level="success")
    from telegram.ext import ConversationHandler

    return ConversationHandler.END


# ============================================================================
# ADD — SLOPSEARCH MODE
# ============================================================================


def _slopsearch_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(i18n.get_text(lang, "btn_approve"), callback_data="slopsearch_approve"),
                InlineKeyboardButton(i18n.get_text(lang, "btn_modify"), callback_data="slopsearch_modify"),
                InlineKeyboardButton(i18n.get_text(lang, "btn_reject"), callback_data="slopsearch_reject"),
            ]
        ]
    )


async def cmd_add_slopsearch_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process natural language query via Gemini to refine into an OLX search."""
    lang = get_lang(update)
    user_query = update.message.text.strip()
    await update.message.reply_text(i18n.get_text(lang, "thinking_refining"))

    refined = await run_slopsearch_llm(user_query, OLX_CATEGORIES, OLX_URL_CONTEXT)
    if refined is None:
        await update.message.reply_text(i18n.get_text(lang, "llm_parse_err"))
        from telegram.ext import ConversationHandler

        return ConversationHandler.END

    # Assemble & validate URL
    raw_url = assemble_url(
        refined.get("base_path", "oferty"),
        refined.get("keyword", refined.get("name", "oferty")),
        refined.get("max_price"),
        refined.get("condition"),
        refined.get("location"),
        refined.get("location_radius"),
        refined.get("min_price"),
    )
    search_ctx = {
        "name": refined.get("name"),
        "max_price": refined.get("max_price"),
        "condition": refined.get("condition"),
    }
    validated_url, used_fallback = await validate_and_correct_url(
        raw_url,
        refined.get("keyword", ""),
        search_ctx,
    )
    refined["url"] = validated_url
    refined["url_fallback"] = used_fallback

    context.user_data["slopsearch_data"] = refined
    await update.message.reply_text(
        build_slopsearch_confirmation(refined, lang),
        reply_markup=_slopsearch_keyboard(lang),
        parse_mode="Markdown",
    )
    return CONFIRM_SLOPSEARCH_QUERY


async def callback_confirm_slopsearch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Approve / Modify / Reject on slopsearch confirmation."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)

    from telegram.ext import ConversationHandler

    if query.data == "slopsearch_reject":
        await query.edit_message_text(f"❌ {i18n.get_text(lang, 'slopsearch_rejected')}")
        context.user_data.pop("slopsearch_data", None)
        return ConversationHandler.END

    if query.data == "slopsearch_modify":
        await query.edit_message_text(
            i18n.get_text(lang, "modify_prompt"),
            parse_mode="Markdown",
        )
        return MODIFY_SLOPSEARCH_QUERY

    # slopsearch_approve
    refined = context.user_data.pop("slopsearch_data", {})
    if not refined:
        await query.edit_message_text(i18n.get_text(lang, "session_expired_add"))
        return ConversationHandler.END

    chat_id = str(query.message.chat_id)
    name = refined.get("name", "Unnamed Search")

    db.create_search(
        chat_id,
        name,
        "slopsearch",
        url=refined.get("url"),
        max_price=refined.get("max_price"),
        min_price=refined.get("min_price"),
        keywords=refined.get("keywords", []),
        location=refined.get("location"),
        location_radius=refined.get("location_radius"),
        status="pending_scrape",
    )

    await query.edit_message_text(
        f"✅ Slopsearch '{name}' approved! I will begin scraping and filtering in the background."
    )
    await notify_admin(context, f"📋 Search created: *{name}* (slopsearch) by `{chat_id}`", level="success")
    return ConversationHandler.END


async def cmd_modify_slopsearch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user's modification request and re-run LLM."""
    modification = update.message.text.strip()
    existing = context.user_data.get("slopsearch_data")
    lang = get_lang(update)

    from telegram.ext import ConversationHandler

    if not existing:
        await update.message.reply_text(i18n.get_text(lang, "session_expired_add"))
        return ConversationHandler.END

    await update.message.reply_text(i18n.get_text(lang, "updating_search"))

    refined = await run_slopsearch_llm(modification, OLX_CATEGORIES, OLX_URL_CONTEXT, existing=existing)
    if refined is None:
        await update.message.reply_text(i18n.get_text(lang, "update_failed"))
        return MODIFY_SLOPSEARCH_QUERY

    # Re-validate URL
    raw_url = assemble_url(
        refined.get("base_path", "oferty"),
        refined.get("keyword", refined.get("name", "oferty")),
        refined.get("max_price"),
        refined.get("condition"),
        refined.get("location"),
        refined.get("location_radius"),
        refined.get("min_price"),
    )
    search_ctx = {
        "name": refined.get("name"),
        "max_price": refined.get("max_price"),
        "condition": refined.get("condition"),
    }
    validated_url, used_fallback = await validate_and_correct_url(
        raw_url,
        refined.get("keyword", ""),
        search_ctx,
    )
    refined["url"] = validated_url
    refined["url_fallback"] = used_fallback

    context.user_data["slopsearch_data"] = refined
    await update.message.reply_text(
        build_slopsearch_confirmation(refined),
        reply_markup=_slopsearch_keyboard(),
        parse_mode="Markdown",
    )
    return CONFIRM_SLOPSEARCH_QUERY


# ============================================================================
# ADD — CHEAP MODE
# ============================================================================


def _cheap_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(i18n.get_text(lang, "btn_approve"), callback_data="cheap_approve"),
                InlineKeyboardButton(i18n.get_text(lang, "btn_modify"), callback_data="cheap_modify"),
                InlineKeyboardButton(i18n.get_text(lang, "btn_cancel"), callback_data="cheap_cancel"),
            ]
        ]
    )


async def cmd_add_cheap_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process NL query via Gemini to generate a product list."""
    lang = get_lang(update)
    user_query = update.message.text.strip()
    context.user_data["cheap_original_query"] = user_query
    await update.message.reply_text(i18n.get_text(lang, "finding_products"))

    data = await run_cheap_mode_llm(user_query)
    if data is None or not data.get("products"):
        await update.message.reply_text(i18n.get_text(lang, "generate_failed"))
        from telegram.ext import ConversationHandler

        return ConversationHandler.END

    context.user_data["cheap_data"] = data
    await update.message.reply_text(
        build_cheap_confirmation(data, lang),
        reply_markup=_cheap_keyboard(lang),
        parse_mode="Markdown",
    )
    return CONFIRM_CHEAP_QUERY


async def callback_confirm_cheap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Approve / Modify / Cancel on cheap mode confirmation."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)

    from telegram.ext import ConversationHandler

    if query.data == "cheap_cancel":
        await query.edit_message_text(f"❌ {i18n.get_text(lang, 'cancel_msg')}")
        context.user_data.pop("cheap_data", None)
        return ConversationHandler.END

    if query.data == "cheap_modify":
        await query.edit_message_text(
            "✏️ What would you like to change?\n(e.g. 'lower the price', 'focus on wireless models', 'add Ducky brand')"
        )
        return MODIFY_CHEAP_QUERY

    # cheap_approve
    data = context.user_data.get("cheap_data")
    if not data:
        await query.edit_message_text(i18n.get_text(lang, "session_expired_add"))
        return ConversationHandler.END

    chat_id = str(query.message.chat_id)
    original_query = context.user_data.get("cheap_original_query", "")
    max_price = data.get("max_price")
    min_price = data.get("min_price")
    products = data.get("products", [])
    location = data.get("location")
    location_radius = data.get("location_radius")
    base_path = data.get("base_path")
    condition = data.get("condition")
    browse_cat = data.get("browse_category")
    custom_filters = data.get("custom_filters")

    search_id = db.create_search(
        chat_id,
        data.get("name", "Cheap search"),
        "cheap",
        max_price=max_price,
        min_price=min_price,
        original_query=original_query,
        products=products,
        location=location,
        location_radius=location_radius,
        base_path=base_path,
        condition=condition,
        browse_category=browse_cat,
        custom_filters=custom_filters,
        status="monitoring",
    )

    # Add product URLs + a broad keyword search for the category
    url_entries = [
        {
            "url": product_to_url(
                p, max_price, location, location_radius, base_path, condition, min_price, custom_filters
            ),
            "product_name": p,
        }
        for p in products
    ]
    # Add a broad keyword URL to catch listings not matching specific models
    broad_name = data.get("name", "").strip()
    if broad_name:
        broad_url = product_to_url(
            broad_name, max_price, location, location_radius, base_path, condition, min_price, custom_filters
        )
        url_entries.append({"url": broad_url, "product_name": f"[broad] {broad_name}"})
    # Add a category browse URL (keyword-less, sorted by newest) to catch generic-titled listings
    if browse_cat:
        browse_url = category_browse_url(
            browse_cat, max_price, min_price, condition, location, location_radius, custom_filters
        )
        url_entries.append({"url": browse_url, "product_name": f"[browse] {browse_cat.split('/')[-1]}"})
    db.add_search_urls(search_id, url_entries)

    await query.edit_message_text(
        f"✅ Cheap search '{data.get('name')}' started! "
        f"Watching {len(products)} products on OLX. Listings will arrive shortly."
    )
    await notify_admin(
        context,
        f"📋 Search created: *{data.get('name')}* (cheap, {len(products)} products) by `{chat_id}`",
        level="success",
    )
    return ConversationHandler.END


async def cmd_modify_cheap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle modification request for cheap mode product list."""
    modification = update.message.text.strip()
    data = context.user_data.get("cheap_data")
    original_query = context.user_data.get("cheap_original_query", "")
    lang = get_lang(update)

    from telegram.ext import ConversationHandler

    if not data:
        await update.message.reply_text(i18n.get_text(lang, "session_expired_add"))
        return ConversationHandler.END

    await update.message.reply_text(i18n.get_text(lang, "updating_products"))

    feedback = [{"listing_title": "N/A", "product": "N/A", "feedback": modification}]
    new_products = await run_cheap_feedback_llm(original_query, data.get("products", []), feedback)

    if not new_products:
        await update.message.reply_text(i18n.get_text(lang, "update_failed"))
        return MODIFY_CHEAP_QUERY

    data["products"] = new_products
    context.user_data["cheap_data"] = data
    await update.message.reply_text(
        build_cheap_confirmation(data, lang),
        reply_markup=_cheap_keyboard(lang),
        parse_mode="Markdown",
    )
    return CONFIRM_CHEAP_QUERY


# ============================================================================
# CANCEL
# ============================================================================


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("search_name", None)
    context.user_data.pop("slopsearch_data", None)
    context.user_data.pop("cheap_data", None)
    lang = get_lang(update)
    await update.message.reply_text(i18n.get_text(lang, "cancel_msg"))
    from telegram.ext import ConversationHandler

    return ConversationHandler.END


async def callback_stale_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch stale Approve/Modify/Cancel buttons from expired conversation sessions."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)
    await query.edit_message_text(query.message.text + f"\n\n{i18n.get_text(lang, 'cancel_msg')}")


# ============================================================================
# LIST
# ============================================================================


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/list — show all searches."""
    chat_id = str(update.message.chat_id)
    lang = get_lang(update)
    searches = db.get_searches_by_chat(chat_id)

    if not searches:
        await update.message.reply_text(i18n.get_text(lang, "list_empty"))
        return

    lines = [i18n.get_text(lang, "list_header")]

    monitors = [s for s in searches if s["mode"] == "monitor"]
    slops = [s for s in searches if s["mode"] == "slopsearch"]
    cheaps = [s for s in searches if s["mode"] == "cheap"]

    if monitors:
        lines.append("🔔 Monitor searches:")
        for s in monitors:
            lines.append(f"  • {s['name']}")
        lines.append("")

    if slops:
        emoji_map = {
            "pending_scrape": "⏳",
            "scraping": "🔍",
            "reviewing": "📬",
            "monitoring": "✅",
            "error": "❌",
        }
        lines.append("🎯 Slopsearches:")
        for s in slops:
            emoji = emoji_map.get(s.get("status", ""), "❓")
            status = s.get("status", "unknown")
            pending = db.count_search_listings(s["id"], "pending")
            accepted = db.count_search_listings(s["id"], "accepted")
            detail = ""
            if status == "reviewing":
                detail = f" — {pending} pending review"
            elif status == "monitoring":
                detail = f" — {accepted} accepted"
            elif status == "scraping":
                detail = " — scraping…"
            lines.append(f"  {emoji} {s['name']}{detail} (`{status}`)")
        lines.append("")

    if cheaps:
        lines.append("💸 Cheap searches:")
        for s in cheaps:
            n_products = len(s.get("products") or [])
            n_accepted = db.count_search_listings(s["id"], "accepted")
            feedback_count = len(db.get_feedback(s["id"]))
            detail = f" — {n_products} products, {n_accepted} approved"
            if feedback_count:
                detail += f", {feedback_count} feedback"
            lines.append(f"  💸 {s['name']}{detail}")

    await update.message.reply_html("\n".join(lines))


# ============================================================================
# DELETE
# ============================================================================


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete — show delete buttons."""
    chat_id = str(update.message.chat_id)
    lang = get_lang(update)
    searches = db.get_searches_by_chat(chat_id)

    if not searches:
        await update.message.reply_text(i18n.get_text(lang, "list_empty"))
        return

    mode_emoji = {"monitor": "🔔", "slopsearch": "🎯", "cheap": "💸"}
    keyboard = []
    for s in searches:
        emoji = mode_emoji.get(s["mode"], "❓")
        keyboard.append(
            [
                InlineKeyboardButton(
                    i18n.get_text(lang, "btn_delete_search", name=f"{emoji} {s['name']}"),
                    callback_data=f"del_{s['id']}",
                )
            ]
        )

    await update.message.reply_text(
        i18n.get_text(lang, "delete_prompt"),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle delete button press. CASCADE deletes all related data."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)

    search_id = int(query.data[4:])  # "del_123"
    search = db.get_search(search_id)
    name = search["name"] if search else "?"

    db.delete_search(search_id)
    await query.edit_message_text(i18n.get_text(lang, "delete_success", name=name))
    chat_id = str(query.message.chat_id)
    await notify_admin(context, f"🗑 Search deleted: *{name}* by `{chat_id}`", level="warning")


# ============================================================================
# EDIT (modify existing search parameters)
# ============================================================================


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/edit — pick a search to modify."""
    chat_id = str(update.message.chat_id)
    lang = get_lang(update)
    searches = db.get_searches_by_chat(chat_id)

    if not searches:
        await update.message.reply_text(i18n.get_text(lang, "list_empty"))
        from telegram.ext import ConversationHandler

        return ConversationHandler.END

    mode_emoji = {"monitor": "🔔", "slopsearch": "🎯", "cheap": "💸"}
    keyboard = []
    for s in searches:
        emoji = mode_emoji.get(s["mode"], "❓")
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{emoji} {s['name']}",
                    callback_data=f"edit_{s['id']}",
                )
            ]
        )

    await update.message.reply_text(
        i18n.get_text(lang, "edit_prompt"),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return EDIT_AWAIT_CHANGES


async def callback_edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User picked a search to edit — show current params and ask for changes."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update)

    search_id = int(query.data[5:])  # "edit_123"
    search = db.get_search(search_id)
    if not search:
        await query.edit_message_text(i18n.get_text(lang, "search_not_found"))
        from telegram.ext import ConversationHandler

        return ConversationHandler.END

    context.user_data["edit_search_id"] = search_id

    await query.edit_message_text(
        i18n.get_text(lang, "edit_what_prompt", name=search["name"], mode=search["mode"]), parse_mode="HTML"
    )
    return EDIT_AWAIT_CHANGES


async def handle_edit_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the user's edit description via LLM and update the search."""
    from telegram.ext import ConversationHandler

    lang = get_lang(update)

    search_id = context.user_data.get("edit_search_id")
    if not search_id:
        await update.message.reply_text(i18n.get_text(lang, "session_expired_edit"))
        return ConversationHandler.END

    search = db.get_search(search_id)
    if not search:
        await update.message.reply_text(i18n.get_text(lang, "search_not_found"))
        return ConversationHandler.END

    changes_text = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    await update.message.reply_text(i18n.get_text(lang, "applying_changes"))

    mode = search["mode"]

    if mode == "cheap":
        # Use cheap feedback LLM to update products, or parse direct param changes
        old_products = search.get("products") or []
        original_query = search.get("original_query") or search.get("name", "")
        old_max_price = search.get("max_price")
        old_min_price = search.get("min_price")
        old_location = search.get("location")
        old_location_radius = search.get("location_radius")

        # Ask LLM to interpret the changes
        result = await run_cheap_mode_llm(
            f"Original request: {original_query}\n"
            f"Current products: {old_products}\n"
            f"Current max_price: {old_max_price}\n"
            f"Current min_price: {old_min_price}\n"
            f"Current location: {old_location} (radius: {old_location_radius} km)\n\n"
            f"User wants to change: {changes_text}\n\n"
            f"Return updated parameters. Keep unchanged fields as they were."
        )

        if not result:
            await update.message.reply_text(i18n.get_text(lang, "update_failed_retry"))
            return ConversationHandler.END

        new_products = result.get("products", old_products)
        new_max_price = result.get("max_price", old_max_price)
        new_min_price = result.get("min_price", old_min_price)
        new_location = result.get("location", old_location)
        new_location_radius = result.get("location_radius", old_location_radius)
        new_name = result.get("name", search["name"])
        new_base_path = result.get("base_path", search.get("base_path"))
        new_condition = result.get("condition", search.get("condition"))
        new_browse_cat = result.get("browse_category", search.get("browse_category"))
        new_custom_filters = result.get("custom_filters") or _get_custom_filters(search)

        # Update DB
        db.update_search(
            search_id,
            name=new_name,
            products=new_products,
            max_price=new_max_price,
            min_price=new_min_price,
            location=new_location,
            location_radius=new_location_radius,
            base_path=new_base_path,
            condition=new_condition,
            browse_category=new_browse_cat,
            custom_filters=new_custom_filters,
        )

        # Regenerate URLs
        url_entries = [
            {
                "url": product_to_url(
                    p,
                    new_max_price,
                    new_location,
                    new_location_radius,
                    new_base_path,
                    new_condition,
                    new_min_price,
                    new_custom_filters,
                ),
                "product_name": p,
            }
            for p in new_products
        ]
        if new_name:
            broad_url = product_to_url(
                new_name,
                new_max_price,
                new_location,
                new_location_radius,
                new_base_path,
                new_condition,
                new_min_price,
                new_custom_filters,
            )
            url_entries.append({"url": broad_url, "product_name": f"[broad] {new_name}"})
        if new_browse_cat:
            browse_url = category_browse_url(
                new_browse_cat,
                new_max_price,
                new_min_price,
                new_condition,
                new_location,
                new_location_radius,
                new_custom_filters,
            )
            url_entries.append({"url": browse_url, "product_name": f"[browse] {new_browse_cat.split('/')[-1]}"})
        db.replace_search_urls(search_id, url_entries)

        # Clear seen_ids so new URLs get a fresh scrape
        db.clear_seen(search_id)

        # Summary
        loc_str = f"📍 {new_location} (+{new_location_radius} km)" if new_location else "🌍 Nationwide"
        price_str = _format_price_range(new_min_price, new_max_price)
        product_list = "\n".join(f"  • {p}" for p in new_products)
        await update.message.reply_text(
            f"✅ Updated *{new_name}*:\n\n"
            f"💰 {price_str}\n"
            f"{loc_str}\n"
            f"📦 Products:\n{product_list}\n\n"
            f"🔄 Seen listings cleared — fresh results on next cycle.",
            parse_mode="Markdown",
        )

    elif mode == "slopsearch":
        # Use slopsearch LLM to re-interpret
        existing = {
            "name": search.get("name"),
            "base_path": "oferty",
            "keyword": search.get("name", ""),
            "max_price": search.get("max_price"),
            "min_price": search.get("min_price"),
            "keywords": search.get("keywords") or [],
            "location": search.get("location"),
            "location_radius": search.get("location_radius"),
        }
        refined = await run_slopsearch_llm(changes_text, OLX_CATEGORIES, OLX_URL_CONTEXT, existing=existing)
        if not refined:
            await update.message.reply_text(i18n.get_text(lang, "update_failed_generic"))
            return ConversationHandler.END

        raw_url = assemble_url(
            refined.get("base_path", "oferty"),
            refined.get("keyword", refined.get("name", "oferty")),
            refined.get("max_price"),
            refined.get("condition"),
            refined.get("location"),
            refined.get("location_radius"),
            refined.get("min_price"),
        )
        validated_url, _ = await validate_and_correct_url(
            raw_url,
            refined.get("keyword", ""),
            {"name": refined.get("name"), "max_price": refined.get("max_price")},
        )

        db.update_search(
            search_id,
            name=refined.get("name", search["name"]),
            url=validated_url,
            max_price=refined.get("max_price"),
            min_price=refined.get("min_price"),
            keywords=refined.get("keywords", []),
            location=refined.get("location"),
            location_radius=refined.get("location_radius"),
        )
        db.clear_seen(search_id)

        await update.message.reply_text(
            f"✅ Updated *{refined.get('name', search['name'])}*\n"
            f"🔗 {validated_url}\n\n"
            f"🔄 Seen listings cleared — fresh results on next cycle.",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    else:
        # monitor mode — just update the URL if they give one
        await update.message.reply_text(
            "Monitor mode searches can be deleted and re-added with /add.\n"
            "Use /delete to remove, then /add to create a new one."
        )

    await notify_admin(context, f"✏️ Search edited: *{search.get('name', '?')}* ({mode}) by `{chat_id}`", level="info")
    context.user_data.pop("edit_search_id", None)
    return ConversationHandler.END


# ============================================================================
# RESUME (slopsearch review)
# ============================================================================


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resume — review pending slopsearch listings."""
    chat_id = str(update.message.chat_id)
    slopsearches = db.get_searches_by_chat(chat_id, mode="slopsearch")
    lang = get_lang(update)

    for search in slopsearches:
        if search["status"] == "reviewing":
            pending = db.count_search_listings(search["id"], "pending")
            if pending > 0:
                await _send_next_review(update.message, search)
                return

    await update.message.reply_text(i18n.get_text(lang, "no_pending_items"))


async def _send_next_review(message_obj, search: dict) -> None:
    """Send the next pending review item."""
    listing = db.get_next_pending_listing(search["id"])
    if not listing:
        await _finalize_review(message_obj.get_bot(), search)
        return

    pending_count = db.count_search_listings(search["id"], "pending")

    keyboard = [
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"rev_acc_{search['id']}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"rev_dec_{search['id']}"),
        ],
        [
            InlineKeyboardButton("💬 Decline + Feedback", callback_data=f"rev_dfb_{search['id']}"),
            InlineKeyboardButton("🏁 Finish Review", callback_data=f"rev_fin_{search['id']}"),
        ],
    ]

    text = format_review_item(search["name"], pending_count, listing)
    await message_obj.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
        disable_web_page_preview=False,
    )


async def callback_review_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Accept/Decline/DeclineWithFeedback/Finish during slopsearch review."""
    query = update.callback_query
    lang = get_lang(update)

    # Format: rev_acc_123, rev_dec_123, rev_dfb_123, rev_fin_123
    action = query.data[4:7]  # acc, dec, dfb, fin
    search_id = int(query.data[8:])

    search = db.get_search(search_id)
    if not search or search["status"] != "reviewing":
        await query.answer(i18n.get_text(lang, "review_expired"), show_alert=True)
        return

    if action == "fin":
        await query.answer(i18n.get_text(lang, "review_finishing"))
        await query.edit_message_reply_markup(reply_markup=None)
        await _finalize_review(context.bot, search)
        return

    listing = db.get_next_pending_listing(search_id)
    if not listing:
        await query.answer(i18n.get_text(lang, "review_no_items"))
        return

    if action == "acc":
        db.update_search_listing(search_id, listing["listing_id"], status="accepted")
        await query.answer(i18n.get_text(lang, "review_accepted"))
        await query.edit_message_text(
            i18n.get_text(lang, "review_accepted_title", title=listing["title"]), parse_mode="Markdown"
        )

        next_listing = db.get_next_pending_listing(search_id)
        if next_listing:
            await _send_next_review(query.message, search)
        else:
            await _finalize_review(context.bot, search)

    elif action == "dec":
        db.update_search_listing(search_id, listing["listing_id"], status="declined")
        await query.answer(i18n.get_text(lang, "review_declined"))
        await query.edit_message_text(
            i18n.get_text(lang, "review_declined_title", title=listing["title"]), parse_mode="Markdown"
        )

        next_listing = db.get_next_pending_listing(search_id)
        if next_listing:
            await _send_next_review(query.message, search)
        else:
            await _finalize_review(context.bot, search)

    elif action == "dfb":
        await query.answer(i18n.get_text(lang, "review_tell_why"))
        await query.edit_message_text(
            f"💬 *Why are you declining this?*\n_{listing['title']}_\n\n"
            f"Type your feedback (e.g. 'wrong switches', 'too big', 'want wireless'):",
            parse_mode="Markdown",
        )
        context.user_data["awaiting_decline_feedback"] = {
            "search_id": search_id,
            "listing_id": listing["listing_id"],
        }


async def handle_decline_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text feedback after 'Decline + Feedback' button."""
    pending = context.user_data.get("awaiting_decline_feedback")
    if not pending:
        return

    lang = get_lang(update)
    feedback_text = update.message.text.strip()
    search_id = pending["search_id"]
    listing_id = pending["listing_id"]
    context.user_data.pop("awaiting_decline_feedback", None)

    search = db.get_search(search_id)
    if not search:
        await update.message.reply_text(i18n.get_text(lang, "session_expired_review"))
        return

    db.update_search_listing(search_id, listing_id, status="declined", decline_feedback=feedback_text)
    db.add_feedback(search_id, "", "", feedback_text)

    await update.message.reply_text(
        i18n.get_text(lang, "decline_feedback_msg", feedback=feedback_text), parse_mode="Markdown"
    )

    next_listing = db.get_next_pending_listing(search_id)
    if next_listing:
        await _send_next_review(update.message, search)
    else:
        await _finalize_review(update.message.get_bot(), search)


async def _finalize_review(bot, search: dict) -> None:
    """Finalize slopsearch review: transition to monitoring, send summary."""
    search_id = search["id"]
    chat_id = search["chat_id"]

    db.update_search_status(search_id, "monitoring")

    accepted = db.get_search_listings(search_id, status="accepted")

    if not accepted:
        await bot.send_message(
            chat_id=int(chat_id),
            text=f"🏁 Review finished for '{search['name']}'. No items were accepted.\nNow monitoring for new matches.",
        )
        return

    prices = [parse_price(item.get("price", "")) for item in accepted]
    prices = [p for p in prices if p > 0]

    text = format_market_summary(search["name"], len(accepted), prices)
    await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")


# ============================================================================
# CHEAP MODE CALLBACKS
# ============================================================================


async def callback_cheap_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Approve All / Skip All buttons on grouped cheap mode messages (ca_ / cs_)."""
    query = update.callback_query
    await query.answer()  # answer immediately to avoid Telegram timeout

    action = query.data[:2]  # 'ca' or 'cs'
    rest = query.data[3:]  # search_id_product_hash
    last_us = rest.rfind("_")
    if last_us == -1:
        return
    search_id = int(rest[:last_us])

    search = db.get_search(search_id)
    if not search:
        return

    msg_id = str(query.message.message_id)
    chat_id = str(query.message.chat_id)
    associated = db.get_sent_messages_by_msg_id(msg_id, chat_id)

    if action == "ca":
        for sm in associated:
            db.update_search_listing(search_id, sm["listing_id"], status="accepted")

        accepted = db.get_search_listings(search_id, status="accepted")
        stats_line = cheap_price_stats(accepted)
        stats_suffix = f"\n{stats_line}" if stats_line else ""

        with contextlib.suppress(Exception):
            await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=int(chat_id),
            text=f"✅ *Approved All ({len(associated)})*{stats_suffix}",
            parse_mode="Markdown",
            reply_to_message_id=int(msg_id),
        )

    elif action == "cs":
        for sm in associated:
            db.update_search_listing(search_id, sm["listing_id"], status="declined")
        with contextlib.suppress(Exception):
            await query.edit_message_reply_markup(reply_markup=None)


# ============================================================================
# CHEAP MODE FEEDBACK REPLY
# ============================================================================


async def handle_feedback_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect replies to cheap mode listing messages and trigger product list refinement."""
    msg = update.message
    if not msg.reply_to_message:
        return

    lang = get_lang(update)
    replied_id = str(msg.reply_to_message.message_id)
    chat_id = str(msg.chat_id)
    feedback_text = msg.text.strip()

    sent = db.lookup_sent_message(replied_id, chat_id)
    if not sent:
        return

    search_id = sent["search_id"]
    search = db.get_search(search_id)
    if not search:
        return

    product = sent.get("product_name", "")
    listing_title = sent.get("title", "?")

    db.add_feedback(search_id, listing_title, product, feedback_text)

    await msg.reply_text(i18n.get_text(lang, "refining_feedback"))

    feedback_history = db.get_feedback(search_id)
    fb_list = [
        {"listing_title": f["listing_title"], "product": f["product"], "feedback": f["feedback"]}
        for f in feedback_history
    ]

    new_products = await run_cheap_feedback_llm(
        search.get("original_query", ""),
        search.get("products") or [],
        fb_list,
    )

    if not new_products:
        await msg.reply_text(i18n.get_text(lang, "refine_failed"))
        return

    max_price = search.get("max_price")
    min_price = search.get("min_price")
    location = search.get("location")
    location_radius = search.get("location_radius")
    base_path = search.get("base_path")
    condition = search.get("condition")
    custom_filters = _get_custom_filters(search)
    db.update_search(search_id, products=new_products)
    db.replace_search_urls(
        search_id,
        [
            {
                "url": product_to_url(
                    p, max_price, location, location_radius, base_path, condition, min_price, custom_filters
                ),
                "product_name": p,
            }
            for p in new_products
        ],
    )

    product_list = "\n".join(f"  • {p}" for p in new_products)
    await msg.reply_text(
        f"✅ Updated product list for *{search['name']}*:\n{product_list}",
        parse_mode="Markdown",
    )


# ============================================================================
# /ADVISOR — SEARCH ADVISOR
# ============================================================================


async def cmd_advisor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the search advisor for a specific search."""
    chat_id = str(update.effective_chat.id)
    i18n.get_language(chat_id)

    searches = db.get_searches_by_chat(chat_id)
    active = [s for s in searches if s["status"] in ("monitoring", "reviewing")]

    if not active:
        await update.message.reply_text("No active searches to advise on.")
        return

    if len(active) == 1:
        await _run_advisor(update, context, active[0]["id"])
        return

    # Multiple searches — let user pick
    keyboard = [[InlineKeyboardButton(s["name"], callback_data=f"adv_{s['id']}")] for s in active]
    await update.message.reply_text(
        "Which search do you want advice on?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_advisor_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle search selection for advisor."""
    query = update.callback_query
    await query.answer()
    search_id = int(query.data.split("_")[1])
    await _run_advisor(update, context, search_id)


async def _run_advisor(update: Update, context: ContextTypes.DEFAULT_TYPE, search_id: int) -> None:
    """Execute the advisor pipeline and show results."""
    from advisor import generate_advice
    from formatters import format_advisor_report

    str(update.effective_chat.id)

    # Send "working" indicator
    msg = update.message or update.callback_query.message
    status_msg = await msg.reply_text("🔍 Probing OLX and analyzing your search... (this takes ~30s)")

    advice = await generate_advice(search_id)

    if "error" in advice:
        await status_msg.edit_text(f"❌ Advisor error: {advice['error']}")
        return

    report = format_advisor_report(advice)
    suggestions = advice.get("suggestions", [])

    # Build apply buttons for actionable suggestions
    keyboard = []
    for i, s in enumerate(suggestions):
        stype = s.get("type", "")
        s.get("value", "")
        label = s.get("label", "?")
        if stype in ("add_product", "remove_product", "raise_price", "expand_location"):
            callback = f"advapply_{search_id}_{i}"
            keyboard.append([InlineKeyboardButton(f"✅ Apply: {label}", callback_data=callback)])

    # Store suggestions in context for callback
    context.chat_data[f"advisor_{search_id}"] = {
        "suggestions": suggestions,
        "search": advice["search"],
    }

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=markup)


async def callback_advisor_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply a single advisor suggestion."""
    from url_builder import category_browse_url, product_to_url

    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")  # advapply_searchid_idx
    search_id = int(parts[1])
    idx = int(parts[2])

    stored = context.chat_data.get(f"advisor_{search_id}")
    if not stored:
        await query.message.reply_text("⚠️ Advisor session expired. Run /advisor again.")
        return

    suggestions = stored["suggestions"]
    if idx >= len(suggestions):
        return

    suggestion = suggestions[idx]
    search = db.get_search(search_id)
    if not search:
        await query.message.reply_text("⚠️ Search not found.")
        return

    stype = suggestion.get("type")
    value = suggestion.get("value")
    products = json.loads(search.get("products") or "[]")
    max_price = search.get("max_price")
    min_price = search.get("min_price")
    location = search.get("location")
    location_radius = search.get("location_radius")
    base_path = search.get("base_path")
    condition = search.get("condition")
    browse_category = search.get("browse_category")
    custom_filters = _get_custom_filters(search)

    result_text = ""

    if stype == "add_product" and value and value not in products:
        products.append(value)
        db.update_search(search_id, products=products)
        # Add new URL
        new_url = product_to_url(
            value, max_price, location, location_radius, base_path, condition, min_price, custom_filters
        )
        db.add_search_url(search_id, new_url, value)
        result_text = f"➕ Added *{value}* to product list."

    elif stype == "remove_product" and value:
        products = [p for p in products if p != value]
        db.update_search(search_id, products=products)
        # Remove URL for that product
        urls = db.get_search_urls(search_id)
        for u in urls:
            if u.get("product_name") == value:
                db.delete_search_url(u["id"])
        result_text = f"➖ Removed *{value}* from product list."

    elif stype == "raise_price" and value:
        new_price = int(value)
        db.update_search(search_id, max_price=new_price)
        # Regenerate all URLs with new price
        urls = db.get_search_urls(search_id)
        for u in urls:
            pname = u.get("product_name", "")
            if pname.startswith("[browse]"):
                new_url = category_browse_url(
                    browse_category or base_path,
                    max_price=new_price,
                    min_price=min_price,
                    condition=condition,
                    location=location,
                    location_radius=location_radius,
                    custom_filters=custom_filters,
                )
            elif pname.startswith("[broad]"):
                broad_kw = pname.replace("[broad] ", "")
                new_url = product_to_url(
                    broad_kw, new_price, location, location_radius, base_path, condition, min_price, custom_filters
                )
            else:
                new_url = product_to_url(
                    pname, new_price, location, location_radius, base_path, condition, min_price, custom_filters
                )
            db.update_search_url(u["id"], url=new_url)
        result_text = f"💰 Max price raised to *{new_price} PLN*."

    elif stype == "expand_location" and value:
        db.update_search(search_id, location=value, location_radius=None)
        # Regenerate all URLs with new location
        urls = db.get_search_urls(search_id)
        for u in urls:
            pname = u.get("product_name", "")
            if pname.startswith("[browse]"):
                new_url = category_browse_url(
                    browse_category or base_path,
                    max_price=max_price,
                    min_price=min_price,
                    condition=condition,
                    location=value,
                    location_radius=None,
                    custom_filters=custom_filters,
                )
            elif pname.startswith("[broad]"):
                broad_kw = pname.replace("[broad] ", "")
                new_url = product_to_url(
                    broad_kw, max_price, value, None, base_path, condition, min_price, custom_filters
                )
            else:
                new_url = product_to_url(pname, max_price, value, None, base_path, condition, min_price, custom_filters)
            db.update_search_url(u["id"], url=new_url)
        result_text = f"📍 Location expanded to *{value}*."

    else:
        result_text = "⚠️ Could not apply this suggestion."

    # Mark suggestion as applied
    suggestion["applied"] = True

    # Update the message — grey out applied button
    keyboard = []
    for i, s in enumerate(suggestions):
        label = s.get("label", "?")
        if s.get("applied"):
            keyboard.append([InlineKeyboardButton(f"✅ {label} (applied)", callback_data="noop")])
        else:
            stype_i = s.get("type", "")
            if stype_i in ("add_product", "remove_product", "raise_price", "expand_location"):
                keyboard.append([InlineKeyboardButton(f"✅ Apply: {label}", callback_data=f"advapply_{search_id}_{i}")])

    with contextlib.suppress(Exception):
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

    await query.message.reply_text(result_text, parse_mode="Markdown")
