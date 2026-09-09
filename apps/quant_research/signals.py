"""Current model signal generation that never requires future labels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import numpy as np
import pandas as pd

from .contracts import SignalBatch


def _model_predictor(model: Any):
    predictor = getattr(model, "model", model)
    if not hasattr(predictor, "predict"):
        raise ValueError("model does not expose predict")
    return predictor


def generate_latest_signal(
    featured: pd.DataFrame,
    feature_columns: list[str],
    model: Any,
    model_id: str,
    strategy_id: str,
    data_snapshot_id: str,
    available_at: datetime,
    validity_days: int = 1,
    horizon: int = 5,
    target_kind: str = "relative_return_score",
) -> SignalBatch:
    if available_at.tzinfo is None:
        raise ValueError("available_at must include timezone")
    if validity_days <= 0 or horizon <= 0:
        raise ValueError("validity and horizon must be positive")
    missing = sorted({"datetime", "instrument", *feature_columns} - set(featured.columns))
    if missing:
        raise ValueError(f"feature frame is missing: {', '.join(missing)}")
    forbidden = {"target", "forward_return", "entry_open", "exit_open", "qqq_forward_return"}
    if forbidden.intersection(feature_columns):
        raise ValueError("signal features include future labels")
    latest = featured["datetime"].max()
    cross_section = featured.loc[featured["datetime"] == latest, ["instrument", *feature_columns]].copy()
    if cross_section.empty or cross_section[feature_columns].isna().any().any():
        raise ValueError("latest cross-section is empty or incomplete")
    scores = np.asarray(_model_predictor(model).predict(cross_section[feature_columns].to_numpy()), dtype=float)
    if scores.shape != (len(cross_section),) or not np.isfinite(scores).all():
        raise ValueError("model returned invalid scores")
    timestamp = available_at.astimezone(timezone.utc)
    identity = f"{strategy_id}|{model_id}|{data_snapshot_id}|{latest}|{timestamp.isoformat()}"
    return SignalBatch(
        signal_id=sha256(identity.encode()).hexdigest()[:24],
        strategy_id=strategy_id,
        generator_kind="model",
        generator_version="1",
        model_id=model_id,
        decision_time=timestamp.isoformat().replace("+00:00", "Z"),
        valid_until=(timestamp + timedelta(days=validity_days)).isoformat().replace("+00:00", "Z"),
        target_kind=target_kind,
        horizon=horizon,
        scores={str(symbol): float(score) for symbol, score in zip(cross_section["instrument"], scores)},
    )
