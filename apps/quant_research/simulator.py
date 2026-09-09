"""Idempotent paper execution of versioned order plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from dataclasses import asdict
import json
from math import isfinite
import os
from pathlib import Path
import tempfile

from .contracts import OrderPlan, as_payload, canonical_hash
from .ledger import EventLedger, LedgerEvent


@dataclass(frozen=True)
class PlanResult:
    plan_id: str
    status: str
    reason_codes: tuple[str, ...]
    fill_event_ids: tuple[str, ...]


@dataclass
class PlanProgress:
    plan_hash: str
    remaining: dict[int, Decimal]
    expected_basis_hash: str
    attempt_count: int = 0
    fill_event_ids: tuple[str, ...] = ()


class PaperSimulator:
    def __init__(self, ledger: EventLedger, fee_bps: float = 10.0):
        if not isfinite(fee_bps) or fee_bps < 0:
            raise ValueError("fee_bps cannot be negative")
        self.ledger = ledger
        self.fee_rate = Decimal(str(fee_bps)) / Decimal("10000")
        self.results: dict[str, PlanResult] = {}
        self.progress: dict[str, PlanProgress] = {}

    def execute(
        self,
        plan: OrderPlan,
        execution_time: datetime,
        prices: dict[str, float],
        fill_fraction: float = 1.0,
    ) -> PlanResult:
        fraction = Decimal(str(fill_fraction))
        if not fraction.is_finite() or fraction <= 0 or fraction > 1:
            raise ValueError("fill_fraction must be in (0, 1]")
        if execution_time.tzinfo is None:
            raise ValueError("execution_time must include a timezone")
        if not plan.orders:
            raise ValueError("order plan cannot be empty")
        for order in plan.orders:
            if not str(order.get("instrument", "")).strip() or "quantity" not in order:
                raise ValueError("orders require an instrument and quantity")
            quantity = Decimal(str(order["quantity"]))
            if not quantity.is_finite() or quantity == 0:
                raise ValueError("order quantity must be finite and nonzero")
            if order.get("max_price") is not None:
                limit = Decimal(str(order["max_price"]))
                if not limit.is_finite() or limit <= 0:
                    raise ValueError("max_price must be finite and positive")
        plan_hash = canonical_hash(as_payload(plan))
        progress = self.progress.get(plan.plan_id)
        if progress is not None and progress.plan_hash != plan_hash:
            raise ValueError(f"plan_id {plan.plan_id!r} was reused with different content")
        previous = self.results.get(plan.plan_id)
        if previous is not None and previous.status in {"filled", "expired"}:
            return previous
        timestamp = execution_time.astimezone(timezone.utc)
        parsed_expiry = datetime.fromisoformat(plan.expires_at.replace("Z", "+00:00"))
        if parsed_expiry.tzinfo is None:
            raise ValueError("plan expiry must include a timezone")
        expires = parsed_expiry.astimezone(timezone.utc)
        if timestamp > expires:
            return self._save(PlanResult(plan.plan_id, "expired", ("EXECUTION_WINDOW_EXPIRED",), ()))
        if progress is None:
            progress = PlanProgress(
                plan_hash=plan_hash,
                remaining={index: Decimal(str(order["quantity"])) for index, order in enumerate(plan.orders)},
                expected_basis_hash=plan.plan_basis_hash,
            )
            if any(not quantity.is_finite() or quantity == 0 for quantity in progress.remaining.values()):
                raise ValueError("order quantity must be finite and nonzero")
            self.progress[plan.plan_id] = progress
        elif progress.plan_hash != plan_hash:
            raise ValueError(f"plan_id {plan.plan_id!r} was reused with different content")
        if progress.expected_basis_hash != self.ledger.basis_hash():
            return self._save(PlanResult(plan.plan_id, "needs_revalidation", ("ACCOUNT_CHANGED",), ()))
        normalized = []
        for index, order in enumerate(plan.orders):
            remaining = progress.remaining[index]
            if not remaining:
                continue
            instrument = str(order["instrument"])
            price_value = prices.get(instrument)
            price = None if price_value is None else Decimal(str(price_value))
            if price is None or not price.is_finite() or price <= 0:
                return self._save(PlanResult(plan.plan_id, "infeasible", ("MISSING_EXECUTION_PRICE",), progress.fill_event_ids))
            maximum = order.get("max_price")
            if maximum is not None:
                limit = Decimal(str(maximum))
                if not limit.is_finite() or limit <= 0:
                    raise ValueError("max_price must be finite and positive")
                if remaining > 0 and price > limit:
                    return self._save(PlanResult(plan.plan_id, "infeasible", ("PRICE_LIMIT_EXCEEDED",), progress.fill_event_ids))
            quantity = remaining if fraction == 1 else remaining * fraction
            normalized.append((index, instrument, quantity, price))
        # A dry-run ledger proves that all fills are feasible before mutating the account.
        trial = EventLedger.replay(self.ledger.initial_cash, self.ledger.events)
        fill_ids = []
        attempt = progress.attempt_count + 1
        try:
            for index, instrument, quantity, price in sorted(normalized, key=lambda row: row[2] > 0):
                fee = abs(quantity) * price * self.fee_rate
                reservation_id = f"{plan.plan_id}:{attempt}:{index}"
                if quantity > 0:
                    required = quantity * price + fee
                    trial.apply(LedgerEvent(f"{plan.plan_id}:reserve:{attempt}:{index}", timestamp.isoformat(), "reserve", {"reservation_id": reservation_id, "amount": str(required)}))
                event_id = f"{plan.plan_id}:fill:{attempt}:{index}"
                payload = {"instrument": instrument, "quantity": str(quantity), "price": str(price), "fee": str(fee)}
                if quantity > 0:
                    payload["reservation_id"] = reservation_id
                trial.apply(LedgerEvent(event_id, timestamp.isoformat(), "equity_fill", payload))
                fill_ids.append(event_id)
        except ValueError:
            return self._save(PlanResult(
                plan.plan_id, "infeasible", ("ACCOUNT_CONSTRAINT_VIOLATION",), progress.fill_event_ids
            ))
        for event in trial.events[len(self.ledger.events):]:
            self.ledger.apply(event)
        progress.attempt_count = attempt
        for index, _, quantity, _ in normalized:
            progress.remaining[index] -= quantity
        progress.fill_event_ids += tuple(fill_ids)
        progress.expected_basis_hash = self.ledger.basis_hash()
        status = "filled" if all(quantity == 0 for quantity in progress.remaining.values()) else "partially_filled"
        return self._save(PlanResult(plan.plan_id, status, (), progress.fill_event_ids))

    def monitor(self) -> dict[str, object]:
        """Return a persistence-friendly operational view of all submitted plans."""
        counts: dict[str, int] = {}
        for result in self.results.values():
            counts[result.status] = counts.get(result.status, 0) + 1
        return {
            "schema_version": "paper_monitor.v1",
            "counts": counts,
            "pending_plan_ids": sorted(
                plan_id
                for plan_id, result in self.results.items()
                if result.status not in {"filled", "expired"}
            ),
            "event_count": len(self.ledger.events),
            "account_basis_hash": self.ledger.basis_hash(),
        }

    def _save(self, result: PlanResult) -> PlanResult:
        self.results[result.plan_id] = result
        return result

    def save(self, path: Path | str) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "paper_simulator.v2",
            "initial_cash": str(self.ledger.initial_cash),
            "fee_rate": str(self.fee_rate),
            "events": [asdict(event) for event in self.ledger.events],
            "results": {plan_id: asdict(result) for plan_id, result in sorted(self.results.items())},
            "progress": {
                plan_id: {
                    "plan_hash": item.plan_hash,
                    "remaining": {str(index): str(quantity) for index, quantity in sorted(item.remaining.items())},
                    "expected_basis_hash": item.expected_basis_hash,
                    "attempt_count": item.attempt_count,
                    "fill_event_ids": item.fill_event_ids,
                }
                for plan_id, item in sorted(self.progress.items())
            },
        }
        handle, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
        os.close(handle)
        temporary_path = Path(temporary)
        try:
            temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary_path, destination)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path | str) -> "PaperSimulator":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") not in {"paper_simulator.v1", "paper_simulator.v2"}:
            raise ValueError("unsupported simulator state version")
        events = [LedgerEvent(**event) for event in payload["events"]]
        ledger = EventLedger.replay(payload["initial_cash"], events)
        simulator = cls(ledger, fee_bps=float(Decimal(payload["fee_rate"]) * Decimal("10000")))
        simulator.results = {
            plan_id: PlanResult(
                result["plan_id"], result["status"], tuple(result["reason_codes"]), tuple(result["fill_event_ids"])
            )
            for plan_id, result in payload["results"].items()
        }
        simulator.progress = {
            plan_id: PlanProgress(
                item["plan_hash"],
                {int(index): Decimal(quantity) for index, quantity in item["remaining"].items()},
                item["expected_basis_hash"],
                int(item["attempt_count"]),
                tuple(item["fill_event_ids"]),
            )
            for plan_id, item in payload.get("progress", {}).items()
        }
        return simulator
