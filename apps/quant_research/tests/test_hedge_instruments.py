import unittest

import numpy as np
import pandas as pd

from apps.quant_research.hedge_instruments import (
    HedgeInstrumentSpec,
    compare_hedge_instruments,
)


class HedgeInstrumentComparisonTest(unittest.TestCase):
    def test_inverse_etf_and_future_are_comparable_but_short_etf_requires_borrow(self):
        dates = pd.bdate_range("2024-01-02", periods=40)
        benchmark = pd.Series(np.linspace(-0.01, 0.01, len(dates)), index=dates)
        portfolio = 1.2 * benchmark
        instruments = pd.DataFrame({
            "QQQ": benchmark,
            "PSQ": -benchmark + 0.0001,
            "NQ": benchmark - 0.00005,
        }, index=dates)
        report = compare_hedge_instruments(
            portfolio,
            benchmark,
            instruments,
            [
                HedgeInstrumentSpec("QQQ", "etf", 1, 2, borrow_available=False),
                HedgeInstrumentSpec("PSQ", "inverse_etf", -1, 5, annual_expense_ratio=0.0095),
                HedgeInstrumentSpec(
                    "NQ", "future", 1, 1, initial_margin_rate=0.12,
                    maintenance_margin_rate=0.10,
                ),
            ],
            hedge_ratio=0.5,
        )
        self.assertEqual(report["results"]["QQQ"]["status"], "infeasible")
        self.assertEqual(report["results"]["PSQ"]["status"], "evaluated")
        self.assertEqual(report["results"]["NQ"]["initial_capital_requirement_per_portfolio_dollar"], 0.06)
        self.assertLess(report["results"]["NQ"]["tracking_error_annualized"], 0.001)
        self.assertEqual(report["execution_capability"], "none")
        self.assertFalse(report["curves"].empty)

    def test_invalid_specs_and_nonfinite_data_fail_closed(self):
        dates = pd.bdate_range("2024-01-02", periods=2)
        values = pd.Series([0.01, 0.02], index=dates)
        with self.assertRaises(ValueError):
            compare_hedge_instruments(
                values, values, pd.DataFrame({"BAD": values}),
                [HedgeInstrumentSpec("BAD", "future", 1, 1, initial_margin_rate=0.1, maintenance_margin_rate=0.2)],
            )


if __name__ == "__main__":
    unittest.main()
