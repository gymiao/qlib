"""Publish a redacted, hash-verified read-only account view for Dashboard APIs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .broker_fills import summarize_broker_fills
from .broker_order_status import lifecycle_by_order, package_events
from .broker_orders import BrokerOrderPackage, broker_order_package_from_payload
from .contracts import canonical_hash
from .simulator import PaperSimulator, SimulatorStateStore


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


def _decimal_text(value: Any) -> str:
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise ValueError("operations snapshot cannot contain non-finite decimals")
    return format(decimal.normalize(), "f")


def _load_package(path: Path) -> BrokerOrderPackage:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"broker order package must be an object: {path}")
    return broker_order_package_from_payload(payload)


def _package_summary(package: BrokerOrderPackage, simulator: PaperSimulator) -> dict[str, Any]:
    fills, statuses, _ = package_events(package, simulator.ledger)
    lifecycle = lifecycle_by_order(package, statuses)
    fill_rows, fill_status = summarize_broker_fills(package, fills)
    fill_by_id = {row["client_order_id"]: row for row in fill_rows}
    orders = []
    for package_order in package.orders:
        client_id = package_order["client_order_id"]
        lifecycle_row = lifecycle[client_id]
        fill_row = fill_by_id[client_id]
        broker_order_id = lifecycle_row["broker_order_id"]
        orders.append(
            {
                "client_order_id": client_id,
                "instrument_id": package_order["instrument_id"],
                "symbol": package_order["symbol"],
                "side": package_order["side"],
                "ordered_quantity_decimal": _decimal_text(package_order["quantity"]),
                "filled_quantity_decimal": _decimal_text(fill_row["filled_quantity"]),
                "remaining_quantity_decimal": _decimal_text(fill_row["remaining_quantity"]),
                "fill_status": fill_row["status"],
                "lifecycle_status": lifecycle_row["status"],
                "broker_order_ref": (
                    canonical_hash({"broker_order_id": broker_order_id})[:12]
                    if broker_order_id is not None
                    else None
                ),
                "accepted_at": lifecycle_row["accepted_at"],
                "terminal_at": lifecycle_row["terminal_at"],
            }
        )
    return {
        "package_id": package.package_id,
        "plan_id": package.plan_id,
        "generated_at": package.generated_at,
        "expires_at": package.expires_at,
        "lifecycle_statuses": sorted({row["lifecycle_status"] for row in orders}),
        "fill_status": fill_status,
        "orders": orders,
    }


def build_operations_snapshot(
    simulator: PaperSimulator,
    generated_at: datetime,
    packages: tuple[BrokerOrderPackage, ...] = (),
) -> dict[str, Any]:
    timestamp = _timestamp(generated_at, "generated_at")
    if simulator.content_sha256 is None:
        raise ValueError("operations snapshot requires a persisted hash-verified simulator state")
    events = simulator.ledger.events
    latest_event_at = max(
        (
            _timestamp(event.available_at or event.effective_at, "event available_at")
            for event in events
        ),
        default=timestamp,
    )
    if timestamp < latest_event_at:
        raise ValueError("generated_at cannot precede the latest available account event")
    account_imports = [event for event in events if event.kind == "account_snapshot_import"]
    if len(account_imports) > 1:
        raise ValueError("operations snapshot found multiple account imports")
    account_import = account_imports[0] if account_imports else None
    account_ref = (
        canonical_hash(
            {
                "broker_id": account_import.payload.get("broker_id"),
                "account_id": account_import.payload.get("account_id"),
            }
        )[:16]
        if account_import is not None
        else "paper-account"
    )
    state = simulator.ledger.state
    identity = {
        "schema_version": "operations_snapshot.v1",
        "kind": "account_snapshot",
        "run_id": simulator.content_sha256,
        "generated_at": _utc_text(timestamp),
        "status": "valid",
        "reason_codes": [],
        "read_only": True,
        "execution_capability": (
            "broker_export_contract_validated"
            if account_import is not None and account_import.payload.get("source_kind") == "broker_observed"
            else "simulation_only"
        ),
        "decimal_encoding": "canonical_string",
        "account": {
            "account_ref": account_ref,
            "account_as_of": _utc_text(latest_event_at),
            "state_revision": simulator.state_revision,
            "state_content_sha256": simulator.content_sha256,
            "event_chain_head": simulator.monitor()["event_chain_head"],
            "cash": {
                "settled_cash": _decimal_text(state.settled_cash),
                "trade_receivable": _decimal_text(state.trade_receivable),
                "dividend_receivable": _decimal_text(state.dividend_receivable),
                "trade_payable": _decimal_text(state.trade_payable),
                "other_payable": _decimal_text(state.other_payable),
                "available_cash": _decimal_text(state.available_cash),
            },
            "positions": [
                {
                    "instrument_id": instrument_id,
                    "quantity_decimal": _decimal_text(quantity),
                    "multiplier_decimal": _decimal_text(state.position_multipliers.get(instrument_id, 1)),
                }
                for instrument_id, quantity in sorted(state.positions.items())
            ],
        },
        "events": {
            "count": len(events),
            "by_kind": dict(sorted(Counter(event.kind for event in events).items())),
        },
        "order_packages": [
            _package_summary(package, simulator)
            for package in sorted(packages, key=lambda item: item.package_id)
        ],
    }
    return {**identity, "content_sha256": canonical_hash(identity)}


def publish_operations_snapshot(
    simulator_state: Path,
    output: Path,
    generated_at: datetime,
    package_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    simulator = SimulatorStateStore(simulator_state).load()
    packages = tuple(_load_package(path) for path in package_paths)
    payload = build_operations_snapshot(simulator, generated_at, packages)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    handle, temporary = tempfile.mkstemp(prefix=f".{output.name}-", dir=output.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator-state", required=True, type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--package", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    payload = publish_operations_snapshot(
        args.simulator_state,
        args.output,
        _timestamp(args.generated_at, "generated_at"),
        tuple(args.package),
    )
    print(json.dumps({
        "status": payload["status"],
        "content_sha256": payload["content_sha256"],
        "output": str(args.output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
