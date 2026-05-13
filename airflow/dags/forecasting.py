"""
Forecasting Experiments
Loads data from ClickHouse, runs models at multiple horizons, and reports metrics.
"""

import os
import warnings
import numpy as np
import pandas as pd
import clickhouse_connect
from sklearn.metrics import mean_absolute_error, mean_squared_error

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

CH_HOST     = os.getenv("CLICKHOUSE_HOST", "localhost")
CH_PORT     = int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123"))
CH_USER     = os.getenv("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CH_DB       = os.getenv("CLICKHOUSE_DB", "analytics")

HORIZONS = {"daily": 14, "weekly": 4, "quarterly": 2}


# ─────────────────────────────────────────────────────────────────────────────
# 1. DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def get_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD
    )


def load_item_daily(client, min_date=None, max_date=None, top_n=50):
    """Daily orders per item. Returns columns: ds, item_id, category_level1, y."""
    date_filter = _date_filter(min_date, max_date, col="date")
    limit = f"LIMIT {top_n}" if top_n else ""
    query = f"""
        WITH item_stats AS (
            SELECT item_id,
                   sum(orders_cnt)         AS total_orders,
                   countIf(orders_cnt > 0) AS active_days
            FROM {CH_DB}.item_daily_stats
            WHERE 1=1 {date_filter}
            GROUP BY item_id
            HAVING total_orders >= 10 AND active_days >= 14
            {limit}
        )
        SELECT ids.date AS ds,
               ids.item_id as item_id,
               c.category_level1 as category_level1,
               ids.orders_cnt AS y,
               ids.views_cnt as views_cnt,
               ids.users_cnt as users_cnt
        FROM {CH_DB}.item_daily_stats ids
        JOIN item_stats its ON ids.item_id = its.item_id
        LEFT JOIN {CH_DB}.items i    ON ids.item_id = i.item_id
        LEFT JOIN {CH_DB}.category c ON cast(i.catalogid AS String) = c.catalogid
        WHERE 1=1 {date_filter}
        ORDER BY ids.item_id, ids.date
    """
    df = client.query_df(query)
    df["ds"] = pd.to_datetime(df["ds"]).dt.tz_localize(None)
    return df


def load_category_daily(client, min_date=None, max_date=None):
    """Daily orders per category. Returns columns: ds, category_level1, y."""
    date_filter = _date_filter(min_date, max_date, col="created_date")
    query = f"""
        SELECT created_date AS ds,
               category_level1,
               count()          AS y,
               uniq(user_id)    AS users_cnt,
               uniq(item_id)    AS items_active,
               sum(is_canceled) AS canceled_cnt
        FROM {CH_DB}.order_facts
        WHERE category_level1 != '' {date_filter}
        GROUP BY created_date, category_level1
        ORDER BY category_level1, created_date
    """
    df = client.query_df(query)
    df["ds"] = pd.to_datetime(df["ds"]).dt.tz_localize(None)
    return df


def load_global_daily(client, min_date=None, max_date=None):
    """Daily orders across all items. Returns columns: ds, y."""
    date_filter = _date_filter(min_date, max_date, col="date")
    query = f"""
        SELECT date AS ds,
               sum(orders_cnt) AS y,
               sum(views_cnt)  AS views_cnt,
               uniq(item_id)   AS items_active
        FROM {CH_DB}.item_daily_stats
        WHERE 1=1 {date_filter}
        GROUP BY date
        ORDER BY date
    """
    df = client.query_df(query)
    df["ds"] = pd.to_datetime(df["ds"]).dt.tz_localize(None)
    return df


def _date_filter(min_date, max_date, col):
    clause = ""
    if min_date:
        clause += f" AND {col} >= '{min_date}'"
    if max_date:
        clause += f" AND {col} <= '{max_date}'"
    return clause


# ─────────────────────────────────────────────────────────────────────────────
# 2. AGGREGATION  (daily → weekly / quarterly)
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_to_freq(df, freq_label, group_col):
    if freq_label == "daily":
        return df

    df = df.copy()
    df["ds"] = pd.to_datetime(df["ds"])

    if freq_label == "weekly":
        df["period"] = df["ds"] - pd.to_timedelta(df["ds"].dt.dayofweek, unit="D")
    else:
        df["period"] = df["ds"].dt.to_period("QS").dt.start_time

    numeric_cols = [
        c for c in df.select_dtypes(include=np.number).columns
        if c != group_col
    ]

    return (
        df.groupby([group_col, "period"])[numeric_cols]
        .sum()
        .reset_index()
        .rename(columns={"period": "ds"})
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. METRICS
# ─────────────────────────────────────────────────────────────────────────────

def smape(y_true, y_pred):
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    return np.nanmean(np.abs(y_true - y_pred) / denom) * 100


def mape(y_true, y_pred):
    mask = y_true != 0
    if mask.sum() == 0:
        return np.nan
    return np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100


def calculate_metrics(y_true, y_pred):
    y_true = np.asarray(y_true).flatten()
    y_pred = np.asarray(y_pred).flatten()
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    yt, yp = y_true[mask], y_pred[mask]
    if len(yt) == 0:
        return {"MAE": np.nan, "RMSE": np.nan, "MAPE": np.nan, "SMAPE": np.nan}
    return {
        "MAE":   mean_absolute_error(yt, yp),
        "RMSE":  np.sqrt(mean_squared_error(yt, yp)),
        "MAPE":  mape(yt, yp),
        "SMAPE": smape(yt, yp),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. MODEL WRAPPERS
# ─────────────────────────────────────────────────────────────────────────────

# Maps freq_label to the frequency string used by StatsForecast / Prophet /
# pd.date_range.  All three must agree so that predicted ds values match the
# aggregated ds values in the test set.
FREQ_SF_MAP     = {"daily": "D",     "weekly": "W-MON", "quarterly": "QS"}
FREQ_PROPHET_MAP = {"daily": "D",    "weekly": "W-MON", "quarterly": "QS"}


class ModelWrapper:
    name = "base"
    def fit(self, df: pd.DataFrame): raise NotImplementedError
    def predict(self, horizon: int) -> pd.DataFrame: raise NotImplementedError


class StatsForecastWrapper(ModelWrapper):
    """Wraps any Nixtla StatsForecast model."""

    SEASON_DEFAULTS = {"daily": 7, "weekly": 52, "quarterly": 4}

    def __init__(self, model_name: str, freq_label: str = "daily"):
        from statsforecast import StatsForecast
        from statsforecast.models import (
            AutoARIMA, AutoETS, SeasonalNaive, Naive, SeasonalWindowAverage,
        )
        self._sf_cls = StatsForecast
        self._models = {
            "AutoARIMA":         lambda s: AutoARIMA(season_length=s),
            "AutoETS":           lambda s: AutoETS(season_length=s),
            "SeasonalNaive":     lambda s: SeasonalNaive(season_length=s),
            "Naive":             lambda _: Naive(),
            "SeasonalWindowAvg": lambda s: SeasonalWindowAverage(season_length=s, window_size=4),
        }
        self.name          = model_name
        self.season_length = self.SEASON_DEFAULTS[freq_label]
        self.sf_freq       = FREQ_SF_MAP[freq_label]
        self._fitted       = None

    def fit(self, df: pd.DataFrame):
        df = df.copy()
        if "unique_id" not in df.columns:
            df["unique_id"] = (
                df["item_id"].astype(str) if "item_id" in df.columns
                else df.get("category_level1", pd.Series("global", index=df.index))
            )
        sf = self._sf_cls(
            models=[self._models[self.name](self.season_length)],
            freq=self.sf_freq,
            n_jobs=-1,
        )
        sf.fit(df[["unique_id", "ds", "y"]])
        self._fitted = sf
        return self

    def predict(self, horizon: int) -> pd.DataFrame:
        fc = self._fitted.predict(h=horizon)
        return fc.rename(columns={self.name: "yhat"})


class ProphetWrapper(ModelWrapper):
    """Wraps Facebook Prophet. Expects a single series."""
    name = "Prophet"

    def __init__(self, freq_label: str = "daily"):
        self._freq   = FREQ_PROPHET_MAP[freq_label]
        self._fitted = None

    def fit(self, df: pd.DataFrame):
        from prophet import Prophet
        m = Prophet(
            weekly_seasonality=True,
            yearly_seasonality=False,
            daily_seasonality=False,
        )
        m.fit(df[["ds", "y"]])
        self._fitted = m
        return self

    def predict(self, horizon: int) -> pd.DataFrame:
        future = self._fitted.make_future_dataframe(periods=horizon, freq=self._freq)
        fc = self._fitted.predict(future)
        return fc.tail(horizon)[["ds", "yhat"]]


# ─────────────────────────────────────────────────────────────────────────────
# 5. EXPERIMENT RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def train_test_split(df: pd.DataFrame, horizon: int):
    df = df.sort_values("ds")
    return df.iloc[:-horizon].copy(), df.iloc[-horizon:].copy()


def run_on_series(series_df, series_id, granularity, freq_label, horizon, models):
    """Fit every model on one series and return a list of result dicts."""
    min_history = horizon + 14
    if len(series_df) < min_history:
        return []

    train, test = train_test_split(series_df, horizon)
    rows = []

    for model in models:
        try:
            model.fit(train)
            preds = model.predict(horizon)
            merged = test.merge(preds[["ds", "yhat"]], on="ds", how="left")
            metrics = calculate_metrics(merged["y"].values, merged["yhat"].fillna(0).values)
            rows.append({
                "model":       model.name,
                "granularity": granularity,
                "freq":        freq_label,
                "series_id":   str(series_id),
                "horizon":     horizon,
                "train_size":  len(train),
                "test_size":   len(test),
                **metrics,
            })
        except Exception as e:
            print(f"    [{model.name}] failed on {series_id}: {e}")

    return rows


def run_experiments(
    client,
    granularities=("global", "category", "item"),
    freqs=("daily", "weekly"),
    min_date=None,
    max_date=None,
):
    """
    Main entry point.
    Returns a DataFrame with one row per (model, series, freq).
    """
    all_rows = []

    for granularity in granularities:
        for freq_label in freqs:
            horizon = HORIZONS[freq_label]
            print(f"\n{'─'*55}")
            print(f"  {granularity.upper()} | {freq_label.upper()} | horizon={horizon}")
            print(f"{'─'*55}")

            if granularity == "global":
                df = load_global_daily(client, min_date, max_date)
                df["unique_id"] = "global"
                id_col = "unique_id"
            elif granularity == "category":
                df = load_category_daily(client, min_date, max_date)
                id_col = "category_level1"
            else:
                df = load_item_daily(client, min_date, max_date, top_n=50)
                id_col = "item_id"

            if df.empty:
                print("  No data — skipping.")
                continue

            df = aggregate_to_freq(df, freq_label, group_col=id_col)

            def make_models():
                return [
                    StatsForecastWrapper("Naive",         freq_label),
                    StatsForecastWrapper("SeasonalNaive", freq_label),
                    StatsForecastWrapper("AutoARIMA",     freq_label),
                    StatsForecastWrapper("AutoETS",       freq_label),
                    ProphetWrapper(freq_label),
                ]

            for sid in df[id_col].unique():
                s_df = df[df[id_col] == sid].copy()
                rows = run_on_series(s_df, sid, granularity, freq_label, horizon, make_models())
                all_rows.extend(rows)
                if rows:
                    print(f"    {sid}: {[r['model'] for r in rows]}")

    return pd.DataFrame(all_rows)


# ─────────────────────────────────────────────────────────────────────────────
# 6. REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def report_results(results: pd.DataFrame):
    if results.empty:
        print("No results.")
        return

    metric_cols = ["MAE", "RMSE", "MAPE", "SMAPE"]

    print("\n" + "="*60)
    print("AVERAGE METRICS BY MODEL")
    print("="*60)
    summary = (
        results.groupby(["granularity", "freq", "model"])[metric_cols]
        .mean()
        .reset_index()
        .sort_values(["granularity", "freq", "MAE"])
    )
    for (gran, freq), grp in summary.groupby(["granularity", "freq"]):
        print(f"\n  {gran.upper()} | {freq.upper()}")
        print(grp.drop(columns=["granularity", "freq"]).to_string(index=False))

    print("\n" + "="*60)
    print("MODEL WIN COUNTS (lowest MAE per series)")
    print("="*60)
    best = results.loc[
        results.groupby(["granularity", "freq", "series_id"])["MAE"].idxmin()
    ]
    wins = (
        best.groupby(["granularity", "freq", "model"])
        .size()
        .reset_index(name="wins")
        .sort_values(["granularity", "freq", "wins"], ascending=[True, True, False])
    )
    print(wins.to_string(index=False))


# ─────────────────────────────────────────────────────────────────────────────
# 7. MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client = get_client()

    results = run_experiments(
        client,
        granularities=["global", "category"],
        freqs=["daily", "weekly"],
        min_date="2024-01-01",
        max_date=None,
    )

    results.to_csv("forecast_results.csv", index=False)
    print(f"\nSaved {len(results)} rows → forecast_results.csv")

    report_results(results)
