"""Export Qlib research artifacts into the dashboard's stable JSON contract."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def capm_statistics(
    strategy_returns: list[float],
    benchmark_returns: list[float],
    period_risk_free_rates: list[float],
    periods_per_year: float,
) -> dict[str, float]:
    strategy_excess = [
        value - risk_free for value, risk_free in zip(strategy_returns, period_risk_free_rates)
    ]
    market_excess = [
        value - risk_free for value, risk_free in zip(benchmark_returns, period_risk_free_rates)
    ]
    market_mean = sum(market_excess) / len(market_excess)
    strategy_mean = sum(strategy_excess) / len(strategy_excess)
    market_variance = sum((value - market_mean) ** 2 for value in market_excess)
    covariance = sum(
        (market - market_mean) * (strategy - strategy_mean)
        for market, strategy in zip(market_excess, strategy_excess)
    )
    beta = covariance / market_variance if market_variance else 0.0
    period_alpha = strategy_mean - beta * market_mean
    residuals = [
        strategy - (period_alpha + beta * market)
        for strategy, market in zip(strategy_excess, market_excess)
    ]
    total_variance = sum((value - strategy_mean) ** 2 for value in strategy_excess)
    residual_variance = sum(value**2 for value in residuals)
    r_squared = 1 - residual_variance / total_variance if total_variance else 0.0
    return {
        "alpha_annualized": (1 + period_alpha) ** periods_per_year - 1,
        "beta": beta,
        "r_squared": r_squared,
    }


def historical_risk_free_rates(path: Path, dates: list[str]) -> list[float]:
    rows = read_csv(path)
    observations = sorted(
        (row["date"], float(row["annual_rate"]))
        for row in rows
        if row.get("date") and row.get("annual_rate")
    )
    if not observations:
        raise ValueError(f"No usable risk-free observations in {path}")
    observation_dates = [row[0] for row in observations]
    rates = []
    for date in dates:
        index = bisect.bisect_right(observation_dates, date) - 1
        if index < 0:
            raise ValueError(f"No risk-free observation on or before {date}")
        rates.append(observations[index][1])
    return rates


def compound(returns: list[float]) -> float:
    value = 1.0
    for period_return in returns:
        value *= 1 + period_return
    return value - 1


def annual_return_rows(
    periods: list[dict[str, str]],
    period_risk_free_rates: list[float],
    periods_per_year: float,
) -> list[dict]:
    rows = []
    def valuation_date(period: dict[str, str]) -> str:
        return period.get("exit_date") or period["signal_date"]

    years = sorted({valuation_date(period)[:4] for period in periods})
    for year in years:
        indices = [
            index for index, period in enumerate(periods)
            if valuation_date(period).startswith(year)
        ]
        strategy_return = compound([float(periods[index]["net_return"]) for index in indices])
        benchmark_return = compound(
            [float(periods[index]["benchmark_return"]) for index in indices]
        )
        risk_free_return = compound([period_risk_free_rates[index] for index in indices])
        rows.append(
            {
                "year": int(year),
                "periods": len(indices),
                "complete_year": len(indices) >= periods_per_year * 0.9,
                "start_date": valuation_date(periods[indices[0]]),
                "end_date": valuation_date(periods[indices[-1]]),
                "strategy_return": strategy_return,
                "benchmark_return": benchmark_return,
                "risk_free_return": risk_free_return,
                "relative_to_benchmark": strategy_return - benchmark_return,
            }
        )
    return rows


def export(
    artifacts: Path,
    output: Path,
    annual_risk_free_rate: float = 0.04,
    risk_free_csv: Path | None = None,
) -> dict:
    with (artifacts / "result.json").open(encoding="utf-8") as file:
        result = json.load(file)
    periods = read_csv(artifacts / "backtest.csv")
    holdings = read_csv(artifacts / "holdings.csv")
    rank_ic = read_csv(artifacts / "rank_ic.csv")

    horizon = float(result.get("configuration", {}).get("rebalance_every_trading_days", 5))
    periods_per_year = 252 / horizon
    valuation_dates = [period.get("exit_date") or period["signal_date"] for period in periods]
    annual_risk_free_rates = (
        historical_risk_free_rates(risk_free_csv, valuation_dates)
        if risk_free_csv
        else [annual_risk_free_rate] * len(periods)
    )
    period_risk_free_rates = [
        (1 + rate) ** (1 / periods_per_year) - 1 for rate in annual_risk_free_rates
    ]
    strategy_returns = [float(period["net_return"]) for period in periods]
    benchmark_returns = [float(period["benchmark_return"]) for period in periods]

    strategy = benchmark = risk_free = peak = 1.0
    equity_curve = []
    for period, period_risk_free_rate in zip(periods, period_risk_free_rates):
        strategy *= 1 + float(period["net_return"])
        benchmark *= 1 + float(period["benchmark_return"])
        risk_free *= 1 + period_risk_free_rate
        peak = max(peak, strategy)
        equity_curve.append(
            {
                "date": period.get("exit_date") or period["signal_date"],
                "signal_date": period["signal_date"],
                "strategy": round(strategy, 8),
                "benchmark": round(benchmark, 8),
                "risk_free": round(risk_free, 8),
                "drawdown": round(strategy / peak - 1, 8),
            }
        )

    latest_signal = max(row["signal_date"] for row in holdings)
    latest_holdings = sorted(
        (
            {
                "instrument": row["instrument"],
                "side": row.get("side", "long"),
                "weight": float(row.get("weight", 0)),
                "score": float(row["score"]),
                "forward_return": float(row["forward_return"]),
            }
            for row in holdings
            if row["signal_date"] == latest_signal
        ),
        key=lambda row: (
            row["side"] == "short",
            -row["score"] if row["side"] == "long" else row["score"],
        ),
    )
    result.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "risk_free": {
                "proxy": "U.S. 3-Month Treasury",
                "tenor": "3M",
                "currency": "USD",
                "source": "U.S. Department of the Treasury",
                "method": "historical_series" if risk_free_csv else "fixed_assumption",
                "average_annual_rate": sum(annual_risk_free_rates) / len(annual_risk_free_rates),
                "total_return": risk_free - 1,
            },
            "capm": capm_statistics(
                strategy_returns,
                benchmark_returns,
                period_risk_free_rates,
                periods_per_year,
            ),
            "equity_curve": equity_curve,
            "annual_returns": annual_return_rows(
                periods,
                period_risk_free_rates,
                periods_per_year,
            ),
            "rank_ic_series": [
                {"date": row["datetime"], "value": float(row["rank_ic"])}
                for row in rank_ic
                if row["rank_ic"]
            ],
            "latest_holdings": latest_holdings,
            "latest_holdings_kind": "historical_evaluated_selection",
            "score_semantics": (
                "ranking_score"
                if str(result.get("configuration", {}).get("objective", "")).lower()
                in {"lambdarank", "rank_xendcg", "rank"}
                else "predicted_forward_return"
            ),
            "current_signal_available": False,
            "recent_periods": [
                {
                    "signal_date": row["signal_date"],
                    "entry_date": row.get("entry_date") or None,
                    "exit_date": row.get("exit_date") or None,
                    "net_return": float(row["net_return"]),
                    "benchmark_return": float(row["benchmark_return"]),
                    "excess_return": float(row["excess_return"]),
                    "turnover": float(row["turnover"]),
                }
                for row in periods[-12:]
            ],
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--annual-risk-free-rate",
        default=0.04,
        type=float,
        help="Annualized risk-free rate used by CAPM, expressed as a decimal (default: 0.04).",
    )
    parser.add_argument(
        "--risk-free-csv",
        type=Path,
        help="Optional historical CSV with date and annual_rate columns; rates are decimals.",
    )
    args = parser.parse_args()
    data = export(
        args.artifacts,
        args.output,
        args.annual_risk_free_rate,
        args.risk_free_csv,
    )
    print(
        f"Exported {len(data['equity_curve'])} periods and "
        f"{len(data['latest_holdings'])} holdings to {args.output}"
    )


if __name__ == "__main__":
    main()
