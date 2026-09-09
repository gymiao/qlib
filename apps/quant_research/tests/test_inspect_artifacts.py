from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from apps.quant_research.config import ResearchConfig
from apps.quant_research.contracts import OrderPlan
from apps.quant_research.data import create_price_snapshot, load_price_snapshot
from apps.quant_research.engine import run_stage1_comparison
from apps.quant_research.inspect_artifacts import inspect_run, inspect_simulator
from apps.quant_research.ledger import EventLedger
from apps.quant_research.reporting import publish_stage1_report
from apps.quant_research.simulator import PaperSimulator


class ArtifactInspectionTest(unittest.TestCase):
    def test_inspects_verified_run_and_simulator_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "prices.csv"
            dates = pd.bdate_range("2024-01-02", periods=30)
            pd.DataFrame({"date": dates, "QQQ": range(100, 130), "SPY": range(200, 230)}).to_csv(source, index=False)
            config = ResearchConfig()
            snapshot = create_price_snapshot(source, root / "snapshots", "etfs", config.instruments, datetime(2024, 1, 8, tzinfo=timezone.utc))
            _, prices = load_price_snapshot(root / "snapshots", snapshot.snapshot_id)
            run = publish_stage1_report(run_stage1_comparison(prices, config), root / "runs", snapshot, config)
            self.assertEqual(inspect_run(run)["status"], "valid")

            ledger = EventLedger(10_000)
            simulator = PaperSimulator(ledger)
            plan = OrderPlan("plan", ledger.basis_hash(), "snapshot", ("rule",), ({"instrument": "QQQ", "quantity": 1},), {}, "2024-01-03T22:00:00Z")
            simulator.execute(plan, datetime(2024, 1, 3, 15, tzinfo=timezone.utc), {"QQQ": 100})
            state = root / "simulator.json"
            simulator.save(state)
            self.assertEqual(inspect_simulator(state)["counts"], {"filled": 1})


if __name__ == "__main__":
    unittest.main()
