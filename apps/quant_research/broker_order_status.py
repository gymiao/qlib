"""Validate read-only broker order-status exports and append lifecycle evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from .broker_orders import BrokerOrderPackage, validate_broker_order_package
from .contracts import canonical_hash
from .ledger import EventLedger, LedgerEvent


_HASH = re.compile(r"[0-9a-f]{64}")
_STATUSES = {"accepted", "rejected", "cancelled", "expired"}
_TERMINAL = {"rejected", "cancelled", "expired"}
_ROW_FIELDS = {
    "broker_status_id",
    "client_order_id",
    "broker_order_id",
    "status",
    "reason_code",
    "effective_at",
    "available_at",
}


@dataclass(frozen=True)
class BrokerOrderStatusBatch:
    batch_id: str
    package_id: str
    account_id: str
    broker_id: str
    captured_at: str
    available_at: str
    statuses: tuple[dict[str, Any], ...]
    source: dict[str, str]
    import_capability: str
    schema_version: str = "broker_order_status_batch.v1"


@dataclass(frozen=True)
class BrokerOrderStatusImport:
    import_id: str
    batch_id: str
    package_id: str
    status: str
    import_capability: str
    applied_event_ids: tuple[str, ...]
    duplicate_event_ids: tuple[str, ...]
    order_statuses: tuple[dict[str, Any], ...]
    account_state_hash: str
    orders_submitted_by_system: int = 0
    schema_version: str = "broker_order_status_import.v1"


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
        "schema_version": "broker_order_status_batch.v1",
        "package_id": payload["package_id"],
        "account_id": payload["account_id"],
        "broker_id": payload["broker_id"],
        "captured_at": payload["captured_at"],
        "available_at": payload["available_at"],
        "statuses": list(payload["statuses"]),
        "source": payload["source"],
    }


def attach_broker_order_status_batch_id(normalized_identity: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "package_id",
        "account_id",
        "broker_id",
        "captured_at",
        "available_at",
        "statuses",
        "source",
    }
    if not isinstance(normalized_identity, dict) or set(normalized_identity) != required:
        raise ValueError("normalized broker order status fields are invalid")
    rows = normalized_identity.get("statuses")
    if not isinstance(rows, (list, tuple)) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("normalized broker order statuses must be a list of objects")
    payload = {
        **normalized_identity,
        "statuses": sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                row.get("available_at", ""),
                row.get("effective_at", ""),
                row.get("broker_status_id", ""),
            ),
        ),
    }
    payload["batch_id"] = canonical_hash(payload)[:24]
    return payload


def broker_order_status_batch_from_payload(
    payload: dict[str, Any],
    package: BrokerOrderPackage,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerOrderStatusBatch:
    validate_broker_order_package(package)
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    required = {
        "schema_version",
        "batch_id",
        "package_id",
        "account_id",
        "broker_id",
        "captured_at",
        "available_at",
        "statuses",
        "source",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") != "broker_order_status_batch.v1"
    ):
        raise ValueError("broker order status batch fields or schema version are invalid")
    if (
        payload["package_id"] != package.package_id
        or payload["account_id"] != package.account_id
        or payload["broker_id"] != package.broker_id
    ):
        raise ValueError("broker order status batch does not belong to the package account")
    captured_at = _timestamp(payload["captured_at"], "captured_at")
    available_at = _timestamp(payload["available_at"], "available_at")
    decision_at = as_of.astimezone(timezone.utc)
    if available_at < captured_at or available_at > decision_at or decision_at - available_at > max_age:
        raise ValueError("status batch timing must be fresh and satisfy captured_at <= available_at <= as_of")
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
        raise ValueError("broker order status source evidence is invalid")
    originals = payload["statuses"]
    if not isinstance(originals, list) or not originals:
        raise ValueError("broker order status batch requires a non-empty statuses list")
    package_orders = {row["client_order_id"]: row for row in package.orders}
    generated_at = _timestamp(package.generated_at, "package generated_at")
    seen_statuses: set[str] = set()
    normalized = []
    for original in originals:
        if not isinstance(original, dict) or set(original) != _ROW_FIELDS:
            raise ValueError("broker order status row does not match the import schema")
        status_id = original["broker_status_id"]
        client_id = original["client_order_id"]
        broker_order_id = original["broker_order_id"]
        status = original["status"]
        reason = original["reason_code"]
        if (
            not isinstance(status_id, str)
            or not status_id
            or status_id in seen_statuses
            or not isinstance(client_id, str)
            or client_id not in package_orders
            or not isinstance(broker_order_id, str)
            or not broker_order_id
            or status not in _STATUSES
            or (reason is not None and (not isinstance(reason, str) or not reason))
            or (status in {"rejected", "cancelled"} and reason is None)
        ):
            raise ValueError("broker order status identity, transition, or reason is invalid")
        effective_at = _timestamp(original["effective_at"], "status effective_at")
        row_available_at = _timestamp(original["available_at"], "status available_at")
        if not (generated_at <= effective_at <= row_available_at <= captured_at):
            raise ValueError("broker order status timing is outside the package or availability window")
        seen_statuses.add(status_id)
        normalized.append(
            {
                "broker_status_id": status_id,
                "client_order_id": client_id,
                "broker_order_id": broker_order_id,
                "status": status,
                "reason_code": reason,
                "effective_at": _utc_text(effective_at),
                "available_at": _utc_text(row_available_at),
            }
        )
    rows = tuple(sorted(normalized, key=lambda row: (row["available_at"], row["effective_at"], row["broker_status_id"])))
    identity = {
        "schema_version": "broker_order_status_batch.v1",
        "package_id": package.package_id,
        "account_id": package.account_id,
        "broker_id": package.broker_id,
        "captured_at": _utc_text(captured_at),
        "available_at": _utc_text(available_at),
        "statuses": list(rows),
        "source": dict(source),
    }
    if payload["batch_id"] != canonical_hash(identity)[:24]:
        raise ValueError("broker order status batch identity mismatch")
    return BrokerOrderStatusBatch(
        batch_id=payload["batch_id"],
        package_id=package.package_id,
        account_id=package.account_id,
        broker_id=package.broker_id,
        captured_at=identity["captured_at"],
        available_at=identity["available_at"],
        statuses=rows,
        source=dict(source),
        import_capability=(
            "broker_order_status_contract_validated"
            if source["source_kind"] == "broker_observed"
            else "simulation_only"
        ),
    )


def load_broker_order_status_batch(
    path: Path | str,
    package: BrokerOrderPackage,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerOrderStatusBatch:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker order status batch must be a JSON object")
    return broker_order_status_batch_from_payload(payload, package, as_of, max_age)


def validate_broker_order_status_batch(
    batch: BrokerOrderStatusBatch,
    package: BrokerOrderPackage,
) -> None:
    identity = _identity(asdict(batch))
    try:
        normalized = broker_order_status_batch_from_payload(
            {**identity, "batch_id": batch.batch_id},
            package,
            _timestamp(batch.available_at, "available_at"),
            timedelta(seconds=1),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("broker order status batch object failed identity validation") from error
    if normalized != batch:
        raise ValueError("broker order status batch object failed identity validation")


def _event_id(broker_id: str, broker_status_id: str) -> str:
    return f"broker-order-status:{broker_id}:{broker_status_id}"


def package_events(
    package: BrokerOrderPackage,
    ledger: EventLedger,
) -> tuple[list[LedgerEvent], list[LedgerEvent], list[LedgerEvent]]:
    if len(ledger.events) < package.account_sequence:
        raise ValueError("ledger is older than the broker order package account sequence")
    prefix = EventLedger.replay(ledger.initial_cash, ledger.events[: package.account_sequence])
    if prefix.snapshot()["state_hash"] != package.account_state_hash:
        raise ValueError("broker order package account prefix does not match the ledger")
    fills: list[LedgerEvent] = []
    statuses: list[LedgerEvent] = []
    others: list[LedgerEvent] = []
    for event in ledger.events[package.account_sequence :]:
        if event.kind in {"equity_fill", "equity_fill_reversal"} and event.payload.get("order_package_id") == package.package_id:
            fills.append(event)
        elif event.kind == "broker_order_status" and event.payload.get("order_package_id") == package.package_id:
            statuses.append(event)
        else:
            others.append(event)
    return fills, statuses, others


def lifecycle_by_order(
    package: BrokerOrderPackage,
    status_events: list[LedgerEvent],
) -> dict[str, dict[str, Any]]:
    orders = {row["client_order_id"]: row for row in package.orders}
    histories: dict[str, list[LedgerEvent]] = {client_id: [] for client_id in orders}
    generated_at = _timestamp(package.generated_at, "package generated_at")
    expected_fields = {
        "order_package_id",
        "client_order_id",
        "broker_order_id",
        "broker_status_id",
        "status",
        "reason_code",
    }
    for event in status_events:
        if set(event.payload) != expected_fields or event.available_at is None:
            raise ValueError("ledger broker order status payload is invalid")
        client_id = event.payload["client_order_id"]
        status_id = event.payload["broker_status_id"]
        effective_at = _timestamp(event.effective_at, "status effective_at")
        available_at = _timestamp(event.available_at, "status available_at")
        reason = event.payload["reason_code"]
        if (
            client_id not in orders
            or event.payload["order_package_id"] != package.package_id
            or not isinstance(status_id, str)
            or not status_id
            or not isinstance(event.payload["broker_order_id"], str)
            or not event.payload["broker_order_id"]
            or event.event_id != _event_id(package.broker_id, status_id)
            or event.payload["status"] not in _STATUSES
            or (reason is not None and (not isinstance(reason, str) or not reason))
            or (event.payload["status"] in {"rejected", "cancelled"} and reason is None)
            or not (generated_at <= effective_at <= available_at)
        ):
            raise ValueError("ledger broker order status metadata is invalid")
        histories[client_id].append(event)
    current: dict[str, dict[str, Any]] = {}
    for client_id, events in histories.items():
        ordered = sorted(events, key=lambda event: (_timestamp(event.effective_at, "effective_at"), _timestamp(event.available_at, "available_at"), event.event_id))
        broker_order_id = None
        status = "pending_human_approval"
        terminal = False
        accepted_at = None
        terminal_at = None
        for event in ordered:
            row = event.payload
            if broker_order_id is not None and row["broker_order_id"] != broker_order_id:
                raise ValueError("broker_order_id changed during the order lifecycle")
            broker_order_id = row["broker_order_id"]
            next_status = row["status"]
            if terminal or (status == "accepted" and next_status == "accepted") or (
                status == "pending_human_approval" and next_status in {"cancelled", "expired"}
            ):
                raise ValueError("invalid broker order status transition")
            if next_status == "accepted":
                accepted_at = event.effective_at
            if next_status in _TERMINAL:
                terminal = True
                terminal_at = event.effective_at
            status = next_status
        current[client_id] = {
            "client_order_id": client_id,
            "broker_order_id": broker_order_id,
            "status": status,
            "accepted_at": accepted_at,
            "terminal_at": terminal_at,
        }
    return current


def import_broker_order_status_batch(
    batch: BrokerOrderStatusBatch,
    package: BrokerOrderPackage,
    ledger: EventLedger,
) -> BrokerOrderStatusImport:
    validate_broker_order_status_batch(batch, package)
    account_imports = [event for event in ledger.events if event.kind == "account_snapshot_import"]
    capability = (
        batch.import_capability
        if len(account_imports) == 1
        and account_imports[0].payload.get("source_kind") == "broker_observed"
        else "simulation_only"
    )
    fills, existing, _ = package_events(package, ledger)
    lifecycle_by_order(package, existing)
    trial = EventLedger.replay(ledger.initial_cash, ledger.events)
    applied: list[str] = []
    duplicates: list[str] = []
    for row in batch.statuses:
        event_id = _event_id(batch.broker_id, row["broker_status_id"])
        event = LedgerEvent(
            event_id=event_id,
            effective_at=row["effective_at"],
            available_at=row["available_at"],
            kind="broker_order_status",
            payload={
                "order_package_id": package.package_id,
                "client_order_id": row["client_order_id"],
                "broker_order_id": row["broker_order_id"],
                "broker_status_id": row["broker_status_id"],
                "status": row["status"],
                "reason_code": row["reason_code"],
            },
        )
        if trial.apply(event):
            applied.append(event_id)
        else:
            duplicates.append(event_id)
    new_events = trial.events[len(ledger.events) :]
    lifecycle = lifecycle_by_order(package, existing + new_events)
    for fill in fills:
        client_id = fill.payload.get("client_order_id")
        if client_id not in lifecycle:
            raise ValueError("observed fill belongs to an unknown order")
        order_lifecycle = lifecycle[client_id]
        executed_at = _timestamp(fill.effective_at, "fill effective_at")
        accepted_at = order_lifecycle["accepted_at"]
        terminal_at = order_lifecycle["terminal_at"]
        if accepted_at is None or executed_at < _timestamp(accepted_at, "accepted_at"):
            raise ValueError("observed fill requires prior order acceptance")
        if terminal_at is not None and executed_at >= _timestamp(terminal_at, "terminal_at"):
            raise ValueError("observed fill cannot occur after a terminal order status")
    for event in new_events:
        ledger.apply(event)
    statuses = tuple(lifecycle[row["client_order_id"]] for row in package.orders)
    values = {row["status"] for row in statuses}
    overall = next(iter(values)) if len(values) == 1 else "mixed"
    identity = {
        "schema_version": "broker_order_status_import.v1",
        "batch_id": batch.batch_id,
        "package_id": package.package_id,
        "status": overall,
        "import_capability": capability,
        "applied_event_ids": applied,
        "duplicate_event_ids": duplicates,
        "order_statuses": list(statuses),
        "account_state_hash": ledger.snapshot()["state_hash"],
        "orders_submitted_by_system": 0,
    }
    return BrokerOrderStatusImport(
        import_id=canonical_hash(identity)[:24],
        batch_id=batch.batch_id,
        package_id=package.package_id,
        status=overall,
        import_capability=capability,
        applied_event_ids=tuple(applied),
        duplicate_event_ids=tuple(duplicates),
        order_statuses=statuses,
        account_state_hash=identity["account_state_hash"],
    )
