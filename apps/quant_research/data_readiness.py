"""Production-data readiness checks for point-in-time research inputs.

The audit is deliberately separate from data acquisition.  It accepts fixed CSV
exports, checks their temporal and relational contracts, and reports whether the
bundle is eligible for historical research.  It never upgrades synthetic or
incomplete inputs merely because they are parseable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .contracts import canonical_hash
from .data import file_hash


MASTER_COLUMNS = {
    "instrument_id",
    "symbol",
    "symbol_effective_from",
    "symbol_effective_to",
    "listed_at",
    "delisted_at",
    "asset_type",
    "exchange",
    "currency",
    "calendar",
    "source",
    "source_kind",
}
MEMBERSHIP_COLUMNS = {
    "index_id",
    "instrument_id",
    "announced_at",
    "available_at",
    "effective_from",
    "effective_to",
    "source",
    "source_kind",
}
BARS_COLUMNS = {
    "instrument_id",
    "timestamp",
    "available_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adjustment_factor",
    "source",
    "source_kind",
}
ACTION_COLUMNS = {
    "action_id",
    "instrument_id",
    "action_type",
    "announced_at",
    "available_at",
    "effective_at",
    "settlement_rule",
    "source",
    "source_kind",
}
OPTION_CONTRACT_COLUMNS = {
    "contract_id",
    "underlying_id",
    "listed_at",
    "expiration",
    "last_trade_at",
    "strike",
    "option_type",
    "multiplier",
    "deliverable_instrument_id",
    "exercise_style",
    "settlement_type",
    "source",
    "source_kind",
}
OPTION_QUOTE_COLUMNS = {
    "contract_id",
    "quote_ts",
    "available_at",
    "bid",
    "ask",
    "underlying_price",
    "source",
    "source_kind",
}
OPTION_LIFECYCLE_COLUMNS = {
    "event_id",
    "contract_id",
    "event_type",
    "effective_at",
    "available_at",
    "contracts",
    "source",
    "source_kind",
}
FUNDAMENTAL_COLUMNS = {
    "instrument_id",
    "observation_at",
    "published_at",
    "available_at",
    "revision_id",
    "source",
    "source_kind",
}
CLASSIFICATION_VINTAGE_COLUMNS = {
    "instrument_id",
    "observation_at",
    "published_at",
    "available_at",
    "revision_id",
    "sector",
    "market_cap",
    "source",
    "source_kind",
}
MACRO_VINTAGE_COLUMNS = {
    "series_id",
    "feature_name",
    "observation_at",
    "published_at",
    "available_at",
    "revision_id",
    "value",
    "unit",
    "source",
    "source_kind",
}
EXPECTED_MACRO_UNITS = {
    "rf_3m": "decimal_rate",
    "vix": "index_points",
    "high_yield_spread": "decimal_rate",
    "fed_funds": "decimal_rate",
}
NON_HISTORICAL_SOURCE_MARKERS = ("synthetic", "demo", "fixture", "scenario")


@dataclass(frozen=True)
class AuditIssue:
    code: str
    table: str
    message: str
    rows: tuple[int, ...] = ()


@dataclass(frozen=True)
class DataReadinessReport:
    schema_version: str
    audit_id: str
    status: str
    capabilities: dict[str, str]
    requested_range: dict[str, str]
    thresholds: dict[str, float]
    tables: dict[str, dict[str, Any]]
    coverage: dict[str, Any]
    issues: tuple[AuditIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = [asdict(issue) for issue in self.issues]
        return payload


def _read_csv(path: Path | str, table: str, required: set[str], issues: list[AuditIssue]) -> pd.DataFrame:
    source = Path(path)
    try:
        frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        issues.append(AuditIssue("UNREADABLE_TABLE", table, str(exc)))
        return pd.DataFrame()
    missing = sorted(required - set(frame.columns))
    if missing:
        issues.append(AuditIssue("MISSING_COLUMNS", table, f"missing required columns: {', '.join(missing)}"))
    return frame


def _nonempty(frame: pd.DataFrame, columns: Iterable[str], table: str, issues: list[AuditIssue]) -> None:
    present = [column for column in columns if column in frame]
    if not present or frame.empty:
        return
    invalid = frame[present].apply(lambda column: column.astype(str).str.strip().eq("")).any(axis=1)
    if invalid.any():
        issues.append(
            AuditIssue(
                "MISSING_VALUES",
                table,
                f"blank values in required fields: {', '.join(present)}",
                tuple(int(row) + 2 for row in frame.index[invalid]),
            )
        )


def _require_rows(frame: pd.DataFrame, table: str, issues: list[AuditIssue]) -> None:
    if frame.empty:
        issues.append(AuditIssue("EMPTY_TABLE", table, "table must contain at least one row"))


def _timestamps(
    frame: pd.DataFrame,
    columns: Iterable[str],
    table: str,
    issues: list[AuditIssue],
    nullable: Iterable[str] = (),
) -> None:
    nullable_set = set(nullable)
    for column in columns:
        if column not in frame:
            continue
        bad_rows: list[int] = []
        parsed: list[pd.Timestamp | pd.NaT] = []
        for row, raw in frame[column].items():
            value = str(raw).strip()
            if not value and column in nullable_set:
                parsed.append(pd.NaT)
                continue
            try:
                timestamp = pd.Timestamp(value)
            except (TypeError, ValueError):
                bad_rows.append(int(row) + 2)
                parsed.append(pd.NaT)
                continue
            if timestamp.tzinfo is None:
                bad_rows.append(int(row) + 2)
                parsed.append(pd.NaT)
            else:
                parsed.append(timestamp.tz_convert("UTC"))
        frame[column] = pd.Series(parsed, index=frame.index, dtype="datetime64[ns, UTC]")
        if bad_rows:
            issues.append(
                AuditIssue(
                    "INVALID_TIMESTAMP",
                    table,
                    f"{column} must contain timezone-aware timestamps",
                    tuple(bad_rows),
                )
            )


def _numbers(
    frame: pd.DataFrame,
    columns: Iterable[str],
    table: str,
    issues: list[AuditIssue],
) -> None:
    for column in columns:
        if column not in frame:
            continue
        parsed = pd.to_numeric(frame[column], errors="coerce")
        invalid = parsed.isna() | ~np.isfinite(parsed)
        frame[column] = parsed
        if invalid.any():
            issues.append(
                AuditIssue(
                    "INVALID_NUMBER",
                    table,
                    f"{column} must contain finite numbers",
                    tuple(int(row) + 2 for row in frame.index[invalid]),
                )
            )


def _duplicates(
    frame: pd.DataFrame,
    keys: list[str],
    table: str,
    issues: list[AuditIssue],
) -> None:
    if not set(keys).issubset(frame.columns) or frame.empty:
        return
    invalid = frame.duplicated(keys, keep=False)
    if invalid.any():
        issues.append(
            AuditIssue(
                "DUPLICATE_PRIMARY_KEY",
                table,
                f"duplicate key: {', '.join(keys)}",
                tuple(int(row) + 2 for row in frame.index[invalid]),
            )
        )


def _foreign_keys(
    frame: pd.DataFrame,
    column: str,
    allowed: set[str],
    table: str,
    issues: list[AuditIssue],
    *,
    allow_blank: bool = False,
) -> None:
    if column not in frame:
        return
    values = frame[column].astype(str)
    invalid = ~values.isin(allowed)
    if allow_blank:
        invalid &= values.str.strip().ne("")
    if invalid.any():
        values = sorted(set(frame.loc[invalid, column].astype(str)))
        issues.append(AuditIssue("UNKNOWN_INSTRUMENT", table, f"unknown {column}: {', '.join(values)}"))


def _table_summary(path: Path | str, frame: pd.DataFrame) -> dict[str, Any]:
    source = Path(path)
    return {
        "path": str(source),
        "sha256": file_hash(source) if source.is_file() else None,
        "rows": int(len(frame)),
        "columns": sorted(frame.columns.astype(str)),
        "sources": sorted(frame["source"].drop_duplicates().astype(str)) if "source" in frame else [],
    }


def _reject_nonhistorical_sources(frame: pd.DataFrame, table: str, issues: list[AuditIssue]) -> None:
    if "source" not in frame or "source_kind" not in frame:
        return
    source_names = frame["source"].astype(str).str.strip()
    source_kinds = frame["source_kind"].astype(str).str.strip().str.lower()
    invalid = source_kinds.ne("historical_observed") | source_names.str.lower().apply(
        lambda source: any(marker in source for marker in NON_HISTORICAL_SOURCE_MARKERS)
    )
    if invalid.any():
        sources = sorted(
            f"{source}:{kind}"
            for source, kind in zip(source_names[invalid], source_kinds[invalid])
        )
        issues.append(
            AuditIssue(
                "NON_HISTORICAL_SOURCE",
                table,
                f"only source_kind=historical_observed can unlock historical capability: {', '.join(sources)}",
            )
        )


def audit_production_data(
    *,
    instrument_master_csv: Path | str,
    membership_csv: Path | str,
    bars_csv: Path | str,
    corporate_actions_csv: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    option_contracts_csv: Path | str | None = None,
    option_quotes_csv: Path | str | None = None,
    option_lifecycle_events_csv: Path | str | None = None,
    fundamentals_csv: Path | str | None = None,
    classification_vintages_csv: Path | str | None = None,
    macro_vintages_csv: Path | str | None = None,
    max_bar_delay_hours: float = 12.0,
) -> DataReadinessReport:
    """Audit a fixed data bundle without mutating or normalizing source files."""

    start_at = pd.Timestamp(start)
    end_at = pd.Timestamp(end)
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("start and end must include timezone")
    start_at = start_at.tz_convert("UTC")
    end_at = end_at.tz_convert("UTC")
    if end_at <= start_at:
        raise ValueError("end must be later than start")
    if not np.isfinite(max_bar_delay_hours) or max_bar_delay_hours <= 0:
        raise ValueError("max_bar_delay_hours must be positive and finite")

    issues: list[AuditIssue] = []
    master = _read_csv(instrument_master_csv, "instrument_master", MASTER_COLUMNS, issues)
    membership = _read_csv(membership_csv, "universe_membership", MEMBERSHIP_COLUMNS, issues)
    bars = _read_csv(bars_csv, "bars", BARS_COLUMNS, issues)
    actions = _read_csv(corporate_actions_csv, "corporate_actions", ACTION_COLUMNS, issues)
    tables = {
        "instrument_master": _table_summary(instrument_master_csv, master),
        "universe_membership": _table_summary(membership_csv, membership),
        "bars": _table_summary(bars_csv, bars),
        "corporate_actions": _table_summary(corporate_actions_csv, actions),
    }

    _nonempty(master, MASTER_COLUMNS - {"delisted_at", "symbol_effective_to"}, "instrument_master", issues)
    _nonempty(membership, MEMBERSHIP_COLUMNS - {"effective_to"}, "universe_membership", issues)
    _nonempty(bars, BARS_COLUMNS, "bars", issues)
    _nonempty(actions, ACTION_COLUMNS - {"settlement_rule"}, "corporate_actions", issues)
    _require_rows(master, "instrument_master", issues)
    _require_rows(membership, "universe_membership", issues)
    _require_rows(bars, "bars", issues)
    _reject_nonhistorical_sources(master, "instrument_master", issues)
    _reject_nonhistorical_sources(membership, "universe_membership", issues)
    _reject_nonhistorical_sources(bars, "bars", issues)
    _reject_nonhistorical_sources(actions, "corporate_actions", issues)
    _timestamps(
        master,
        ("listed_at", "delisted_at", "symbol_effective_from", "symbol_effective_to"),
        "instrument_master",
        issues,
        ("delisted_at", "symbol_effective_to"),
    )
    _timestamps(
        membership,
        ("announced_at", "available_at", "effective_from", "effective_to"),
        "universe_membership",
        issues,
        ("effective_to",),
    )
    _timestamps(bars, ("timestamp", "available_at"), "bars", issues)
    _timestamps(actions, ("announced_at", "available_at", "effective_at"), "corporate_actions", issues)
    _numbers(bars, ("open", "high", "low", "close", "volume", "adjustment_factor"), "bars", issues)
    _duplicates(master, ["instrument_id", "symbol_effective_from"], "instrument_master", issues)
    _duplicates(membership, ["index_id", "instrument_id", "effective_from"], "universe_membership", issues)
    _duplicates(bars, ["instrument_id", "timestamp"], "bars", issues)
    _duplicates(actions, ["action_id"], "corporate_actions", issues)

    known = set(master["instrument_id"].astype(str)) if "instrument_id" in master else set()
    _foreign_keys(membership, "instrument_id", known, "universe_membership", issues)
    _foreign_keys(bars, "instrument_id", known, "bars", issues)
    _foreign_keys(actions, "instrument_id", known, "corporate_actions", issues)

    instrument_ranges = pd.DataFrame()
    if {
        "instrument_id",
        "symbol",
        "listed_at",
        "delisted_at",
        "symbol_effective_from",
        "symbol_effective_to",
    }.issubset(master):
        invalid = master["delisted_at"].notna() & (master["delisted_at"] <= master["listed_at"])
        invalid_symbol = (
            (master["symbol_effective_from"] < master["listed_at"])
            | (
                master["symbol_effective_to"].notna()
                & (master["symbol_effective_to"] <= master["symbol_effective_from"])
            )
            | (
                master["delisted_at"].notna()
                & (
                    master["symbol_effective_to"].isna()
                    | (master["symbol_effective_to"] > master["delisted_at"])
                )
            )
        )
        if invalid.any():
            issues.append(
                AuditIssue(
                    "INVALID_LISTING_INTERVAL",
                    "instrument_master",
                    "delisted_at must be later than listed_at",
                    tuple(int(row) + 2 for row in master.index[invalid]),
                )
            )
        if invalid_symbol.any():
            issues.append(
                AuditIssue(
                    "INVALID_SYMBOL_INTERVAL",
                    "instrument_master",
                    "symbol intervals must be non-empty and remain inside the listing interval",
                    tuple(int(row) + 2 for row in master.index[invalid_symbol]),
                )
            )
        for instrument_id, group in master.groupby("instrument_id"):
            listing_pairs = group[["listed_at", "delisted_at"]].astype(str).drop_duplicates()
            if len(listing_pairs) != 1:
                issues.append(
                    AuditIssue(
                        "INCONSISTENT_LISTING_INTERVAL",
                        "instrument_master",
                        f"instrument {instrument_id} has inconsistent listing intervals",
                        tuple(int(row) + 2 for row in group.index),
                    )
                )
            previous_end: pd.Timestamp | None = None
            for row, item in group.sort_values("symbol_effective_from").iterrows():
                if previous_end is not None and item["symbol_effective_from"] < previous_end:
                    issues.append(
                        AuditIssue(
                            "OVERLAPPING_INSTRUMENT_SYMBOL",
                            "instrument_master",
                            "instrument symbol intervals cannot overlap",
                            (int(row) + 2,),
                        )
                    )
                previous_end = (
                    item["symbol_effective_to"]
                    if pd.notna(item["symbol_effective_to"])
                    else pd.Timestamp.max.tz_localize("UTC")
                )
        for _, group in master.sort_values("symbol_effective_from").groupby("symbol"):
            previous_end: pd.Timestamp | None = None
            for row, item in group.iterrows():
                if previous_end is not None and item["symbol_effective_from"] < previous_end:
                    issues.append(
                        AuditIssue(
                            "OVERLAPPING_SYMBOL_INTERVAL",
                            "instrument_master",
                            "one symbol cannot identify multiple instruments at the same time",
                            (int(row) + 2,),
                        )
                    )
                previous_end = (
                    item["symbol_effective_to"]
                    if pd.notna(item["symbol_effective_to"])
                    else pd.Timestamp.max.tz_localize("UTC")
                )
        instrument_ranges = (
            master.sort_values("symbol_effective_from")
            .drop_duplicates("instrument_id")
            .set_index("instrument_id")[["listed_at", "delisted_at"]]
        )

    if {"announced_at", "available_at", "effective_from", "effective_to"}.issubset(membership):
        invalid_availability = membership["available_at"] < membership["announced_at"]
        invalid_interval = membership["effective_to"].notna() & (
            membership["effective_to"] <= membership["effective_from"]
        )
        if invalid_availability.any():
            issues.append(
                AuditIssue(
                    "INVALID_AVAILABILITY",
                    "universe_membership",
                    "available_at cannot precede announced_at",
                    tuple(int(row) + 2 for row in membership.index[invalid_availability]),
                )
            )
        if invalid_interval.any():
            issues.append(
                AuditIssue(
                    "INVALID_MEMBERSHIP_INTERVAL",
                    "universe_membership",
                    "effective_to must be later than effective_from",
                    tuple(int(row) + 2 for row in membership.index[invalid_interval]),
                )
            )
        for _, group in membership.sort_values("effective_from").groupby(["index_id", "instrument_id"]):
            previous_end: pd.Timestamp | None = None
            for row, item in group.iterrows():
                if previous_end is not None and item["effective_from"] < previous_end:
                    issues.append(
                        AuditIssue(
                            "OVERLAPPING_MEMBERSHIP",
                            "universe_membership",
                            "membership intervals must be half-open and non-overlapping",
                            (int(row) + 2,),
                        )
                    )
                previous_end = (
                    item["effective_to"] if pd.notna(item["effective_to"]) else pd.Timestamp.max.tz_localize("UTC")
                )

    if not instrument_ranges.empty and {"instrument_id", "effective_from", "effective_to"}.issubset(membership):
        joined = membership.join(instrument_ranges, on="instrument_id", how="left")
        known_rows = joined["listed_at"].notna()
        outside = known_rows & (
            (joined["effective_from"] < joined["listed_at"])
            | (
                joined["delisted_at"].notna()
                & (joined["effective_to"].isna() | (joined["effective_to"] > joined["delisted_at"]))
            )
        )
        if outside.any():
            issues.append(
                AuditIssue(
                    "MEMBERSHIP_OUTSIDE_LISTING",
                    "universe_membership",
                    "membership intervals must stay within the instrument listing interval",
                    tuple(int(row) + 2 for row in membership.index[outside]),
                )
            )

    if {"timestamp", "available_at", "open", "high", "low", "close", "volume", "adjustment_factor"}.issubset(bars):
        invalid_availability = bars["available_at"] < bars["timestamp"]
        excessive_delay = (bars["available_at"] - bars["timestamp"]) > pd.Timedelta(hours=max_bar_delay_hours)
        invalid_price = (
            (bars[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (bars["volume"] < 0)
            | (bars["adjustment_factor"] <= 0)
            | (bars["high"] < bars[["open", "low", "close"]].max(axis=1))
            | (bars["low"] > bars[["open", "high", "close"]].min(axis=1))
        )
        if invalid_availability.any():
            issues.append(
                AuditIssue(
                    "INVALID_AVAILABILITY",
                    "bars",
                    "available_at cannot precede timestamp",
                    tuple(int(row) + 2 for row in bars.index[invalid_availability]),
                )
            )
        if excessive_delay.any():
            issues.append(
                AuditIssue(
                    "BAR_AVAILABILITY_TOO_LATE",
                    "bars",
                    f"bar availability exceeds the frozen {max_bar_delay_hours:g}-hour limit",
                    tuple(int(row) + 2 for row in bars.index[excessive_delay]),
                )
            )
        if invalid_price.any():
            issues.append(
                AuditIssue(
                    "INVALID_BAR",
                    "bars",
                    "OHLCV or adjustment factor is invalid",
                    tuple(int(row) + 2 for row in bars.index[invalid_price]),
                )
            )
    if not instrument_ranges.empty and {"instrument_id", "timestamp"}.issubset(bars):
        joined = bars.join(instrument_ranges, on="instrument_id", how="left")
        known_rows = joined["listed_at"].notna()
        outside = known_rows & (
            (joined["timestamp"] < joined["listed_at"])
            | (joined["delisted_at"].notna() & (joined["timestamp"] > joined["delisted_at"]))
        )
        if outside.any():
            issues.append(
                AuditIssue(
                    "BAR_OUTSIDE_LISTING",
                    "bars",
                    "bar timestamp falls outside the instrument listing interval",
                    tuple(int(row) + 2 for row in bars.index[outside]),
                )
            )

    if {"announced_at", "available_at"}.issubset(actions):
        invalid = actions["available_at"] < actions["announced_at"]
        if invalid.any():
            issues.append(
                AuditIssue(
                    "INVALID_AVAILABILITY",
                    "corporate_actions",
                    "available_at cannot precede announced_at",
                    tuple(int(row) + 2 for row in actions.index[invalid]),
                )
            )
    if {"action_type", "settlement_rule"}.issubset(actions):
        delistings = actions["action_type"].str.lower().eq("delisting")
        missing_rule = delistings & actions["settlement_rule"].str.strip().eq("")
        if missing_rule.any():
            issues.append(
                AuditIssue(
                    "MISSING_DELISTING_RULE",
                    "corporate_actions",
                    "delisting actions require settlement_rule",
                    tuple(int(row) + 2 for row in actions.index[missing_rule]),
                )
            )
    if {"instrument_id", "delisted_at"}.issubset(master) and {"instrument_id", "action_type"}.issubset(actions):
        expected = set(master.loc[master["delisted_at"].notna(), "instrument_id"])
        recorded = set(actions.loc[actions["action_type"].str.lower().eq("delisting"), "instrument_id"])
        missing = sorted(expected - recorded)
        if missing:
            issues.append(
                AuditIssue(
                    "MISSING_DELISTING_ACTION",
                    "corporate_actions",
                    f"delisted instruments lack liquidation events: {', '.join(missing)}",
                )
            )

    relevant_members: set[str] = set()
    if {"instrument_id", "effective_from", "effective_to"}.issubset(membership):
        relevant = (membership["effective_from"] < end_at) & (
            membership["effective_to"].isna() | (membership["effective_to"] > start_at)
        )
        relevant_members = set(membership.loc[relevant, "instrument_id"].astype(str))
    covered_members: set[str] = set()
    if {"instrument_id", "timestamp"}.issubset(bars):
        in_range = (bars["timestamp"] >= start_at) & (bars["timestamp"] < end_at)
        covered_members = set(bars.loc[in_range, "instrument_id"].astype(str))
    missing_member_bars = sorted(relevant_members - covered_members)
    if missing_member_bars:
        issues.append(
            AuditIssue(
                "MISSING_MEMBER_BARS",
                "bars",
                f"members without bars in requested range: {', '.join(missing_member_bars)}",
            )
        )

    def audit_vintage_table(
        path: Path | str | None,
        table: str,
        required: set[str],
        keys: list[str],
        *,
        require_fundamental_values: bool = False,
    ) -> str:
        if path is None:
            return "not_provided"
        before = len(issues)
        frame = _read_csv(path, table, required, issues)
        tables[table] = _table_summary(path, frame)
        _nonempty(frame, required, table, issues)
        _require_rows(frame, table, issues)
        _reject_nonhistorical_sources(frame, table, issues)
        _timestamps(frame, ("observation_at", "published_at", "available_at"), table, issues)
        _duplicates(frame, keys, table, issues)
        if "instrument_id" in required:
            _foreign_keys(frame, "instrument_id", known, table, issues)
        value_columns = (
            [column for column in frame.columns if column.startswith("fundamental_")]
            if require_fundamental_values
            else ["value"]
        )
        if require_fundamental_values and not value_columns:
            issues.append(
                AuditIssue(
                    "MISSING_FEATURE_COLUMNS",
                    table,
                    "fundamental vintages require at least one fundamental_* column",
                )
            )
        _numbers(frame, value_columns, table, issues)
        if {"observation_at", "published_at", "available_at"}.issubset(frame):
            invalid = (frame["published_at"] < frame["observation_at"]) | (
                frame["available_at"] < frame["published_at"]
            )
            if invalid.any():
                issues.append(
                    AuditIssue(
                        "INVALID_VINTAGE_TIMELINE",
                        table,
                        "timestamps must satisfy observation_at <= published_at <= available_at",
                        tuple(int(row) + 2 for row in frame.index[invalid]),
                    )
                )
        if table == "macro_vintages" and {"feature_name", "unit"}.issubset(frame):
            invalid = ~frame.apply(
                lambda row: EXPECTED_MACRO_UNITS.get(str(row["feature_name"])) == str(row["unit"]),
                axis=1,
            )
            if invalid.any():
                issues.append(
                    AuditIssue(
                        "INVALID_MACRO_UNIT",
                        table,
                        "macro feature_name or unit does not match the frozen research schema",
                        tuple(int(row) + 2 for row in frame.index[invalid]),
                    )
                )
        new_issues = issues[before:]
        if new_issues and all(issue.code == "NON_HISTORICAL_SOURCE" for issue in new_issues):
            return "prototype"
        return "historical_validated" if not new_issues else "invalid"

    fundamental_status = audit_vintage_table(
        fundamentals_csv,
        "fundamental_vintages",
        FUNDAMENTAL_COLUMNS,
        ["instrument_id", "observation_at", "revision_id"],
        require_fundamental_values=True,
    )
    macro_status = audit_vintage_table(
        macro_vintages_csv,
        "macro_vintages",
        MACRO_VINTAGE_COLUMNS,
        ["series_id", "observation_at", "revision_id"],
    )

    classification_status = "not_provided"
    if classification_vintages_csv is not None:
        before = len(issues)
        classification = _read_csv(
            classification_vintages_csv,
            "classification_vintages",
            CLASSIFICATION_VINTAGE_COLUMNS,
            issues,
        )
        tables["classification_vintages"] = _table_summary(
            classification_vintages_csv, classification
        )
        _nonempty(
            classification,
            CLASSIFICATION_VINTAGE_COLUMNS,
            "classification_vintages",
            issues,
        )
        _require_rows(classification, "classification_vintages", issues)
        _reject_nonhistorical_sources(classification, "classification_vintages", issues)
        _timestamps(
            classification,
            ("observation_at", "published_at", "available_at"),
            "classification_vintages",
            issues,
        )
        _numbers(classification, ("market_cap",), "classification_vintages", issues)
        _duplicates(
            classification,
            ["instrument_id", "observation_at", "revision_id"],
            "classification_vintages",
            issues,
        )
        _foreign_keys(
            classification,
            "instrument_id",
            known,
            "classification_vintages",
            issues,
        )
        if {"observation_at", "published_at", "available_at", "market_cap"}.issubset(classification):
            invalid = (
                (classification["published_at"] < classification["observation_at"])
                | (classification["available_at"] < classification["published_at"])
                | (classification["market_cap"] <= 0)
            )
            if invalid.any():
                issues.append(
                    AuditIssue(
                        "INVALID_CLASSIFICATION_VINTAGE",
                        "classification_vintages",
                        "classification timestamps or market cap are invalid",
                        tuple(int(row) + 2 for row in classification.index[invalid]),
                    )
                )
        new_classification_issues = issues[before:]
        if new_classification_issues and all(
            issue.code == "NON_HISTORICAL_SOURCE" for issue in new_classification_issues
        ):
            classification_status = "prototype"
        else:
            classification_status = (
                "historical_validated" if not new_classification_issues else "invalid"
            )

    option_status = "not_provided"
    contracts = pd.DataFrame()
    contract_ids: set[str] = set()
    if (option_contracts_csv is None) != (option_quotes_csv is None):
        issues.append(
            AuditIssue(
                "INCOMPLETE_OPTION_BUNDLE",
                "options",
                "option contracts and option quotes must be provided together",
            )
        )
        option_status = "invalid"
    elif option_contracts_csv is not None and option_quotes_csv is not None:
        before = len(issues)
        contracts = _read_csv(option_contracts_csv, "option_contracts", OPTION_CONTRACT_COLUMNS, issues)
        quotes = _read_csv(option_quotes_csv, "option_quotes", OPTION_QUOTE_COLUMNS, issues)
        tables["option_contracts"] = _table_summary(option_contracts_csv, contracts)
        tables["option_quotes"] = _table_summary(option_quotes_csv, quotes)
        _nonempty(contracts, OPTION_CONTRACT_COLUMNS - {"deliverable_instrument_id"}, "option_contracts", issues)
        _nonempty(quotes, OPTION_QUOTE_COLUMNS, "option_quotes", issues)
        _require_rows(contracts, "option_contracts", issues)
        _require_rows(quotes, "option_quotes", issues)
        _reject_nonhistorical_sources(contracts, "option_contracts", issues)
        _reject_nonhistorical_sources(quotes, "option_quotes", issues)
        _timestamps(contracts, ("listed_at", "expiration", "last_trade_at"), "option_contracts", issues)
        _timestamps(quotes, ("quote_ts", "available_at"), "option_quotes", issues)
        _numbers(contracts, ("strike", "multiplier"), "option_contracts", issues)
        _numbers(quotes, ("bid", "ask", "underlying_price"), "option_quotes", issues)
        _duplicates(contracts, ["contract_id"], "option_contracts", issues)
        _duplicates(quotes, ["contract_id", "quote_ts"], "option_quotes", issues)
        _foreign_keys(contracts, "underlying_id", known, "option_contracts", issues)
        _foreign_keys(
            contracts,
            "deliverable_instrument_id",
            known,
            "option_contracts",
            issues,
            allow_blank=True,
        )
        contract_ids = set(contracts["contract_id"].astype(str)) if "contract_id" in contracts else set()
        if "contract_id" in quotes:
            unknown_contracts = sorted(set(quotes["contract_id"].astype(str)) - contract_ids)
            if unknown_contracts:
                issues.append(
                    AuditIssue(
                        "UNKNOWN_CONTRACT",
                        "option_quotes",
                        f"quotes reference unknown contracts: {', '.join(unknown_contracts)}",
                    )
                )
        if {
            "listed_at",
            "last_trade_at",
            "expiration",
            "strike",
            "multiplier",
            "option_type",
            "exercise_style",
            "settlement_type",
            "deliverable_instrument_id",
        }.issubset(contracts):
            physical_without_deliverable = contracts["settlement_type"].str.lower().eq("physical") & contracts[
                "deliverable_instrument_id"
            ].str.strip().eq("")
            invalid = (
                (contracts["last_trade_at"] < contracts["listed_at"])
                | (contracts["expiration"] < contracts["last_trade_at"])
                | (contracts["strike"] <= 0)
                | (contracts["multiplier"] <= 0)
                | (contracts["multiplier"] != np.floor(contracts["multiplier"]))
                | ~contracts["option_type"].str.lower().isin({"put", "call"})
                | ~contracts["exercise_style"].str.lower().isin({"american", "european"})
                | ~contracts["settlement_type"].str.lower().isin({"physical", "cash"})
                | physical_without_deliverable
            )
            if invalid.any():
                issues.append(
                    AuditIssue(
                        "INVALID_OPTION_CONTRACT",
                        "option_contracts",
                        "contract dates, type, strike, or multiplier are invalid",
                        tuple(int(row) + 2 for row in contracts.index[invalid]),
                    )
                )
        if {"quote_ts", "available_at", "bid", "ask", "underlying_price"}.issubset(quotes):
            invalid = (
                (quotes["available_at"] < quotes["quote_ts"])
                | (quotes["bid"] < 0)
                | (quotes["ask"] < quotes["bid"])
                | (quotes["underlying_price"] <= 0)
            )
            if invalid.any():
                issues.append(
                    AuditIssue(
                        "INVALID_OPTION_QUOTE",
                        "option_quotes",
                        "quote timing or market values are invalid",
                        tuple(int(row) + 2 for row in quotes.index[invalid]),
                    )
                )
        if {"contract_id", "listed_at", "last_trade_at"}.issubset(contracts) and {
            "contract_id",
            "quote_ts",
        }.issubset(quotes):
            contract_windows = contracts.set_index("contract_id")[["listed_at", "last_trade_at"]]
            joined_quotes = quotes.join(contract_windows, on="contract_id", how="left")
            known_quotes = joined_quotes["listed_at"].notna()
            outside = known_quotes & (
                (joined_quotes["quote_ts"] < joined_quotes["listed_at"])
                | (joined_quotes["quote_ts"] > joined_quotes["last_trade_at"])
            )
            if outside.any():
                issues.append(
                    AuditIssue(
                        "QUOTE_OUTSIDE_CONTRACT_LIFETIME",
                        "option_quotes",
                        "quote timestamp falls outside the contract listing interval",
                        tuple(int(row) + 2 for row in quotes.index[outside]),
                    )
                )
            relevant_contracts = set(
                contracts.loc[(contracts["listed_at"] < end_at) & (contracts["expiration"] > start_at), "contract_id"]
            )
            quoted_contracts = set(
                quotes.loc[(quotes["quote_ts"] >= start_at) & (quotes["quote_ts"] < end_at), "contract_id"]
            )
            missing_quotes = sorted(relevant_contracts - quoted_contracts)
            if missing_quotes:
                issues.append(
                    AuditIssue(
                        "MISSING_OPTION_QUOTES",
                        "option_quotes",
                        f"contracts without quotes in requested range: {', '.join(missing_quotes)}",
                    )
                )
        new_option_issues = issues[before:]
        if new_option_issues and all(issue.code == "NON_HISTORICAL_SOURCE" for issue in new_option_issues):
            option_status = "scenario_only"
        else:
            option_status = "historical_validated" if not new_option_issues else "invalid"

    lifecycle_status = "not_provided"
    if option_lifecycle_events_csv is not None:
        before = len(issues)
        lifecycle = _read_csv(
            option_lifecycle_events_csv,
            "option_lifecycle_events",
            OPTION_LIFECYCLE_COLUMNS,
            issues,
        )
        tables["option_lifecycle_events"] = _table_summary(option_lifecycle_events_csv, lifecycle)
        _nonempty(lifecycle, OPTION_LIFECYCLE_COLUMNS, "option_lifecycle_events", issues)
        _require_rows(lifecycle, "option_lifecycle_events", issues)
        _reject_nonhistorical_sources(lifecycle, "option_lifecycle_events", issues)
        _timestamps(lifecycle, ("effective_at", "available_at"), "option_lifecycle_events", issues)
        _numbers(lifecycle, ("contracts",), "option_lifecycle_events", issues)
        _duplicates(lifecycle, ["event_id"], "option_lifecycle_events", issues)
        if option_contracts_csv is None or option_quotes_csv is None:
            issues.append(
                AuditIssue(
                    "LIFECYCLE_WITHOUT_OPTION_BUNDLE",
                    "option_lifecycle_events",
                    "option lifecycle events require contract and quote tables",
                )
            )
        if "contract_id" in lifecycle:
            unknown = sorted(set(lifecycle["contract_id"].astype(str)) - contract_ids)
            if unknown:
                issues.append(
                    AuditIssue(
                        "UNKNOWN_CONTRACT",
                        "option_lifecycle_events",
                        f"events reference unknown contracts: {', '.join(unknown)}",
                    )
                )
        if {"event_type", "effective_at", "available_at", "contracts"}.issubset(lifecycle):
            invalid = (
                ~lifecycle["event_type"].str.strip().str.lower().isin({"early_assignment"})
                | (lifecycle["available_at"] < lifecycle["effective_at"])
                | (lifecycle["contracts"] <= 0)
                | (lifecycle["contracts"] != np.floor(lifecycle["contracts"]))
            )
            if invalid.any():
                issues.append(
                    AuditIssue(
                        "INVALID_OPTION_LIFECYCLE_EVENT",
                        "option_lifecycle_events",
                        "events require supported type, ordered timestamps, and positive integer contracts",
                        tuple(int(row) + 2 for row in lifecycle.index[invalid]),
                    )
                )
        if "contract_id" in lifecycle and {"contract_id", "listed_at", "expiration"}.issubset(contracts):
            windows = contracts.set_index("contract_id")[["listed_at", "expiration"]]
            joined = lifecycle.join(windows, on="contract_id", how="left")
            known_events = joined["listed_at"].notna()
            outside = known_events & (
                (joined["effective_at"] < joined["listed_at"])
                | (joined["effective_at"] >= joined["expiration"])
                | (joined["available_at"] > joined["expiration"])
            )
            if outside.any():
                issues.append(
                    AuditIssue(
                        "LIFECYCLE_EVENT_OUTSIDE_CONTRACT_LIFETIME",
                        "option_lifecycle_events",
                        "event falls outside the option contract lifetime",
                        tuple(int(row) + 2 for row in lifecycle.index[outside]),
                    )
                )
        new_lifecycle_issues = issues[before:]
        if new_lifecycle_issues and all(
            issue.code == "NON_HISTORICAL_SOURCE" for issue in new_lifecycle_issues
        ):
            lifecycle_status = "scenario_only"
        else:
            lifecycle_status = "observed_historical_events" if not new_lifecycle_issues else "invalid"

    core_tables = {"instrument_master", "universe_membership", "bars", "corporate_actions"}
    core_invalid = any(issue.table in core_tables for issue in issues)
    historical_status = "prototype" if core_invalid else "historical_validated"
    overall_invalid = bool(issues)
    capabilities = {
        "equity_history": historical_status,
        "historical_options": option_status,
        "option_lifecycle": lifecycle_status,
        "fundamentals": fundamental_status,
        "macro_vintages": macro_status,
        "classification_vintages": classification_status,
        "live_execution": "not_evaluated",
    }
    requested_range = {"start": start_at.isoformat(), "end": end_at.isoformat()}
    thresholds = {"max_bar_delay_hours": float(max_bar_delay_hours)}
    coverage = {
        "requested_members": len(relevant_members),
        "members_with_bars": len(relevant_members & covered_members),
        "missing_member_bars": missing_member_bars,
    }
    schema_version = "production_data_readiness.v2"
    audit_identity = {
        "schema_version": schema_version,
        "requested_range": requested_range,
        "thresholds": thresholds,
        "tables": {
            name: {key: value for key, value in summary.items() if key != "path"}
            for name, summary in tables.items()
        },
        "capabilities": capabilities,
        "coverage": coverage,
        "issues": [asdict(issue) for issue in issues],
    }
    return DataReadinessReport(
        schema_version=schema_version,
        audit_id=canonical_hash(audit_identity)[:20],
        status="invalid" if overall_invalid else "valid",
        capabilities=capabilities,
        requested_range=requested_range,
        thresholds=thresholds,
        tables=tables,
        coverage=coverage,
        issues=tuple(issues),
    )
