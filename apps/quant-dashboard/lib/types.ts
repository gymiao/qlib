export type MetricSet = {
  periods: number;
  total_return: number;
  annualized_return: number;
  annualized_volatility: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
};

export type DashboardData = {
  generated_at: string;
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
  };
  segments: Record<string, [string, string]>;
  prediction: {
    observations: number;
    mean_daily_rank_ic: number;
    rank_ic_std: number;
    rank_ic_ir: number;
    positive_rank_ic_rate: number;
  };
  strategy_net: MetricSet;
  benchmark_qqq: MetricSet;
  excess: MetricSet;
  average_turnover: number;
  equity_curve: Array<{
    date: string;
    strategy: number;
    benchmark: number;
    drawdown: number;
  }>;
  rank_ic_series: Array<{ date: string; value: number }>;
  latest_holdings: Array<{
    instrument: string;
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
