"""Point-in-time resolution for macro and company feature vintages."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .data_readiness import (
    CLASSIFICATION_VINTAGE_COLUMNS,
    EXPECTED_MACRO_UNITS,
    FUNDAMENTAL_COLUMNS,
    MACRO_VINTAGE_COLUMNS,
)


def _strict_utc(values: pd.Series, field: str) -> pd.Series:
    parsed = []
    for value in values:
        if pd.isna(value) or str(value).strip() == "":
            raise ValueError(f"{field} cannot be blank")
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{field} must include timezone")
        parsed.append(timestamp.tz_convert("UTC"))
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns, UTC]")


def decision_timestamps(
    dates: Iterable[object],
    timezone: str = "America/New_York",
    local_time: str = "18:00:00",
) -> pd.Series:
    try:
        offset = pd.Timedelta(local_time)
    except ValueError as exc:
        raise ValueError("local_time must be an HH:MM:SS duration") from exc
    if offset < pd.Timedelta(0) or offset >= pd.Timedelta(days=1):
        raise ValueError("local_time must fall within one day")
    sessions = pd.Series(pd.to_datetime(list(dates))).dt.tz_localize(None).dt.normalize()
    return (sessions + offset).dt.tz_localize(
        timezone,
        ambiguous="raise",
        nonexistent="raise",
    ).dt.tz_convert("UTC")


def load_macro_vintages(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted(MACRO_VINTAGE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"macro vintages are missing columns: {', '.join(missing)}")
    for column in ("observation_at", "published_at", "available_at"):
        frame[column] = _strict_utc(frame[column], column)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    if frame["value"].isna().any() or not np.isfinite(frame["value"]).all():
        raise ValueError("macro vintage values must be finite")
    if frame.groupby("feature_name")["series_id"].nunique().gt(1).any():
        raise ValueError("one feature_name cannot map to multiple macro series")
    invalid_unit = ~frame.apply(
        lambda row: EXPECTED_MACRO_UNITS.get(str(row["feature_name"])) == str(row["unit"]),
        axis=1,
    )
    if invalid_unit.any():
        raise ValueError("macro feature_name or unit does not match the frozen research schema")
    return frame.sort_values(["feature_name", "observation_at", "available_at"]).reset_index(drop=True)


def load_fundamental_vintages(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted(FUNDAMENTAL_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"fundamental vintages are missing columns: {', '.join(missing)}")
    feature_columns = [column for column in frame if column.startswith("fundamental_")]
    if not feature_columns:
        raise ValueError("fundamental vintages require at least one fundamental_* column")
    for column in ("observation_at", "published_at", "available_at"):
        frame[column] = _strict_utc(frame[column], column)
    frame[feature_columns] = frame[feature_columns].apply(pd.to_numeric, errors="coerce")
    values = frame[feature_columns].to_numpy(dtype=float)
    if np.isnan(values).all(axis=1).any() or np.isinf(values).any():
        raise ValueError("each fundamental vintage requires finite values")
    return frame.sort_values(["instrument_id", "observation_at", "available_at"]).reset_index(drop=True)


def load_classification_vintages(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = sorted(CLASSIFICATION_VINTAGE_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"classification vintages are missing columns: {', '.join(missing)}")
    for column in ("observation_at", "published_at", "available_at"):
        frame[column] = _strict_utc(frame[column], column)
    frame["market_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
    invalid = (
        frame["sector"].astype(str).str.strip().eq("")
        | frame["market_cap"].isna()
        | ~np.isfinite(frame["market_cap"])
        | (frame["market_cap"] <= 0)
        | (frame["published_at"] < frame["observation_at"])
        | (frame["available_at"] < frame["published_at"])
    )
    if invalid.any() or frame.duplicated(["instrument_id", "observation_at", "revision_id"]).any():
        raise ValueError("classification vintages contain invalid values or duplicate revisions")
    return frame.sort_values(["instrument_id", "observation_at", "available_at"]).reset_index(drop=True)


def _latest_vintage(source: pd.DataFrame, decision: pd.Timestamp) -> pd.Series | None:
    known = source.loc[source["available_at"] <= decision]
    if known.empty:
        return None
    observation = known["observation_at"].max()
    return known.loc[known["observation_at"] == observation].sort_values("available_at").iloc[-1]


def attach_macro_vintages(
    featured: pd.DataFrame,
    vintages: pd.DataFrame,
    *,
    timezone: str = "America/New_York",
    local_time: str = "18:00:00",
) -> tuple[pd.DataFrame, list[str]]:
    decisions = pd.DataFrame({"datetime": pd.to_datetime(featured["datetime"]).drop_duplicates().sort_values()})
    decisions["decision_time_utc"] = decision_timestamps(decisions["datetime"], timezone, local_time).to_numpy()
    feature_names = sorted(vintages["feature_name"].unique())
    for feature_name in feature_names:
        source = vintages.loc[vintages["feature_name"] == feature_name]
        decisions[feature_name] = [
            np.nan if (row := _latest_vintage(source, decision)) is None else float(row["value"])
            for decision in decisions["decision_time_utc"]
        ]
    result = featured.merge(decisions.drop(columns="decision_time_utc"), on="datetime", how="left")
    return result, feature_names


def attach_fundamental_vintages(
    featured: pd.DataFrame,
    vintages: pd.DataFrame,
    *,
    timezone: str = "America/New_York",
    local_time: str = "18:00:00",
) -> tuple[pd.DataFrame, list[str]]:
    if "instrument_id" not in featured:
        raise ValueError("canonical fundamental vintages require stable instrument_id in market data")
    feature_columns = [column for column in vintages if column.startswith("fundamental_")]
    decisions = featured[["datetime", "instrument_id"]].drop_duplicates().copy()
    decisions["decision_time_utc"] = decision_timestamps(decisions["datetime"], timezone, local_time).to_numpy()
    records = []
    for instrument_id, group in decisions.groupby("instrument_id", sort=False):
        source = vintages.loc[vintages["instrument_id"].astype(str) == str(instrument_id)]
        for row in group.itertuples(index=False):
            vintage = _latest_vintage(source, row.decision_time_utc) if not source.empty else None
            values = {column: np.nan if vintage is None else float(vintage[column]) for column in feature_columns}
            records.append({"datetime": row.datetime, "instrument_id": instrument_id, **values})
    resolved = pd.DataFrame(records)
    result = featured.merge(resolved, on=["datetime", "instrument_id"], how="left")
    return result, feature_columns


def attach_classification_vintages(
    featured: pd.DataFrame,
    vintages: pd.DataFrame,
    *,
    timezone: str = "America/New_York",
    local_time: str = "18:00:00",
) -> pd.DataFrame:
    if "instrument_id" not in featured:
        raise ValueError("classification vintages require stable instrument_id in market data")
    decisions = featured[["datetime", "instrument_id"]].drop_duplicates().copy()
    decisions["decision_time_utc"] = decision_timestamps(
        decisions["datetime"], timezone, local_time
    ).to_numpy()
    records = []
    for instrument_id, group in decisions.groupby("instrument_id", sort=False):
        source = vintages.loc[vintages["instrument_id"].astype(str) == str(instrument_id)]
        for row in group.itertuples(index=False):
            vintage = _latest_vintage(source, row.decision_time_utc) if not source.empty else None
            records.append({
                "datetime": row.datetime,
                "instrument_id": instrument_id,
                "sector": None if vintage is None else str(vintage["sector"]),
                "market_cap": np.nan if vintage is None else float(vintage["market_cap"]),
            })
    return featured.merge(pd.DataFrame(records), on=["datetime", "instrument_id"], how="left")
