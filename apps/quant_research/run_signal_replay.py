"""Run a deterministic QQQ/SPY replay with frozen TradingAgents signals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .integrations.replay import SignalReplayConfig, run_signal_replay
from .integrations.tradingagents import load_research_signals
from .run_research import _load_prices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--prices-csv", type=Path)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-01-01")
    parser.add_argument("--output-dir", type=Path, default=Path(".artifacts/signal_replay"))
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--transaction-cost-bps", type=float, default=2.0)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = run_signal_replay(
        _load_prices(args.prices_csv, args.start, args.end),
        pd.Series({"QQQ": 0.70, "SPY": 0.30}, name="target_weight"),
        load_research_signals(args.signals),
        SignalReplayConfig(initial_cash=args.initial_cash, transaction_cost_bps=args.transaction_cost_bps),
    )
    curves = report.pop("equity_curves")
    orders = report.pop("orders")
    targets = report.pop("targets")
    curves.to_csv(output / "equity_curves.csv", index=False)
    orders.to_csv(output / "orders.csv", index=False)
    targets.to_csv(output / "targets.csv", index=False)
    (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
