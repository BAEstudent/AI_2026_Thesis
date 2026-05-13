"""Start handler and main menu routing."""
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu, forecast_granularity_menu


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start."""
    text = (
        "👋 Welcome to *Seller Analytics Bot*!\n\n"
        "I help you view forecasts, detect anomalies, and ask questions about your data.\n\n"
        "Choose an option below:"
    )
    await update.effective_message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help."""
    text = (
        "*Available commands:*\n"
        "/start – Open main menu\n"
        "/help  – Show this message\n\n"
        "*Navigation tips:*\n"
        "• Use the inline buttons to move between menus\n"
        "• Forecasts are available at *global*, *category*, and *item* levels\n"
        "• You can view numbers, charts, and model comparison scores\n"
        "• Retrain a model on-the-fly if you need fresh predictions\n\n"
        "_Data is refreshed nightly by Airflow._"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route main-menu callback queries."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu:main":
        await query.edit_message_text(
            "📊 *Main Menu*\nChoose an option:",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
    elif data == "menu:forecasts":
        await query.edit_message_text(
            "🔮 *Forecasts*\nSelect granularity:",
            parse_mode="Markdown",
            reply_markup=forecast_granularity_menu(),
        )
    elif data == "menu:anomalies":
        await query.edit_message_text(
            "⚠️ *Anomalies*\n\n"
            "Anomaly detection is coming soon!\n"
            "You will be able to subscribe to alerts here.",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
    elif data == "menu:text2sql":
        await query.edit_message_text(
            "💬 *Ask Data*\n\n"
            "Text-to-SQL is coming soon!\n"
            "You will be able to ask questions like:\n"
            "_\"What were top 5 items last week?\"_",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
    elif data == "menu:help":
        await query.edit_message_text(
            "*Help*\n\nUse /start to open the main menu at any time.",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
    elif data == "menu:text2sql":
        # Set a flag so the next text message is treated as a question
        context.user_data["text2sql_mode"] = True
        await query.edit_message_text(
            "💬 *Ask Data*\n\n"
            "Type your question in plain English.\n"
            "Examples:\n"
            "• _\"How many orders were placed yesterday?\"_\n"
            "• _\"Show top 5 selling items this month\"_\n"
            "• _\"What is the average price per category?\"_\n\n"
            "Send your question now.",
            parse_mode="Markdown",
            reply_markup=main_menu(),  # user can still go back
        )
