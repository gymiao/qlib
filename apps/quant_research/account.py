"""A small auditable ledger for mixed equity and option research accounts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .options import OptionContract


@dataclass
class EquityPosition:
    symbol: str
    shares: float
    factor_loadings: dict[str, float] = field(default_factory=dict)


@dataclass
class OptionPosition:
    contract: OptionContract
    contracts: int
    delta: float = 0.0


@dataclass
class AccountEvent:
    event_date: date
    kind: str
    instrument: str
    quantity: float
    cash_change: float
    fee: float = 0.0
    note: str = ""


class Account:
    """Cash and position ledger with explicit option reserve and settlement."""

    def __init__(self, initial_cash: float):
        if initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        self.cash = float(initial_cash)
        self.equities: dict[str, EquityPosition] = {}
        self.options: dict[str, OptionPosition] = {}
        self.events: list[AccountEvent] = []

    def trade_equity(self, event_date: date, symbol: str, shares: float, price: float, fee: float = 0.0) -> None:
        if price <= 0 or fee < 0:
            raise ValueError("price must be positive and fee cannot be negative")
        cash_change = -shares * price - fee
        if self.cash + cash_change < -1e-8:
            raise ValueError("insufficient cash")
        position = self.equities.setdefault(symbol, EquityPosition(symbol, 0.0))
        position.shares += shares
        self.cash += cash_change
        self.events.append(AccountEvent(event_date, "equity_trade", symbol, shares, cash_change, fee))

    def trade_option(
        self,
        event_date: date,
        contract: OptionContract,
        contracts: int,
        premium: float,
        fee_per_contract: float = 0.0,
    ) -> None:
        if premium < 0 or fee_per_contract < 0:
            raise ValueError("premium and fee cannot be negative")
        fee = abs(contracts) * fee_per_contract
        cash_change = -contracts * contract.multiplier * premium - fee
        new_contracts = self.options.get(contract.contract_id, OptionPosition(contract, 0)).contracts + contracts
        if new_contracts < 0:
            reserve = abs(new_contracts) * contract.strike * contract.multiplier
            other_reserve = self.cash_secured_put_reserve(exclude=contract.contract_id)
            if self.cash + cash_change + 1e-8 < reserve + other_reserve:
                raise ValueError("insufficient cash for cash-secured put")
        elif self.cash + cash_change < -1e-8:
            raise ValueError("insufficient cash")
        self.cash += cash_change
        if new_contracts:
            self.options[contract.contract_id] = OptionPosition(contract, new_contracts)
        else:
            self.options.pop(contract.contract_id, None)
        self.events.append(AccountEvent(event_date, "option_trade", contract.contract_id, contracts, cash_change, fee))

    def cash_secured_put_reserve(self, exclude: str | None = None) -> float:
        return sum(
            abs(position.contracts) * position.contract.strike * position.contract.multiplier
            for contract_id, position in self.options.items()
            if contract_id != exclude
            and position.contracts < 0
            and position.contract.option_type == "put"
            and position.contract.settlement == "physical"
        )

    @property
    def available_cash(self) -> float:
        return self.cash - self.cash_secured_put_reserve()

    def mark_to_market(self, equity_prices: dict[str, float], option_prices: dict[str, float]) -> float:
        equity_value = sum(position.shares * equity_prices[position.symbol] for position in self.equities.values())
        option_value = sum(
            position.contracts * position.contract.multiplier * option_prices[contract_id]
            for contract_id, position in self.options.items()
        )
        return self.cash + equity_value + option_value

    def settle_expired(self, event_date: date, spots: dict[str, float], allow_short_stock: bool = False) -> None:
        expired = [item for item in self.options.items() if item[1].contract.expiration <= event_date]
        for contract_id, position in expired:
            contract = position.contract
            intrinsic = contract.intrinsic_value(spots[contract.underlying])
            if contract.settlement == "cash":
                cash_change = position.contracts * contract.multiplier * intrinsic
                self.cash += cash_change
            elif intrinsic > 0 and contract.option_type == "put":
                shares = -position.contracts * contract.multiplier
                current = self.equities.get(contract.underlying, EquityPosition(contract.underlying, 0.0)).shares
                if current + shares < -1e-8 and not allow_short_stock:
                    raise ValueError("physical put exercise would create an unsupported short stock position")
                equity = self.equities.setdefault(contract.underlying, EquityPosition(contract.underlying, 0.0))
                equity.shares += shares
                cash_change = -shares * contract.strike
                self.cash += cash_change
            else:
                cash_change = 0.0
            self.events.append(
                AccountEvent(event_date, "option_settlement", contract_id, -position.contracts, cash_change, note="expiry")
            )
            del self.options[contract_id]

    def factor_dollars(self, equity_prices: dict[str, float]) -> dict[str, float]:
        exposures: dict[str, float] = {}
        for position in self.equities.values():
            value = position.shares * equity_prices[position.symbol]
            for factor, loading in position.factor_loadings.items():
                exposures[factor] = exposures.get(factor, 0.0) + value * loading
        for position in self.options.values():
            underlying_price = equity_prices[position.contract.underlying]
            delta_dollars = (
                position.contracts * position.contract.multiplier * position.delta * underlying_price
            )
            underlying = self.equities.get(position.contract.underlying)
            loadings = underlying.factor_loadings if underlying else {position.contract.underlying: 1.0}
            for factor, loading in loadings.items():
                exposures[factor] = exposures.get(factor, 0.0) + delta_dollars * loading
        return exposures
