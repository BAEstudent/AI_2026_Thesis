"""Inline keyboard builders for the Telegram bot."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Main menu
# ─────────────────────────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔮 Forecasts", callback_data="menu:forecasts")],
            [
                InlineKeyboardButton("⚠️ Anomalies", callback_data="menu:anomalies"),
                InlineKeyboardButton("💬 Ask Data", callback_data="menu:text2sql"),
            ],
            [InlineKeyboardButton("ℹ️ Help", callback_data="menu:help")],
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Forecast granularity selection
# ─────────────────────────────────────────────────────────────────────────────

def forecast_granularity_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌍 Global", callback_data="fc:gran:global")],
            [InlineKeyboardButton("📁 Category", callback_data="fc:gran:category")],
            [InlineKeyboardButton("📦 Item", callback_data="fc:gran:item")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:main")],
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Frequency selection (used after granularity)
# ─────────────────────────────────────────────────────────────────────────────

def frequency_menu(back_callback: str) -> InlineKeyboardMarkup:
    buttons = []
    for freq in sorted(settings.VALID_FREQS):
        emoji = {"daily": "📅", "weekly": "📆", "quarterly": "🏘️"}.get(freq, "📊")
        buttons.append(
            [InlineKeyboardButton(f"{emoji} {freq.capitalize()}", callback_data=f"fc:freq:{freq}")]
        )
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────────────────────────────────────
# Category list (paginated if needed, start simple)
# ─────────────────────────────────────────────────────────────────────────────

def category_list_menu(categories: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(cat, callback_data=f"fc:cat:{cat}")]
        for cat in categories[:20]  # hard cap for inline keyboards
    ]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="menu:forecasts")])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────────────────────────────────────
# Item action menu (after item is selected)
# ─────────────────────────────────────────────────────────────────────────────

def item_action_menu(item_id: str, freq: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📈 View Forecast",
                    callback_data=f"fc:item:view:{item_id}:{freq}",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Model Scores",
                    callback_data=f"fc:item:metrics:{item_id}:{freq}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Retrain Now",
                    callback_data=f"fc:item:refresh:{item_id}:{freq}",
                )
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="fc:gran:item")],
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Model selection (when multiple models exist)
# ─────────────────────────────────────────────────────────────────────────────

def model_select_menu(
    models: list[str], base_callback: str, back_callback: str
) -> InlineKeyboardMarkup:
    """base_callback must contain a {model} placeholder."""
    buttons = []
    for mdl in models:
        cb = base_callback.format(model=mdl)
        buttons.append([InlineKeyboardButton(mdl, callback_data=cb)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────────────────────────────────────
# Forecast display actions (chart / table toggle)
# ─────────────────────────────────────────────────────────────────────────────

def forecast_display_menu(
    item_id: str, freq: str, model: str | None = None
) -> InlineKeyboardMarkup:
    model_part = f":{model}" if model else ""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Table",
                    callback_data=f"fc:disp:table:{item_id}:{freq}{model_part}",
                ),
                InlineKeyboardButton(
                    "📈 Chart",
                    callback_data=f"fc:disp:chart:{item_id}:{freq}{model_part}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 Model Scores",
                    callback_data=f"fc:item:metrics:{item_id}:{freq}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Retrain",
                    callback_data=f"fc:item:refresh:{item_id}:{freq}",
                )
            ],
            [InlineKeyboardButton("🔙 Back", callback_data=f"fc:gran:item")],
        ]
    )


def global_display_menu(freq: str, model: str | None = None) -> InlineKeyboardMarkup:
    model_part = f":{model}" if model else ""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Table",
                    callback_data=f"fc:disp:table:global:{freq}{model_part}",
                ),
                InlineKeyboardButton(
                    "📈 Chart",
                    callback_data=f"fc:disp:chart:global:{freq}{model_part}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 Model Scores",
                    callback_data=f"fc:metrics:global:global:{freq}",
                )
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="fc:gran:global")],
        ]
    )


def category_display_menu(category: str, freq: str, model: str | None = None) -> InlineKeyboardMarkup:
    model_part = f":{model}" if model else ""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Table",
                    callback_data=f"fc:disp:table:{category}:{freq}{model_part}",
                ),
                InlineKeyboardButton(
                    "📈 Chart",
                    callback_data=f"fc:disp:chart:{category}:{freq}{model_part}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 Model Scores",
                    callback_data=f"fc:metrics:category:{category}:{freq}",
                )
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="fc:gran:category")],
        ]
    )
