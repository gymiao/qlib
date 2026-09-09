"""Export one completed Stage-1 run into the Dashboard v2 report schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def export(run_dir: Path, output: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or result.get("status") != "complete":
        raise ValueError("run is not complete")
    curves = pd.read_csv(run_dir / "equity_curves.csv")
    orders = pd.read_csv(run_dir / "orders.csv")
    if curves.empty:
        raise ValueError("run has no equity curve")
    payload = {
        "schema_version": "research_report.v2",
        "kind": "research_report",
        "run_id": manifest["run_id"],
        "generated_at": manifest["data_snapshot"]["as_of"],
        "status": "complete",
        "reason_codes": [],
        "period_start": str(curves["date"].min()),
        "period_end": str(curves["date"].max()),
        "data_snapshot_id": manifest["data_snapshot"]["snapshot_id"],
        "metrics": result["metrics"],
        "risk": result["risk_snapshots"],
        "equity_curves": curves.to_dict("records"),
        "orders": orders.to_dict("records"),
        "data_quality": manifest["data_snapshot"]["quality"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = export(args.run_dir, args.output)
    print(f"Exported {payload['run_id']} to {args.output}")


if __name__ == "__main__":
    main()
