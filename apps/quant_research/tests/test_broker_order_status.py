from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_order_status import (
    attach_broker_order_status_batch_id,
    broker_order_status_batch_from_payload,
    import_broker_order_status_batch,
)
from apps.quant_research.import_broker_order_status import main
from apps.quant_research.broker_orders import broker_order_package_payload
from apps.quant_research.simulator import PaperSimulator
from apps.quant_research.tests.test_broker_fills import AS_OF, setup_order_package


def status_batch_payload(
    package,
    status,
    *,
    status_id,
    effective_at,
    reason_code=None,
    broker_order_id="broker-order-1",
):
    identity = {
        "schema_version": "broker_order_status_batch.v1",
        "package_id": package.package_id,
        "account_id": package.account_id,
        "broker_id": package.broker_id,
        "captured_at": "2024-01-02T21:18:00Z",
        "available_at": "2024-01-02T21:19:00Z",
        "statuses": [
            {
                "broker_status_id": status_id,
                "client_order_id": package.orders[0]["client_order_id"],
                "broker_order_id": broker_order_id,
                "status": status,
                "reason_code": reason_code,
                "effective_at": effective_at,
                "available_at": effective_at,
            }
        ],
        "source": {
            "source_kind": "broker_observed",
            "export_id": f"export-{status_id}",
            "content_sha256": "e" * 64,
        },
    }
    return attach_broker_order_status_batch_id(identity)


class BrokerOrderStatusTest(unittest.TestCase):
    def test_accept_then_cancel_is_recorded_without_financial_change(self):
        package, ledger = setup_order_package(accepted=False)
        before = ledger.basis_hash()
        accepted = broker_order_status_batch_from_payload(
            status_batch_payload(
                package,
                "accepted",
                status_id="accepted-1",
                effective_at="2024-01-02T21:11:00Z",
            ),
            package,
            AS_OF,
        )
        first = import_broker_order_status_batch(accepted, package, ledger)
        self.assertEqual(first.status, "accepted")
        self.assertEqual(first.orders_submitted_by_system, 0)
        self.assertEqual(ledger.basis_hash(), before)

        cancelled = broker_order_status_batch_from_payload(
            status_batch_payload(
                package,
                "cancelled",
                status_id="cancelled-1",
                effective_at="2024-01-02T21:17:00Z",
                reason_code="USER_CANCELLED",
            ),
            package,
            AS_OF,
        )
        second = import_broker_order_status_batch(cancelled, package, ledger)
        self.assertEqual(second.status, "cancelled")
        duplicate = import_broker_order_status_batch(cancelled, package, ledger)
        self.assertEqual(duplicate.applied_event_ids, ())
        self.assertEqual(len(duplicate.duplicate_event_ids), 1)

    def test_invalid_transition_broker_id_and_reason_are_rejected_atomically(self):
        package, ledger = setup_order_package(accepted=False)
        cancelled_payload = status_batch_payload(
            package,
            "cancelled",
            status_id="cancelled-1",
            effective_at="2024-01-02T21:17:00Z",
            reason_code="USER_CANCELLED",
        )
        cancelled = broker_order_status_batch_from_payload(cancelled_payload, package, AS_OF)
        with self.assertRaisesRegex(ValueError, "transition"):
            import_broker_order_status_batch(cancelled, package, ledger)
        self.assertEqual(len(ledger.events), package.account_sequence)

        bad_reason = status_batch_payload(
            package,
            "rejected",
            status_id="rejected-1",
            effective_at="2024-01-02T21:11:00Z",
            reason_code="BROKER_REJECTED",
        )
        bad_reason["statuses"][0]["reason_code"] = None
        bad_reason = attach_broker_order_status_batch_id(
            {key: value for key, value in bad_reason.items() if key != "batch_id"}
        )
        with self.assertRaisesRegex(ValueError, "reason"):
            broker_order_status_batch_from_payload(bad_reason, package, AS_OF)

        accepted = broker_order_status_batch_from_payload(
            status_batch_payload(
                package,
                "accepted",
                status_id="accepted-1",
                effective_at="2024-01-02T21:11:00Z",
            ),
            package,
            AS_OF,
        )
        import_broker_order_status_batch(accepted, package, ledger)
        changed_id = broker_order_status_batch_from_payload(
            status_batch_payload(
                package,
                "expired",
                status_id="expired-1",
                effective_at="2024-01-02T21:17:00Z",
                broker_order_id="different-order",
            ),
            package,
            AS_OF,
        )
        with self.assertRaisesRegex(ValueError, "broker_order_id changed"):
            import_broker_order_status_batch(changed_id, package, ledger)

    def test_cli_persists_status_and_is_idempotent(self):
        package, ledger = setup_order_package(accepted=False)
        payload = status_batch_payload(
            package,
            "accepted",
            status_id="accepted-cli",
            effective_at="2024-01-02T21:11:00Z",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_file = root / "package.json"
            status_file = root / "statuses.json"
            state_file = root / "simulator.json"
            package_file.write_text(json.dumps(broker_order_package_payload(package)), encoding="utf-8")
            status_file.write_text(json.dumps(payload), encoding="utf-8")
            PaperSimulator(ledger).save(state_file)
            args = [
                "--package", str(package_file),
                "--statuses", str(status_file),
                "--simulator-state", str(state_file),
                "--as-of", datetime(2024, 1, 2, 21, 20, tzinfo=timezone.utc).isoformat(),
            ]
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "accepted")
            with redirect_stdout(StringIO()) as retry:
                self.assertEqual(main(args), 0)
            self.assertEqual(len(json.loads(retry.getvalue())["duplicate_event_ids"]), 1)
            restored = PaperSimulator.load(state_file)
            self.assertEqual(restored.ledger.events[-1].kind, "broker_order_status")


if __name__ == "__main__":
    unittest.main()
