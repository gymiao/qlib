"""Research components for stock, index hedge, and option portfolios."""

from .account import Account, EquityPosition, OptionPosition
from .backtest import HedgeBacktestConfig, run_hedge_comparison
from .options import OptionContract, black_scholes_put, put_delta
from .risk import FactorExposure, estimate_factor_exposure

__all__ = [
    "Account",
    "EquityPosition",
    "FactorExposure",
    "HedgeBacktestConfig",
    "OptionContract",
    "OptionPosition",
    "black_scholes_put",
    "estimate_factor_exposure",
    "put_delta",
    "run_hedge_comparison",
]
