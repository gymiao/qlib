from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_account import (
    account_snapshot_from_payload,
    attach_broker_account_snapshot_id,
    ledger_from_broker_snapshot,
    reconcile_broker_snapshot,
)
from apps.quant_research.contracts import canonical_hash
from apps.quant_research.ledger import LedgerEvent
from apps.quant_research.reconcile_broker_account import main
from apps.quant_research.simulator import PaperSimulator


AS_OF = datetime(2024, 1, 2, 21, 10, tzinfo=timezone.utc)


def snapshot_payload(source_kind="broker_observed"):
    payload = {
        "schema_version": "broker_account_snapshot.v1",
        "account_id": "account-redacted-1",
        "broker_id": "broker-adapter-1",
        "account_type": "cash",
        "base_currency": "USD",
        "captured_at": "2024-01-02T21:00:00Z",
        "available_at": "2024-01-02T21:05:00Z",
        "balances": {
            "settled_cash": 10000.0,
            "trade_receivable": 20.0,
            "dividend_receivable": 5.0,
            "trade_payable": 10.0,
            "other_payable": 0.0,
        },
        "positions": [
            {
                "instrument_id": "inst-qqq",
                "symbol": "QQQ",
                "asset_type": "etf",
                "quantity": 12.0,
                "multiplier": 1.0,
            }
        ],
        "source": {
            "source_kind": source_kind,
            "export_id": "export-1",
            "content_sha256": "a" * 64,
        },
    }
    payload["snapshot_id"] = canonical_hash(payload)[:24]
    return payload


class BrokerAccountTest(unittest.TestCase):
    def test_v2_decimal_snapshot_preserves_exact_balances_and_positions(self):
        payload = snapshot_payload()
        payload.pop("snapshot_id")
        payload["schema_version"] = "broker_account_snapshot.v2"
        payload["balances"] = {
            "settled_cash": "10000.123456789012345678",
            "trade_receivable": "20",
            "dividend_receivable": "5",
            "trade_payable": "10",
            "other_payable": "0",
        }
        for row in payload["positions"]:
            row["quantity_decimal"] = str(int(row.pop("quantity")))
            row["multiplier_decimal"] = str(int(row.pop("multiplier")))
        payload = attach_broker_account_snapshot_id(payload)
        snapshot = account_snapshot_from_payload(payload, AS_OF)
        self.assertEqual(snapshot.schema_version, "broker_account_snapshot.v2")
        ledger = ledger_from_broker_snapshot(snapshot)
        self.assertEqual(ledger.state.settled_cash, Decimal("10000.123456789012345678"))
        self.assertEqual(ledger.state.positions["inst-qqq"], Decimal("12"))

        noncanonical = json.loads(json.dumps(payload))
        noncanonical["balances"]["settled_cash"] += "0"
        noncanonical["snapshot_id"] = canonical_hash(
            {key: value for key, value in noncanonical.items() if key != "snapshot_id"}
        )[:24]
        with self.assertRaisesRegex(ValueError, "settled_cash is invalid"):
            account_snapshot_from_payload(noncanonical, AS_OF)

    def test_valid_snapshot_bootstraps_and_reconciles(self):
        snapshot = account_snapshot_from_payload(snapshot_payload(), AS_OF)
        ledger = ledger_from_broker_snapshot(snapshot)
        self.assertEqual(snapshot.execution_capability, "broker_export_contract_validated")
        self.assertEqual(str(ledger.state.settled_cash), "10000.0")
        self.assertEqual(str(ledger.state.positions["inst-qqq"]), "12.0")
        report = reconcile_broker_snapshot(snapshot, ledger)
        self.assertEqual(report.status, "matched")
        self.assertEqual(report.differences, ())

    def test_synthetic_snapshot_never_unlocks_broker_capability(self):
        snapshot = account_snapshot_from_payload(snapshot_payload("synthetic"), AS_OF)
        self.assertEqual(snapshot.execution_capability, "simulation_only")

    def test_stale_future_or_naive_snapshot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            account_snapshot_from_payload(snapshot_payload(), AS_OF + timedelta(days=2))
        with self.assertRaisesRegex(ValueError, "timing"):
            account_snapshot_from_payload(snapshot_payload(), datetime(2024, 1, 2, 21, 4, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "timezone"):
            account_snapshot_from_payload(snapshot_payload(), datetime(2024, 1, 2, 21, 10))

    def test_cash_import_rejects_short_or_option_positions(self):
        short = snapshot_payload()
        short["positions"][0]["quantity"] = -1
        short["snapshot_id"] = canonical_hash({key: value for key, value in short.items() if key != "snapshot_id"})[:24]
        with self.assertRaisesRegex(ValueError, "short"):
            account_snapshot_from_payload(short, AS_OF)
        option = snapshot_payload()
        option["positions"][0]["asset_type"] = "option"
        option["snapshot_id"] = canonical_hash({key: value for key, value in option.items() if key != "snapshot_id"})[:24]
        with self.assertRaisesRegex(ValueError, "only equity"):
            account_snapshot_from_payload(option, AS_OF)

    def test_reconciliation_reports_position_and_cash_differences(self):
        snapshot = account_snapshot_from_payload(snapshot_payload(), AS_OF)
        ledger = ledger_from_broker_snapshot(snapshot)
        ledger.apply(
            LedgerEvent(
                "external-change",
                "2024-01-02T21:06:00Z",
                "external_cash",
                {"amount": 100},
            )
        )
        ledger.state.position_multipliers["inst-qqq"] = ledger.state.position_multipliers[
            "inst-qqq"
        ] * 2
        report = reconcile_broker_snapshot(snapshot, ledger)
        self.assertEqual(report.status, "mismatch")
        self.assertEqual(report.differences[0]["field"], "settled_cash")
        self.assertIn("multiplier", {difference["kind"] for difference in report.differences})

    def test_manually_forged_snapshot_cannot_initialize_ledger(self):
        snapshot = account_snapshot_from_payload(snapshot_payload(), AS_OF)
        forged = replace(snapshot, balances={**snapshot.balances, "settled_cash": 999999.0})
        with self.assertRaisesRegex(ValueError, "identity validation"):
            ledger_from_broker_snapshot(forged)

    def test_cli_bootstrap_is_read_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_file = root / "snapshot.json"
            state_file = root / "simulator.json"
            snapshot_file.write_text(json.dumps(snapshot_payload()), encoding="utf-8")
            output = StringIO()
            args = [
                "bootstrap",
                "--snapshot", str(snapshot_file),
                "--simulator-state", str(state_file),
                "--as-of", "2024-01-02T21:10:00Z",
            ]
            with redirect_stdout(output):
                self.assertEqual(main(args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["orders_submitted"], 0)
            self.assertEqual(result["reconciliation"]["status"], "matched")
            with self.assertRaisesRegex(FileExistsError, "not overwrite"):
                main(args)
            simulator = PaperSimulator.load(state_file)
            simulator.ledger.apply(
                LedgerEvent(
                    "external-after-bootstrap",
                    "2024-01-02T21:06:00Z",
                    "external_cash",
                    {"amount": 1},
                )
            )
            simulator.save(state_file)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["reconcile", *args[1:]]), 2)
            self.assertEqual(json.loads(output.getvalue())["reconciliation"]["status"], "mismatch")


if __name__ == "__main__":
    unittest.main()
