"""Text‑to‑SQL handler – turns natural language into SQL and runs it."""
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu
from bot.services.text2sql_api import text2sql_api, Text2SQLAPIError


async def text2sql_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle a text message when the user is in 'text2sql' mode.
    Sends the question to the TriSQL service and returns the result.
    """
    if not context.user_data.get("text2sql_mode"):
        return  # not our turn

    # Clear the mode so it's only a one-shot question
    context.user_data["text2sql_mode"] = False

    question = update.message.text.strip()
    await update.message.reply_chat_action("typing")

    try:
        result = await text2sql_api.ask_question(question)
    except Text2SQLAPIError as exc:
        await update.message.reply_text(
            f"❌ *Text‑to‑SQL error*\n```{exc}```",
            parse_mode="Markdown",
        )
        return

    sql = result.get("sql", "")
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    row_count = result.get("row_count", 0)

    # Build a displayable answer
    lines = [
        f"💬 *Your question:*\n{question}\n",
        f"🤖 *Generated SQL:*\n```sql\n{sql}\n```\n",
        f"📊 *Results ({row_count} rows)*\n",
    ]

    if rows:
        # Format as a small Markdown table (monospace)
        table = "```\n"
        # header
        table += " | ".join(columns) + "\n"
        table += "-" * (sum(len(c) for c in columns) + 3 * (len(columns)-1)) + "\n"
        # rows (limit to first 20 to avoid huge messages)
        for row in rows[:20]:
            table += " | ".join(str(val) for val in row) + "\n"
        if len(rows) > 20:
            table += f"... and {len(rows) - 20} more rows\n"
        table += "```"
        lines.append(table)
    else:
        lines.append("_No rows returned._")

    text = "\n".join(lines)

    # Telegram messages have a 4096 char limit; truncate if needed
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (truncated)"

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )
