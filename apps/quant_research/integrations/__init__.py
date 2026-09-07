"""Stable integration boundaries for external research systems."""

from .tradingagents import (
    ResearchOverlayPolicy,
    ResearchSignal,
    apply_research_overlay,
    load_research_signals,
)
from .replay import SignalReplayConfig, run_signal_replay

__all__ = [
    "ResearchOverlayPolicy",
    "ResearchSignal",
    "apply_research_overlay",
    "load_research_signals",
    "SignalReplayConfig",
    "run_signal_replay",
]
