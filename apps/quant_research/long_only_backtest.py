"""Daily-account long-only replay for point-in-time model scores."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
from math import sqrt

import pandas as pd

from .ledger import EventLedger, LedgerEvent
from .stock_selection import select_long_only


def _event_id(strategy: str, date: pd.Timestamp, instrument: str, kind: str) -> str:
    raw = f"{strategy}|{date.isoformat()}|{instrument}|{kind}"
    return sha256(raw.encode()).hexdigest()[:24]


def _performance(nav: pd.Series, benchmark: pd.Series) -> dict:
    returns = nav.pct_change().dropna()
    benchmark_returns = benchmark.pct_change().reindex(returns.index).dropna()
    aligned = pd.concat([returns, benchmark_returns], axis=1).dropna()
    excess = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    drawdown = nav / nav.cummax() - 1
    return {
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "benchmark_return": float(benchmark.iloc[-1] / benchmark.iloc[0] - 1),
        "annualized_volatility": float(returns.std(ddof=1) * sqrt(252)) if len(returns) > 1 else 0.0,
        "information_ratio": float(excess.mean() / excess.std(ddof=1) * sqrt(252)) if len(excess) > 1 and excess.std(ddof=1) else 0.0,
        "max_drawdown": float(drawdown.min()),
    }


def run_long_only_backtest(
    scores: pd.Series,
    open_prices: pd.DataFrame,
    benchmark_open: pd.Series,
    top_k: int = 10,
    rebalance_every: int = 5,
    initial_cash: float = 100_000,
    cost_bps: float = 10,
    max_weight: float = 0.15,
    buffer_multiplier: float = 1.5,
    sectors: pd.Series | None = None,
    benchmark_sector_weights: dict[str, float] | None = None,
    max_sector_deviation: float | None = None,
) -> dict:
    if not isinstance(scores.index, pd.MultiIndex) or list(scores.index.names) != ["datetime", "instrument"]:
        raise ValueError("scores require a datetime/instrument MultiIndex")
    if top_k <= 0 or rebalance_every <= 0 or initial_cash <= 0 or cost_bps < 0:
        raise ValueError("invalid backtest configuration")
    prices = open_prices.sort_index().astype(float)
    benchmark = benchmark_open.sort_index().reindex(prices.index).astype(float)
    if prices.empty or prices.isna().all(axis=1).any() or benchmark.isna().any():
        raise ValueError("prices and benchmark require aligned observations")
    score_dates = pd.DatetimeIndex(sorted(scores.index.get_level_values("datetime").unique()))
    signal_dates = score_dates[::rebalance_every]
    execution_by_date = {}
    for signal_date in signal_dates:
        future = prices.index[prices.index > signal_date]
        if len(future):
            execution_by_date[future[0]] = signal_date
    ledger = EventLedger(initial_cash)
    previous: set[str] = set()
    pending_settlement = False
    curves = []
    order_rows = []
    holding_rows = []
    fee_rate = Decimal(str(cost_bps)) / Decimal("10000")
    for current_date, price_row in prices.iterrows():
        if pending_settlement:
            if ledger.state.trade_receivable:
                amount = ledger.state.trade_receivable
                ledger.apply(LedgerEvent(_event_id("long-only", current_date, "cash", "settle-receivable"), current_date.isoformat(), "settle_trade_receivable", {"amount": str(amount)}))
            if ledger.state.trade_payable:
                amount = ledger.state.trade_payable
                ledger.apply(LedgerEvent(_event_id("long-only", current_date, "cash", "settle-payable"), current_date.isoformat(), "settle_trade_payable", {"amount": str(amount)}))
            pending_settlement = False
        signal_date = execution_by_date.get(current_date)
        period_turnover = Decimal("0")
        if signal_date is not None:
            cross_section = scores.xs(signal_date).dropna()
            available_prices = price_row.dropna()
            cross_section = cross_section.loc[cross_section.index.intersection(available_prices.index)]
            weights = select_long_only(
                cross_section, top_k, previous, buffer_multiplier, max_weight,
                sectors, benchmark_sector_weights, max_sector_deviation,
            )
            marks = {symbol: float(price_row[symbol]) for symbol in ledger.state.positions if symbol in price_row and pd.notna(price_row[symbol])}
            nav = ledger.state.nav(marks)
            desired = {
                symbol: (nav * Decimal(str(weight)) / Decimal(str(price_row[symbol]))).to_integral_value(rounding=ROUND_DOWN)
                for symbol, weight in weights.items()
            }
            all_symbols = sorted(set(ledger.state.positions) | set(desired))
            trades = [(symbol, desired.get(symbol, Decimal("0")) - ledger.state.positions.get(symbol, Decimal("0"))) for symbol in all_symbols]
            for symbol, quantity in sorted(trades, key=lambda item: item[1] > 0):
                if not quantity:
                    continue
                price = Decimal(str(price_row[symbol]))
                fee = abs(quantity) * price * fee_rate
                ledger.apply(LedgerEvent(_event_id("long-only", current_date, symbol, "fill"), current_date.isoformat(), "equity_fill", {
                    "instrument": symbol, "quantity": str(quantity), "price": str(price),
                    "fee": str(fee), "allow_receivable": True,
                }))
                trade_value = abs(quantity) * price
                period_turnover += trade_value
                order_rows.append({"signal_date": signal_date, "execution_date": current_date, "instrument": symbol, "quantity": float(quantity), "price": float(price), "fee": float(fee)})
            pending_settlement = bool(ledger.state.trade_receivable or ledger.state.trade_payable)
            previous = set(weights.index)
            for symbol, weight in weights.items():
                holding_rows.append({"signal_date": signal_date, "execution_date": current_date, "instrument": symbol, "target_weight": float(weight), "score": float(cross_section[symbol])})
        marks = {symbol: float(price_row[symbol]) for symbol in ledger.state.positions if symbol in price_row and pd.notna(price_row[symbol])}
        nav = ledger.state.nav(marks)
        curves.append({"date": current_date, "nav": float(nav), "benchmark": float(benchmark.loc[current_date] / benchmark.iloc[0] * initial_cash), "turnover": float(period_turnover / nav) if nav else 0.0})
    curve = pd.DataFrame(curves).set_index("date")
    return {
        "schema_version": "long_only_report.v1",
        "status": "complete",
        "metrics": _performance(curve["nav"], curve["benchmark"]),
        "equity_curve": curve.reset_index(),
        "orders": pd.DataFrame(order_rows),
        "holdings": pd.DataFrame(holding_rows),
        "events": [asdict(event) for event in ledger.events],
    }
