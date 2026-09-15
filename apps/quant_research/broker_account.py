"""Broker-neutral, read-only cash-account snapshot import and reconciliation."""

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
BALANCE_FIELDS = (
    "settled_cash",
    "trade_receivable",
    "dividend_receivable",
    "trade_payable",
    "other_payable",
)


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    snapshot_id: str
    account_id: str
    broker_id: str
    account_type: str
    base_currency: str
    captured_at: str
    available_at: str
    balances: dict[str, Any]
    positions: tuple[dict[str, Any], ...]
    source: dict[str, str]
    execution_capability: str
    schema_version: str = "broker_account_snapshot.v1"


@dataclass(frozen=True)
class AccountReconciliation:
    reconciliation_id: str
    snapshot_id: str
    account_id: str
    ledger_state_hash: str
    status: str
    differences: tuple[dict[str, str], ...]
    schema_version: str = "account_reconciliation.v1"


def _timestamp(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_decimal(
    value: Any,
    field: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
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
        or (canonical_input and value != canonical)
    ):
        raise ValueError(f"{field} is invalid")
    return canonical


def _validated_positions(rows: Any, schema_version: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(rows, list):
        raise ValueError("positions must be a JSON list")
    required = (
        {"instrument_id", "symbol", "asset_type", "quantity", "multiplier"}
        if schema_version == "broker_account_snapshot.v1"
        else {"instrument_id", "symbol", "asset_type", "quantity_decimal", "multiplier_decimal"}
    )
    positions = []
    seen = set()
    for original in rows:
        if not isinstance(original, dict) or set(original) != required:
            raise ValueError("position rows must match the broker snapshot schema")
        instrument_id = original["instrument_id"]
        symbol = original["symbol"]
        asset_type = original["asset_type"]
        if schema_version == "broker_account_snapshot.v1":
            quantity: float | str = float(original["quantity"])
            multiplier: float | str = float(original["multiplier"])
        else:
            quantity = _canonical_decimal(
                original["quantity_decimal"], "position quantity", positive=True, canonical_input=True
            )
            multiplier = _canonical_decimal(
                original["multiplier_decimal"], "position multiplier", positive=True, canonical_input=True
            )
        quantity_value = money(quantity)
        multiplier_value = money(multiplier)
        if (
            not isinstance(instrument_id, str)
            or not instrument_id
            or instrument_id in seen
            or not isinstance(symbol, str)
            or not symbol
        ):
            raise ValueError("position identifiers must be unique non-empty strings")
        if asset_type not in {"equity", "etf"}:
            raise ValueError("initial broker import supports only equity and ETF positions")
        if not quantity_value.is_finite() or quantity_value <= 0:
            raise ValueError("cash-account imports cannot contain zero, short, or non-finite positions")
        if not multiplier_value.is_finite() or multiplier_value != 1:
            raise ValueError("equity and ETF imports require multiplier 1")
        seen.add(instrument_id)
        positions.append(
            {
                "instrument_id": instrument_id,
                "symbol": symbol,
                "asset_type": asset_type,
                "quantity": quantity,
                "multiplier": multiplier,
            }
        )
    return tuple(sorted(positions, key=lambda row: row["instrument_id"]))


def _snapshot_identity(
    *,
    account_id: str,
    broker_id: str,
    captured_at: str,
    available_at: str,
    balances: dict[str, Any],
    positions: tuple[dict[str, Any], ...],
    source: dict[str, str],
    schema_version: str = "broker_account_snapshot.v1",
) -> dict[str, Any]:
    external_positions = (
        list(positions)
        if schema_version == "broker_account_snapshot.v1"
        else [
            {
                **{key: value for key, value in row.items() if key not in {"quantity", "multiplier"}},
                "quantity_decimal": row["quantity"],
                "multiplier_decimal": row["multiplier"],
            }
            for row in positions
        ]
    )
    return {
        "schema_version": schema_version,
        "account_id": account_id,
        "broker_id": broker_id,
        "account_type": "cash",
        "base_currency": "USD",
        "captured_at": captured_at,
        "available_at": available_at,
        "balances": balances,
        "positions": external_positions,
        "source": source,
    }


def attach_broker_account_snapshot_id(normalized_identity: dict[str, Any]) -> dict[str, Any]:
    """Attach a content ID after an adapter emits canonical v1 or v2 account fields."""
    required = {
        "schema_version",
        "account_id",
        "broker_id",
        "account_type",
        "base_currency",
        "captured_at",
        "available_at",
        "balances",
        "positions",
        "source",
    }
    if (
        not isinstance(normalized_identity, dict)
        or set(normalized_identity) != required
        or normalized_identity.get("schema_version") not in {
            "broker_account_snapshot.v1",
            "broker_account_snapshot.v2",
        }
    ):
        raise ValueError("normalized broker account snapshot fields are invalid")
    payload = dict(normalized_identity)
    payload["snapshot_id"] = canonical_hash(payload)[:24]
    return payload


def validate_broker_account_snapshot(snapshot: BrokerAccountSnapshot) -> None:
    identity = _snapshot_identity(
        account_id=snapshot.account_id,
        broker_id=snapshot.broker_id,
        captured_at=snapshot.captured_at,
        available_at=snapshot.available_at,
        balances=snapshot.balances,
        positions=snapshot.positions,
        source=snapshot.source,
        schema_version=snapshot.schema_version,
    )
    try:
        normalized = account_snapshot_from_payload(
            {**identity, "snapshot_id": snapshot.snapshot_id},
            _timestamp(snapshot.available_at, "available_at"),
            timedelta(seconds=1),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("broker account snapshot object failed identity validation") from error
    if normalized != snapshot:
        raise ValueError("broker account snapshot object failed identity validation")


def account_snapshot_from_payload(
    payload: dict[str, Any],
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerAccountSnapshot:
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if not math.isfinite(max_age.total_seconds()) or max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    required = {
        "schema_version",
        "snapshot_id",
        "account_id",
        "broker_id",
        "account_type",
        "base_currency",
        "captured_at",
        "available_at",
        "balances",
        "positions",
        "source",
    }
    if set(payload) != required or payload.get("schema_version") not in {
        "broker_account_snapshot.v1",
        "broker_account_snapshot.v2",
    }:
        raise ValueError("broker account snapshot fields or schema version are invalid")
    if (
        not isinstance(payload["account_id"], str)
        or not payload["account_id"]
        or not isinstance(payload["broker_id"], str)
        or not payload["broker_id"]
    ):
        raise ValueError("account_id and broker_id are required")
    if payload["account_type"] != "cash" or payload["base_currency"] != "USD":
        raise ValueError("initial broker import supports only USD cash accounts")
    captured_at = _timestamp(payload["captured_at"], "captured_at")
    available_at = _timestamp(payload["available_at"], "available_at")
    decision_at = as_of.astimezone(timezone.utc)
    if available_at < captured_at or available_at > decision_at:
        raise ValueError("snapshot timing must satisfy captured_at <= available_at <= as_of")
    if decision_at - available_at > max_age:
        raise ValueError("broker account snapshot is stale")
    balances = payload["balances"]
    if not isinstance(balances, dict) or set(balances) != set(BALANCE_FIELDS):
        raise ValueError("broker account balance fields are invalid")
    schema_version = payload["schema_version"]
    if schema_version == "broker_account_snapshot.v1":
        normalized_balances: dict[str, Any] = {field: float(balances[field]) for field in BALANCE_FIELDS}
    else:
        normalized_balances = {
            field: _canonical_decimal(
                balances[field],
                f"balance {field}",
                non_negative=True,
                canonical_input=True,
            )
            for field in BALANCE_FIELDS
        }
    if any(not money(value).is_finite() or money(value) < 0 for value in normalized_balances.values()):
        raise ValueError("broker account balances must be finite and non-negative")
    source = payload["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"source_kind", "export_id", "content_sha256"}
        or source["source_kind"] not in {"broker_observed", "synthetic"}
        or not isinstance(source["export_id"], str)
        or not source["export_id"]
        or not isinstance(source["content_sha256"], str)
        or _HASH.fullmatch(source["content_sha256"]) is None
    ):
        raise ValueError("broker snapshot source evidence is invalid")
    positions = _validated_positions(payload["positions"], schema_version)
    identity = _snapshot_identity(
        account_id=payload["account_id"],
        broker_id=payload["broker_id"],
        captured_at=_utc_text(captured_at),
        available_at=_utc_text(available_at),
        balances=normalized_balances,
        positions=positions,
        source=dict(source),
        schema_version=schema_version,
    )
    expected_id = canonical_hash(identity)[:24]
    if payload["snapshot_id"] != expected_id:
        raise ValueError("broker account snapshot identity mismatch")
    return BrokerAccountSnapshot(
        snapshot_id=expected_id,
        account_id=payload["account_id"],
        broker_id=payload["broker_id"],
        account_type="cash",
        base_currency="USD",
        captured_at=_utc_text(captured_at),
        available_at=_utc_text(available_at),
        balances=normalized_balances,
        positions=positions,
        source=dict(source),
        execution_capability=(
            "broker_export_contract_validated"
            if source["source_kind"] == "broker_observed"
            else "simulation_only"
        ),
        schema_version=schema_version,
    )


def load_broker_account_snapshot(
    path: Path | str,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=24),
) -> BrokerAccountSnapshot:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker account snapshot must be a JSON object")
    return account_snapshot_from_payload(payload, as_of, max_age)


def ledger_from_broker_snapshot(snapshot: BrokerAccountSnapshot) -> EventLedger:
    validate_broker_account_snapshot(snapshot)
    ledger = EventLedger(0)
    payload = {
        **snapshot.balances,
        "positions": [
            {
                "instrument_id": row["instrument_id"],
                "quantity": row["quantity"],
                "multiplier": row["multiplier"],
            }
            for row in snapshot.positions
        ],
        "account_id": snapshot.account_id,
        "broker_id": snapshot.broker_id,
        "source_kind": snapshot.source["source_kind"],
    }
    ledger.apply(
        LedgerEvent(
            event_id=f"account-snapshot:{snapshot.snapshot_id}",
            effective_at=snapshot.captured_at,
            available_at=snapshot.available_at,
            kind="account_snapshot_import",
            payload=payload,
        )
    )
    return ledger


def reconcile_broker_snapshot(
    snapshot: BrokerAccountSnapshot,
    ledger: EventLedger,
) -> AccountReconciliation:
    validate_broker_account_snapshot(snapshot)
    differences: list[dict[str, str]] = []
    imports = [event for event in ledger.events if event.kind == "account_snapshot_import"]
    if (
        len(imports) != 1
        or imports[0].payload.get("account_id") != snapshot.account_id
        or imports[0].payload.get("broker_id") != snapshot.broker_id
    ):
        differences.append(
            {
                "kind": "identity",
                "field": "account_import",
                "broker": f"{snapshot.broker_id}:{snapshot.account_id}",
                "ledger": "missing_or_different",
            }
        )
    for field in BALANCE_FIELDS:
        broker_value = money(snapshot.balances[field])
        ledger_value = getattr(ledger.state, field)
        if broker_value != ledger_value:
            differences.append(
                {
                    "kind": "balance",
                    "field": field,
                    "broker": str(broker_value),
                    "ledger": str(ledger_value),
                }
            )
    broker_positions = {row["instrument_id"]: money(row["quantity"]) for row in snapshot.positions}
    for instrument_id in sorted(set(broker_positions) | set(ledger.state.positions)):
        broker_value = broker_positions.get(instrument_id, Decimal("0"))
        ledger_value = ledger.state.positions.get(instrument_id, Decimal("0"))
        if broker_value != ledger_value:
            differences.append(
                {
                    "kind": "position",
                    "field": instrument_id,
                    "broker": str(broker_value),
                    "ledger": str(ledger_value),
                }
            )
        broker_multiplier = next(
            (
                money(row["multiplier"])
                for row in snapshot.positions
                if row["instrument_id"] == instrument_id
            ),
            Decimal("0"),
        )
        ledger_multiplier = ledger.state.position_multipliers.get(instrument_id, Decimal("0"))
        if broker_multiplier != ledger_multiplier:
            differences.append(
                {
                    "kind": "multiplier",
                    "field": instrument_id,
                    "broker": str(broker_multiplier),
                    "ledger": str(ledger_multiplier),
                }
            )
    state_hash = ledger.snapshot()["state_hash"]
    identity = {
        "snapshot_id": snapshot.snapshot_id,
        "account_id": snapshot.account_id,
        "ledger_state_hash": state_hash,
        "differences": differences,
    }
    return AccountReconciliation(
        reconciliation_id=canonical_hash(identity)[:24],
        snapshot_id=snapshot.snapshot_id,
        account_id=snapshot.account_id,
        ledger_state_hash=state_hash,
        status="matched" if not differences else "mismatch",
        differences=tuple(differences),
    )
