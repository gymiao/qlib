import unittest

import pandas as pd

from apps.quant_research.option_backtest import run_bear_put_spread, run_fixed_protective_put, run_rolling_protective_put


class ProtectivePutBacktestTest(unittest.TestCase):
    def test_historical_quotes_drive_marks_and_expiry(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        prices = pd.Series([100, 98, 94, 90, 88], index=dates, name="SPY")
        expiration = pd.Timestamp(dates[-1].date(), tz="UTC") + pd.Timedelta(hours=21)
        quotes = pd.DataFrame({
            "contract_id": ["SPY-P95"] * 4,
            "underlying": ["SPY"] * 4,
            "quote_ts": pd.to_datetime([f"{date.date()}T20:00:00Z" for date in dates[:4]]),
            "available_at": pd.to_datetime([f"{date.date()}T20:05:00Z" for date in dates[:4]]),
            "expiration": [expiration] * 4,
            "strike": [95] * 4,
            "bid": [2, 3, 5, 7], "ask": [2.2, 3.2, 5.2, 7.2],
            "underlying_price": [100, 98, 94, 90], "multiplier": [100] * 4,
        })
        report = run_fixed_protective_put(prices, quotes, "SPY", 100, 1000, 500)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["quote_mode"], "historical_bid_ask")
        self.assertTrue(report["equity_curve"]["nav"].notna().all())
        self.assertIn("long_put_exercise", {event["kind"] for event in report["events"]})
        self.assertEqual(report["equity_curve"].iloc[-1]["nav"], 10_279.35)

    def test_insufficient_budget_returns_explicit_infeasible_result(self):
        dates = pd.bdate_range("2024-01-02", periods=2)
        quotes = pd.DataFrame({
            "contract_id": ["SPY-P95"], "underlying": ["SPY"],
            "quote_ts": pd.to_datetime(["2024-01-02T20:00:00Z"]),
            "available_at": pd.to_datetime(["2024-01-02T20:05:00Z"]),
            "expiration": pd.to_datetime(["2024-03-01T21:00:00Z"]),
            "strike": [95], "bid": [2], "ask": [2.2],
            "underlying_price": [100], "multiplier": [100],
        })
        report = run_fixed_protective_put(pd.Series([100, 101], index=dates), quotes, "SPY", 100, 1000, 100)
        self.assertEqual(report["status"], "infeasible")
        self.assertEqual(report["selection"]["reason_code"], "BUDGET_BELOW_ONE_CONTRACT")

    def test_rolling_put_closes_at_bid_and_opens_replacement_at_ask(self):
        dates = pd.bdate_range("2024-01-02", periods=6)
        expirations = [
            pd.Timestamp("2024-01-08T21:00:00Z"),
            pd.Timestamp("2024-02-16T21:00:00Z"),
        ]
        records = []
        for date in dates:
            for contract, expiration, strike, bid, ask in (
                ("SPY-P95-JAN", expirations[0], 95, 2.0, 2.2),
                ("SPY-P95-FEB", expirations[1], 95, 3.0, 3.3),
            ):
                records.append({
                    "contract_id": contract, "underlying": "SPY",
                    "quote_ts": pd.Timestamp(f"{date.date()}T20:00:00Z"),
                    "available_at": pd.Timestamp(f"{date.date()}T20:05:00Z"),
                    "expiration": expiration, "strike": strike, "bid": bid, "ask": ask,
                    "underlying_price": 100, "multiplier": 100,
                })
        report = run_rolling_protective_put(
            pd.Series([100, 99, 98, 97, 96, 95], index=dates), pd.DataFrame(records),
            "SPY", 100, 2000, 1000, roll_before_dte=4, target_dte=7,
        )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(report["rolls"]), 1)
        self.assertEqual(report["rolls"].iloc[0]["closed_contract"], "SPY-P95-JAN")
        self.assertEqual(report["rolls"].iloc[0]["close_bid"], 2.0)
        self.assertEqual(report["rolls"].iloc[0]["open_ask"], 3.3)
        self.assertEqual(report["quote_quality"]["coverage"], 1.0)

    def test_rolling_quote_gap_is_retained_and_fails_quality_gate(self):
        dates = pd.bdate_range("2024-01-02", periods=3)
        quotes = pd.DataFrame({
            "contract_id": ["SPY-P95"], "underlying": ["SPY"],
            "quote_ts": pd.to_datetime(["2024-01-02T20:00:00Z"]),
            "available_at": pd.to_datetime(["2024-01-02T20:05:00Z"]),
            "expiration": pd.to_datetime(["2024-03-01T21:00:00Z"]),
            "strike": [95], "bid": [2], "ask": [2.2], "underlying_price": [100], "multiplier": [100],
        })
        report = run_rolling_protective_put(
            pd.Series([100, 99, 98], index=dates), quotes, "SPY", 100, 1000, 500,
            min_quote_coverage=0.8,
        )
        self.assertEqual(len(report["equity_curve"]), 3)
        self.assertEqual(report["status"], "quality_failed")
        self.assertEqual(len(report["quote_quality"]["unavailable_dates"]), 2)

    def test_bear_put_spread_uses_bid_ask_and_physical_expiry(self):
        dates = pd.bdate_range("2024-01-02", periods=4)
        expiration = pd.Timestamp(f"{dates[-1].date()}T21:00:00Z")
        records = []
        for date in dates:
            records.extend([
                {"contract_id": "SPY-P100", "underlying": "SPY", "quote_ts": pd.Timestamp(f"{date.date()}T20:00:00Z"), "available_at": pd.Timestamp(f"{date.date()}T20:05:00Z"), "expiration": expiration, "strike": 100, "bid": 7.8, "ask": 8.0, "underlying_price": 100, "multiplier": 100},
                {"contract_id": "SPY-P90", "underlying": "SPY", "quote_ts": pd.Timestamp(f"{date.date()}T20:00:00Z"), "available_at": pd.Timestamp(f"{date.date()}T20:05:00Z"), "expiration": expiration, "strike": 90, "bid": 2.0, "ask": 2.2, "underlying_price": 100, "multiplier": 100},
            ])
        report = run_bear_put_spread(
            pd.Series([100, 95, 85, 80], index=dates), pd.DataFrame(records), "SPY",
            "SPY-P100", "SPY-P90", 1, 20_000,
        )
        self.assertEqual(report["status"], "complete")
        kinds = [event["kind"] for event in report["events"]]
        self.assertIn("short_put_assignment", kinds)
        self.assertIn("long_put_exercise", kinds)

    def test_spread_long_leg_without_deliverable_is_explicit_failure(self):
        dates = pd.bdate_range("2024-01-02", periods=2)
        expiration = pd.Timestamp(f"{dates[-1].date()}T21:00:00Z")
        quotes = pd.DataFrame([
            {"contract_id": contract, "underlying": "SPY", "quote_ts": pd.Timestamp("2024-01-02T20:00:00Z"), "available_at": pd.Timestamp("2024-01-02T20:05:00Z"), "expiration": expiration, "strike": strike, "bid": bid, "ask": ask, "underlying_price": 100, "multiplier": 100}
            for contract, strike, bid, ask in (("SPY-P100", 100, 7.8, 8.0), ("SPY-P90", 90, 2.0, 2.2))
        ])
        report = run_bear_put_spread(
            pd.Series([100, 95], index=dates), quotes, "SPY", "SPY-P100", "SPY-P90", 1, 20_000,
        )
        self.assertEqual(report["status"], "lifecycle_failed")
        self.assertEqual(report["reason_codes"], ["MISSING_DELIVERABLE_UNDERLYING"])


if __name__ == "__main__":
    unittest.main()
