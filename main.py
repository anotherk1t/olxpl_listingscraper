"""
OLX Poland Listing Scraper — Telegram Bot
Real-time marketplace monitoring with AI-powered filtering.
"""

import logging
import warnings

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

import db
from admin import cmd_admin, cmd_health
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
    TOKEN,
)
from handlers import (
    callback_add_mode,
    callback_advisor_apply,
    callback_advisor_pick,
    callback_cheap_review,
    callback_confirm_cheap,
    callback_confirm_slopsearch,
    callback_delete,
    callback_edit_pick,
    callback_language,
    callback_review_item,
    callback_stale_conversation,
    cmd_add_cheap_query,
    cmd_add_name,
    cmd_add_slopsearch_query,
    cmd_add_start,
    cmd_add_url,
    cmd_advisor,
    cmd_cancel,
    cmd_delete,
    cmd_edit,
    cmd_language,
    cmd_list,
    cmd_modify_cheap,
    cmd_modify_slopsearch,
    cmd_resume,
    cmd_slopgest,
    cmd_start,
    handle_decline_feedback,
    handle_edit_changes,
    handle_feedback_reply,
)
from jobs import detect_sold, scrape_all

warnings.filterwarnings("ignore", category=PTBUserWarning)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Quiet noisy loggers (getUpdates every 10s floods the output)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._updater").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    """Initialize and run the bot."""
    db.init_db()
    logger.info("Database initialized")

    app = Application.builder().token(TOKEN).build()

    # Conversation handler for /add
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add_start)],
        states={
            ASK_MODE: [CallbackQueryHandler(callback_add_mode, pattern="^addmode_")],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_add_name)],
            ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_add_url)],
            ASK_SLOPSEARCH_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_add_slopsearch_query),
            ],
            CONFIRM_SLOPSEARCH_QUERY: [
                CallbackQueryHandler(callback_confirm_slopsearch, pattern="^slopsearch_"),
            ],
            MODIFY_SLOPSEARCH_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_modify_slopsearch),
            ],
            ASK_CHEAP_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_add_cheap_query),
            ],
            CONFIRM_CHEAP_QUERY: [
                CallbackQueryHandler(callback_confirm_cheap, pattern="^cheap_"),
            ],
            MODIFY_CHEAP_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_modify_cheap),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )

    # Conversation handler for /edit
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", cmd_edit)],
        states={
            EDIT_AWAIT_CHANGES: [
                CallbackQueryHandler(callback_edit_pick, pattern="^edit_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_changes),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CallbackQueryHandler(callback_language, pattern="^lang_"))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("slopgest", cmd_slopgest))
    app.add_handler(CommandHandler("advisor", cmd_advisor))
    app.add_handler(CallbackQueryHandler(callback_advisor_pick, pattern="^adv_"))
    app.add_handler(CallbackQueryHandler(callback_advisor_apply, pattern="^advapply_"))
    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CallbackQueryHandler(callback_delete, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(callback_review_item, pattern="^rev_"))
    app.add_handler(CallbackQueryHandler(callback_cheap_review, pattern="^c[as]_"))
    # Catch stale conversation buttons after restart
    app.add_handler(CallbackQueryHandler(callback_stale_conversation, pattern="^(cheap_|slopsearch_|edit_)"))

    # Decline-with-feedback text input (group 1 = checked before generic)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_decline_feedback),
        group=1,
    )
    # Feedback reply handler for cheap mode
    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, handle_feedback_reply),
    )

    # Schedule background jobs
    jq = app.job_queue
    jq.run_repeating(scrape_all, interval=CONFIG.CHECK_INTERVAL, first=10)
    jq.run_repeating(detect_sold, interval=CONFIG.MONITOR_INTERVAL, first=60)

    # Send startup notification
    async def _on_startup(app):
        from admin import notify_admin_raw

        await notify_admin_raw(app.bot, "🚀 Bot started successfully")

    app.post_init = _on_startup

    # Start bot
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
