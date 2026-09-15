from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from apps.quant_research.option_quotes import (
    available_chain,
    load_option_market_data,
    load_option_quotes,
    select_fixed_covered_call,
    select_fixed_protective_put,
)


class OptionQuoteTest(unittest.TestCase):
    def quotes(self):
        return pd.DataFrame({
            "contract_id": ["SPY-P95", "SPY-P90"],
            "underlying": ["SPY", "SPY"],
            "listed_at": pd.to_datetime(["2023-12-01T14:30:00Z"] * 2),
            "quote_ts": pd.to_datetime(["2024-01-02T20:00:00Z", "2024-01-02T20:00:00Z"]),
            "available_at": pd.to_datetime(["2024-01-02T20:05:00Z", "2024-01-02T20:05:00Z"]),
            "last_trade_at": pd.to_datetime(["2024-03-01T20:00:00Z"] * 2),
            "expiration": pd.to_datetime(["2024-03-01T21:00:00Z", "2024-03-01T21:00:00Z"]),
            "option_type": ["put", "put"],
            "strike": [95, 90], "bid": [2.8, 1.5], "ask": [3.0, 1.7],
            "underlying_price": [100, 100], "multiplier": [100, 100],
            "source": ["vendor-a", "vendor-a"],
            "source_kind": ["historical_observed", "historical_observed"],
            "deliverable_instrument_id": ["SPY", "SPY"],
            "exercise_style": ["american", "american"],
            "settlement_type": ["physical", "physical"],
        })

    def test_d02_future_quote_is_not_visible(self):
        quotes = self.quotes()
        before = available_chain(quotes, "SPY", datetime(2024, 1, 2, 20, 1, tzinfo=timezone.utc))
        after = available_chain(quotes, "SPY", datetime(2024, 1, 2, 20, 6, tzinfo=timezone.utc))
        self.assertTrue(before.empty)
        self.assertEqual(len(after), 2)

    def test_chain_rejects_unlisted_and_no_longer_tradable_contracts(self):
        quotes = self.quotes()
        decision = pd.Timestamp("2024-01-02T20:06:00Z")
        quotes.loc[0, "listed_at"] = decision + pd.Timedelta(minutes=1)
        quotes.loc[1, "last_trade_at"] = decision - pd.Timedelta(minutes=1)
        self.assertTrue(available_chain(quotes, "SPY", decision).empty)

    def test_loader_rejects_missing_lifecycle_and_source_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.csv"
            for column in ("listed_at", "last_trade_at", "deliverable_instrument_id", "source"):
                quotes = self.quotes()
                quotes.loc[0, column] = None
                quotes.to_csv(path, index=False)
                with self.subTest(column=column), self.assertRaisesRegex(ValueError, "invalid"):
                    load_option_quotes(path)

    def test_o03_budget_and_integer_contracts(self):
        chain = available_chain(self.quotes(), "SPY", datetime(2024, 1, 2, 20, 6, tzinfo=timezone.utc))
        rejected = select_fixed_protective_put(chain, 100, 100)
        selected = select_fixed_protective_put(chain, 250, 1000)
        self.assertEqual(rejected.reason_code, "BUDGET_BELOW_ONE_CONTRACT")
        self.assertEqual(selected.contract_id, "SPY-P95")
        self.assertEqual(selected.contracts, 2)
        self.assertEqual(selected.nominal_coverage, 0.8)

    def test_covered_call_filters_calls_and_requires_full_share_coverage(self):
        quotes = self.quotes()
        call = quotes.iloc[[0]].copy()
        call["contract_id"] = "SPY-C105"
        call["option_type"] = "call"
        call["strike"] = 105
        call["bid"] = 1.5
        call["ask"] = 1.7
        chain = pd.concat([quotes, call], ignore_index=True)
        selected = select_fixed_covered_call(chain, 250)
        self.assertEqual(selected.contract_id, "SPY-C105")
        self.assertEqual(selected.contracts, 2)
        self.assertEqual(selected.nominal_coverage, 0.8)
        rejected = select_fixed_covered_call(chain, 50)
        self.assertEqual(rejected.reason_code, "INSUFFICIENT_COVERED_SHARES")

    def test_loader_rejects_crossed_market(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.csv"
            quotes = self.quotes()
            quotes.loc[0, "bid"] = 4
            quotes.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_option_quotes(path)

    def test_loader_rejects_nonfinite_values_and_fractional_multiplier(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.csv"
            quotes = self.quotes()
            quotes.loc[0, "bid"] = float("inf")
            quotes.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_option_quotes(path)
            quotes = self.quotes()
            quotes["multiplier"] = quotes["multiplier"].astype(float)
            quotes.loc[0, "multiplier"] = 100.5
            quotes.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_option_quotes(path)

    def test_canonical_contract_and_quote_tables_join_with_source_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = pd.DataFrame({
                "contract_id": ["SPY-C105"], "underlying_id": ["inst-spy"],
                "listed_at": ["2023-12-01T14:30:00Z"], "expiration": ["2024-03-01T21:00:00Z"],
                "last_trade_at": ["2024-03-01T20:00:00Z"], "strike": [105],
                "option_type": ["call"], "multiplier": [100],
                "deliverable_instrument_id": ["inst-spy"], "exercise_style": ["american"],
                "settlement_type": ["physical"], "source": ["vendor-contracts"],
                "source_kind": ["historical_observed"],
            })
            quotes = pd.DataFrame({
                "contract_id": ["SPY-C105"], "quote_ts": ["2024-01-02T20:00:00Z"],
                "available_at": ["2024-01-02T20:05:00Z"], "bid": [2], "ask": [2.2],
                "underlying_price": [100], "source": ["vendor-quotes"],
                "source_kind": ["historical_observed"],
            })
            contract_file, quote_file = root / "contracts.csv", root / "quotes.csv"
            contracts.to_csv(contract_file, index=False)
            quotes.to_csv(quote_file, index=False)
            joined = load_option_market_data(contract_file, quote_file)
            self.assertEqual(joined.iloc[0]["underlying"], "inst-spy")
            self.assertEqual(joined.iloc[0]["option_type"], "call")
            self.assertEqual(joined.iloc[0]["source_kind"], "historical_observed")
            contracts.loc[0, "source"] = "synthetic_demo"
            contracts.loc[0, "source_kind"] = "synthetic"
            contracts.to_csv(contract_file, index=False)
            downgraded = load_option_market_data(contract_file, quote_file)
            self.assertEqual(downgraded.iloc[0]["source_kind"], "scenario_only")


if __name__ == "__main__":
    unittest.main()
