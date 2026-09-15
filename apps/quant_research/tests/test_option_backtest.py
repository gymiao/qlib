import unittest

import pandas as pd

from apps.quant_research.option_backtest import (
    run_bear_put_spread,
    run_fixed_covered_call,
    run_fixed_protective_put,
    run_rolling_protective_put,
)


class ProtectivePutBacktestTest(unittest.TestCase):
    def test_fixed_covered_call_uses_bid_marks_liability_and_assigns_shares(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        expiration = pd.Timestamp(f"{dates[-1].date()}T21:00:00Z")
        quotes = pd.DataFrame({
            "contract_id": ["SPY-C105"] * 4,
            "underlying": ["SPY"] * 4,
            "option_type": ["call"] * 4,
            "listed_at": pd.to_datetime(["2023-12-01T00:00:00Z"] * 4),
            "last_trade_at": [expiration - pd.Timedelta(hours=1)] * 4,
            "exercise_style": ["american"] * 4,
            "settlement_type": ["physical"] * 4,
            "deliverable_instrument_id": ["SPY"] * 4,
            "quote_ts": pd.to_datetime([f"{date.date()}T20:00:00Z" for date in dates[:4]]),
            "available_at": pd.to_datetime([f"{date.date()}T20:05:00Z" for date in dates[:4]]),
            "expiration": [expiration] * 4,
            "strike": [105] * 4,
            "bid": [2, 3, 4, 5], "ask": [2.2, 3.2, 4.2, 5.2],
            "underlying_price": [100, 102, 104, 106], "multiplier": [100] * 4,
            "source": ["vendor-a"] * 4, "source_kind": ["historical_observed"] * 4,
        })
        report = run_fixed_covered_call(
            pd.Series([100, 102, 104, 106, 110], index=dates),
            quotes, "SPY", 100, 1_000,
        )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["quote_mode"], "historical_bid_ask")
        self.assertEqual(report["equity_curve"].iloc[-1]["shares"], 0)
        self.assertEqual(report["equity_curve"].iloc[-1]["nav"], 11_699.35)
        self.assertIn("short_call_assignment", {event["kind"] for event in report["events"]})
        synthetic = quotes.copy()
        synthetic["source"] = "synthetic_fixture"
        synthetic["source_kind"] = "synthetic"
        downgraded = run_fixed_covered_call(
            pd.Series([100, 102, 104, 106, 110], index=dates),
            synthetic, "SPY", 100, 1_000,
        )
        self.assertEqual(downgraded["quote_mode"], "synthetic_bid_ask_fixture")
        self.assertEqual(downgraded["evidence_capability"], "scenario_only")
        for column, value in (("settlement_type", "cash"), ("deliverable_instrument_id", "SPY-ADJUSTED")):
            unsupported = quotes.copy()
            unsupported[column] = value
            with self.subTest(column=column), self.assertRaises(ValueError):
                run_fixed_covered_call(
                    pd.Series([100, 102, 104, 106, 110], index=dates),
                    unsupported, "SPY", 100, 1_000,
                )
        incomplete = quotes.drop(columns=["listed_at"])
        unverified = run_fixed_covered_call(
            pd.Series([100, 102, 104, 106, 110], index=dates), incomplete, "SPY", 100, 1_000,
        )
        self.assertEqual(unverified["evidence_capability"], "scenario_only")

    def test_fixed_covered_call_rejects_missing_calls_and_partial_lot(self):
        dates = pd.bdate_range("2024-01-02", periods=2)
        put_only = pd.DataFrame({
            "contract_id": ["SPY-P95"], "underlying": ["SPY"], "option_type": ["put"],
            "quote_ts": pd.to_datetime(["2024-01-02T20:00:00Z"]),
            "available_at": pd.to_datetime(["2024-01-02T20:05:00Z"]),
            "expiration": pd.to_datetime(["2024-03-01T21:00:00Z"]),
            "strike": [95], "bid": [2], "ask": [2.2],
            "underlying_price": [100], "multiplier": [100],
        })
        no_call = run_fixed_covered_call(pd.Series([100, 101], index=dates), put_only, "SPY", 100, 1000)
        self.assertEqual(no_call["reason_codes"], ["NO_EXPLICIT_CALL_QUOTES"])
        calls = put_only.copy()
        calls["contract_id"] = "SPY-C105"
        calls["option_type"] = "call"
        calls["strike"] = 105
        partial = run_fixed_covered_call(pd.Series([100, 101], index=dates), calls, "SPY", 50, 1000)
        self.assertEqual(partial["selection"]["reason_code"], "INSUFFICIENT_COVERED_SHARES")

    def test_fixed_covered_call_applies_observed_early_assignment(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        expiration = pd.Timestamp(f"{dates[-1].date()}T21:00:00Z")
        quotes = pd.DataFrame({
            "contract_id": ["SPY-C105"] * 4, "underlying": ["SPY"] * 4,
            "option_type": ["call"] * 4,
            "listed_at": pd.to_datetime(["2023-12-01T00:00:00Z"] * 4),
            "quote_ts": pd.to_datetime([f"{day.date()}T20:00:00Z" for day in dates[:4]]),
            "available_at": pd.to_datetime([f"{day.date()}T20:05:00Z" for day in dates[:4]]),
            "last_trade_at": [expiration - pd.Timedelta(hours=1)] * 4,
            "expiration": [expiration] * 4, "strike": [105] * 4,
            "bid": [2, 3, 4, 5], "ask": [2.2, 3.2, 4.2, 5.2],
            "underlying_price": [100, 102, 106, 107], "multiplier": [100] * 4,
            "exercise_style": ["american"] * 4, "settlement_type": ["physical"] * 4,
            "deliverable_instrument_id": ["SPY"] * 4,
            "source": ["vendor-a"] * 4, "source_kind": ["historical_observed"] * 4,
        })
        assignments = pd.DataFrame([{
            "event_id": "assign-1", "contract_id": "SPY-C105",
            "event_type": "early_assignment",
            "effective_at": f"{dates[2].date()}T13:00:00Z",
            "available_at": f"{dates[2].date()}T13:05:00Z",
            "contracts": 1, "source": "broker-history",
            "source_kind": "historical_observed",
        }])
        report = run_fixed_covered_call(
            pd.Series([100, 102, 106, 107, 110], index=dates), quotes, "SPY", 100, 1_000,
            assignment_events=assignments,
        )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["equity_curve"].iloc[2]["shares"], 0)
        self.assertEqual(report["assignment_evidence"]["capability"], "observed_historical_events")
        self.assertIn("short_call_early_assignment", {event["kind"] for event in report["events"]})

        partial = run_fixed_covered_call(
            pd.Series([100, 102, 106, 107, 110], index=dates), quotes, "SPY", 200, 1_000,
            assignment_events=assignments,
        )
        self.assertEqual(partial["equity_curve"].iloc[2]["shares"], 100)
        self.assertEqual(partial["equity_curve"].iloc[-1]["shares"], 0)
        self.assertEqual(
            [event["kind"] for event in partial["events"]].count("short_call_assignment"), 1
        )

        excessive = assignments.copy()
        excessive["contracts"] = 2
        with self.assertRaisesRegex(ValueError, "exceeds open contracts"):
            run_fixed_covered_call(
                pd.Series([100, 102, 106, 107, 110], index=dates), quotes, "SPY", 100, 1_000,
                assignment_events=excessive,
            )

    def test_historical_quotes_drive_marks_and_expiry(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        prices = pd.Series([100, 98, 94, 90, 88], index=dates, name="SPY")
        expiration = pd.Timestamp(dates[-1].date(), tz="UTC") + pd.Timedelta(hours=21)
        quotes = pd.DataFrame({
            "contract_id": ["SPY-P95"] * 4,
            "underlying": ["SPY"] * 4,
            "option_type": ["put"] * 4,
            "listed_at": pd.to_datetime(["2023-12-01T00:00:00Z"] * 4),
            "last_trade_at": [expiration - pd.Timedelta(hours=1)] * 4,
            "exercise_style": ["american"] * 4,
            "settlement_type": ["physical"] * 4,
            "deliverable_instrument_id": ["SPY"] * 4,
            "quote_ts": pd.to_datetime([f"{date.date()}T20:00:00Z" for date in dates[:4]]),
            "available_at": pd.to_datetime([f"{date.date()}T20:05:00Z" for date in dates[:4]]),
            "expiration": [expiration] * 4,
            "strike": [95] * 4,
            "bid": [2, 3, 5, 7], "ask": [2.2, 3.2, 5.2, 7.2],
            "underlying_price": [100, 98, 94, 90], "multiplier": [100] * 4,
            "source": ["vendor-a"] * 4, "source_kind": ["historical_observed"] * 4,
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
