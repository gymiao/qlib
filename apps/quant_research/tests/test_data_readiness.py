from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from apps.quant_research.audit_production_data import main
from apps.quant_research.data_readiness import audit_production_data


class ProductionDataReadinessTest(unittest.TestCase):
    def write_bundle(self, root: Path) -> dict[str, Path]:
        paths = {
            name: root / f"{name}.csv"
            for name in (
                "instrument_master", "membership", "bars", "actions", "contracts", "quotes", "lifecycle"
            )
        }
        pd.DataFrame(
            [
                {
                    "instrument_id": "inst-aaa",
                    "symbol": "AAA",
                    "symbol_effective_from": "2020-01-02T14:30:00Z",
                    "symbol_effective_to": "",
                    "listed_at": "2020-01-02T14:30:00Z",
                    "delisted_at": "",
                    "asset_type": "equity",
                    "exchange": "XNAS",
                    "currency": "USD",
                    "calendar": "XNYS",
                    "source": "vendor-a",
                    "source_kind": "historical_observed",
                },
                {
                    "instrument_id": "inst-bbb",
                    "symbol": "BBB",
                    "symbol_effective_from": "2020-01-02T14:30:00Z",
                    "symbol_effective_to": "2024-06-03T20:00:00Z",
                    "listed_at": "2020-01-02T14:30:00Z",
                    "delisted_at": "2024-06-03T20:00:00Z",
                    "asset_type": "equity",
                    "exchange": "XNAS",
                    "currency": "USD",
                    "calendar": "XNYS",
                    "source": "vendor-a",
                    "source_kind": "historical_observed",
                },
            ]
        ).to_csv(paths["instrument_master"], index=False)
        pd.DataFrame(
            [
                {
                    "index_id": "NDX",
                    "instrument_id": "inst-aaa",
                    "announced_at": "2023-12-15T21:00:00Z",
                    "available_at": "2023-12-15T22:00:00Z",
                    "effective_from": "2024-01-02T14:30:00Z",
                    "effective_to": "",
                    "source": "vendor-a",
                    "source_kind": "historical_observed",
                },
                {
                    "index_id": "NDX",
                    "instrument_id": "inst-bbb",
                    "announced_at": "2023-12-15T21:00:00Z",
                    "available_at": "2023-12-15T22:00:00Z",
                    "effective_from": "2024-01-02T14:30:00Z",
                    "effective_to": "2024-06-03T20:00:00Z",
                    "source": "vendor-a",
                    "source_kind": "historical_observed",
                },
            ]
        ).to_csv(paths["membership"], index=False)
        pd.DataFrame(
            [
                {
                    "instrument_id": instrument,
                    "timestamp": "2024-01-02T21:00:00Z",
                    "available_at": "2024-01-02T21:05:00Z",
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "volume": 100000,
                    "adjustment_factor": 1,
                    "source": "vendor-a",
                    "source_kind": "historical_observed",
                }
                for instrument in ("inst-aaa", "inst-bbb")
            ]
        ).to_csv(paths["bars"], index=False)
        pd.DataFrame(
            [
                {
                    "action_id": "delist-bbb",
                    "instrument_id": "inst-bbb",
                    "action_type": "delisting",
                    "announced_at": "2024-05-20T20:00:00Z",
                    "available_at": "2024-05-20T20:05:00Z",
                    "effective_at": "2024-06-03T20:00:00Z",
                    "settlement_rule": "cash_at_final_mark",
                    "source": "vendor-a",
                    "source_kind": "historical_observed",
                }
            ]
        ).to_csv(paths["actions"], index=False)
        pd.DataFrame(
            [
                {
                    "contract_id": "AAA-202403-P95",
                    "underlying_id": "inst-aaa",
                    "listed_at": "2023-12-01T14:30:00Z",
                    "expiration": "2024-03-15T21:00:00Z",
                    "last_trade_at": "2024-03-15T20:00:00Z",
                    "strike": 95,
                    "option_type": "put",
                    "multiplier": 100,
                    "deliverable_instrument_id": "inst-aaa",
                    "exercise_style": "american",
                    "settlement_type": "physical",
                    "source": "vendor-o",
                    "source_kind": "historical_observed",
                }
            ]
        ).to_csv(paths["contracts"], index=False)
        pd.DataFrame(
            [
                {
                    "contract_id": "AAA-202403-P95",
                    "quote_ts": "2024-01-02T20:00:00Z",
                    "available_at": "2024-01-02T20:01:00Z",
                    "bid": 2.8,
                    "ask": 3.0,
                    "underlying_price": 100,
                    "source": "vendor-o",
                    "source_kind": "historical_observed",
                }
            ]
        ).to_csv(paths["quotes"], index=False)
        pd.DataFrame([{
            "event_id": "assignment-1", "contract_id": "AAA-202403-P95",
            "event_type": "early_assignment",
            "effective_at": "2024-01-15T13:00:00Z",
            "available_at": "2024-01-15T13:05:00Z",
            "contracts": 1, "source": "vendor-o",
            "source_kind": "historical_observed",
        }]).to_csv(paths["lifecycle"], index=False)
        return paths

    def audit(
        self,
        paths: dict[str, Path],
        with_options: bool = True,
        with_lifecycle: bool = False,
        **kwargs,
    ):
        option_args = (
            {"option_contracts_csv": paths["contracts"], "option_quotes_csv": paths["quotes"]}
            if with_options
            else {}
        )
        if with_lifecycle:
            option_args["option_lifecycle_events_csv"] = paths["lifecycle"]
        return audit_production_data(
            instrument_master_csv=paths["instrument_master"],
            membership_csv=paths["membership"],
            bars_csv=paths["bars"],
            corporate_actions_csv=paths["actions"],
            start="2024-01-01T00:00:00Z",
            end="2024-02-01T00:00:00Z",
            **option_args,
            **kwargs,
        )

    def test_valid_bundle_unlocks_only_historical_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.audit(self.write_bundle(Path(directory)))
        self.assertEqual(report.status, "valid")
        self.assertEqual(report.capabilities["equity_history"], "historical_validated")
        self.assertEqual(report.capabilities["historical_options"], "historical_validated")
        self.assertEqual(report.capabilities["option_lifecycle"], "not_provided")
        self.assertEqual(report.capabilities["live_execution"], "not_evaluated")
        self.assertEqual(report.coverage["requested_members"], 2)
        self.assertEqual(report.issues, ())
        self.assertEqual(report.schema_version, "production_data_readiness.v2")
        self.assertEqual(len(report.audit_id), 20)
        self.assertEqual(len(report.tables["bars"]["sha256"]), 64)

    def test_observed_option_lifecycle_has_separate_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            report = self.audit(paths, with_lifecycle=True)
            self.assertEqual(report.status, "valid")
            self.assertEqual(report.capabilities["historical_options"], "historical_validated")
            self.assertEqual(report.capabilities["option_lifecycle"], "observed_historical_events")
            lifecycle = pd.read_csv(paths["lifecycle"])
            lifecycle.loc[0, "available_at"] = "2024-03-16T13:05:00Z"
            lifecycle.to_csv(paths["lifecycle"], index=False)
            invalid = self.audit(paths, with_lifecycle=True)
            self.assertEqual(invalid.capabilities["option_lifecycle"], "invalid")
            self.assertIn(
                "LIFECYCLE_EVENT_OUTSIDE_CONTRACT_LIFETIME",
                {issue.code for issue in invalid.issues},
            )

    def test_audit_id_depends_on_content_not_local_paths(self):
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            first = self.audit(self.write_bundle(Path(first_directory)))
            second = self.audit(self.write_bundle(Path(second_directory)))
        self.assertEqual(first.audit_id, second.audit_id)

    def test_overlap_unknown_member_and_missing_bars_are_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            membership = pd.read_csv(paths["membership"])
            membership.loc[len(membership)] = {
                "index_id": "NDX",
                "instrument_id": "inst-aaa",
                "announced_at": "2024-01-05T21:00:00Z",
                "available_at": "2024-01-05T22:00:00Z",
                "effective_from": "2024-01-10T14:30:00Z",
                "effective_to": "2024-01-20T21:00:00Z",
                "source": "vendor-a",
                "source_kind": "historical_observed",
            }
            membership.loc[len(membership)] = {
                "index_id": "NDX",
                "instrument_id": "inst-missing",
                "announced_at": "2023-12-15T21:00:00Z",
                "available_at": "2023-12-15T22:00:00Z",
                "effective_from": "2024-01-02T14:30:00Z",
                "effective_to": "",
                "source": "vendor-a",
                "source_kind": "historical_observed",
            }
            membership.to_csv(paths["membership"], index=False)
            report = self.audit(paths, with_options=False)
        codes = {issue.code for issue in report.issues}
        self.assertEqual(report.status, "invalid")
        self.assertEqual(report.capabilities["equity_history"], "prototype")
        self.assertIn("OVERLAPPING_MEMBERSHIP", codes)
        self.assertIn("UNKNOWN_INSTRUMENT", codes)
        self.assertIn("MISSING_MEMBER_BARS", codes)

    def test_naive_time_and_delisting_without_rule_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            master = pd.read_csv(paths["instrument_master"])
            master.loc[0, "listed_at"] = "2020-01-02 14:30:00"
            master.to_csv(paths["instrument_master"], index=False)
            actions = pd.read_csv(paths["actions"])
            actions.loc[0, "settlement_rule"] = ""
            actions.to_csv(paths["actions"], index=False)
            report = self.audit(paths, with_options=False)
        codes = {issue.code for issue in report.issues}
        self.assertIn("INVALID_TIMESTAMP", codes)
        self.assertIn("MISSING_DELISTING_RULE", codes)

    def test_bar_arriving_after_frozen_delay_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            bars = pd.read_csv(paths["bars"])
            bars.loc[0, "available_at"] = "2024-01-03T15:00:00Z"
            bars.to_csv(paths["bars"], index=False)
            report = self.audit(paths, with_options=False)
        self.assertIn("BAR_AVAILABILITY_TOO_LATE", {issue.code for issue in report.issues})
        self.assertEqual(report.thresholds["max_bar_delay_hours"], 12.0)

    def test_explicit_nonhistorical_source_kind_is_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            bars = pd.read_csv(paths["bars"])
            bars["source"] = "neutral-vendor-name"
            bars["source_kind"] = "scenario"
            bars.to_csv(paths["bars"], index=False)
            report = self.audit(paths, with_options=False)
        self.assertEqual(report.capabilities["equity_history"], "prototype")
        self.assertIn("NON_HISTORICAL_SOURCE", {issue.code for issue in report.issues})

    def test_point_in_time_feature_vintages_have_separate_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_bundle(root)
            fundamentals = root / "fundamentals.csv"
            macro = root / "macro.csv"
            classifications = root / "classifications.csv"
            pd.DataFrame(
                [
                    {
                        "instrument_id": "inst-aaa",
                        "observation_at": "2023-09-30T20:00:00Z",
                        "published_at": "2023-11-01T20:00:00Z",
                        "available_at": "2023-11-01T20:05:00Z",
                        "revision_id": "original",
                        "source": "vendor-f",
                        "source_kind": "historical_observed",
                        "fundamental_pe": 20,
                    }
                ]
            ).to_csv(fundamentals, index=False)
            pd.DataFrame(
                [
                    {
                        "series_id": "DGS3MO",
                        "feature_name": "rf_3m",
                        "observation_at": "2024-01-02T21:00:00Z",
                        "published_at": "2024-01-03T20:00:00Z",
                        "available_at": "2024-01-03T20:05:00Z",
                        "revision_id": "original",
                        "value": 0.052,
                        "unit": "decimal_rate",
                        "source": "vendor-m",
                        "source_kind": "historical_observed",
                    }
                ]
            ).to_csv(macro, index=False)
            pd.DataFrame([{
                "instrument_id": "inst-aaa",
                "observation_at": "2023-12-31T21:00:00Z",
                "published_at": "2024-01-01T20:00:00Z",
                "available_at": "2024-01-01T20:05:00Z",
                "revision_id": "original", "sector": "Technology",
                "market_cap": 1_000_000,
                "source": "vendor-c", "source_kind": "historical_observed",
            }]).to_csv(classifications, index=False)
            report = self.audit(
                paths,
                with_options=False,
                fundamentals_csv=fundamentals,
                macro_vintages_csv=macro,
                classification_vintages_csv=classifications,
            )
            invalid_macro = pd.read_csv(macro)
            invalid_macro["unit"] = "percent"
            invalid_macro.to_csv(macro, index=False)
            invalid_report = self.audit(paths, with_options=False, macro_vintages_csv=macro)
        self.assertEqual(report.status, "valid")
        self.assertEqual(report.capabilities["fundamentals"], "historical_validated")
        self.assertEqual(report.capabilities["macro_vintages"], "historical_validated")
        self.assertEqual(report.capabilities["classification_vintages"], "historical_validated")
        self.assertEqual(invalid_report.capabilities["macro_vintages"], "invalid")
        self.assertIn("INVALID_MACRO_UNIT", {issue.code for issue in invalid_report.issues})

    def test_vintage_available_before_publication_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self.write_bundle(root)
            macro = root / "macro.csv"
            pd.DataFrame(
                [
                    {
                        "series_id": "DGS3MO",
                        "feature_name": "rf_3m",
                        "observation_at": "2024-01-02T21:00:00Z",
                        "published_at": "2024-01-03T20:00:00Z",
                        "available_at": "2024-01-03T19:00:00Z",
                        "revision_id": "bad",
                        "value": 0.052,
                        "unit": "decimal_rate",
                        "source": "vendor-m",
                        "source_kind": "historical_observed",
                    }
                ]
            ).to_csv(macro, index=False)
            report = self.audit(paths, with_options=False, macro_vintages_csv=macro)
        self.assertEqual(report.capabilities["macro_vintages"], "invalid")
        self.assertIn("INVALID_VINTAGE_TIMELINE", {issue.code for issue in report.issues})

    def test_option_contract_and_quote_must_arrive_as_one_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            report = audit_production_data(
                instrument_master_csv=paths["instrument_master"],
                membership_csv=paths["membership"],
                bars_csv=paths["bars"],
                corporate_actions_csv=paths["actions"],
                start="2024-01-01T00:00:00Z",
                end="2024-02-01T00:00:00Z",
                option_contracts_csv=paths["contracts"],
            )
        self.assertEqual(report.status, "invalid")
        self.assertEqual(report.capabilities["historical_options"], "invalid")
        self.assertIn("INCOMPLETE_OPTION_BUNDLE", {issue.code for issue in report.issues})

    def test_empty_core_table_and_incomplete_option_schema_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            pd.read_csv(paths["bars"]).iloc[0:0].to_csv(paths["bars"], index=False)
            pd.DataFrame({"contract_id": ["AAA-202403-P95"]}).to_csv(paths["contracts"], index=False)
            report = self.audit(paths)
        codes = {issue.code for issue in report.issues}
        self.assertEqual(report.status, "invalid")
        self.assertEqual(report.capabilities["equity_history"], "prototype")
        self.assertEqual(report.capabilities["historical_options"], "invalid")
        self.assertIn("EMPTY_TABLE", codes)
        self.assertIn("MISSING_COLUMNS", codes)

    def test_cli_writes_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.write_bundle(Path(directory))
            output = Path(directory) / "report.json"
            argv = [
                "audit_production_data",
                "--instrument-master",
                str(paths["instrument_master"]),
                "--membership",
                str(paths["membership"]),
                "--bars",
                str(paths["bars"]),
                "--corporate-actions",
                str(paths["actions"]),
                "--start",
                "2024-01-01T00:00:00Z",
                "--end",
                "2024-02-01T00:00:00Z",
                "--output",
                str(output),
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(StringIO()):
                exit_code = main()
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "valid")
        self.assertEqual(payload["capabilities"]["historical_options"], "not_provided")

    def test_repository_schema_demo_is_rejected_as_historical(self):
        root = Path(__file__).resolve().parents[3] / "examples" / "data" / "production_bundle_demo"
        report = audit_production_data(
            instrument_master_csv=root / "instrument_master.csv.example",
            membership_csv=root / "universe_membership.csv.example",
            bars_csv=root / "bars.csv.example",
            corporate_actions_csv=root / "corporate_actions.csv.example",
            option_contracts_csv=root / "option_contracts.csv.example",
            option_quotes_csv=root / "option_quotes.csv.example",
            option_lifecycle_events_csv=root / "option_lifecycle_events.csv.example",
            fundamentals_csv=root / "fundamental_vintages.csv.example",
            classification_vintages_csv=root / "classification_vintages.csv.example",
            macro_vintages_csv=root / "macro_vintages.csv.example",
            start="2024-01-01T00:00:00Z",
            end="2024-02-01T00:00:00Z",
        )
        self.assertEqual(report.status, "invalid")
        self.assertEqual(report.capabilities["equity_history"], "prototype")
        self.assertEqual(report.capabilities["historical_options"], "scenario_only")
        self.assertEqual(report.capabilities["option_lifecycle"], "scenario_only")
        self.assertEqual(report.capabilities["fundamentals"], "prototype")
        self.assertEqual(report.capabilities["classification_vintages"], "prototype")
        self.assertEqual(report.capabilities["macro_vintages"], "prototype")
        self.assertEqual(report.capabilities["live_execution"], "not_evaluated")
        self.assertIn("NON_HISTORICAL_SOURCE", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()
