"""Nasdaq-100 medium/low-frequency cross-sectional research pipeline.

This is a research example, not investment advice.  The bundled universe is a
point-in-time snapshot, so historical results have survivorship bias unless a
point-in-time membership file is supplied.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
import requests
import yfinance as yf
import lightgbm as lgb
from qlib.contrib.model.gbdt import LGBModel
from qlib.data.dataset.handler import DataHandlerLP
from qlib.workflow import R


FEATURE_COLUMNS = [
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "ret_60",
    "ma_ratio_5",
    "ma_ratio_10",
    "ma_ratio_20",
    "ma_ratio_60",
    "volatility_10",
    "volatility_20",
    "volatility_60",
    "volume_ratio_5",
    "volume_ratio_20",
    "intraday_range",
    "open_gap",
    "rsi_14",
]

RISK_FEATURE_COLUMNS = [
    "downside_volatility_20",
    "drawdown_60",
    "return_skew_20",
    "dollar_volume_ratio_20",
    "amihud_20",
    "beta_60",
    "alpha_60",
    "qqq_corr_60",
    "residual_volatility_60",
    "relative_ret_20",
    "relative_ret_60",
]

MARKET_FEATURE_COLUMNS = [
    "qqq_ret_5",
    "qqq_ret_20",
    "qqq_volatility_20",
    "qqq_drawdown_60",
]

FRED_SERIES = {
    "rf_3m": "DGS3MO",
    "vix": "VIXCLS",
    "high_yield_spread": "BAMLH0A0HYM2",
    "fed_funds": "DFF",
}

MACRO_FEATURE_COLUMNS = list(FRED_SERIES)


def load_local_env(path: Path = Path(".env")) -> None:
    """Load missing environment variables from a local, git-ignored .env file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class FrameDataset:
    """Small DatasetH-compatible adapter used by Qlib's LightGBM model."""

    def __init__(self, frame: pd.DataFrame, segments: dict[str, tuple[pd.Timestamp, pd.Timestamp]]):
        self.frame = frame.sort_index()
        self.segments = segments

    def prepare(self, segment, col_set="feature", data_key=DataHandlerLP.DK_I):
        del data_key
        start, end = self.segments[segment] if isinstance(segment, str) else (segment.start, segment.stop)
        selected = self.frame.loc[(slice(start, end), slice(None)), :]
        if isinstance(col_set, str):
            return selected[col_set]
        return selected.loc[:, list(col_set)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=None, help="Exclusive Yahoo Finance end date")
    parser.add_argument("--universe-file", default="examples/data/nasdaq100_snapshot_2026-05-01.csv")
    parser.add_argument("--output-dir", default=".artifacts/nasdaq100_medium_low_frequency")
    parser.add_argument("--horizon", type=int, default=5, help="Trading days between entry and exit")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=10.0, help="One-way cost in basis points")
    parser.add_argument(
        "--max-net-beta",
        type=float,
        default=0.10,
        help="Maximum absolute long-short net beta; <=0 disables",
    )
    parser.add_argument("--test-years", type=int, default=2)
    parser.add_argument("--valid-years", type=int, default=1)
    parser.add_argument(
        "--label-mode",
        choices=("cross_sectional_median", "benchmark_relative", "beta_adjusted"),
        default="benchmark_relative",
    )
    parser.add_argument(
        "--walk-forward-years",
        type=int,
        default=1,
        help="Retrain on an expanding window for each test block; 0 disables walk-forward",
    )
    parser.add_argument(
        "--objective",
        choices=("mse", "lambdarank"),
        default="mse",
        help="Regression baseline or cross-sectional ranking objective",
    )
    parser.add_argument(
        "--feature-set",
        choices=("base", "enhanced"),
        default="enhanced",
        help="17 price/volume features or the full multi-source feature set",
    )
    parser.add_argument("--max-stocks", type=int, default=None, help="Useful for a quick smoke test")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached market data")
    parser.add_argument(
        "--fundamentals-file",
        default=None,
        help="Optional point-in-time CSV with datetime, instrument and fundamental_* columns",
    )
    parser.add_argument(
        "--membership-file",
        default=None,
        help="Optional point-in-time membership CSV with symbol, start_date and end_date",
    )
    parser.add_argument(
        "--disable-fred",
        action="store_true",
        help="Disable FRED macro feature download",
    )
    return parser.parse_args()


def load_universe(path: Path, max_stocks: int | None = None) -> tuple[list[str], dict]:
    universe = pd.read_csv(path)
    required = {"symbol", "effective_date", "source"}
    missing = required.difference(universe.columns)
    if missing:
        raise ValueError(f"Universe file is missing columns: {sorted(missing)}")
    symbols = universe["symbol"].dropna().astype(str).str.strip().drop_duplicates().tolist()
    if max_stocks:
        symbols = symbols[:max_stocks]
    metadata = {
        "file": str(path),
        "effective_date": str(universe["effective_date"].iloc[0]),
        "source": str(universe["source"].iloc[0]),
        "point_in_time": bool(universe.get("point_in_time", pd.Series([False])).iloc[0]),
        "securities": len(symbols),
    }
    return symbols, metadata


def normalize_membership(membership: pd.DataFrame) -> pd.DataFrame:
    membership = membership.copy()
    required = {"symbol", "start_date", "end_date"}
    if not required.issubset(membership.columns):
        raise ValueError(f"Membership file requires columns: {sorted(required)}")
    membership["symbol"] = membership["symbol"].astype(str).str.strip()
    membership["start_date"] = pd.to_datetime(membership["start_date"])
    membership["end_date"] = pd.to_datetime(membership["end_date"]).fillna(pd.Timestamp.max)
    if (membership["end_date"] <= membership["start_date"]).any():
        raise ValueError("Membership intervals must be non-empty half-open ranges")
    for symbol, group in membership.sort_values("start_date").groupby("symbol"):
        previous_end = None
        for row in group.itertuples():
            if previous_end is not None and row.start_date < previous_end:
                raise ValueError(f"Overlapping membership intervals for {symbol}")
            previous_end = row.end_date
    return membership


def point_in_time_membership_mask(featured: pd.DataFrame, membership: pd.DataFrame) -> pd.Series:
    membership = normalize_membership(membership)
    intervals = {
        symbol: list(zip(group["start_date"], group["end_date"]))
        for symbol, group in membership.groupby("symbol")
    }
    return pd.Series([
        any(start <= date < end for start, end in intervals.get(instrument, []))
        for date, instrument in zip(featured["datetime"], featured["instrument"])
    ], index=featured.index)


def apply_point_in_time_membership(featured: pd.DataFrame, path: Path) -> pd.DataFrame:
    keep = point_in_time_membership_mask(featured, pd.read_csv(path))
    return featured.loc[keep].copy()


def load_fundamentals(path: Path) -> pd.DataFrame:
    fundamentals = pd.read_csv(path)
    if "datetime" in fundamentals:
        fundamentals["datetime"] = pd.to_datetime(fundamentals["datetime"])
    return fundamentals


def _normalize_download(downloaded: pd.DataFrame, tickers: list[str]) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    if not isinstance(downloaded.columns, pd.MultiIndex):
        downloaded = pd.concat({tickers[0]: downloaded}, axis=1)
    available = set(downloaded.columns.get_level_values(0))
    for ticker in tickers:
        if ticker not in available:
            failed.append(ticker)
            continue
        frame = downloaded[ticker].copy()
        required = ["Open", "High", "Low", "Close", "Volume"]
        if any(column not in frame for column in required):
            failed.append(ticker)
            continue
        frame = frame[required].dropna(subset=["Open", "High", "Low", "Close"])
        if frame.empty:
            failed.append(ticker)
            continue
        frame["instrument"] = ticker
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "datetime"
        frames.append(frame.reset_index())
    if not frames:
        raise RuntimeError("Yahoo Finance returned no usable stock data")
    return pd.concat(frames, ignore_index=True), failed


def download_market_data(
    symbols: list[str],
    start: str,
    end: str | None,
    output_dir: Path,
    refresh: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    stocks_path = output_dir / "stocks.parquet"
    benchmark_path = output_dir / "qqq.parquet"
    if not refresh and stocks_path.exists() and benchmark_path.exists():
        return pd.read_parquet(stocks_path), pd.read_parquet(benchmark_path), []

    downloaded = yf.download(
        symbols,
        start=start,
        end=end,
        auto_adjust=True,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    stocks, failed = _normalize_download(downloaded, symbols)
    benchmark_raw = yf.download(
        ["QQQ"],
        start=start,
        end=end,
        auto_adjust=True,
        group_by="ticker",
        progress=False,
    )
    benchmark, benchmark_failed = _normalize_download(benchmark_raw, ["QQQ"])
    if benchmark_failed:
        raise RuntimeError("Failed to download QQQ benchmark")
    stocks.to_parquet(stocks_path, index=False)
    benchmark.to_parquet(benchmark_path, index=False)
    return stocks, benchmark, failed


def download_fred_features(
    start: str,
    end: str | None,
    output_dir: Path,
    refresh: bool,
) -> pd.DataFrame:
    cache = output_dir / "fred_macro.parquet"
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY is required unless --disable-fred is used")
    frames = []
    for feature_name, series_id in FRED_SERIES.items():
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
        }
        if end:
            params["observation_end"] = end
        response = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
        frame = pd.DataFrame(
            {
                "datetime": pd.to_datetime([row["date"] for row in observations]),
                feature_name: pd.to_numeric(
                    [row["value"] for row in observations], errors="coerce"
                ),
            }
        ).dropna()
        frames.append(frame.set_index("datetime"))
    macro = pd.concat(frames, axis=1).sort_index().ffill().reset_index()
    # FRED rates and spreads are percentages; use decimal units in the model.
    for column in ("rf_3m", "high_yield_spread", "fed_funds"):
        macro[column] /= 100
    macro.to_parquet(cache, index=False)
    return macro


def market_feature_frame(benchmark: pd.DataFrame, horizon: int, include_labels: bool = True) -> pd.DataFrame:
    market = benchmark.sort_values("datetime")[["datetime", "Open", "Close"]].copy()
    returns = market["Close"].pct_change()
    market["qqq_daily_return"] = returns
    market["qqq_ret_5"] = market["Close"].pct_change(5)
    market["qqq_ret_20"] = market["Close"].pct_change(20)
    market["qqq_volatility_20"] = returns.rolling(20).std()
    market["qqq_drawdown_60"] = market["Close"] / market["Close"].rolling(60).max() - 1
    if include_labels:
        market["qqq_forward_return"] = (
            market["Open"].shift(-(horizon + 1)) / market["Open"].shift(-1) - 1
        )
    return market.drop(columns=["Open", "Close"])


def _instrument_features(
    frame: pd.DataFrame,
    horizon: int,
    market_features: pd.DataFrame | None = None,
    include_labels: bool = True,
) -> pd.DataFrame:
    frame = frame.sort_values("datetime").reset_index(drop=True).copy()
    close = frame["Close"]
    open_price = frame["Open"]
    volume = frame["Volume"].replace(0, np.nan)
    result = frame[["datetime", "instrument"]].copy()
    for lag in (1, 5, 10, 20, 60):
        result[f"ret_{lag}"] = close.pct_change(lag)
    for window in (5, 10, 20, 60):
        result[f"ma_ratio_{window}"] = close / close.rolling(window).mean() - 1
    for window in (10, 20, 60):
        result[f"volatility_{window}"] = close.pct_change().rolling(window).std()
    for window in (5, 20):
        result[f"volume_ratio_{window}"] = volume / volume.rolling(window).mean() - 1
    result["intraday_range"] = (frame["High"] - frame["Low"]) / close
    result["open_gap"] = open_price / close.shift(1) - 1
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    result["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    daily_return = close.pct_change()
    result["downside_volatility_20"] = daily_return.clip(upper=0).rolling(20).std()
    result["drawdown_60"] = close / close.rolling(60).max() - 1
    result["return_skew_20"] = daily_return.rolling(20).skew()
    dollar_volume = close * volume
    result["dollar_volume_ratio_20"] = dollar_volume / dollar_volume.rolling(20).mean() - 1
    result["amihud_20"] = (daily_return.abs() / dollar_volume).rolling(20).mean()

    if market_features is not None:
        result = result.merge(market_features, on="datetime", how="left")
        covariance = daily_return.rolling(60).cov(result["qqq_daily_return"])
        market_variance = result["qqq_daily_return"].rolling(60).var()
        result["beta_60"] = covariance / market_variance.replace(0, np.nan)
        result["alpha_60"] = (
            daily_return.rolling(60).mean()
            - result["beta_60"] * result["qqq_daily_return"].rolling(60).mean()
        )
        result["qqq_corr_60"] = daily_return.rolling(60).corr(result["qqq_daily_return"])
        residual = daily_return - result["beta_60"] * result["qqq_daily_return"]
        result["residual_volatility_60"] = residual.rolling(60).std()
        result["relative_ret_20"] = close.pct_change(20) - result["qqq_ret_20"]
        result["relative_ret_60"] = close.pct_change(60) - (
            (1 + result["qqq_daily_return"]).rolling(60).apply(np.prod, raw=True) - 1
        )

    if include_labels:
        # Signal is observed at close(t); execution is open(t+1), avoiding same-close execution.
        result["forward_return"] = open_price.shift(-(horizon + 1)) / open_price.shift(-1) - 1
        result["entry_open"] = open_price.shift(-1)
        result["exit_open"] = open_price.shift(-(horizon + 1))
    return result


def build_feature_frame(
    stocks: pd.DataFrame,
    horizon: int,
    benchmark: pd.DataFrame | None = None,
    macro: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
    label_mode: str = "cross_sectional_median",
    include_labels: bool = True,
    membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    market_features = (
        market_feature_frame(benchmark, horizon, include_labels=include_labels)
        if benchmark is not None
        else None
    )
    featured = pd.concat(
        [
            _instrument_features(frame, horizon, market_features, include_labels=include_labels)
            for _, frame in stocks.groupby("instrument", sort=False)
        ],
        ignore_index=True,
    )
    extra_columns = RISK_FEATURE_COLUMNS + MARKET_FEATURE_COLUMNS if benchmark is not None else []
    if macro is not None:
        featured = pd.merge_asof(
            featured.sort_values("datetime"),
            macro.sort_values("datetime"),
            on="datetime",
            direction="backward",
        )
        extra_columns += MACRO_FEATURE_COLUMNS
    if fundamentals is not None:
        required = {"datetime", "instrument"}
        if not required.issubset(fundamentals.columns):
            raise ValueError("Fundamentals file requires datetime and instrument columns")
        fundamental_columns = [
            column
            for column in fundamentals.columns
            if column.startswith("fundamental_")
            and pd.api.types.is_numeric_dtype(fundamentals[column])
        ]
        if not fundamental_columns:
            raise ValueError("Fundamentals file has no fundamental_* columns")
        joined = []
        for instrument, frame in featured.groupby("instrument", sort=False):
            source = fundamentals.loc[fundamentals["instrument"] == instrument].copy()
            if source.empty:
                joined.append(frame)
                continue
            source["datetime"] = pd.to_datetime(source["datetime"])
            joined.append(
                pd.merge_asof(
                    frame.sort_values("datetime"),
                    source[["datetime"] + fundamental_columns].sort_values("datetime"),
                    on="datetime",
                    direction="backward",
                )
            )
        featured = pd.concat(joined, ignore_index=True)
        extra_columns += fundamental_columns
    featured.replace([np.inf, -np.inf], np.nan, inplace=True)
    time_series_columns = [
        column
        for column in MARKET_FEATURE_COLUMNS + MACRO_FEATURE_COLUMNS
        if column in extra_columns
    ]
    cross_sectional_columns = [
        column for column in FEATURE_COLUMNS + extra_columns if column not in time_series_columns
    ]
    featured.dropna(subset=cross_sectional_columns, inplace=True)
    if membership is not None:
        # Time-series features are already warm because raw history was retained;
        # only eligible members enter same-date ranks, labels, and selection.
        featured = featured.loc[point_in_time_membership_mask(featured, membership)].copy()

    # Stock-specific features use daily cross-sectional ranks.
    if "beta_60" in featured:
        featured["portfolio_beta_60"] = featured["beta_60"]
    ranked = featured.groupby("datetime")[cross_sectional_columns].rank(pct=True)
    featured[cross_sectional_columns] = ranked * 2 - 1

    # Market and macro variables are identical for every stock on a date; daily
    # cross-sectional ranking would erase them. Use trailing-only z-scores.
    availability_columns = []
    if time_series_columns:
        daily = featured.groupby("datetime")[time_series_columns].first().sort_index()
        for column in time_series_columns:
            available = daily[column].notna().astype(float)
            if not available.all():
                availability_column = f"{column}_available"
                availability_columns.append(availability_column)
                featured[availability_column] = featured["datetime"].map(available)
            trailing_mean = daily[column].rolling(252, min_periods=60).mean()
            trailing_std = daily[column].rolling(252, min_periods=60).std()
            normalized = ((daily[column] - trailing_mean) / trailing_std.replace(0, np.nan)).fillna(0)
            featured[column] = featured["datetime"].map(normalized)
    model_features = cross_sectional_columns + time_series_columns + availability_columns
    if not include_labels:
        featured.attrs["feature_columns"] = model_features
        return featured
    if label_mode == "benchmark_relative":
        if benchmark is None:
            raise ValueError("benchmark_relative label requires benchmark data")
        featured["target"] = featured["forward_return"] - featured["qqq_forward_return"]
    elif label_mode == "beta_adjusted":
        if benchmark is None:
            raise ValueError("beta_adjusted label requires benchmark data")
        featured["target"] = (
            featured["forward_return"]
            - featured["portfolio_beta_60"] * featured["qqq_forward_return"]
        )
    elif label_mode == "cross_sectional_median":
        daily_median = featured.groupby("datetime")["forward_return"].transform("median")
        featured["target"] = featured["forward_return"] - daily_median
    else:
        raise ValueError(f"Unsupported label mode: {label_mode}")
    featured.attrs["feature_columns"] = model_features
    return featured


def build_feature_only_frame(
    stocks: pd.DataFrame,
    horizon: int,
    benchmark: pd.DataFrame | None = None,
    macro: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
    membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build inference features without computing or requiring future labels."""
    return build_feature_frame(
        stocks,
        horizon,
        benchmark=benchmark,
        macro=macro,
        fundamentals=fundamentals,
        include_labels=False,
        membership=membership,
    )


def make_segments(
    dates: pd.DatetimeIndex,
    valid_years: int,
    test_years: int,
    purge_days: int = 5,
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    dates = pd.DatetimeIndex(sorted(pd.unique(dates)))
    last = dates[-1]
    test_start_target = last - pd.DateOffset(years=test_years)
    valid_start_target = test_start_target - pd.DateOffset(years=valid_years)
    test_start_idx = dates.searchsorted(test_start_target)
    valid_start_idx = dates.searchsorted(valid_start_target)
    train_end_idx = valid_start_idx - purge_days - 1
    valid_end_idx = test_start_idx - purge_days - 1
    if train_end_idx < 252 or valid_end_idx < valid_start_idx:
        raise ValueError("Not enough history for the requested train/valid/test split")
    return {
        "train": (dates[0], dates[train_end_idx]),
        "valid": (dates[valid_start_idx], dates[valid_end_idx]),
        "test": (dates[test_start_idx], dates[-1]),
    }


def qlib_frame(
    featured: pd.DataFrame,
    feature_columns: list[str] | None = None,
    include_label: bool = True,
) -> pd.DataFrame:
    feature_columns = feature_columns or FEATURE_COLUMNS
    source_columns = feature_columns + (["target"] if include_label else [])
    column_tuples = [("feature", name) for name in feature_columns]
    if include_label:
        column_tuples.append(("label", "relative_forward_return"))
    columns = pd.MultiIndex.from_tuples(column_tuples)
    frame = featured[source_columns].copy()
    frame.columns = columns
    frame.index = pd.MultiIndex.from_frame(featured[["datetime", "instrument"]])
    return frame.sort_index()


def rank_ic(predictions: pd.Series, labels: pd.Series) -> pd.Series:
    aligned = pd.concat([predictions.rename("score"), labels.rename("label")], axis=1).dropna()
    return aligned.groupby(level="datetime").apply(
        lambda frame: frame["score"].corr(frame["label"], method="spearman") if len(frame) >= 5 else np.nan
    )


MODEL_PARAMS = {
    "loss": "mse",
    "learning_rate": 0.03,
    "max_depth": 5,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l1": 0.2,
    "lambda_l2": 2.0,
    "num_threads": 4,
    "seed": 42,
    "num_boost_round": 600,
    "early_stopping_rounds": 50,
}


class LGBRankModel:
    """Minimal Qlib-compatible LightGBM LambdaRank model."""

    def __init__(self):
        self.model = None

    @staticmethod
    def _dataset(dataset: FrameDataset, segment: str) -> lgb.Dataset:
        frame = dataset.prepare(segment, col_set=["feature", "label"])
        features = frame["feature"]
        target = frame["label"].iloc[:, 0]
        relevance = target.groupby(level="datetime").rank(pct=True)
        relevance = np.minimum((relevance * 10).astype(int), 9)
        group = frame.groupby(level="datetime", sort=False).size().to_numpy()
        return lgb.Dataset(
            features.to_numpy(),
            label=relevance.to_numpy(),
            group=group,
            free_raw_data=False,
            feature_name=list(features.columns),
        )

    def fit(self, dataset: FrameDataset, verbose_eval: int = 0):
        train = self._dataset(dataset, "train")
        valid = self._dataset(dataset, "valid")
        self.model = lgb.train(
            {
                **{key: value for key, value in MODEL_PARAMS.items() if key not in {
                    "loss", "num_boost_round", "early_stopping_rounds"
                }},
                "objective": "lambdarank",
                "metric": "ndcg",
                "ndcg_eval_at": [5, 10],
                "verbosity": -1,
            },
            train,
            num_boost_round=MODEL_PARAMS["num_boost_round"],
            valid_sets=[train, valid],
            valid_names=["train", "valid"],
            callbacks=[
                lgb.early_stopping(MODEL_PARAMS["early_stopping_rounds"]),
                lgb.log_evaluation(period=verbose_eval),
            ],
        )

    def predict(self, dataset: FrameDataset, segment: str = "test") -> pd.Series:
        features = dataset.prepare(segment, col_set="feature")
        return pd.Series(self.model.predict(features.to_numpy()), index=features.index)


def make_model(objective: str = "mse"):
    return LGBModel(**MODEL_PARAMS) if objective == "mse" else LGBRankModel()


def walk_forward_predict(
    frame: pd.DataFrame,
    feature_columns: list[str],
    test_segment: tuple[pd.Timestamp, pd.Timestamp],
    validation_years: int,
    block_years: int,
    purge_days: int,
    objective: str = "mse",
) -> tuple[pd.Series, object, list[dict[str, str]]]:
    dates = pd.DatetimeIndex(sorted(frame.index.get_level_values("datetime").unique()))
    test_dates = dates[(dates >= test_segment[0]) & (dates <= test_segment[1])]
    predictions = []
    windows = []
    final_model = None
    block_start = test_dates[0]
    while block_start <= test_dates[-1]:
        next_start = block_start + pd.DateOffset(years=block_years)
        candidates = test_dates[test_dates <= next_start]
        block_end = candidates[-1] if len(candidates) else test_dates[-1]
        test_start_idx = dates.get_loc(block_start)
        valid_start_target = block_start - pd.DateOffset(years=validation_years)
        valid_start_idx = dates.searchsorted(valid_start_target)
        train_end_idx = valid_start_idx - purge_days - 1
        valid_end_idx = test_start_idx - purge_days - 1
        if train_end_idx < 252 or valid_end_idx < valid_start_idx:
            raise ValueError("Not enough data for walk-forward window")
        segments = {
            "train": (dates[0], dates[train_end_idx]),
            "valid": (dates[valid_start_idx], dates[valid_end_idx]),
            "test": (block_start, block_end),
        }
        dataset = FrameDataset(frame, segments)
        model = make_model(objective)
        model.fit(dataset, verbose_eval=0)
        predictions.append(model.predict(dataset, "test"))
        windows.append(
            {
                name: f"{start.date().isoformat()}:{end.date().isoformat()}"
                for name, (start, end) in segments.items()
            }
        )
        final_model = model
        remaining = test_dates[test_dates > block_end]
        if not len(remaining):
            break
        block_start = remaining[0]
    if final_model is None:
        raise RuntimeError("Walk-forward produced no fitted model")
    return pd.concat(predictions).sort_index(), final_model, windows


def _max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns.fillna(0)).cumprod()
    return float((equity / equity.cummax() - 1).min())


def performance_metrics(returns: pd.Series, periods_per_year: float) -> dict[str, float]:
    returns = returns.dropna()
    if returns.empty:
        return {}
    equity = float((1 + returns).prod())
    years = len(returns) / periods_per_year
    volatility = float(returns.std(ddof=1) * math.sqrt(periods_per_year))
    return {
        "periods": int(len(returns)),
        "total_return": equity - 1,
        "annualized_return": equity ** (1 / years) - 1 if years > 0 and equity > 0 else np.nan,
        "annualized_volatility": volatility,
        "sharpe": float(returns.mean() / returns.std(ddof=1) * math.sqrt(periods_per_year))
        if returns.std(ddof=1) > 0
        else np.nan,
        "max_drawdown": _max_drawdown(returns),
        "win_rate": float((returns > 0).mean()),
    }


def run_weekly_backtest(
    featured: pd.DataFrame,
    predictions: pd.Series,
    benchmark: pd.DataFrame,
    top_k: int,
    horizon: int,
    cost_bps: float,
    max_net_beta: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scored = featured.set_index(["datetime", "instrument"]).join(predictions.rename("score"), how="inner")
    signal_dates = pd.DatetimeIndex(sorted(scored.index.get_level_values("datetime").unique()))
    rebalance_dates = signal_dates[::horizon]
    qqq = benchmark.set_index("datetime").sort_index()["Open"]
    previous_long: set[str] = set()
    previous_short: set[str] = set()
    rows: list[dict] = []
    holdings: list[dict] = []

    for signal_date in rebalance_dates:
        cross_section = scored.xs(signal_date).dropna(subset=["score", "forward_return"])
        if len(cross_section) < top_k:
            continue
        long_pool = cross_section.nlargest(top_k * 3, "score")
        short_pool = cross_section.nsmallest(top_k * 3, "score")
        longs = long_pool.head(top_k)
        shorts = short_pool.head(top_k)
        if max_net_beta and "portfolio_beta_60" in cross_section:
            score_scale = float(cross_section["score"].std()) or 1.0
            best = None
            for penalty in np.linspace(-score_scale * 4, score_scale * 4, 81):
                long_rank = long_pool["score"] + penalty * long_pool["portfolio_beta_60"]
                short_rank = short_pool["score"] + penalty * short_pool["portfolio_beta_60"]
                candidate_longs = long_pool.loc[long_rank.nlargest(top_k).index]
                candidate_shorts = short_pool.loc[short_rank.nsmallest(top_k).index]
                net_beta = 0.5 * (
                    candidate_longs["portfolio_beta_60"].mean()
                    - candidate_shorts["portfolio_beta_60"].mean()
                )
                score_spread = (
                    candidate_longs["score"].mean() - candidate_shorts["score"].mean()
                )
                feasible = abs(net_beta) <= max_net_beta
                quality = (0 if feasible else 1, abs(net_beta), -score_spread)
                if best is None or quality < best[0]:
                    best = (quality, candidate_longs, candidate_shorts)
            _, longs, shorts = best
        if len(longs) < top_k or len(shorts) < top_k:
            continue
        current_long = set(longs.index)
        current_short = set(shorts.index)
        if not previous_long:
            long_turnover = short_turnover = 1.0
        else:
            long_turnover = len(current_long.symmetric_difference(previous_long)) / (2 * top_k)
            short_turnover = len(current_short.symmetric_difference(previous_short)) / (2 * top_k)
        turnover = 0.5 * (long_turnover + short_turnover)
        long_return = float(longs["forward_return"].mean())
        short_return = float(shorts["forward_return"].mean())
        # Dollar-neutral, 100% gross exposure: 50% long and 50% short.
        gross_return = 0.5 * (long_return - short_return)
        transaction_cost = turnover * cost_bps / 10_000
        portfolio_beta = (
            0.5
            * (
                longs["portfolio_beta_60"].mean()
                - shorts["portfolio_beta_60"].mean()
            )
            if "portfolio_beta_60" in longs
            else np.nan
        )

        entry_candidates = qqq.index[qqq.index > signal_date]
        if len(entry_candidates) <= horizon:
            continue
        entry_date = entry_candidates[0]
        exit_date = entry_candidates[horizon]
        benchmark_return = float(qqq.loc[exit_date] / qqq.loc[entry_date] - 1)
        rows.append(
            {
                "signal_date": signal_date,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "gross_return": gross_return,
                "long_return": long_return,
                "short_return": short_return,
                "long_turnover": long_turnover,
                "short_turnover": short_turnover,
                "turnover": turnover,
                "transaction_cost": transaction_cost,
                "net_return": gross_return - transaction_cost,
                "benchmark_return": benchmark_return,
                "excess_return": gross_return - transaction_cost - benchmark_return,
                "portfolio_beta": float(portfolio_beta),
            }
        )
        for side, selected in (("long", longs), ("short", shorts)):
            holdings.extend(
                {
                    "signal_date": signal_date,
                    "instrument": ticker,
                    "side": side,
                    "weight": 0.5 / top_k if side == "long" else -0.5 / top_k,
                    "score": float(row["score"]),
                    "forward_return": float(row["forward_return"]),
                }
                for ticker, row in selected.iterrows()
            )
        previous_long = current_long
        previous_short = current_short
    return pd.DataFrame(rows), pd.DataFrame(holdings)


def main() -> None:
    load_local_env()
    args = parse_args()
    if args.horizon <= 0 or args.top_k <= 0 or args.cost_bps < 0:
        raise ValueError("horizon/top-k must be positive and cost-bps cannot be negative")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    symbols, universe = load_universe(Path(args.universe_file), args.max_stocks)
    stocks, benchmark, failed = download_market_data(
        symbols, args.start, args.end, output_dir, args.refresh
    )
    macro = (
        None
        if args.disable_fred
        else download_fred_features(args.start, args.end, output_dir, args.refresh)
    )
    fundamentals = load_fundamentals(Path(args.fundamentals_file)) if args.fundamentals_file else None
    membership = pd.read_csv(args.membership_file) if args.membership_file else None
    featured = build_feature_frame(
        stocks,
        args.horizon,
        benchmark=benchmark,
        macro=macro,
        fundamentals=fundamentals,
        label_mode=args.label_mode,
        membership=membership,
    )
    if args.membership_file:
        universe["point_in_time"] = True
        universe["membership_file"] = args.membership_file
    feature_columns = list(featured.attrs.get("feature_columns", FEATURE_COLUMNS))
    if args.feature_set == "base":
        feature_columns = FEATURE_COLUMNS.copy()
    labeled = featured.dropna(subset=["target"])
    segments = make_segments(
        labeled["datetime"],
        args.valid_years,
        args.test_years,
        purge_days=args.horizon,
    )
    model_frame = qlib_frame(labeled, feature_columns)
    dataset = FrameDataset(model_frame, segments)

    tracking_uri = f"sqlite:///{(output_dir / 'mlflow.db').as_posix()}"
    qlib.init(
        provider_uri=(output_dir / "unused_qlib_data").as_posix(),
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": tracking_uri, "default_exp_name": "nasdaq100_medium_low_frequency"},
        },
    )
    walk_forward_windows = []
    with R.start(experiment_name="nasdaq100_medium_low_frequency"):
        R.log_params(
            **MODEL_PARAMS,
            feature_count=len(feature_columns),
            feature_names=",".join(feature_columns),
            label_mode=args.label_mode,
            horizon=args.horizon,
            top_k=args.top_k,
            cost_bps=args.cost_bps,
            fred_enabled=macro is not None,
            fundamentals_enabled=fundamentals is not None,
            point_in_time_membership=bool(args.membership_file),
            walk_forward_years=args.walk_forward_years,
            objective=args.objective,
            feature_set=args.feature_set,
        )
        if args.walk_forward_years:
            predictions, model, walk_forward_windows = walk_forward_predict(
                model_frame,
                feature_columns,
                segments["test"],
                validation_years=args.valid_years,
                block_years=args.walk_forward_years,
                purge_days=args.horizon,
                objective=args.objective,
            )
        else:
            model = make_model(args.objective)
            model.fit(dataset, verbose_eval=0)
            predictions = model.predict(dataset, "test")
        R.save_objects(trained_model=model)

    labels = model_frame.loc[predictions.index, ("label", "relative_forward_return")]
    ic = rank_ic(predictions, labels)
    prediction_errors = predictions - labels
    direction_accuracy = float(
        (np.sign(predictions.to_numpy()) == np.sign(labels.to_numpy())).mean()
    )
    prediction_mae = float(prediction_errors.abs().mean())
    prediction_rmse = float(np.sqrt((prediction_errors**2).mean()))
    test_start, test_end = segments["test"]
    test_featured = featured.loc[featured["datetime"].between(test_start, test_end)]
    backtest, holdings = run_weekly_backtest(
        test_featured,
        predictions,
        benchmark,
        args.top_k,
        args.horizon,
        args.cost_bps,
        max_net_beta=args.max_net_beta if args.max_net_beta > 0 else None,
    )
    if backtest.empty:
        raise RuntimeError("Backtest produced no periods")

    booster = getattr(model, "model", None)
    feature_importance = pd.DataFrame(columns=["feature", "gain", "split"])
    if booster is not None and hasattr(booster, "feature_importance"):
        booster_names = booster.feature_name()
        importance_names = (
            feature_columns
            if len(booster_names) == len(feature_columns)
            and all(name.startswith("Column_") for name in booster_names)
            else booster_names
        )
        feature_importance = pd.DataFrame(
            {
                "feature": importance_names,
                "gain": booster.feature_importance(importance_type="gain"),
                "split": booster.feature_importance(importance_type="split"),
            }
        ).sort_values("gain", ascending=False)
    best_iteration = int(getattr(booster, "best_iteration", 0) or 0)

    periods_per_year = 252 / args.horizon
    result = {
        "research_only": True,
        "universe": universe,
        "survivorship_bias_warning": not universe["point_in_time"],
        "downloaded_securities": int(stocks["instrument"].nunique()),
        "failed_symbols": failed,
        "configuration": {
            "start": args.start,
            "end": args.end,
            "horizon_trading_days": args.horizon,
            "rebalance_every_trading_days": args.horizon,
            "top_k": args.top_k,
            "one_way_cost_bps": args.cost_bps,
            "max_net_beta": args.max_net_beta,
            "portfolio_mode": "dollar_neutral_long_short",
            "gross_exposure": 1.0,
            "net_exposure": 0.0,
            "label_mode": args.label_mode,
            "feature_count": len(feature_columns),
            "feature_columns": feature_columns,
            "fred_series": FRED_SERIES if macro is not None else {},
            "fundamentals_file": args.fundamentals_file,
            "membership_file": args.membership_file,
            "walk_forward_years": args.walk_forward_years,
            "best_iteration": best_iteration,
            "objective": args.objective,
            "feature_set": args.feature_set,
        },
        "walk_forward_windows": walk_forward_windows,
        "segments": {
            name: [start.date().isoformat(), end.date().isoformat()] for name, (start, end) in segments.items()
        },
        "prediction": {
            "observations": int(len(predictions)),
            "mean_daily_rank_ic": float(ic.mean()),
            "rank_ic_std": float(ic.std()),
            "rank_ic_ir": float(ic.mean() / ic.std()) if ic.std() > 0 else np.nan,
            "positive_rank_ic_rate": float((ic > 0).mean()),
            "direction_accuracy": direction_accuracy,
            "mae": prediction_mae,
            "rmse": prediction_rmse,
            "target": {
                "benchmark_relative": "qqq_relative_forward_return",
                "beta_adjusted": "beta_adjusted_forward_return",
                "cross_sectional_median": "cross_sectional_relative_forward_return",
            }[args.label_mode],
        },
        "strategy_net": performance_metrics(backtest["net_return"], periods_per_year),
        "benchmark_qqq": performance_metrics(backtest["benchmark_return"], periods_per_year),
        "excess": performance_metrics(backtest["excess_return"], periods_per_year),
        "average_turnover": float(backtest["turnover"].mean()),
        "average_portfolio_beta": float(backtest["portfolio_beta"].mean()),
        "top_feature_importance": feature_importance.head(15).to_dict("records"),
    }
    backtest.to_csv(output_dir / "backtest.csv", index=False)
    holdings.to_csv(output_dir / "holdings.csv", index=False)
    predictions.rename("score").to_csv(output_dir / "predictions.csv")
    ic.rename("rank_ic").to_csv(output_dir / "rank_ic.csv")
    feature_importance.to_csv(output_dir / "feature_importance.csv", index=False)
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
