"""Research components for stock, index hedge, and option portfolios."""

from .account import Account, EquityPosition, OptionPosition
from .backtest import HedgeBacktestConfig, run_hedge_comparison
from .hedge_instruments import HedgeInstrumentSpec, compare_hedge_instruments
from .options import (
    OptionContract,
    black_scholes_call,
    black_scholes_put,
    call_delta,
    put_delta,
)
from .option_lifecycle import load_option_lifecycle_events, validate_option_lifecycle_events
from .risk import CashHedgePlan, FactorExposure, estimate_factor_exposure, plan_cash_etf_hedge

__all__ = [
    "Account",
    "EquityPosition",
    "CashHedgePlan",
    "FactorExposure",
    "HedgeBacktestConfig",
    "HedgeInstrumentSpec",
    "OptionContract",
    "OptionPosition",
    "black_scholes_call",
    "black_scholes_put",
    "call_delta",
    "compare_hedge_instruments",
    "estimate_factor_exposure",
    "load_option_lifecycle_events",
    "put_delta",
    "plan_cash_etf_hedge",
    "run_hedge_comparison",
    "validate_option_lifecycle_events",
]
