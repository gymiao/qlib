"""Long-only portfolio construction for existing Qlib stock scores."""

from __future__ import annotations

import pandas as pd


def select_long_only(
    scores: pd.Series,
    top_k: int = 10,
    previous: set[str] | None = None,
    buffer_multiplier: float = 1.5,
    max_weight: float = 0.15,
    sectors: pd.Series | None = None,
    benchmark_sector_weights: dict[str, float] | None = None,
    max_sector_deviation: float | None = None,
) -> pd.Series:
    """Select equal-weight longs with a rank buffer for existing holdings."""
    if top_k <= 0 or buffer_multiplier < 1 or max_weight <= 0:
        raise ValueError("invalid portfolio construction parameters")
    if max_sector_deviation is not None and not 0 <= max_sector_deviation <= 1:
        raise ValueError("max_sector_deviation must be in [0, 1]")
    clean = scores.dropna().sort_values(ascending=False)
    if len(clean) < top_k:
        raise ValueError("not enough scored instruments")
    previous = previous or set()
    buffer_size = max(top_k, int(round(top_k * buffer_multiplier)))
    buffered = set(clean.head(buffer_size).index)
    retained = [symbol for symbol in clean.index if symbol in previous and symbol in buffered][:top_k]
    additions = [symbol for symbol in clean.index if symbol not in retained]
    weight = min(1.0 / top_k, max_weight)
    candidates = retained + [symbol for symbol in additions if symbol not in retained]
    selected: list[str] = []
    sector_weights: dict[str, float] = {}
    for symbol in candidates:
        if sectors is not None and max_sector_deviation is not None:
            if symbol not in sectors.index or pd.isna(sectors.loc[symbol]):
                continue
            sector = str(sectors.loc[symbol])
            benchmark_weight = (benchmark_sector_weights or {}).get(sector, 0.0)
            limit = benchmark_weight + max_sector_deviation
            if sector_weights.get(sector, 0.0) + weight > limit + 1e-12:
                continue
            sector_weights[sector] = sector_weights.get(sector, 0.0) + weight
        selected.append(symbol)
        if len(selected) == top_k:
            break
    if len(selected) < top_k:
        raise ValueError("sector constraints leave fewer than top_k feasible instruments")
    weights = pd.Series(weight, index=selected, name="target_weight")
    if weights.sum() > 1.0 + 1e-12:
        raise ValueError("weights exceed portfolio capital")
    return weights
