from pathlib import Path
import tempfile
import unittest

import pandas as pd

from apps.quant_research.canonical_bars import load_canonical_market_data


class CanonicalBarsTest(unittest.TestCase):
    def test_maps_stable_ids_by_time_and_applies_adjustment_factor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            pd.DataFrame(
                [
                    {
                        "instrument_id": "company",
                        "timestamp": "2024-01-02T21:00:00Z",
                        "available_at": "2024-01-02T21:05:00Z",
                        "open": 100,
                        "high": 104,
                        "low": 98,
                        "close": 102,
                        "volume": 1000,
                        "adjustment_factor": 0.5,
                    },
                    {
                        "instrument_id": "company",
                        "timestamp": "2024-01-03T21:00:00Z",
                        "available_at": "2024-01-03T21:05:00Z",
                        "open": 52,
                        "high": 54,
                        "low": 51,
                        "close": 53,
                        "volume": 2000,
                        "adjustment_factor": 1,
                    },
                    {
                        "instrument_id": "benchmark",
                        "timestamp": "2024-01-02T21:00:00Z",
                        "available_at": "2024-01-02T21:05:00Z",
                        "open": 400,
                        "high": 405,
                        "low": 398,
                        "close": 402,
                        "volume": 3000,
                        "adjustment_factor": 1,
                    },
                ]
            ).to_csv(path, index=False)
            master = pd.DataFrame(
                [
                    {
                        "instrument_id": "company",
                        "symbol": "OLD",
                        "symbol_effective_from": "2020-01-01T00:00:00Z",
                        "symbol_effective_to": "2024-01-03T14:30:00Z",
                    },
                    {
                        "instrument_id": "company",
                        "symbol": "NEW",
                        "symbol_effective_from": "2024-01-03T14:30:00Z",
                        "symbol_effective_to": "",
                    },
                    {
                        "instrument_id": "benchmark",
                        "symbol": "QQQ",
                        "symbol_effective_from": "2020-01-01T00:00:00Z",
                        "symbol_effective_to": "",
                    },
                ]
            )
            stocks, benchmark = load_canonical_market_data(
                path,
                master,
                ["OLD", "NEW"],
                "2024-01-01T00:00:00Z",
                "2024-02-01T00:00:00Z",
            )
        self.assertEqual(list(stocks["instrument"]), ["NEW", "OLD"])
        old = stocks.loc[stocks["instrument"] == "OLD"].iloc[0]
        self.assertEqual(old["Open"], 50)
        self.assertEqual(old["Volume"], 2000)
        self.assertEqual(len(benchmark), 1)
        self.assertEqual(stocks.attrs["data_mode"], "canonical_audited")

    def test_rejects_missing_benchmark(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            pd.DataFrame(
                [
                    {
                        "instrument_id": "company",
                        "timestamp": "2024-01-02T21:00:00Z",
                        "available_at": "2024-01-02T21:05:00Z",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 1000,
                        "adjustment_factor": 1,
                    }
                ]
            ).to_csv(path, index=False)
            master = pd.DataFrame(
                [
                    {
                        "instrument_id": "company",
                        "symbol": "AAA",
                        "symbol_effective_from": "2020-01-01T00:00:00Z",
                        "symbol_effective_to": "",
                    }
                ]
            )
            with self.assertRaisesRegex(ValueError, "benchmark"):
                load_canonical_market_data(
                    path,
                    master,
                    ["AAA"],
                    "2024-01-01T00:00:00Z",
                    "2024-02-01T00:00:00Z",
                )

    def test_rejects_bar_not_available_at_decision_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            pd.DataFrame(
                [
                    {
                        "instrument_id": instrument_id,
                        "timestamp": "2024-01-02T21:00:00Z",
                        "available_at": "2024-01-02T22:00:00Z",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 1000,
                        "adjustment_factor": 1,
                    }
                    for instrument_id in ("company", "benchmark")
                ]
            ).to_csv(path, index=False)
            master = pd.DataFrame(
                [
                    {
                        "instrument_id": "company",
                        "symbol": "AAA",
                        "symbol_effective_from": "2020-01-01T00:00:00Z",
                        "symbol_effective_to": "",
                    },
                    {
                        "instrument_id": "benchmark",
                        "symbol": "QQQ",
                        "symbol_effective_from": "2020-01-01T00:00:00Z",
                        "symbol_effective_to": "",
                    },
                ]
            )
            with self.assertRaisesRegex(ValueError, "decision time"):
                load_canonical_market_data(
                    path,
                    master,
                    ["AAA"],
                    "2024-01-01T00:00:00Z",
                    "2024-02-01T00:00:00Z",
                    decision_time="16:30:00",
                )


if __name__ == "__main__":
    unittest.main()
