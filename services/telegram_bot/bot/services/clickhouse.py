"""Direct ClickHouse client for lookups / search."""
import clickhouse_connect

from bot.config import settings


class ClickHouseClient:
    """Thin wrapper around clickhouse_connect."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=settings.CLICKHOUSE_HOST,
                port=settings.CLICKHOUSE_HTTP_PORT,
                username=settings.CLICKHOUSE_USER,
                password=settings.CLICKHOUSE_PASSWORD,
                database=settings.CLICKHOUSE_DB,
            )
        return self._client

    def search_items(self, query: str, limit: int = 10) -> list[dict]:
        """Search items by name or id."""
        ch = self._get_client()
        # Use FINAL because ReplacingMergeTree
        sql = """
            SELECT item_id, itemname
            FROM items FINAL
            WHERE item_id ILIKE %(q)s
               OR itemname ILIKE %(q)s
            LIMIT %(limit)s
        """
        df = ch.query_df(sql, parameters={"q": f"%{query}%", "limit": limit})
        return df.to_dict(orient="records") if not df.empty else []

    def list_categories(self, limit: int = 50) -> list[str]:
        """Return distinct top-level categories."""
        ch = self._get_client()
        sql = """
            SELECT DISTINCT category_level1
            FROM order_facts FINAL
            WHERE category_level1 != ''
            ORDER BY category_level1
            LIMIT %(limit)s
        """
        df = ch.query_df(sql, parameters={"limit": limit})
        return df["category_level1"].tolist() if not df.empty else []


# Singleton
ch_client = ClickHouseClient()
