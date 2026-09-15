"""Historical-quote replay for one fixed protective put position."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timezone
from decimal import Decimal

import pandas as pd

from .ledger import EventLedger, LedgerEvent
from .option_lifecycle import validate_option_lifecycle_events
from .option_quotes import (
    available_chain,
    select_fixed_covered_call,
    select_fixed_protective_put,
)


_NON_HISTORICAL_SOURCE_MARKERS = ("synthetic", "demo", "fixture", "scenario")
_LIFECYCLE_EVIDENCE_COLUMNS = {
    "source", "source_kind", "option_type", "listed_at", "last_trade_at",
    "exercise_style", "settlement_type", "deliverable_instrument_id",
}


def _quote_evidence(quotes: pd.DataFrame) -> dict[str, str]:
    complete = _LIFECYCLE_EVIDENCE_COLUMNS.issubset(quotes.columns) and not quotes.empty
    if complete:
        complete = (
            not quotes[list(_LIFECYCLE_EVIDENCE_COLUMNS)].isna().any().any()
            and quotes["source"].astype(str).str.strip().ne("").all()
            and quotes["option_type"].isin({"put", "call"}).all()
            and quotes["exercise_style"].isin({"american", "european"}).all()
            and quotes["settlement_type"].eq("physical").all()
            and quotes["deliverable_instrument_id"].eq(quotes["underlying"]).all()
        )
        times = {
            column: pd.to_datetime(quotes[column], utc=True, errors="coerce")
            for column in ("listed_at", "quote_ts", "available_at", "last_trade_at", "expiration")
        }
        complete = complete and all(values.notna().all() for values in times.values())
        complete = complete and (
            (times["listed_at"] <= times["quote_ts"]).all()
            and (times["quote_ts"] <= times["available_at"]).all()
            and (times["quote_ts"] <= times["last_trade_at"]).all()
            and (times["last_trade_at"] <= times["expiration"]).all()
        )
    if complete:
        kinds = quotes["source_kind"].astype(str).str.strip().str.lower()
        names = quotes["source"].astype(str).str.strip().str.lower()
        historical = kinds.eq("historical_observed").all() and not names.apply(
            lambda value: any(marker in value for marker in _NON_HISTORICAL_SOURCE_MARKERS)
        ).any()
        if historical:
            return {
                "quote_mode": "historical_bid_ask",
                "evidence_capability": "historical_option_quote_replay",
            }
        if kinds.isin({"synthetic", "scenario_only"}).all():
            return {
                "quote_mode": "synthetic_bid_ask_fixture",
                "evidence_capability": "scenario_only",
            }
    return {
        "quote_mode": "unverified_bid_ask",
        "evidence_capability": "scenario_only",
    }


def _validate_physical_contract(
    metadata: pd.Series,
    *,
    underlying: str,
    option_type: str,
) -> None:
    if "option_type" in metadata and str(metadata["option_type"]).lower() != option_type:
        raise ValueError(f"strategy requires an explicit {option_type} contract")
    if "settlement_type" in metadata and str(metadata["settlement_type"]).lower() != "physical":
        raise ValueError("this option replay requires physical settlement")
    if "deliverable_instrument_id" in metadata:
        deliverable = str(metadata["deliverable_instrument_id"]).strip()
        if pd.isna(metadata["deliverable_instrument_id"]) or deliverable != underlying:
            raise ValueError("adjusted or mismatched option deliverable is unsupported")


def _lifecycle_evidence(events: pd.DataFrame | None) -> dict[str, object]:
    if events is None:
        return {"mode": "not_provided", "observed_event_count": 0, "capability": "scenario_only"}
    kinds = events["source_kind"].astype(str).str.strip().str.lower()
    names = events["source"].astype(str).str.strip().str.lower()
    historical = not events.empty and kinds.eq("historical_observed").all() and not names.apply(
        lambda value: any(marker in value for marker in _NON_HISTORICAL_SOURCE_MARKERS)
    ).any()
    return {
        "mode": "observed_events" if historical else "non_historical_events",
        "observed_event_count": len(events),
        "capability": "observed_historical_events" if historical else "scenario_only",
    }


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
            **_quote_evidence(quotes),
            "selection": asdict(selection),
            "equity_curve": pd.DataFrame(),
            "events": [asdict(event) for event in ledger.events],
        }
    contract_id = str(selection.contract_id)
    contract_quotes = quotes.loc[quotes["contract_id"] == contract_id].sort_values("available_at")
    metadata = contract_quotes.iloc[0]
    _validate_physical_contract(metadata, underlying=underlying, option_type="put")
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
        **_quote_evidence(quotes),
        "selection": asdict(selection),
        "equity_curve": pd.DataFrame(rows),
        "events": [asdict(event) for event in ledger.events],
    }


def run_fixed_covered_call(
    underlying_prices: pd.Series,
    quotes: pd.DataFrame,
    underlying: str,
    owned_shares: int,
    initial_cash: float,
    target_dte: int = 45,
    target_moneyness: float = 1.05,
    fee_per_contract: float = 0.65,
    min_quote_coverage: float = 0.8,
    assignment_events: pd.DataFrame | None = None,
) -> dict:
    """Replay a covered call through observed early assignment or expiry."""
    prices = underlying_prices.sort_index().astype(float)
    if (
        prices.empty
        or not prices.index.is_unique
        or (prices <= 0).any()
        or isinstance(owned_shares, bool)
        or not isinstance(owned_shares, int)
        or owned_shares <= 0
        or initial_cash < 0
        or fee_per_contract < 0
        or not 0 <= min_quote_coverage <= 1
    ):
        raise ValueError("invalid covered-call price, position, cash, or quality input")
    typed = quotes.loc[
        (quotes["underlying"] == underlying)
        & (quotes["option_type"].astype(str).str.lower() == "call")
    ] if "option_type" in quotes.columns else pd.DataFrame()
    if typed.empty:
        return {
            "schema_version": "covered_call_report.v1",
            "status": "infeasible",
            "reason_codes": ["NO_EXPLICIT_CALL_QUOTES"],
            **_quote_evidence(quotes),
            "equity_curve": pd.DataFrame(),
            "events": [],
        }
    decision_time = typed["available_at"].min()
    chain = available_chain(quotes, underlying, decision_time, max_age_minutes=24 * 60)
    selection = select_fixed_covered_call(
        chain, owned_shares, target_dte, target_moneyness, fee_per_contract,
    )
    if selection.status != "feasible":
        return {
            "schema_version": "covered_call_report.v1",
            "status": "infeasible",
            "reason_codes": [selection.reason_code],
            **_quote_evidence(quotes),
            "selection": asdict(selection),
            "equity_curve": pd.DataFrame(),
            "events": [],
        }
    contract_id = str(selection.contract_id)
    contract_quotes = typed.loc[typed["contract_id"] == contract_id].sort_values("available_at")
    metadata = contract_quotes.iloc[0]
    _validate_physical_contract(metadata, underlying=underlying, option_type="call")
    multiplier = int(metadata["multiplier"])
    covered_shares = selection.contracts * multiplier
    if covered_shares > owned_shares:
        raise ValueError("covered-call selection exceeds owned shares")
    expiration = pd.Timestamp(metadata["expiration"])
    if expiration.tzinfo is None:
        raise ValueError("covered-call expiration must include timezone")
    expiration = expiration.tz_convert("UTC")
    decision_date = pd.Timestamp(decision_time).tz_convert("UTC").tz_localize(None).normalize()
    observed_assignments = None
    if assignment_events is not None:
        observed_assignments = validate_option_lifecycle_events(assignment_events)
        observed_assignments = observed_assignments.loc[
            observed_assignments["contract_id"].astype(str) == contract_id
        ].copy()
        invalid_time = (
            (observed_assignments["effective_at"] < pd.Timestamp(decision_time))
            | (observed_assignments["effective_at"] >= expiration)
            | (observed_assignments["available_at"] > expiration)
        )
        if invalid_time.any():
            raise ValueError("covered-call assignment is outside the open contract lifetime")
        if observed_assignments["contracts"].sum() > selection.contracts:
            raise ValueError("covered-call assignment exceeds open contracts")
    local_dates = pd.DatetimeIndex(prices.index).tz_localize(None).normalize()
    prices = prices.loc[local_dates >= decision_date]
    if prices.empty:
        raise ValueError("covered-call price history does not reach the decision date")

    cash = float(initial_cash) + selection.total_credit
    shares = float(owned_shares)
    active = True
    remaining_contracts = selection.contracts
    applied_assignment_ids: set[str] = set()
    events = [{
        "event_id": "covered-call-open",
        "effective_at": pd.Timestamp(decision_time).isoformat(),
        "kind": "covered_call_sell_to_open",
        "contract_id": contract_id,
        "contracts": -selection.contracts,
        "premium": selection.premium,
        "fee": selection.contracts * fee_per_contract,
        "covered_shares": covered_shares,
    }]
    rows: list[dict] = []
    required_marks = 0
    valid_marks = 0
    for current_date, spot_value in prices.items():
        spot = float(spot_value)
        as_of = _at_end_of_day(pd.Timestamp(current_date))
        if active and observed_assignments is not None:
            visible_assignments = observed_assignments.loc[
                observed_assignments["available_at"] <= as_of
            ]
            for assignment in visible_assignments.itertuples(index=False):
                event_id = str(assignment.event_id)
                if event_id in applied_assignment_ids:
                    continue
                assigned_contracts = int(assignment.contracts)
                if assigned_contracts > remaining_contracts:
                    raise ValueError("covered-call assignment exceeds open contracts")
                assigned_shares = assigned_contracts * multiplier
                shares -= assigned_shares
                cash += assigned_shares * float(metadata["strike"])
                remaining_contracts -= assigned_contracts
                applied_assignment_ids.add(event_id)
                events.append({
                    "event_id": event_id,
                    "effective_at": pd.Timestamp(assignment.effective_at).isoformat(),
                    "available_at": pd.Timestamp(assignment.available_at).isoformat(),
                    "kind": "short_call_early_assignment",
                    "contract_id": contract_id,
                    "contracts": assigned_contracts,
                    "shares_delivered": assigned_shares,
                    "strike": float(metadata["strike"]),
                    "source": str(assignment.source),
                    "source_kind": str(assignment.source_kind),
                })
                if remaining_contracts == 0:
                    active = False
                    break
        if active and as_of >= expiration:
            if spot > float(metadata["strike"]):
                expiring_shares = remaining_contracts * multiplier
                shares -= expiring_shares
                cash += expiring_shares * float(metadata["strike"])
                events.append({
                    "event_id": "covered-call-assignment",
                    "effective_at": expiration.isoformat(),
                    "kind": "short_call_assignment",
                    "contract_id": contract_id,
                    "contracts": remaining_contracts,
                    "shares_delivered": expiring_shares,
                    "strike": float(metadata["strike"]),
                })
            else:
                events.append({
                    "event_id": "covered-call-expiry",
                    "effective_at": expiration.isoformat(),
                    "kind": "option_expire_worthless",
                    "contract_id": contract_id,
                    "contracts": -remaining_contracts,
                })
            active = False
            remaining_contracts = 0
        mark = None
        if active:
            required_marks += 1
            visible = available_chain(quotes, underlying, as_of, max_age_minutes=24 * 60)
            visible = visible.loc[visible["contract_id"] == contract_id]
            if not visible.empty:
                latest = visible.iloc[-1]
                mark = float((latest["bid"] + latest["ask"]) / 2)
                valid_marks += 1
        nav = cash + shares * spot if not active else (
            None if mark is None else cash + shares * spot - remaining_contracts * multiplier * mark
        )
        rows.append({
            "date": pd.Timestamp(current_date), "nav": nav,
            "mark_quality": "not_required" if not active else "valid" if mark is not None else "unavailable",
            "settled_cash": cash, "shares": shares,
            "active_contract": contract_id if active else None,
        })
    coverage = valid_marks / required_marks if required_marks else 1.0
    if active:
        status = "open_position"
        reasons = ["PRICE_HISTORY_ENDS_BEFORE_EXPIRY"]
    elif coverage < min_quote_coverage:
        status = "quality_failed"
        reasons = ["OPTION_QUOTE_COVERAGE_BELOW_MINIMUM"]
    else:
        status = "complete"
        reasons = []
    return {
        "schema_version": "covered_call_report.v1",
        "status": status,
        "reason_codes": reasons,
        **_quote_evidence(quotes),
        "claim_boundary": "historical_quote_replay_not_live_execution_or_strategy_recommendation",
        "assignment_evidence": _lifecycle_evidence(observed_assignments),
        "lifecycle_assumptions": [
            "OBSERVED_EARLY_ASSIGNMENT_EVENTS_ONLY" if assignment_events is not None
            else "NO_EARLY_ASSIGNMENT_DATA",
            "NO_HEURISTIC_EARLY_ASSIGNMENT_INFERENCE",
            "PHYSICAL_DELIVERY_AT_EXPIRY",
        ],
        "selection": asdict(selection),
        "quote_quality": {"coverage": coverage, "minimum_required": min_quote_coverage},
        "equity_curve": pd.DataFrame(rows),
        "events": events,
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
                    _validate_physical_contract(new_row, underlying=underlying, option_type="put")
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
                _validate_physical_contract(selected, underlying=underlying, option_type="put")
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
        **_quote_evidence(quotes),
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
    _validate_physical_contract(long_meta, underlying=underlying, option_type="put")
    _validate_physical_contract(short_meta, underlying=underlying, option_type="put")
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
        return {
            "schema_version": "bear_put_spread_report.v1",
            "status": "infeasible",
            "reason_codes": ["NO_VALID_OPENING_QUOTES"],
            **_quote_evidence(quotes),
        }
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
        **_quote_evidence(quotes), "equity_curve": pd.DataFrame(rows),
        "events": [asdict(event) for event in ledger.events],
    }
