"""Immutable local archive for genuinely forward-recorded signals and later labels."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

import pandas as pd

from .contracts import FeatureBatch, LabelBatch, SignalBatch, canonical_hash


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
EVIDENCE_SCOPE = "local_content_addressed_not_third_party_timestamped"


def _decimal_text(value: Any) -> str:
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise ValueError("dashboard signal cannot contain non-finite scores")
    return format(decimal.normalize(), "f")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: str | datetime, field: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: str, field: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} contains unsafe path characters")
    return value


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"archived JSON must be an object: {path.name}")
    return value


def _validate_data_manifest(payload: dict[str, Any], data_snapshot_id: str) -> None:
    schema = payload.get("schema_version")
    if schema in {"production_data_readiness.v1", "production_data_readiness.v2"}:
        identity = {
            "schema_version": schema,
            "requested_range": payload.get("requested_range"),
            "thresholds": payload.get("thresholds"),
            "tables": {
                name: {key: value for key, value in summary.items() if key != "path"}
                for name, summary in payload.get("tables", {}).items()
            },
            "capabilities": payload.get("capabilities"),
            "coverage": payload.get("coverage"),
            "issues": payload.get("issues"),
        }
        expected_id = canonical_hash(identity)[:20]
        core_tables = {"instrument_master", "universe_membership", "bars", "corporate_actions"}
        tables = payload.get("tables", {})
        if (
            payload.get("audit_id") != data_snapshot_id
            or expected_id != data_snapshot_id
            or payload.get("status") != "valid"
            or payload.get("capabilities", {}).get("equity_history") != "historical_validated"
            or not core_tables.issubset(tables)
            or any(not tables[name].get("sha256") for name in core_tables)
        ):
            raise ValueError("production data readiness manifest is invalid or does not match its audit_id")
        return
    if schema == "data_snapshot.v1":
        identity = payload.get("identity")
        if not isinstance(identity, dict):
            raise ValueError("data snapshot manifest has no identity")
        unsigned = {
            **identity,
            "snapshot_id": payload.get("snapshot_id"),
            "tables": payload.get("tables"),
            "quality": payload.get("quality"),
        }
        if (
            payload.get("snapshot_id") != data_snapshot_id
            or canonical_hash(unsigned) != payload.get("manifest_hash")
            or payload.get("quality", {}).get("status") != "valid"
        ):
            raise ValueError("data snapshot manifest is invalid or does not match its snapshot_id")
        return
    raise ValueError("unsupported data manifest schema")


def _validate_feature_batch(
    feature_batch: FeatureBatch,
    signal: SignalBatch,
    data_snapshot_id: str,
) -> None:
    if feature_batch.schema_version != "feature_batch.v1":
        raise ValueError("forward archive requires feature_batch.v1")
    if feature_batch.data_snapshot_id != data_snapshot_id:
        raise ValueError("feature batch does not reference the archived data snapshot")
    feature_time = _timestamp(feature_batch.decision_time, "feature decision_time")
    signal_time = _timestamp(signal.decision_time, "signal decision_time")
    if feature_time != signal_time:
        raise ValueError("feature batch and signal decision_time must match")
    expected_columns = {"instrument_id", *feature_batch.columns}
    identifiers: list[str] = []
    for row in feature_batch.rows:
        if not isinstance(row, dict) or set(row) != expected_columns:
            raise ValueError("feature batch rows must contain only instrument_id and declared columns")
        instrument_id = row["instrument_id"]
        if not isinstance(instrument_id, str) or not instrument_id:
            raise ValueError("feature batch instrument_id must be a non-empty string")
        try:
            values = [float(row[column]) for column in feature_batch.columns]
        except (TypeError, ValueError) as error:
            raise ValueError("feature batch values must be numeric") from error
        if any(not math.isfinite(value) for value in values):
            raise ValueError("feature batch values must be finite")
        identifiers.append(instrument_id)
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("feature batch identifiers must be non-empty and unique")
    if set(identifiers) != set(signal.scores):
        raise ValueError("feature batch targets do not match signal score targets")


def _validate_manifest(root: Path, expected_kind: str) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("archive manifest is missing")
    manifest = _load_json(manifest_path)
    claimed_hash = manifest.get("manifest_hash")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if not claimed_hash or canonical_hash(unsigned) != claimed_hash:
        raise RuntimeError("archive manifest hash mismatch")
    if manifest.get("kind") != expected_kind or manifest.get("status") != "complete":
        raise RuntimeError("archive manifest kind or status is invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("archive manifest has no file evidence")
    for name, evidence in files.items():
        if Path(name).name != name or name == "manifest.json":
            raise RuntimeError("archive manifest contains an invalid file path")
        artifact = root / name
        if (
            not artifact.is_file()
            or not isinstance(evidence, dict)
            or artifact.stat().st_size != evidence.get("bytes")
            or _file_hash(artifact) != evidence.get("sha256")
        ):
            raise RuntimeError(f"archive artifact hash mismatch: {name}")
    return manifest


def _publish_directory(target: Path, files: dict[str, Any], manifest: dict[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for name, payload in files.items():
            (temporary / name).write_text(
                json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        manifest["files"] = {
            path.name: {"sha256": _file_hash(path), "bytes": path.stat().st_size}
            for path in sorted(temporary.iterdir())
            if path.is_file()
        }
        manifest["manifest_hash"] = canonical_hash(manifest)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def validate_forward_signal(path: Path | str) -> tuple[dict[str, Any], SignalBatch]:
    root = Path(path).resolve()
    manifest = _validate_manifest(root, "forward_signal")
    payload = _load_json(root / "signal.json")
    try:
        signal = SignalBatch(**payload)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"archived signal contract is invalid: {error}") from error
    _safe_id(signal.signal_id, "signal_id")
    if signal.schema_version != "signal_batch.v1":
        raise RuntimeError("archived signal schema version is unsupported")
    if root.name != signal.signal_id or manifest.get("signal_id") != signal.signal_id:
        raise RuntimeError("archive signal identity mismatch")
    if manifest.get("signal_payload_hash") != canonical_hash(payload):
        raise RuntimeError("archive signal payload identity mismatch")
    data_manifest = _load_json(root / "data_manifest.json")
    try:
        _validate_data_manifest(data_manifest, manifest.get("data_snapshot_id"))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"archived data manifest is invalid: {error}") from error
    if manifest.get("data_manifest_hash") != canonical_hash(data_manifest):
        raise RuntimeError("archive data manifest identity mismatch")
    feature_payload = _load_json(root / "feature_batch.json")
    try:
        feature_batch = FeatureBatch(
            feature_schema_id=feature_payload["feature_schema_id"],
            data_snapshot_id=feature_payload["data_snapshot_id"],
            decision_time=feature_payload["decision_time"],
            columns=tuple(feature_payload["columns"]),
            rows=tuple(feature_payload["rows"]),
            schema_version=feature_payload["schema_version"],
        )
        if set(feature_payload) != set(asdict(feature_batch)):
            raise ValueError("feature batch fields do not match the contract")
        _validate_feature_batch(feature_batch, signal, manifest.get("data_snapshot_id"))
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"archived feature batch is invalid: {error}") from error
    if manifest.get("feature_batch_hash") != canonical_hash(asdict(feature_batch)):
        raise RuntimeError("archive feature batch identity mismatch")
    return manifest, signal


def publish_forward_signal(
    signal: SignalBatch,
    archive_root: Path | str,
    data_snapshot_id: str,
    data_as_of: str | datetime,
    data_manifest: dict[str, Any],
    feature_batch: FeatureBatch,
) -> Path:
    """Record a signal only while it could still have been acted on."""

    if not data_snapshot_id:
        raise ValueError("data_snapshot_id is required")
    if not signal.scores:
        raise ValueError("forward archive currently requires scored signal targets")
    _safe_id(signal.signal_id, "signal_id")
    if signal.schema_version != "signal_batch.v1":
        raise ValueError("forward archive requires signal_batch.v1")
    _validate_data_manifest(data_manifest, data_snapshot_id)
    _validate_feature_batch(feature_batch, signal, data_snapshot_id)
    decision = _timestamp(signal.decision_time, "decision_time")
    valid_until = _timestamp(signal.valid_until, "valid_until")
    data_time = _timestamp(data_as_of, "data_as_of")
    if data_time > decision:
        raise ValueError("data_as_of cannot be after decision_time")

    payload = asdict(signal)
    target = Path(archive_root).resolve() / "signals" / signal.signal_id
    if target.exists():
        manifest, archived = validate_forward_signal(target)
        expected = {
            "signal": payload,
            "data_snapshot_id": data_snapshot_id,
            "data_as_of": _utc_text(data_time),
        }
        actual = {
            "signal": asdict(archived),
            "data_snapshot_id": manifest.get("data_snapshot_id"),
            "data_as_of": manifest.get("data_as_of"),
        }
        if expected != actual:
            raise RuntimeError("signal_id already exists with different immutable content")
        archived_data_manifest = _load_json(target / "data_manifest.json")
        if archived_data_manifest != data_manifest:
            raise RuntimeError("signal_id already exists with a different data manifest")
        archived_features = _load_json(target / "feature_batch.json")
        if canonical_hash(archived_features) != canonical_hash(asdict(feature_batch)):
            raise RuntimeError("signal_id already exists with a different feature batch")
        return target

    recorded_at = _timestamp(_utc_now(), "recorded_at")
    if not decision <= recorded_at < valid_until:
        raise ValueError("signal can only enter the forward archive during its live validity window")

    manifest = {
        "schema_version": "forward_signal_manifest.v1",
        "kind": "forward_signal",
        "status": "complete",
        "signal_id": signal.signal_id,
        "strategy_id": signal.strategy_id,
        "decision_time": _utc_text(decision),
        "data_as_of": _utc_text(data_time),
        "valid_until": _utc_text(valid_until),
        "recorded_at": _utc_text(recorded_at),
        "data_snapshot_id": data_snapshot_id,
        "data_manifest_hash": canonical_hash(data_manifest),
        "feature_batch_hash": canonical_hash(asdict(feature_batch)),
        "signal_payload_hash": canonical_hash(payload),
        "evidence_scope": EVIDENCE_SCOPE,
    }
    try:
        return _publish_directory(
            target,
            {
                "signal.json": payload,
                "data_manifest.json": data_manifest,
                "feature_batch.json": asdict(feature_batch),
            },
            manifest,
        )
    except FileExistsError:
        manifest, archived = validate_forward_signal(target)
        if (
            asdict(archived) != payload
            or manifest.get("data_snapshot_id") != data_snapshot_id
            or manifest.get("data_as_of") != _utc_text(data_time)
            or _load_json(target / "data_manifest.json") != data_manifest
            or canonical_hash(_load_json(target / "feature_batch.json"))
            != canonical_hash(asdict(feature_batch))
        ):
            raise RuntimeError("signal_id was concurrently published with different immutable content")
        return target


def _validated_labels(
    signal: SignalBatch,
    labels: LabelBatch,
    evaluated_at: datetime,
) -> list[dict[str, Any]]:
    if not labels.rows:
        raise ValueError("forward evaluation requires at least one matured label")
    decision = _timestamp(signal.decision_time, "decision_time")
    required = {"instrument_id", "label_start", "label_end", "label_available_at", "value"}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for original in labels.rows:
        missing = sorted(required - set(original))
        if missing:
            raise ValueError(f"label row is missing: {', '.join(missing)}")
        unknown = sorted(set(original) - required)
        if unknown:
            raise ValueError(f"label row has unknown fields: {', '.join(unknown)}")
        instrument_id = original["instrument_id"]
        if not isinstance(instrument_id, str) or instrument_id not in signal.scores:
            raise ValueError("label instrument_id is not present in the archived signal")
        if instrument_id in seen:
            raise ValueError("forward evaluation contains duplicate instrument_id")
        seen.add(instrument_id)
        label_start = _timestamp(original["label_start"], "label_start")
        label_end = _timestamp(original["label_end"], "label_end")
        available = _timestamp(original["label_available_at"], "label_available_at")
        value = float(original["value"])
        if label_start <= decision or label_end < label_start:
            raise ValueError("label window must start after the decision and end after it starts")
        if available < label_end:
            raise ValueError("label_available_at cannot precede label_end")
        if available > evaluated_at:
            raise ValueError("forward evaluation cannot consume an unavailable label")
        if not math.isfinite(value):
            raise ValueError("forward label values must be finite")
        row = dict(original)
        row.update(
            {
                "instrument_id": instrument_id,
                "label_start": _utc_text(label_start),
                "label_end": _utc_text(label_end),
                "label_available_at": _utc_text(available),
                "value": value,
            }
        )
        rows.append(row)
    return sorted(rows, key=lambda row: row["instrument_id"])


def _rank_ic(signal: SignalBatch, rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    scores = pd.Series([signal.scores[row["instrument_id"]] for row in rows], dtype=float)
    values = pd.Series([row["value"] for row in rows], dtype=float)
    correlation = scores.rank(method="average").corr(values.rank(method="average"))
    return None if pd.isna(correlation) else float(correlation)


def validate_forward_evaluation(path: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(path).resolve()
    manifest = _validate_manifest(root, "forward_evaluation")
    payload = _load_json(root / "evaluation.json")
    required = {
        "schema_version",
        "evaluation_id",
        "signal_id",
        "strategy_id",
        "evaluated_at",
        "label_data_snapshot_id",
        "status",
        "coverage",
        "expected_labels",
        "matured_labels",
        "rank_ic",
        "mean_realized_value",
        "labels",
    }
    if set(payload) != required or payload.get("schema_version") != "forward_evaluation.v1":
        raise RuntimeError("forward evaluation contract is invalid")
    signal_id = payload.get("signal_id")
    try:
        _safe_id(signal_id, "signal_id")
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    if (
        root.name != manifest.get("evaluation_id")
        or payload.get("evaluation_id") != root.name
        or manifest.get("signal_id") != signal_id
        or root.parent.name != signal_id
    ):
        raise RuntimeError("forward evaluation identity mismatch")
    if manifest.get("evaluation_payload_hash") != canonical_hash(payload):
        raise RuntimeError("forward evaluation payload identity mismatch")
    signal_root = root.parents[2] / "signals" / signal_id
    signal_manifest, signal = validate_forward_signal(signal_root)
    if manifest.get("signal_manifest_hash") != signal_manifest["manifest_hash"]:
        raise RuntimeError("forward evaluation references a different signal manifest")
    try:
        evaluated_at = _timestamp(payload["evaluated_at"], "evaluated_at")
        labels = LabelBatch(payload["label_data_snapshot_id"], tuple(payload["labels"]))
        rows = _validated_labels(signal, labels, evaluated_at)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"forward evaluation labels are invalid: {error}") from error
    coverage = len(rows) / len(signal.scores)
    expected_identity = {
        "signal_manifest_hash": signal_manifest["manifest_hash"],
        "label_data_snapshot_id": labels.data_snapshot_id,
        "rows": rows,
    }
    expected_metrics = {
        "evaluation_id": canonical_hash(expected_identity)[:24],
        "strategy_id": signal.strategy_id,
        "status": "complete" if coverage == 1 else "partial",
        "coverage": coverage,
        "expected_labels": len(signal.scores),
        "matured_labels": len(rows),
        "rank_ic": _rank_ic(signal, rows),
        "mean_realized_value": float(pd.Series([row["value"] for row in rows]).mean()),
        "labels": rows,
    }
    for key, expected in expected_metrics.items():
        if payload.get(key) != expected:
            raise RuntimeError(f"forward evaluation derived field mismatch: {key}")
    return manifest, payload


def publish_forward_evaluation(
    archive_root: Path | str,
    signal_id: str,
    labels: LabelBatch,
) -> Path:
    if not labels.data_snapshot_id:
        raise ValueError("label data_snapshot_id is required")
    if labels.schema_version != "label_batch.v1":
        raise ValueError("forward evaluation requires label_batch.v1")
    _safe_id(signal_id, "signal_id")
    signal_root = Path(archive_root).resolve() / "signals" / signal_id
    signal_manifest, signal = validate_forward_signal(signal_root)
    evaluated_at = _timestamp(_utc_now(), "evaluated_at")
    rows = _validated_labels(signal, labels, evaluated_at)
    coverage = len(rows) / len(signal.scores)
    identity = {
        "signal_manifest_hash": signal_manifest["manifest_hash"],
        "label_data_snapshot_id": labels.data_snapshot_id,
        "rows": rows,
    }
    evaluation_id = canonical_hash(identity)[:24]
    payload = {
        "schema_version": "forward_evaluation.v1",
        "evaluation_id": evaluation_id,
        "signal_id": signal_id,
        "strategy_id": signal.strategy_id,
        "evaluated_at": _utc_text(evaluated_at),
        "label_data_snapshot_id": labels.data_snapshot_id,
        "status": "complete" if coverage == 1 else "partial",
        "coverage": coverage,
        "expected_labels": len(signal.scores),
        "matured_labels": len(rows),
        "rank_ic": _rank_ic(signal, rows),
        "mean_realized_value": float(pd.Series([row["value"] for row in rows]).mean()),
        "labels": rows,
    }
    target = Path(archive_root).resolve() / "evaluations" / signal_id / evaluation_id
    if target.exists():
        _, archived = validate_forward_evaluation(target)
        comparable = {key: value for key, value in payload.items() if key != "evaluated_at"}
        existing = {key: value for key, value in archived.items() if key != "evaluated_at"}
        if comparable != existing:
            raise RuntimeError("evaluation_id already exists with different immutable content")
        return target
    manifest = {
        "schema_version": "forward_evaluation_manifest.v1",
        "kind": "forward_evaluation",
        "status": "complete",
        "evaluation_id": evaluation_id,
        "signal_id": signal_id,
        "evaluated_at": payload["evaluated_at"],
        "evaluation_payload_hash": canonical_hash(payload),
        "signal_manifest_hash": signal_manifest["manifest_hash"],
        "evidence_scope": EVIDENCE_SCOPE,
    }
    return _publish_directory(target, {"evaluation.json": payload}, manifest)


def summarize_forward_archive(archive_root: Path | str) -> dict[str, Any]:
    root = Path(archive_root).resolve()
    signals: list[dict[str, Any]] = []
    latest_evaluations: list[dict[str, Any]] = []
    for path in sorted((root / "signals").glob("*")) if (root / "signals").is_dir() else []:
        if path.is_dir():
            manifest, signal = validate_forward_signal(path)
            signals.append(
                {
                    "signal_id": signal.signal_id,
                    "strategy_id": signal.strategy_id,
                    "decision_time": signal.decision_time,
                    "recorded_at": manifest["recorded_at"],
                    "evaluated": False,
                }
            )
            evaluation_root = root / "evaluations" / signal.signal_id
            candidates = []
            if evaluation_root.is_dir():
                for evaluation_path in sorted(evaluation_root.iterdir()):
                    if evaluation_path.is_dir():
                        _, payload = validate_forward_evaluation(evaluation_path)
                        candidates.append(payload)
            if candidates:
                latest = max(candidates, key=lambda item: (item["coverage"], item["evaluated_at"]))
                if latest["signal_id"] != signal.signal_id:
                    raise RuntimeError("forward evaluation is linked to the wrong signal")
                evaluation_path = evaluation_root / latest["evaluation_id"]
                evaluation_manifest, _ = validate_forward_evaluation(evaluation_path)
                if evaluation_manifest.get("signal_manifest_hash") != manifest["manifest_hash"]:
                    raise RuntimeError("forward evaluation references a different signal manifest")
                signals[-1]["evaluated"] = True
                signals[-1]["evaluation_status"] = latest["status"]
                signals[-1]["evaluation_id"] = latest["evaluation_id"]
                latest_evaluations.append(latest)
    known_signal_ids = {item["signal_id"] for item in signals}
    evaluation_parent = root / "evaluations"
    if evaluation_parent.is_dir():
        orphan_ids = sorted(
            path.name
            for path in evaluation_parent.iterdir()
            if path.is_dir() and path.name not in known_signal_ids
        )
        if orphan_ids:
            raise RuntimeError(f"forward archive contains orphan evaluations: {', '.join(orphan_ids)}")
    complete = sum(item["status"] == "complete" for item in latest_evaluations)
    return {
        "schema_version": "forward_archive_summary.v1",
        "evidence_scope": EVIDENCE_SCOPE,
        "signal_count": len(signals),
        "evaluated_signal_count": len(latest_evaluations),
        "completely_evaluated_signal_count": complete,
        "unevaluated_signal_count": len(signals) - len(latest_evaluations),
        "signals": signals,
    }


def publish_current_signal_view(
    archive_root: Path | str,
    output: Path | str,
    as_of: str | datetime,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """Publish one currently valid, fully revalidated signal for a read-only API."""
    decision_at = _timestamp(as_of, "as_of")
    root = Path(archive_root).resolve()
    candidates: list[tuple[datetime, str, dict[str, Any], SignalBatch]] = []
    for path in sorted((root / "signals").glob("*")) if (root / "signals").is_dir() else []:
        if not path.is_dir():
            continue
        manifest, signal = validate_forward_signal(path)
        if strategy_id is not None and signal.strategy_id != strategy_id:
            continue
        decision = _timestamp(signal.decision_time, "decision_time")
        valid_until = _timestamp(signal.valid_until, "valid_until")
        if decision <= decision_at < valid_until:
            candidates.append((decision, signal.signal_id, manifest, signal))
    if not candidates:
        raise ValueError("CURRENT_SIGNAL_NOT_PUBLISHED")
    _, _, manifest, signal = max(candidates, key=lambda item: (item[0], item[1]))
    signal_view = {
        "schema_version": "dashboard_signal_payload.v1",
        "signal_id": signal.signal_id,
        "strategy_id": signal.strategy_id,
        "generator_kind": signal.generator_kind,
        "generator_version": signal.generator_version,
        "model_id": signal.model_id,
        "decision_time": signal.decision_time,
        "valid_until": signal.valid_until,
        "target_kind": signal.target_kind,
        "horizon": signal.horizon,
        "score_encoding": "canonical_decimal_string",
        "scores_decimal": {
            instrument_id: _decimal_text(score)
            for instrument_id, score in sorted(signal.scores.items())
        },
    }
    identity = {
        "schema_version": "dashboard_signal.v1",
        "kind": "signal_batch",
        "run_id": signal.signal_id,
        "generated_at": _utc_text(decision_at),
        "status": "valid",
        "reason_codes": [],
        "read_only": True,
        "evidence_scope": manifest["evidence_scope"],
        "data_snapshot_id": manifest["data_snapshot_id"],
        "data_as_of": manifest["data_as_of"],
        "recorded_at": manifest["recorded_at"],
        "signal": signal_view,
    }
    payload = {**identity, "content_sha256": canonical_hash(identity)}
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    handle, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return payload
