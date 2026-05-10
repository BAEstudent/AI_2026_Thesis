"""
forecast_dag.py
Nightly DAG: trains all models on fresh data, writes future forecasts
and train/test metrics to ClickHouse.

Three parallel tasks (one per granularity) so item/category/global
don't block each other.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone, timedelta

from airflow.decorators import dag, task

sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# DAG
# ─────────────────────────────────────────────────────────────────────────────

default_args = {
    "owner": "airflow",
    "retries": 0,
}

@dag(
    dag_id="forecast_pipeline",
    schedule="0 3 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["forecasting"],
)
def forecast_pipeline():

    @task()
    def run_global(**context):
        _run_granularity("global")

    @task()
    def run_category(**context):
        _run_granularity("category")

    @task()
    def run_item(**context):
        _run_granularity("item")

    run_global() >> run_category() >> run_item()


dag_instance = forecast_pipeline()


# ─────────────────────────────────────────────────────────────────────────────
# CORE LOGIC  (called by each task)
# ─────────────────────────────────────────────────────────────────────────────

def _run_granularity(granularity: str):
    """
    For every (freq, series) in the given granularity:
      1. compute train/test metrics  → analytics.forecast_metrics
      2. train on full data, predict future horizon → analytics.forecasts
    """
    import pandas as pd
    import clickhouse_connect

    from forecasting import (
        get_client, HORIZONS,
        load_global_daily, load_category_daily, load_item_daily,
        aggregate_to_freq,
        StatsForecastWrapper, ProphetWrapper,
        train_test_split, run_on_series,
    )

    ch = get_client()
    freqs = ["daily", "weekly"]
    min_date = "2024-01-01"

    for freq_label in freqs:
        horizon = HORIZONS[freq_label]

        # --- load raw daily data ---
        if granularity == "global":
            df = load_global_daily(ch, min_date)
            df["unique_id"] = "global"
            id_col = "unique_id"
        elif granularity == "category":
            df = load_category_daily(ch, min_date)
            id_col = "category_level1"
        else:
            df = load_item_daily(ch, min_date, top_n=50)
            id_col = "item_id"

        if df.empty:
            print(f"  No data for {granularity}/{freq_label}, skipping.")
            continue

        df = aggregate_to_freq(df, freq_label, group_col=id_col)

        for sid in df[id_col].unique():
            s_df = df[df[id_col] == sid].copy()
            computed_at = datetime.now(timezone.utc)

            def make_models():
                return [
                    StatsForecastWrapper("Naive",         freq_label),
                    StatsForecastWrapper("SeasonalNaive", freq_label),
                    StatsForecastWrapper("AutoARIMA",     freq_label),
                    StatsForecastWrapper("AutoETS",       freq_label),
                    ProphetWrapper(freq_label),
                ]

            # 1 — metrics (train/test split)
            metric_rows = run_on_series(
                s_df, sid, granularity, freq_label, horizon, make_models()
            )
            if metric_rows:
                _write_metrics(ch, metric_rows, computed_at)

            # 2 — future forecasts (train on ALL data)
            forecast_rows = _future_forecast(
                s_df, sid, granularity, freq_label, horizon,
                make_models(), computed_at,
            )
            if forecast_rows:
                _write_forecasts(ch, forecast_rows)

            print(f"  ✓ {granularity}/{freq_label}/{sid}: "
                  f"{len(metric_rows)} metric rows, "
                  f"{len(forecast_rows)} forecast rows")


def _future_forecast(
    series_df, series_id, granularity, freq_label, horizon,
    models, computed_at,
) -> list[dict]:
    """Train each model on the full series, return future forecast rows."""
    rows = []
    for model in models:
        try:
            model.fit(series_df)
            preds = model.predict(horizon)
            for _, row in preds.iterrows():
                rows.append({
                    "granularity": granularity,
                    "freq":        freq_label,
                    "series_id":   str(series_id),
                    "model":       model.name,
                    "ds":          row["ds"].date() if hasattr(row["ds"], "date") else row["ds"],
                    "yhat":        float(row["yhat"]),
                    "computed_at": computed_at,
                })
        except Exception as e:
            print(f"    [{model.name}] future forecast failed for {series_id}: {e}")
    return rows


def _write_forecasts(ch, rows: list[dict]):
    import pandas as pd
    df = pd.DataFrame(rows)
    ch.insert_df("analytics.forecasts", df)


def _write_metrics(ch, rows: list[dict], computed_at):
    import pandas as pd
    df = pd.DataFrame(rows)
    df["computed_at"] = computed_at
    ch.insert_df("analytics.forecast_metrics", df[[
        "granularity", "freq", "series_id", "model",
        "MAE", "RMSE", "MAPE", "SMAPE", "train_size", "computed_at",
    ]])
