"""Fair, cross-window comparison of equal-weight, momentum, and model portfolios."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite

import pandas as pd

from .long_only_backtest import run_long_only_backtest


@dataclass(frozen=True)
class EvaluationWindow:
    name: str
    start: str
    end: str


def _slice_scores(scores: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    mask = scores.index.get_level_values("datetime").isin(dates)
    return scores.loc[mask].sort_index()


def run_baseline_comparison(
    model_scores: pd.Series,
    open_prices: pd.DataFrame,
    benchmark_open: pd.Series,
    windows: tuple[EvaluationWindow, ...],
    top_k: int = 10,
    momentum_lookback: int = 20,
    rebalance_every: int = 5,
    initial_cash: float = 100_000,
    cost_bps: float = 10,
    max_weight: float = 0.15,
    cost_scenarios_bps: tuple[float, ...] = (0, 10, 20, 30),
    minimum_evidence_windows: int = 3,
) -> dict:
    """Run every strategy on identical signal dates, prices, costs, and windows."""
    if not isinstance(model_scores.index, pd.MultiIndex) or list(model_scores.index.names) != ["datetime", "instrument"]:
        raise ValueError("model_scores require a datetime/instrument MultiIndex")
    if not windows or top_k <= 0 or momentum_lookback <= 0 or minimum_evidence_windows <= 0:
        raise ValueError("invalid baseline comparison configuration")
    if len({window.name for window in windows}) != len(windows):
        raise ValueError("evaluation window names must be unique")
    parsed_windows = [
        (window.name, pd.Timestamp(window.start), pd.Timestamp(window.end))
        for window in windows
    ]
    overlapping_pairs = [
        (left_name, right_name)
        for index, (left_name, left_start, left_end) in enumerate(parsed_windows)
        for right_name, right_start, right_end in parsed_windows[index + 1 :]
        if max(left_start, right_start) <= min(left_end, right_end)
    ]
    scenarios = tuple(sorted(set(float(value) for value in (*cost_scenarios_bps, cost_bps))))
    if not scenarios or any(not isfinite(value) or value < 0 for value in scenarios):
        raise ValueError("cost scenarios must be finite and non-negative")
    prices = open_prices.sort_index().astype(float)
    benchmark = benchmark_open.sort_index().astype(float)
    momentum = prices.pct_change(momentum_lookback)
    outputs: dict[str, dict[str, dict]] = {}
    metric_rows: list[dict] = []
    cost_rows: list[dict] = []
    window_evidence: dict[str, dict] = {}

    for window in windows:
        start, end = pd.Timestamp(window.start), pd.Timestamp(window.end)
        if start > end:
            raise ValueError(f"window {window.name!r} starts after it ends")
        window_prices = prices.loc[start:end]
        window_benchmark = benchmark.reindex(window_prices.index)
        model_dates = pd.DatetimeIndex(
            model_scores.index.get_level_values("datetime").unique()
        )
        eligible = window_prices.index.intersection(model_dates)
        eligible = eligible[momentum.reindex(eligible).notna().sum(axis=1) >= top_k]
        model_universe = model_scores.loc[
            model_scores.index.get_level_values("instrument").isin(prices.columns)
        ]
        model_counts = model_universe.groupby(level="datetime").count().reindex(eligible).fillna(0)
        eligible = eligible[model_counts >= top_k]
        if len(eligible) < 2:
            raise ValueError(f"window {window.name!r} has too few common signal dates")
        window_evidence[window.name] = {
            "common_signal_dates": len(eligible),
            "first_signal_date": eligible[0].isoformat(),
            "last_signal_date": eligible[-1].isoformat(),
        }

        symbols = list(prices.columns)
        equal_frame = pd.DataFrame(
            [list(range(len(symbols), 0, -1))] * len(eligible), index=eligible, columns=symbols
        )
        equal_scores = equal_frame.stack().rename_axis(["datetime", "instrument"])
        momentum_scores = momentum.loc[eligible].stack().rename_axis(["datetime", "instrument"])
        strategies = {
            "equal_weight": (equal_scores, len(symbols), min(max_weight, 0.99 / len(symbols))),
            "momentum": (momentum_scores, top_k, max_weight),
            "model": (_slice_scores(model_universe, eligible), top_k, max_weight),
        }
        outputs[window.name] = {}
        for strategy, (strategy_scores, strategy_top_k, strategy_max_weight) in strategies.items():
            primary = None
            for scenario_cost in scenarios:
                result = run_long_only_backtest(
                    strategy_scores, window_prices, window_benchmark, top_k=strategy_top_k,
                    rebalance_every=rebalance_every, initial_cash=initial_cash,
                    cost_bps=scenario_cost, max_weight=strategy_max_weight,
                )
                metrics = result["metrics"]
                cost_rows.append({
                    "window": window.name,
                    "strategy": strategy,
                    "cost_bps": scenario_cost,
                    **metrics,
                    "excess_return": metrics["total_return"] - metrics["benchmark_return"],
                })
                if scenario_cost == float(cost_bps):
                    primary = result
            if primary is None:
                raise RuntimeError("primary cost scenario was not evaluated")
            outputs[window.name][strategy] = primary
            primary_metrics = primary["metrics"]
            benchmark_return = primary_metrics["benchmark_return"]
            metric_rows.append({
                "window": window.name, "start": start, "end": end, "strategy": strategy,
                "market_regime": "up" if benchmark_return > 0.05 else "down" if benchmark_return < -0.05 else "flat",
                **primary_metrics,
                "excess_return": primary_metrics["total_return"] - benchmark_return,
            })
    metrics = pd.DataFrame(metric_rows)
    sensitivity = pd.DataFrame(cost_rows)
    primary = metrics.pivot(index="window", columns="strategy", values="total_return")
    model_rows = metrics.loc[metrics["strategy"] == "model"].set_index("window")
    max_cost = max(scenarios)
    stressed_model = sensitivity.loc[
        (sensitivity["strategy"] == "model") & (sensitivity["cost_bps"] == max_cost)
    ].set_index("window")
    positive_excess = int((model_rows["excess_return"] > 0).sum())
    beats_equal = int((primary["model"] > primary["equal_weight"]).sum())
    beats_momentum = int((primary["model"] > primary["momentum"]).sum())
    stress_positive = int((stressed_model["excess_return"] > 0).sum())
    required_majority = ceil(len(windows) * 0.6)
    reason_codes = []
    if len(windows) < minimum_evidence_windows:
        reason_codes.append("INSUFFICIENT_INDEPENDENT_WINDOWS")
    if overlapping_pairs:
        reason_codes.append("OVERLAPPING_EVIDENCE_WINDOWS")
    if positive_excess < required_majority:
        reason_codes.append("EXCESS_RETURN_NOT_STABLE")
    if beats_equal < required_majority or beats_momentum < required_majority:
        reason_codes.append("MODEL_DOES_NOT_STABLY_BEAT_BASELINES")
    if stress_positive < required_majority:
        reason_codes.append("COST_STRESS_NOT_ROBUST")
    evidence_status = (
        "insufficient_evidence"
        if "INSUFFICIENT_INDEPENDENT_WINDOWS" in reason_codes
        else "candidate"
        if not reason_codes
        else "not_robust"
    )
    return {
        "schema_version": "baseline_comparison.v2",
        "status": "complete",
        "comparison_policy": {
            "signal_calendar": "intersection",
            "execution": "next_trading_day_open",
            "cost_bps": cost_bps,
            "top_k": top_k,
            "momentum_lookback": momentum_lookback,
            "equal_weight_universe_size": len(prices.columns),
            "equal_weight_target_gross": len(prices.columns) * min(max_weight, 0.99 / len(prices.columns)),
            "cost_scenarios_bps": scenarios,
            "minimum_evidence_windows": minimum_evidence_windows,
        },
        "metrics": metrics,
        "cost_sensitivity": sensitivity,
        "model_evidence": {
            "status": evidence_status,
            "reason_codes": reason_codes,
            "windows": len(windows),
            "required_majority": required_majority,
            "positive_excess_windows": positive_excess,
            "beats_equal_weight_windows": beats_equal,
            "beats_momentum_windows": beats_momentum,
            "positive_excess_at_max_cost_windows": stress_positive,
            "max_cost_bps": max_cost,
            "market_regime_counts": model_rows["market_regime"].value_counts().sort_index().to_dict(),
            "overlapping_window_pairs": overlapping_pairs,
            "claim_boundary": "candidate_is_not_proof_of_alpha_or_live_trading_readiness",
        },
        "window_evidence": window_evidence,
        "runs": outputs,
    }
