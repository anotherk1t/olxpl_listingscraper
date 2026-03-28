"""
OLX Scraper — Admin Notifications & Health Monitoring

All admin features are gated by ADMIN_CHAT_ID from .env.
Non-admin users get no response (silent ignore).
"""

import logging
import os
import time

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_CHAT_ID, CONFIG

logger = logging.getLogger(__name__)

# ============================================================================
# STATE
# ============================================================================

_bot_start_time: float = time.time()
_last_scrape_time: float | None = None
_last_scrape_duration: float | None = None
_last_error: str | None = None
_recent_errors: dict[str, float] = {}  # msg_hash -> timestamp (for dedup)
_admin_prefs: dict[str, bool] = {"verbose_logs": False, "errors_only": True}

ERROR_DEDUP_WINDOW = 300  # 5 minutes


def _is_admin(chat_id) -> bool:
    return ADMIN_CHAT_ID and str(chat_id) == str(ADMIN_CHAT_ID)


# ============================================================================
# CORE: notify_admin
# ============================================================================

_LEVEL_EMOJI = {
    "info": "🔵",
    "warning": "🟡",
    "error": "🔴",
    "success": "🟢",
}


async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str, level: str = "info") -> None:
    """Send a notification to the admin chat. Rate-limits duplicate errors."""
    if not ADMIN_CHAT_ID:
        return
    await _send_admin_message(context.bot, message, level)


async def notify_admin_raw(bot, message: str, level: str = "info") -> None:
    """Send admin notification using a Bot instance directly (for startup/shutdown)."""
    if not ADMIN_CHAT_ID:
        return
    await _send_admin_message(bot, message, level)


async def _send_admin_message(bot, message: str, level: str) -> None:
    """Internal: send formatted admin message."""
    # Skip info messages if errors_only mode and not verbose
    if level == "info" and _admin_prefs.get("errors_only") and not _admin_prefs.get("verbose_logs"):
        return

    # Dedup errors within window
    if level == "error":
        global _last_error
        _last_error = message
        msg_key = str(hash(message))
        now = time.time()
        if msg_key in _recent_errors and now - _recent_errors[msg_key] < ERROR_DEDUP_WINDOW:
            return
        _recent_errors[msg_key] = now
        # Prune old entries
        _recent_errors.update({k: v for k, v in _recent_errors.items() if now - v < ERROR_DEDUP_WINDOW})

    emoji = _LEVEL_EMOJI.get(level, "⚪")
    formatted = f"{emoji} *{level.upper()}*\n{message}"

    try:
        await bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=formatted,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning(f"Failed to send admin notification: {e}")


# ============================================================================
# SCRAPE TRACKING
# ============================================================================


def record_scrape(duration: float) -> None:
    """Record the last scrape cycle time and duration."""
    global _last_scrape_time, _last_scrape_duration
    _last_scrape_time = time.time()
    _last_scrape_duration = duration


# ============================================================================
# /health COMMAND
# ============================================================================


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report bot health stats. Admin-only."""
    if not _is_admin(update.effective_chat.id):
        return

    import db

    uptime = time.time() - _bot_start_time
    hours, remainder = divmod(int(uptime), 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count active searches by mode
    searches = db.get_active_searches()
    mode_counts = {}
    for s in searches:
        mode_counts[s["mode"]] = mode_counts.get(s["mode"], 0) + 1

    search_lines = "\n".join(f"  {m}: {c}" for m, c in sorted(mode_counts.items())) or "  None"

    # Last scrape
    if _last_scrape_time:
        ago = int(time.time() - _last_scrape_time)
        scrape_str = f"{ago}s ago ({_last_scrape_duration:.1f}s duration)" if _last_scrape_duration else f"{ago}s ago"
    else:
        scrape_str = "Not yet"

    # Copilot CLI status
    import subprocess as _sp

    try:
        r = _sp.run(["copilot", "--version"], capture_output=True, text=True, timeout=5)
        llm_status = f"✅ {r.stdout.strip()}" if r.returncode == 0 else "⚠️ CLI error"
    except FileNotFoundError:
        llm_status = "❌ Not installed"
    except Exception:
        llm_status = "❌ Unreachable"

    # DB size
    db_path = CONFIG.DB_PATH
    try:
        db_size = os.path.getsize(db_path) / 1024
        db_str = f"{db_size:.0f} KB"
    except Exception:
        db_str = "Unknown"

    # Memory
    try:
        import resource

        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        mem_str = f"{mem_mb:.1f} MB"
    except Exception:
        mem_str = "N/A"

    error_str = f"```\n{_last_error[:200]}\n```" if _last_error else "None"

    text = (
        f"🏥 *Bot Health*\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"📊 Active searches:\n{search_lines}\n"
        f"🔄 Last scrape: {scrape_str}\n"
        f"🌐 LLM: {llm_status}\n"
        f"💾 DB: {db_str}\n"
        f"🧠 Memory: {mem_str}\n"
        f"❌ Last error: {error_str}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ============================================================================
# /admin COMMAND
# ============================================================================


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin toggle commands. Admin-only."""
    if not _is_admin(update.effective_chat.id):
        return

    args = context.args or []
    if not args:
        status = (
            f"⚙️ *Admin Settings*\n\n"
            f"Verbose logs: {'✅' if _admin_prefs['verbose_logs'] else '❌'}\n"
            f"Errors only: {'✅' if _admin_prefs['errors_only'] else '❌'}\n\n"
            f"Commands:\n"
            f"`/admin logs` — toggle verbose cycle logs\n"
            f"`/admin errors` — toggle error-only mode\n"
            f"`/admin status` — show health"
        )
        await update.message.reply_text(status, parse_mode="Markdown")
        return

    subcmd = args[0].lower()
    if subcmd == "logs":
        _admin_prefs["verbose_logs"] = not _admin_prefs["verbose_logs"]
        state = "enabled" if _admin_prefs["verbose_logs"] else "disabled"
        await update.message.reply_text(f"📋 Verbose logs: {state}")
    elif subcmd == "errors":
        _admin_prefs["errors_only"] = not _admin_prefs["errors_only"]
        state = "enabled" if _admin_prefs["errors_only"] else "disabled"
        await update.message.reply_text(f"🔴 Errors-only mode: {state}")
    elif subcmd == "status":
        await cmd_health(update, context)
    else:
        await update.message.reply_text("Unknown admin command. Use: `logs`, `errors`, `status`", parse_mode="Markdown")
