import unittest
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from apps.quant_research.backtest import HedgeBacktestConfig, run_hedge_comparison
from apps.quant_research.options import black_scholes_call, black_scholes_put, call_delta, put_delta
from apps.quant_research.risk import RiskSnapshot, estimate_factor_exposure, plan_cash_etf_hedge
from apps.quant_research.run_research import main as run_research_main
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
        self.assertGreater(black_scholes_call(100, 100, 1, 0.03, 0.20), 0)
        self.assertGreater(call_delta(100, 100, 1, 0.03, 0.20), 0)

    def test_comparison_outputs_all_strategies_without_nan(self):
        report = run_hedge_comparison(self.prices(), HedgeBacktestConfig())
        self.assertEqual(report["option_data_mode"], "black_scholes_scenario_not_historical_quotes")
        for symbol in ("QQQ", "SPY"):
            self.assertEqual(
                set(report["results"][symbol]),
                {
                    "buy_hold",
                    "cash_reduced",
                    "volatility_target",
                    "dynamic_trend_volatility_hedge",
                    "protective_put_model",
                    "dynamic_protective_put_model",
                    "bear_put_spread_model",
                    "covered_call_model",
                    "collar_model",
                },
            )
            for metrics in report["results"][symbol].values():
                self.assertTrue(all(np.isfinite(value) for value in metrics.values()))
        self.assertFalse(report["option_trades"].empty)
        self.assertEqual(report["schema_version"], "hedge_comparison.v3")
        self.assertEqual(
            set(report["option_trades"]["strategy"]),
            {
                "protective_put_model", "dynamic_protective_put_model",
                "bear_put_spread_model", "covered_call_model", "collar_model",
            },
        )
        spread = report["option_trades"].query(
            "symbol == 'QQQ' and strategy == 'bear_put_spread_model' and action == 'buy_to_open'"
        )
        short = report["option_trades"].query(
            "symbol == 'QQQ' and strategy == 'bear_put_spread_model' and action == 'sell_to_open'"
        )
        self.assertFalse(spread.empty)
        self.assertTrue((spread["strike"].to_numpy() > short["strike"].to_numpy()).all())

    def test_initial_put_values_do_not_depend_on_future_prices(self):
        base = self.prices(periods=80)
        changed = base.copy()
        changed.loc[changed.index[30]:, "QQQ"] *= 0.5
        baseline = run_hedge_comparison(base)["equity_curves"]
        stressed = run_hedge_comparison(changed)["equity_curves"]
        baseline_head = baseline.query("symbol == 'QQQ' and strategy == 'protective_put_model'").head(20)
        stressed_head = stressed.query("symbol == 'QQQ' and strategy == 'protective_put_model'").head(20)
        np.testing.assert_allclose(baseline_head["equity"], stressed_head["equity"])

    def test_new_overlays_and_dynamic_hedge_are_causal(self):
        base = self.prices(periods=100)
        changed = base.copy()
        changed.loc[changed.index[40]:, ["QQQ", "SPY"]] *= 1.5
        baseline = run_hedge_comparison(base)["equity_curves"]
        stressed = run_hedge_comparison(changed)["equity_curves"]
        for strategy in (
            "covered_call_model", "collar_model", "dynamic_trend_volatility_hedge",
            "dynamic_protective_put_model",
            "bear_put_spread_model",
        ):
            left = baseline.query("symbol == 'QQQ' and strategy == @strategy").head(30)
            right = stressed.query("symbol == 'QQQ' and strategy == @strategy").head(30)
            np.testing.assert_allclose(left["equity"], right["equity"])

    def test_cli_exposes_option_and_dynamic_hedge_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prices = self.prices(periods=80).rename_axis("date")
            price_file = root / "prices.csv"
            output = root / "output"
            prices.to_csv(price_file)
            args = [
                "--prices-csv", str(price_file), "--output-dir", str(output),
                "--put-coverage", "0.5", "--put-moneyness", "0.9",
                "--put-spread-width", "0.15",
                "--call-coverage", "0.75", "--call-moneyness", "1.1",
                "--option-dte", "45", "--option-roll-dte", "10",
                "--put-volatility-skew", "2.0", "--call-volatility-skew", "0.75",
                "--dynamic-put-volatility-trigger", "0.18",
                "--dynamic-trend-window", "30", "--dynamic-risk-off-exposure", "0.25",
                "--dynamic-rebalance-threshold", "0.08",
            ]
            with redirect_stdout(StringIO()):
                self.assertEqual(run_research_main(args), 0)
            report = json.loads((output / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(report["configuration"]["call_moneyness"], 1.1)
            self.assertEqual(report["configuration"]["put_dte"], 45)
            self.assertEqual(report["configuration"]["put_spread_width"], 0.15)
            self.assertEqual(report["configuration"]["dynamic_trend_window"], 30)
            self.assertEqual(report["configuration"]["put_volatility_skew"], 2.0)
            self.assertEqual(report["configuration"]["dynamic_put_volatility_trigger"], 0.18)
            self.assertTrue((output / "option_trades.csv").is_file())

    def test_joint_factor_model_avoids_two_independent_market_betas(self):
        prices = self.prices()
        returns = prices.pct_change().dropna()
        portfolio = 0.8 * returns["SPY"] + 0.4 * (returns["QQQ"] - 1.15 * returns["SPY"])
        exposure = estimate_factor_exposure(portfolio, returns)
        self.assertAlmostEqual(exposure.spy_beta, 0.8, places=1)
        self.assertAlmostEqual(exposure.qqq_residual_beta, 0.4, places=1)

    def test_cash_hedge_plan_solves_joint_factors_without_double_hedging(self):
        snapshot = RiskSnapshot(
            as_of="2024-01-02T21:00:00Z",
            factor_definition="SPY_PLUS_QQQ_RESIDUAL_V1",
            qqq_spy_loading=1.2,
            spy_factor_dollars=110_000,
            qqq_residual_dollars=50_000,
            option_delta_dollars=0,
            observations=252,
            quality="valid",
        )
        plan = plan_cash_etf_hedge(
            snapshot, spy_price=500, qqq_price=500,
            current_spy_shares=100, current_qqq_shares=100,
            target_spy_factor_dollars=55_000,
            target_qqq_residual_dollars=25_000,
            deadband_dollars=1,
        )
        self.assertEqual(plan.status, "planned")
        self.assertEqual({row["instrument"]: row["quantity"] for row in plan.orders}, {"QQQ": -50, "SPY": -50})
        self.assertEqual(plan.achieved_spy_factor_dollars, 55_000)
        self.assertEqual(plan.achieved_qqq_residual_dollars, 25_000)
        self.assertEqual(plan.execution_capability, "research_plan_only")

    def test_cash_hedge_plan_reports_long_only_constraint(self):
        snapshot = RiskSnapshot(
            as_of="2024-01-02T21:00:00Z",
            factor_definition="SPY_PLUS_QQQ_RESIDUAL_V1",
            qqq_spy_loading=1.2,
            spy_factor_dollars=20_000,
            qqq_residual_dollars=-10_000,
            option_delta_dollars=-20_000,
            observations=252,
            quality="valid",
        )
        plan = plan_cash_etf_hedge(
            snapshot, spy_price=500, qqq_price=500,
            current_spy_shares=40, current_qqq_shares=20,
            target_spy_factor_dollars=20_000,
            target_qqq_residual_dollars=0,
            deadband_dollars=1,
        )
        self.assertEqual(plan.status, "partially_constrained")
        self.assertIn("QQQ_EXPOSURE_INCREASE_DISABLED", plan.reason_codes)
        self.assertIn("QQQ_TARGET_NOT_REACHED", plan.reason_codes)

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
