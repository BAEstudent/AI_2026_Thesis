"""Model comparison (metrics) handler."""
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    global_display_menu,
    category_display_menu,
    item_action_menu,
    forecast_granularity_menu,
)
from bot.services.forecast_api import forecast_api, ForecastAPIError


async def metrics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:item:metrics:* and fc:metrics:* callbacks."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    # formats:
    #   fc:item:metrics:<item_id>:<freq>
    #   fc:metrics:global:global:<freq>
    #   fc:metrics:category:<category>:<freq>
    if parts[1] == "item":
        granularity = "item"
        series_id = parts[3]
        freq = parts[4]
    else:
        granularity = parts[2]
        series_id = parts[3]
        freq = parts[4]

    try:
        rows = await forecast_api.get_metrics(granularity, series_id, freq=freq)
    except ForecastAPIError as exc:
        await query.edit_message_text(
            f"❌ Error loading metrics:\n```{exc}```",
            parse_mode="Markdown",
        )
        return

    if not rows:
        await query.edit_message_text(
            "No metrics found. Run the forecast DAG first.",
            reply_markup=forecast_granularity_menu(),
        )
        return

    lines = [
        f"📊 *Model Scores* – `{series_id}` ({freq})\n",
        "```",
        f"{'Model':<16} {'MAE':>8} {'RMSE':>8} {'MAPE':>8} {'SMAPE':>8}",
        "-" * 56,
    ]
    for r in rows:
        lines.append(
            f"{r['model']:<16} {r['MAE']:>8.2f} {r['RMSE']:>8.2f} "
            f"{r.get('MAPE', 0):>8.2f} {r.get('SMAPE', 0):>8.2f}"
        )
    lines.append("```")
    text = "\n".join(lines)

    # Choose correct back menu
    if granularity == "global":
        reply_markup = global_display_menu(freq)
    elif granularity == "category":
        reply_markup = category_display_menu(series_id, freq)
    else:
        reply_markup = item_action_menu(series_id, freq)

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
