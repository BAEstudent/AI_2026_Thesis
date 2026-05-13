"""Pydantic settings for the Telegram bot."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_PROXY: str | None = None

    # Forecast API (FastAPI service)
    FORECAST_API_URL: str = "http://localhost:8001"
    FORECAST_API_TIMEOUT: int = 60

    # Text‑to‑SQL service
    TEXT2SQL_API_URL: str = "http://localhost:8002"
    TEXT2SQL_API_TIMEOUT: int = 30

    # ClickHouse (for direct lookups / search)
    CLICKHOUSE_HOST: str = "clickhouse"
    CLICKHOUSE_HTTP_PORT: int = 8123
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_DB: str = "analytics"

    # Bot behaviour
    DEFAULT_FREQ: str = "daily"
    VALID_FREQS: frozenset[str] = frozenset({"daily", "weekly", "quarterly"})
    MAX_ITEM_SEARCH_RESULTS: int = 10


settings = Settings()
