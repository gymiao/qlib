"""Run the offline QQQ/SPY ledger comparison from an immutable CSV snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .config import ResearchConfig, load_config
from .data import create_price_snapshot, load_price_snapshot
from .engine import run_stage1_comparison
from .reporting import publish_stage1_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices-csv", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--snapshot-root", type=Path, default=Path(".artifacts/research_snapshots"))
    parser.add_argument("--output-root", type=Path, default=Path(".artifacts/quant_research_runs"))
    parser.add_argument("--as-of", required=True, help="UTC ISO-8601 time at which the input was available")
    args = parser.parse_args()
    parsed_as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if parsed_as_of.tzinfo is None:
        raise ValueError("--as-of requires an explicit timezone")
    config = load_config(args.config) if args.config else ResearchConfig()
    snapshot = create_price_snapshot(
        args.prices_csv,
        args.snapshot_root,
        universe_id="qqq-spy-etf",
        instruments=config.instruments,
        as_of=parsed_as_of.astimezone(timezone.utc),
    )
    _, prices = load_price_snapshot(args.snapshot_root, snapshot.snapshot_id)
    report = run_stage1_comparison(prices, config)
    target = publish_stage1_report(report, args.output_root, snapshot, config)
    print(target)


if __name__ == "__main__":
    main()
