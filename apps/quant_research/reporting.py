"""Atomic, content-addressed publication of completed research runs."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .config import ResearchConfig
from .contracts import DataSnapshot, canonical_hash


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_published_run(path: Path | str) -> dict:
    root = Path(path).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    claimed_hash = manifest.get("manifest_hash")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if not claimed_hash or canonical_hash(unsigned) != claimed_hash:
        raise RuntimeError("published run manifest hash mismatch")
    if manifest.get("status") != "complete":
        raise RuntimeError("published run is incomplete")
    for name, evidence in manifest.get("files", {}).items():
        if Path(name).name != name:
            raise RuntimeError("published run contains an invalid file path")
        artifact = root / name
        if not artifact.is_file() or _hash(artifact) != evidence["sha256"] or artifact.stat().st_size != evidence["bytes"]:
            raise RuntimeError(f"published run artifact hash mismatch: {name}")
    return manifest


def publish_stage1_report(
    report: dict,
    output_root: Path | str,
    snapshot: DataSnapshot,
    config: ResearchConfig,
    source_paths: tuple[Path | str, ...] | None = None,
) -> Path:
    if report.get("status") != "complete":
        raise ValueError("only complete reports can be published")
    if source_paths is None:
        module_root = Path(__file__).resolve().parent
        source_paths = tuple(
            module_root / name
            for name in ("config.py", "contracts.py", "engine.py", "ledger.py", "portfolio.py", "reporting.py")
        )
    workspace_root = Path(__file__).resolve().parents[2]
    sources = {}
    for value in source_paths:
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError(f"source file does not exist: {path}")
        try:
            key = path.relative_to(workspace_root).as_posix()
        except ValueError:
            key = path.name
        if key in sources:
            raise ValueError(f"source archive name is ambiguous: {key}")
        sources[key] = {"sha256": _hash(path), "bytes": path.stat().st_size}
    dependencies = {}
    for package in ("numpy", "pandas", "PyYAML"):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = "unavailable"
    run_id = canonical_hash({
        "kind": "stage1_etf_account",
        "snapshot": snapshot.manifest_hash,
        "config": config.config_hash,
        "sources": sources,
        "dependencies": dependencies,
        "engine_version": "1",
    })[:20]
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / run_id
    if target.exists():
        manifest = validate_published_run(target)
        if manifest["run_id"] != run_id:
            raise RuntimeError("existing run directory is invalid")
        return target
    temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=root))
    try:
        report["equity_curves"].to_csv(temporary / "equity_curves.csv", index=False)
        report["orders"].to_csv(temporary / "orders.csv", index=False)
        (temporary / "config.json").write_text(
            json.dumps(asdict(config), indent=2, sort_keys=True), encoding="utf-8"
        )
        result = {key: value for key, value in report.items() if key not in {"equity_curves", "orders", "event_logs"}}
        (temporary / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
        (temporary / "event_log.json").write_text(json.dumps(report["event_logs"], indent=2, sort_keys=True), encoding="utf-8")
        files = {
            path.name: {"sha256": _hash(path), "bytes": path.stat().st_size}
            for path in sorted(temporary.iterdir())
            if path.is_file()
        }
        manifest = {
            "schema_version": "run_manifest.v1",
            "run_id": run_id,
            "status": "complete",
            "run_kind": "stage1_etf_account",
            "config_hash": config.config_hash,
            "data_snapshot": asdict(snapshot),
            "source_files": sources,
            "runtime": {"python": sys.version.split()[0], "dependencies": dependencies},
            "files": files,
        }
        manifest["manifest_hash"] = canonical_hash(manifest)
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target
