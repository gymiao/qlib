"""Event-sourced cash account used by deterministic research backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Iterable


ZERO = Decimal("0")


def money(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    effective_at: str
    kind: str
    payload: dict[str, Any]
    available_at: str | None = None
    schema_version: str = "account_event.v1"

    def fingerprint(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)
        return sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class LedgerState:
    settled_cash: Decimal
    trade_receivable: Decimal = ZERO
    dividend_receivable: Decimal = ZERO
    trade_payable: Decimal = ZERO
    other_payable: Decimal = ZERO
    positions: dict[str, Decimal] = field(default_factory=dict)
    position_multipliers: dict[str, Decimal] = field(default_factory=dict)
    reservations: dict[str, Decimal] = field(default_factory=dict)
    collateral: dict[str, Decimal] = field(default_factory=dict)

    @property
    def available_cash(self) -> Decimal:
        available = (
            self.settled_cash
            - self.trade_payable
            - self.other_payable
            - sum(self.reservations.values(), ZERO)
            - sum(self.collateral.values(), ZERO)
        )
        return max(available, ZERO)

    def nav(self, marks: dict[str, float | Decimal]) -> Decimal:
        position_value = sum(
            (
                quantity * self.position_multipliers.get(instrument, Decimal("1")) * money(marks[instrument])
                for instrument, quantity in self.positions.items()
            ), ZERO
        )
        return (
            self.settled_cash
            + self.trade_receivable
            + self.dividend_receivable
            - self.trade_payable
            - self.other_payable
            + position_value
        )


class EventLedger:
    """Apply immutable account events exactly once and support deterministic replay."""

    def __init__(self, initial_cash: float | Decimal):
        if money(initial_cash) < 0:
            raise ValueError("initial_cash cannot be negative")
        self.initial_cash = money(initial_cash)
        self.state = LedgerState(self.initial_cash)
        self.events: list[LedgerEvent] = []
        self._fingerprints: dict[str, str] = {}

    def apply(self, event: LedgerEvent) -> bool:
        fingerprint = event.fingerprint()
        previous = self._fingerprints.get(event.event_id)
        if previous is not None:
            if previous != fingerprint:
                raise ValueError(f"event_id {event.event_id!r} was reused with different content")
            return False
        handler = getattr(self, f"_apply_{event.kind}", None)
        if handler is None:
            raise ValueError(f"unsupported ledger event: {event.kind}")
        handler(event.payload)
        self._fingerprints[event.event_id] = fingerprint
        self.events.append(event)
        return True

    def _apply_reserve(self, payload: dict[str, Any]) -> None:
        reservation_id = str(payload["reservation_id"])
        amount = money(payload["amount"])
        if amount < 0 or reservation_id in self.state.reservations:
            raise ValueError("invalid or duplicate reservation")
        if amount > self.state.available_cash:
            raise ValueError("insufficient settled cash for reservation")
        self.state.reservations[reservation_id] = amount

    def _apply_release_reserve(self, payload: dict[str, Any]) -> None:
        reservation_id = str(payload["reservation_id"])
        if reservation_id not in self.state.reservations:
            raise ValueError("unknown reservation")
        del self.state.reservations[reservation_id]

    def _consume_reservation(self, payload: dict[str, Any], required: Decimal) -> None:
        reservation_id = payload.get("reservation_id")
        if reservation_id is None:
            extra = self.state.trade_receivable if payload.get("allow_receivable") else ZERO
            if required > self.state.available_cash + extra:
                raise ValueError("insufficient settled cash")
            return
        reserved = self.state.reservations.get(str(reservation_id))
        if reserved is None or required > reserved:
            raise ValueError("fill exceeds reserved cash")
        del self.state.reservations[str(reservation_id)]

    def _apply_equity_fill(self, payload: dict[str, Any]) -> None:
        instrument = str(payload["instrument"])
        quantity = money(payload["quantity"])
        price = money(payload["price"])
        fee = money(payload.get("fee", 0))
        if not quantity or price <= 0 or fee < 0:
            raise ValueError("invalid equity fill")
        current = self.state.positions.get(instrument, ZERO)
        if current + quantity < 0:
            raise ValueError("short equity positions are disabled")
        if quantity > 0:
            payable = quantity * price + fee
            self._consume_reservation(payload, payable)
            self.state.trade_payable += payable
        else:
            self.state.trade_receivable += -quantity * price - fee
        remaining = current + quantity
        if remaining:
            self.state.positions[instrument] = remaining
            self.state.position_multipliers[instrument] = Decimal("1")
        else:
            self.state.positions.pop(instrument, None)
            self.state.position_multipliers.pop(instrument, None)

    def _apply_position_import(self, payload: dict[str, Any]) -> None:
        instrument = str(payload["instrument"])
        quantity = money(payload["quantity"])
        multiplier = money(payload.get("multiplier", 1))
        if not quantity or multiplier <= 0 or instrument in self.state.positions:
            raise ValueError("invalid or duplicate imported position")
        self.state.positions[instrument] = quantity
        self.state.position_multipliers[instrument] = multiplier

    def _apply_option_fill(self, payload: dict[str, Any]) -> None:
        contract_id = str(payload["contract_id"])
        quantity = money(payload["quantity"])
        premium = money(payload["premium"])
        multiplier = money(payload.get("multiplier", 100))
        fee = money(payload.get("fee", 0))
        if not quantity or premium < 0 or multiplier <= 0 or fee < 0:
            raise ValueError("invalid option fill")
        current = self.state.positions.get(contract_id, ZERO)
        existing_multiplier = self.state.position_multipliers.get(contract_id, multiplier)
        if existing_multiplier != multiplier:
            raise ValueError("option multiplier changed without an adjustment event")
        if quantity > 0:
            payable = quantity * multiplier * premium + fee
            self._consume_reservation(payload, payable)
            self.state.trade_payable += payable
        else:
            self.state.trade_receivable += -quantity * multiplier * premium - fee
        remaining = current + quantity
        if remaining:
            self.state.positions[contract_id] = remaining
            self.state.position_multipliers[contract_id] = multiplier
        else:
            self.state.positions.pop(contract_id, None)
            self.state.position_multipliers.pop(contract_id, None)

    def _apply_settle_trade_payable(self, payload: dict[str, Any]) -> None:
        amount = money(payload["amount"])
        if amount < 0 or amount > self.state.trade_payable or amount > self.state.settled_cash:
            raise ValueError("invalid trade payable settlement")
        self.state.trade_payable -= amount
        self.state.settled_cash -= amount

    def _apply_settle_trade_receivable(self, payload: dict[str, Any]) -> None:
        amount = money(payload["amount"])
        if amount < 0 or amount > self.state.trade_receivable:
            raise ValueError("invalid trade receivable settlement")
        self.state.trade_receivable -= amount
        self.state.settled_cash += amount

    def _apply_dividend_entitlement(self, payload: dict[str, Any]) -> None:
        amount = money(payload["amount"])
        if amount < 0:
            raise ValueError("dividend entitlement cannot be negative")
        self.state.dividend_receivable += amount

    def _apply_dividend_payment(self, payload: dict[str, Any]) -> None:
        amount = money(payload["amount"])
        if amount < 0 or amount > self.state.dividend_receivable:
            raise ValueError("invalid dividend payment")
        self.state.dividend_receivable -= amount
        self.state.settled_cash += amount

    def _apply_split(self, payload: dict[str, Any]) -> None:
        instrument = str(payload["instrument"])
        ratio = money(payload["ratio"])
        if ratio <= 0 or instrument not in self.state.positions:
            raise ValueError("invalid split")
        self.state.positions[instrument] *= ratio

    def _apply_long_put_exercise(self, payload: dict[str, Any]) -> None:
        contract_id = str(payload["contract_id"])
        underlying = str(payload["underlying"])
        contracts = money(payload["contracts"])
        strike = money(payload["strike"])
        multiplier = self.state.position_multipliers.get(contract_id)
        if contracts <= 0 or multiplier is None or self.state.positions.get(contract_id, ZERO) < contracts:
            raise ValueError("invalid long put exercise")
        shares = contracts * multiplier
        if self.state.positions.get(underlying, ZERO) < shares:
            raise ValueError("long put exercise would create a short stock position")
        self.state.positions[underlying] -= shares
        if not self.state.positions[underlying]:
            self.state.positions.pop(underlying)
            self.state.position_multipliers.pop(underlying, None)
        self.state.positions[contract_id] -= contracts
        if not self.state.positions[contract_id]:
            self.state.positions.pop(contract_id)
            self.state.position_multipliers.pop(contract_id, None)
        self.state.trade_receivable += shares * strike

    def _apply_short_put_assignment(self, payload: dict[str, Any]) -> None:
        contract_id = str(payload["contract_id"])
        underlying = str(payload["underlying"])
        contracts = money(payload["contracts"])
        strike = money(payload["strike"])
        collateral_id = str(payload["collateral_id"])
        multiplier = self.state.position_multipliers.get(contract_id)
        if contracts <= 0 or multiplier is None or self.state.positions.get(contract_id, ZERO) > -contracts:
            raise ValueError("invalid short put assignment")
        required = contracts * multiplier * strike
        collateral = self.state.collateral.get(collateral_id)
        if collateral is None or collateral < required:
            raise ValueError("assignment is not covered by collateral")
        if collateral == required:
            del self.state.collateral[collateral_id]
        else:
            self.state.collateral[collateral_id] = collateral - required
        self.state.trade_payable += required
        self.state.positions[underlying] = self.state.positions.get(underlying, ZERO) + contracts * multiplier
        self.state.position_multipliers[underlying] = Decimal("1")
        self.state.positions[contract_id] += contracts
        if not self.state.positions[contract_id]:
            self.state.positions.pop(contract_id)
            self.state.position_multipliers.pop(contract_id, None)

    def _apply_option_expire_worthless(self, payload: dict[str, Any]) -> None:
        contract_id = str(payload["contract_id"])
        contracts = money(payload["contracts"])
        current = self.state.positions.get(contract_id, ZERO)
        if not contracts or not current or (current > 0) != (contracts > 0) or abs(contracts) > abs(current):
            raise ValueError("invalid worthless expiration")
        self.state.positions[contract_id] -= contracts
        if not self.state.positions[contract_id]:
            self.state.positions.pop(contract_id)
            self.state.position_multipliers.pop(contract_id, None)
        collateral_id = payload.get("collateral_id")
        if collateral_id is not None:
            collateral_id = str(collateral_id)
            collateral = self.state.collateral.get(collateral_id)
            release = money(payload.get("collateral_release", collateral or 0))
            if collateral is None or release < 0 or release > collateral:
                raise ValueError("invalid collateral release at expiration")
            if release == collateral:
                del self.state.collateral[collateral_id]
            else:
                self.state.collateral[collateral_id] -= release

    def _apply_external_cash(self, payload: dict[str, Any]) -> None:
        amount = money(payload["amount"])
        if self.state.settled_cash + amount < 0:
            raise ValueError("external cash flow exceeds settled cash")
        self.state.settled_cash += amount

    def _apply_collateral_open(self, payload: dict[str, Any]) -> None:
        collateral_id = str(payload["collateral_id"])
        amount = money(payload["amount"])
        if amount < 0 or collateral_id in self.state.collateral or amount > self.state.available_cash:
            raise ValueError("invalid collateral")
        self.state.collateral[collateral_id] = amount

    def _apply_collateral_release(self, payload: dict[str, Any]) -> None:
        collateral_id = str(payload["collateral_id"])
        if collateral_id not in self.state.collateral:
            raise ValueError("unknown collateral")
        del self.state.collateral[collateral_id]

    def snapshot(self) -> dict[str, Any]:
        state = {
            "settled_cash": str(self.state.settled_cash),
            "trade_receivable": str(self.state.trade_receivable),
            "dividend_receivable": str(self.state.dividend_receivable),
            "trade_payable": str(self.state.trade_payable),
            "other_payable": str(self.state.other_payable),
            "positions": {key: str(value) for key, value in sorted(self.state.positions.items())},
            "position_multipliers": {key: str(value) for key, value in sorted(self.state.position_multipliers.items())},
            "reservations": {key: str(value) for key, value in sorted(self.state.reservations.items())},
            "collateral": {key: str(value) for key, value in sorted(self.state.collateral.items())},
            "last_sequence": len(self.events),
        }
        state["state_hash"] = sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return state

    def basis_hash(self) -> str:
        """Hash trading state while excluding marks, risk, and event sequence."""
        basis = self.snapshot().copy()
        basis.pop("state_hash")
        basis.pop("last_sequence")
        return sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @classmethod
    def replay(cls, initial_cash: float | Decimal, events: Iterable[LedgerEvent]) -> "EventLedger":
        ledger = cls(initial_cash)
        for event in events:
            ledger.apply(event)
        return ledger
