"""On-demand retrain handler."""
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import item_action_menu, forecast_granularity_menu
from bot.services.forecast_api import forecast_api, ForecastAPIError


async def refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:item:refresh:* callbacks."""
    query = update.callback_query
    await query.answer("⏳ Retraining… this may take 5–15s.")

    parts = query.data.split(":")
    item_id = parts[3]
    freq = parts[4]

    try:
        result = await forecast_api.refresh_item_forecast(item_id, freq=freq)
    except ForecastAPIError as exc:
        await query.edit_message_text(
            f"❌ Retrain failed:\n```{exc}```",
            parse_mode="Markdown",
            reply_markup=item_action_menu(item_id, freq),
        )
        return

    points = result.get("points", [])
    if not points:
        await query.edit_message_text(
            "⚠️ Retrain returned empty forecast.",
            reply_markup=item_action_menu(item_id, freq),
        )
        return

    # Build quick summary
    first_ds = points[0]["ds"]
    last_ds = points[-1]["ds"]
    model = result.get("model", "Unknown")

    text = (
        f"✅ *Retrained successfully*\n\n"
        f"📦 Item: `{item_id}`\n"
        f"📅 Freq: {freq}\n"
        f"🤖 Model: {model}\n"
        f"📊 Horizon: {first_ds} → {last_ds}\n"
        f"🕐 Computed at: {result.get('computed_at', 'N/A')}\n\n"
        "Use 📈 View Forecast to see the full chart/table."
    )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=item_action_menu(item_id, freq)
    )
