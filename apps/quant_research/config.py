"""Validated, hashable configuration for deterministic research runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from .contracts import canonical_hash


@dataclass(frozen=True)
class AccountConfig:
    currency: str = "USD"
    seed_cash: float = 100_000.0
    allow_borrowing: bool = False
    settled_cash_only: bool = True
    fractional_shares: bool = False


@dataclass(frozen=True)
class ExecutionConfig:
    equity_one_way_cost_bps: float = 10.0
    missing_open: str = "reject"
    insufficient_cash: str = "reject"
    settlement_days: int = 1


@dataclass(frozen=True)
class ResearchConfig:
    profile: str = "etf_cash_demo"
    instruments: tuple[str, ...] = ("QQQ", "SPY")
    account: AccountConfig = field(default_factory=AccountConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    initial_target_weights: dict[str, float] = field(default_factory=lambda: {"QQQ": 0.45, "SPY": 0.45})
    reduced_target_weights: dict[str, float] = field(default_factory=lambda: {"QQQ": 0.30, "SPY": 0.30})
    random_seed: int = 42
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.account.currency != "USD" or not isfinite(self.account.seed_cash) or self.account.seed_cash <= 0:
            raise ValueError("only positive USD research accounts are supported")
        if not self.instruments or len(set(self.instruments)) != len(self.instruments):
            raise ValueError("instruments must be unique and non-empty")
        for name, weights in (("initial", self.initial_target_weights), ("reduced", self.reduced_target_weights)):
            if set(weights) - set(self.instruments) or any(not isfinite(value) or value < 0 for value in weights.values()):
                raise ValueError(f"{name} weights contain invalid instruments or values")
            if sum(weights.values()) > 1 + 1e-12:
                raise ValueError(f"{name} weights exceed capital")
        if not isfinite(self.execution.equity_one_way_cost_bps) or self.execution.equity_one_way_cost_bps < 0 or self.execution.settlement_days < 0:
            raise ValueError("execution costs and settlement days cannot be negative")
        if self.execution.missing_open != "reject" or self.execution.insufficient_cash != "reject":
            raise ValueError("the first implementation only supports explicit rejection")
        if self.random_seed < 0:
            raise ValueError("random_seed cannot be negative")

    @property
    def config_hash(self) -> str:
        return canonical_hash(asdict(self))

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ResearchConfig":
        account = AccountConfig(**value.get("account", {}))
        execution = ExecutionConfig(**value.get("execution", {}))
        universe = value.get("universe", {})
        comparison = value.get("comparison", {})
        return cls(
            profile=value.get("profile", "etf_cash_demo"),
            instruments=tuple(universe.get("instruments", ("QQQ", "SPY"))),
            account=account,
            execution=execution,
            initial_target_weights=dict(comparison.get("initial_target_weights", {"QQQ": 0.45, "SPY": 0.45})),
            reduced_target_weights=dict(comparison.get("reduced_target_weights", {"QQQ": 0.30, "SPY": 0.30})),
            random_seed=int(value.get("random_seed", 42)),
            schema_version=int(value.get("schema_version", 2)),
        )


def load_config(path: Path | str) -> ResearchConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("configuration must be a mapping")
    return ResearchConfig.from_mapping(payload)
