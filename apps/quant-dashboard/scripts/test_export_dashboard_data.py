import json
import tempfile
import unittest
from pathlib import Path

from export_dashboard_data import export


class ExportDashboardDataTest(unittest.TestCase):
    def test_export_builds_equity_curve_and_latest_holdings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(
                json.dumps(
                    {
                        "research_only": True,
                        "strategy_net": {},
                        "benchmark_qqq": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "backtest.csv").write_text(
                "signal_date,net_return,benchmark_return,excess_return,turnover\n"
                "2025-01-01,0.1,0.05,0.05,1.0\n"
                "2025-01-08,-0.05,0.01,-0.06,0.4\n",
                encoding="utf-8",
            )
            (root / "holdings.csv").write_text(
                "signal_date,instrument,score,forward_return\n"
                "2025-01-01,OLD,0.5,0.1\n"
                "2025-01-08,BBB,0.4,0.03\n"
                "2025-01-08,AAA,0.8,0.02\n",
                encoding="utf-8",
            )
            (root / "rank_ic.csv").write_text(
                "datetime,rank_ic\n2025-01-01,0.1\n2025-01-02,-0.2\n",
                encoding="utf-8",
            )
            output = root / "dashboard.json"
            data = export(root, output)

            self.assertEqual(len(data["equity_curve"]), 2)
            self.assertAlmostEqual(data["equity_curve"][-1]["strategy"], 1.045)
            self.assertEqual([row["instrument"] for row in data["latest_holdings"]], ["AAA", "BBB"])
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
