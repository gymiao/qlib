"""Validate broker cash-activity exports and append them to the event ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from typing import Any

from .contracts import canonical_hash
from .ledger import EventLedger, LedgerEvent, money


_HASH = re.compile(r"[0-9a-f]{64}")
_ACTIVITY_FIELDS_V1 = {
    "broker_activity_id",
    "activity_type",
    "amount",
    "currency",
    "reference_id",
    "effective_at",
    "available_at",
}
_ACTIVITY_FIELDS_V2 = (_ACTIVITY_FIELDS_V1 - {"amount"}) | {"amount_decimal"}
_LEDGER_KINDS = {
    "trade_receivable_settlement": "settle_trade_receivable",
    "trade_payable_settlement": "settle_trade_payable",
    "dividend_entitlement": "dividend_entitlement",
    "dividend_payment": "dividend_payment",
    "cash_deposit": "external_cash",
    "cash_withdrawal": "external_cash",
}
_PRIORITY = {
    "cash_deposit": 0,
    "dividend_entitlement": 1,
    "trade_receivable_settlement": 2,
    "trade_payable_settlement": 2,
    "dividend_payment": 2,
    "cash_withdrawal": 3,
}


@dataclass(frozen=True)
class BrokerActivityBatch:
    batch_id: str
    account_id: str
    broker_id: str
    captured_at: str
    available_at: str
    activities: tuple[dict[str, Any], ...]
    source: dict[str, str]
    import_capability: str
    schema_version: str = "broker_activity_batch.v1"


@dataclass(frozen=True)
class BrokerActivityImport:
    import_id: str
    batch_id: str
    account_id: str
    status: str
    import_capability: str
    applied_event_ids: tuple[str, ...]
    duplicate_event_ids: tuple[str, ...]
    account_state_hash: str
    orders_submitted_by_system: int = 0
    schema_version: str = "broker_activity_import.v1"


def _timestamp(value: str | datetime, field: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return result


def _canonical_decimal(value: Any, field: str, *, canonical_input: bool = False) -> str:
    if isinstance(value, bool) or (canonical_input and not isinstance(value, str)):
        raise ValueError(f"{field} must be a canonical decimal string")
    try:
        decimal = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{field} must be decimal") from error
    canonical = format(decimal.normalize(), "f") if decimal.is_finite() else ""
    if not decimal.is_finite() or decimal <= 0 or (canonical_input and value != canonical):
        raise ValueError(f"{field} must be finite, positive, and canonical")
    return canonical


def _identity_activities(schema_version: str, activities: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    if schema_version == "broker_activity_batch.v1":
        return [dict(row) for row in activities]
    if schema_version == "broker_activity_batch.v2":
        return [
            {
                **{key: value for key, value in row.items() if key != "amount"},
                "amount_decimal": row["amount"],
            }
            for row in activities
        ]
    raise ValueError("unsupported broker activity batch schema")


def _batch_identity(
    *,
    account_id: str,
    broker_id: str,
    captured_at: str,
    available_at: str,
    activities: tuple[dict[str, Any], ...],
    source: dict[str, str],
    schema_version: str = "broker_activity_batch.v1",
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "account_id": account_id,
        "broker_id": broker_id,
        "captured_at": captured_at,
        "available_at": available_at,
        "activities": _identity_activities(schema_version, activities),
        "source": source,
    }


def attach_broker_activity_batch_id(normalized_identity: dict[str, Any]) -> dict[str, Any]:
    """Attach a content ID to canonical v1 or v2 adapter output."""
    required = {
        "schema_version",
        "account_id",
        "broker_id",
        "captured_at",
        "available_at",
        "activities",
        "source",
    }
    if not isinstance(normalized_identity, dict) or set(normalized_identity) != required:
        raise ValueError("normalized broker activity identity fields are invalid")
    activities = normalized_identity.get("activities")
    if not isinstance(activities, (list, tuple)) or not all(isinstance(row, dict) for row in activities):
        raise ValueError("normalized broker activities must be a list of objects")
    payload = {
        **normalized_identity,
        "activities": sorted(
            (dict(row) for row in activities),
            key=lambda row: (
                row.get("effective_at", ""),
                _PRIORITY.get(row.get("activity_type"), 99),
                row.get("available_at", ""),
                row.get("broker_activity_id", ""),
            ),
        ),
    }
    payload["batch_id"] = canonical_hash(payload)[:24]
    return payload


def _broker_account_import(ledger: EventLedger, account_id: str, broker_id: str) -> LedgerEvent:
    imports = [event for event in ledger.events if event.kind == "account_snapshot_import"]
    if (
        len(imports) != 1
        or imports[0].payload.get("account_id") != account_id
        or imports[0].payload.get("broker_id") != broker_id
    ):
        raise ValueError("broker activity batch does not belong to the ledger account")
    return imports[0]


def broker_activity_batch_from_payload(
    payload: dict[str, Any],
    ledger: EventLedger,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerActivityBatch:
    """Validate and normalize an immutable broker cash-activity export."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if not math.isfinite(max_age.total_seconds()) or max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    required = {
        "schema_version",
        "batch_id",
        "account_id",
        "broker_id",
        "captured_at",
        "available_at",
        "activities",
        "source",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") not in {"broker_activity_batch.v1", "broker_activity_batch.v2"}
        or not isinstance(payload.get("account_id"), str)
        or not payload["account_id"]
        or not isinstance(payload.get("broker_id"), str)
        or not payload["broker_id"]
    ):
        raise ValueError("broker activity batch fields or schema version are invalid")
    account_import = _broker_account_import(ledger, payload["account_id"], payload["broker_id"])
    snapshot_at = _timestamp(account_import.effective_at, "account snapshot effective_at")
    captured_at = _timestamp(payload["captured_at"], "captured_at")
    available_at = _timestamp(payload["available_at"], "available_at")
    decision_at = as_of.astimezone(timezone.utc)
    if available_at < captured_at or available_at > decision_at or decision_at - available_at > max_age:
        raise ValueError("activity batch timing must be fresh and satisfy captured_at <= available_at <= as_of")
    source = payload["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"source_kind", "export_id", "content_sha256"}
        or source.get("source_kind") not in {"broker_observed", "synthetic"}
        or not isinstance(source.get("export_id"), str)
        or not source["export_id"]
        or not isinstance(source.get("content_sha256"), str)
        or _HASH.fullmatch(source["content_sha256"]) is None
    ):
        raise ValueError("broker activity source evidence is invalid")
    schema_version = payload["schema_version"]
    originals = payload["activities"]
    if not isinstance(originals, list) or not originals:
        raise ValueError("broker activity batch requires a non-empty activities list")
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for original in originals:
        expected_fields = _ACTIVITY_FIELDS_V1 if schema_version == "broker_activity_batch.v1" else _ACTIVITY_FIELDS_V2
        if not isinstance(original, dict) or set(original) != expected_fields:
            raise ValueError("broker activity row does not match the import schema")
        activity_id = original["broker_activity_id"]
        activity_type = original["activity_type"]
        reference_id = original["reference_id"]
        if (
            not isinstance(activity_id, str)
            or not activity_id
            or activity_id in seen_ids
            or activity_type not in _LEDGER_KINDS
            or not isinstance(reference_id, str)
            or not reference_id
            or original["currency"] != "USD"
        ):
            raise ValueError("broker activity identifier, type, reference, or currency is invalid")
        amount: float | str = (
            _positive_number(original["amount"], "activity amount")
            if schema_version == "broker_activity_batch.v1"
            else _canonical_decimal(
                original["amount_decimal"],
                "activity amount",
                canonical_input=True,
            )
        )
        effective_at = _timestamp(original["effective_at"], "activity effective_at")
        row_available_at = _timestamp(original["available_at"], "activity available_at")
        if not (snapshot_at < effective_at <= row_available_at <= captured_at):
            raise ValueError("activity timing must be after the account snapshot and before batch capture")
        seen_ids.add(activity_id)
        normalized.append(
            {
                "broker_activity_id": activity_id,
                "activity_type": activity_type,
                "amount": amount,
                "currency": "USD",
                "reference_id": reference_id,
                "effective_at": _utc_text(effective_at),
                "available_at": _utc_text(row_available_at),
            }
        )
    activities = tuple(
        sorted(
            normalized,
            key=lambda row: (
                row["effective_at"],
                _PRIORITY[row["activity_type"]],
                row["available_at"],
                row["broker_activity_id"],
            ),
        )
    )
    identity = _batch_identity(
        account_id=payload["account_id"],
        broker_id=payload["broker_id"],
        captured_at=_utc_text(captured_at),
        available_at=_utc_text(available_at),
        activities=activities,
        source=dict(source),
        schema_version=schema_version,
    )
    expected_id = canonical_hash(identity)[:24]
    if payload["batch_id"] != expected_id:
        raise ValueError("broker activity batch identity mismatch")
    return BrokerActivityBatch(
        batch_id=expected_id,
        account_id=payload["account_id"],
        broker_id=payload["broker_id"],
        captured_at=identity["captured_at"],
        available_at=identity["available_at"],
        activities=activities,
        source=dict(source),
        import_capability=(
            "broker_activity_contract_validated"
            if source["source_kind"] == "broker_observed"
            and account_import.payload.get("source_kind") == "broker_observed"
            else "simulation_only"
        ),
        schema_version=schema_version,
    )


def load_broker_activity_batch(
    path: Path | str,
    ledger: EventLedger,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerActivityBatch:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker activity batch must be a JSON object")
    return broker_activity_batch_from_payload(payload, ledger, as_of, max_age)


def validate_broker_activity_batch(batch: BrokerActivityBatch, ledger: EventLedger) -> None:
    identity = _batch_identity(
        account_id=batch.account_id,
        broker_id=batch.broker_id,
        captured_at=batch.captured_at,
        available_at=batch.available_at,
        activities=batch.activities,
        source=batch.source,
        schema_version=batch.schema_version,
    )
    try:
        normalized = broker_activity_batch_from_payload(
            {**identity, "batch_id": batch.batch_id},
            ledger,
            _timestamp(batch.available_at, "available_at"),
            timedelta(seconds=1),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("broker activity batch object failed identity validation") from error
    if normalized != batch:
        raise ValueError("broker activity batch object failed identity validation")


def _event_id(broker_id: str, activity_id: str) -> str:
    return f"broker-activity:{broker_id}:{activity_id}"


def import_broker_activity_batch(
    batch: BrokerActivityBatch,
    ledger: EventLedger,
) -> BrokerActivityImport:
    """Atomically apply broker-observed cash activities; never submit orders."""
    validate_broker_activity_batch(batch, ledger)
    trial = EventLedger.replay(ledger.initial_cash, ledger.events)
    applied: list[str] = []
    duplicates: list[str] = []
    for row in batch.activities:
        activity_type = row["activity_type"]
        amount = money(row["amount"])
        ledger_amount = -amount if activity_type == "cash_withdrawal" else amount
        event_id = _event_id(batch.broker_id, row["broker_activity_id"])
        event = LedgerEvent(
            event_id=event_id,
            effective_at=row["effective_at"],
            available_at=row["available_at"],
            kind=_LEDGER_KINDS[activity_type],
            payload={
                "amount": str(ledger_amount),
                "account_id": batch.account_id,
                "broker_id": batch.broker_id,
                "broker_activity_id": row["broker_activity_id"],
                "activity_type": activity_type,
                "currency": row["currency"],
                "reference_id": row["reference_id"],
            },
        )
        if trial.apply(event):
            applied.append(event_id)
        else:
            duplicates.append(event_id)
    for event in trial.events[len(ledger.events) :]:
        ledger.apply(event)
    state_hash = ledger.snapshot()["state_hash"]
    status = "duplicate" if not applied else "applied"
    identity = {
        "schema_version": "broker_activity_import.v1",
        "batch_id": batch.batch_id,
        "account_id": batch.account_id,
        "status": status,
        "import_capability": batch.import_capability,
        "applied_event_ids": applied,
        "duplicate_event_ids": duplicates,
        "account_state_hash": state_hash,
        "orders_submitted_by_system": 0,
    }
    return BrokerActivityImport(
        import_id=canonical_hash(identity)[:24],
        batch_id=batch.batch_id,
        account_id=batch.account_id,
        status=status,
        import_capability=batch.import_capability,
        applied_event_ids=tuple(applied),
        duplicate_event_ids=tuple(duplicates),
        account_state_hash=state_hash,
    )
