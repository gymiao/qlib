import tempfile
import unittest
from pathlib import Path

import pandas as pd

from apps.quant_research.option_lifecycle import load_option_lifecycle_events


class OptionLifecycleTest(unittest.TestCase):
    def test_loader_normalizes_observed_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            pd.DataFrame([{
                "event_id": "assignment-1", "contract_id": "SPY-C105",
                "event_type": "early_assignment",
                "effective_at": "2024-01-04T13:00:00Z",
                "available_at": "2024-01-04T13:05:00Z",
                "contracts": 1, "source": "broker-history",
                "source_kind": "historical_observed",
            }]).to_csv(path, index=False)
            events = load_option_lifecycle_events(path)
            self.assertEqual(events.iloc[0]["contracts"], 1)
            self.assertEqual(str(events.iloc[0]["available_at"].tz), "UTC")

    def test_loader_rejects_future_visibility_fraction_and_duplicates(self):
        base = {
            "event_id": "assignment-1", "contract_id": "SPY-C105",
            "event_type": "early_assignment",
            "effective_at": "2024-01-04T13:00:00Z",
            "available_at": "2024-01-04T12:59:00Z",
            "contracts": 0.5, "source": "broker-history",
            "source_kind": "historical_observed",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            pd.DataFrame([base, base]).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "invalid"):
                load_option_lifecycle_events(path)


if __name__ == "__main__":
    unittest.main()
