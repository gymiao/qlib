from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).parents[1] / "examples" / "nasdaq100_medium_low_frequency.py"
SPEC = spec_from_file_location("nasdaq100_medium_low_frequency", SCRIPT)
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def synthetic_market(instruments=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), periods=900):
    dates = pd.bdate_range("2020-01-01", periods=periods)
    rows = []
    for number, instrument in enumerate(instruments):
        rng = np.random.default_rng(100 + number)
        returns = rng.normal(0.0003 + number * 0.00005, 0.015, periods)
        close = 100 * np.cumprod(1 + returns)
        open_price = close * (1 + rng.normal(0, 0.002, periods))
        rows.append(
            pd.DataFrame(
                {
                    "datetime": dates,
                    "instrument": instrument,
                    "Open": open_price,
                    "High": np.maximum(open_price, close) * 1.005,
                    "Low": np.minimum(open_price, close) * 0.995,
                    "Close": close,
                    "Volume": rng.integers(1_000_000, 5_000_000, periods),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_build_feature_frame_has_cross_sectional_target():
    featured = MODULE.build_feature_frame(synthetic_market(), horizon=5)
    assert set(MODULE.FEATURE_COLUMNS).issubset(featured.columns)
    assert featured[MODULE.FEATURE_COLUMNS].max().max() <= 1
    assert featured[MODULE.FEATURE_COLUMNS].min().min() >= -1
    medians = featured.dropna(subset=["target"]).groupby("datetime")["target"].median()
    assert np.allclose(medians, 0)


def test_backtest_uses_next_open_and_charges_turnover():
    stocks = synthetic_market(periods=900)
    featured = MODULE.build_feature_frame(stocks, horizon=5).dropna(subset=["target"])
    dates = pd.DatetimeIndex(sorted(featured["datetime"].unique()))
    test_start = dates[-100]
    test = featured.loc[featured["datetime"] >= test_start]
    index = pd.MultiIndex.from_frame(test[["datetime", "instrument"]])
    predictions = pd.Series(test["ret_20"].to_numpy(), index=index)
    benchmark = synthetic_market(("QQQ",), periods=900)
    backtest, holdings = MODULE.run_weekly_backtest(
        test, predictions, benchmark, top_k=3, horizon=5, cost_bps=10
    )
    assert not backtest.empty
    assert len(holdings) == len(backtest) * 6
    assert set(holdings["side"]) == {"long", "short"}
    assert np.isclose(holdings.groupby(["signal_date", "side"])["weight"].sum().abs(), 0.5).all()
    assert (backtest["entry_date"] > backtest["signal_date"]).all()
    assert np.isclose(backtest.iloc[0]["transaction_cost"], 0.001)
    assert np.allclose(
        backtest["gross_return"],
        0.5 * (backtest["long_return"] - backtest["short_return"]),
    )
    assert np.allclose(
        backtest["net_return"], backtest["gross_return"] - backtest["transaction_cost"]
    )


def test_time_segments_have_purge_gap():
    dates = pd.bdate_range("2018-01-01", periods=1500)
    segments = MODULE.make_segments(dates, valid_years=1, test_years=2, purge_days=5)
    train_end = dates.get_loc(segments["train"][1])
    valid_start = dates.get_loc(segments["valid"][0])
    valid_end = dates.get_loc(segments["valid"][1])
    test_start = dates.get_loc(segments["test"][0])
    assert valid_start - train_end == 6
    assert test_start - valid_end == 6


def test_enhanced_features_and_beta_adjusted_label():
    stocks = synthetic_market(periods=900)
    benchmark = synthetic_market(("QQQ",), periods=900)
    dates = pd.bdate_range("2020-01-01", periods=900)
    macro = pd.DataFrame(
        {
            "datetime": dates,
            "rf_3m": 0.04,
            "vix": 20.0,
            "high_yield_spread": 0.035,
            "fed_funds": 0.045,
        }
    )
    featured = MODULE.build_feature_frame(
        stocks,
        horizon=5,
        benchmark=benchmark,
        macro=macro,
        label_mode="beta_adjusted",
    )
    feature_columns = featured.attrs["feature_columns"]
    assert set(MODULE.RISK_FEATURE_COLUMNS).issubset(feature_columns)
    assert set(MODULE.MARKET_FEATURE_COLUMNS).issubset(feature_columns)
    assert set(MODULE.MACRO_FEATURE_COLUMNS).issubset(feature_columns)
    assert featured["target"].notna().mean() > 0.98


def test_benchmark_relative_label_is_stock_return_minus_qqq():
    stocks = synthetic_market(periods=900)
    benchmark = synthetic_market(("QQQ",), periods=900)
    featured = MODULE.build_feature_frame(stocks, 5, benchmark=benchmark, label_mode="benchmark_relative")
    mature = featured.dropna(subset=["target", "forward_return", "qqq_forward_return"])
    np.testing.assert_allclose(mature["target"], mature["forward_return"] - mature["qqq_forward_return"])


def test_feature_only_path_never_builds_future_labels_and_is_causal():
    stocks = synthetic_market(periods=900)
    benchmark = synthetic_market(("QQQ",), periods=900)
    full = MODULE.build_feature_only_frame(stocks, horizon=5, benchmark=benchmark)
    cutoff = stocks["datetime"].drop_duplicates().sort_values().iloc[-30]
    truncated = MODULE.build_feature_only_frame(
        stocks.loc[stocks["datetime"] <= cutoff],
        horizon=5,
        benchmark=benchmark.loc[benchmark["datetime"] <= cutoff],
    )
    forbidden = {"target", "forward_return", "entry_open", "exit_open", "qqq_forward_return"}
    assert not forbidden.intersection(full.columns)
    feature_columns = full.attrs["feature_columns"]
    left = full.loc[full["datetime"] <= cutoff, ["datetime", "instrument", *feature_columns]].reset_index(drop=True)
    right = truncated[["datetime", "instrument", *feature_columns]].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
    inference = MODULE.qlib_frame(truncated, feature_columns, include_label=False)
    assert set(inference.columns.get_level_values(0)) == {"feature"}


def test_membership_is_half_open_ranked_before_cross_section_and_keeps_prewarm():
    stocks = synthetic_market(("AAA", "BBB", "CCC", "DDD"), periods=400)
    dates = pd.DatetimeIndex(sorted(stocks["datetime"].unique()))
    switch = dates[300]
    membership = pd.DataFrame({
        "symbol": ["AAA", "BBB", "BBB", "CCC"],
        "start_date": [dates[0], dates[0], switch, switch],
        "end_date": [switch, switch, pd.NaT, pd.NaT],
    })
    featured = MODULE.build_feature_only_frame(stocks, 5, membership=membership)
    before = set(featured.loc[featured["datetime"] == dates[299], "instrument"])
    after = set(featured.loc[featured["datetime"] == switch, "instrument"])
    assert before == {"AAA", "BBB"}
    assert after == {"BBB", "CCC"}
    assert featured.loc[(featured["datetime"] == switch) & (featured["instrument"] == "CCC"), "ret_60"].notna().all()
    without_nonmember = MODULE.build_feature_only_frame(stocks.loc[stocks["instrument"] != "DDD"], 5, membership=membership)
    columns = featured.attrs["feature_columns"]
    pd.testing.assert_frame_equal(
        featured[["datetime", "instrument", *columns]].reset_index(drop=True),
        without_nonmember[["datetime", "instrument", *columns]].reset_index(drop=True),
    )
