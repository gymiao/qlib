"""Current model signal generation that never requires future labels."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from .contracts import FeatureBatch, SignalBatch, canonical_hash


def _model_predictor(model: Any):
    predictor = getattr(model, "model", model)
    if not hasattr(predictor, "predict"):
        raise ValueError("model does not expose predict")
    return predictor


def build_latest_feature_batch(
    featured: pd.DataFrame,
    feature_columns: list[str],
    data_snapshot_id: str,
    decision_time: datetime,
) -> FeatureBatch:
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must include timezone")
    missing = sorted({"datetime", "instrument", *feature_columns} - set(featured.columns))
    if missing:
        raise ValueError(f"feature frame is missing: {', '.join(missing)}")
    forbidden = {"target", "forward_return", "entry_open", "exit_open", "qqq_forward_return"}
    if forbidden.intersection(feature_columns):
        raise ValueError("signal features include future labels")
    latest = featured["datetime"].max()
    identifier = "instrument_id" if "instrument_id" in featured else "instrument"
    columns = [identifier, *feature_columns]
    cross_section = featured.loc[featured["datetime"] == latest, columns].copy()
    if cross_section.empty or cross_section[columns].isna().any().any():
        raise ValueError("latest cross-section is empty or incomplete")
    identifiers = cross_section[identifier].astype(str)
    if identifiers.str.strip().eq("").any() or identifiers.duplicated().any():
        raise ValueError("latest cross-section identifiers must be unique and non-empty")
    values = cross_section[feature_columns].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("latest cross-section contains non-finite features")
    rows = tuple(
        {
            "instrument_id": instrument_id,
            **{column: float(value) for column, value in zip(feature_columns, row_values)},
        }
        for instrument_id, row_values in zip(identifiers, values)
    )
    feature_schema_id = canonical_hash({"columns": feature_columns, "version": "latest_cross_section.v1"})[:20]
    timestamp = decision_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return FeatureBatch(
        feature_schema_id=feature_schema_id,
        data_snapshot_id=data_snapshot_id,
        decision_time=timestamp,
        columns=tuple(feature_columns),
        rows=rows,
    )


def generate_signal_from_feature_batch(
    feature_batch: FeatureBatch,
    model: Any,
    model_id: str,
    strategy_id: str,
    validity_days: int = 1,
    horizon: int = 5,
    target_kind: str = "relative_return_score",
) -> SignalBatch:
    if validity_days <= 0 or horizon <= 0:
        raise ValueError("validity and horizon must be positive")
    if not feature_batch.rows:
        raise ValueError("feature batch is empty")
    identifiers = [row.get("instrument_id") for row in feature_batch.rows]
    if any(not isinstance(value, str) or not value for value in identifiers) or len(set(identifiers)) != len(
        identifiers
    ):
        raise ValueError("feature batch identifiers must be unique non-empty strings")
    try:
        values = np.asarray(
            [[row[column] for column in feature_batch.columns] for row in feature_batch.rows],
            dtype=float,
        )
    except KeyError as error:
        raise ValueError(f"feature batch row is missing column: {error.args[0]}") from error
    if not np.isfinite(values).all():
        raise ValueError("feature batch contains non-finite values")
    scores = np.asarray(_model_predictor(model).predict(values), dtype=float)
    if scores.shape != (len(feature_batch.rows),) or not np.isfinite(scores).all():
        raise ValueError("model returned invalid scores")
    timestamp = datetime.fromisoformat(feature_batch.decision_time.replace("Z", "+00:00")).astimezone(timezone.utc)
    identity = {
        "strategy_id": strategy_id,
        "model_id": model_id,
        "generator_version": "1",
        "feature_batch_hash": canonical_hash(asdict(feature_batch)),
        "decision_time": timestamp.isoformat(),
        "validity_days": validity_days,
        "horizon": horizon,
        "target_kind": target_kind,
    }
    return SignalBatch(
        signal_id=canonical_hash(identity)[:24],
        strategy_id=strategy_id,
        generator_kind="model",
        generator_version="1",
        model_id=model_id,
        decision_time=timestamp.isoformat().replace("+00:00", "Z"),
        valid_until=(timestamp + timedelta(days=validity_days)).isoformat().replace("+00:00", "Z"),
        target_kind=target_kind,
        horizon=horizon,
        scores={instrument_id: float(score) for instrument_id, score in zip(identifiers, scores)},
    )


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
    feature_batch = build_latest_feature_batch(
        featured,
        feature_columns,
        data_snapshot_id,
        available_at,
    )
    return generate_signal_from_feature_batch(
        feature_batch,
        model,
        model_id,
        strategy_id,
        validity_days=validity_days,
        horizon=horizon,
        target_kind=target_kind,
    )
