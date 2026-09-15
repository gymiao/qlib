"""Versioned contracts shared by data, models, portfolio, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
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
        decision_time = datetime.fromisoformat(self.decision_time.replace("Z", "+00:00"))
        if decision_time.tzinfo is None:
            raise ValueError("feature decision_time must include a timezone")
        if not self.feature_schema_id or not self.data_snapshot_id:
            raise ValueError("feature batch identities cannot be empty")
        if len(set(self.columns)) != len(self.columns) or any(not column for column in self.columns):
            raise ValueError("feature columns must be unique non-empty names")


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
        decision_time = datetime.fromisoformat(self.decision_time.replace("Z", "+00:00"))
        valid_until = datetime.fromisoformat(self.valid_until.replace("Z", "+00:00"))
        if decision_time.tzinfo is None or valid_until.tzinfo is None:
            raise ValueError("signal timestamps must include a timezone")
        if valid_until <= decision_time:
            raise ValueError("valid_until must be after decision_time")
        if self.horizon is not None and self.horizon <= 0:
            raise ValueError("signal horizon must be positive")
        if self.generator_kind == "model" and not self.scores:
            raise ValueError("model signal scores cannot be empty")
        if any(not isinstance(key, str) or not key for key in self.scores):
            raise ValueError("signal score identifiers must be non-empty strings")
        if any(not math.isfinite(float(value)) for value in self.scores.values()):
            raise ValueError("signal scores must be finite")


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
