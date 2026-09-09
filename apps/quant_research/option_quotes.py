"""Point-in-time option quote adapter and fixed protective-put selector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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


REQUIRED_COLUMNS = {
    "contract_id", "underlying", "quote_ts", "available_at", "expiration",
    "strike", "bid", "ask", "underlying_price", "multiplier",
}


def load_option_quotes(path: Path | str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"option quotes are missing: {', '.join(missing)}")
    for column in ("quote_ts", "available_at", "expiration"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    numeric = ["strike", "bid", "ask", "underlying_price", "multiplier"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric)
    invalid = (
        (frame["strike"] <= 0) | (frame["bid"] < 0) | (frame["ask"] < frame["bid"])
        | (frame["underlying_price"] <= 0) | (frame["multiplier"] <= 0)
        | (frame["available_at"] < frame["quote_ts"])
    )
    if invalid.any() or frame.duplicated(["contract_id", "quote_ts"]).any():
        raise ValueError("option quotes contain invalid values or duplicate snapshots")
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
