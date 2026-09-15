"""Point-in-time option quote adapter and fixed protective-put selector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ProtectivePutSelection:
    status: str
    reason_code: str | None
    contract_id: str | None
    contracts: int
    premium: float
    total_cost: float
    nominal_coverage: float


@dataclass(frozen=True)
class CoveredCallSelection:
    status: str
    reason_code: str | None
    contract_id: str | None
    contracts: int
    premium: float
    total_credit: float
    nominal_coverage: float


REQUIRED_COLUMNS = {
    "contract_id", "underlying", "listed_at", "quote_ts", "available_at", "last_trade_at", "expiration",
    "strike", "option_type", "bid", "ask", "underlying_price", "multiplier",
    "deliverable_instrument_id", "exercise_style", "settlement_type", "source", "source_kind",
}

CONTRACT_COLUMNS = {
    "contract_id", "underlying_id", "listed_at", "expiration", "last_trade_at",
    "strike", "option_type", "multiplier", "deliverable_instrument_id",
    "exercise_style", "settlement_type", "source", "source_kind",
}

QUOTE_COLUMNS = {
    "contract_id", "quote_ts", "available_at", "bid", "ask", "underlying_price",
    "source", "source_kind",
}

_NON_HISTORICAL_MARKERS = ("synthetic", "demo", "fixture", "scenario")


def load_option_quotes(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"option quotes are missing: {', '.join(missing)}")
    for column in ("listed_at", "quote_ts", "available_at", "last_trade_at", "expiration"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    numeric = ["strike", "bid", "ask", "underlying_price", "multiplier"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric)
    frame["option_type"] = frame["option_type"].astype(str).str.lower()
    frame["exercise_style"] = frame["exercise_style"].astype(str).str.lower()
    frame["settlement_type"] = frame["settlement_type"].astype(str).str.lower()
    invalid = (
        frame[list(REQUIRED_COLUMNS)].isna().any(axis=1)
        | ~np.isfinite(frame[numeric]).all(axis=1)
        | (frame["strike"] <= 0) | (frame["bid"] < 0) | (frame["ask"] < frame["bid"])
        | (frame["underlying_price"] <= 0) | (frame["multiplier"] <= 0)
        | (frame["multiplier"] != np.floor(frame["multiplier"]))
        | (frame["available_at"] < frame["quote_ts"])
        | (frame["quote_ts"] < frame["listed_at"])
        | (frame["quote_ts"] > frame["last_trade_at"])
        | (frame["expiration"] < frame["last_trade_at"])
        | ~frame["option_type"].isin({"put", "call"})
        | ~frame["exercise_style"].isin({"american", "european"})
        | ~frame["settlement_type"].isin({"physical", "cash"})
        | (
            frame["settlement_type"].eq("physical")
            & frame["deliverable_instrument_id"].astype(str).str.strip().eq("")
        )
        | frame["source"].astype(str).str.strip().eq("")
        | frame["source_kind"].astype(str).str.strip().eq("")
    )
    if invalid.any() or frame.duplicated(["contract_id", "quote_ts"]).any():
        raise ValueError("option quotes contain invalid values or duplicate snapshots")
    return frame.sort_values(["available_at", "contract_id"]).reset_index(drop=True)


def load_option_market_data(
    contracts_path: Path | str,
    quotes_path: Path | str,
) -> pd.DataFrame:
    """Join the canonical production contract and quote tables for replay."""
    contracts = pd.read_csv(contracts_path)
    quotes = pd.read_csv(quotes_path)
    missing_contracts = sorted(CONTRACT_COLUMNS - set(contracts.columns))
    missing_quotes = sorted(QUOTE_COLUMNS - set(quotes.columns))
    if missing_contracts or missing_quotes:
        missing = [*(f"contracts.{name}" for name in missing_contracts), *(f"quotes.{name}" for name in missing_quotes)]
        raise ValueError(f"option market data is missing: {', '.join(missing)}")
    if contracts["contract_id"].duplicated().any() or quotes.duplicated(["contract_id", "quote_ts"]).any():
        raise ValueError("option market data contains duplicate contracts or quotes")
    contract_fields = contracts[list(CONTRACT_COLUMNS)].rename(columns={
        "underlying_id": "underlying",
        "source": "contract_source",
        "source_kind": "contract_source_kind",
    })
    quote_fields = quotes[list(QUOTE_COLUMNS)].rename(columns={
        "source": "quote_source",
        "source_kind": "quote_source_kind",
    })
    try:
        frame = quote_fields.merge(contract_fields, on="contract_id", how="left", validate="many_to_one")
    except Exception as error:
        raise ValueError("option contract and quote join failed") from error
    if frame["underlying"].isna().any():
        raise ValueError("option quotes reference unknown contracts")
    for column in ("listed_at", "quote_ts", "available_at", "last_trade_at", "expiration"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    numeric = ["strike", "bid", "ask", "underlying_price", "multiplier"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame["option_type"] = frame["option_type"].astype(str).str.lower()
    frame["exercise_style"] = frame["exercise_style"].astype(str).str.lower()
    frame["settlement_type"] = frame["settlement_type"].astype(str).str.lower()
    invalid = (
        frame[list(REQUIRED_COLUMNS - {"source", "source_kind"})
              + ["contract_source", "quote_source", "contract_source_kind", "quote_source_kind"]].isna().any(axis=1)
        | frame[numeric].isna().any(axis=1)
        | ~np.isfinite(frame[numeric]).all(axis=1)
        | (frame["strike"] <= 0)
        | (frame["bid"] < 0)
        | (frame["ask"] < frame["bid"])
        | (frame["underlying_price"] <= 0)
        | (frame["multiplier"] <= 0)
        | (frame["multiplier"] != np.floor(frame["multiplier"]))
        | ~frame["option_type"].isin({"put", "call"})
        | ~frame["exercise_style"].isin({"american", "european"})
        | ~frame["settlement_type"].isin({"physical", "cash"})
        | (frame["available_at"] < frame["quote_ts"])
        | (frame["quote_ts"] < frame["listed_at"])
        | (frame["quote_ts"] > frame["last_trade_at"])
        | (frame["expiration"] < frame["last_trade_at"])
        | (
            frame["settlement_type"].eq("physical")
            & frame["deliverable_instrument_id"].astype(str).str.strip().eq("")
        )
        | frame["contract_source"].astype(str).str.strip().eq("")
        | frame["quote_source"].astype(str).str.strip().eq("")
        | frame["contract_source_kind"].astype(str).str.strip().eq("")
        | frame["quote_source_kind"].astype(str).str.strip().eq("")
    )
    if invalid.any():
        raise ValueError("joined option market data contains invalid contract, quote, or lifetime values")
    contract_historical = frame["contract_source_kind"].astype(str).str.lower().eq("historical_observed")
    quote_historical = frame["quote_source_kind"].astype(str).str.lower().eq("historical_observed")
    names = frame["contract_source"].astype(str).str.lower() + " " + frame["quote_source"].astype(str).str.lower()
    source_historical = ~names.apply(lambda value: any(marker in value for marker in _NON_HISTORICAL_MARKERS))
    historical = contract_historical & quote_historical & source_historical
    frame["source"] = frame["quote_source"].astype(str)
    frame["source_kind"] = historical.map({True: "historical_observed", False: "scenario_only"})
    return frame.sort_values(["available_at", "contract_id"]).reset_index(drop=True)


def available_chain(
    quotes: pd.DataFrame,
    underlying: str,
    decision_time: datetime | pd.Timestamp,
    max_age_minutes: int = 60,
) -> pd.DataFrame:
    timestamp = pd.Timestamp(decision_time)
    if timestamp.tzinfo is None:
        raise ValueError("decision_time must include timezone")
    timestamp = timestamp.tz_convert("UTC")
    eligible = quotes.loc[
        (quotes["underlying"] == underlying)
        & (quotes["available_at"] <= timestamp)
        & (quotes["quote_ts"] <= timestamp)
        & (quotes["quote_ts"] >= timestamp - pd.Timedelta(minutes=max_age_minutes))
        & (quotes["expiration"] > timestamp)
    ]
    if "listed_at" in eligible.columns:
        eligible = eligible.loc[eligible["listed_at"] <= timestamp]
    if "last_trade_at" in eligible.columns:
        eligible = eligible.loc[eligible["last_trade_at"] >= timestamp]
    if eligible.empty:
        return eligible.copy()
    return eligible.sort_values("available_at").groupby("contract_id", as_index=False).tail(1)


def select_fixed_protective_put(
    chain: pd.DataFrame,
    protected_shares: int,
    budget: float,
    target_dte: int = 60,
    target_moneyness: float = 0.95,
    fee_per_contract: float = 0.65,
) -> ProtectivePutSelection:
    if protected_shares <= 0 or budget < 0 or target_dte <= 0 or not 0 < target_moneyness <= 1:
        raise ValueError("invalid protective-put request")
    if chain.empty:
        return ProtectivePutSelection("infeasible", "NO_VALID_QUOTE", None, 0, 0, 0, 0)
    decision = chain["available_at"].max()
    candidates = chain.copy()
    if "option_type" in candidates.columns:
        candidates = candidates.loc[candidates["option_type"].astype(str).str.lower() == "put"]
    if candidates.empty:
        return ProtectivePutSelection("infeasible", "NO_VALID_PUT_QUOTE", None, 0, 0, 0, 0)
    candidates["dte"] = (candidates["expiration"] - decision).dt.total_seconds() / 86400
    candidates["moneyness_gap"] = (
        candidates["strike"] / candidates["underlying_price"] - target_moneyness
    ).abs()
    candidates["dte_gap"] = (candidates["dte"] - target_dte).abs()
    candidates = candidates.sort_values(["dte_gap", "moneyness_gap", "ask", "contract_id"])
    row = candidates.iloc[0]
    desired = protected_shares // int(row["multiplier"])
    per_contract = float(row["ask"] * row["multiplier"] + fee_per_contract)
    affordable = int(budget // per_contract) if per_contract > 0 else 0
    contracts = min(desired, affordable)
    if contracts <= 0:
        return ProtectivePutSelection("infeasible", "BUDGET_BELOW_ONE_CONTRACT", None, 0, 0, 0, 0)
    covered = contracts * int(row["multiplier"])
    return ProtectivePutSelection(
        "feasible", None, str(row["contract_id"]), contracts, float(row["ask"]),
        contracts * per_contract, covered / protected_shares,
    )


def select_fixed_covered_call(
    chain: pd.DataFrame,
    owned_shares: int,
    target_dte: int = 45,
    target_moneyness: float = 1.05,
    fee_per_contract: float = 0.65,
) -> CoveredCallSelection:
    """Select an integer, fully share-covered call using executable bid."""
    if owned_shares <= 0 or target_dte <= 0 or target_moneyness < 1 or fee_per_contract < 0:
        raise ValueError("invalid covered-call request")
    if chain.empty:
        return CoveredCallSelection("infeasible", "NO_VALID_QUOTE", None, 0, 0, 0, 0)
    if "option_type" not in chain.columns:
        raise ValueError("covered-call selection requires explicit option_type")
    candidates = chain.loc[chain["option_type"].astype(str).str.lower() == "call"].copy()
    if candidates.empty:
        return CoveredCallSelection("infeasible", "NO_VALID_CALL_QUOTE", None, 0, 0, 0, 0)
    decision = candidates["available_at"].max()
    candidates["dte"] = (candidates["expiration"] - decision).dt.total_seconds() / 86400
    candidates["moneyness_gap"] = (
        candidates["strike"] / candidates["underlying_price"] - target_moneyness
    ).abs()
    candidates["dte_gap"] = (candidates["dte"] - target_dte).abs()
    candidates = candidates.sort_values(
        ["dte_gap", "moneyness_gap", "bid", "contract_id"],
        ascending=[True, True, False, True],
    )
    row = candidates.iloc[0]
    contracts = owned_shares // int(row["multiplier"])
    per_contract = float(row["bid"] * row["multiplier"] - fee_per_contract)
    if contracts <= 0:
        return CoveredCallSelection("infeasible", "INSUFFICIENT_COVERED_SHARES", None, 0, 0, 0, 0)
    if per_contract <= 0:
        return CoveredCallSelection("infeasible", "NON_POSITIVE_NET_CREDIT", None, 0, 0, 0, 0)
    covered = contracts * int(row["multiplier"])
    return CoveredCallSelection(
        "feasible", None, str(row["contract_id"]), contracts, float(row["bid"]),
        contracts * per_contract, covered / owned_shares,
    )
