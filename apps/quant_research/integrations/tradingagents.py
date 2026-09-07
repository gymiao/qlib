"""Validated import of versioned TradingAgents research signals.

The adapter uses files instead of importing TradingAgents.  This keeps an LLM
runtime and the deterministic backtest in separate environments and makes a
recorded signal sufficient to reproduce a portfolio replay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


SCHEMA_VERSION = "research_signal.v1"
RATINGS = frozenset({"Buy", "Overweight", "Hold", "Underweight", "Sell"})
COMPLETED_STATUS = "completed"
FORWARD_MODES = frozenset({"forward", "frozen_fixture"})


def _parse_timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ResearchSignal:
    """A time-bounded, externally generated research opinion."""

    signal_id: str
    run_id: str
    symbol: str
    benchmark: str
    analysis_as_of: datetime
    generated_at: datetime
    available_at: datetime
    valid_until: datetime
    rating: str
    status: str
    research_mode: str
    summary: str = ""
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResearchSignal":
        required = {
            "schema_version", "signal_id", "run_id", "symbol", "benchmark",
            "analysis_as_of", "generated_at", "available_at", "valid_until",
            "rating", "status", "research_mode",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"research signal is missing fields: {', '.join(missing)}")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {payload['schema_version']!r}")
        signal = cls(
            signal_id=str(payload["signal_id"]),
            run_id=str(payload["run_id"]),
            symbol=str(payload["symbol"]).upper(),
            benchmark=str(payload["benchmark"]).upper(),
            analysis_as_of=_parse_timestamp(payload["analysis_as_of"], "analysis_as_of"),
            generated_at=_parse_timestamp(payload["generated_at"], "generated_at"),
            available_at=_parse_timestamp(payload["available_at"], "available_at"),
            valid_until=_parse_timestamp(payload["valid_until"], "valid_until"),
            rating=str(payload["rating"]),
            status=str(payload["status"]),
            research_mode=str(payload["research_mode"]),
            summary=str(payload.get("summary", "")),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs", [])),
        )
        signal.validate()
        return signal

    def validate(self) -> None:
        if not self.signal_id or not self.run_id or not self.symbol or not self.benchmark:
            raise ValueError("signal_id, run_id, symbol and benchmark must be non-empty")
        if self.rating not in RATINGS:
            raise ValueError(f"unsupported rating: {self.rating!r}")
        if self.generated_at < self.analysis_as_of:
            raise ValueError("generated_at cannot be earlier than analysis_as_of")
        if self.available_at < self.generated_at:
            raise ValueError("available_at cannot be earlier than generated_at")
        if self.valid_until <= self.available_at:
            raise ValueError("valid_until must be after available_at")


def load_research_signals(path: Path | str) -> list[ResearchSignal]:
    """Load JSONL signals and reject duplicate signal identifiers."""
    records: list[ResearchSignal] = []
    ids: set[str] = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                signal = ResearchSignal.from_dict(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid research signal on line {line_number}: {exc}") from exc
            if signal.signal_id in ids:
                raise ValueError(f"duplicate signal_id on line {line_number}: {signal.signal_id}")
            ids.add(signal.signal_id)
            records.append(signal)
    return records


def effective_signals(
    signals: Iterable[ResearchSignal],
    decision_time: datetime | pd.Timestamp,
    allowed_modes: frozenset[str] = FORWARD_MODES,
) -> dict[str, ResearchSignal]:
    """Select the latest usable signal per symbol without reading future records."""
    timestamp = pd.Timestamp(decision_time).to_pydatetime()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    selected: dict[str, ResearchSignal] = {}
    for signal in signals:
        if signal.status != COMPLETED_STATUS or signal.research_mode not in allowed_modes:
            continue
        if not signal.available_at <= timestamp < signal.valid_until:
            continue
        previous = selected.get(signal.symbol)
        if previous is None or signal.available_at > previous.available_at:
            selected[signal.symbol] = signal
    return selected


@dataclass(frozen=True)
class ResearchOverlayPolicy:
    """A frozen mapping from research rating to long-only exposure multiplier."""

    multipliers: dict[str, float] = field(
        default_factory=lambda: {
            "Buy": 1.0,
            "Overweight": 1.0,
            "Hold": 0.70,
            "Underweight": 0.35,
            "Sell": 0.0,
        }
    )

    def __post_init__(self) -> None:
        if set(self.multipliers) != RATINGS or any(value < 0 or value > 1 for value in self.multipliers.values()):
            raise ValueError("overlay multipliers must cover every rating and be in [0, 1]")


def apply_research_overlay(
    base_weights: pd.Series,
    signals: dict[str, ResearchSignal],
    policy: ResearchOverlayPolicy | None = None,
) -> pd.Series:
    """Reduce long-only weights according to recorded research ratings.

    Reduced weight remains as cash.  The function intentionally does not
    renormalize survivors, which would turn a risk filter into an unintended
    concentration increase.
    """
    policy = policy or ResearchOverlayPolicy()
    clean = base_weights.astype(float).copy()
    if clean.empty or (clean < 0).any() or clean.sum() > 1 + 1e-12:
        raise ValueError("base_weights must be non-negative and sum to at most one")
    for symbol in clean.index:
        signal = signals.get(str(symbol).upper())
        if signal is not None:
            clean.loc[symbol] *= policy.multipliers[signal.rating]
    return clean.rename("target_weight")
