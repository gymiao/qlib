from datetime import datetime, timezone
import unittest

from apps.quant_research.contracts import SignalBatch
from apps.quant_research.ledger import EventLedger
from apps.quant_research.simulator import PaperSimulator
from apps.quant_research.workflow import plan_signal_orders


class WorkflowTest(unittest.TestCase):
    def signal(self):
        return SignalBatch(
            "signal-1", "long-only", "model", "1",
            "2024-01-02T21:00:00Z", "2024-01-03T21:00:00Z",
            "relative_return_score", "model-1", 5,
            {"A": 3.0, "B": 2.0, "C": 1.0},
        )

    def test_current_signal_becomes_idempotent_filled_plan(self):
        now = datetime(2024, 1, 3, 15, tzinfo=timezone.utc)
        ledger = EventLedger(10_000)
        plan = plan_signal_orders(self.signal(), ledger, {"A": 100, "B": 100, "C": 100}, now, top_k=2, max_weight=0.4)
        result = PaperSimulator(ledger).execute(plan, now, {"A": 100, "B": 100, "C": 100})
        self.assertEqual(result.status, "filled")
        self.assertEqual(set(ledger.state.positions), {"A", "B"})
        self.assertEqual(sum(ledger.state.positions.values()), 80)

    def test_expired_signal_cannot_create_plan(self):
        with self.assertRaisesRegex(ValueError, "not currently valid"):
            plan_signal_orders(
                self.signal(), EventLedger(10_000), {"A": 100, "B": 100, "C": 100},
                datetime(2024, 1, 4, tzinfo=timezone.utc), top_k=2,
            )

    def test_naive_planning_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            plan_signal_orders(
                self.signal(), EventLedger(10_000), {"A": 100, "B": 100, "C": 100},
                datetime(2024, 1, 3, 15), top_k=2,
            )


if __name__ == "__main__":
    unittest.main()
