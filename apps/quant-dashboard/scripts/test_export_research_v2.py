import json
import tempfile
import unittest
from pathlib import Path

from export_research_v2 import export


class ExportResearchV2Test(unittest.TestCase):
    def test_complete_run_exports_separate_research_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            (run / "manifest.json").write_text(json.dumps({
                "run_id": "run-1",
                "status": "complete",
                "data_snapshot": {
                    "snapshot_id": "snapshot-1",
                    "as_of": "2026-09-09T00:00:00Z",
                    "quality": {"status": "valid"},
                },
            }))
            (run / "result.json").write_text(json.dumps({
                "status": "complete",
                "metrics": {"hold": {"total_return": 0.1}},
                "risk_snapshots": {"hold": {"quality": "valid"}},
            }))
            (run / "equity_curves.csv").write_text("date,strategy,nav\n2026-01-02,hold,10000\n")
            (run / "orders.csv").write_text("date,strategy,instrument,quantity\n2026-01-02,hold,QQQ,10\n")
            output = root / "research-v2.json"
            payload = export(run, output)
            self.assertEqual(payload["kind"], "research_report")
            self.assertEqual(payload["status"], "complete")
            self.assertNotIn("signal_batch", payload)
            self.assertEqual(payload["data_snapshot_id"], "snapshot-1")

    def test_incomplete_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"status": "failed"}))
            (root / "result.json").write_text(json.dumps({"status": "failed"}))
            with self.assertRaisesRegex(ValueError, "not complete"):
                export(root, root / "output.json")


if __name__ == "__main__":
    unittest.main()
