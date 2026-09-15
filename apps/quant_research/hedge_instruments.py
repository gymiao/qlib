"""Comparable observed-return diagnostics for ETF and futures hedge instruments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt

import numpy as np
import pandas as pd

from .contracts import canonical_hash


@dataclass(frozen=True)
class HedgeInstrumentSpec:
    symbol: str
    kind: str
    benchmark_exposure: float
    one_way_cost_bps: float
    annual_expense_ratio: float = 0.0
    initial_margin_rate: float = 1.0
    maintenance_margin_rate: float = 1.0
    borrow_available: bool = False


def _metrics(returns: pd.Series) -> dict[str, float]:
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    downside = returns.loc[returns <= returns.quantile(0.05)]
    return {
        "total_return": float(equity.iloc[-1] - 1.0),
        "annualized_volatility": float(returns.std(ddof=1) * sqrt(252)),
        "max_drawdown": float(drawdown.min()),
        "daily_expected_shortfall_95": float(downside.mean()),
    }


def compare_hedge_instruments(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    instrument_returns: pd.DataFrame,
    specs: list[HedgeInstrumentSpec],
    *,
    hedge_ratio: float = 0.5,
) -> dict:
    """Evaluate observed hedge tools without generating executable orders."""
    if not 0 <= hedge_ratio <= 1:
        raise ValueError("hedge_ratio must be between zero and one")
    if not specs or len({spec.symbol for spec in specs}) != len(specs):
        raise ValueError("hedge specs must be non-empty with unique symbols")
    aligned = pd.concat(
        [
            portfolio_returns.rename("portfolio"),
            benchmark_returns.rename("benchmark"),
            instrument_returns[[spec.symbol for spec in specs]],
        ],
        axis=1,
        join="inner",
    ).dropna()
    if len(aligned) < 2 or not np.isfinite(aligned.to_numpy(dtype=float)).all():
        raise ValueError("aligned return history must contain finite observations")
    results = {}
    curves = []
    for spec in specs:
        if (
            not spec.symbol
            or spec.kind not in {"etf", "inverse_etf", "future"}
            or not np.isfinite(spec.benchmark_exposure)
            or spec.benchmark_exposure == 0
            or spec.one_way_cost_bps < 0
            or spec.annual_expense_ratio < 0
            or not 0 < spec.initial_margin_rate <= 1
            or not 0 < spec.maintenance_margin_rate <= spec.initial_margin_rate
        ):
            raise ValueError("invalid hedge instrument specification")
        position_notional = -hedge_ratio / spec.benchmark_exposure
        reasons = []
        if spec.kind in {"etf", "inverse_etf"} and position_notional < 0 and not spec.borrow_available:
            reasons.append("BORROW_REQUIRED_BUT_UNAVAILABLE")
        if spec.kind == "inverse_etf" and spec.benchmark_exposure >= 0:
            reasons.append("INVERSE_ETF_EXPOSURE_MUST_BE_NEGATIVE")
        feasible = not reasons
        tracking = aligned[spec.symbol] - spec.benchmark_exposure * aligned["benchmark"]
        expense = abs(position_notional) * spec.annual_expense_ratio / 252.0
        hedged = aligned["portfolio"] + position_notional * aligned[spec.symbol] - expense
        hedged = hedged.copy()
        hedged.iloc[0] -= abs(position_notional) * spec.one_way_cost_bps / 10_000.0
        result = {
            "status": "evaluated" if feasible else "infeasible",
            "reason_codes": reasons,
            "specification": asdict(spec),
            "position_notional_per_portfolio_dollar": float(position_notional),
            "initial_capital_requirement_per_portfolio_dollar": float(
                abs(position_notional)
                * (spec.initial_margin_rate if spec.kind == "future" else 1.0)
            ),
            "maintenance_margin_per_portfolio_dollar": float(
                abs(position_notional)
                * (spec.maintenance_margin_rate if spec.kind == "future" else 1.0)
            ),
            "tracking_error_annualized": float(tracking.std(ddof=1) * sqrt(252)),
            "mean_daily_basis": float(tracking.mean()),
            "metrics": _metrics(hedged) if feasible else None,
        }
        results[spec.symbol] = result
        if feasible:
            curves.append(pd.DataFrame({
                "date": aligned.index,
                "symbol": spec.symbol,
                "hedged_return": hedged.to_numpy(),
            }))
    identity = {
        "schema_version": "hedge_instrument_comparison.v1",
        "hedge_ratio": hedge_ratio,
        "observations": len(aligned),
        "date_range": [str(aligned.index[0]), str(aligned.index[-1])],
        "results": results,
    }
    return {
        **identity,
        "comparison_id": canonical_hash(identity)[:24],
        "research_only": True,
        "execution_capability": "none",
        "return_convention": "daily_reset_overlay_per_initial_portfolio_dollar",
        "cost_scope": "opening_cost_and_expense_only_no_roll_or_rebalance_cost",
        "claim_boundary": "observed_return_diagnostic_not_execution_or_hedge_recommendation",
        "curves": pd.concat(curves, ignore_index=True) if curves else pd.DataFrame(),
    }
