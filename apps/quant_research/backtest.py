"""Comparable QQQ/SPY hedge and protective-put scenario backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from math import sqrt

import numpy as np
import pandas as pd

from .options import black_scholes_put


@dataclass(frozen=True)
class HedgeBacktestConfig:
    initial_cash: float = 100_000.0
    reduced_exposure: float = 0.70
    target_volatility: float = 0.12
    volatility_window: int = 20
    max_exposure: float = 1.0
    equity_cost_bps: float = 2.0
    put_coverage: float = 1.0
    put_moneyness: float = 0.95
    put_dte: int = 63
    put_roll_dte: int = 21
    put_iv_multiplier: float = 1.15
    annual_rate: float = 0.03
    option_fee_per_contract: float = 0.65
    option_half_spread: float = 0.03
    initial_volatility: float = 0.20

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 <= self.reduced_exposure <= 1 or not 0 <= self.put_coverage <= 1:
            raise ValueError("exposure and coverage must be between zero and one")
        if not 0 < self.put_moneyness <= 1 or self.put_dte <= self.put_roll_dte:
            raise ValueError("invalid put moneyness or DTE configuration")
        if self.initial_volatility <= 0:
            raise ValueError("initial_volatility must be positive")


def _metrics(equity: pd.Series, initial_cash: float) -> dict[str, float | int]:
    returns = equity.pct_change().dropna()
    if returns.empty:
        return {}
    years = len(returns) / 252.0
    total_return = float(equity.iloc[-1] / initial_cash - 1)
    annual_return = float((equity.iloc[-1] / initial_cash) ** (1 / years) - 1) if years else 0.0
    volatility = float(returns.std(ddof=1) * sqrt(252))
    drawdown = equity / equity.cummax() - 1
    downside = returns[returns < 0]
    expected_shortfall = float(downside[downside <= returns.quantile(0.05)].mean()) if len(downside) else 0.0
    return {
        "observations": int(len(equity)),
        "total_return": total_return,
        "annualized_return": annual_return,
        "annualized_volatility": volatility,
        "sharpe": float((returns.mean() - 0.0) / returns.std(ddof=1) * sqrt(252)) if returns.std(ddof=1) else 0.0,
        "max_drawdown": float(drawdown.min()),
        "daily_expected_shortfall_95": expected_shortfall,
    }


def _cash_growth(cash: float, annual_rate: float, days: int = 1) -> float:
    return cash * (1.0 + annual_rate * days / 365.0)


def _static_exposure(prices: pd.Series, exposure: float, config: HedgeBacktestConfig) -> pd.Series:
    first = float(prices.iloc[0])
    fee_rate = config.equity_cost_bps / 10_000
    shares = config.initial_cash * exposure / (first * (1 + fee_rate))
    cash = config.initial_cash - shares * first * (1 + fee_rate)
    values = []
    previous_date = prices.index[0]
    for current_date, price in prices.items():
        elapsed = max((current_date - previous_date).days, 0)
        cash = _cash_growth(cash, config.annual_rate, elapsed)
        values.append(cash + shares * float(price))
        previous_date = current_date
    return pd.Series(values, index=prices.index)


def _volatility_target(prices: pd.Series, config: HedgeBacktestConfig) -> pd.Series:
    returns = prices.pct_change()
    realized = returns.rolling(config.volatility_window).std() * sqrt(252)
    target = (config.target_volatility / realized.shift(1)).clip(0, config.max_exposure).fillna(config.reduced_exposure)
    cash = config.initial_cash
    shares = 0.0
    values = []
    previous_date = prices.index[0]
    fee_rate = config.equity_cost_bps / 10_000
    for current_date, price in prices.items():
        elapsed = max((current_date - previous_date).days, 0)
        cash = _cash_growth(cash, config.annual_rate, elapsed)
        nav_before = cash + shares * float(price)
        desired_shares = nav_before * float(target.loc[current_date]) / float(price)
        trade = desired_shares - shares
        if trade > 0:
            affordable_trade = max(cash, 0.0) / (float(price) * (1 + fee_rate))
            trade = min(trade, affordable_trade)
            desired_shares = shares + trade
        cash -= trade * float(price) + abs(trade * float(price)) * fee_rate
        shares = desired_shares
        values.append(cash + shares * float(price))
        previous_date = current_date
    return pd.Series(values, index=prices.index)


def _protective_put(prices: pd.Series, config: HedgeBacktestConfig) -> tuple[pd.Series, pd.DataFrame]:
    returns = prices.pct_change()
    trailing_vol = (returns.rolling(config.volatility_window).std() * sqrt(252)).shift(1)
    fallback_vol = config.initial_volatility
    first = float(prices.iloc[0])
    fee_rate = config.equity_cost_bps / 10_000
    shares = config.initial_cash * config.reduced_exposure / (first * (1 + fee_rate))
    cash = config.initial_cash - shares * first * (1 + fee_rate)
    contracts = int(shares * config.put_coverage // 100)
    expiration = None
    strike = 0.0
    option_value = 0.0
    values: list[float] = []
    trades: list[dict] = []
    previous_date = prices.index[0]

    for current_date, price_value in prices.items():
        spot = float(price_value)
        elapsed = max((current_date - previous_date).days, 0)
        cash = _cash_growth(cash, config.annual_rate, elapsed)
        volatility = float(trailing_vol.loc[current_date]) if np.isfinite(trailing_vol.loc[current_date]) else fallback_vol
        volatility = max(volatility * config.put_iv_multiplier, 0.05)
        if expiration is not None:
            remaining = max((expiration - current_date.date()).days, 0) / 365.0
            option_value = black_scholes_put(spot, strike, remaining, config.annual_rate, volatility)
        days_left = (expiration - current_date.date()).days if expiration else -1
        should_roll = contracts > 0 and (expiration is None or days_left <= config.put_roll_dte)
        if should_roll:
            if expiration is not None:
                theoretical_bid = max(option_value * (1 - config.option_half_spread), 0.0)
                proceeds = contracts * 100 * theoretical_bid - contracts * config.option_fee_per_contract
                cash += proceeds
                trades.append({"date": current_date, "action": "sell_to_close", "strike": strike, "contracts": contracts, "premium": theoretical_bid, "cash_change": proceeds})
            expiration = current_date.date() + timedelta(days=config.put_dte)
            strike = spot * config.put_moneyness
            theoretical = black_scholes_put(spot, strike, config.put_dte / 365.0, config.annual_rate, volatility)
            ask = theoretical * (1 + config.option_half_spread)
            cost = contracts * 100 * ask + contracts * config.option_fee_per_contract
            if cost <= cash:
                cash -= cost
                option_value = theoretical
                trades.append({"date": current_date, "action": "buy_to_open", "strike": strike, "contracts": contracts, "premium": ask, "cash_change": -cost})
            else:
                expiration = None
                strike = 0.0
                option_value = 0.0
        values.append(cash + shares * spot + contracts * 100 * option_value)
        previous_date = current_date
    return pd.Series(values, index=prices.index), pd.DataFrame(trades)


def run_hedge_comparison(prices: pd.DataFrame, config: HedgeBacktestConfig | None = None) -> dict:
    """Run comparable buy/hold, cash reduction, vol target and put scenarios."""
    config = config or HedgeBacktestConfig()
    if not {"QQQ", "SPY"}.issubset(prices.columns):
        raise ValueError("prices requires QQQ and SPY columns")
    clean = prices[["QQQ", "SPY"]].sort_index().dropna()
    if len(clean) < config.volatility_window + 2 or not clean.index.is_unique:
        raise ValueError("prices need unique dates and sufficient aligned history")
    results: dict[str, dict] = {}
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    for symbol in ("QQQ", "SPY"):
        series = clean[symbol]
        strategies = {
            "buy_hold": _static_exposure(series, 1.0, config),
            "cash_reduced": _static_exposure(series, config.reduced_exposure, config),
            "volatility_target": _volatility_target(series, config),
        }
        protected, trades = _protective_put(series, config)
        strategies["protective_put_model"] = protected
        symbol_result = {}
        for name, equity in strategies.items():
            symbol_result[name] = _metrics(equity, config.initial_cash)
            all_equity.append(pd.DataFrame({"date": equity.index, "symbol": symbol, "strategy": name, "equity": equity.values}))
        if not trades.empty:
            trades.insert(1, "symbol", symbol)
            all_trades.append(trades)
        results[symbol] = symbol_result
    return {
        "research_only": True,
        "option_data_mode": "black_scholes_scenario_not_historical_quotes",
        "configuration": asdict(config),
        "date_range": [clean.index[0].date().isoformat(), clean.index[-1].date().isoformat()],
        "results": results,
        "equity_curves": pd.concat(all_equity, ignore_index=True),
        "option_trades": pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(),
    }
