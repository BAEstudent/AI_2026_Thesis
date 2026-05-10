"""Forecast granularity & item search handlers."""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.config import settings
from bot.keyboards import (
    forecast_granularity_menu,
    frequency_menu,
    category_list_menu,
    item_action_menu,
    global_display_menu,
    category_display_menu,
)
from bot.services.clickhouse import ch_client
from bot.services.forecast_api import forecast_api, ForecastAPIError


# ─────────────────────────────────────────────────────────────────────────────
# Granularity selection
# ─────────────────────────────────────────────────────────────────────────────

async def granularity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:gran:* callbacks."""
    query = update.callback_query
    await query.answer()
    granularity = query.data.split(":")[2]

    # Store current granularity in user_data for later steps
    context.user_data["granularity"] = granularity

    if granularity == "global":
        await query.edit_message_text(
            "🌍 *Global Forecast*\nChoose frequency:",
            parse_mode="Markdown",
            reply_markup=frequency_menu(back_callback="menu:forecasts"),
        )
    elif granularity == "category":
        # Load categories from ClickHouse
        await query.edit_message_text("📁 Loading categories…")
        categories = ch_client.list_categories(limit=50)
        if not categories:
            await query.edit_message_text(
                "No categories found. Please run the ETL first.",
                reply_markup=forecast_granularity_menu(),
            )
            return
        await query.edit_message_text(
            "📁 *Select Category*\nChoose a category:",
            parse_mode="Markdown",
            reply_markup=category_list_menu(categories),
        )
    elif granularity == "item":
        await query.edit_message_text(
            "📦 *Item Forecast*\n\n"
            "Send me an *item ID* or *item name* to search.",
            parse_mode="Markdown",
        )
        # Set state so next text message is handled by item_search_handler
        context.user_data["awaiting_item_search"] = True


# ─────────────────────────────────────────────────────────────────────────────
# Frequency selection
# ─────────────────────────────────────────────────────────────────────────────

async def frequency_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:freq:* callbacks."""
    query = update.callback_query
    await query.answer()
    freq = query.data.split(":")[2]
    context.user_data["freq"] = freq
    granularity = context.user_data.get("granularity")

    if granularity == "global":
        # Directly fetch global forecast
        await _fetch_and_show_global(query, freq)
    elif granularity == "category":
        # We should never reach here without a category selected,
        # but handle gracefully
        await query.edit_message_text(
            "⚠️ Please select a category first.",
            reply_markup=forecast_granularity_menu(),
        )
    elif granularity == "item":
        # If item already selected, show actions
        item_id = context.user_data.get("selected_item_id")
        if item_id:
            await query.edit_message_text(
                f"📦 *Item:* `{item_id}`\n*Freq:* {freq}",
                parse_mode="Markdown",
                reply_markup=item_action_menu(item_id, freq),
            )
        else:
            await query.edit_message_text(
                "📦 *Item Forecast*\n\nSend me an *item ID* or *item name* to search.",
                parse_mode="Markdown",
            )
            context.user_data["awaiting_item_search"] = True


# ─────────────────────────────────────────────────────────────────────────────
# Category selected
# ─────────────────────────────────────────────────────────────────────────────

async def category_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:cat:* callbacks."""
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 2)[2]
    context.user_data["selected_category"] = category
    freq = context.user_data.get("freq", settings.DEFAULT_FREQ)

    await _fetch_and_show_category(query, category, freq)


# ─────────────────────────────────────────────────────────────────────────────
# Item search (text message handler)
# ─────────────────────────────────────────────────────────────────────────────

async def item_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text item search when awaiting_item_search is True."""
    if not context.user_data.get("awaiting_item_search"):
        return  # Let other handlers deal with it

    context.user_data["awaiting_item_search"] = False
    query_text = update.message.text.strip()
    freq = context.user_data.get("freq", settings.DEFAULT_FREQ)

    # Try exact item_id first via API
    try:
        results = await forecast_api.get_item_forecast(query_text, freq=freq)
        if results:
            # Exact match found
            context.user_data["selected_item_id"] = query_text
            await update.message.reply_text(
                f"📦 *Item:* `{query_text}`\n*Freq:* {freq}",
                parse_mode="Markdown",
                reply_markup=item_action_menu(query_text, freq),
            )
            return
    except ForecastAPIError:
        pass  # Not found, fall through to search

    # Search ClickHouse
    matches = ch_client.search_items(query_text, limit=settings.MAX_ITEM_SEARCH_RESULTS)
    if not matches:
        await update.message.reply_text(
            "❌ No items found. Try again or check the item ID.",
            reply_markup=forecast_granularity_menu(),
        )
        return

    if len(matches) == 1:
        item_id = matches[0]["item_id"]
        context.user_data["selected_item_id"] = item_id
        await update.message.reply_text(
            f"📦 *Item:* `{item_id}`\n*Freq:* {freq}",
            parse_mode="Markdown",
            reply_markup=item_action_menu(item_id, freq),
        )
        return

    # Multiple matches – show list
    buttons = [
        [InlineKeyboardButton(
            f"{m['item_id']} – {m['itemname'][:30]}",
            callback_data=f"fc:item:select:{m['item_id']}"
        )]
        for m in matches
    ]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="fc:gran:item")])
    await update.message.reply_text(
        "🔍 *Multiple items found* – pick one:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def item_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle fc:item:select:* callbacks."""
    query = update.callback_query
    await query.answer()
    item_id = query.data.split(":", 3)[3]
    context.user_data["selected_item_id"] = item_id
    freq = context.user_data.get("freq", settings.DEFAULT_FREQ)

    await query.edit_message_text(
        f"📦 *Item:* `{item_id}`\n*Freq:* {freq}",
        parse_mode="Markdown",
        reply_markup=item_action_menu(item_id, freq),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers to fetch & show
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_and_show_global(query, freq: str) -> None:
    try:
        results = await forecast_api.get_global_forecast(freq=freq)
    except ForecastAPIError as exc:
        await query.edit_message_text(f"❌ Error: {exc}", reply_markup=forecast_granularity_menu())
        return

    if not results:
        await query.edit_message_text(
            "No global forecasts found. Run the forecast DAG first.",
            reply_markup=forecast_granularity_menu(),
        )
        return

    # Show summary and display options
    models = [r["model"] for r in results]
    text = (
        f"🌍 *Global Forecast*\n"
        f"*Freq:* {freq}\n"
        f"*Models:* {', '.join(models)}\n\n"
        "Choose display format:"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=global_display_menu(freq)
    )


async def _fetch_and_show_category(query, category: str, freq: str) -> None:
    try:
        results = await forecast_api.get_category_forecast(category, freq=freq)
    except ForecastAPIError as exc:
        await query.edit_message_text(f"❌ Error: {exc}", reply_markup=forecast_granularity_menu())
        return

    if not results:
        await query.edit_message_text(
            f"No forecasts for category *{category}*. Run the DAG first.",
            parse_mode="Markdown",
            reply_markup=forecast_granularity_menu(),
        )
        return

    models = [r["model"] for r in results]
    text = (
        f"📁 *Category:* `{category}`\n"
        f"*Freq:* {freq}\n"
        f"*Models:* {', '.join(models)}\n\n"
        "Choose display format:"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=category_display_menu(category, freq)
    )
