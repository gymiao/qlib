"""Import broker-observed reversals for exact, still-unsettled equity fills."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

from .broker_fills import _correction_event_id, summarize_broker_fills
from .broker_order_status import package_events
from .broker_orders import BrokerOrderPackage, validate_broker_order_package
from .contracts import canonical_hash
from .ledger import EventLedger, LedgerEvent


_HASH = re.compile(r"[0-9a-f]{64}")
_ROW_FIELDS = {
    "broker_correction_id",
    "broker_execution_id",
    "reason_code",
    "effective_at",
    "available_at",
}


@dataclass(frozen=True)
class BrokerFillCorrectionBatch:
    batch_id: str
    package_id: str
    account_id: str
    broker_id: str
    captured_at: str
    available_at: str
    corrections: tuple[dict[str, str], ...]
    source: dict[str, str]
    import_capability: str
    schema_version: str = "broker_fill_correction_batch.v1"


@dataclass(frozen=True)
class BrokerFillCorrectionImport:
    import_id: str
    batch_id: str
    package_id: str
    status: str
    import_capability: str
    applied_event_ids: tuple[str, ...]
    duplicate_event_ids: tuple[str, ...]
    order_statuses: tuple[dict[str, Any], ...]
    fill_status: str
    account_state_hash: str
    orders_submitted_by_system: int = 0
    schema_version: str = "broker_fill_correction_import.v1"


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


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "broker_fill_correction_batch.v1",
        "package_id": payload["package_id"],
        "account_id": payload["account_id"],
        "broker_id": payload["broker_id"],
        "captured_at": payload["captured_at"],
        "available_at": payload["available_at"],
        "corrections": list(payload["corrections"]),
        "source": payload["source"],
    }


def attach_broker_fill_correction_batch_id(normalized_identity: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "package_id", "account_id", "broker_id", "captured_at",
        "available_at", "corrections", "source",
    }
    if not isinstance(normalized_identity, dict) or set(normalized_identity) != required:
        raise ValueError("normalized broker fill correction fields are invalid")
    rows = normalized_identity.get("corrections")
    if not isinstance(rows, (list, tuple)) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("normalized broker fill corrections must be a list of objects")
    payload = {
        **normalized_identity,
        "corrections": sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                row.get("available_at", ""),
                row.get("effective_at", ""),
                row.get("broker_correction_id", ""),
            ),
        ),
    }
    payload["batch_id"] = canonical_hash(payload)[:24]
    return payload


def broker_fill_correction_batch_from_payload(
    payload: dict[str, Any],
    package: BrokerOrderPackage,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerFillCorrectionBatch:
    validate_broker_order_package(package)
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if not math.isfinite(max_age.total_seconds()) or max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    required = {
        "schema_version", "batch_id", "package_id", "account_id", "broker_id",
        "captured_at", "available_at", "corrections", "source",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") != "broker_fill_correction_batch.v1"
    ):
        raise ValueError("broker fill correction fields or schema version are invalid")
    if (
        payload["package_id"] != package.package_id
        or payload["account_id"] != package.account_id
        or payload["broker_id"] != package.broker_id
    ):
        raise ValueError("broker fill correction batch does not belong to the package account")
    captured_at = _timestamp(payload["captured_at"], "captured_at")
    available_at = _timestamp(payload["available_at"], "available_at")
    decision_at = as_of.astimezone(timezone.utc)
    if available_at < captured_at or available_at > decision_at or decision_at - available_at > max_age:
        raise ValueError("correction timing must be fresh and satisfy captured_at <= available_at <= as_of")
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
        raise ValueError("broker fill correction source evidence is invalid")
    originals = payload["corrections"]
    if not isinstance(originals, list) or not originals:
        raise ValueError("broker fill correction batch requires non-empty corrections")
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    generated_at = _timestamp(package.generated_at, "package generated_at")
    for original in originals:
        if not isinstance(original, dict) or set(original) != _ROW_FIELDS:
            raise ValueError("broker fill correction row does not match the import schema")
        correction_id = original["broker_correction_id"]
        execution_id = original["broker_execution_id"]
        reason_code = original["reason_code"]
        if (
            not isinstance(correction_id, str) or not correction_id or correction_id in seen
            or not isinstance(execution_id, str) or not execution_id
            or not isinstance(reason_code, str) or not reason_code
        ):
            raise ValueError("broker fill correction identifiers and reason are invalid")
        effective_at = _timestamp(original["effective_at"], "correction effective_at")
        row_available_at = _timestamp(original["available_at"], "correction available_at")
        if not (generated_at <= effective_at <= row_available_at <= captured_at):
            raise ValueError("broker fill correction timing is outside the availability window")
        seen.add(correction_id)
        normalized.append({
            "broker_correction_id": correction_id,
            "broker_execution_id": execution_id,
            "reason_code": reason_code,
            "effective_at": _utc_text(effective_at),
            "available_at": _utc_text(row_available_at),
        })
    rows = tuple(sorted(normalized, key=lambda row: (row["available_at"], row["effective_at"], row["broker_correction_id"])))
    identity = {
        "schema_version": "broker_fill_correction_batch.v1",
        "package_id": package.package_id,
        "account_id": package.account_id,
        "broker_id": package.broker_id,
        "captured_at": _utc_text(captured_at),
        "available_at": _utc_text(available_at),
        "corrections": list(rows),
        "source": dict(source),
    }
    if payload["batch_id"] != canonical_hash(identity)[:24]:
        raise ValueError("broker fill correction batch identity mismatch")
    return BrokerFillCorrectionBatch(
        batch_id=payload["batch_id"], package_id=package.package_id,
        account_id=package.account_id, broker_id=package.broker_id,
        captured_at=identity["captured_at"], available_at=identity["available_at"],
        corrections=rows, source=dict(source),
        import_capability=(
            "broker_fill_correction_contract_validated"
            if source["source_kind"] == "broker_observed" else "simulation_only"
        ),
    )


def load_broker_fill_correction_batch(
    path: Path | str,
    package: BrokerOrderPackage,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerFillCorrectionBatch:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker fill correction batch must be a JSON object")
    return broker_fill_correction_batch_from_payload(payload, package, as_of, max_age)


def validate_broker_fill_correction_batch(
    batch: BrokerFillCorrectionBatch,
    package: BrokerOrderPackage,
) -> None:
    identity = _identity(asdict(batch))
    try:
        normalized = broker_fill_correction_batch_from_payload(
            {**identity, "batch_id": batch.batch_id}, package,
            _timestamp(batch.available_at, "available_at"), timedelta(seconds=1),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("broker fill correction batch object failed identity validation") from error
    if normalized != batch:
        raise ValueError("broker fill correction batch object failed identity validation")


def _fill_by_execution(events: list[LedgerEvent]) -> tuple[dict[str, LedgerEvent], set[str]]:
    fills: dict[str, LedgerEvent] = {}
    reversed_ids: set[str] = set()
    for event in events:
        execution_id = event.payload.get("broker_execution_id")
        if event.kind == "equity_fill":
            fills[str(execution_id)] = event
        elif event.kind == "equity_fill_reversal":
            reversed_ids.add(str(execution_id))
    return fills, reversed_ids


def import_broker_fill_correction_batch(
    batch: BrokerFillCorrectionBatch,
    package: BrokerOrderPackage,
    ledger: EventLedger,
) -> BrokerFillCorrectionImport:
    validate_broker_fill_correction_batch(batch, package)
    lifecycle_events, _, _ = package_events(package, ledger)
    summarize_broker_fills(package, lifecycle_events)
    fills, reversed_ids = _fill_by_execution(lifecycle_events)
    account_imports = [event for event in ledger.events if event.kind == "account_snapshot_import"]
    capability = (
        batch.import_capability
        if len(account_imports) == 1 and account_imports[0].payload.get("source_kind") == "broker_observed"
        else "simulation_only"
    )
    trial = EventLedger.replay(ledger.initial_cash, ledger.events)
    existing_event_ids = {event.event_id for event in trial.events}
    applied: list[str] = []
    duplicates: list[str] = []
    for row in batch.corrections:
        execution_id = row["broker_execution_id"]
        original = fills.get(execution_id)
        if original is None:
            raise ValueError("broker fill correction references an unknown execution")
        event = LedgerEvent(
            event_id=_correction_event_id(batch.broker_id, row["broker_correction_id"]),
            effective_at=row["effective_at"], available_at=row["available_at"],
            kind="equity_fill_reversal",
            payload={
                **original.payload,
                "broker_correction_id": row["broker_correction_id"],
                "reason_code": row["reason_code"],
            },
        )
        if event.event_id in existing_event_ids:
            if trial.apply(event):
                raise AssertionError("duplicate correction unexpectedly applied")
            duplicates.append(event.event_id)
            continue
        if execution_id in reversed_ids:
            raise ValueError("broker execution was already reversed by another correction")
        if _timestamp(row["effective_at"], "correction effective_at") < _timestamp(original.effective_at, "fill effective_at"):
            raise ValueError("broker fill correction predates the original execution")
        original_index = ledger.events.index(original)
        settlement_kind = "settle_trade_payable" if original.payload["side"] == "buy" else "settle_trade_receivable"
        if any(event.kind == settlement_kind for event in ledger.events[original_index + 1 :]):
            raise ValueError("cannot reverse a fill after its cash bucket may have settled")
        trial.apply(event)
        reversed_ids.add(execution_id)
        applied.append(event.event_id)
    new_events = trial.events[len(ledger.events):]
    order_statuses, overall = summarize_broker_fills(package, lifecycle_events + new_events)
    for event in new_events:
        ledger.apply(event)
    state_hash = ledger.snapshot()["state_hash"]
    status = "duplicate" if not applied else "applied"
    identity = {
        "schema_version": "broker_fill_correction_import.v1",
        "batch_id": batch.batch_id, "package_id": package.package_id,
        "status": status, "import_capability": capability,
        "applied_event_ids": applied, "duplicate_event_ids": duplicates,
        "order_statuses": list(order_statuses), "fill_status": overall,
        "account_state_hash": state_hash, "orders_submitted_by_system": 0,
    }
    return BrokerFillCorrectionImport(
        import_id=canonical_hash(identity)[:24], batch_id=batch.batch_id,
        package_id=package.package_id, status=status, import_capability=capability,
        applied_event_ids=tuple(applied), duplicate_event_ids=tuple(duplicates),
        order_statuses=order_statuses, fill_status=overall, account_state_hash=state_hash,
    )
