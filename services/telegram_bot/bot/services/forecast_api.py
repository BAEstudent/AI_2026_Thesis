"""Typed HTTP client for the forecasting FastAPI service."""
from typing import Any

import httpx

from bot.config import settings


class ForecastAPIError(Exception):
    """Raised when the forecast API returns an error."""
    pass


class ForecastAPIClient:
    """Async client for the forecasting service."""

    def __init__(self) -> None:
        self.base_url = settings.FORECAST_API_URL.rstrip("/")
        self.timeout = settings.FORECAST_API_TIMEOUT

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
                raise ForecastAPIError(
                    f"⏱️ Connection to forecast API timed out. "
                    f"Is the forecasting service running and healthy?\n"
                    f"URL: {url}"
                ) from exc
            except httpx.ConnectError as exc:
                raise ForecastAPIError(
                    f"🔌 Cannot connect to forecast API at {url}.\n"
                    f"Common causes:\n"
                    f"• forecasting container is not running\n"
                    f"• forecasting app bound to 127.0.0.1 instead of 0.0.0.0\n"
                    f"• services on different Docker networks"
                ) from exc
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text
                raise ForecastAPIError(f"API error {exc.response.status_code}: {detail}") from exc

    # ─────────────────────────────────────────────────────────────────────────
    # GET endpoints
    # ─────────────────────────────────────────────────────────────────────────

    async def get_global_forecast(
        self, freq: str = "daily", model: str | None = None
    ) -> list[dict]:
        params = {"freq": freq}
        if model:
            params["model"] = model
        return await self._request("GET", "/forecast/global", params=params)

    async def get_category_forecast(
        self, category: str, freq: str = "daily", model: str | None = None
    ) -> list[dict]:
        params = {"freq": freq}
        if model:
            params["model"] = model
        return await self._request(
            "GET", f"/forecast/category/{category}", params=params
        )

    async def get_item_forecast(
        self, item_id: str, freq: str = "daily", model: str | None = None
    ) -> list[dict]:
        params = {"freq": freq}
        if model:
            params["model"] = model
        return await self._request(
            "GET", f"/forecast/item/{item_id}", params=params
        )

    async def get_metrics(
        self, granularity: str, series_id: str, freq: str = "daily"
    ) -> list[dict]:
        params = {"freq": freq}
        return await self._request(
            "GET",
            f"/forecast/metrics/{granularity}/{series_id}",
            params=params,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # POST endpoints
    # ─────────────────────────────────────────────────────────────────────────

    async def refresh_item_forecast(
        self, item_id: str, freq: str = "daily"
    ) -> dict:
        params = {"freq": freq}
        return await self._request(
            "POST", f"/forecast/item/{item_id}/refresh", params=params
        )


# Singleton
forecast_api = ForecastAPIClient()
