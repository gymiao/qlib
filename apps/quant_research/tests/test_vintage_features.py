from pathlib import Path
import tempfile
import unittest

import pandas as pd

from apps.quant_research.vintage_features import (
    attach_classification_vintages,
    attach_fundamental_vintages,
    attach_macro_vintages,
    load_classification_vintages,
    load_fundamental_vintages,
    load_macro_vintages,
)


class VintageFeatureTest(unittest.TestCase):
    def test_classification_is_hidden_until_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "classification.csv"
            pd.DataFrame([{
                "instrument_id": "company",
                "observation_at": "2023-12-31T21:00:00Z",
                "published_at": "2024-01-03T23:30:00Z",
                "available_at": "2024-01-03T23:35:00Z",
                "revision_id": "original", "sector": "Technology",
                "market_cap": 1_000_000,
                "source": "vendor", "source_kind": "historical_observed",
            }]).to_csv(path, index=False)
            vintages = load_classification_vintages(path)
        featured = pd.DataFrame({
            "datetime": pd.to_datetime(["2024-01-03", "2024-01-04"]),
            "instrument_id": ["company", "company"],
        })
        result = attach_classification_vintages(featured, vintages)
        self.assertTrue(pd.isna(result.loc[0, "sector"]))
        self.assertEqual(result.loc[1, "sector"], "Technology")
        self.assertEqual(result.loc[1, "market_cap"], 1_000_000)

    def test_macro_uses_latest_observation_not_latest_old_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.csv"
            pd.DataFrame(
                [
                    {
                        "series_id": "RATE",
                        "feature_name": "rf_3m",
                        "observation_at": "2024-01-01T21:00:00Z",
                        "published_at": "2024-01-02T20:00:00Z",
                        "available_at": "2024-01-02T20:05:00Z",
                        "revision_id": "first",
                        "value": 0.04,
                        "unit": "decimal_rate",
                        "source": "vendor",
                        "source_kind": "historical_observed",
                    },
                    {
                        "series_id": "RATE",
                        "feature_name": "rf_3m",
                        "observation_at": "2024-01-03T21:00:00Z",
                        "published_at": "2024-01-04T20:00:00Z",
                        "available_at": "2024-01-04T20:05:00Z",
                        "revision_id": "first",
                        "value": 0.05,
                        "unit": "decimal_rate",
                        "source": "vendor",
                        "source_kind": "historical_observed",
                    },
                    {
                        "series_id": "RATE",
                        "feature_name": "rf_3m",
                        "observation_at": "2024-01-01T21:00:00Z",
                        "published_at": "2024-01-05T20:00:00Z",
                        "available_at": "2024-01-05T20:05:00Z",
                        "revision_id": "revised",
                        "value": 0.045,
                        "unit": "decimal_rate",
                        "source": "vendor",
                        "source_kind": "historical_observed",
                    },
                ]
            ).to_csv(path, index=False)
            vintages = load_macro_vintages(path)
        featured = pd.DataFrame({"datetime": pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-08"])})
        result, columns = attach_macro_vintages(featured, vintages)
        self.assertEqual(columns, ["rf_3m"])
        self.assertEqual(result["rf_3m"].tolist(), [0.04, 0.05, 0.05])

    def test_fundamentals_are_hidden_until_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fundamentals.csv"
            pd.DataFrame(
                [
                    {
                        "instrument_id": "company",
                        "observation_at": "2023-12-31T21:00:00Z",
                        "published_at": "2024-01-03T23:30:00Z",
                        "available_at": "2024-01-03T23:35:00Z",
                        "revision_id": "original",
                        "source": "vendor",
                        "source_kind": "historical_observed",
                        "fundamental_quality": 0.8,
                    }
                ]
            ).to_csv(path, index=False)
            vintages = load_fundamental_vintages(path)
        featured = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2024-01-03", "2024-01-04"]),
                "instrument_id": ["company", "company"],
            }
        )
        result, columns = attach_fundamental_vintages(featured, vintages)
        self.assertEqual(columns, ["fundamental_quality"])
        self.assertTrue(pd.isna(result.loc[0, "fundamental_quality"]))
        self.assertEqual(result.loc[1, "fundamental_quality"], 0.8)

    def test_macro_unit_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.csv"
            pd.DataFrame(
                [
                    {
                        "series_id": "RATE",
                        "feature_name": "rf_3m",
                        "observation_at": "2024-01-01T21:00:00Z",
                        "published_at": "2024-01-02T20:00:00Z",
                        "available_at": "2024-01-02T20:05:00Z",
                        "revision_id": "first",
                        "value": 5.2,
                        "unit": "percent",
                        "source": "vendor",
                        "source_kind": "historical_observed",
                    }
                ]
            ).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "unit"):
                load_macro_vintages(path)


if __name__ == "__main__":
    unittest.main()
