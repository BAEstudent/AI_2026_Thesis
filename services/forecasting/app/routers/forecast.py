from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas import ForecastPoint, ForecastResponse, ModelScore

router = APIRouter(prefix="/forecast", tags=["forecast"])

VALID_FREQS = {"daily", "weekly", "quarterly"}


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ch(request: Request):
    return request.app.state.ch


def _query_forecasts(ch, granularity: str, series_id: str,
                     freq: str, model: str | None) -> list[ForecastResponse]:
    """
    Pull the latest forecast rows from ClickHouse and group them by model.
    ReplacingMergeTree deduplication is forced with FINAL.
    """
    model_filter = f"AND model = '{model}'" if model else ""
    query = f"""
        SELECT granularity, freq, series_id, model, ds, yhat, computed_at
        FROM analytics.forecasts FINAL
        WHERE granularity = '{granularity}'
          AND series_id   = '{series_id}'
          AND freq        = '{freq}'
          {model_filter}
        ORDER BY model, ds
    """
    df = ch.query_df(query)
    if df.empty:
        return []

    results = []
    for mdl, grp in df.groupby("model"):
        results.append(ForecastResponse(
            granularity=granularity,
            freq=freq,
            series_id=series_id,
            model=mdl,
            computed_at=grp["computed_at"].iloc[0],
            points=[
                ForecastPoint(ds=row["ds"], yhat=row["yhat"])
                for _, row in grp.iterrows()
            ],
        ))
    return results


def _best_model(ch, granularity: str, series_id: str, freq: str) -> str:
    """Return the model with the lowest MAE for this series from the metrics table."""
    query = f"""
        SELECT model
        FROM analytics.forecast_metrics FINAL
        WHERE granularity = '{granularity}'
          AND series_id   = '{series_id}'
          AND freq        = '{freq}'
        ORDER BY MAE ASC
        LIMIT 1
    """
    df = ch.query_df(query)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No metrics found for {granularity}/{series_id}/{freq}. "
                   "Run the forecast DAG first.",
        )
    return df["model"].iloc[0]


def _retrain_and_forecast(granularity: str, series_id: str,
                          freq: str, model_name: str, ch) -> ForecastResponse:
    """
    Blocking function: load data → train best model → predict future.
    Called via run_in_executor so it doesn't block the event loop.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

    import pandas as pd
    from forecasting import (
        HORIZONS, load_global_daily, load_category_daily, load_item_daily,
        aggregate_to_freq, StatsForecastWrapper, ProphetWrapper,
    )

    min_date = "2024-01-01"

    if granularity == "global":
        df = load_global_daily(ch, min_date)
        df["unique_id"] = "global"
        id_col = "unique_id"
    elif granularity == "category":
        df = load_category_daily(ch, min_date)
        id_col = "category_level1"
    else:
        df = load_item_daily(ch, min_date, top_n=None)
        id_col = "item_id"

    series_df = aggregate_to_freq(
        df[df[id_col] == series_id].copy(), freq, id_col
    )
    if series_df.empty:
        raise HTTPException(status_code=404, detail=f"Series '{series_id}' not found.")

    model_map = {
        "Naive":         lambda: StatsForecastWrapper("Naive",         freq),
        "SeasonalNaive": lambda: StatsForecastWrapper("SeasonalNaive", freq),
        "AutoARIMA":     lambda: StatsForecastWrapper("AutoARIMA",     freq),
        "AutoETS":       lambda: StatsForecastWrapper("AutoETS",       freq),
        "Prophet":       lambda: ProphetWrapper(freq),
    }
    if model_name not in model_map:
        raise HTTPException(status_code=400, detail=f"Unknown model '{model_name}'.")

    model = model_map[model_name]()
    model.fit(series_df)
    preds = model.predict(HORIZONS[freq])

    computed_at = datetime.now(timezone.utc)

    # upsert into forecasts table so the result is visible next time
    rows = []
    for _, row in preds.iterrows():
        rows.append({
            "granularity": granularity,
            "freq":        freq,
            "series_id":   series_id,
            "model":       model_name,
            "ds":          row["ds"].date() if hasattr(row["ds"], "date") else row["ds"],
            "yhat":        float(row["yhat"]),
            "computed_at": computed_at,
        })
    ch.insert_df("analytics.forecasts", pd.DataFrame(rows))

    return ForecastResponse(
        granularity=granularity,
        freq=freq,
        series_id=series_id,
        model=model_name,
        computed_at=computed_at,
        points=[ForecastPoint(ds=r["ds"], yhat=r["yhat"]) for r in rows],
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET endpoints  (read from pre-computed forecasts table)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/global", response_model=list[ForecastResponse])
def get_global_forecast(
    request: Request,
    freq:  Annotated[str, Query()] = "daily",
    model: Annotated[str | None, Query()] = None,
):
    if freq not in VALID_FREQS:
        raise HTTPException(400, f"freq must be one of {VALID_FREQS}")
    results = _query_forecasts(_ch(request), "global", "global", freq, model)
    if not results:
        raise HTTPException(404, "No forecasts found. Run the forecast DAG first.")
    return results


@router.get("/category/{category}", response_model=list[ForecastResponse])
def get_category_forecast(
    category: str,
    request:  Request,
    freq:  Annotated[str, Query()] = "daily",
    model: Annotated[str | None, Query()] = None,
):
    if freq not in VALID_FREQS:
        raise HTTPException(400, f"freq must be one of {VALID_FREQS}")
    results = _query_forecasts(_ch(request), "category", category, freq, model)
    if not results:
        raise HTTPException(404, f"No forecasts found for category '{category}'.")
    return results


@router.get("/item/{item_id}", response_model=list[ForecastResponse])
def get_item_forecast(
    item_id: str,
    request: Request,
    freq:  Annotated[str, Query()] = "daily",
    model: Annotated[str | None, Query()] = None,
):
    if freq not in VALID_FREQS:
        raise HTTPException(400, f"freq must be one of {VALID_FREQS}")
    results = _query_forecasts(_ch(request), "item", item_id, freq, model)
    if not results:
        raise HTTPException(404, f"No forecasts found for item '{item_id}'.")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# POST /refresh  (on-the-fly retraining with best model)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/item/{item_id}/refresh", response_model=ForecastResponse)
async def refresh_item_forecast(
    item_id: str,
    request: Request,
    freq: Annotated[str, Query()] = "daily",
):
    """
    Retrain the best model for this item on fresh data and return new forecasts.
    Runs in a thread pool so ML training doesn't block the event loop.
    Takes ~5–15s depending on the model.
    """
    if freq not in VALID_FREQS:
        raise HTTPException(400, f"freq must be one of {VALID_FREQS}")

    ch = _ch(request)
    model_name = _best_model(ch, "item", item_id, freq)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,                                   # default ThreadPoolExecutor
        _retrain_and_forecast,
        "item", item_id, freq, model_name, ch,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /metrics  (model comparison scores for a series)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/metrics/{granularity}/{series_id}", response_model=list[ModelScore])
def get_metrics(
    granularity: str,
    series_id:   str,
    request:     Request,
    freq: Annotated[str, Query()] = "daily",
):
    query = f"""
        SELECT model, MAE, RMSE, MAPE, SMAPE
        FROM analytics.forecast_metrics FINAL
        WHERE granularity = '{granularity}'
          AND series_id   = '{series_id}'
          AND freq        = '{freq}'
        ORDER BY MAE ASC
    """
    df = _ch(request).query_df(query)
    if df.empty:
        raise HTTPException(404, "No metrics found.")
    return df.to_dict(orient="records")
