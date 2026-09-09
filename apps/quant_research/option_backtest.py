"""Historical-quote replay for one fixed protective put position."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timezone
from decimal import Decimal

import pandas as pd

from .ledger import EventLedger, LedgerEvent
from .option_quotes import available_chain, select_fixed_protective_put


def _at_end_of_day(value: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(value.date(), time(23, 59), tzinfo=timezone.utc))


def run_fixed_protective_put(
    underlying_prices: pd.Series,
    quotes: pd.DataFrame,
    underlying: str,
    protected_shares: int,
    initial_cash: float,
    premium_budget: float,
    fee_per_contract: float = 0.65,
) -> dict:
    prices = underlying_prices.sort_index().astype(float)
    if prices.empty or not prices.index.is_unique or (prices <= 0).any() or protected_shares <= 0:
        raise ValueError("invalid underlying price history or protected shares")
    decision_time = quotes.loc[quotes["underlying"] == underlying, "available_at"].min()
    if pd.isna(decision_time):
        raise ValueError("no quotes for the requested underlying")
    chain = available_chain(quotes, underlying, decision_time, max_age_minutes=24 * 60)
    selection = select_fixed_protective_put(
        chain, protected_shares, premium_budget, fee_per_contract=fee_per_contract
    )
    ledger = EventLedger(initial_cash)
    first_date = prices.index[0]
    ledger.apply(LedgerEvent("import-underlying", first_date.isoformat(), "position_import", {
        "instrument": underlying, "quantity": protected_shares,
    }))
    if selection.status != "feasible":
        return {
            "schema_version": "protective_put_report.v1",
            "status": "infeasible",
            "selection": asdict(selection),
            "equity_curve": pd.DataFrame(),
            "events": [asdict(event) for event in ledger.events],
        }
    contract_id = str(selection.contract_id)
    contract_quotes = quotes.loc[quotes["contract_id"] == contract_id].sort_values("available_at")
    metadata = contract_quotes.iloc[0]
    fee = selection.contracts * fee_per_contract
    ledger.apply(LedgerEvent("buy-put", pd.Timestamp(decision_time).isoformat(), "option_fill", {
        "contract_id": contract_id,
        "quantity": selection.contracts,
        "premium": selection.premium,
        "multiplier": int(metadata["multiplier"]),
        "fee": fee,
    }))
    expiration = pd.Timestamp(metadata["expiration"])
    strike = float(metadata["strike"])
    payable_settled = False
    expired = False
    rows = []
    last_mark = None
    last_mark_time = None
    for current_date, spot in prices.items():
        as_of = _at_end_of_day(pd.Timestamp(current_date))
        if not payable_settled and pd.Timestamp(current_date) > pd.Timestamp(decision_time).tz_convert(None).normalize():
            amount = ledger.state.trade_payable
            if amount:
                ledger.apply(LedgerEvent("settle-put-premium", as_of.isoformat(), "settle_trade_payable", {"amount": str(amount)}))
            payable_settled = True
        visible = contract_quotes.loc[
            (contract_quotes["available_at"] <= as_of)
            & (contract_quotes["quote_ts"] <= as_of)
        ]
        if not visible.empty:
            latest = visible.iloc[-1]
            last_mark = float((latest["bid"] + latest["ask"]) / 2)
            last_mark_time = pd.Timestamp(latest["quote_ts"])
        if not expired and as_of >= expiration:
            if float(spot) < strike:
                ledger.apply(LedgerEvent("exercise-put", expiration.isoformat(), "long_put_exercise", {
                    "contract_id": contract_id, "underlying": underlying,
                    "contracts": selection.contracts, "strike": strike,
                }))
            else:
                ledger.apply(LedgerEvent("expire-put", expiration.isoformat(), "option_expire_worthless", {
                    "contract_id": contract_id, "contracts": selection.contracts,
                }))
            expired = True
            last_mark = None
        marks = {}
        if underlying in ledger.state.positions:
            marks[underlying] = float(spot)
        if contract_id in ledger.state.positions and last_mark is not None:
            marks[contract_id] = last_mark
        mark_quality = "valid"
        if contract_id in ledger.state.positions and last_mark is None:
            nav = None
            mark_quality = "unavailable"
        else:
            nav = float(ledger.state.nav(marks))
            if contract_id in ledger.state.positions and as_of - last_mark_time > pd.Timedelta(days=1):
                mark_quality = "stale"
        rows.append({"date": pd.Timestamp(current_date), "nav": nav, "mark_quality": mark_quality, "settled_cash": float(ledger.state.settled_cash)})
    return {
        "schema_version": "protective_put_report.v1",
        "status": "complete",
        "quote_mode": "historical_bid_ask",
        "selection": asdict(selection),
        "equity_curve": pd.DataFrame(rows),
        "events": [asdict(event) for event in ledger.events],
    }


def run_rolling_protective_put(
    underlying_prices: pd.Series,
    quotes: pd.DataFrame,
    underlying: str,
    protected_shares: int,
    initial_cash: float,
    premium_budget_per_roll: float,
    roll_before_dte: int = 5,
    target_dte: int = 60,
    target_moneyness: float = 0.95,
    fee_per_contract: float = 0.65,
    min_quote_coverage: float = 0.8,
) -> dict:
    """Replay a protective put that closes at bid and reopens at ask near expiry."""
    prices = underlying_prices.sort_index().astype(float)
    if (
        prices.empty or not prices.index.is_unique or (prices <= 0).any()
        or protected_shares <= 0 or roll_before_dte < 0
        or not 0 <= min_quote_coverage <= 1
    ):
        raise ValueError("invalid rolling protective-put request")
    ledger = EventLedger(initial_cash)
    ledger.apply(LedgerEvent("rolling-import-underlying", prices.index[0].isoformat(), "position_import", {
        "instrument": underlying, "quantity": protected_shares,
    }))
    active: dict | None = None
    rows: list[dict] = []
    rolls: list[dict] = []
    pending_settlement = False

    for day_number, (current_date, spot) in enumerate(prices.items()):
        as_of = _at_end_of_day(pd.Timestamp(current_date))
        if pending_settlement:
            if ledger.state.trade_receivable:
                ledger.apply(LedgerEvent(f"rolling-settle-receivable-{day_number}", as_of.isoformat(), "settle_trade_receivable", {
                    "amount": str(ledger.state.trade_receivable),
                }))
            if ledger.state.trade_payable:
                ledger.apply(LedgerEvent(f"rolling-settle-payable-{day_number}", as_of.isoformat(), "settle_trade_payable", {
                    "amount": str(ledger.state.trade_payable),
                }))
            pending_settlement = False
        chain = available_chain(quotes, underlying, as_of, max_age_minutes=24 * 60)
        if active is not None:
            expiration = pd.Timestamp(active["expiration"])
            if as_of >= expiration:
                if float(spot) < float(active["strike"]):
                    ledger.apply(LedgerEvent(f"rolling-exercise-{day_number}", expiration.isoformat(), "long_put_exercise", {
                        "contract_id": active["contract_id"], "underlying": underlying,
                        "contracts": active["contracts"], "strike": active["strike"],
                    }))
                else:
                    ledger.apply(LedgerEvent(f"rolling-expire-{day_number}", expiration.isoformat(), "option_expire_worthless", {
                        "contract_id": active["contract_id"], "contracts": active["contracts"],
                    }))
                active = None
            elif (expiration - as_of).total_seconds() / 86400 <= roll_before_dte:
                old_quote = chain.loc[chain["contract_id"] == active["contract_id"]]
                replacements = chain.loc[
                    (chain["contract_id"] != active["contract_id"])
                    & (chain["expiration"] > expiration)
                ]
                replacement = select_fixed_protective_put(
                    replacements, protected_shares, premium_budget_per_roll,
                    target_dte, target_moneyness, fee_per_contract,
                )
                if not old_quote.empty and replacement.status == "feasible":
                    close_price = float(old_quote.iloc[-1]["bid"])
                    ledger.apply(LedgerEvent(f"rolling-close-{day_number}", as_of.isoformat(), "option_fill", {
                        "contract_id": active["contract_id"], "quantity": -active["contracts"],
                        "premium": close_price, "multiplier": active["multiplier"],
                        "fee": active["contracts"] * fee_per_contract,
                    }))
                    new_row = replacements.loc[replacements["contract_id"] == replacement.contract_id].iloc[-1]
                    ledger.apply(LedgerEvent(f"rolling-open-{day_number}", as_of.isoformat(), "option_fill", {
                        "contract_id": replacement.contract_id, "quantity": replacement.contracts,
                        "premium": replacement.premium, "multiplier": int(new_row["multiplier"]),
                        "fee": replacement.contracts * fee_per_contract, "allow_receivable": True,
                    }))
                    rolls.append({
                        "date": pd.Timestamp(current_date), "closed_contract": active["contract_id"],
                        "close_bid": close_price, "opened_contract": replacement.contract_id,
                        "open_ask": replacement.premium, "contracts": replacement.contracts,
                    })
                    active = {
                        "contract_id": str(replacement.contract_id), "contracts": replacement.contracts,
                        "expiration": pd.Timestamp(new_row["expiration"]), "strike": float(new_row["strike"]),
                        "multiplier": int(new_row["multiplier"]),
                    }
                    pending_settlement = True
        if active is None and underlying in ledger.state.positions:
            selection = select_fixed_protective_put(
                chain, protected_shares, premium_budget_per_roll,
                target_dte, target_moneyness, fee_per_contract,
            )
            if selection.status == "feasible":
                selected = chain.loc[chain["contract_id"] == selection.contract_id].iloc[-1]
                ledger.apply(LedgerEvent(f"rolling-open-{day_number}", as_of.isoformat(), "option_fill", {
                    "contract_id": selection.contract_id, "quantity": selection.contracts,
                    "premium": selection.premium, "multiplier": int(selected["multiplier"]),
                    "fee": selection.contracts * fee_per_contract,
                }))
                active = {
                    "contract_id": str(selection.contract_id), "contracts": selection.contracts,
                    "expiration": pd.Timestamp(selected["expiration"]), "strike": float(selected["strike"]),
                    "multiplier": int(selected["multiplier"]),
                }
                pending_settlement = True

        marks = {}
        if underlying in ledger.state.positions:
            marks[underlying] = float(spot)
        mark_quality = "valid"
        if active is not None:
            current_quote = chain.loc[chain["contract_id"] == active["contract_id"]]
            if current_quote.empty:
                mark_quality = "unavailable"
            else:
                latest = current_quote.iloc[-1]
                marks[active["contract_id"]] = float((latest["bid"] + latest["ask"]) / 2)
        nav = None if mark_quality == "unavailable" else float(ledger.state.nav(marks))
        rows.append({
            "date": pd.Timestamp(current_date), "nav": nav, "mark_quality": mark_quality,
            "active_contract": None if active is None else active["contract_id"],
        })

    curve = pd.DataFrame(rows)
    coverage = float(curve["nav"].notna().mean())
    return {
        "schema_version": "rolling_protective_put_report.v1",
        "status": "complete" if coverage >= min_quote_coverage else "quality_failed",
        "quote_mode": "historical_bid_ask",
        "quote_quality": {
            "coverage": coverage, "minimum_required": min_quote_coverage,
            "unavailable_dates": [value.isoformat() for value in curve.loc[curve["nav"].isna(), "date"]],
        },
        "equity_curve": curve,
        "rolls": pd.DataFrame(rolls),
        "events": [asdict(event) for event in ledger.events],
    }


def run_bear_put_spread(
    underlying_prices: pd.Series,
    quotes: pd.DataFrame,
    underlying: str,
    long_contract_id: str,
    short_contract_id: str,
    contracts: int,
    initial_cash: float,
    fee_per_contract: float = 0.65,
) -> dict:
    """Replay a named vertical spread and preserve its physical-delivery lifecycle."""
    prices = underlying_prices.sort_index().astype(float)
    legs = quotes.loc[quotes["contract_id"].isin([long_contract_id, short_contract_id])]
    if prices.empty or contracts <= 0 or set(legs["contract_id"]) != {long_contract_id, short_contract_id}:
        raise ValueError("invalid bear put spread request")
    metadata = legs.sort_values("available_at").groupby("contract_id").first()
    long_meta, short_meta = metadata.loc[long_contract_id], metadata.loc[short_contract_id]
    if (
        float(long_meta["strike"]) <= float(short_meta["strike"])
        or pd.Timestamp(long_meta["expiration"]) != pd.Timestamp(short_meta["expiration"])
        or int(long_meta["multiplier"]) != int(short_meta["multiplier"])
    ):
        raise ValueError("spread legs require higher long strike, common expiry, and multiplier")
    first_as_of = _at_end_of_day(pd.Timestamp(prices.index[0]))
    chain = available_chain(quotes, underlying, first_as_of, max_age_minutes=24 * 60)
    visible = chain.set_index("contract_id")
    if long_contract_id not in visible.index or short_contract_id not in visible.index:
        return {"schema_version": "bear_put_spread_report.v1", "status": "infeasible", "reason_codes": ["NO_VALID_OPENING_QUOTES"]}
    ledger = EventLedger(initial_cash)
    multiplier = int(long_meta["multiplier"])
    ledger.apply(LedgerEvent("spread-buy-long", first_as_of.isoformat(), "option_fill", {
        "contract_id": long_contract_id, "quantity": contracts,
        "premium": float(visible.loc[long_contract_id]["ask"]), "multiplier": multiplier,
        "fee": contracts * fee_per_contract,
    }))
    ledger.apply(LedgerEvent("spread-sell-short", first_as_of.isoformat(), "option_fill", {
        "contract_id": short_contract_id, "quantity": -contracts,
        "premium": float(visible.loc[short_contract_id]["bid"]), "multiplier": multiplier,
        "fee": contracts * fee_per_contract,
    }))
    collateral_id = f"spread:{short_contract_id}"
    collateral = contracts * multiplier * float(short_meta["strike"])
    ledger.apply(LedgerEvent("spread-collateral", first_as_of.isoformat(), "collateral_open", {
        "collateral_id": collateral_id, "amount": collateral,
    }))
    expiration = pd.Timestamp(long_meta["expiration"])
    settled = False
    expired = False
    lifecycle_reason = None
    rows = []
    for day_number, (current_date, spot) in enumerate(prices.items()):
        as_of = _at_end_of_day(pd.Timestamp(current_date))
        if not settled and day_number > 0:
            if ledger.state.trade_receivable:
                ledger.apply(LedgerEvent("spread-settle-credit", as_of.isoformat(), "settle_trade_receivable", {"amount": str(ledger.state.trade_receivable)}))
            if ledger.state.trade_payable:
                ledger.apply(LedgerEvent("spread-settle-debit", as_of.isoformat(), "settle_trade_payable", {"amount": str(ledger.state.trade_payable)}))
            settled = True
        if not expired and as_of >= expiration:
            long_itm = float(spot) < float(long_meta["strike"])
            short_itm = float(spot) < float(short_meta["strike"])
            if short_itm:
                ledger.apply(LedgerEvent("spread-short-assignment", expiration.isoformat(), "short_put_assignment", {
                    "contract_id": short_contract_id, "underlying": underlying, "contracts": contracts,
                    "strike": float(short_meta["strike"]), "collateral_id": collateral_id,
                }))
                ledger.apply(LedgerEvent("spread-long-exercise", expiration.isoformat(), "long_put_exercise", {
                    "contract_id": long_contract_id, "underlying": underlying, "contracts": contracts,
                    "strike": float(long_meta["strike"]),
                }))
            elif long_itm:
                lifecycle_reason = "MISSING_DELIVERABLE_UNDERLYING"
                ledger.apply(LedgerEvent("spread-short-expire", expiration.isoformat(), "option_expire_worthless", {
                    "contract_id": short_contract_id, "contracts": -contracts,
                    "collateral_id": collateral_id, "collateral_release": collateral,
                }))
            else:
                ledger.apply(LedgerEvent("spread-long-expire", expiration.isoformat(), "option_expire_worthless", {
                    "contract_id": long_contract_id, "contracts": contracts,
                }))
                ledger.apply(LedgerEvent("spread-short-expire", expiration.isoformat(), "option_expire_worthless", {
                    "contract_id": short_contract_id, "contracts": -contracts,
                    "collateral_id": collateral_id, "collateral_release": collateral,
                }))
            expired = True
        daily = available_chain(quotes, underlying, as_of, max_age_minutes=24 * 60).set_index("contract_id")
        marks = {}
        for contract_id in (long_contract_id, short_contract_id):
            if contract_id in ledger.state.positions and contract_id in daily.index:
                row = daily.loc[contract_id]
                marks[contract_id] = float((row["bid"] + row["ask"]) / 2)
        missing = set(ledger.state.positions) - set(marks)
        nav = None if missing else float(ledger.state.nav(marks))
        rows.append({"date": pd.Timestamp(current_date), "nav": nav, "mark_quality": "unavailable" if missing else "valid"})
    return {
        "schema_version": "bear_put_spread_report.v1",
        "status": "lifecycle_failed" if lifecycle_reason else "complete",
        "reason_codes": [] if lifecycle_reason is None else [lifecycle_reason],
        "quote_mode": "historical_bid_ask", "equity_curve": pd.DataFrame(rows),
        "events": [asdict(event) for event in ledger.events],
    }
