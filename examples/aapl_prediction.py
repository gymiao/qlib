"""Train a Qlib LightGBM model on AAPL daily bars and predict the next close."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
import yfinance as yf
from qlib.contrib.model.gbdt import LGBModel
from qlib.data.dataset.handler import DataHandlerLP
from qlib.workflow import R


class FrameDataset:
    """Minimal DatasetH-compatible adapter for a time-indexed feature frame."""

    def __init__(self, frame: pd.DataFrame, segments: dict[str, tuple[pd.Timestamp, pd.Timestamp]]):
        self.frame = frame
        self.segments = segments

    def prepare(self, segment, col_set="feature", data_key=DataHandlerLP.DK_I):
        start, end = self.segments[segment] if isinstance(segment, str) else (segment.start, segment.stop)
        selected = self.frame.loc[(slice(start, end), slice(None)), :]
        if isinstance(col_set, str):
            return selected[col_set]
        return selected.loc[:, list(col_set)]


def make_features(raw: pd.DataFrame) -> pd.DataFrame:
    close = raw["Close"]
    volume = raw["Volume"].replace(0, np.nan)
    out = pd.DataFrame(index=raw.index)
    for lag in (1, 2, 3, 5, 10, 20, 60):
        out[f"ret_{lag}"] = close.pct_change(lag)
    for window in (5, 10, 20, 60):
        out[f"ma_ratio_{window}"] = close / close.rolling(window).mean() - 1
        out[f"volatility_{window}"] = close.pct_change().rolling(window).std()
        out[f"volume_ratio_{window}"] = volume / volume.rolling(window).mean() - 1
    out["intraday_range"] = (raw["High"] - raw["Low"]) / close
    out["open_gap"] = raw["Open"] / close.shift(1) - 1
    out["close_location"] = (close - raw["Low"]) / (raw["High"] - raw["Low"]).replace(0, np.nan)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    out["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out["target"] = close.shift(-1) / close - 1
    return out.replace([np.inf, -np.inf], np.nan)


def qlib_frame(features: pd.DataFrame) -> pd.DataFrame:
    columns = pd.MultiIndex.from_tuples(
        [("feature", name) for name in features.columns if name != "target"] + [("label", "next_return")]
    )
    values = features[[name for name in features.columns if name != "target"] + ["target"]].copy()
    values.columns = columns
    values.index = pd.MultiIndex.from_arrays(
        [values.index, np.repeat("AAPL", len(values))], names=["datetime", "instrument"]
    )
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--output-dir", default=".artifacts/aapl_prediction")
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--input-csv", type=Path, help="Optional cached daily OHLCV CSV")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.input_csv:
        raw = pd.read_csv(args.input_csv, index_col=0, parse_dates=True)
    else:
        raw = yf.download("AAPL", start=args.start, auto_adjust=True, progress=False, multi_level_index=False)
    if raw.empty:
        raise RuntimeError("Yahoo Finance returned no AAPL data")
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    raw.to_csv(output_dir / "aapl_daily.csv")

    features = make_features(raw)
    features = features.dropna(subset=[c for c in features.columns if c != "target"])
    labeled = features.dropna(subset=["target"])
    if len(labeled) < 800:
        raise RuntimeError(f"Not enough observations: {len(labeled)}")

    valid_size = min(252, len(labeled) // 5)
    test_size = min(252, len(labeled) // 5)
    train_end = labeled.index[-valid_size - test_size - 1]
    valid_start = labeled.index[-valid_size - test_size]
    valid_end = labeled.index[-test_size - 1]
    test_start = labeled.index[-test_size]
    test_end = labeled.index[-1]
    latest_date = features.index[-1]

    frame = qlib_frame(features)
    segments = {
        "train": (labeled.index[0], train_end),
        "valid": (valid_start, valid_end),
        "test": (test_start, test_end),
        "latest": (latest_date, latest_date),
    }
    dataset = FrameDataset(frame, segments)

    tracking_uri = f"sqlite:///{(output_dir / 'mlflow.db').as_posix()}"
    qlib.init(
        provider_uri=(output_dir / "unused_qlib_data").as_posix(),
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": tracking_uri, "default_exp_name": "aapl_prediction"},
        },
    )
    model = LGBModel(
        loss="mse",
        learning_rate=0.02,
        max_depth=args.max_depth,
        num_leaves=15,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        bagging_freq=1,
        lambda_l1=0.1,
        lambda_l2=1.0,
        num_threads=4,
        seed=42,
        num_boost_round=800,
        early_stopping_rounds=60,
    )
    with R.start(experiment_name="aapl_prediction"):
        model.fit(dataset, verbose_eval=0)

    predicted = model.predict(dataset, "test")
    actual = dataset.prepare("test", col_set="label").iloc[:, 0]
    residual = actual - predicted

    segment_metrics = {}
    for segment in ("train", "valid", "test"):
        segment_predicted = model.predict(dataset, segment)
        segment_actual = dataset.prepare(segment, col_set="label").iloc[:, 0]
        segment_residual = segment_actual - segment_predicted
        segment_metrics[segment] = {
            "observations": int(len(segment_actual)),
            "mae_return": float(np.mean(np.abs(segment_residual))),
            "correlation": float(segment_predicted.corr(segment_actual)),
        }

    latest_return = float(model.predict(dataset, "latest").iloc[0])
    latest_close = float(raw.loc[latest_date, "Close"])
    predicted_close = latest_close * (1 + latest_return)
    lower_return, upper_return = np.quantile(residual, [0.05, 0.95]) + latest_return

    result = {
        "ticker": "AAPL",
        "qlib_version": getattr(qlib, "__version__", "unknown"),
        "data_start": raw.index[0].date().isoformat(),
        "last_observation": latest_date.date().isoformat(),
        "last_adjusted_close": latest_close,
        "forecast_horizon": "next trading-day adjusted close",
        "model": {"max_depth": args.max_depth},
        "predicted_return": latest_return,
        "predicted_close": predicted_close,
        "empirical_90pct_interval": [latest_close * (1 + lower_return), latest_close * (1 + upper_return)],
        "split": {name: [start.date().isoformat(), end.date().isoformat()] for name, (start, end) in segments.items()},
        "holdout": {
            "observations": int(len(actual)),
            "mae_return": float(np.mean(np.abs(residual))),
            "rmse_return": float(np.sqrt(np.mean(residual**2))),
            "direction_accuracy": float(np.mean(np.sign(predicted) == np.sign(actual))),
            "correlation": float(predicted.corr(actual)),
            "zero_return_baseline_mae": float(np.mean(np.abs(actual))),
        },
        "segment_metrics": segment_metrics,
    }
    (output_dir / "prediction.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
