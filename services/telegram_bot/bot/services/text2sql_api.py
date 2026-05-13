"""Typed HTTP client for the Text‑to‑SQL FastAPI service."""
from typing import Any

import httpx

from bot.config import settings


class Text2SQLAPIError(Exception):
    """Raised when the Text‑to‑SQL API returns an error."""
    pass


class Text2SQLAPIClient:
    """Async client for the Text‑to‑SQL service."""

    def __init__(self) -> None:
        self.base_url = settings.TEXT2SQL_API_URL.rstrip("/")
        self.timeout = settings.TEXT2SQL_API_TIMEOUT

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.request(
                    method, url, params=params, json=json_data
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.TimeoutException as exc:
                raise Text2SQLAPIError(
                    f"⏱️ Text‑to‑SQL service timed out. Is the service running?"
                ) from exc
            except httpx.ConnectError as exc:
                raise Text2SQLAPIError(
                    f"🔌 Cannot connect to Text‑to‑SQL service at {url}.\n"
                    f"Make sure the `text-to-sql` container is running."
                ) from exc
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text
                raise Text2SQLAPIError(
                    f"Text‑to‑SQL API error {exc.response.status_code}: {detail}"
                ) from exc

    async def ask_question(self, question: str) -> dict[str, Any]:
        """Send a natural language question and get back SQL + results."""
        return await self._request(
            "POST",
            "/api/v1/query",
            json_data={"question": question},
        )


# Singleton
text2sql_api = Text2SQLAPIClient()
