"""Fair, cross-window comparison of equal-weight, momentum, and model portfolios."""

from __future__ import annotations

from dataclasses import dataclass

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
) -> dict:
    """Run every strategy on identical signal dates, prices, costs, and windows."""
    if not isinstance(model_scores.index, pd.MultiIndex) or list(model_scores.index.names) != ["datetime", "instrument"]:
        raise ValueError("model_scores require a datetime/instrument MultiIndex")
    if not windows or top_k <= 0 or momentum_lookback <= 0:
        raise ValueError("invalid baseline comparison configuration")
    if len({window.name for window in windows}) != len(windows):
        raise ValueError("evaluation window names must be unique")
    prices = open_prices.sort_index().astype(float)
    benchmark = benchmark_open.sort_index().astype(float)
    momentum = prices.pct_change(momentum_lookback)
    outputs: dict[str, dict[str, dict]] = {}
    metric_rows: list[dict] = []
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
            "equal_weight": equal_scores,
            "momentum": momentum_scores,
            "model": _slice_scores(model_universe, eligible),
        }
        outputs[window.name] = {}
        for strategy, scores in strategies.items():
            result = run_long_only_backtest(
                scores, window_prices, window_benchmark, top_k=top_k,
                rebalance_every=rebalance_every, initial_cash=initial_cash,
                cost_bps=cost_bps, max_weight=max_weight,
            )
            outputs[window.name][strategy] = result
            metric_rows.append({
                "window": window.name, "start": start, "end": end, "strategy": strategy,
                **result["metrics"],
            })
    return {
        "schema_version": "baseline_comparison.v1",
        "status": "complete",
        "comparison_policy": {
            "signal_calendar": "intersection",
            "execution": "next_trading_day_open",
            "cost_bps": cost_bps,
            "top_k": top_k,
            "momentum_lookback": momentum_lookback,
        },
        "metrics": pd.DataFrame(metric_rows),
        "window_evidence": window_evidence,
        "runs": outputs,
    }
