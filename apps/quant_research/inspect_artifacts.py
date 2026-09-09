"""Inspect and verify published research runs or saved paper-simulator state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .reporting import validate_published_run
from .simulator import PaperSimulator


def inspect_run(path: Path | str) -> dict:
    manifest = validate_published_run(path)
    snapshot = manifest.get("data_snapshot", {})
    return {
        "kind": "published_run",
        "status": "valid",
        "run_id": manifest["run_id"],
        "run_kind": manifest["run_kind"],
        "data_snapshot_id": snapshot.get("snapshot_id"),
        "artifact_count": len(manifest.get("files", {})),
        "source_file_count": len(manifest.get("source_files", {})),
        "config_hash": manifest.get("config_hash"),
    }


def inspect_simulator(path: Path | str) -> dict:
    monitor = PaperSimulator.load(path).monitor()
    return {"kind": "paper_simulator", "status": "valid", **monitor}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-dir", type=Path)
    group.add_argument("--simulator-state", type=Path)
    args = parser.parse_args()
    payload = inspect_run(args.run_dir) if args.run_dir else inspect_simulator(args.simulator_state)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
