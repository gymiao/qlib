"""Export Qlib research artifacts into the dashboard's stable JSON contract."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def export(artifacts: Path, output: Path) -> dict:
    with (artifacts / "result.json").open(encoding="utf-8") as file:
        result = json.load(file)
    periods = read_csv(artifacts / "backtest.csv")
    holdings = read_csv(artifacts / "holdings.csv")
    rank_ic = read_csv(artifacts / "rank_ic.csv")

    strategy = benchmark = peak = 1.0
    equity_curve = []
    for period in periods:
        strategy *= 1 + float(period["net_return"])
        benchmark *= 1 + float(period["benchmark_return"])
        peak = max(peak, strategy)
        equity_curve.append(
            {
                "date": period["signal_date"],
                "strategy": round(strategy, 8),
                "benchmark": round(benchmark, 8),
                "drawdown": round(strategy / peak - 1, 8),
            }
        )

    latest_signal = max(row["signal_date"] for row in holdings)
    latest_holdings = sorted(
        (
            {
                "instrument": row["instrument"],
                "score": float(row["score"]),
                "forward_return": float(row["forward_return"]),
            }
            for row in holdings
            if row["signal_date"] == latest_signal
        ),
        key=lambda row: row["score"],
        reverse=True,
    )
    result.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "equity_curve": equity_curve,
            "rank_ic_series": [
                {"date": row["datetime"], "value": float(row["rank_ic"])}
                for row in rank_ic
                if row["rank_ic"]
            ],
            "latest_holdings": latest_holdings,
            "recent_periods": [
                {
                    "signal_date": row["signal_date"],
                    "net_return": float(row["net_return"]),
                    "benchmark_return": float(row["benchmark_return"]),
                    "excess_return": float(row["excess_return"]),
                    "turnover": float(row["turnover"]),
                }
                for row in periods[-12:]
            ],
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = export(args.artifacts, args.output)
    print(
        f"Exported {len(data['equity_curve'])} periods and "
        f"{len(data['latest_holdings'])} holdings to {args.output}"
    )


if __name__ == "__main__":
    main()
