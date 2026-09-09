export type MetricSet = {
  periods: number;
  total_return: number;
  annualized_return: number;
  annualized_volatility: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
};

export type ModelOption = {
  id: string;
  name: string;
  description: string;
  category: string;
  status: "active" | "research" | "planned";
  data_url: string;
};

export type ModelCatalog = {
  default_model: string;
  models: ModelOption[];
};

export type DashboardData = {
  generated_at: string;
  score_semantics?: "ranking_score" | "predicted_forward_return";
  latest_holdings_kind?: "historical_evaluated_selection";
  current_signal_available?: boolean;
  research_only: boolean;
  survivorship_bias_warning: boolean;
  universe: {
    effective_date: string;
    securities: number;
    source: string;
    point_in_time: boolean;
  };
  downloaded_securities: number;
  configuration: {
    start: string;
    end: string | null;
    horizon_trading_days: number;
    rebalance_every_trading_days: number;
    top_k: number;
    one_way_cost_bps: number;
    max_net_beta?: number;
    portfolio_mode?: string;
    gross_exposure?: number;
    net_exposure?: number;
    label_mode?: string;
    feature_count?: number;
    feature_columns?: string[];
    walk_forward_years?: number;
    best_iteration?: number;
    objective?: string;
    feature_set?: string;
    fred_series?: Record<string, string>;
  };
  walk_forward_windows?: Array<Record<string, string>>;
  segments: Record<string, [string, string]>;
  prediction: {
    observations: number;
    mean_daily_rank_ic: number;
    rank_ic_std: number;
    rank_ic_ir: number;
    positive_rank_ic_rate: number;
    direction_accuracy?: number;
    mae?: number;
    rmse?: number;
    target?: string;
  };
  strategy_net: MetricSet;
  benchmark_qqq: MetricSet;
  excess: MetricSet;
  average_turnover: number;
  average_portfolio_beta?: number;
  risk_free: {
    proxy: string;
    tenor: string;
    currency: string;
    source: string;
    method: "historical_series" | "fixed_assumption";
    average_annual_rate: number;
    total_return: number;
  };
  capm: {
    alpha_annualized: number;
    beta: number;
    r_squared: number;
  };
  equity_curve: Array<{
    date: string;
    strategy: number;
    benchmark: number;
    risk_free: number;
    drawdown: number;
  }>;
  annual_returns: Array<{
    year: number;
    periods: number;
    complete_year: boolean;
    start_date: string;
    end_date: string;
    strategy_return: number;
    benchmark_return: number;
    risk_free_return: number;
    relative_to_benchmark: number;
  }>;
  rank_ic_series: Array<{ date: string; value: number }>;
  latest_holdings: Array<{
    instrument: string;
    side: "long" | "short";
    weight: number;
    score: number;
    forward_return: number;
  }>;
  recent_periods: Array<{
    signal_date: string;
    net_return: number;
    benchmark_return: number;
    excess_return: number;
    turnover: number;
  }>;
};
