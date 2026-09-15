from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from apps.quant_research.contracts import OrderPlan, canonical_hash
from apps.quant_research.ledger import EventLedger, LedgerEvent
from apps.quant_research.simulator import (
    ConcurrentStateError,
    PaperSimulator,
    SimulatorStateStore,
)


def _concurrent_deposit(state_path, event_id):
    store = SimulatorStateStore(state_path)

    def operation(simulator):
        simulator.ledger.apply(
            LedgerEvent(event_id, "2024-01-03T20:00:00Z", "external_cash", {"amount": "1"})
        )

    store.transact(operation)


class PaperSimulatorTest(unittest.TestCase):
    def plan(self, ledger, orders, plan_id="plan-1"):
        return OrderPlan(
            plan_id, ledger.basis_hash(), "snapshot-1", ("rule-1",), tuple(orders), {},
            "2024-01-03T22:00:00Z",
        )

    def test_plan_retry_does_not_duplicate_fills(self):
        ledger = EventLedger(10_000)
        simulator = PaperSimulator(ledger)
        plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 10, "max_price": 101}])
        first = simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100})
        second = simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100})
        self.assertEqual(first, second)
        self.assertEqual(first.status, "filled")
        self.assertEqual(ledger.state.positions["QQQ"], 10)
        self.assertEqual(len([event for event in ledger.events if event.kind == "equity_fill"]), 1)

    def test_missing_price_and_changed_account_do_not_fill(self):
        ledger = EventLedger(10_000)
        simulator = PaperSimulator(ledger)
        missing = simulator.execute(self.plan(ledger, [{"instrument": "QQQ", "quantity": 10}]), datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {})
        self.assertEqual(missing.reason_codes, ("MISSING_EXECUTION_PRICE",))
        stale_plan = self.plan(ledger, [{"instrument": "SPY", "quantity": 2}], "stale")
        ledger.state.settled_cash -= 1
        changed = simulator.execute(stale_plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"SPY": 400})
        self.assertEqual(changed.status, "needs_revalidation")
        self.assertFalse(ledger.state.positions)

    def test_plan_is_atomic_when_later_fill_is_unaffordable(self):
        ledger = EventLedger(1000)
        simulator = PaperSimulator(ledger)
        plan = self.plan(ledger, [
            {"instrument": "QQQ", "quantity": 5},
            {"instrument": "SPY", "quantity": 5},
        ])
        result = simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100, "SPY": 200})
        self.assertEqual(result.reason_codes, ("ACCOUNT_CONSTRAINT_VIOLATION",))
        self.assertFalse(ledger.events)
        self.assertFalse(ledger.state.positions)

    def test_persisted_simulator_resumes_without_duplicate_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = EventLedger(10_000)
            simulator = PaperSimulator(ledger)
            plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 10}], "persisted")
            original = simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100})
            state = Path(directory) / "simulator.json"
            simulator.save(state)
            resumed = PaperSimulator.load(state)
            repeated = resumed.execute(plan, datetime(2024, 1, 3, 16, tzinfo=timezone.utc), {"QQQ": 101})
            self.assertEqual(original, repeated)
            self.assertEqual(resumed.ledger.state.positions["QQQ"], 10)
            self.assertEqual(len([event for event in resumed.ledger.events if event.kind == "equity_fill"]), 1)

    def test_temporary_missing_price_can_be_retried(self):
        ledger = EventLedger(10_000)
        simulator = PaperSimulator(ledger)
        plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 10}])
        missing = simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {})
        filled = simulator.execute(plan, datetime(2024, 1, 3, 16, tzinfo=timezone.utc), {"QQQ": 100})
        self.assertEqual(missing.status, "infeasible")
        self.assertEqual(filled.status, "filled")
        self.assertEqual(ledger.state.positions["QQQ"], 10)

    def test_partial_fill_then_completion_is_idempotent(self):
        ledger = EventLedger(10_000)
        simulator = PaperSimulator(ledger)
        plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 10}])
        partial = simulator.execute(
            plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100}, fill_fraction=0.4
        )
        complete = simulator.execute(plan, datetime(2024, 1, 3, 16, tzinfo=timezone.utc), {"QQQ": 101})
        repeated = simulator.execute(plan, datetime(2024, 1, 3, 17, tzinfo=timezone.utc), {"QQQ": 99})
        self.assertEqual(partial.status, "partially_filled")
        self.assertEqual(complete.status, "filled")
        self.assertEqual(complete, repeated)
        self.assertEqual(ledger.state.positions["QQQ"], 10)
        self.assertEqual(len(complete.fill_event_ids), 2)

    def test_partial_progress_survives_restart_and_monitor_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = EventLedger(10_000)
            simulator = PaperSimulator(ledger)
            plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 10}], "partial")
            simulator.execute(
                plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100}, fill_fraction=0.4
            )
            self.assertEqual(simulator.monitor()["pending_plan_ids"], ["partial"])
            state = Path(directory) / "simulator.json"
            simulator.save(state)
            resumed = PaperSimulator.load(state)
            complete = resumed.execute(plan, datetime(2024, 1, 3, 16, tzinfo=timezone.utc), {"QQQ": 101})
            self.assertEqual(complete.status, "filled")
            self.assertEqual(resumed.ledger.state.positions["QQQ"], 10)
            self.assertEqual(resumed.monitor()["counts"], {"filled": 1})

    def test_sixty_session_restart_run_stays_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            simulator = PaperSimulator(EventLedger(100_000))
            state = Path(directory) / "simulator.json"
            for index in range(60):
                plan = OrderPlan(
                    f"daily-{index}", simulator.ledger.basis_hash(), "snapshot", ("rule",),
                    ({"instrument": "QQQ", "quantity": 1 if index % 2 == 0 else -1},),
                    {}, "2025-01-01T00:00:00Z",
                )
                result = simulator.execute(
                    plan, datetime(2024, 1, 2, 15, index, tzinfo=timezone.utc), {"QQQ": 100}
                )
                self.assertEqual(result.status, "filled")
                if index % 10 == 9:
                    simulator.save(state)
                    simulator = PaperSimulator.load(state)
                    self.assertEqual(
                        simulator.execute(plan, datetime(2024, 1, 2, 16, index, tzinfo=timezone.utc), {"QQQ": 101}),
                        result,
                    )
            self.assertNotIn("QQQ", simulator.ledger.state.positions)
            self.assertEqual(simulator.monitor()["counts"], {"filled": 60})

    def test_rejects_ambiguous_time_and_reused_completed_plan_id(self):
        ledger = EventLedger(10_000)
        simulator = PaperSimulator(ledger)
        plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 1}])
        with self.assertRaisesRegex(ValueError, "timezone"):
            simulator.execute(plan, datetime(2024, 1, 3, 15), {"QQQ": 100})
        simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100})
        changed = OrderPlan(
            plan.plan_id, plan.plan_basis_hash, plan.account_snapshot_ref, plan.source_ids,
            ({"instrument": "QQQ", "quantity": 2},), plan.reservations, plan.expires_at,
        )
        with self.assertRaisesRegex(ValueError, "reused"):
            simulator.execute(changed, datetime(2024, 1, 3, 16, tzinfo=timezone.utc), {"QQQ": 100})

    def test_nonfinite_execution_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            PaperSimulator(EventLedger(10_000), fee_bps=float("nan"))
        ledger = EventLedger(10_000)
        simulator = PaperSimulator(ledger)
        plan = self.plan(ledger, [{"instrument": "QQQ", "quantity": 1, "max_price": float("nan")}])
        with self.assertRaisesRegex(ValueError, "max_price"):
            simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100})

    def test_v3_state_detects_content_and_event_chain_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "simulator.json"
            simulator = PaperSimulator(EventLedger(100))
            simulator.ledger.apply(
                LedgerEvent("deposit", "2024-01-03T20:00:00Z", "external_cash", {"amount": "1"})
            )
            simulator.save(state)
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "paper_simulator.v3")
            self.assertEqual(payload["state_revision"], 1)
            payload["events"][0]["payload"]["amount"] = "2"
            state.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content hash"):
                PaperSimulator.load(state)
            payload["content_sha256"] = canonical_hash(
                {key: value for key, value in payload.items() if key != "content_sha256"}
            )
            state.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "event chain"):
                PaperSimulator.load(state)

    def test_stale_writer_is_rejected_and_previous_version_is_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "simulator.json"
            PaperSimulator(EventLedger(100)).save(state)
            first = PaperSimulator.load(state)
            stale = PaperSimulator.load(state)
            first.ledger.apply(
                LedgerEvent("first", "2024-01-03T20:00:00Z", "external_cash", {"amount": "1"})
            )
            previous_hash = first.content_sha256
            first.save(state)
            archived = state.with_name(f".{state.name}.history") / f"{previous_hash}.json"
            self.assertTrue(archived.is_file())
            stale.ledger.apply(
                LedgerEvent("stale", "2024-01-03T20:01:00Z", "external_cash", {"amount": "1"})
            )
            with self.assertRaisesRegex(ConcurrentStateError, "changed"):
                stale.save(state)
            restored = PaperSimulator.load(state)
            self.assertEqual(restored.ledger.state.settled_cash, 101)
            self.assertEqual(restored.state_revision, 2)

    def test_transaction_failure_does_not_change_persisted_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "simulator.json"
            store = SimulatorStateStore(state)
            original = store.create(PaperSimulator(EventLedger(100)))
            original_hash = original.content_sha256

            def fail(simulator):
                simulator.ledger.apply(
                    LedgerEvent("never", "2024-01-03T20:00:00Z", "external_cash", {"amount": "1"})
                )
                raise RuntimeError("injected failure")

            with self.assertRaisesRegex(RuntimeError, "injected"):
                store.transact(fail)
            restored = store.load()
            self.assertEqual(restored.content_sha256, original_hash)
            self.assertEqual(restored.ledger.state.settled_cash, 100)

    def test_cross_process_transactions_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "simulator.json"
            SimulatorStateStore(state).create(PaperSimulator(EventLedger(100)))
            context = multiprocessing.get_context("fork")
            workers = [
                context.Process(target=_concurrent_deposit, args=(str(state), f"deposit-{index}"))
                for index in range(4)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            restored = SimulatorStateStore(state).load()
            self.assertEqual(restored.ledger.state.settled_cash, 104)
            self.assertEqual(restored.state_revision, 5)

    def test_v2_state_is_readable_and_upgrades_on_save(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "simulator.json"
            legacy = {
                "schema_version": "paper_simulator.v2",
                "initial_cash": "100",
                "fee_rate": "0.001",
                "events": [],
                "results": {},
                "progress": {},
            }
            state.write_text(json.dumps(legacy), encoding="utf-8")
            simulator = PaperSimulator.load(state)
            self.assertEqual(simulator.state_revision, 0)
            simulator.save(state)
            upgraded = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(upgraded["schema_version"], "paper_simulator.v3")
            self.assertEqual(upgraded["state_revision"], 1)


if __name__ == "__main__":
    unittest.main()
