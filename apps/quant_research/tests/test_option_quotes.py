from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from apps.quant_research.option_quotes import available_chain, load_option_quotes, select_fixed_protective_put


class OptionQuoteTest(unittest.TestCase):
    def quotes(self):
        return pd.DataFrame({
            "contract_id": ["SPY-P95", "SPY-P90"],
            "underlying": ["SPY", "SPY"],
            "quote_ts": pd.to_datetime(["2024-01-02T20:00:00Z", "2024-01-02T20:00:00Z"]),
            "available_at": pd.to_datetime(["2024-01-02T20:05:00Z", "2024-01-02T20:05:00Z"]),
            "expiration": pd.to_datetime(["2024-03-01T21:00:00Z", "2024-03-01T21:00:00Z"]),
            "strike": [95, 90], "bid": [2.8, 1.5], "ask": [3.0, 1.7],
            "underlying_price": [100, 100], "multiplier": [100, 100],
        })

    def test_d02_future_quote_is_not_visible(self):
        quotes = self.quotes()
        before = available_chain(quotes, "SPY", datetime(2024, 1, 2, 20, 1, tzinfo=timezone.utc))
        after = available_chain(quotes, "SPY", datetime(2024, 1, 2, 20, 6, tzinfo=timezone.utc))
        self.assertTrue(before.empty)
        self.assertEqual(len(after), 2)

    def test_o03_budget_and_integer_contracts(self):
        chain = available_chain(self.quotes(), "SPY", datetime(2024, 1, 2, 20, 6, tzinfo=timezone.utc))
        rejected = select_fixed_protective_put(chain, 100, 100)
        selected = select_fixed_protective_put(chain, 250, 1000)
        self.assertEqual(rejected.reason_code, "BUDGET_BELOW_ONE_CONTRACT")
        self.assertEqual(selected.contract_id, "SPY-P95")
        self.assertEqual(selected.contracts, 2)
        self.assertEqual(selected.nominal_coverage, 0.8)

    def test_loader_rejects_crossed_market(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.csv"
            quotes = self.quotes()
            quotes.loc[0, "bid"] = 4
            quotes.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_option_quotes(path)


if __name__ == "__main__":
    unittest.main()
