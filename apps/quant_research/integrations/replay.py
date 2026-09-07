"""Deterministic replay of frozen research signals against price history."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Iterable

import pandas as pd

from .tradingagents import ResearchOverlayPolicy, ResearchSignal, apply_research_overlay, effective_signals


@dataclass(frozen=True)
class SignalReplayConfig:
    initial_cash: float = 100_000.0
    transaction_cost_bps: float = 2.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.transaction_cost_bps < 0:
            raise ValueError("initial_cash must be positive and transaction_cost_bps cannot be negative")


def _metrics(nav: pd.Series, initial_cash: float) -> dict[str, float | int]:
    returns = nav.pct_change().dropna()
    years = len(returns) / 252.0
    drawdown = nav / nav.cummax() - 1
    return {
        "observations": int(len(nav)),
        "total_return": float(nav.iloc[-1] / initial_cash - 1),
        "annualized_return": float((nav.iloc[-1] / initial_cash) ** (1 / years) - 1) if years else 0.0,
        "annualized_volatility": float(returns.std(ddof=1) * sqrt(252)) if len(returns) > 1 else 0.0,
        "max_drawdown": float(drawdown.min()),
    }


def run_signal_replay(
    prices: pd.DataFrame,
    base_weights: pd.Series,
    signals: Iterable[ResearchSignal],
    config: SignalReplayConfig | None = None,
    policy: ResearchOverlayPolicy | None = None,
) -> dict:
    """Replay a fixed base portfolio and a research-overlay portfolio.

    Every rebalance derives solely from signals available before that trading
    date.  This validates integration mechanics; frozen fixtures are not proof
    of historical LLM performance.
    """
    config = config or SignalReplayConfig()
    policy = policy or ResearchOverlayPolicy()
    clean = prices.sort_index().copy()
    if clean.empty or not clean.index.is_unique or not set(base_weights.index).issubset(clean.columns):
        raise ValueError("prices require unique dates and every base-weight symbol")
    clean = clean[list(base_weights.index)].dropna().astype(float)
    if (clean <= 0).any().any():
        raise ValueError("prices must be positive")
    base_weights = base_weights.astype(float)
    if (base_weights < 0).any() or base_weights.sum() > 1 + 1e-12:
        raise ValueError("base_weights must be non-negative and sum to at most one")
    signals = list(signals)
    fee_rate = config.transaction_cost_bps / 10_000

    def replay(use_overlay: bool) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
        cash = config.initial_cash
        shares = pd.Series(0.0, index=base_weights.index)
        nav_values: list[float] = []
        targets: list[dict] = []
        orders: list[dict] = []
        previous_target: pd.Series | None = None
        for current_date, row in clean.iterrows():
            as_of = pd.Timestamp(current_date).tz_localize("UTC")
            active = effective_signals(signals, as_of)
            target = apply_research_overlay(base_weights, active, policy) if use_overlay else base_weights
            should_rebalance = previous_target is None or not target.equals(previous_target)
            if should_rebalance:
                nav_before = cash + float((shares * row).sum())
                desired = nav_before * target / row
                # Sell first.  This exposes cash before later buys and records
                # a non-negative available cash balance at every fill.
                for symbol, quantity in (desired - shares).items():
                    if quantity < -1e-12:
                        value = -quantity * float(row[symbol])
                        fee = value * fee_rate
                        cash += value - fee
                        shares[symbol] += quantity
                        orders.append({"date": current_date, "symbol": symbol, "shares": float(quantity), "price": float(row[symbol]), "fee": fee, "side": "sell"})
                buys = (desired - shares).clip(lower=0)
                buy_cost = float((buys * row * (1 + fee_rate)).sum())
                if buy_cost > cash and buy_cost > 0:
                    buys *= cash / buy_cost
                for symbol, quantity in buys.items():
                    if quantity > 1e-12:
                        value = quantity * float(row[symbol])
                        fee = value * fee_rate
                        cash -= value + fee
                        shares[symbol] += quantity
                        orders.append({"date": current_date, "symbol": symbol, "shares": float(quantity), "price": float(row[symbol]), "fee": fee, "side": "buy"})
                targets.append({"date": current_date, "cash_weight": float(1 - target.sum()), **target.to_dict(), "active_signal_ids": ",".join(sorted(item.signal_id for item in active.values()))})
                previous_target = target
            nav_values.append(cash + float((shares * row).sum()))
        return pd.Series(nav_values, index=clean.index, name="equity"), pd.DataFrame(orders), pd.DataFrame(targets)

    base_nav, base_orders, _ = replay(False)
    overlay_nav, overlay_orders, targets = replay(True)
    return {
        "research_only": True,
        "signal_mode": "frozen_signal_replay_not_historical_llm_performance",
        "configuration": asdict(config),
        "date_range": [clean.index[0].date().isoformat(), clean.index[-1].date().isoformat()],
        "base_metrics": _metrics(base_nav, config.initial_cash),
        "overlay_metrics": _metrics(overlay_nav, config.initial_cash),
        "equity_curves": pd.concat([
            pd.DataFrame({"date": base_nav.index, "strategy": "base", "equity": base_nav.values}),
            pd.DataFrame({"date": overlay_nav.index, "strategy": "research_overlay", "equity": overlay_nav.values}),
        ], ignore_index=True),
        "orders": pd.concat([
            base_orders.assign(strategy="base"),
            overlay_orders.assign(strategy="research_overlay"),
        ], ignore_index=True),
        "targets": targets,
    }
