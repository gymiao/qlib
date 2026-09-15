"""Write a local manual-review order package; never submit it to a broker."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile

from .broker_account import load_broker_account_snapshot
from .broker_orders import broker_order_package_payload, prepare_broker_order_package
from .contracts import OrderPlan, canonical_hash
from .simulator import SimulatorStateStore


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--simulator-state", required=True, type=Path)
    parser.add_argument("--symbol-map", required=True, type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--max-snapshot-age-minutes", type=float, default=1440.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    timestamp = datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
    snapshot = load_broker_account_snapshot(
        args.snapshot,
        timestamp,
        timedelta(minutes=args.max_snapshot_age_minutes),
    )
    plan_payload = _object(args.plan)
    plan_payload["source_ids"] = tuple(plan_payload.get("source_ids", ()))
    plan_payload["orders"] = tuple(plan_payload.get("orders", ()))
    plan_payload["reason_codes"] = tuple(plan_payload.get("reason_codes", ()))
    plan = OrderPlan(**plan_payload)
    symbol_map = _object(args.symbol_map)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in symbol_map.items()):
        raise ValueError("symbol map must contain string instrument_id to symbol pairs")
    simulator = SimulatorStateStore(args.simulator_state).load()
    package = prepare_broker_order_package(plan, snapshot, simulator.ledger, symbol_map, timestamp)
    payload = broker_order_package_payload(package)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        existing = _object(args.output)
        if canonical_hash(existing) != canonical_hash(payload):
            raise FileExistsError("order package output exists with different content")
    else:
        handle, temporary = tempfile.mkstemp(prefix=f".{args.output.name}-", dir=args.output.parent)
        os.close(handle)
        temporary_path = Path(temporary)
        try:
            temporary_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, args.output)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
