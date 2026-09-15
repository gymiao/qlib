"""Validate read-only broker fill exports and apply them to the event ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from typing import Any

from .broker_order_status import lifecycle_by_order, package_events
from .broker_orders import BrokerOrderPackage, validate_broker_order_package
from .contracts import canonical_hash
from .ledger import EventLedger, LedgerEvent, money


_HASH = re.compile(r"[0-9a-f]{64}")
_FILL_FIELDS_V1 = {
    "broker_execution_id",
    "client_order_id",
    "instrument_id",
    "symbol",
    "side",
    "quantity",
    "price",
    "fee",
    "executed_at",
    "available_at",
}
_FILL_FIELDS_V2 = (_FILL_FIELDS_V1 - {"quantity", "price", "fee"}) | {
    "quantity_decimal",
    "price_decimal",
    "fee_decimal",
}


@dataclass(frozen=True)
class BrokerFillBatch:
    batch_id: str
    package_id: str
    account_id: str
    broker_id: str
    captured_at: str
    available_at: str
    fills: tuple[dict[str, Any], ...]
    source: dict[str, str]
    import_capability: str
    schema_version: str = "broker_fill_batch.v1"


@dataclass(frozen=True)
class BrokerFillImport:
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
    schema_version: str = "broker_fill_import.v1"


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


def _number(value: Any, field: str, *, positive: bool = False, integer: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(result) or (positive and result <= 0) or (integer and result != math.floor(result)):
        raise ValueError(f"{field} is invalid")
    return result


def _decimal_text(
    value: Any,
    field: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
    integer: bool = False,
    canonical_input: bool = False,
) -> str:
    if isinstance(value, bool) or (canonical_input and not isinstance(value, str)):
        raise ValueError(f"{field} must be a canonical decimal string")
    try:
        decimal = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{field} must be decimal") from error
    canonical = format(decimal.normalize(), "f") if decimal.is_finite() else ""
    if (
        not decimal.is_finite()
        or (positive and decimal <= 0)
        or (non_negative and decimal < 0)
        or (integer and decimal != decimal.to_integral_value())
        or (canonical_input and value != canonical)
    ):
        raise ValueError(f"{field} is invalid")
    return canonical


def _identity_fills(schema_version: str, fills: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    if schema_version == "broker_fill_batch.v1":
        return [dict(row) for row in fills]
    if schema_version == "broker_fill_batch.v2":
        return [
            {
                **{key: value for key, value in row.items() if key not in {"quantity", "price", "fee"}},
                "quantity_decimal": row["quantity"],
                "price_decimal": row["price"],
                "fee_decimal": row["fee"],
            }
            for row in fills
        ]
    raise ValueError("unsupported broker fill batch schema")


def _batch_identity(
    *,
    package_id: str,
    account_id: str,
    broker_id: str,
    captured_at: str,
    available_at: str,
    fills: tuple[dict[str, Any], ...],
    source: dict[str, str],
    schema_version: str = "broker_fill_batch.v1",
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "package_id": package_id,
        "account_id": account_id,
        "broker_id": broker_id,
        "captured_at": captured_at,
        "available_at": available_at,
        "fills": _identity_fills(schema_version, fills),
        "source": source,
    }


def attach_broker_fill_batch_id(normalized_identity: dict[str, Any]) -> dict[str, Any]:
    """Attach the content ID after an adapter has emitted canonical v1 or v2 values."""
    required = {
        "schema_version",
        "package_id",
        "account_id",
        "broker_id",
        "captured_at",
        "available_at",
        "fills",
        "source",
    }
    if not isinstance(normalized_identity, dict) or set(normalized_identity) != required:
        raise ValueError("normalized broker fill identity fields are invalid")
    fills = normalized_identity.get("fills")
    if not isinstance(fills, (list, tuple)) or not all(isinstance(row, dict) for row in fills):
        raise ValueError("normalized broker fills must be a list of objects")
    payload = {
        **normalized_identity,
        "fills": sorted(
            (dict(row) for row in fills),
            key=lambda row: (
                row.get("available_at", ""),
                row.get("executed_at", ""),
                row.get("broker_execution_id", ""),
            ),
        ),
    }
    payload["batch_id"] = canonical_hash(payload)[:24]
    return payload


def broker_fill_batch_from_payload(
    payload: dict[str, Any],
    package: BrokerOrderPackage,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerFillBatch:
    """Validate and normalize one immutable broker fill export."""
    validate_broker_order_package(package)
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if not math.isfinite(max_age.total_seconds()) or max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    required = {
        "schema_version",
        "batch_id",
        "package_id",
        "account_id",
        "broker_id",
        "captured_at",
        "available_at",
        "fills",
        "source",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") not in {"broker_fill_batch.v1", "broker_fill_batch.v2"}
    ):
        raise ValueError("broker fill batch fields or schema version are invalid")
    if (
        payload["package_id"] != package.package_id
        or payload["account_id"] != package.account_id
        or payload["broker_id"] != package.broker_id
    ):
        raise ValueError("broker fill batch does not belong to the order package account")
    captured_at = _timestamp(payload["captured_at"], "captured_at")
    available_at = _timestamp(payload["available_at"], "available_at")
    decision_at = as_of.astimezone(timezone.utc)
    if available_at < captured_at or available_at > decision_at or decision_at - available_at > max_age:
        raise ValueError("fill batch timing must be fresh and satisfy captured_at <= available_at <= as_of")
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
        raise ValueError("broker fill source evidence is invalid")
    schema_version = payload["schema_version"]
    originals = payload["fills"]
    if not isinstance(originals, list) or not originals:
        raise ValueError("broker fill batch requires a non-empty fills list")
    package_orders = {row["client_order_id"]: row for row in package.orders}
    generated_at = _timestamp(package.generated_at, "package generated_at")
    expires_at = _timestamp(package.expires_at, "package expires_at")
    seen_executions: set[str] = set()
    totals: dict[str, Decimal] = {}
    normalized: list[dict[str, Any]] = []
    for original in originals:
        expected_fields = _FILL_FIELDS_V1 if schema_version == "broker_fill_batch.v1" else _FILL_FIELDS_V2
        if not isinstance(original, dict) or set(original) != expected_fields:
            raise ValueError("broker fill row does not match the import schema")
        execution_id = original["broker_execution_id"]
        client_id = original["client_order_id"]
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or execution_id in seen_executions
            or not isinstance(client_id, str)
            or client_id not in package_orders
        ):
            raise ValueError("broker execution or client order identifier is invalid")
        order = package_orders[client_id]
        if (
            original["instrument_id"] != order["instrument_id"]
            or original["symbol"] != order["symbol"]
            or original["side"] != order["side"]
        ):
            raise ValueError("broker fill instrument, symbol, or side differs from the package")
        if schema_version == "broker_fill_batch.v1":
            quantity: float | str = _number(original["quantity"], "fill quantity", positive=True, integer=True)
            price: float | str = _number(original["price"], "fill price", positive=True)
            fee: float | str = _number(original["fee"], "fill fee")
            if fee < 0:
                raise ValueError("fill fee cannot be negative")
        else:
            quantity = _decimal_text(original["quantity_decimal"], "fill quantity", positive=True, integer=True, canonical_input=True)
            price = _decimal_text(original["price_decimal"], "fill price", positive=True, canonical_input=True)
            fee = _decimal_text(original["fee_decimal"], "fill fee", non_negative=True, canonical_input=True)
        quantity_value = money(quantity)
        price_value = money(price)
        limit = money(order["limit_price"])
        if (order["side"] == "buy" and price_value > limit) or (order["side"] == "sell" and price_value < limit):
            raise ValueError("broker fill price violates the package limit")
        executed_at = _timestamp(original["executed_at"], "fill executed_at")
        row_available_at = _timestamp(original["available_at"], "fill available_at")
        if not (generated_at <= executed_at <= expires_at and executed_at <= row_available_at <= captured_at):
            raise ValueError("fill timing is outside the package or availability window")
        total = totals.get(client_id, Decimal("0")) + quantity_value
        if total > money(order["quantity"]):
            raise ValueError("broker fill batch exceeds the package order quantity")
        totals[client_id] = total
        seen_executions.add(execution_id)
        normalized.append(
            {
                "broker_execution_id": execution_id,
                "client_order_id": client_id,
                "instrument_id": order["instrument_id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity": quantity,
                "price": price,
                "fee": fee,
                "executed_at": _utc_text(executed_at),
                "available_at": _utc_text(row_available_at),
            }
        )
    fills = tuple(
        sorted(
            normalized,
            key=lambda row: (row["available_at"], row["executed_at"], row["broker_execution_id"]),
        )
    )
    identity = _batch_identity(
        package_id=package.package_id,
        account_id=package.account_id,
        broker_id=package.broker_id,
        captured_at=_utc_text(captured_at),
        available_at=_utc_text(available_at),
        fills=fills,
        source=dict(source),
        schema_version=schema_version,
    )
    expected_id = canonical_hash(identity)[:24]
    if payload["batch_id"] != expected_id:
        raise ValueError("broker fill batch identity mismatch")
    return BrokerFillBatch(
        batch_id=expected_id,
        package_id=package.package_id,
        account_id=package.account_id,
        broker_id=package.broker_id,
        captured_at=identity["captured_at"],
        available_at=identity["available_at"],
        fills=fills,
        source=dict(source),
        import_capability=(
            "broker_fill_contract_validated" if source["source_kind"] == "broker_observed" else "simulation_only"
        ),
        schema_version=schema_version,
    )


def load_broker_fill_batch(
    path: Path | str,
    package: BrokerOrderPackage,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerFillBatch:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker fill batch must be a JSON object")
    return broker_fill_batch_from_payload(payload, package, as_of, max_age)


def validate_broker_fill_batch(batch: BrokerFillBatch, package: BrokerOrderPackage) -> None:
    identity = _batch_identity(
        package_id=batch.package_id,
        account_id=batch.account_id,
        broker_id=batch.broker_id,
        captured_at=batch.captured_at,
        available_at=batch.available_at,
        fills=batch.fills,
        source=batch.source,
        schema_version=batch.schema_version,
    )
    try:
        normalized = broker_fill_batch_from_payload(
            {**identity, "batch_id": batch.batch_id},
            package,
            _timestamp(batch.available_at, "available_at"),
            timedelta(seconds=1),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("broker fill batch object failed identity validation") from error
    if normalized != batch:
        raise ValueError("broker fill batch object failed identity validation")


def _event_id(broker_id: str, broker_execution_id: str) -> str:
    return f"broker-fill:{broker_id}:{broker_execution_id}"


def _correction_event_id(broker_id: str, broker_correction_id: str) -> str:
    return f"broker-fill-correction:{broker_id}:{broker_correction_id}"


def _package_fill_events(
    package: BrokerOrderPackage,
    ledger: EventLedger,
) -> tuple[list[LedgerEvent], list[LedgerEvent], list[LedgerEvent]]:
    return package_events(package, ledger)


def _quantities_and_statuses(
    package: BrokerOrderPackage,
    events: list[LedgerEvent],
) -> tuple[tuple[dict[str, Any], ...], str]:
    orders = {row["client_order_id"]: row for row in package.orders}
    filled = {client_id: Decimal("0") for client_id in orders}
    generated_at = _timestamp(package.generated_at, "package generated_at")
    expires_at = _timestamp(package.expires_at, "package expires_at")
    fill_payload_fields = {
        "instrument",
        "symbol",
        "side",
        "quantity",
        "price",
        "fee",
        "order_package_id",
        "client_order_id",
        "broker_execution_id",
    }
    reversal_payload_fields = fill_payload_fields | {
        "broker_correction_id",
        "reason_code",
    }
    originals: dict[str, LedgerEvent] = {}
    reversed_executions: set[str] = set()
    for event in events:
        is_reversal = event.kind == "equity_fill_reversal"
        expected_fields = reversal_payload_fields if is_reversal else fill_payload_fields
        if event.kind not in {"equity_fill", "equity_fill_reversal"} or set(event.payload) != expected_fields:
            raise ValueError("ledger package fill lifecycle payload is invalid")
        client_id = event.payload.get("client_order_id")
        if client_id not in orders:
            raise ValueError("ledger contains a fill for an unknown package order")
        order = orders[client_id]
        execution_id = event.payload.get("broker_execution_id")
        if (
            event.payload.get("instrument") != order["instrument_id"]
            or event.payload.get("symbol") != order["symbol"]
            or event.payload.get("side") != order["side"]
            or not isinstance(execution_id, str)
            or not execution_id
            or event.payload.get("order_package_id") != package.package_id
        ):
            raise ValueError("ledger package fill metadata differs from the exported order")
        if is_reversal:
            correction_id = event.payload.get("broker_correction_id")
            if (
                not isinstance(correction_id, str)
                or not correction_id
                or not isinstance(event.payload.get("reason_code"), str)
                or not event.payload["reason_code"]
                or event.event_id != _correction_event_id(package.broker_id, correction_id)
            ):
                raise ValueError("ledger package fill reversal metadata is invalid")
        elif event.event_id != _event_id(package.broker_id, execution_id):
            raise ValueError("ledger package fill event ID is invalid")
        signed_quantity = money(event.payload["quantity"])
        quantity = abs(signed_quantity)
        price = money(event.payload["price"])
        fee = money(event.payload["fee"])
        if (
            not quantity.is_finite()
            or quantity <= 0
            or quantity != quantity.to_integral_value()
            or not price.is_finite()
            or price <= 0
            or not fee.is_finite()
            or fee < 0
        ):
            raise ValueError("ledger package fill quantity is invalid")
        if (order["side"] == "buy" and signed_quantity <= 0) or (
            order["side"] == "sell" and signed_quantity >= 0
        ):
            raise ValueError("ledger package fill direction differs from the exported order")
        limit = money(order["limit_price"])
        if (order["side"] == "buy" and price > limit) or (order["side"] == "sell" and price < limit):
            raise ValueError("ledger package fill price violates the exported limit")
        executed_at = _timestamp(event.effective_at, "ledger fill effective_at")
        if event.available_at is None:
            raise ValueError("ledger package fill requires available_at")
        available_at = _timestamp(event.available_at, "ledger fill available_at")
        if is_reversal:
            if executed_at < generated_at or executed_at > available_at:
                raise ValueError("ledger package fill reversal timing is invalid")
        elif not (generated_at <= executed_at <= expires_at and executed_at <= available_at):
            raise ValueError("ledger package fill timing is invalid")
        if is_reversal:
            original = originals.get(execution_id)
            if original is None or execution_id in reversed_executions:
                raise ValueError("broker fill reversal requires one unreversed execution")
            original_economics = {
                key: original.payload[key]
                for key in fill_payload_fields
            }
            reversal_economics = {
                key: event.payload[key]
                for key in fill_payload_fields
            }
            if original_economics != reversal_economics:
                raise ValueError("broker fill reversal differs from the original execution")
            if executed_at < _timestamp(original.effective_at, "original fill effective_at"):
                raise ValueError("broker fill reversal predates the original execution")
            reversed_executions.add(execution_id)
            filled[client_id] -= quantity
        else:
            if execution_id in originals:
                raise ValueError("duplicate broker execution in package lifecycle")
            originals[execution_id] = event
            filled[client_id] += quantity
        if filled[client_id] < 0:
            raise ValueError("broker fill reversal exceeds prior fills")
        if filled[client_id] > money(order["quantity"]):
            raise ValueError("broker fills exceed the package order quantity")
    statuses = []
    for order in package.orders:
        client_id = order["client_order_id"]
        quantity = money(order["quantity"])
        completed = filled[client_id]
        status = "unfilled" if completed == 0 else "filled" if completed == quantity else "partially_filled"
        statuses.append(
            {
                "client_order_id": client_id,
                "ordered_quantity": float(quantity),
                "filled_quantity": float(completed),
                "remaining_quantity": float(quantity - completed),
                "status": status,
            }
        )
    values = {row["status"] for row in statuses}
    overall = "filled" if values == {"filled"} else "unfilled" if values == {"unfilled"} else "partially_filled"
    return tuple(statuses), overall


def summarize_broker_fills(
    package: BrokerOrderPackage,
    events: list[LedgerEvent],
) -> tuple[tuple[dict[str, Any], ...], str]:
    """Validate package fill events and return exact cumulative quantities."""
    return _quantities_and_statuses(package, events)


def import_broker_fill_batch(
    batch: BrokerFillBatch,
    package: BrokerOrderPackage,
    ledger: EventLedger,
) -> BrokerFillImport:
    """Atomically append observed fills; this function never sends broker orders."""
    validate_broker_order_package(package)
    validate_broker_fill_batch(batch, package)
    if (
        batch.schema_version not in {"broker_fill_batch.v1", "broker_fill_batch.v2"}
        or batch.package_id != package.package_id
        or batch.account_id != package.account_id
        or batch.broker_id != package.broker_id
        or batch.import_capability not in {"broker_fill_contract_validated", "simulation_only"}
    ):
        raise ValueError("broker fill batch object does not match the order package")
    existing, status_events, later_account_changes = _package_fill_events(package, ledger)
    _quantities_and_statuses(package, existing)
    lifecycle = lifecycle_by_order(package, status_events)
    trial = EventLedger.replay(ledger.initial_cash, ledger.events)
    applied: list[str] = []
    duplicates: list[str] = []
    for row in batch.fills:
        order_lifecycle = lifecycle[row["client_order_id"]]
        executed_at = _timestamp(row["executed_at"], "fill executed_at")
        accepted_at = order_lifecycle["accepted_at"]
        terminal_at = order_lifecycle["terminal_at"]
        if accepted_at is None or executed_at < _timestamp(accepted_at, "accepted_at"):
            raise ValueError("broker fill requires an accepted order status")
        if terminal_at is not None and executed_at >= _timestamp(terminal_at, "terminal_at"):
            raise ValueError("broker fill occurred after the order became terminal")
        signed_quantity = money(row["quantity"]) * (1 if row["side"] == "buy" else -1)
        event_id = _event_id(batch.broker_id, row["broker_execution_id"])
        event = LedgerEvent(
            event_id=event_id,
            effective_at=row["executed_at"],
            available_at=row["available_at"],
            kind="equity_fill",
            payload={
                "instrument": row["instrument_id"],
                "symbol": row["symbol"],
                "side": row["side"],
                "quantity": str(signed_quantity),
                "price": str(money(row["price"])),
                "fee": str(money(row["fee"])),
                "order_package_id": package.package_id,
                "client_order_id": row["client_order_id"],
                "broker_execution_id": row["broker_execution_id"],
            },
        )
        if trial.apply(event):
            applied.append(event_id)
        else:
            duplicates.append(event_id)
    new_events = trial.events[len(ledger.events) :]
    if new_events and later_account_changes:
        raise ValueError("account changed outside the broker order package after export")
    all_events = existing + new_events
    statuses, status = _quantities_and_statuses(package, all_events)
    for event in new_events:
        ledger.apply(event)
    account_state_hash = ledger.snapshot()["state_hash"]
    identity = {
        "schema_version": "broker_fill_import.v1",
        "batch_id": batch.batch_id,
        "package_id": package.package_id,
        "status": status,
        "import_capability": batch.import_capability,
        "applied_event_ids": applied,
        "duplicate_event_ids": duplicates,
        "order_statuses": list(statuses),
        "account_state_hash": account_state_hash,
        "orders_submitted_by_system": 0,
    }
    return BrokerFillImport(
        import_id=canonical_hash(identity)[:24],
        batch_id=batch.batch_id,
        package_id=package.package_id,
        status=status,
        import_capability=batch.import_capability,
        applied_event_ids=tuple(applied),
        duplicate_event_ids=tuple(duplicates),
        order_statuses=statuses,
        account_state_hash=account_state_hash,
    )
