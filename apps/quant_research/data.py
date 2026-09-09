"""Immutable local data snapshots with content-addressed manifests."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from .contracts import DataSnapshot, canonical_hash, utc_timestamp


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_price_snapshot(
    source_csv: Path | str,
    snapshot_root: Path | str,
    universe_id: str,
    instruments: tuple[str, ...],
    as_of: datetime,
) -> DataSnapshot:
    source = Path(source_csv).resolve()
    frame = pd.read_csv(source, parse_dates=["date"])
    required = {"date", *instruments}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"price source is missing columns: {', '.join(missing)}")
    if frame.empty or frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise ValueError("price source requires unique, sorted dates")
    values = frame[list(instruments)]
    if values.isna().any().any() or not np.isfinite(values.to_numpy(dtype=float)).all() or (values <= 0).any().any():
        raise ValueError("price source contains missing or non-positive values")
    identity = {
        "source_hash": file_hash(source),
        "universe_id": universe_id,
        "instruments": list(instruments),
        "as_of": utc_timestamp(as_of),
        "rows": len(frame),
        "date_range": [frame["date"].iloc[0].date().isoformat(), frame["date"].iloc[-1].date().isoformat()],
    }
    snapshot_id = canonical_hash(identity)[:20]
    target = Path(snapshot_root).resolve() / snapshot_id
    target.mkdir(parents=True, exist_ok=True)
    prices_path = target / "prices.csv"
    if prices_path.exists() and file_hash(prices_path) != identity["source_hash"]:
        raise RuntimeError("immutable snapshot collision")
    if not prices_path.exists():
        shutil.copyfile(source, prices_path)
    table = {"path": "prices.csv", "sha256": file_hash(prices_path), "rows": len(frame)}
    gaps = frame["date"].diff().dt.days.dropna()
    quality = {
        "status": "valid", "missing_values": 0, "duplicate_dates": 0,
        "rows": len(frame), "instruments": list(instruments),
        "start_date": identity["date_range"][0], "end_date": identity["date_range"][1],
        "max_calendar_gap_days": int(gaps.max()) if len(gaps) else 0,
    }
    unsigned = {**identity, "snapshot_id": snapshot_id, "tables": {"prices": table}, "quality": quality}
    manifest_hash = canonical_hash(unsigned)
    snapshot = DataSnapshot(snapshot_id, manifest_hash, identity["as_of"], universe_id, {"prices": table}, quality)
    manifest_path = target / "manifest.json"
    payload = {**asdict(snapshot), "identity": identity}
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
        raise RuntimeError("immutable manifest collision")
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot


def load_price_snapshot(snapshot_root: Path | str, snapshot_id: str) -> tuple[DataSnapshot, pd.DataFrame]:
    target = Path(snapshot_root).resolve() / snapshot_id
    payload = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    snapshot = DataSnapshot(**{key: payload[key] for key in DataSnapshot.__dataclass_fields__})
    unsigned = {
        **payload["identity"], "snapshot_id": snapshot.snapshot_id,
        "tables": snapshot.tables, "quality": snapshot.quality,
    }
    if snapshot.snapshot_id != snapshot_id or canonical_hash(unsigned) != snapshot.manifest_hash:
        raise RuntimeError("snapshot manifest hash mismatch")
    if snapshot.tables.get("prices", {}).get("path") != "prices.csv":
        raise RuntimeError("snapshot price path is invalid")
    table_path = target / snapshot.tables["prices"]["path"]
    if file_hash(table_path) != snapshot.tables["prices"]["sha256"]:
        raise RuntimeError("snapshot content hash mismatch")
    frame = pd.read_csv(table_path, parse_dates=["date"]).set_index("date")
    return snapshot, frame
