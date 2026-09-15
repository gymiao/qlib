"""Create content-addressed, manual-review-only broker order packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException
import re
from typing import Any

from .broker_account import (
    BrokerAccountSnapshot,
    reconcile_broker_snapshot,
    validate_broker_account_snapshot,
)
from .contracts import OrderPlan, canonical_hash
from .ledger import EventLedger, money


_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,31}")


@dataclass(frozen=True)
class BrokerOrderPackage:
    package_id: str
    plan_id: str
    account_id: str
    broker_id: str
    account_snapshot_id: str
    account_state_hash: str
    account_sequence: int
    generated_at: str
    expires_at: str
    status: str
    orders: tuple[dict[str, Any], ...]
    source_ids: tuple[str, ...]
    execution_capability: str = "manual_export_only"
    orders_submitted: int = 0
    schema_version: str = "broker_order_package.v1"


def _timestamp(value: str | datetime, field: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _package_identity(package: BrokerOrderPackage) -> dict[str, Any]:
    payload = broker_order_package_payload(package)
    return {key: value for key, value in payload.items() if key != "package_id"}


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _external_orders(package: BrokerOrderPackage) -> list[dict[str, Any]]:
    if package.schema_version == "broker_order_package.v1":
        return [dict(row) for row in package.orders]
    if package.schema_version == "broker_order_package.v2":
        return [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"quantity", "limit_price", "estimated_fee"}
                },
                "quantity_decimal": row["quantity"],
                "limit_price_decimal": row["limit_price"],
                "estimated_fee_decimal": row["estimated_fee"],
            }
            for row in package.orders
        ]
    raise ValueError("unsupported broker order package schema")


def broker_order_package_payload(package: BrokerOrderPackage) -> dict[str, Any]:
    payload = asdict(package)
    payload["orders"] = _external_orders(package)
    payload["source_ids"] = list(package.source_ids)
    return payload


def _client_order_id(
    plan_id: str,
    index: int,
    instrument_id: str,
    signed_quantity: Decimal,
    limit: Decimal,
) -> str:
    return canonical_hash(
        {
            "plan_id": plan_id,
            "index": index,
            "instrument_id": instrument_id,
            "quantity": _decimal_text(signed_quantity),
            "limit": _decimal_text(limit),
        }
    )[:24]


def validate_broker_order_package(package: BrokerOrderPackage) -> None:
    if (
        package.schema_version not in {"broker_order_package.v1", "broker_order_package.v2"}
        or package.status != "pending_human_approval"
        or package.execution_capability != "manual_export_only"
        or package.orders_submitted != 0
        or not package.orders
        or not package.plan_id
        or not package.account_id
        or not package.broker_id
        or not package.source_ids
        or len(set(package.source_ids)) != len(package.source_ids)
        or isinstance(package.account_sequence, bool)
        or not isinstance(package.account_sequence, int)
        or package.account_sequence < 1
        or package.package_id != canonical_hash(_package_identity(package))[:24]
    ):
        raise ValueError("broker order package identity or capability is invalid")
    generated_at = _timestamp(package.generated_at, "generated_at")
    expires_at = _timestamp(package.expires_at, "expires_at")
    if generated_at >= expires_at:
        raise ValueError("broker order package is expired at generation")
    client_ids = set()
    for index, row in enumerate(package.orders):
        required = {
            "client_order_id",
            "instrument_id",
            "symbol",
            "side",
            "order_type",
        }
        required |= {"quantity", "limit_price", "estimated_fee"}
        if set(row) != required:
            raise ValueError("broker order row does not match the export schema")
        client_id = row["client_order_id"]
        if not isinstance(client_id, str) or not client_id or client_id in client_ids:
            raise ValueError("client_order_id must be a unique non-empty string")
        client_ids.add(client_id)
        if (
            not isinstance(row["instrument_id"], str)
            or not row["instrument_id"]
            or not isinstance(row["symbol"], str)
            or _SYMBOL.fullmatch(row["symbol"]) is None
            or row["side"] not in {"buy", "sell"}
        ):
            raise ValueError("broker order symbol or side is invalid")
        if any(isinstance(row[field], bool) for field in ("quantity", "limit_price", "estimated_fee")):
            raise ValueError("broker order quantity, limit, or fee is invalid")
        quantity = money(row["quantity"])
        limit = money(row["limit_price"])
        fee = money(row["estimated_fee"])
        if (
            not quantity.is_finite()
            or quantity <= 0
            or quantity != quantity.to_integral_value()
            or row["order_type"] != "limit"
            or not limit.is_finite()
            or limit <= 0
            or not fee.is_finite()
            or fee < 0
        ):
            raise ValueError("broker order quantity, limit, or fee is invalid")
        signed_quantity = quantity * (1 if row["side"] == "buy" else -1)
        expected_client_id = _client_order_id(
            package.plan_id,
            index,
            row["instrument_id"],
            signed_quantity,
            limit,
        )
        if client_id != expected_client_id:
            raise ValueError("broker client_order_id does not match the package order")


def broker_order_package_from_payload(payload: dict[str, Any]) -> BrokerOrderPackage:
    """Load the exact JSON representation emitted by ``prepare_broker_orders``."""
    required = {field.name for field in BrokerOrderPackage.__dataclass_fields__.values()}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("broker order package fields are invalid")
    normalized = dict(payload)
    if not isinstance(normalized["orders"], list) or not all(
        isinstance(row, dict) for row in normalized["orders"]
    ):
        raise ValueError("broker order package orders must be a JSON list of objects")
    if not isinstance(normalized["source_ids"], list) or not all(
        isinstance(value, str) for value in normalized["source_ids"]
    ):
        raise ValueError("broker order package source_ids must be a JSON list of strings")
    schema_version = normalized.get("schema_version")
    if schema_version not in {"broker_order_package.v1", "broker_order_package.v2"}:
        raise ValueError("broker order package schema version is invalid")
    external_required = {
        "client_order_id",
        "instrument_id",
        "symbol",
        "side",
        "order_type",
    } | (
        {"quantity", "limit_price", "estimated_fee"}
        if schema_version == "broker_order_package.v1"
        else {"quantity_decimal", "limit_price_decimal", "estimated_fee_decimal"}
    )
    orders = []
    for row in normalized["orders"]:
        if set(row) != external_required:
            raise ValueError("broker order package order fields are invalid")
        if schema_version == "broker_order_package.v1":
            orders.append(dict(row))
        else:
            decimal_fields = ("quantity_decimal", "limit_price_decimal", "estimated_fee_decimal")
            if any(not isinstance(row[field], str) for field in decimal_fields):
                raise ValueError("broker order package v2 decimals must be strings")
            try:
                quantity = Decimal(row["quantity_decimal"])
                limit = Decimal(row["limit_price_decimal"])
                fee = Decimal(row["estimated_fee_decimal"])
            except DecimalException as error:
                raise ValueError("broker order package v2 decimals are invalid") from error
            if any(
                text != _decimal_text(value)
                for text, value in (
                    (row["quantity_decimal"], quantity),
                    (row["limit_price_decimal"], limit),
                    (row["estimated_fee_decimal"], fee),
                )
            ):
                raise ValueError("broker order package v2 decimals are not canonical")
            orders.append({
                **{key: value for key, value in row.items() if not key.endswith("_decimal")},
                "quantity": row["quantity_decimal"],
                "limit_price": row["limit_price_decimal"],
                "estimated_fee": row["estimated_fee_decimal"],
            })
    normalized["orders"] = tuple(orders)
    normalized["source_ids"] = tuple(normalized["source_ids"])
    try:
        package = BrokerOrderPackage(**normalized)
        validate_broker_order_package(package)
    except (TypeError, ValueError) as error:
        raise ValueError("broker order package object failed identity validation") from error
    return package


def prepare_broker_order_package(
    plan: OrderPlan,
    account_snapshot: BrokerAccountSnapshot,
    ledger: EventLedger,
    symbol_map: dict[str, str],
    generated_at: datetime,
    schema_version: str = "broker_order_package.v2",
) -> BrokerOrderPackage:
    if schema_version not in {"broker_order_package.v1", "broker_order_package.v2"}:
        raise ValueError("unsupported broker order package schema")
    validate_broker_account_snapshot(account_snapshot)
    timestamp = _timestamp(generated_at, "generated_at")
    expires_at = _timestamp(plan.expires_at, "expires_at")
    if timestamp >= expires_at:
        raise ValueError("cannot export an expired order plan")
    account_available_at = _timestamp(account_snapshot.available_at, "account available_at")
    if account_available_at > timestamp or timestamp - account_available_at > timedelta(hours=24):
        raise ValueError("broker account snapshot is unavailable or stale at order export")
    ledger_snapshot = ledger.snapshot()
    if plan.plan_basis_hash != ledger.basis_hash() or plan.account_snapshot_ref != ledger_snapshot["state_hash"]:
        raise ValueError("order plan does not match the current account state")
    imports = [event for event in ledger.events if event.kind == "account_snapshot_import"]
    if (
        len(imports) != 1
        or imports[0].payload.get("account_id") != account_snapshot.account_id
        or imports[0].payload.get("broker_id") != account_snapshot.broker_id
    ):
        raise ValueError("order plan ledger does not belong to the broker account snapshot")
    if reconcile_broker_snapshot(account_snapshot, ledger).status != "matched":
        raise ValueError("broker account snapshot must reconcile before order export")
    if not plan.orders:
        raise ValueError("order plan cannot be empty")
    exported = []
    planned_sells: dict[str, Decimal] = {}
    required_buying_power = Decimal("0")
    for index, order in enumerate(plan.orders):
        instrument_id = order.get("instrument")
        if not isinstance(instrument_id, str) or not instrument_id:
            raise ValueError("order plan instrument is invalid")
        symbol = symbol_map.get(instrument_id)
        if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
            raise ValueError(f"missing or invalid broker symbol for {instrument_id}")
        quantity = money(order.get("quantity"))
        if not quantity.is_finite() or not quantity or quantity != quantity.to_integral_value():
            raise ValueError("manual broker export requires non-zero integer share quantities")
        limit = money(order.get("max_price"))
        fee = money(order.get("estimated_fee", 0))
        if not limit.is_finite() or limit <= 0 or not fee.is_finite() or fee < 0:
            raise ValueError("manual broker export requires a positive limit and non-negative fee")
        side = "buy" if quantity > 0 else "sell"
        if side == "sell":
            planned_sells[instrument_id] = planned_sells.get(instrument_id, Decimal("0")) + -quantity
        else:
            required_buying_power += quantity * limit + fee
        client_order_id = _client_order_id(plan.plan_id, index, instrument_id, quantity, limit)
        numeric_values: tuple[float | str, float | str, float | str] = (
            (float(abs(quantity)), float(limit), float(fee))
            if schema_version == "broker_order_package.v1"
            else (_decimal_text(abs(quantity)), _decimal_text(limit), _decimal_text(fee))
        )
        exported.append(
            {
                "client_order_id": client_order_id,
                "instrument_id": instrument_id,
                "symbol": symbol,
                "side": side,
                "quantity": numeric_values[0],
                "order_type": "limit",
                "limit_price": numeric_values[1],
                "estimated_fee": numeric_values[2],
            }
        )
    for instrument_id, quantity in planned_sells.items():
        if quantity > ledger.state.positions.get(instrument_id, Decimal("0")):
            raise ValueError("manual broker export cannot create a short position")
    if required_buying_power > ledger.state.available_cash:
        raise ValueError("manual broker export exceeds settled cash buying power at limit prices")
    provisional = BrokerOrderPackage(
        package_id="pending",
        plan_id=plan.plan_id,
        account_id=account_snapshot.account_id,
        broker_id=account_snapshot.broker_id,
        account_snapshot_id=account_snapshot.snapshot_id,
        account_state_hash=ledger_snapshot["state_hash"],
        account_sequence=int(ledger_snapshot["last_sequence"]),
        generated_at=_utc_text(timestamp),
        expires_at=_utc_text(expires_at),
        status="pending_human_approval",
        orders=tuple(exported),
        source_ids=plan.source_ids,
        schema_version=schema_version,
    )
    package = BrokerOrderPackage(
        **{**asdict(provisional), "package_id": canonical_hash(_package_identity(provisional))[:24]}
    )
    validate_broker_order_package(package)
    return package
