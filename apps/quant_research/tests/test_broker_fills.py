from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_account import account_snapshot_from_payload, ledger_from_broker_snapshot
from apps.quant_research.broker_fills import (
    attach_broker_fill_batch_id,
    broker_fill_batch_from_payload,
    import_broker_fill_batch,
)
from apps.quant_research.broker_order_status import (
    attach_broker_order_status_batch_id,
    broker_order_status_batch_from_payload,
    import_broker_order_status_batch,
)
from apps.quant_research.broker_orders import broker_order_package_payload, prepare_broker_order_package
from apps.quant_research.contracts import OrderPlan, canonical_hash
from apps.quant_research.import_broker_fills import main
from apps.quant_research.ledger import LedgerEvent
from apps.quant_research.simulator import ConcurrentStateError, PaperSimulator
from apps.quant_research.tests.test_broker_account import snapshot_payload


GENERATED_AT = datetime(2024, 1, 2, 21, 10, tzinfo=timezone.utc)
AS_OF = datetime(2024, 1, 2, 21, 20, tzinfo=timezone.utc)


def setup_order_package(quantity=-2, *, accepted=True):
    snapshot = account_snapshot_from_payload(snapshot_payload(), GENERATED_AT)
    ledger = ledger_from_broker_snapshot(snapshot)
    plan = OrderPlan(
        plan_id="plan-fill-1",
        plan_basis_hash=ledger.basis_hash(),
        account_snapshot_ref=ledger.snapshot()["state_hash"],
        source_ids=("signal-1",),
        orders=(
            {
                "instrument": "inst-qqq",
                "quantity": quantity,
                "max_price": 410.0,
                "estimated_fee": 1.0,
            },
        ),
        reservations={},
        expires_at="2024-01-03T21:00:00Z",
    )
    package = prepare_broker_order_package(plan, snapshot, ledger, {"inst-qqq": "QQQ"}, GENERATED_AT)
    if accepted:
        status = broker_order_status_batch_from_payload(status_payload(package), package, AS_OF)
        import_broker_order_status_batch(status, package, ledger)
    return package, ledger


def status_payload(package, *, status="accepted", reason_code=None, status_id="status-1"):
    payload = {
        "schema_version": "broker_order_status_batch.v1",
        "package_id": package.package_id,
        "account_id": package.account_id,
        "broker_id": package.broker_id,
        "captured_at": "2024-01-02T21:12:00Z",
        "available_at": "2024-01-02T21:13:00Z",
        "statuses": [
            {
                "broker_status_id": status_id,
                "client_order_id": package.orders[0]["client_order_id"],
                "broker_order_id": "broker-order-1",
                "status": status,
                "reason_code": reason_code,
                "effective_at": "2024-01-02T21:11:00Z",
                "available_at": "2024-01-02T21:11:30Z",
            }
        ],
        "source": {
            "source_kind": "synthetic",
            "export_id": f"status-export-{status_id}",
            "content_sha256": "a" * 64,
        },
    }
    return attach_broker_order_status_batch_id(payload)


def fill_payload(
    package,
    *,
    execution_id="exec-1",
    quantity=2,
    price=410,
    executed_at="2024-01-02T21:12:00Z",
    row_available_at="2024-01-02T21:14:00Z",
    captured_at="2024-01-02T21:15:00Z",
    batch_available_at="2024-01-02T21:16:00Z",
):
    order = package.orders[0]
    payload = {
        "schema_version": "broker_fill_batch.v1",
        "package_id": package.package_id,
        "account_id": package.account_id,
        "broker_id": package.broker_id,
        "captured_at": captured_at,
        "available_at": batch_available_at,
        "fills": [
            {
                "broker_execution_id": execution_id,
                "client_order_id": order["client_order_id"],
                "instrument_id": order["instrument_id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "quantity": float(quantity),
                "price": float(price),
                "fee": 1.0,
                "executed_at": executed_at,
                "available_at": row_available_at,
            }
        ],
        "source": {
            "source_kind": "broker_observed",
            "export_id": f"export-{execution_id}",
            "content_sha256": "b" * 64,
        },
    }
    return attach_broker_fill_batch_id(payload)


class BrokerFillImportTest(unittest.TestCase):
    def test_complete_observed_sell_fill_is_applied_without_submitting_orders(self):
        package, ledger = setup_order_package()
        batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        result = import_broker_fill_batch(batch, package, ledger)
        self.assertEqual(batch.import_capability, "broker_fill_contract_validated")
        self.assertEqual(result.import_capability, "broker_fill_contract_validated")
        self.assertEqual(result.status, "filled")
        self.assertEqual(result.orders_submitted_by_system, 0)
        self.assertEqual(str(ledger.state.positions["inst-qqq"]), "10.0")
        self.assertEqual(ledger.state.trade_receivable, Decimal("839"))
        self.assertEqual(result.order_statuses[0]["remaining_quantity"], 0.0)

    def test_fill_requires_prior_acceptance_and_respects_terminal_time(self):
        package, ledger = setup_order_package(accepted=False)
        batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        with self.assertRaisesRegex(ValueError, "accepted order status"):
            import_broker_fill_batch(batch, package, ledger)

        rejected = broker_order_status_batch_from_payload(
            status_payload(package, status="rejected", reason_code="BROKER_REJECTED"),
            package,
            AS_OF,
        )
        import_broker_order_status_batch(rejected, package, ledger)
        with self.assertRaisesRegex(ValueError, "accepted order status"):
            import_broker_fill_batch(batch, package, ledger)

        package, ledger = setup_order_package()
        cancelled_payload = status_payload(
            package,
            status="cancelled",
            reason_code="USER_CANCELLED",
            status_id="status-cancelled",
        )
        cancelled_payload["statuses"][0]["effective_at"] = "2024-01-02T21:17:00Z"
        cancelled_payload["statuses"][0]["available_at"] = "2024-01-02T21:17:30Z"
        cancelled_payload["captured_at"] = "2024-01-02T21:18:00Z"
        cancelled_payload["available_at"] = "2024-01-02T21:18:30Z"
        cancelled_payload = attach_broker_order_status_batch_id(
            {key: value for key, value in cancelled_payload.items() if key != "batch_id"}
        )
        cancelled = broker_order_status_batch_from_payload(cancelled_payload, package, AS_OF)
        import_broker_order_status_batch(cancelled, package, ledger)
        late_fill = broker_fill_batch_from_payload(
            fill_payload(
                package,
                execution_id="late-fill",
                executed_at="2024-01-02T21:18:00Z",
                row_available_at="2024-01-02T21:18:30Z",
                captured_at="2024-01-02T21:19:00Z",
                batch_available_at="2024-01-02T21:19:30Z",
            ),
            package,
            AS_OF,
        )
        with self.assertRaisesRegex(ValueError, "became terminal"):
            import_broker_fill_batch(late_fill, package, ledger)

    def test_buy_fill_uses_settled_cash_and_creates_trade_payable(self):
        package, ledger = setup_order_package(quantity=2)
        batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        result = import_broker_fill_batch(batch, package, ledger)
        self.assertEqual(result.status, "filled")
        self.assertEqual(str(ledger.state.positions["inst-qqq"]), "14.0")
        self.assertEqual(ledger.state.trade_payable, Decimal("831"))

    def test_v2_decimal_fill_preserves_exact_broker_amounts(self):
        package, ledger = setup_order_package()
        payload = fill_payload(package, execution_id="decimal-v2")
        row = payload["fills"][0]
        row["quantity_decimal"] = "2"
        row["price_decimal"] = "410.123456789012345678"
        row["fee_decimal"] = "0.000000000000000001"
        for field in ("quantity", "price", "fee"):
            row.pop(field)
        payload["schema_version"] = "broker_fill_batch.v2"
        payload = attach_broker_fill_batch_id(
            {key: value for key, value in payload.items() if key != "batch_id"}
        )
        batch = broker_fill_batch_from_payload(payload, package, AS_OF)
        self.assertEqual(batch.schema_version, "broker_fill_batch.v2")
        import_broker_fill_batch(batch, package, ledger)
        self.assertEqual(
            ledger.state.trade_receivable,
            Decimal("20")
            + Decimal("2") * Decimal("410.123456789012345678")
            - Decimal("0.000000000000000001"),
        )

        noncanonical = json.loads(json.dumps(payload))
        noncanonical["fills"][0]["price_decimal"] = "410.1234567890123456780"
        noncanonical = attach_broker_fill_batch_id(
            {key: value for key, value in noncanonical.items() if key != "batch_id"}
        )
        with self.assertRaisesRegex(ValueError, "fill price is invalid"):
            broker_fill_batch_from_payload(noncanonical, package, AS_OF)

    def test_reimport_is_idempotent_even_from_a_new_overlapping_export(self):
        package, ledger = setup_order_package()
        first = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        import_broker_fill_batch(first, package, ledger)
        overlapping = fill_payload(package)
        overlapping["source"]["export_id"] = "later-export"
        overlapping["source"]["content_sha256"] = "c" * 64
        overlapping["batch_id"] = canonical_hash(
            {key: value for key, value in overlapping.items() if key != "batch_id"}
        )[:24]
        second = broker_fill_batch_from_payload(overlapping, package, AS_OF)
        result = import_broker_fill_batch(second, package, ledger)
        self.assertEqual(result.applied_event_ids, ())
        self.assertEqual(len(result.duplicate_event_ids), 1)
        self.assertEqual(str(ledger.state.positions["inst-qqq"]), "10.0")
        self.assertEqual(len(ledger.events), package.account_sequence + 2)

    def test_duplicate_can_be_verified_after_settlement_but_new_fill_is_blocked(self):
        package, ledger = setup_order_package()
        batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        import_broker_fill_batch(batch, package, ledger)
        ledger.apply(
            LedgerEvent(
                "settlement-after-fill",
                "2024-01-03T21:00:00Z",
                "settle_trade_receivable",
                {"amount": "819"},
            )
        )
        duplicate = import_broker_fill_batch(batch, package, ledger)
        self.assertEqual(len(duplicate.duplicate_event_ids), 1)
        new_payload = fill_payload(package, execution_id="new-after-settlement", quantity=1)
        new_batch = broker_fill_batch_from_payload(new_payload, package, AS_OF)
        with self.assertRaisesRegex(ValueError, "account changed outside"):
            import_broker_fill_batch(new_batch, package, ledger)

    def test_incremental_partial_batches_reconcile_to_filled(self):
        package, ledger = setup_order_package()
        first = broker_fill_batch_from_payload(
            fill_payload(package, execution_id="exec-a", quantity=1), package, AS_OF
        )
        first_result = import_broker_fill_batch(first, package, ledger)
        self.assertEqual(first_result.status, "partially_filled")
        second_payload = fill_payload(package, execution_id="exec-b", quantity=1)
        second = broker_fill_batch_from_payload(second_payload, package, AS_OF)
        second_result = import_broker_fill_batch(second, package, ledger)
        self.assertEqual(second_result.status, "filled")
        self.assertEqual(second_result.order_statuses[0]["filled_quantity"], 2.0)

    def test_wrong_order_data_overfill_limit_and_future_visibility_are_rejected(self):
        package, _ = setup_order_package()
        cases = (
            ("side", "buy", "instrument, symbol, or side"),
            ("instrument_id", "inst-spy", "instrument, symbol, or side"),
            ("client_order_id", "unknown", "identifier"),
            ("quantity", 3, "exceeds"),
            ("price", 400, "limit"),
            ("available_at", "2024-01-02T21:21:00Z", "availability window"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = fill_payload(package)
                payload["fills"][0][field] = value
                payload["batch_id"] = canonical_hash(
                    {key: item for key, item in payload.items() if key != "batch_id"}
                )[:24]
                with self.assertRaisesRegex(ValueError, message):
                    broker_fill_batch_from_payload(payload, package, AS_OF)

    def test_cumulative_overfill_and_unrelated_account_change_are_atomic(self):
        package, ledger = setup_order_package()
        first = broker_fill_batch_from_payload(fill_payload(package, quantity=2), package, AS_OF)
        import_broker_fill_batch(first, package, ledger)
        extra = broker_fill_batch_from_payload(
            fill_payload(package, execution_id="exec-extra", quantity=1), package, AS_OF
        )
        event_count = len(ledger.events)
        with self.assertRaisesRegex(ValueError, "exceed"):
            import_broker_fill_batch(extra, package, ledger)
        self.assertEqual(len(ledger.events), event_count)

        package, ledger = setup_order_package()
        ledger.apply(LedgerEvent("external", "2024-01-02T21:11:00Z", "external_cash", {"amount": 1}))
        batch = broker_fill_batch_from_payload(fill_payload(package), package, AS_OF)
        with self.assertRaisesRegex(ValueError, "outside"):
            import_broker_fill_batch(batch, package, ledger)
        self.assertEqual(len(ledger.events), package.account_sequence + 2)

    def test_forged_existing_package_fill_is_not_trusted(self):
        package, ledger = setup_order_package()
        order = package.orders[0]
        ledger.apply(
            LedgerEvent(
                "broker-fill:broker-adapter-1:forged",
                "2024-01-02T21:12:00Z",
                "equity_fill",
                {
                    "instrument": order["instrument_id"],
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "quantity": "-1",
                    "price": "1",
                    "fee": "0",
                    "order_package_id": package.package_id,
                    "client_order_id": order["client_order_id"],
                    "broker_execution_id": "forged",
                },
                available_at="2024-01-02T21:14:00Z",
            )
        )
        batch = broker_fill_batch_from_payload(
            fill_payload(package, execution_id="exec-clean", quantity=1), package, AS_OF
        )
        with self.assertRaisesRegex(ValueError, "limit"):
            import_broker_fill_batch(batch, package, ledger)

    def test_cli_persists_once_and_reports_duplicate_on_retry(self):
        package, ledger = setup_order_package()
        payload = fill_payload(package)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_file = root / "package.json"
            fills_file = root / "fills.json"
            state_file = root / "simulator.json"
            package_file.write_text(json.dumps(broker_order_package_payload(package)), encoding="utf-8")
            fills_file.write_text(json.dumps(payload), encoding="utf-8")
            PaperSimulator(ledger).save(state_file)
            args = [
                "--package", str(package_file),
                "--fills", str(fills_file),
                "--simulator-state", str(state_file),
                "--as-of", "2024-01-02T21:20:00Z",
            ]
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["orders_submitted_by_system"], 0)
            self.assertEqual(json.loads(output.getvalue())["import_capability"], "broker_fill_contract_validated")
            with redirect_stdout(StringIO()) as retry_output:
                self.assertEqual(main(args), 0)
            self.assertEqual(len(json.loads(retry_output.getvalue())["duplicate_event_ids"]), 1)
            restored = PaperSimulator.load(state_file)
            self.assertEqual(str(restored.ledger.state.positions["inst-qqq"]), "10.0")
            self.assertEqual(len(restored.ledger.events), package.account_sequence + 2)

    def test_cli_expected_state_hash_rejects_stale_import(self):
        package, ledger = setup_order_package()
        payload = fill_payload(package)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_file = root / "package.json"
            fills_file = root / "fills.json"
            state_file = root / "simulator.json"
            package_file.write_text(json.dumps(broker_order_package_payload(package)), encoding="utf-8")
            fills_file.write_text(json.dumps(payload), encoding="utf-8")
            PaperSimulator(ledger).save(state_file)
            args = [
                "--package", str(package_file),
                "--fills", str(fills_file),
                "--simulator-state", str(state_file),
                "--as-of", "2024-01-02T21:20:00Z",
                "--expected-content-sha256", "0" * 64,
            ]
            with self.assertRaisesRegex(ConcurrentStateError, "expected version"):
                main(args)
            restored = PaperSimulator.load(state_file)
            self.assertEqual(str(restored.ledger.state.positions["inst-qqq"]), "12.0")


if __name__ == "__main__":
    unittest.main()
