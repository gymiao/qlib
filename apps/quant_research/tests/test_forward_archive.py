from datetime import datetime, timezone
from contextlib import redirect_stdout
from dataclasses import asdict
from io import StringIO
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from apps.quant_research.contracts import FeatureBatch, LabelBatch, SignalBatch, canonical_hash
from apps.quant_research.forward_archive import (
    publish_forward_evaluation,
    publish_current_signal_view,
    publish_forward_signal,
    summarize_forward_archive,
    validate_forward_evaluation,
    validate_forward_signal,
)
from apps.quant_research.manage_forward_archive import main


NOW = datetime(2024, 1, 2, 21, 5, tzinfo=timezone.utc)


DATA_IDENTITY = {
    "schema_version": "production_data_readiness.v1",
    "requested_range": {"start": "2020-01-01T00:00:00+00:00", "end": "2024-01-03T00:00:00+00:00"},
    "thresholds": {"max_bar_delay_hours": 12.0},
    "tables": {
        name: {"sha256": character * 64, "rows": 1, "columns": [], "sources": ["vendor"]}
        for name, character in zip(
            ("instrument_master", "universe_membership", "bars", "corporate_actions"),
            "abcd",
        )
    },
    "capabilities": {"equity_history": "historical_validated"},
    "coverage": {"requested_members": 2, "members_with_bars": 2, "missing_member_bars": []},
    "issues": [],
}
DATA_SNAPSHOT_ID = canonical_hash(DATA_IDENTITY)[:20]
DATA_MANIFEST = {
    **DATA_IDENTITY,
    "audit_id": DATA_SNAPSHOT_ID,
    "status": "valid",
}
FEATURE_BATCH = FeatureBatch(
    feature_schema_id="feature-schema-1",
    data_snapshot_id=DATA_SNAPSHOT_ID,
    decision_time="2024-01-02T21:00:00Z",
    columns=("ret_5",),
    rows=(
        {"instrument_id": "inst-a", "ret_5": 0.2},
        {"instrument_id": "inst-b", "ret_5": 0.1},
    ),
)


def signal(signal_id="signal-1"):
    return SignalBatch(
        signal_id=signal_id,
        strategy_id="qqq-long-only",
        generator_kind="model",
        generator_version="model-code-v1",
        model_id="model-1",
        decision_time="2024-01-02T21:00:00Z",
        valid_until="2024-01-03T14:30:00Z",
        target_kind="relative_return_score",
        horizon=5,
        scores={"inst-a": 0.8, "inst-b": 0.2},
    )


def labels(*instrument_ids):
    return LabelBatch(
        data_snapshot_id="labels-snapshot-1",
        rows=tuple(
            {
                "instrument_id": instrument_id,
                "label_start": "2024-01-03T14:30:00Z",
                "label_end": "2024-01-10T14:30:00Z",
                "label_available_at": "2024-01-10T21:05:00Z",
                "value": 0.03 if instrument_id == "inst-a" else -0.01,
            }
            for instrument_id in instrument_ids
        ),
    )


class ForwardArchiveTest(unittest.TestCase):
    def test_v2_readiness_manifest_can_anchor_forward_signal(self):
        identity = json.loads(json.dumps(DATA_IDENTITY))
        identity["schema_version"] = "production_data_readiness.v2"
        identity["capabilities"]["option_lifecycle"] = "not_provided"
        snapshot_id = canonical_hash(identity)[:20]
        manifest = {**identity, "audit_id": snapshot_id, "status": "valid"}
        features = FeatureBatch(
            feature_schema_id="feature-schema-1",
            data_snapshot_id=snapshot_id,
            decision_time="2024-01-02T21:00:00Z",
            columns=("ret_5",),
            rows=FEATURE_BATCH.rows,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            path = publish_forward_signal(
                signal(), directory, snapshot_id, "2024-01-02T20:55:00Z", manifest, features
            )
            archived, _ = validate_forward_signal(path)
            self.assertEqual(archived["data_snapshot_id"], snapshot_id)

    def test_current_signal_view_revalidates_archive_and_validity_window(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            root = Path(directory)
            publish_forward_signal(
                signal(), root, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
            )
            output = root / "current.json"
            payload = publish_current_signal_view(
                root,
                output,
                "2024-01-02T21:06:00Z",
                "qqq-long-only",
            )
            identity = {key: value for key, value in payload.items() if key != "content_sha256"}
            self.assertEqual(payload["content_sha256"], canonical_hash(identity))
            self.assertEqual(payload["signal"]["signal_id"], "signal-1")
            self.assertTrue(payload["read_only"])
            with self.assertRaisesRegex(ValueError, "CURRENT_SIGNAL_NOT_PUBLISHED"):
                publish_current_signal_view(root, output, "2024-01-04T00:00:00Z")

    def test_signal_is_immutable_idempotent_and_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            root = Path(directory)
            first = publish_forward_signal(
                signal(), root, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
            )
            second = publish_forward_signal(
                signal(), root, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
            )
            self.assertEqual(first, second)
            manifest, archived = validate_forward_signal(first)
            self.assertEqual(archived.signal_id, "signal-1")
            self.assertEqual(
                manifest["evidence_scope"],
                "local_content_addressed_not_third_party_timestamped",
            )
            path = first / "signal.json"
            payload = json.loads(path.read_text())
            payload["scores"]["inst-a"] = 9
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(RuntimeError, "artifact hash mismatch"):
                validate_forward_signal(first)

    def test_rejects_backfilled_or_future_data_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "apps.quant_research.forward_archive._utc_now",
                return_value=datetime(2024, 1, 3, 15, tzinfo=timezone.utc),
            ):
                with self.assertRaisesRegex(ValueError, "live validity window"):
                    publish_forward_signal(
                        signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                    )
            with patch("apps.quant_research.forward_archive._utc_now", return_value=NOW):
                with self.assertRaisesRegex(ValueError, "data_as_of"):
                    publish_forward_signal(
                        signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T21:01:00Z", DATA_MANIFEST, FEATURE_BATCH
                    )

    def test_rejects_unverified_data_manifest(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            invalid = {**DATA_MANIFEST, "status": "invalid"}
            with self.assertRaisesRegex(ValueError, "readiness manifest"):
                publish_forward_signal(
                    signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", invalid, FEATURE_BATCH
                )

    def test_rejects_feature_batch_that_does_not_match_signal(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            mismatched = FeatureBatch(
                feature_schema_id="feature-schema-1",
                data_snapshot_id=DATA_SNAPSHOT_ID,
                decision_time="2024-01-02T21:00:00Z",
                columns=("ret_5",),
                rows=({"instrument_id": "different", "ret_5": 0.2},),
            )
            with self.assertRaisesRegex(ValueError, "score targets"):
                publish_forward_signal(
                    signal(),
                    directory,
                    DATA_SNAPSHOT_ID,
                    "2024-01-02T20:55:00Z",
                    DATA_MANIFEST,
                    mismatched,
                )

    def test_expired_retry_can_verify_existing_signal_but_cannot_change_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("apps.quant_research.forward_archive._utc_now", return_value=NOW):
                first = publish_forward_signal(
                    signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                )
            with patch(
                "apps.quant_research.forward_archive._utc_now",
                return_value=datetime(2024, 1, 3, 15, tzinfo=timezone.utc),
            ):
                second = publish_forward_signal(
                    signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                )
                self.assertEqual(first, second)
                changed_manifest = json.loads(json.dumps(DATA_MANIFEST))
                changed_manifest["tables"]["bars"]["path"] = "/different/local/path.csv"
                with self.assertRaisesRegex(RuntimeError, "different data manifest"):
                    publish_forward_signal(
                        signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", changed_manifest, FEATURE_BATCH
                    )

    def test_signal_id_cannot_escape_archive(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                publish_forward_signal(
                    signal("../escape"), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                )

    def test_partial_then_complete_evaluation_is_append_only(self):
        evaluation_time = datetime(2024, 1, 10, 21, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with patch("apps.quant_research.forward_archive._utc_now", return_value=NOW):
                publish_forward_signal(
                    signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                )
            with patch("apps.quant_research.forward_archive._utc_now", return_value=evaluation_time):
                partial = publish_forward_evaluation(directory, "signal-1", labels("inst-a"))
                complete = publish_forward_evaluation(directory, "signal-1", labels("inst-a", "inst-b"))
                self.assertNotEqual(partial, complete)
                summary = summarize_forward_archive(directory)
                self.assertEqual(summary["signal_count"], 1)
                self.assertEqual(summary["evaluated_signal_count"], 1)
                self.assertEqual(summary["completely_evaluated_signal_count"], 1)
                self.assertEqual(len(list((Path(directory) / "evaluations" / "signal-1").iterdir())), 2)

                copied = Path(directory) / "evaluations" / "wrong-signal" / complete.name
                copied.parent.mkdir(parents=True)
                shutil.copytree(complete, copied)
                with self.assertRaisesRegex(RuntimeError, "orphan evaluations"):
                    summarize_forward_archive(directory)

    def test_evaluation_rejects_label_before_it_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("apps.quant_research.forward_archive._utc_now", return_value=NOW):
                publish_forward_signal(
                    signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                )
            with patch(
                "apps.quant_research.forward_archive._utc_now",
                return_value=datetime(2024, 1, 10, 20, tzinfo=timezone.utc),
            ):
                with self.assertRaisesRegex(ValueError, "unavailable label"):
                    publish_forward_evaluation(directory, "signal-1", labels("inst-a"))

    def test_evaluation_validator_recomputes_derived_metrics(self):
        evaluation_time = datetime(2024, 1, 10, 21, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            with patch("apps.quant_research.forward_archive._utc_now", return_value=NOW):
                publish_forward_signal(
                    signal(), directory, DATA_SNAPSHOT_ID, "2024-01-02T20:55:00Z", DATA_MANIFEST, FEATURE_BATCH
                )
            with patch("apps.quant_research.forward_archive._utc_now", return_value=evaluation_time):
                evaluation = publish_forward_evaluation(
                    directory,
                    "signal-1",
                    labels("inst-a", "inst-b"),
                )
            evaluation_file = evaluation / "evaluation.json"
            payload = json.loads(evaluation_file.read_text())
            payload["coverage"] = 0.5
            evaluation_file.write_text(
                json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            manifest_file = evaluation / "manifest.json"
            manifest = json.loads(manifest_file.read_text())
            manifest["evaluation_payload_hash"] = canonical_hash(payload)
            content = evaluation_file.read_bytes()
            manifest["files"]["evaluation.json"] = {
                "sha256": sha256(content).hexdigest(),
                "bytes": len(content),
            }
            manifest.pop("manifest_hash")
            manifest["manifest_hash"] = canonical_hash(manifest)
            manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "derived field mismatch"):
                validate_forward_evaluation(evaluation)

    def test_signal_contract_rejects_nonfinite_empty_and_invalid_time(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            SignalBatch(
                "bad", "strategy", "model", "1", "2024-01-02T21:00:00Z",
                "2024-01-03T21:00:00Z", "weights", model_id="model-1", scores={}
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            SignalBatch(
                "bad", "strategy", "rule", "1", "2024-01-02T21:00:00Z",
                "2024-01-03T21:00:00Z", "weights", scores={"QQQ": float("nan")}
            )
        with self.assertRaisesRegex(ValueError, "after"):
            SignalBatch(
                "bad", "strategy", "rule", "1", "2024-01-03T21:00:00Z",
                "2024-01-02T21:00:00Z", "weights", scores={"QQQ": 1.0}
            )

    def test_cli_publishes_and_inspects_archive(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.quant_research.forward_archive._utc_now", return_value=NOW
        ):
            root = Path(directory)
            signal_file = root / "signal-input.json"
            signal_file.write_text(json.dumps(asdict(signal())), encoding="utf-8")
            data_manifest_file = root / "data-manifest.json"
            data_manifest_file.write_text(json.dumps(DATA_MANIFEST), encoding="utf-8")
            feature_batch_file = root / "feature-batch.json"
            feature_batch_file.write_text(json.dumps(asdict(FEATURE_BATCH)), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "publish",
                            "--archive-root", str(root / "archive"),
                            "--signal-file", str(signal_file),
                            "--data-snapshot-id", DATA_SNAPSHOT_ID,
                            "--data-as-of", "2024-01-02T20:55:00Z",
                            "--data-manifest", str(data_manifest_file),
                            "--feature-batch", str(feature_batch_file),
                        ]
                    ),
                    0,
                )
            published = json.loads(output.getvalue())
            self.assertEqual(published["manifest"]["signal_id"], "signal-1")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(["inspect", "--archive-root", str(root / "archive")]),
                    0,
                )
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["signal_count"], 1)


if __name__ == "__main__":
    unittest.main()
