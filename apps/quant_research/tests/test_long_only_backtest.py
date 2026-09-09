import unittest

import numpy as np
import pandas as pd

from apps.quant_research.long_only_backtest import run_long_only_backtest


class LongOnlyBacktestTest(unittest.TestCase):
    def fixture(self):
        dates = pd.bdate_range("2024-01-02", periods=30)
        symbols = ["A", "B", "C", "D"]
        prices = pd.DataFrame({symbol: 100 + np.arange(30) * (i + 1) / 10 for i, symbol in enumerate(symbols)}, index=dates)
        score_index = pd.MultiIndex.from_product([dates[:-1], symbols], names=["datetime", "instrument"])
        values = np.tile([4.0, 3.0, 2.0, 1.0], len(dates) - 1)
        return dates, prices, pd.Series(values, index=score_index), pd.Series(100 + np.arange(30) / 10, index=dates)

    def test_rebalances_next_day_and_charges_actual_trades(self):
        dates, prices, scores, benchmark = self.fixture()
        report = run_long_only_backtest(scores, prices, benchmark, top_k=2, rebalance_every=5, max_weight=0.45)
        self.assertEqual(report["status"], "complete")
        self.assertTrue((report["orders"]["execution_date"] > report["orders"]["signal_date"]).all())
        self.assertTrue((report["orders"]["fee"] > 0).all())
        self.assertTrue(np.isfinite(list(report["metrics"].values())).all())
        self.assertGreater(report["equity_curve"]["turnover"].max(), 0)

    def test_future_scores_do_not_change_prior_account_history(self):
        dates, prices, scores, benchmark = self.fixture()
        changed = scores.copy()
        changed.loc[(slice(dates[15], None), slice(None))] *= -1
        base = run_long_only_backtest(scores, prices, benchmark, top_k=2, rebalance_every=5, max_weight=0.45)["equity_curve"]
        stressed = run_long_only_backtest(changed, prices, benchmark, top_k=2, rebalance_every=5, max_weight=0.45)["equity_curve"]
        pd.testing.assert_frame_equal(base.loc[base["date"] <= dates[15]], stressed.loc[stressed["date"] <= dates[15]])


if __name__ == "__main__":
    unittest.main()
