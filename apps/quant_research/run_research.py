"""CLI for reproducible QQQ/SPY cash, hedge, and option-overlay scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

from .backtest import HedgeBacktestConfig, run_hedge_comparison


def _load_prices(path: Path | None, start: str, end: str | None) -> pd.DataFrame:
    if path:
        frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    else:
        downloaded = yf.download(["QQQ", "SPY"], start=start, end=end, auto_adjust=False, progress=False)
        if downloaded.empty:
            raise RuntimeError("QQQ/SPY download returned no data")
        close = downloaded["Close"]
        frame = close if isinstance(close, pd.DataFrame) else close.to_frame()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame[["QQQ", "SPY"]].astype(float)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices-csv", type=Path)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path(".artifacts/hedge_research"))
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--reduced-exposure", type=float, default=0.70)
    parser.add_argument("--target-volatility", type=float, default=0.12)
    parser.add_argument("--put-coverage", type=float, default=1.0)
    parser.add_argument("--put-moneyness", type=float, default=0.95)
    parser.add_argument("--put-spread-width", type=float, default=0.10)
    parser.add_argument("--call-coverage", type=float, default=1.0)
    parser.add_argument("--call-moneyness", type=float, default=1.05)
    parser.add_argument("--option-dte", type=int, default=63)
    parser.add_argument("--option-roll-dte", type=int, default=21)
    parser.add_argument("--put-volatility-skew", type=float, default=1.50)
    parser.add_argument("--call-volatility-skew", type=float, default=0.50)
    parser.add_argument("--dynamic-put-volatility-trigger", type=float, default=0.20)
    parser.add_argument("--dynamic-trend-window", type=int, default=50)
    parser.add_argument("--dynamic-risk-off-exposure", type=float, default=0.35)
    parser.add_argument("--dynamic-rebalance-threshold", type=float, default=0.05)
    args = parser.parse_args(argv)

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prices = _load_prices(args.prices_csv, args.start, args.end)
    config = HedgeBacktestConfig(
        initial_cash=args.initial_cash,
        reduced_exposure=args.reduced_exposure,
        target_volatility=args.target_volatility,
        put_coverage=args.put_coverage,
        put_moneyness=args.put_moneyness,
        put_spread_width=args.put_spread_width,
        call_coverage=args.call_coverage,
        call_moneyness=args.call_moneyness,
        put_dte=args.option_dte,
        put_roll_dte=args.option_roll_dte,
        put_volatility_skew=args.put_volatility_skew,
        call_volatility_skew=args.call_volatility_skew,
        dynamic_put_volatility_trigger=args.dynamic_put_volatility_trigger,
        dynamic_trend_window=args.dynamic_trend_window,
        dynamic_risk_off_exposure=args.dynamic_risk_off_exposure,
        dynamic_rebalance_threshold=args.dynamic_rebalance_threshold,
    )
    report = run_hedge_comparison(prices, config)
    equity = report.pop("equity_curves")
    trades = report.pop("option_trades")
    equity.to_csv(output / "equity_curves.csv", index=False)
    trades.to_csv(output / "option_trades.csv", index=False)
    prices.rename_axis("date").to_csv(output / "prices.csv")
    (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
