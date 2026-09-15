from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_fills import broker_fill_batch_from_payload, import_broker_fill_batch
from apps.quant_research.contracts import canonical_hash
from apps.quant_research.operations_snapshot import main, publish_operations_snapshot
from apps.quant_research.broker_orders import broker_order_package_payload
from apps.quant_research.simulator import PaperSimulator
from apps.quant_research.tests.test_broker_fills import AS_OF, fill_payload, setup_order_package


class OperationsSnapshotTest(unittest.TestCase):
    def test_snapshot_is_redacted_exact_and_content_addressed(self):
        package, ledger = setup_order_package()
        batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        import_broker_fill_batch(batch, package, ledger)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "simulator.json"
            output = root / "operations.json"
            PaperSimulator(ledger).save(state_file)
            payload = publish_operations_snapshot(
                state_file,
                output,
                datetime(2024, 1, 2, 21, 20, tzinfo=timezone.utc),
                (),
            )
            identity = {key: value for key, value in payload.items() if key != "content_sha256"}
            self.assertEqual(payload["content_sha256"], canonical_hash(identity))
            self.assertEqual(payload["decimal_encoding"], "canonical_string")
            self.assertEqual(payload["account"]["cash"]["trade_receivable"], "839")
            self.assertNotIn("demo-account-redacted", output.read_text(encoding="utf-8"))

    def test_cli_includes_validated_order_lifecycle_without_raw_broker_id(self):
        package, ledger = setup_order_package()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "simulator.json"
            package_file = root / "package.json"
            output = root / "operations.json"
            PaperSimulator(ledger).save(state_file)
            package_file.write_text(json.dumps(broker_order_package_payload(package)), encoding="utf-8")
            with redirect_stdout(StringIO()) as summary:
                self.assertEqual(main([
                    "--simulator-state", str(state_file),
                    "--generated-at", "2024-01-02T21:20:00Z",
                    "--package", str(package_file),
                    "--output", str(output),
                ]), 0)
            self.assertEqual(json.loads(summary.getvalue())["status"], "valid")
            payload = json.loads(output.read_text(encoding="utf-8"))
            order = payload["order_packages"][0]["orders"][0]
            self.assertEqual(order["lifecycle_status"], "accepted")
            self.assertEqual(order["fill_status"], "unfilled")
            self.assertEqual(len(order["broker_order_ref"]), 12)
            self.assertNotIn("broker-order-1", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
