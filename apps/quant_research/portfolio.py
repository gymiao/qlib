"""Deterministic intent netting and integer order planning."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class ShareIntent:
    strategy_id: str
    instrument: str
    quantity: Decimal


@dataclass(frozen=True)
class PlannedOrder:
    instrument: str
    quantity: Decimal
    reference_price: Decimal
    estimated_fee: Decimal
    attribution: dict[str, Decimal]


def net_share_intents(intents: Iterable[ShareIntent]) -> dict[str, PlannedOrder]:
    grouped: dict[str, list[ShareIntent]] = {}
    for intent in intents:
        grouped.setdefault(intent.instrument, []).append(intent)
    result: dict[str, PlannedOrder] = {}
    for instrument, items in grouped.items():
        total = sum((item.quantity for item in items), Decimal("0"))
        if total:
            result[instrument] = PlannedOrder(
                instrument=instrument,
                quantity=total,
                reference_price=Decimal("0"),
                estimated_fee=Decimal("0"),
                attribution={item.strategy_id: item.quantity for item in items},
            )
    return result


def target_weight_orders(
    nav: Decimal,
    settled_available_cash: Decimal,
    current_shares: dict[str, Decimal],
    target_weights: dict[str, float],
    prices: dict[str, float],
    fee_bps: float,
) -> list[PlannedOrder]:
    if not nav.is_finite() or not settled_available_cash.is_finite() or nav <= 0 or settled_available_cash < 0 or not isfinite(fee_bps) or fee_bps < 0:
        raise ValueError("invalid account values for planning")
    required_prices = set(target_weights) | {symbol for symbol, quantity in current_shares.items() if quantity}
    if required_prices - set(prices) or any(not isfinite(weight) or weight < 0 for weight in target_weights.values()):
        raise ValueError("target weights require valid prices and non-negative values")
    if sum(target_weights.values()) > 1 + 1e-12:
        raise ValueError("target weights exceed capital")
    fee_rate = Decimal(str(fee_bps)) / Decimal("10000")
    orders: list[PlannedOrder] = []
    for instrument in sorted(required_prices):
        price = Decimal(str(prices[instrument]))
        if not price.is_finite() or price <= 0:
            raise ValueError("missing or invalid execution price")
        desired = (
            nav * Decimal(str(target_weights.get(instrument, 0))) / price
        ).to_integral_value(rounding=ROUND_DOWN)
        quantity = desired - current_shares.get(instrument, Decimal("0"))
        if quantity:
            fee = (abs(quantity) * price * fee_rate).quantize(Decimal("0.000001"))
            orders.append(PlannedOrder(instrument, quantity, price, fee, {"target_weight": quantity}))
    buy_cost = sum((order.quantity * order.reference_price + order.estimated_fee for order in orders if order.quantity > 0), Decimal("0"))
    # In a settled-cash account sale proceeds cannot fund same-session buys.
    if buy_cost > settled_available_cash:
        raise ValueError("insufficient settled cash for planned buys")
    return sorted(orders, key=lambda order: (order.quantity > 0, order.instrument))
