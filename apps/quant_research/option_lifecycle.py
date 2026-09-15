"""Canonical observed option lifecycle events used by historical replay."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_LIFECYCLE_COLUMNS = {
    "event_id",
    "contract_id",
    "event_type",
    "effective_at",
    "available_at",
    "contracts",
    "source",
    "source_kind",
}


def validate_option_lifecycle_events(events: pd.DataFrame) -> pd.DataFrame:
    """Normalize and validate observed lifecycle events without inferring any."""
    missing = sorted(REQUIRED_LIFECYCLE_COLUMNS - set(events.columns))
    if missing:
        raise ValueError(f"option lifecycle events are missing: {', '.join(missing)}")
    frame = events.copy()
    for column in ("effective_at", "available_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame["contracts"] = pd.to_numeric(frame["contracts"], errors="coerce")
    frame["event_type"] = frame["event_type"].astype(str).str.strip().str.lower()
    invalid = (
        frame[list(REQUIRED_LIFECYCLE_COLUMNS)].isna().any(axis=1)
        | ~np.isfinite(frame["contracts"])
        | (frame["contracts"] <= 0)
        | (frame["contracts"] != np.floor(frame["contracts"]))
        | (frame["available_at"] < frame["effective_at"])
        | ~frame["event_type"].isin({"early_assignment"})
        | ~frame["source_kind"].astype(str).str.strip().str.lower().isin(
            {"historical_observed", "synthetic", "scenario_only"}
        )
    )
    for column in ("event_id", "contract_id", "source", "source_kind"):
        invalid |= frame[column].astype(str).str.strip().eq("")
    if invalid.any() or frame["event_id"].duplicated().any():
        raise ValueError("option lifecycle events contain invalid values or duplicate event IDs")
    frame["contracts"] = frame["contracts"].astype(int)
    return frame.sort_values(["available_at", "effective_at", "event_id"]).reset_index(drop=True)


def load_option_lifecycle_events(path: Path | str) -> pd.DataFrame:
    return validate_option_lifecycle_events(pd.read_csv(path))
