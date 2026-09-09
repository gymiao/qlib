from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from apps.quant_research.config import ResearchConfig
from apps.quant_research.contracts import FeatureBatch, SignalBatch
from apps.quant_research.data import create_price_snapshot, load_price_snapshot
from apps.quant_research.ledger import EventLedger, LedgerEvent
from apps.quant_research.engine import run_stage1_comparison
from apps.quant_research.portfolio import ShareIntent, net_share_intents, target_weight_orders
from apps.quant_research.reporting import publish_stage1_report, validate_published_run
from apps.quant_research.risk import joint_factor_dollars
from apps.quant_research.signals import generate_latest_signal
from decimal import Decimal


class StageZeroTest(unittest.TestCase):
    def test_config_is_stable_and_validated(self):
        first = ResearchConfig()
        second = ResearchConfig()
        self.assertEqual(first.config_hash, second.config_hash)
        with self.assertRaisesRegex(ValueError, "exceed"):
            ResearchConfig(initial_target_weights={"QQQ": 0.8, "SPY": 0.8})
        with self.assertRaisesRegex(ValueError, "invalid"):
            ResearchConfig(initial_target_weights={"QQQ": float("nan")})

    def test_snapshot_is_content_addressed_and_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "QQQ": [100, 101], "SPY": [90, 91]}).to_csv(source, index=False)
            as_of = datetime(2024, 1, 4, tzinfo=timezone.utc)
            first = create_price_snapshot(source, root / "snapshots", "qqq-spy", ("QQQ", "SPY"), as_of)
            second = create_price_snapshot(source, root / "snapshots", "qqq-spy", ("QQQ", "SPY"), as_of)
            self.assertEqual(first, second)
            loaded, prices = load_price_snapshot(root / "snapshots", first.snapshot_id)
            self.assertEqual(loaded.manifest_hash, first.manifest_hash)
            self.assertEqual(loaded.quality["rows"], 2)
            self.assertEqual(loaded.quality["max_calendar_gap_days"], 1)
            self.assertEqual(list(prices.columns), ["QQQ", "SPY"])
            changed = create_price_snapshot(source, root / "snapshots", "qqq-only", ("QQQ",), as_of)
            self.assertNotEqual(changed.snapshot_id, first.snapshot_id)

    def test_snapshot_rejects_nonfinite_prices_and_manifest_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            pd.DataFrame({"date": ["2024-01-02"], "QQQ": [float("inf")], "SPY": [90]}).to_csv(source, index=False)
            with self.assertRaisesRegex(ValueError, "non-positive"):
                create_price_snapshot(source, root / "snapshots", "bad", ("QQQ", "SPY"), datetime(2024, 1, 3, tzinfo=timezone.utc))
            pd.DataFrame({"date": ["2024-01-02"], "QQQ": [100], "SPY": [90]}).to_csv(source, index=False)
            snapshot = create_price_snapshot(source, root / "snapshots", "good", ("QQQ", "SPY"), datetime(2024, 1, 3, tzinfo=timezone.utc))
            manifest = root / "snapshots" / snapshot.snapshot_id / "manifest.json"
            payload = __import__("json").loads(manifest.read_text())
            payload["quality"]["status"] = "forged"
            manifest.write_text(__import__("json").dumps(payload))
            with self.assertRaisesRegex(RuntimeError, "manifest hash"):
                load_price_snapshot(root / "snapshots", snapshot.snapshot_id)

    def test_features_exclude_labels_and_rules_need_no_model(self):
        with self.assertRaisesRegex(ValueError, "cannot contain"):
            FeatureBatch("features-v1", "snapshot", "2024-01-02T21:00:00Z", ("ret_5", "target"), ())
        signal = SignalBatch("rule-1", "cash-reduction", "rule", "1", "2024-01-02T21:00:00Z", "2024-01-03T14:30:00Z", "weights")
        self.assertIsNone(signal.model_id)

    def test_current_signal_uses_latest_features_without_labels(self):
        class SumModel:
            def predict(self, values):
                return values.sum(axis=1)

        frame = pd.DataFrame({
            "datetime": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-03"]),
            "instrument": ["OLD", "QQQ", "SPY"],
            "ret_5": [0.0, 0.2, 0.1],
        })
        signal = generate_latest_signal(
            frame, ["ret_5"], SumModel(), "model-1", "selector", "snapshot-1",
            datetime(2024, 1, 3, 21, tzinfo=timezone.utc),
        )
        self.assertEqual(set(signal.scores), {"QQQ", "SPY"})
        self.assertEqual(signal.target_kind, "relative_return_score")
        self.assertNotIn("OLD", signal.scores)


class StageOneLedgerTest(unittest.TestCase):
    def event(self, event_id, kind, payload):
        return LedgerEvent(event_id, "2024-01-02T14:30:00Z", kind, payload)

    def test_l01_trade_payable_and_settlement(self):
        ledger = EventLedger(10_000)
        ledger.apply(self.event("buy", "equity_fill", {"instrument": "SPY", "quantity": 10, "price": 100, "fee": 1}))
        self.assertEqual(ledger.state.nav({"SPY": 100}), 9999)
        self.assertEqual(ledger.state.trade_payable, 1001)
        ledger.apply(self.event("settle", "settle_trade_payable", {"amount": 1001}))
        self.assertEqual(ledger.state.settled_cash, 8999)
        self.assertEqual(ledger.state.nav({"SPY": 100}), 9999)

    def test_l02_dividend_and_split(self):
        ledger = EventLedger(0)
        ledger.state.positions["SPY"] = 100
        ledger.apply(self.event("div", "dividend_entitlement", {"amount": 100}))
        self.assertEqual(ledger.state.nav({"SPY": 99}), 10_000)
        ledger.apply(self.event("pay", "dividend_payment", {"amount": 100}))
        self.assertEqual(ledger.state.nav({"SPY": 99}), 10_000)
        ledger.apply(self.event("split", "split", {"instrument": "SPY", "ratio": 2}))
        self.assertEqual(ledger.state.positions["SPY"], 200)
        self.assertEqual(ledger.state.nav({"SPY": 49.5}), 10_000)

    def test_l05_idempotency_replay_and_external_cash(self):
        events = [
            self.event("buy", "equity_fill", {"instrument": "QQQ", "quantity": 10, "price": 100}),
            self.event("settle", "settle_trade_payable", {"amount": 1000}),
            self.event("deposit", "external_cash", {"amount": 1000}),
        ]
        ledger = EventLedger.replay(10_000, events)
        self.assertFalse(ledger.apply(events[-1]))
        replayed = EventLedger.replay(10_000, events)
        self.assertEqual(ledger.snapshot(), replayed.snapshot())
        self.assertEqual(ledger.state.nav({"QQQ": 100}), 11_000)

    def test_reservation_is_not_counted_in_nav(self):
        ledger = EventLedger(10_000)
        ledger.apply(self.event("reserve", "reserve", {"reservation_id": "order-1", "amount": 4000}))
        self.assertEqual(ledger.state.available_cash, 6000)
        self.assertEqual(ledger.state.nav({}), 10_000)
        with self.assertRaisesRegex(ValueError, "reserved"):
            ledger.apply(self.event("fill", "equity_fill", {"instrument": "SPY", "quantity": 50, "price": 100, "reservation_id": "order-1"}))

    def test_e01_intents_are_netted_once(self):
        orders = net_share_intents([
            ShareIntent("selector", "QQQ", Decimal("10")),
            ShareIntent("risk", "QQQ", Decimal("-6")),
        ])
        self.assertEqual(orders["QQQ"].quantity, 4)
        self.assertEqual(sum(orders["QQQ"].attribution.values()), 4)

    def test_l04_full_replacement_charges_both_sides(self):
        orders = target_weight_orders(
            Decimal("20000"), Decimal("10010"), {"OLD": Decimal("100")},
            {"NEW": 0.5}, {"OLD": 100, "NEW": 100}, 10,
        )
        self.assertEqual([(order.instrument, order.quantity) for order in orders], [
            ("OLD", Decimal("-100")), ("NEW", Decimal("100")),
        ])
        self.assertEqual(sum(order.estimated_fee for order in orders), Decimal("20.000000"))

    def test_l04_weight_drift_trades_even_when_names_do_not_change(self):
        orders = target_weight_orders(
            Decimal("20000"), Decimal("1010"),
            {"A": Decimal("90"), "B": Decimal("110")},
            {"A": 0.5, "B": 0.5}, {"A": 100, "B": 100}, 10,
        )
        self.assertEqual([(order.instrument, order.quantity) for order in orders], [
            ("B", Decimal("-10")), ("A", Decimal("10")),
        ])

    def test_stage1_report_uses_ledger_and_settled_cash(self):
        dates = pd.bdate_range("2024-01-02", periods=30)
        prices = pd.DataFrame({"QQQ": [100 + i * 1.1 for i in range(30)], "SPY": [200 + i for i in range(30)]}, index=dates)
        report = run_stage1_comparison(prices)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(set(report["metrics"]), {"initial_hold", "cash_reduced"})
        first = report["equity_curves"].query("strategy == 'initial_hold'").iloc[0]
        second = report["equity_curves"].query("strategy == 'initial_hold'").iloc[1]
        self.assertGreater(first["trade_payable"], 0)
        self.assertEqual(second["trade_payable"], 0)
        self.assertGreaterEqual(report["equity_curves"]["available_cash"].min(), 0)
        self.assertEqual(report["risk_snapshots"]["initial_hold"]["factor_definition"], "SPY_PLUS_QQQ_RESIDUAL_V1")

    def test_repository_demo_csv_runs_stage1(self):
        repository = Path(__file__).resolve().parents[3]
        source = repository / "examples" / "data" / "qqq_spy_stage1_demo.csv.example"
        prices = pd.read_csv(source, parse_dates=["date"]).set_index("date")
        report = run_stage1_comparison(prices, ResearchConfig())
        self.assertEqual(len(prices), 30)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(set(report["metrics"]), {"initial_hold", "cash_reduced"})
        self.assertEqual(len(report["equity_curves"]), 60)

    def test_r01_joint_factor_exposure_includes_put_delta_once(self):
        self.assertEqual(joint_factor_dollars(50_000, 50_000, 1.2), (110_000, 50_000))
        self.assertEqual(joint_factor_dollars(50_000, 50_000, 1.2, -20_000), (86_000, 30_000))

    def test_s1_3_report_is_atomic_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "prices.csv"
            dates = pd.bdate_range("2024-01-02", periods=30)
            pd.DataFrame({"date": dates, "QQQ": [100 + i * 1.1 for i in range(30)], "SPY": [200 + i for i in range(30)]}).to_csv(source, index=False)
            config = ResearchConfig()
            snapshot = create_price_snapshot(source, root / "snapshots", "qqq-spy", config.instruments, datetime(2024, 1, 8, tzinfo=timezone.utc))
            _, prices = load_price_snapshot(root / "snapshots", snapshot.snapshot_id)
            first = publish_stage1_report(run_stage1_comparison(prices, config), root / "runs", snapshot, config)
            second = publish_stage1_report(run_stage1_comparison(prices, config), root / "runs", snapshot, config)
            self.assertEqual(first, second)
            manifest = __import__("json").loads((first / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "complete")
            self.assertTrue(manifest["source_files"])
            self.assertTrue(all(not key.startswith("/") for key in manifest["source_files"]))
            self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["source_files"].values()))
            self.assertIn("pandas", manifest["runtime"]["dependencies"])
            self.assertTrue((first / "config.json").exists())
            self.assertTrue((first / "event_log.json").exists())
            self.assertFalse(any(path.name.startswith(".") for path in (root / "runs").iterdir()))
            validate_published_run(first)
            (first / "orders.csv").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "artifact hash"):
                validate_published_run(first)


class StageTwoOptionLedgerTest(unittest.TestCase):
    def event(self, event_id, kind, payload):
        return LedgerEvent(event_id, "2024-02-16T21:00:00Z", kind, payload)

    def test_l03_long_put_spread_and_fee_are_reflected_once(self):
        ledger = EventLedger(10_000)
        ledger.apply(self.event("put", "option_fill", {"contract_id": "SPY-P100", "quantity": 1, "premium": 5, "multiplier": 100, "fee": 1}))
        self.assertEqual(ledger.state.trade_payable, 501)
        self.assertEqual(ledger.state.nav({"SPY-P100": 4.8}), 9979)
        ledger.apply(self.event("settle", "settle_trade_payable", {"amount": 501}))
        self.assertEqual(ledger.state.settled_cash, 9499)
        self.assertEqual(ledger.state.nav({"SPY-P100": 4.8}), 9979)

    def test_o01_covered_long_put_exercise_does_not_double_count_intrinsic(self):
        ledger = EventLedger(0)
        ledger.state.positions["SPY"] = Decimal("100")
        ledger.state.position_multipliers["SPY"] = Decimal("1")
        ledger.state.positions["SPY-P100"] = Decimal("1")
        ledger.state.position_multipliers["SPY-P100"] = Decimal("100")
        self.assertEqual(ledger.state.nav({"SPY": 90, "SPY-P100": 10}), 10_000)
        ledger.apply(self.event("exercise", "long_put_exercise", {"contract_id": "SPY-P100", "underlying": "SPY", "contracts": 1, "strike": 100}))
        self.assertEqual(ledger.state.trade_receivable, 10_000)
        self.assertEqual(ledger.state.nav({}), 10_000)

    def test_o02_cash_secured_short_put_assignment(self):
        ledger = EventLedger(10_000)
        ledger.apply(self.event("sell", "option_fill", {"contract_id": "SPY-P95", "quantity": -1, "premium": 2, "multiplier": 100}))
        ledger.apply(self.event("premium", "settle_trade_receivable", {"amount": 200}))
        ledger.apply(self.event("collateral", "collateral_open", {"collateral_id": "SPY-P95", "amount": 9500}))
        self.assertEqual(ledger.state.available_cash, 700)
        self.assertEqual(ledger.state.nav({"SPY-P95": 2}), 10_000)
        ledger.apply(self.event("assignment", "short_put_assignment", {"contract_id": "SPY-P95", "underlying": "SPY", "contracts": 1, "strike": 95, "collateral_id": "SPY-P95"}))
        self.assertEqual(ledger.state.available_cash, 700)
        self.assertEqual(ledger.state.nav({"SPY": 90}), 9700)
        ledger.apply(self.event("strike-settle", "settle_trade_payable", {"amount": 9500}))
        self.assertEqual(ledger.state.settled_cash, 700)
        self.assertEqual(ledger.state.nav({"SPY": 90}), 9700)

    def test_o03_rejects_uncovered_exercise_and_fractional_contract_assumption(self):
        ledger = EventLedger(1000)
        ledger.state.positions["SPY-P100"] = Decimal("1")
        ledger.state.position_multipliers["SPY-P100"] = Decimal("100")
        with self.assertRaisesRegex(ValueError, "short stock"):
            ledger.apply(self.event("exercise", "long_put_exercise", {"contract_id": "SPY-P100", "underlying": "SPY", "contracts": 1, "strike": 100}))

    def test_partial_short_put_assignment_keeps_remaining_collateral(self):
        ledger = EventLedger(20_000)
        ledger.apply(self.event("short-two", "option_fill", {"contract_id": "SPY-P95", "quantity": -2, "premium": 2, "multiplier": 100}))
        ledger.apply(self.event("premium", "settle_trade_receivable", {"amount": 400}))
        ledger.apply(self.event("collateral", "collateral_open", {"collateral_id": "SPY-P95", "amount": 19000}))
        ledger.apply(self.event("assign-one", "short_put_assignment", {"contract_id": "SPY-P95", "underlying": "SPY", "contracts": 1, "strike": 95, "collateral_id": "SPY-P95"}))
        self.assertEqual(ledger.state.positions["SPY-P95"], -1)
        self.assertEqual(ledger.state.positions["SPY"], 100)
        self.assertEqual(ledger.state.collateral["SPY-P95"], 9500)
        self.assertEqual(ledger.state.trade_payable, 9500)

    def test_bear_put_spread_one_leg_assignment_keeps_real_exposure(self):
        ledger = EventLedger(20_000)
        ledger.apply(self.event("long", "option_fill", {"contract_id": "SPY-P100", "quantity": 1, "premium": 8, "multiplier": 100}))
        ledger.apply(self.event("settle-long", "settle_trade_payable", {"amount": 800}))
        ledger.apply(self.event("short", "option_fill", {"contract_id": "SPY-P90", "quantity": -1, "premium": 3, "multiplier": 100}))
        ledger.apply(self.event("settle-short", "settle_trade_receivable", {"amount": 300}))
        ledger.apply(self.event("collateral", "collateral_open", {"collateral_id": "SPY-P90", "amount": 9000}))
        ledger.apply(self.event("assign-short", "short_put_assignment", {"contract_id": "SPY-P90", "underlying": "SPY", "contracts": 1, "strike": 90, "collateral_id": "SPY-P90"}))
        self.assertEqual(ledger.state.positions["SPY"], 100)
        self.assertEqual(ledger.state.positions["SPY-P100"], 1)
        ledger.apply(self.event("exercise-long", "long_put_exercise", {"contract_id": "SPY-P100", "underlying": "SPY", "contracts": 1, "strike": 100}))
        self.assertNotIn("SPY", ledger.state.positions)
        self.assertEqual(ledger.state.trade_payable, 9000)
        self.assertEqual(ledger.state.trade_receivable, 10000)


if __name__ == "__main__":
    unittest.main()
