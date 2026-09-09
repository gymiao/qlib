"""Stage-1 ETF account replay built on the event-sourced ledger."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json

import pandas as pd

from .config import ResearchConfig
from .ledger import EventLedger, LedgerEvent
from .portfolio import target_weight_orders
from .risk import build_risk_snapshot


def _event_id(strategy: str, date: pd.Timestamp, suffix: str) -> str:
    return sha256(f"{strategy}|{date.isoformat()}|{suffix}".encode()).hexdigest()[:24]


def _metrics(nav: pd.Series) -> dict[str, float | int]:
    returns = nav.pct_change().dropna()
    drawdown = nav / nav.cummax() - 1
    return {
        "observations": int(len(nav)),
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "annualized_volatility": float(returns.std(ddof=1) * (252**0.5)) if len(returns) > 1 else 0.0,
        "max_drawdown": float(drawdown.min()),
    }


def replay_static_etf_portfolio(
    prices: pd.DataFrame,
    target_weights: dict[str, float],
    config: ResearchConfig,
    strategy_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame, EventLedger]:
    clean = prices[list(config.instruments)].sort_index().astype(float)
    if clean.empty or not clean.index.is_unique or clean.isna().any().any() or (clean <= 0).any().any():
        raise ValueError("prices require positive, complete, unique observations")
    ledger = EventLedger(config.account.seed_cash)
    first_date = clean.index[0]
    first_marks = clean.iloc[0].to_dict()
    orders = target_weight_orders(
        Decimal(str(config.account.seed_cash)),
        ledger.state.available_cash,
        {},
        target_weights,
        first_marks,
        config.execution.equity_one_way_cost_bps,
    )
    order_rows: list[dict] = []
    for index, order in enumerate(orders):
        cost = order.quantity * order.reference_price + order.estimated_fee
        reservation_id = f"{strategy_id}-{index}"
        ledger.apply(LedgerEvent(_event_id(strategy_id, first_date, f"reserve-{index}"), first_date.isoformat(), "reserve", {"reservation_id": reservation_id, "amount": str(cost)}))
        ledger.apply(LedgerEvent(_event_id(strategy_id, first_date, f"fill-{index}"), first_date.isoformat(), "equity_fill", {"instrument": order.instrument, "quantity": str(order.quantity), "price": str(order.reference_price), "fee": str(order.estimated_fee), "reservation_id": reservation_id}))
        order_rows.append({"date": first_date, "strategy": strategy_id, **asdict(order)})
    snapshots: list[dict] = []
    payable_settled = False
    for row_number, (current_date, marks) in enumerate(clean.iterrows()):
        if row_number >= config.execution.settlement_days and not payable_settled and ledger.state.trade_payable:
            amount = ledger.state.trade_payable
            ledger.apply(LedgerEvent(_event_id(strategy_id, current_date, "settle"), current_date.isoformat(), "settle_trade_payable", {"amount": str(amount)}))
            payable_settled = True
        nav = ledger.state.nav(marks.to_dict())
        snapshots.append({
            "date": current_date,
            "strategy": strategy_id,
            "nav": float(nav),
            "settled_cash": float(ledger.state.settled_cash),
            "trade_payable": float(ledger.state.trade_payable),
            "available_cash": float(ledger.state.available_cash),
            **{f"shares_{symbol}": float(ledger.state.positions.get(symbol, 0)) for symbol in config.instruments},
        })
    return pd.DataFrame(snapshots), pd.DataFrame(order_rows), ledger


def run_stage1_comparison(prices: pd.DataFrame, config: ResearchConfig | None = None) -> dict:
    config = config or ResearchConfig()
    curves = []
    orders = []
    event_logs: dict[str, list[dict]] = {}
    metrics: dict[str, dict] = {}
    risks: dict[str, dict] = {}
    for name, weights in (("initial_hold", config.initial_target_weights), ("cash_reduced", config.reduced_target_weights)):
        curve, strategy_orders, ledger = replay_static_etf_portfolio(prices, weights, config, name)
        curves.append(curve)
        orders.append(strategy_orders)
        event_logs[name] = [asdict(event) for event in ledger.events]
        metrics[name] = _metrics(curve.set_index("date")["nav"])
        final = curve.iloc[-1]
        risk = build_risk_snapshot(
            pd.Timestamp(final["date"]).isoformat(),
            float(final["shares_SPY"] * prices.loc[final["date"], "SPY"]),
            float(final["shares_QQQ"] * prices.loc[final["date"], "QQQ"]),
            prices[["SPY", "QQQ"]].pct_change().loc[: final["date"]],
        )
        risks[name] = asdict(risk)
    return {
        "schema_version": "research_report.v2",
        "kind": "research_report",
        "status": "complete",
        "config_hash": config.config_hash,
        "metrics": metrics,
        "risk_snapshots": risks,
        "equity_curves": pd.concat(curves, ignore_index=True),
        "orders": pd.concat(orders, ignore_index=True),
        "event_logs": event_logs,
    }
