"""Long-only portfolio construction for existing Qlib stock scores."""

from __future__ import annotations

import pandas as pd


def select_long_only(
    scores: pd.Series,
    top_k: int = 10,
    previous: set[str] | None = None,
    buffer_multiplier: float = 1.5,
    max_weight: float = 0.15,
) -> pd.Series:
    """Select equal-weight longs with a rank buffer for existing holdings."""
    if top_k <= 0 or buffer_multiplier < 1 or max_weight <= 0:
        raise ValueError("invalid portfolio construction parameters")
    clean = scores.dropna().sort_values(ascending=False)
    if len(clean) < top_k:
        raise ValueError("not enough scored instruments")
    previous = previous or set()
    buffer_size = max(top_k, int(round(top_k * buffer_multiplier)))
    buffered = set(clean.head(buffer_size).index)
    retained = [symbol for symbol in clean.index if symbol in previous and symbol in buffered][:top_k]
    additions = [symbol for symbol in clean.index if symbol not in retained]
    selected = (retained + additions)[:top_k]
    weight = min(1.0 / top_k, max_weight)
    weights = pd.Series(weight, index=selected, name="target_weight")
    if weights.sum() > 1.0 + 1e-12:
        raise ValueError("weights exceed portfolio capital")
    return weights
