"""Import a read-only broker order-status export; never submit or cancel orders."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path

from .broker_order_status import (
    import_broker_order_status_batch,
    load_broker_order_status_batch,
)
from .broker_orders import broker_order_package_from_payload
from .simulator import SimulatorStateStore


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--statuses", required=True, type=Path)
    parser.add_argument("--simulator-state", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--max-age-minutes", type=float, default=1440.0)
    parser.add_argument(
        "--expected-content-sha256",
        help="Optional CAS guard from a previously inspected simulator state.",
    )
    args = parser.parse_args(argv)

    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    package = broker_order_package_from_payload(_object(args.package))
    batch = load_broker_order_status_batch(
        args.statuses,
        package,
        as_of,
        timedelta(minutes=args.max_age_minutes),
    )
    result, _ = SimulatorStateStore(args.simulator_state).transact(
        lambda simulator: import_broker_order_status_batch(batch, package, simulator.ledger),
        expected_content_sha256=args.expected_content_sha256,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
