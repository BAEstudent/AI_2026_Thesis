"""Display forecast as table text or chart image."""
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from telegram import Update, InputFile
from telegram.ext import ContextTypes

from bot.keyboards import (
    forecast_display_menu,
    global_display_menu,
    category_display_menu,
)
from bot.services.forecast_api import forecast_api, ForecastAPIError


# ─────────────────────────────────────────────────────────────────────────────
# Chart generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_forecast_chart(
    results: list[dict],
    title: str,
    xlabel: str = "Date",
    ylabel: str = "Forecast",
) -> io.BytesIO:
    """Generate a PNG chart from forecast results and return a BytesIO buffer."""
    fig, ax = plt.subplots(figsize=(10, 5))

    for model_data in results:
        points = model_data.get("points", [])
        if not points:
            continue
        dates = [p["ds"] for p in points]
        values = [p["yhat"] for p in points]
        ax.plot(dates, values, label=model_data["model"], marker="o", markersize=3)

    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf


def _format_table(points: list[dict], model_name: str) -> str:
    """Format forecast points as a monospace table."""
    lines = [f"📊 *{model_name} Forecast*\n"]
    lines.append("```")
    lines.append(f"{'Date':<12} {'Forecast':>10}")
    lines.append("-" * 24)
    for p in points:
        ds = str(p["ds"])
        yhat = p["yhat"]
        lines.append(f"{ds:<12} {yhat:>10.2f}")
    lines.append("```")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Display callback router
# ─────────────────────────────────────────────────────────────────────────────

async def display_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:disp:* callbacks."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    # format: fc:disp:<mode>:<series_id>:<freq>[:model]
    mode = parts[2]          # table | chart
    series_id = parts[3]     # global, category name, or item_id
    freq = parts[4]
    model = parts[5] if len(parts) > 5 else None

    granularity = context.user_data.get("granularity", "item")
    if series_id == "global":
        granularity = "global"
        series_id = "global"
    elif context.user_data.get("selected_category") == series_id:
        granularity = "category"

    # Fetch data
    try:
        if granularity == "global":
            results = await forecast_api.get_global_forecast(freq=freq, model=model)
        elif granularity == "category":
            results = await forecast_api.get_category_forecast(series_id, freq=freq, model=model)
        else:
            results = await forecast_api.get_item_forecast(series_id, freq=freq, model=model)
    except ForecastAPIError as exc:
        await query.edit_message_text(f"❌ Error fetching forecast: {exc}")
        return

    if not results:
        await query.edit_message_text("No forecast data found.")
        return

    if mode == "chart":
        # Send chart as photo
        title = f"{granularity.capitalize()} Forecast: {series_id} ({freq})"
        if model:
            title += f" – {model}"
        buf = _generate_forecast_chart(results, title)

        # When sending a photo in response to a callback, we reply to the chat
        await query.message.reply_photo(
            photo=InputFile(buf, filename=f"forecast_{series_id}.png"),
            caption=f"📈 *{granularity.capitalize()} Forecast* `{series_id}` ({freq})"
            + (f" – {model}" if model else ""),
            parse_mode="Markdown",
        )
        # Keep the original message with the action menu
        await query.answer("Chart sent!")

    else:  # table
        # Build text tables (one per model, or single if model filtered)
        texts = []
        for r in results:
            pts = r.get("points", [])
            if pts:
                texts.append(_format_table(pts, r["model"]))
        text = "\n\n".join(texts)
        if len(text) > 4000:
            text = text[:3950] + "\n\n... (truncated)"

        # Determine correct back-menu
        if granularity == "global":
            reply_markup = global_display_menu(freq, model)
        elif granularity == "category":
            reply_markup = category_display_menu(series_id, freq, model)
        else:
            reply_markup = forecast_display_menu(series_id, freq, model)

        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=reply_markup
        )
