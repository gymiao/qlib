from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.broker_account import account_snapshot_from_payload, ledger_from_broker_snapshot
from apps.quant_research.broker_orders import (
    broker_order_package_from_payload,
    broker_order_package_payload,
    prepare_broker_order_package,
    validate_broker_order_package,
)
from apps.quant_research.contracts import OrderPlan, canonical_hash
from apps.quant_research.prepare_broker_orders import main
from apps.quant_research.simulator import PaperSimulator
from apps.quant_research.tests.test_broker_account import AS_OF, snapshot_payload


GENERATED_AT = datetime(2024, 1, 2, 21, 10, tzinfo=timezone.utc)


def setup_account():
    snapshot = account_snapshot_from_payload(snapshot_payload(), AS_OF)
    ledger = ledger_from_broker_snapshot(snapshot)
    return snapshot, ledger


def plan(ledger, *, expires_at="2024-01-03T21:00:00Z", quantity=-2):
    return OrderPlan(
        plan_id="plan-1",
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
        expires_at=expires_at,
    )


class BrokerOrderPackageTest(unittest.TestCase):
    def test_valid_plan_becomes_manual_review_package(self):
        snapshot, ledger = setup_account()
        package = prepare_broker_order_package(
            plan(ledger), snapshot, ledger, {"inst-qqq": "QQQ"}, GENERATED_AT
        )
        validate_broker_order_package(package)
        self.assertEqual(package.status, "pending_human_approval")
        self.assertEqual(package.execution_capability, "manual_export_only")
        self.assertEqual(package.orders_submitted, 0)
        self.assertEqual(package.schema_version, "broker_order_package.v2")
        self.assertEqual(package.orders[0]["side"], "sell")
        self.assertEqual(package.orders[0]["quantity"], "2")

        payload = broker_order_package_payload(package)
        self.assertEqual(payload["orders"][0]["quantity_decimal"], "2")
        self.assertEqual(broker_order_package_from_payload(payload), package)
        noncanonical = json.loads(json.dumps(payload))
        noncanonical["orders"][0]["limit_price_decimal"] = "410.0"
        noncanonical["package_id"] = canonical_hash(
            {key: value for key, value in noncanonical.items() if key != "package_id"}
        )[:24]
        with self.assertRaisesRegex(ValueError, "not canonical"):
            broker_order_package_from_payload(noncanonical)
        invalid = json.loads(json.dumps(payload))
        invalid["orders"][0]["limit_price_decimal"] = "not-a-number"
        with self.assertRaisesRegex(ValueError, "decimals are invalid"):
            broker_order_package_from_payload(invalid)
        wrong_type = json.loads(json.dumps(payload))
        wrong_type["orders"][0]["limit_price_decimal"] = 410
        with self.assertRaisesRegex(ValueError, "decimals must be strings"):
            broker_order_package_from_payload(wrong_type)

        legacy = prepare_broker_order_package(
            plan(ledger),
            snapshot,
            ledger,
            {"inst-qqq": "QQQ"},
            GENERATED_AT,
            schema_version="broker_order_package.v1",
        )
        self.assertEqual(legacy.orders[0]["quantity"], 2.0)
        self.assertEqual(broker_order_package_from_payload(broker_order_package_payload(legacy)), legacy)

    def test_account_change_expiry_and_missing_symbol_are_blocking(self):
        snapshot, ledger = setup_account()
        stale_plan = plan(ledger)
        ledger.state.settled_cash += 1
        with self.assertRaisesRegex(ValueError, "current account state"):
            prepare_broker_order_package(
                stale_plan, snapshot, ledger, {"inst-qqq": "QQQ"}, GENERATED_AT
            )
        snapshot, ledger = setup_account()
        with self.assertRaisesRegex(ValueError, "expired"):
            prepare_broker_order_package(
                plan(ledger, expires_at="2024-01-02T21:09:00Z"),
                snapshot,
                ledger,
                {"inst-qqq": "QQQ"},
                GENERATED_AT,
            )
        with self.assertRaisesRegex(ValueError, "broker symbol"):
            prepare_broker_order_package(plan(ledger), snapshot, ledger, {}, GENERATED_AT)

    def test_fractional_or_short_creating_export_is_rejected(self):
        snapshot, ledger = setup_account()
        with self.assertRaisesRegex(ValueError, "integer"):
            prepare_broker_order_package(
                plan(ledger, quantity=1.5), snapshot, ledger, {"inst-qqq": "QQQ"}, GENERATED_AT
            )
        with self.assertRaisesRegex(ValueError, "short position"):
            prepare_broker_order_package(
                plan(ledger, quantity=-13), snapshot, ledger, {"inst-qqq": "QQQ"}, GENERATED_AT
            )

    def test_buy_export_cannot_depend_on_unsettled_sale_proceeds(self):
        snapshot, ledger = setup_account()
        expensive = plan(ledger, quantity=30)
        with self.assertRaisesRegex(ValueError, "buying power"):
            prepare_broker_order_package(
                expensive, snapshot, ledger, {"inst-qqq": "QQQ"}, GENERATED_AT
            )

    def test_cli_is_idempotent_and_never_submits_orders(self):
        snapshot, ledger = setup_account()
        order_plan = plan(ledger)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_file = root / "snapshot.json"
            plan_file = root / "plan.json"
            state_file = root / "simulator.json"
            symbols_file = root / "symbols.json"
            output_file = root / "orders.json"
            snapshot_file.write_text(json.dumps(snapshot_payload()), encoding="utf-8")
            plan_file.write_text(json.dumps(asdict(order_plan)), encoding="utf-8")
            symbols_file.write_text(json.dumps({"inst-qqq": "QQQ"}), encoding="utf-8")
            PaperSimulator(ledger).save(state_file)
            args = [
                "--plan", str(plan_file),
                "--snapshot", str(snapshot_file),
                "--simulator-state", str(state_file),
                "--symbol-map", str(symbols_file),
                "--generated-at", "2024-01-02T21:10:00Z",
                "--output", str(output_file),
            ]
            first_output = StringIO()
            with redirect_stdout(first_output):
                self.assertEqual(main(args), 0)
            first = json.loads(first_output.getvalue())
            self.assertEqual(first["orders_submitted"], 0)
            with redirect_stdout(StringIO()):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(output_file.read_text()), first)


if __name__ == "__main__":
    unittest.main()
