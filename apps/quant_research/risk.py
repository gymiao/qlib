"""Joint SPY and QQQ-residual risk estimation."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
from typing import Any

import numpy as np
import pandas as pd

from .contracts import canonical_hash


@dataclass(frozen=True)
class FactorExposure:
    spy_beta: float
    qqq_residual_beta: float
    intercept: float
    r_squared: float
    observations: int


@dataclass(frozen=True)
class RiskSnapshot:
    as_of: str
    factor_definition: str
    qqq_spy_loading: float
    spy_factor_dollars: float
    qqq_residual_dollars: float
    option_delta_dollars: float
    observations: int
    quality: str


@dataclass(frozen=True)
class CashHedgePlan:
    plan_id: str
    as_of: str
    status: str
    reason_codes: tuple[str, ...]
    orders: tuple[dict[str, Any], ...]
    current_spy_factor_dollars: float
    current_qqq_residual_dollars: float
    target_spy_factor_dollars: float
    target_qqq_residual_dollars: float
    achieved_spy_factor_dollars: float
    achieved_qqq_residual_dollars: float
    qqq_spy_loading: float
    execution_capability: str = "research_plan_only"
    schema_version: str = "cash_hedge_plan.v1"


def joint_factor_dollars(
    spy_value: float,
    qqq_value: float,
    qqq_spy_loading: float,
    option_qqq_delta_dollars: float = 0.0,
) -> tuple[float, float]:
    """Map SPY and QQQ value into SPY plus QQQ-residual factors."""
    qqq_total = qqq_value + option_qqq_delta_dollars
    return spy_value + qqq_spy_loading * qqq_total, qqq_total


def estimate_qqq_spy_loading(benchmark_returns: pd.DataFrame) -> tuple[float, int]:
    clean = benchmark_returns[["SPY", "QQQ"]].dropna()
    if len(clean) < 20:
        raise ValueError("at least 20 aligned observations are required")
    spy = clean["SPY"].to_numpy()
    design = np.column_stack([np.ones(len(clean)), spy])
    coefficients, _, _, _ = np.linalg.lstsq(design, clean["QQQ"].to_numpy(), rcond=None)
    return float(coefficients[1]), len(clean)


def build_risk_snapshot(
    as_of: str,
    spy_value: float,
    qqq_value: float,
    benchmark_returns: pd.DataFrame,
    option_qqq_delta_dollars: float = 0.0,
) -> RiskSnapshot:
    loading, observations = estimate_qqq_spy_loading(benchmark_returns)
    spy_factor, residual = joint_factor_dollars(spy_value, qqq_value, loading, option_qqq_delta_dollars)
    return RiskSnapshot(
        as_of=as_of,
        factor_definition="SPY_PLUS_QQQ_RESIDUAL_V1",
        qqq_spy_loading=loading,
        spy_factor_dollars=spy_factor,
        qqq_residual_dollars=residual,
        option_delta_dollars=option_qqq_delta_dollars,
        observations=observations,
        quality="valid" if observations >= 60 else "limited_history",
    )


def _integer_toward_zero(value: float) -> int:
    return ceil(value) if value < 0 else floor(value)


def plan_cash_etf_hedge(
    snapshot: RiskSnapshot,
    *,
    spy_price: float,
    qqq_price: float,
    current_spy_shares: int,
    current_qqq_shares: int,
    target_spy_factor_dollars: float,
    target_qqq_residual_dollars: float,
    deadband_dollars: float = 500.0,
) -> CashHedgePlan:
    """Plan integer SPY/QQQ trades without double-counting their common factor.

    This is a cash-account risk reduction plan: it may sell existing ETF shares
    but will not buy more exposure or create a short position.
    """
    numeric = (
        spy_price, qqq_price, current_spy_shares, current_qqq_shares,
        target_spy_factor_dollars, target_qqq_residual_dollars, deadband_dollars,
        snapshot.spy_factor_dollars, snapshot.qqq_residual_dollars,
        snapshot.qqq_spy_loading,
    )
    if (
        snapshot.factor_definition != "SPY_PLUS_QQQ_RESIDUAL_V1"
        or not all(isfinite(float(value)) for value in numeric)
        or spy_price <= 0
        or qqq_price <= 0
        or deadband_dollars < 0
        or isinstance(current_spy_shares, bool)
        or isinstance(current_qqq_shares, bool)
        or not isinstance(current_spy_shares, int)
        or not isinstance(current_qqq_shares, int)
        or current_spy_shares < 0
        or current_qqq_shares < 0
    ):
        raise ValueError("invalid joint-factor cash hedge request")

    reasons: list[str] = []
    qqq_dollar_request = target_qqq_residual_dollars - snapshot.qqq_residual_dollars
    if abs(qqq_dollar_request) <= deadband_dollars:
        qqq_trade = 0
    else:
        qqq_trade = _integer_toward_zero(qqq_dollar_request / qqq_price)
    if qqq_trade > 0:
        qqq_trade = 0
        reasons.append("QQQ_EXPOSURE_INCREASE_DISABLED")
    if qqq_trade < -current_qqq_shares:
        qqq_trade = -current_qqq_shares
        reasons.append("QQQ_POSITION_LIMIT")
    qqq_trade_dollars = qqq_trade * qqq_price
    achieved_qqq = snapshot.qqq_residual_dollars + qqq_trade_dollars

    spy_dollar_request = (
        target_spy_factor_dollars
        - snapshot.spy_factor_dollars
        - snapshot.qqq_spy_loading * qqq_trade_dollars
    )
    if abs(spy_dollar_request) <= deadband_dollars:
        spy_trade = 0
    else:
        spy_trade = _integer_toward_zero(spy_dollar_request / spy_price)
    if spy_trade > 0:
        spy_trade = 0
        reasons.append("SPY_EXPOSURE_INCREASE_DISABLED")
    if spy_trade < -current_spy_shares:
        spy_trade = -current_spy_shares
        reasons.append("SPY_POSITION_LIMIT")
    achieved_spy = (
        snapshot.spy_factor_dollars
        + snapshot.qqq_spy_loading * qqq_trade_dollars
        + spy_trade * spy_price
    )

    if abs(achieved_qqq - target_qqq_residual_dollars) > deadband_dollars:
        reasons.append("QQQ_TARGET_NOT_REACHED")
    if abs(achieved_spy - target_spy_factor_dollars) > deadband_dollars:
        reasons.append("SPY_FACTOR_TARGET_NOT_REACHED")
    reasons = list(dict.fromkeys(reasons))
    orders = tuple(
        {
            "instrument": symbol,
            "quantity": quantity,
            "reference_price": price,
            "notional_change": quantity * price,
            "reason_code": "JOINT_FACTOR_CASH_HEDGE",
        }
        for symbol, quantity, price in (
            ("QQQ", qqq_trade, qqq_price),
            ("SPY", spy_trade, spy_price),
        )
        if quantity
    )
    within_targets = not any(code.endswith("TARGET_NOT_REACHED") for code in reasons)
    status = "no_action" if not orders and within_targets else "planned" if within_targets else "partially_constrained"
    identity = {
        "schema_version": "cash_hedge_plan.v1",
        "as_of": snapshot.as_of,
        "status": status,
        "reason_codes": reasons,
        "orders": list(orders),
        "current_spy_factor_dollars": snapshot.spy_factor_dollars,
        "current_qqq_residual_dollars": snapshot.qqq_residual_dollars,
        "target_spy_factor_dollars": target_spy_factor_dollars,
        "target_qqq_residual_dollars": target_qqq_residual_dollars,
        "achieved_spy_factor_dollars": achieved_spy,
        "achieved_qqq_residual_dollars": achieved_qqq,
        "qqq_spy_loading": snapshot.qqq_spy_loading,
        "execution_capability": "research_plan_only",
    }
    return CashHedgePlan(
        plan_id=canonical_hash(identity)[:24],
        as_of=snapshot.as_of,
        status=status,
        reason_codes=tuple(reasons),
        orders=orders,
        current_spy_factor_dollars=snapshot.spy_factor_dollars,
        current_qqq_residual_dollars=snapshot.qqq_residual_dollars,
        target_spy_factor_dollars=target_spy_factor_dollars,
        target_qqq_residual_dollars=target_qqq_residual_dollars,
        achieved_spy_factor_dollars=achieved_spy,
        achieved_qqq_residual_dollars=achieved_qqq,
        qqq_spy_loading=snapshot.qqq_spy_loading,
    )


def factor_returns(benchmark_returns: pd.DataFrame) -> pd.DataFrame:
    required = {"SPY", "QQQ"}
    if not required.issubset(benchmark_returns.columns):
        raise ValueError("benchmark_returns requires SPY and QQQ columns")
    clean = benchmark_returns[["SPY", "QQQ"]].dropna()
    spy = clean["SPY"]
    denominator = float(np.dot(spy, spy))
    qqq_on_spy = float(np.dot(spy, clean["QQQ"]) / denominator) if denominator else 0.0
    return pd.DataFrame({"SPY": spy, "QQQ_RESIDUAL": clean["QQQ"] - qqq_on_spy * spy}, index=clean.index)


def estimate_factor_exposure(portfolio_returns: pd.Series, benchmark_returns: pd.DataFrame) -> FactorExposure:
    factors = factor_returns(benchmark_returns)
    joined = pd.concat([portfolio_returns.rename("portfolio"), factors], axis=1).dropna()
    if len(joined) < 20:
        raise ValueError("at least 20 aligned observations are required")
    design = np.column_stack([np.ones(len(joined)), joined["SPY"], joined["QQQ_RESIDUAL"]])
    coefficients, _, _, _ = np.linalg.lstsq(design, joined["portfolio"].to_numpy(), rcond=None)
    fitted = design @ coefficients
    residual = joined["portfolio"].to_numpy() - fitted
    centered = joined["portfolio"].to_numpy() - joined["portfolio"].mean()
    total = float(np.dot(centered, centered))
    r_squared = 1.0 - float(np.dot(residual, residual)) / total if total else 0.0
    return FactorExposure(
        spy_beta=float(coefficients[1]),
        qqq_residual_beta=float(coefficients[2]),
        intercept=float(coefficients[0]),
        r_squared=r_squared,
        observations=len(joined),
    )
