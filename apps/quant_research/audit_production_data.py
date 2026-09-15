"""Command-line production-data readiness audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_readiness import audit_production_data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument-master", required=True, type=Path)
    parser.add_argument("--membership", required=True, type=Path)
    parser.add_argument("--bars", required=True, type=Path)
    parser.add_argument("--corporate-actions", required=True, type=Path)
    parser.add_argument("--start", required=True, help="Timezone-aware inclusive audit start")
    parser.add_argument("--end", required=True, help="Timezone-aware exclusive audit end")
    parser.add_argument("--option-contracts", type=Path)
    parser.add_argument("--option-quotes", type=Path)
    parser.add_argument("--option-lifecycle-events", type=Path)
    parser.add_argument("--fundamentals", type=Path)
    parser.add_argument("--classification-vintages", type=Path)
    parser.add_argument("--macro-vintages", type=Path)
    parser.add_argument("--max-bar-delay-hours", type=float, default=12.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = audit_production_data(
        instrument_master_csv=args.instrument_master,
        membership_csv=args.membership,
        bars_csv=args.bars,
        corporate_actions_csv=args.corporate_actions,
        start=args.start,
        end=args.end,
        option_contracts_csv=args.option_contracts,
        option_quotes_csv=args.option_quotes,
        option_lifecycle_events_csv=args.option_lifecycle_events,
        fundamentals_csv=args.fundamentals,
        classification_vintages_csv=args.classification_vintages,
        macro_vintages_csv=args.macro_vintages,
        max_bar_delay_hours=args.max_bar_delay_hours,
    )
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.status == "valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
