from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_fill_corrections import (
    attach_broker_fill_correction_batch_id,
    broker_fill_correction_batch_from_payload,
    import_broker_fill_correction_batch,
)
from apps.quant_research.broker_fills import broker_fill_batch_from_payload, import_broker_fill_batch
from apps.quant_research.broker_orders import broker_order_package_payload
from apps.quant_research.import_broker_fill_corrections import main
from apps.quant_research.ledger import LedgerEvent
from apps.quant_research.simulator import PaperSimulator
from apps.quant_research.tests.test_broker_fills import AS_OF, fill_payload, setup_order_package


CORRECTION_AS_OF = datetime(2024, 1, 2, 21, 30, tzinfo=timezone.utc)


def correction_payload(package, *, correction_id="correction-1", execution_id="exec-1"):
    payload = {
        "schema_version": "broker_fill_correction_batch.v1",
        "package_id": package.package_id,
        "account_id": package.account_id,
        "broker_id": package.broker_id,
        "captured_at": "2024-01-02T21:24:00Z",
        "available_at": "2024-01-02T21:25:00Z",
        "corrections": [{
            "broker_correction_id": correction_id,
            "broker_execution_id": execution_id,
            "reason_code": "BROKER_BUSTED_EXECUTION",
            "effective_at": "2024-01-02T21:22:00Z",
            "available_at": "2024-01-02T21:23:00Z",
        }],
        "source": {
            "source_kind": "broker_observed",
            "export_id": f"correction-export-{correction_id}",
            "content_sha256": "d" * 64,
        },
    }
    return attach_broker_fill_correction_batch_id(payload)


def filled_account(quantity=-2):
    package, ledger = setup_order_package(quantity=quantity)
    fill = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
    import_broker_fill_batch(fill, package, ledger)
    return package, ledger


class BrokerFillCorrectionTest(unittest.TestCase):
    def test_sell_fill_reversal_restores_position_and_receivable(self):
        package, ledger = filled_account()
        batch = broker_fill_correction_batch_from_payload(
            correction_payload(package), package, CORRECTION_AS_OF,
        )
        result = import_broker_fill_correction_batch(batch, package, ledger)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.fill_status, "unfilled")
        self.assertEqual(result.orders_submitted_by_system, 0)
        self.assertEqual(ledger.state.positions["inst-qqq"], Decimal("12"))
        self.assertEqual(ledger.state.trade_receivable, Decimal("20"))

    def test_buy_fill_reversal_restores_payable(self):
        package, ledger = filled_account(quantity=2)
        batch = broker_fill_correction_batch_from_payload(
            correction_payload(package), package, CORRECTION_AS_OF,
        )
        import_broker_fill_correction_batch(batch, package, ledger)
        self.assertEqual(ledger.state.positions["inst-qqq"], Decimal("12"))
        self.assertEqual(ledger.state.trade_payable, Decimal("10"))

    def test_duplicate_is_idempotent_and_second_correction_is_rejected(self):
        package, ledger = filled_account()
        batch = broker_fill_correction_batch_from_payload(
            correction_payload(package), package, CORRECTION_AS_OF,
        )
        import_broker_fill_correction_batch(batch, package, ledger)
        duplicate = import_broker_fill_correction_batch(batch, package, ledger)
        self.assertEqual(duplicate.status, "duplicate")
        alternative = broker_fill_correction_batch_from_payload(
            correction_payload(package, correction_id="correction-2"), package, CORRECTION_AS_OF,
        )
        with self.assertRaisesRegex(ValueError, "already reversed"):
            import_broker_fill_correction_batch(alternative, package, ledger)

    def test_settled_fill_cannot_be_reversed(self):
        package, ledger = filled_account()
        ledger.apply(LedgerEvent(
            "settle-before-correction", "2024-01-02T21:21:00Z",
            "settle_trade_receivable", {"amount": "819"},
        ))
        batch = broker_fill_correction_batch_from_payload(
            correction_payload(package), package, CORRECTION_AS_OF,
        )
        count = len(ledger.events)
        with self.assertRaisesRegex(ValueError, "may have settled"):
            import_broker_fill_correction_batch(batch, package, ledger)
        self.assertEqual(len(ledger.events), count)

    def test_replacement_execution_can_fill_the_reopened_quantity(self):
        package, ledger = filled_account()
        correction = broker_fill_correction_batch_from_payload(
            correction_payload(package), package, CORRECTION_AS_OF,
        )
        import_broker_fill_correction_batch(correction, package, ledger)
        replacement_payload = fill_payload(
            package,
            execution_id="exec-replacement",
            executed_at="2024-01-02T21:13:00Z",
            row_available_at="2024-01-02T21:26:00Z",
            captured_at="2024-01-02T21:27:00Z",
            batch_available_at="2024-01-02T21:28:00Z",
        )
        replacement = broker_fill_batch_from_payload(
            replacement_payload, package, CORRECTION_AS_OF,
        )
        result = import_broker_fill_batch(replacement, package, ledger)
        self.assertEqual(result.status, "filled")
        self.assertEqual(ledger.state.positions["inst-qqq"], Decimal("10"))

    def test_cli_persists_reversal_once(self):
        package, ledger = filled_account()
        payload = correction_payload(package)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_file = root / "package.json"
            correction_file = root / "corrections.json"
            state_file = root / "simulator.json"
            package_file.write_text(json.dumps(broker_order_package_payload(package)), encoding="utf-8")
            correction_file.write_text(json.dumps(payload), encoding="utf-8")
            PaperSimulator(ledger).save(state_file)
            args = [
                "--package", str(package_file), "--corrections", str(correction_file),
                "--simulator-state", str(state_file), "--as-of", "2024-01-02T21:30:00Z",
            ]
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["fill_status"], "unfilled")
            with redirect_stdout(StringIO()) as retry:
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(retry.getvalue())["status"], "duplicate")
            restored = PaperSimulator.load(state_file)
            self.assertEqual(restored.ledger.state.positions["inst-qqq"], Decimal("12"))


if __name__ == "__main__":
    unittest.main()
