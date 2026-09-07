from datetime import datetime, timezone
from pathlib import Path
import unittest

import pandas as pd

from apps.quant_research.integrations.replay import run_signal_replay
from apps.quant_research.integrations.tradingagents import (
    ResearchSignal,
    apply_research_overlay,
    effective_signals,
    load_research_signals,
)


def signal(signal_id, symbol, rating, available_at, valid_until="2024-02-01T00:00:00Z"):
    return ResearchSignal.from_dict({
        "schema_version": "research_signal.v1",
        "signal_id": signal_id,
        "run_id": "run-1",
        "symbol": symbol,
        "benchmark": "QQQ",
        "analysis_as_of": "2024-01-01T00:00:00Z",
        "generated_at": "2024-01-01T00:01:00Z",
        "available_at": available_at,
        "valid_until": valid_until,
        "rating": rating,
        "status": "completed",
        "research_mode": "frozen_fixture",
    })


class TradingAgentsIntegrationTest(unittest.TestCase):
    def test_future_signal_is_not_visible_to_earlier_decision(self):
        later = signal("late", "QQQ", "Sell", "2024-01-04T00:00:00Z")
        active = effective_signals([later], datetime(2024, 1, 3, tzinfo=timezone.utc))
        self.assertEqual(active, {})

    def test_overlay_reduces_weight_to_cash_without_renormalizing(self):
        active = {"QQQ": signal("sell", "QQQ", "Sell", "2024-01-01T00:02:00Z")}
        weights = apply_research_overlay(pd.Series({"QQQ": 0.7, "SPY": 0.3}), active)
        self.assertEqual(weights["QQQ"], 0)
        self.assertAlmostEqual(weights.sum(), 0.3)

    def test_fixture_loads_and_replay_has_no_negative_cash(self):
        fixture = Path("examples/data/research_signals_fixture.jsonl")
        signals = load_research_signals(fixture)
        dates = pd.bdate_range("2020-01-02", periods=90)
        prices = pd.DataFrame({"QQQ": range(100, 190), "SPY": range(200, 290)}, index=dates, dtype=float)
        report = run_signal_replay(prices, pd.Series({"QQQ": 0.7, "SPY": 0.3}), signals)
        self.assertEqual(report["signal_mode"], "frozen_signal_replay_not_historical_llm_performance")
        self.assertTrue((report["orders"]["fee"] >= 0).all())
        self.assertEqual(set(report["equity_curves"]["strategy"]), {"base", "research_overlay"})


if __name__ == "__main__":
    unittest.main()
