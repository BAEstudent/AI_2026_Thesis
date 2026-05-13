"""Entry point – builds and runs the Telegram bot application."""
import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import settings
from bot.handlers.start import start_command, help_command, menu_callback
from bot.handlers.forecasts import (
    granularity_callback,
    frequency_callback,
    category_selected_callback,
    item_search_handler,
    item_select_callback,
)
from bot.handlers.display import display_callback
from bot.handlers.refresh import refresh_callback
from bot.handlers.metrics import metrics_callback
from bot.handlers.text2sql import text2sql_message_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _build_app() -> Application:
    builder = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN)

    if settings.TELEGRAM_PROXY:
        builder.proxy(settings.TELEGRAM_PROXY)
        logger.info(f"Using proxy: {settings.TELEGRAM_PROXY}")

    app: Application = builder.build()

    # ── Commands ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # ── Callbacks ─────────────────────────────────────────────────────────
    # Main menu
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))

    # Forecast granularity
    app.add_handler(CallbackQueryHandler(granularity_callback, pattern=r"^fc:gran:"))

    # Frequency selection
    app.add_handler(CallbackQueryHandler(frequency_callback, pattern=r"^fc:freq:"))

    # Category selected
    app.add_handler(CallbackQueryHandler(category_selected_callback, pattern=r"^fc:cat:"))

    # Item selection from search results
    app.add_handler(CallbackQueryHandler(item_select_callback, pattern=r"^fc:item:select:"))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text2sql_message_handler,
        ),
        group=0,
    )

    # Item actions: view, metrics, refresh
    app.add_handler(CallbackQueryHandler(display_callback, pattern=r"^fc:disp:"))
    app.add_handler(CallbackQueryHandler(metrics_callback, pattern=r"^fc:item:metrics:"))
    app.add_handler(CallbackQueryHandler(metrics_callback, pattern=r"^fc:metrics:"))
    app.add_handler(CallbackQueryHandler(refresh_callback, pattern=r"^fc:item:refresh:"))

    # ── Text messages (item search) ────────────────────────────────────────
    # Only process text when user is in item-search mode; fallback otherwise
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            item_search_handler,
        ),
        group=1,
    )

    # ── Error handler ─────────────────────────────────────────────────────
    async def error_handler(update: Update, context) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Please try /start again."
            )

    app.add_error_handler(error_handler)

    return app


def main() -> None:
    app = _build_app()
    logger.info("Bot starting polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
