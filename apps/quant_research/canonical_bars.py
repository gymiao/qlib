"""Load audited canonical daily bars into the legacy Nasdaq feature shape."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .vintage_features import decision_timestamps


def _strict_utc(values: pd.Series, field: str, nullable: bool = False) -> pd.Series:
    parsed = []
    for value in values:
        if pd.isna(value) or str(value).strip() == "":
            if nullable:
                parsed.append(pd.NaT)
                continue
            raise ValueError(f"{field} cannot be blank")
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            raise ValueError(f"{field} must include timezone")
        parsed.append(timestamp.tz_convert("UTC"))
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns, UTC]")


def load_canonical_market_data(
    bars_csv: Path | str,
    instrument_master: pd.DataFrame,
    symbols: Iterable[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    benchmark_symbol: str = "QQQ",
    session_timezone: str = "America/New_York",
    decision_time: str = "18:00:00",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Map stable IDs to time-valid symbols and return adjusted daily frames.

    The canonical bar schema stores raw OHLCV and an adjusted/raw price factor.
    Prices are multiplied by the factor and volume is divided by it, which keeps
    split-adjusted price and volume histories on the same basis.
    """

    requested = list(dict.fromkeys(str(symbol).strip() for symbol in symbols))
    if not requested or any(not symbol for symbol in requested):
        raise ValueError("symbols must contain non-blank values")
    start_at = pd.Timestamp(start)
    end_at = pd.Timestamp(end)
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("canonical bar start and end must include timezone")
    start_at = start_at.tz_convert("UTC")
    end_at = end_at.tz_convert("UTC")
    if end_at <= start_at:
        raise ValueError("canonical bar end must be later than start")

    bars = pd.read_csv(bars_csv, dtype=str, keep_default_na=False)
    bar_required = {
        "instrument_id",
        "timestamp",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjustment_factor",
    }
    master_required = {"instrument_id", "symbol", "symbol_effective_from", "symbol_effective_to"}
    if not bar_required.issubset(bars.columns):
        raise ValueError(f"canonical bars require columns: {sorted(bar_required)}")
    if not master_required.issubset(instrument_master.columns):
        raise ValueError(f"instrument master requires columns: {sorted(master_required)}")

    bars["timestamp"] = _strict_utc(bars["timestamp"], "timestamp")
    bars["available_at"] = _strict_utc(bars["available_at"], "available_at")
    numeric = ["open", "high", "low", "close", "volume", "adjustment_factor"]
    bars[numeric] = bars[numeric].apply(pd.to_numeric, errors="coerce")
    if bars[numeric].isna().any().any() or not np.isfinite(bars[numeric].to_numpy()).all():
        raise ValueError("canonical bars contain non-finite numeric values")
    bars = bars.loc[(bars["timestamp"] >= start_at) & (bars["timestamp"] < end_at)].copy()
    if bars.empty:
        raise ValueError("canonical bars contain no rows in the requested interval")
    bars["_bar_row"] = np.arange(len(bars))

    master = instrument_master.copy()
    master["symbol"] = master["symbol"].astype(str).str.strip()
    master["symbol_effective_from"] = _strict_utc(master["symbol_effective_from"], "symbol_effective_from")
    master["symbol_effective_to"] = _strict_utc(
        master["symbol_effective_to"],
        "symbol_effective_to",
        nullable=True,
    ).fillna(pd.Timestamp.max.tz_localize("UTC"))
    candidates = bars.merge(
        master[["instrument_id", "symbol", "symbol_effective_from", "symbol_effective_to"]],
        on="instrument_id",
        how="left",
    )
    eligible = candidates.loc[
        (candidates["timestamp"] >= candidates["symbol_effective_from"])
        & (candidates["timestamp"] < candidates["symbol_effective_to"])
    ].copy()
    matches = eligible.groupby("_bar_row").size()
    unresolved = sorted(set(bars["_bar_row"]) - set(matches.index))
    ambiguous = sorted(matches.index[matches.ne(1)].astype(int))
    if unresolved or ambiguous:
        raise ValueError(
            "canonical bars require exactly one active symbol mapping per row; "
            f"unresolved={unresolved[:10]}, ambiguous={ambiguous[:10]}"
        )

    eligible["datetime"] = (
        eligible["timestamp"].dt.tz_convert(session_timezone).dt.tz_localize(None).dt.normalize()
    )
    decisions = decision_timestamps(eligible["datetime"], session_timezone, decision_time)
    unavailable = eligible["available_at"].reset_index(drop=True) > decisions.reset_index(drop=True)
    if unavailable.any():
        rows = eligible.loc[unavailable.to_numpy(), "_bar_row"].astype(int).tolist()
        raise ValueError(f"canonical bars are unavailable at the configured decision time: {rows[:10]}")
    factor = eligible["adjustment_factor"]
    eligible["Open"] = eligible["open"] * factor
    eligible["High"] = eligible["high"] * factor
    eligible["Low"] = eligible["low"] * factor
    eligible["Close"] = eligible["close"] * factor
    eligible["Volume"] = eligible["volume"] / factor
    eligible["instrument"] = eligible["symbol"]
    result = eligible[
        ["datetime", "instrument_id", "instrument", "Open", "High", "Low", "Close", "Volume"]
    ].copy()
    if result.duplicated(["datetime", "instrument"]).any():
        raise ValueError("canonical input must contain one daily bar per symbol and session")

    available_symbols = set(result["instrument"])
    missing = sorted(set(requested) - available_symbols)
    if missing:
        raise ValueError(f"canonical bars are missing requested symbols: {', '.join(missing)}")
    if benchmark_symbol not in available_symbols:
        raise ValueError(f"canonical bars are missing benchmark symbol: {benchmark_symbol}")
    stocks = result.loc[result["instrument"].isin(requested)].sort_values(["instrument", "datetime"])
    benchmark = result.loc[result["instrument"] == benchmark_symbol].sort_values("datetime")
    stocks.attrs.update(
        {
            "data_mode": "canonical_audited",
            "price_adjustment": "ohlc_times_adjustment_factor_volume_divided_by_factor",
        }
    )
    benchmark.attrs.update(stocks.attrs)
    return stocks.reset_index(drop=True), benchmark.reset_index(drop=True)
