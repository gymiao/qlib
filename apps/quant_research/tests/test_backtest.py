import unittest

import numpy as np
import pandas as pd

from apps.quant_research.backtest import HedgeBacktestConfig, run_hedge_comparison
from apps.quant_research.options import black_scholes_put, put_delta
from apps.quant_research.risk import estimate_factor_exposure
from apps.quant_research.stock_selection import select_long_only


class ResearchBacktestTest(unittest.TestCase):
    @staticmethod
    def prices(periods=260):
        dates = pd.bdate_range("2023-01-02", periods=periods)
        rng = np.random.default_rng(7)
        spy_returns = rng.normal(0.0002, 0.011, periods)
        qqq_returns = 1.15 * spy_returns + rng.normal(0.0001, 0.005, periods)
        return pd.DataFrame(
            {"SPY": 400 * np.cumprod(1 + spy_returns), "QQQ": 300 * np.cumprod(1 + qqq_returns)},
            index=dates,
        )

    def test_put_value_and_delta(self):
        value = black_scholes_put(100, 100, 1, 0.03, 0.20)
        self.assertGreater(value, 0)
        self.assertLess(put_delta(100, 100, 1, 0.03, 0.20), 0)

    def test_comparison_outputs_all_strategies_without_nan(self):
        report = run_hedge_comparison(self.prices(), HedgeBacktestConfig())
        self.assertEqual(report["option_data_mode"], "black_scholes_scenario_not_historical_quotes")
        for symbol in ("QQQ", "SPY"):
            self.assertEqual(
                set(report["results"][symbol]),
                {"buy_hold", "cash_reduced", "volatility_target", "protective_put_model"},
            )
            for metrics in report["results"][symbol].values():
                self.assertTrue(all(np.isfinite(value) for value in metrics.values()))
        self.assertFalse(report["option_trades"].empty)

    def test_initial_put_values_do_not_depend_on_future_prices(self):
        base = self.prices(periods=80)
        changed = base.copy()
        changed.loc[changed.index[30]:, "QQQ"] *= 0.5
        baseline = run_hedge_comparison(base)["equity_curves"]
        stressed = run_hedge_comparison(changed)["equity_curves"]
        baseline_head = baseline.query("symbol == 'QQQ' and strategy == 'protective_put_model'").head(20)
        stressed_head = stressed.query("symbol == 'QQQ' and strategy == 'protective_put_model'").head(20)
        np.testing.assert_allclose(baseline_head["equity"], stressed_head["equity"])

    def test_joint_factor_model_avoids_two_independent_market_betas(self):
        prices = self.prices()
        returns = prices.pct_change().dropna()
        portfolio = 0.8 * returns["SPY"] + 0.4 * (returns["QQQ"] - 1.15 * returns["SPY"])
        exposure = estimate_factor_exposure(portfolio, returns)
        self.assertAlmostEqual(exposure.spy_beta, 0.8, places=1)
        self.assertAlmostEqual(exposure.qqq_residual_beta, 0.4, places=1)

    def test_long_only_buffer_retains_existing_names(self):
        scores = pd.Series({"A": 10, "B": 9, "C": 8, "D": 7, "E": 6})
        weights = select_long_only(scores, top_k=3, previous={"D", "E"}, buffer_multiplier=4 / 3)
        self.assertIn("D", weights.index)
        self.assertNotIn("E", weights.index)
        self.assertAlmostEqual(weights.sum(), 0.45)

    def test_long_only_respects_sector_deviation(self):
        scores = pd.Series({"A": 10, "B": 9, "C": 8, "D": 7, "E": 6})
        sectors = pd.Series({"A": "tech", "B": "tech", "C": "health", "D": "finance", "E": "health"})
        weights = select_long_only(
            scores, top_k=3, max_weight=1 / 3, sectors=sectors,
            benchmark_sector_weights={"tech": 0.34, "health": 0.33, "finance": 0.33},
            max_sector_deviation=0.05,
        )
        self.assertEqual(set(weights.index), {"A", "C", "D"})
        actual = weights.groupby(sectors.loc[weights.index]).sum()
        self.assertLessEqual(actual["tech"], 0.39)


if __name__ == "__main__":
    unittest.main()
