"""Option contracts and deterministic valuation helpers.

The pricing helpers are for scenario analysis and testable fallback valuation.
They are not a substitute for executable historical bid/ask quotes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import erf, exp, log, sqrt


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


@dataclass(frozen=True)
class OptionContract:
    contract_id: str
    underlying: str
    expiration: date
    strike: float
    option_type: str = "put"
    multiplier: int = 100
    exercise_style: str = "american"
    settlement: str = "physical"

    def __post_init__(self) -> None:
        if self.option_type not in {"put", "call"}:
            raise ValueError("option_type must be put or call")
        if self.strike <= 0 or self.multiplier <= 0:
            raise ValueError("strike and multiplier must be positive")
        if self.exercise_style not in {"american", "european"}:
            raise ValueError("exercise_style must be american or european")
        if self.settlement not in {"physical", "cash"}:
            raise ValueError("settlement must be physical or cash")

    def intrinsic_value(self, spot: float) -> float:
        if self.option_type == "put":
            return max(self.strike - spot, 0.0)
        return max(spot - self.strike, 0.0)


def black_scholes_put(
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    volatility: float,
    dividend_yield: float = 0.0,
) -> float:
    """Return a European put value, with safe expiry/zero-volatility behavior."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if time_years <= 0:
        return max(strike - spot, 0.0)
    if volatility <= 0:
        return max(strike * exp(-rate * time_years) - spot * exp(-dividend_yield * time_years), 0.0)
    root_time = sqrt(time_years)
    d1 = (
        log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility * volatility) * time_years
    ) / (volatility * root_time)
    d2 = d1 - volatility * root_time
    return (
        strike * exp(-rate * time_years) * _normal_cdf(-d2)
        - spot * exp(-dividend_yield * time_years) * _normal_cdf(-d1)
    )


def put_delta(
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    volatility: float,
    dividend_yield: float = 0.0,
) -> float:
    """Return the Black-Scholes delta of a long put."""
    if time_years <= 0:
        return -1.0 if spot < strike else 0.0
    if volatility <= 0:
        forward_spot = spot * exp((rate - dividend_yield) * time_years)
        return -exp(-dividend_yield * time_years) if forward_spot < strike else 0.0
    d1 = (
        log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility * volatility) * time_years
    ) / (volatility * sqrt(time_years))
    return exp(-dividend_yield * time_years) * (_normal_cdf(d1) - 1.0)
