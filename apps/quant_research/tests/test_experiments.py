import unittest

import numpy as np
import pandas as pd

from apps.quant_research.experiments import EvaluationWindow, run_baseline_comparison


class BaselineExperimentTest(unittest.TestCase):
    def fixture(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        symbols = ["A", "B", "C", "D"]
        prices = pd.DataFrame(
            {symbol: 100 + np.arange(50) * (index + 1) / 10 for index, symbol in enumerate(symbols)},
            index=dates,
        )
        index = pd.MultiIndex.from_product([dates, symbols], names=["datetime", "instrument"])
        scores = pd.Series(np.tile([1.0, 4.0, 2.0, 3.0], len(dates)), index=index)
        benchmark = pd.Series(100 + np.arange(50) / 10, index=dates)
        return dates, prices, scores, benchmark

    def test_three_baselines_share_policy_and_produce_cross_window_metrics(self):
        dates, prices, scores, benchmark = self.fixture()
        windows = (
            EvaluationWindow("early", str(dates[5].date()), str(dates[29].date())),
            EvaluationWindow("late", str(dates[20].date()), str(dates[-1].date())),
        )
        report = run_baseline_comparison(
            scores, prices, benchmark, windows, top_k=2, momentum_lookback=3,
            rebalance_every=5, max_weight=0.45,
        )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["schema_version"], "baseline_comparison.v2")
        self.assertEqual(set(report["metrics"]["strategy"]), {"equal_weight", "momentum", "model"})
        self.assertEqual(len(report["metrics"]), 6)
        self.assertEqual(len(report["cost_sensitivity"]), 24)
        self.assertEqual(set(report["runs"]["early"]), {"equal_weight", "momentum", "model"})
        self.assertGreater(report["window_evidence"]["early"]["common_signal_dates"], 0)
        self.assertEqual(report["model_evidence"]["status"], "insufficient_evidence")
        self.assertIn("INSUFFICIENT_INDEPENDENT_WINDOWS", report["model_evidence"]["reason_codes"])
        self.assertIn("OVERLAPPING_EVIDENCE_WINDOWS", report["model_evidence"]["reason_codes"])
        equal_holdings = report["runs"]["early"]["equal_weight"]["holdings"]
        self.assertEqual(equal_holdings.groupby("signal_date")["instrument"].nunique().min(), 4)

        model_costs = report["cost_sensitivity"].query("window == 'early' and strategy == 'model'")
        returns = model_costs.sort_values("cost_bps")["total_return"].to_numpy()
        self.assertTrue(np.all(np.diff(returns) <= 1e-12))

    def test_future_prices_do_not_change_earlier_momentum_orders(self):
        dates, prices, scores, benchmark = self.fixture()
        window = (EvaluationWindow("all", str(dates[5].date()), str(dates[-1].date())),)
        base = run_baseline_comparison(
            scores, prices, benchmark, window, top_k=2, momentum_lookback=3,
            rebalance_every=5, max_weight=0.45,
        )["runs"]["all"]["momentum"]["orders"]
        changed = prices.copy()
        changed.loc[dates[30]:] = changed.loc[dates[30]:] * [4, 3, 2, 1]
        stressed = run_baseline_comparison(
            scores, changed, benchmark, window, top_k=2, momentum_lookback=3,
            rebalance_every=5, max_weight=0.45,
        )["runs"]["all"]["momentum"]["orders"]
        columns = ["signal_date", "execution_date", "instrument", "quantity"]
        pd.testing.assert_frame_equal(
            base.loc[base["signal_date"] < dates[30], columns].reset_index(drop=True),
            stressed.loc[stressed["signal_date"] < dates[30], columns].reset_index(drop=True),
        )


if __name__ == "__main__":
    unittest.main()
