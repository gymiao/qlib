"""CLI for publishing, evaluating, and inspecting the local forward-signal archive."""

from __future__ import annotations

import argparse
from dataclasses import fields
import json
from pathlib import Path

from .contracts import FeatureBatch, LabelBatch, SignalBatch
from .forward_archive import (
    publish_forward_evaluation,
    publish_current_signal_view,
    publish_forward_signal,
    summarize_forward_archive,
    validate_forward_evaluation,
    validate_forward_signal,
)


def _object(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def _contract(payload: dict, contract_type):
    names = {field.name for field in fields(contract_type)}
    unknown = sorted(set(payload) - names)
    if unknown:
        raise ValueError(f"unknown contract fields: {', '.join(unknown)}")
    return contract_type(**payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="archive a currently valid forward signal")
    publish.add_argument("--archive-root", required=True)
    publish.add_argument("--signal-file", required=True)
    publish.add_argument("--data-snapshot-id", required=True)
    publish.add_argument("--data-as-of", required=True)
    publish.add_argument("--data-manifest", required=True)
    publish.add_argument("--feature-batch", required=True)

    evaluate = commands.add_parser("evaluate", help="append matured labels to an archived signal")
    evaluate.add_argument("--archive-root", required=True)
    evaluate.add_argument("--signal-id", required=True)
    evaluate.add_argument("--labels-file", required=True)

    inspect = commands.add_parser("inspect", help="validate and summarize a forward archive")
    inspect.add_argument("--archive-root", required=True)

    export = commands.add_parser("export-current", help="publish a verified current signal for Dashboard")
    export.add_argument("--archive-root", required=True)
    export.add_argument("--as-of", required=True)
    export.add_argument("--strategy-id")
    export.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "publish":
        signal = _contract(_object(args.signal_file), SignalBatch)
        feature_payload = _object(args.feature_batch)
        feature_payload["columns"] = tuple(feature_payload.get("columns", ()))
        feature_payload["rows"] = tuple(feature_payload.get("rows", ()))
        feature_batch = _contract(feature_payload, FeatureBatch)
        path = publish_forward_signal(
            signal,
            args.archive_root,
            args.data_snapshot_id,
            args.data_as_of,
            _object(args.data_manifest),
            feature_batch,
        )
        manifest, _ = validate_forward_signal(path)
        result = {"path": str(path), "manifest": manifest}
    elif args.command == "evaluate":
        labels = _contract(_object(args.labels_file), LabelBatch)
        if isinstance(labels.rows, list):
            labels = LabelBatch(labels.data_snapshot_id, tuple(labels.rows), labels.schema_version)
        path = publish_forward_evaluation(args.archive_root, args.signal_id, labels)
        manifest, evaluation = validate_forward_evaluation(path)
        result = {"path": str(path), "manifest": manifest, "evaluation": evaluation}
    elif args.command == "inspect":
        result = summarize_forward_archive(args.archive_root)
    else:
        result = publish_current_signal_view(
            args.archive_root,
            args.output,
            args.as_of,
            args.strategy_id,
        )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
