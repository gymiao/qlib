"""Connect a current signal to portfolio construction and paper execution."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256

import pandas as pd

from .contracts import OrderPlan, SignalBatch
from .ledger import EventLedger
from .portfolio import target_weight_orders
from .stock_selection import select_long_only


def plan_signal_orders(
    signal: SignalBatch,
    ledger: EventLedger,
    marks: dict[str, float],
    as_of: datetime,
    top_k: int = 10,
    max_weight: float = 0.15,
    fee_bps: float = 10,
    sectors: pd.Series | None = None,
    benchmark_sector_weights: dict[str, float] | None = None,
    max_sector_deviation: float | None = None,
) -> OrderPlan:
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    timestamp = as_of.astimezone(timezone.utc)
    raw_decision = datetime.fromisoformat(signal.decision_time.replace("Z", "+00:00"))
    raw_valid_until = datetime.fromisoformat(signal.valid_until.replace("Z", "+00:00"))
    if raw_decision.tzinfo is None or raw_valid_until.tzinfo is None:
        raise ValueError("signal times must include a timezone")
    decision = raw_decision.astimezone(timezone.utc)
    valid_until = raw_valid_until.astimezone(timezone.utc)
    if not decision <= timestamp < valid_until:
        raise ValueError("signal is not currently valid")
    weights = select_long_only(
        pd.Series(signal.scores), top_k, max_weight=max_weight,
        sectors=sectors, benchmark_sector_weights=benchmark_sector_weights,
        max_sector_deviation=max_sector_deviation,
    )
    nav = ledger.state.nav({instrument: marks[instrument] for instrument in ledger.state.positions})
    planned = target_weight_orders(
        nav,
        ledger.state.available_cash,
        ledger.state.positions,
        weights.to_dict(),
        marks,
        fee_bps,
    )
    orders = tuple({
        "instrument": order.instrument,
        "quantity": float(order.quantity),
        "max_price": float(order.reference_price) * 1.01,
        "estimated_fee": float(order.estimated_fee),
    } for order in planned)
    identity = f"{signal.signal_id}|{ledger.basis_hash()}|{timestamp.isoformat()}|{orders}"
    return OrderPlan(
        plan_id=sha256(identity.encode()).hexdigest()[:24],
        plan_basis_hash=ledger.basis_hash(),
        account_snapshot_ref=ledger.snapshot()["state_hash"],
        source_ids=(signal.signal_id,),
        orders=orders,
        reservations={order["instrument"]: order["quantity"] * marks[order["instrument"]] for order in orders if order["quantity"] > 0},
        expires_at=signal.valid_until,
    )
