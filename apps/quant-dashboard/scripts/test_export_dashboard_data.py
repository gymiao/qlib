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
            data = export(root, output, annual_risk_free_rate=0.03)

            self.assertEqual(len(data["equity_curve"]), 2)
            self.assertAlmostEqual(data["equity_curve"][-1]["strategy"], 1.045)
            self.assertEqual([row["instrument"] for row in data["latest_holdings"]], ["AAA", "BBB"])
            self.assertIn("risk_free", data["equity_curve"][-1])
            self.assertEqual(data["risk_free"]["average_annual_rate"], 0.03)
            self.assertEqual(data["risk_free"]["proxy"], "U.S. 3-Month Treasury")
            self.assertEqual(data["risk_free"]["method"], "fixed_assumption")
            self.assertIn("alpha_annualized", data["capm"])
            self.assertIn("beta", data["capm"])
            self.assertEqual(len(data["annual_returns"]), 1)
            self.assertEqual(data["annual_returns"][0]["year"], 2025)
            self.assertAlmostEqual(data["annual_returns"][0]["strategy_return"], 0.045)
            self.assertFalse(data["annual_returns"][0]["complete_year"])
            self.assertTrue(output.exists())

    def test_exit_date_controls_equity_and_annual_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(json.dumps({"configuration": {"rebalance_every_trading_days": 5}}))
            (root / "backtest.csv").write_text(
                "signal_date,entry_date,exit_date,net_return,benchmark_return,excess_return,turnover\n"
                "2024-12-30,2024-12-31,2025-01-08,0.1,0.05,0.05,1.0\n"
            )
            (root / "holdings.csv").write_text("signal_date,instrument,score,forward_return\n2024-12-30,AAA,1,0.1\n")
            (root / "rank_ic.csv").write_text("datetime,rank_ic\n2024-12-30,0.1\n")
            data = export(root, root / "dashboard.json")
            self.assertEqual(data["equity_curve"][0]["date"], "2025-01-08")
            self.assertEqual(data["annual_returns"][0]["year"], 2025)
            self.assertFalse(data["current_signal_available"])

    def test_lambdarank_score_is_not_labeled_as_return(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(json.dumps({"configuration": {"objective": "lambdarank"}}))
            (root / "backtest.csv").write_text("signal_date,net_return,benchmark_return,excess_return,turnover\n2025-01-01,0.1,0.05,0.05,1\n")
            (root / "holdings.csv").write_text("signal_date,instrument,score,forward_return\n2025-01-01,AAA,12.5,0.1\n")
            (root / "rank_ic.csv").write_text("datetime,rank_ic\n2025-01-01,0.1\n")
            data = export(root, root / "dashboard.json")
            self.assertEqual(data["score_semantics"], "ranking_score")
            self.assertEqual(data["latest_holdings"][0]["score"], 12.5)


if __name__ == "__main__":
    unittest.main()
