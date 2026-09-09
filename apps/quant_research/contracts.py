"""Versioned contracts shared by data, models, portfolio, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    manifest_hash: str
    as_of: str
    universe_id: str
    tables: dict[str, dict[str, Any]]
    quality: dict[str, Any]
    schema_version: str = "data_snapshot.v1"


@dataclass(frozen=True)
class FeatureBatch:
    feature_schema_id: str
    data_snapshot_id: str
    decision_time: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    schema_version: str = "feature_batch.v1"

    def __post_init__(self) -> None:
        forbidden = {"label", "target", "forward_return"}
        if forbidden.intersection(self.columns):
            raise ValueError("feature batches cannot contain labels or forward returns")


@dataclass(frozen=True)
class LabelBatch:
    data_snapshot_id: str
    rows: tuple[dict[str, Any], ...]
    schema_version: str = "label_batch.v1"


@dataclass(frozen=True)
class SignalBatch:
    signal_id: str
    strategy_id: str
    generator_kind: str
    generator_version: str
    decision_time: str
    valid_until: str
    target_kind: str
    model_id: str | None = None
    horizon: int | None = None
    scores: dict[str, float] = field(default_factory=dict)
    schema_version: str = "signal_batch.v1"

    def __post_init__(self) -> None:
        if self.generator_kind not in {"model", "rule"}:
            raise ValueError("generator_kind must be model or rule")
        if self.generator_kind == "model" and not self.model_id:
            raise ValueError("model signals require model_id")


@dataclass(frozen=True)
class OrderPlan:
    plan_id: str
    plan_basis_hash: str
    account_snapshot_ref: str
    source_ids: tuple[str, ...]
    orders: tuple[dict[str, Any], ...]
    reservations: dict[str, float]
    expires_at: str
    reason_codes: tuple[str, ...] = ()
    schema_version: str = "order_plan.v1"


def as_payload(contract: Any) -> dict[str, Any]:
    return asdict(contract)
