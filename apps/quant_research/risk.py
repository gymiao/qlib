"""Joint SPY and QQQ-residual risk estimation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


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
