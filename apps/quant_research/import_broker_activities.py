"""Import read-only broker settlements and cash activities; never place orders."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path

from .broker_activities import import_broker_activity_batch, load_broker_activity_batch
from .simulator import SimulatorStateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activities", required=True, type=Path)
    parser.add_argument("--simulator-state", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--max-age-minutes", type=float, default=1440.0)
    parser.add_argument(
        "--expected-content-sha256",
        help="Optional CAS guard from a previously inspected simulator state.",
    )
    args = parser.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    store = SimulatorStateStore(args.simulator_state)

    def operation(simulator):
        batch = load_broker_activity_batch(
            args.activities,
            simulator.ledger,
            as_of,
            timedelta(minutes=args.max_age_minutes),
        )
        return import_broker_activity_batch(batch, simulator.ledger)

    result, _ = store.transact(
        operation,
        expected_content_sha256=args.expected_content_sha256,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
