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
