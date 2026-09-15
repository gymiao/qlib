"""Bootstrap or reconcile a paper ledger from a broker-neutral read-only snapshot."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path

from .broker_account import (
    ledger_from_broker_snapshot,
    load_broker_account_snapshot,
    reconcile_broker_snapshot,
)
from .simulator import PaperSimulator, SimulatorStateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "reconcile"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot", required=True, type=Path)
        command.add_argument("--simulator-state", required=True, type=Path)
        command.add_argument("--as-of", required=True)
        command.add_argument("--max-age-minutes", type=float, default=1440.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    snapshot = load_broker_account_snapshot(
        args.snapshot,
        as_of,
        timedelta(minutes=args.max_age_minutes),
    )
    store = SimulatorStateStore(args.simulator_state)
    if args.command == "bootstrap":
        simulator = store.create(PaperSimulator(ledger_from_broker_snapshot(snapshot)))
    else:
        simulator = store.load()
    reconciliation = reconcile_broker_snapshot(snapshot, simulator.ledger)
    result = {
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "account_id": snapshot.account_id,
            "broker_id": snapshot.broker_id,
            "available_at": snapshot.available_at,
            "execution_capability": snapshot.execution_capability,
        },
        "reconciliation": asdict(reconciliation),
        "simulator_state": str(args.simulator_state.resolve()),
        "orders_submitted": 0,
    }
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if reconciliation.status == "matched" else 2


if __name__ == "__main__":
    raise SystemExit(main())
