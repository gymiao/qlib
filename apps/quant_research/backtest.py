"""Comparable QQQ/SPY hedge and protective-put scenario backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from math import sqrt

import numpy as np
import pandas as pd

from .options import black_scholes_call, black_scholes_put


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
    put_spread_width: float = 0.10
    put_dte: int = 63
    put_roll_dte: int = 21
    put_iv_multiplier: float = 1.15
    put_volatility_skew: float = 1.50
    call_volatility_skew: float = 0.50
    dynamic_put_volatility_trigger: float = 0.20
    annual_rate: float = 0.03
    option_fee_per_contract: float = 0.65
    option_half_spread: float = 0.03
    initial_volatility: float = 0.20
    call_coverage: float = 1.0
    call_moneyness: float = 1.05
    dynamic_trend_window: int = 50
    dynamic_risk_off_exposure: float = 0.35
    dynamic_rebalance_threshold: float = 0.05

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 <= self.reduced_exposure <= 1 or not 0 <= self.put_coverage <= 1:
            raise ValueError("exposure and coverage must be between zero and one")
        if (
            not 0 < self.put_moneyness <= 1
            or not 0 < self.put_spread_width < self.put_moneyness
            or self.put_dte <= self.put_roll_dte
        ):
            raise ValueError("invalid put moneyness or DTE configuration")
        if (
            self.initial_volatility <= 0
            or self.put_volatility_skew < 0
            or self.call_volatility_skew < 0
            or self.dynamic_put_volatility_trigger <= 0
        ):
            raise ValueError("initial_volatility must be positive")
        if (
            not 0 <= self.call_coverage <= 1
            or self.call_moneyness < 1
            or self.dynamic_trend_window < 2
            or not 0 <= self.dynamic_risk_off_exposure <= self.max_exposure
            or not 0 <= self.dynamic_rebalance_threshold <= 1
        ):
            raise ValueError("invalid covered-call or dynamic-hedge configuration")


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


def _smile_volatility(base_volatility: float, spot: float, strike: float, skew: float) -> float:
    """Frozen one-sided downside skew for scenario pricing only."""
    moneyness_distance = abs(1.0 - strike / spot)
    return max(base_volatility * (1.0 + skew * moneyness_distance), 0.05)


def _scenario_put_value(
    spot: float, strike: float, years: float, volatility: float, config: HedgeBacktestConfig
) -> float:
    smile_vol = _smile_volatility(volatility, spot, strike, config.put_volatility_skew)
    return black_scholes_put(spot, strike, years, config.annual_rate, smile_vol)


def _scenario_call_value(
    spot: float, strike: float, years: float, volatility: float, config: HedgeBacktestConfig
) -> float:
    smile_vol = _smile_volatility(volatility, spot, strike, config.call_volatility_skew)
    return black_scholes_call(spot, strike, years, config.annual_rate, smile_vol)


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


def _dynamic_trend_volatility_hedge(prices: pd.Series, config: HedgeBacktestConfig) -> pd.Series:
    """Lagged trend/volatility cash hedge with a rebalance deadband."""
    returns = prices.pct_change()
    realized = (returns.rolling(config.volatility_window).std() * sqrt(252)).shift(1)
    trend = prices.shift(1).rolling(config.dynamic_trend_window).mean()
    prior = prices.shift(1)
    volatility_target = (config.target_volatility / realized).clip(0, config.max_exposure)
    target = volatility_target.fillna(config.reduced_exposure)
    risk_off = trend.notna() & (prior < trend)
    target = target.where(~risk_off, target.clip(upper=config.dynamic_risk_off_exposure))

    cash = config.initial_cash
    shares = 0.0
    values: list[float] = []
    previous_date = prices.index[0]
    fee_rate = config.equity_cost_bps / 10_000
    current_exposure = 0.0
    for current_date, price_value in prices.items():
        price = float(price_value)
        elapsed = max((current_date - previous_date).days, 0)
        cash = _cash_growth(cash, config.annual_rate, elapsed)
        nav_before = cash + shares * price
        desired_exposure = float(target.loc[current_date])
        if abs(desired_exposure - current_exposure) >= config.dynamic_rebalance_threshold:
            desired_shares = nav_before * desired_exposure / price
            trade = desired_shares - shares
            if trade > 0:
                trade = min(trade, max(cash, 0.0) / (price * (1 + fee_rate)))
            cash -= trade * price + abs(trade * price) * fee_rate
            shares += trade
            current_exposure = shares * price / max(cash + shares * price, 1e-12)
        values.append(cash + shares * price)
        previous_date = current_date
    return pd.Series(values, index=prices.index)


def _short_call_or_collar(
    prices: pd.Series,
    config: HedgeBacktestConfig,
    *,
    include_put: bool,
) -> tuple[pd.Series, pd.DataFrame]:
    """Scenario replay for a covered call or a collar, rolled before expiry."""
    returns = prices.pct_change()
    trailing_vol = (returns.rolling(config.volatility_window).std() * sqrt(252)).shift(1)
    first = float(prices.iloc[0])
    fee_rate = config.equity_cost_bps / 10_000
    shares = config.initial_cash / (first * (1 + fee_rate))
    cash = config.initial_cash - shares * first * (1 + fee_rate)
    contracts = int(shares * config.call_coverage // 100)
    expiration = None
    call_strike = 0.0
    put_strike = 0.0
    call_value = 0.0
    put_value = 0.0
    active = False
    values: list[float] = []
    trades: list[dict] = []
    previous_date = prices.index[0]

    for current_date, price_value in prices.items():
        spot = float(price_value)
        elapsed = max((current_date - previous_date).days, 0)
        cash = _cash_growth(cash, config.annual_rate, elapsed)
        volatility = float(trailing_vol.loc[current_date]) if np.isfinite(trailing_vol.loc[current_date]) else config.initial_volatility
        volatility = max(volatility * config.put_iv_multiplier, 0.05)
        if active and expiration is not None:
            remaining = max((expiration - current_date.date()).days, 0) / 365.0
            call_value = _scenario_call_value(spot, call_strike, remaining, volatility, config)
            if include_put:
                put_value = _scenario_put_value(spot, put_strike, remaining, volatility, config)
        days_left = (expiration - current_date.date()).days if expiration else -1
        should_roll = contracts > 0 and (not active or days_left <= config.put_roll_dte)
        if should_roll:
            if active:
                call_ask = call_value * (1 + config.option_half_spread)
                call_close = contracts * 100 * call_ask + contracts * config.option_fee_per_contract
                cash -= call_close
                trades.append({"date": current_date, "leg": "call", "action": "buy_to_close", "strike": call_strike, "contracts": contracts, "premium": call_ask, "cash_change": -call_close})
                if include_put:
                    put_bid = max(put_value * (1 - config.option_half_spread), 0.0)
                    put_close = contracts * 100 * put_bid - contracts * config.option_fee_per_contract
                    cash += put_close
                    trades.append({"date": current_date, "leg": "put", "action": "sell_to_close", "strike": put_strike, "contracts": contracts, "premium": put_bid, "cash_change": put_close})
                active = False
                if cash < 0:
                    sale_price = spot * (1 - fee_rate)
                    shares_sold = min(shares, -cash / sale_price)
                    proceeds = shares_sold * sale_price
                    shares -= shares_sold
                    cash += proceeds
                    trades.append({
                        "date": current_date, "leg": "equity",
                        "action": "sell_to_fund_roll", "strike": None,
                        "contracts": 0, "premium": spot, "cash_change": proceeds,
                    })
                contracts = int(shares * config.call_coverage // 100)
                if contracts <= 0:
                    expiration = None
                    call_value = 0.0
                    put_value = 0.0
                    values.append(cash + shares * spot)
                    previous_date = current_date
                    continue
            expiration = current_date.date() + timedelta(days=config.put_dte)
            call_strike = spot * config.call_moneyness
            call_value = _scenario_call_value(
                spot, call_strike, config.put_dte / 365.0, volatility, config
            )
            call_bid = max(call_value * (1 - config.option_half_spread), 0.0)
            call_credit = contracts * 100 * call_bid - contracts * config.option_fee_per_contract
            put_cost = 0.0
            if include_put:
                put_strike = spot * config.put_moneyness
                put_value = _scenario_put_value(
                    spot, put_strike, config.put_dte / 365.0, volatility, config
                )
                put_ask = put_value * (1 + config.option_half_spread)
                put_cost = contracts * 100 * put_ask + contracts * config.option_fee_per_contract
            if put_cost <= cash + call_credit:
                cash += call_credit - put_cost
                trades.append({"date": current_date, "leg": "call", "action": "sell_to_open", "strike": call_strike, "contracts": contracts, "premium": call_bid, "cash_change": call_credit})
                if include_put:
                    trades.append({"date": current_date, "leg": "put", "action": "buy_to_open", "strike": put_strike, "contracts": contracts, "premium": put_ask, "cash_change": -put_cost})
                active = True
            else:
                call_value = 0.0
                put_value = 0.0
                active = False
        option_value = contracts * 100 * (put_value if include_put and active else 0.0)
        option_value -= contracts * 100 * (call_value if active else 0.0)
        values.append(cash + shares * spot + option_value)
        previous_date = current_date
    return pd.Series(values, index=prices.index), pd.DataFrame(trades)


def _protective_put(
    prices: pd.Series,
    config: HedgeBacktestConfig,
    *,
    dynamic: bool = False,
) -> tuple[pd.Series, pd.DataFrame]:
    returns = prices.pct_change()
    trailing_vol = (returns.rolling(config.volatility_window).std() * sqrt(252)).shift(1)
    prior = prices.shift(1)
    trend = prior.rolling(config.dynamic_trend_window).mean()
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
        protection_signal = not dynamic or (
            pd.notna(trend.loc[current_date])
            and (
                float(prior.loc[current_date]) < float(trend.loc[current_date])
                or (
                    np.isfinite(trailing_vol.loc[current_date])
                    and float(trailing_vol.loc[current_date]) >= config.dynamic_put_volatility_trigger
                )
            )
        )
        if expiration is not None:
            remaining = max((expiration - current_date.date()).days, 0) / 365.0
            option_value = _scenario_put_value(spot, strike, remaining, volatility, config)
        if expiration is not None and not protection_signal:
            theoretical_bid = max(option_value * (1 - config.option_half_spread), 0.0)
            proceeds = contracts * 100 * theoretical_bid - contracts * config.option_fee_per_contract
            cash += proceeds
            trades.append({
                "date": current_date,
                "action": "sell_to_close_signal_off",
                "strike": strike,
                "contracts": contracts,
                "premium": theoretical_bid,
                "cash_change": proceeds,
            })
            expiration = None
            strike = 0.0
            option_value = 0.0
        days_left = (expiration - current_date.date()).days if expiration else -1
        should_roll = contracts > 0 and protection_signal and (
            expiration is None or days_left <= config.put_roll_dte
        )
        if should_roll:
            if expiration is not None:
                theoretical_bid = max(option_value * (1 - config.option_half_spread), 0.0)
                proceeds = contracts * 100 * theoretical_bid - contracts * config.option_fee_per_contract
                cash += proceeds
                trades.append({"date": current_date, "action": "sell_to_close", "strike": strike, "contracts": contracts, "premium": theoretical_bid, "cash_change": proceeds})
            expiration = current_date.date() + timedelta(days=config.put_dte)
            strike = spot * config.put_moneyness
            theoretical = _scenario_put_value(
                spot, strike, config.put_dte / 365.0, volatility, config
            )
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


def _bear_put_spread(prices: pd.Series, config: HedgeBacktestConfig) -> tuple[pd.Series, pd.DataFrame]:
    """Scenario long put spread with executable-side spread and roll accounting."""
    returns = prices.pct_change()
    trailing_vol = (returns.rolling(config.volatility_window).std() * sqrt(252)).shift(1)
    first = float(prices.iloc[0])
    fee_rate = config.equity_cost_bps / 10_000
    shares = config.initial_cash * config.reduced_exposure / (first * (1 + fee_rate))
    cash = config.initial_cash - shares * first * (1 + fee_rate)
    contracts = int(shares * config.put_coverage // 100)
    expiration = None
    long_strike = 0.0
    short_strike = 0.0
    long_value = 0.0
    short_value = 0.0
    values: list[float] = []
    trades: list[dict] = []
    previous_date = prices.index[0]

    for current_date, price_value in prices.items():
        spot = float(price_value)
        elapsed = max((current_date - previous_date).days, 0)
        cash = _cash_growth(cash, config.annual_rate, elapsed)
        base_vol = (
            float(trailing_vol.loc[current_date])
            if np.isfinite(trailing_vol.loc[current_date]) else config.initial_volatility
        )
        base_vol = max(base_vol * config.put_iv_multiplier, 0.05)
        if expiration is not None:
            remaining = max((expiration - current_date.date()).days, 0) / 365.0
            long_value = _scenario_put_value(spot, long_strike, remaining, base_vol, config)
            short_value = _scenario_put_value(spot, short_strike, remaining, base_vol, config)
        days_left = (expiration - current_date.date()).days if expiration else -1
        if contracts > 0 and (expiration is None or days_left <= config.put_roll_dte):
            if expiration is not None:
                long_bid = max(long_value * (1 - config.option_half_spread), 0.0)
                short_ask = short_value * (1 + config.option_half_spread)
                close_cash = contracts * 100 * (long_bid - short_ask) - 2 * contracts * config.option_fee_per_contract
                cash += close_cash
                trades.extend([
                    {"date": current_date, "leg": "long_put", "action": "sell_to_close", "strike": long_strike, "contracts": contracts, "premium": long_bid, "cash_change": contracts * 100 * long_bid - contracts * config.option_fee_per_contract},
                    {"date": current_date, "leg": "short_put", "action": "buy_to_close", "strike": short_strike, "contracts": contracts, "premium": short_ask, "cash_change": -contracts * 100 * short_ask - contracts * config.option_fee_per_contract},
                ])
            expiration = current_date.date() + timedelta(days=config.put_dte)
            long_strike = spot * config.put_moneyness
            short_strike = spot * (config.put_moneyness - config.put_spread_width)
            years = config.put_dte / 365.0
            long_value = _scenario_put_value(spot, long_strike, years, base_vol, config)
            short_value = _scenario_put_value(spot, short_strike, years, base_vol, config)
            long_ask = long_value * (1 + config.option_half_spread)
            short_bid = max(short_value * (1 - config.option_half_spread), 0.0)
            open_cost = contracts * 100 * (long_ask - short_bid) + 2 * contracts * config.option_fee_per_contract
            if open_cost <= cash:
                cash -= open_cost
                trades.extend([
                    {"date": current_date, "leg": "long_put", "action": "buy_to_open", "strike": long_strike, "contracts": contracts, "premium": long_ask, "cash_change": -contracts * 100 * long_ask - contracts * config.option_fee_per_contract},
                    {"date": current_date, "leg": "short_put", "action": "sell_to_open", "strike": short_strike, "contracts": contracts, "premium": short_bid, "cash_change": contracts * 100 * short_bid - contracts * config.option_fee_per_contract},
                ])
            else:
                expiration = None
                long_value = 0.0
                short_value = 0.0
        values.append(cash + shares * spot + contracts * 100 * (long_value - short_value))
        previous_date = current_date
    return pd.Series(values, index=prices.index), pd.DataFrame(trades)


def run_hedge_comparison(prices: pd.DataFrame, config: HedgeBacktestConfig | None = None) -> dict:
    """Compare cash, dynamic hedge, put, covered-call, and collar scenarios."""
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
            "dynamic_trend_volatility_hedge": _dynamic_trend_volatility_hedge(series, config),
        }
        protected, trades = _protective_put(series, config)
        strategies["protective_put_model"] = protected
        dynamic_protected, dynamic_put_trades = _protective_put(series, config, dynamic=True)
        strategies["dynamic_protective_put_model"] = dynamic_protected
        spread, spread_trades = _bear_put_spread(series, config)
        strategies["bear_put_spread_model"] = spread
        covered, covered_trades = _short_call_or_collar(series, config, include_put=False)
        collar, collar_trades = _short_call_or_collar(series, config, include_put=True)
        strategies["covered_call_model"] = covered
        strategies["collar_model"] = collar
        symbol_result = {}
        for name, equity in strategies.items():
            symbol_result[name] = _metrics(equity, config.initial_cash)
            all_equity.append(pd.DataFrame({"date": equity.index, "symbol": symbol, "strategy": name, "equity": equity.values}))
        if not trades.empty:
            trades.insert(1, "symbol", symbol)
            trades.insert(2, "strategy", "protective_put_model")
            all_trades.append(trades)
        if not dynamic_put_trades.empty:
            dynamic_put_trades.insert(1, "symbol", symbol)
            dynamic_put_trades.insert(2, "strategy", "dynamic_protective_put_model")
            all_trades.append(dynamic_put_trades)
        if not spread_trades.empty:
            spread_trades.insert(1, "symbol", symbol)
            spread_trades.insert(2, "strategy", "bear_put_spread_model")
            all_trades.append(spread_trades)
        for name, option_frame in (
            ("covered_call_model", covered_trades),
            ("collar_model", collar_trades),
        ):
            if not option_frame.empty:
                option_frame.insert(1, "symbol", symbol)
                option_frame.insert(2, "strategy", name)
                all_trades.append(option_frame)
        results[symbol] = symbol_result
    return {
        "research_only": True,
        "schema_version": "hedge_comparison.v3",
        "option_data_mode": "black_scholes_scenario_not_historical_quotes",
        "claim_boundary": "scenario_comparison_not_historical_option_performance_or_trading_advice",
        "configuration": asdict(config),
        "date_range": [clean.index[0].date().isoformat(), clean.index[-1].date().isoformat()],
        "results": results,
        "equity_curves": pd.concat(all_equity, ignore_index=True),
        "option_trades": pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(),
    }
