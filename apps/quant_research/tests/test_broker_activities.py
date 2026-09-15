from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_activities import (
    attach_broker_activity_batch_id,
    broker_activity_batch_from_payload,
    import_broker_activity_batch,
)
from apps.quant_research.broker_fills import broker_fill_batch_from_payload, import_broker_fill_batch
from apps.quant_research.broker_account import (
    account_snapshot_from_payload,
    ledger_from_broker_snapshot,
    reconcile_broker_snapshot,
)
from apps.quant_research.contracts import canonical_hash
from apps.quant_research.import_broker_activities import main
from apps.quant_research.simulator import ConcurrentStateError, PaperSimulator
from apps.quant_research.tests.test_broker_account import AS_OF, snapshot_payload
from apps.quant_research.tests.test_broker_fills import fill_payload, setup_order_package


ACTIVITY_AS_OF = datetime(2024, 1, 2, 21, 25, tzinfo=timezone.utc)


def setup_ledger(source_kind="broker_observed"):
    snapshot = account_snapshot_from_payload(snapshot_payload(source_kind), AS_OF)
    return ledger_from_broker_snapshot(snapshot)


def activity_row(activity_id, activity_type, amount, *, reference_id="reference-1"):
    return {
        "broker_activity_id": activity_id,
        "activity_type": activity_type,
        "amount": float(amount),
        "currency": "USD",
        "reference_id": reference_id,
        "effective_at": "2024-01-02T21:18:00Z",
        "available_at": "2024-01-02T21:20:00Z",
    }


def activity_payload(ledger, activities, *, source_kind="broker_observed", export_id="activity-export-1"):
    account_import = next(event for event in ledger.events if event.kind == "account_snapshot_import")
    identity = {
        "schema_version": "broker_activity_batch.v1",
        "account_id": account_import.payload["account_id"],
        "broker_id": account_import.payload["broker_id"],
        "captured_at": "2024-01-02T21:21:00Z",
        "available_at": "2024-01-02T21:22:00Z",
        "activities": activities,
        "source": {
            "source_kind": source_kind,
            "export_id": export_id,
            "content_sha256": "d" * 64,
        },
    }
    return attach_broker_activity_batch_id(identity)


def ledger_with_sell_fill():
    package, ledger = setup_order_package()
    batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF + (ACTIVITY_AS_OF - AS_OF))
    import_broker_fill_batch(batch, package, ledger)
    return ledger


class BrokerActivityImportTest(unittest.TestCase):
    def test_trade_receivable_settlement_moves_only_the_observed_amount(self):
        ledger = ledger_with_sell_fill()
        payload = activity_payload(
            ledger,
            [activity_row("settle-receivable-1", "trade_receivable_settlement", 819)],
        )
        batch = broker_activity_batch_from_payload(payload, ledger, ACTIVITY_AS_OF)
        result = import_broker_activity_batch(batch, ledger)
        self.assertEqual(result.import_capability, "broker_activity_contract_validated")
        self.assertEqual(result.orders_submitted_by_system, 0)
        self.assertEqual(ledger.state.trade_receivable, Decimal("20"))
        self.assertEqual(ledger.state.settled_cash, Decimal("10819"))
        later = snapshot_payload()
        later.pop("snapshot_id")
        later["captured_at"] = "2024-01-02T21:23:00Z"
        later["available_at"] = "2024-01-02T21:24:00Z"
        later["balances"]["settled_cash"] = 10819.0
        later["balances"]["trade_receivable"] = 20.0
        later["positions"][0]["quantity"] = 10.0
        later["source"]["export_id"] = "post-settlement"
        later["source"]["content_sha256"] = "e" * 64
        later["snapshot_id"] = canonical_hash(later)[:24]
        observed = account_snapshot_from_payload(later, ACTIVITY_AS_OF)
        self.assertEqual(reconcile_broker_snapshot(observed, ledger).status, "matched")

    def test_trade_payable_settlement_reduces_settled_cash(self):
        package, ledger = setup_order_package(quantity=2)
        fill = broker_fill_batch_from_payload(fill_payload(package), package, ACTIVITY_AS_OF)
        import_broker_fill_batch(fill, package, ledger)
        payload = activity_payload(
            ledger,
            [activity_row("settle-payable-1", "trade_payable_settlement", 821)],
        )
        batch = broker_activity_batch_from_payload(payload, ledger, ACTIVITY_AS_OF)
        import_broker_activity_batch(batch, ledger)
        self.assertEqual(ledger.state.trade_payable, Decimal("10"))
        self.assertEqual(ledger.state.settled_cash, Decimal("9179"))

    def test_cash_and_dividend_activities_use_deterministic_dependency_order(self):
        ledger = setup_ledger()
        rows = [
            activity_row("withdraw", "cash_withdrawal", 25),
            activity_row("dividend-pay", "dividend_payment", 10),
            activity_row("dividend-due", "dividend_entitlement", 10),
            activity_row("deposit", "cash_deposit", 100),
        ]
        batch = broker_activity_batch_from_payload(activity_payload(ledger, rows), ledger, ACTIVITY_AS_OF)
        result = import_broker_activity_batch(batch, ledger)
        self.assertEqual(result.status, "applied")
        self.assertEqual(ledger.state.dividend_receivable, Decimal("5"))
        self.assertEqual(ledger.state.settled_cash, Decimal("10085"))

    def test_v2_decimal_activity_preserves_exact_cash_amount(self):
        ledger = setup_ledger()
        payload = activity_payload(ledger, [activity_row("deposit-v2", "cash_deposit", 1)])
        row = payload["activities"][0]
        row["amount_decimal"] = "0.123456789012345678"
        row.pop("amount")
        payload["schema_version"] = "broker_activity_batch.v2"
        payload = attach_broker_activity_batch_id(
            {key: value for key, value in payload.items() if key != "batch_id"}
        )
        batch = broker_activity_batch_from_payload(payload, ledger, ACTIVITY_AS_OF)
        self.assertEqual(batch.schema_version, "broker_activity_batch.v2")
        import_broker_activity_batch(batch, ledger)
        self.assertEqual(ledger.state.settled_cash, Decimal("10000.123456789012345678"))

        noncanonical = json.loads(json.dumps(payload))
        noncanonical["activities"][0]["amount_decimal"] += "0"
        noncanonical = attach_broker_activity_batch_id(
            {key: value for key, value in noncanonical.items() if key != "batch_id"}
        )
        with self.assertRaisesRegex(ValueError, "canonical"):
            broker_activity_batch_from_payload(noncanonical, ledger, ACTIVITY_AS_OF)

    def test_overlapping_export_is_idempotent_and_changed_content_collides(self):
        ledger = setup_ledger()
        row = activity_row("deposit-1", "cash_deposit", 100)
        first = broker_activity_batch_from_payload(activity_payload(ledger, [row]), ledger, ACTIVITY_AS_OF)
        import_broker_activity_batch(first, ledger)
        overlap_payload = activity_payload(ledger, [row], export_id="activity-export-2")
        overlap = broker_activity_batch_from_payload(overlap_payload, ledger, ACTIVITY_AS_OF)
        result = import_broker_activity_batch(overlap, ledger)
        self.assertEqual(result.status, "duplicate")
        self.assertEqual(ledger.state.settled_cash, Decimal("10100"))
        changed = activity_payload(ledger, [activity_row("deposit-1", "cash_deposit", 101)])
        changed_batch = broker_activity_batch_from_payload(changed, ledger, ACTIVITY_AS_OF)
        with self.assertRaisesRegex(ValueError, "reused with different content"):
            import_broker_activity_batch(changed_batch, ledger)
        self.assertEqual(ledger.state.settled_cash, Decimal("10100"))

    def test_invalid_type_currency_time_and_mixed_evidence_are_rejected_or_downgraded(self):
        ledger = setup_ledger()
        cases = (
            ("activity_type", "interest", "type"),
            ("currency", "EUR", "currency"),
            ("amount", 0, "positive"),
            ("effective_at", "2024-01-02T20:59:00Z", "after the account snapshot"),
            ("available_at", "2024-01-02T21:23:00Z", "before batch capture"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                row = activity_row("bad", "cash_deposit", 1)
                row[field] = value
                payload = activity_payload(ledger, [row])
                with self.assertRaisesRegex(ValueError, message):
                    broker_activity_batch_from_payload(payload, ledger, ACTIVITY_AS_OF)
        synthetic_ledger = setup_ledger("synthetic")
        payload = activity_payload(synthetic_ledger, [activity_row("deposit", "cash_deposit", 1)])
        batch = broker_activity_batch_from_payload(payload, synthetic_ledger, ACTIVITY_AS_OF)
        self.assertEqual(batch.import_capability, "simulation_only")

    def test_multirow_failure_leaves_the_real_ledger_unchanged(self):
        ledger = ledger_with_sell_fill()
        before = ledger.snapshot()
        rows = [
            activity_row("settle-a", "trade_receivable_settlement", 500),
            activity_row("settle-b", "trade_receivable_settlement", 500),
        ]
        batch = broker_activity_batch_from_payload(activity_payload(ledger, rows), ledger, ACTIVITY_AS_OF)
        with self.assertRaisesRegex(ValueError, "invalid trade receivable"):
            import_broker_activity_batch(batch, ledger)
        self.assertEqual(ledger.snapshot(), before)

    def test_cli_persists_and_retries_without_duplicate_cash(self):
        ledger = setup_ledger()
        payload = activity_payload(ledger, [activity_row("deposit-cli", "cash_deposit", 100)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "simulator.json"
            activity_file = root / "activities.json"
            PaperSimulator(ledger).save(state_file)
            activity_file.write_text(json.dumps(payload), encoding="utf-8")
            args = [
                "--activities", str(activity_file),
                "--simulator-state", str(state_file),
                "--as-of", "2024-01-02T21:25:00Z",
            ]
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["orders_submitted_by_system"], 0)
            with redirect_stdout(StringIO()) as retry:
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(retry.getvalue())["status"], "duplicate")
            restored = PaperSimulator.load(state_file)
            self.assertEqual(restored.ledger.state.settled_cash, Decimal("10100"))

    def test_cli_expected_state_hash_rejects_stale_import(self):
        ledger = setup_ledger()
        payload = activity_payload(ledger, [activity_row("deposit-cli", "cash_deposit", 100)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "simulator.json"
            activity_file = root / "activities.json"
            PaperSimulator(ledger).save(state_file)
            activity_file.write_text(json.dumps(payload), encoding="utf-8")
            args = [
                "--activities", str(activity_file),
                "--simulator-state", str(state_file),
                "--as-of", "2024-01-02T21:25:00Z",
                "--expected-content-sha256", "0" * 64,
            ]
            with self.assertRaisesRegex(ConcurrentStateError, "expected version"):
                main(args)
            restored = PaperSimulator.load(state_file)
            self.assertEqual(restored.ledger.state.settled_cash, Decimal("10000"))


if __name__ == "__main__":
    unittest.main()
