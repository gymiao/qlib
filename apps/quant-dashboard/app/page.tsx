"use client";

import { useEffect, useMemo, useState } from "react";
import type { DashboardData, MetricSet } from "../lib/types";

const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const decimal = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatPercent(value: number) {
  return percent.format(value);
}

function LineChart({ data }: { data: DashboardData["equity_curve"] }) {
  const width = 1000;
  const height = 320;
  const padX = 54;
  const padY = 18;

  if (data.length === 0) {
    return <div className="emptyChart">暂无净值数据</div>;
  }

  const all = data.flatMap((point) => [point.strategy, point.benchmark]);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const range = Math.max(max - min, 0.0001);
  const chartMin = min - range * 0.08;
  const chartMax = max + range * 0.08;
  const x = (index: number) =>
    padX + (index / Math.max(data.length - 1, 1)) * (width - padX * 2);
  const y = (value: number) =>
    height - padY - ((value - chartMin) / (chartMax - chartMin)) * (height - padY * 2);
  const path = (key: "strategy" | "benchmark") =>
    data.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point[key])}`).join(" ");
  const levels = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="chartWrap" aria-label="策略与基准净值曲线">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <defs>
          <linearGradient id="strategyFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#9cf59d" stopOpacity=".22" />
            <stop offset="100%" stopColor="#9cf59d" stopOpacity="0" />
          </linearGradient>
        </defs>
        {levels.map((level) => {
          const value = chartMax - (chartMax - chartMin) * level;
          const lineY = padY + (height - padY * 2) * level;
          return (
            <g key={level}>
              <line x1={padX} x2={width - padX} y1={lineY} y2={lineY} className="gridLine" />
              <text x="0" y={lineY + 4} className="axisLabel">{value.toFixed(2)}</text>
            </g>
          );
        })}
        <path
          d={`${path("strategy")} L${x(data.length - 1)},${height - padY} L${x(0)},${height - padY} Z`}
          fill="url(#strategyFill)"
        />
        <path d={path("benchmark")} className="benchmarkLine" />
        <path d={path("strategy")} className="strategyLine" />
        <circle cx={x(data.length - 1)} cy={y(data.at(-1)!.strategy)} r="4" className="strategyPoint" />
      </svg>
      <div className="chartAxis">
        <span>{data.at(0)?.date}</span>
        <span>{data.at(-1)?.date}</span>
      </div>
    </div>
  );
}

function IcChart({ data }: { data: DashboardData["rank_ic_series"] }) {
  if (data.length === 0) {
    return <div className="emptyChart compact">暂无 Rank IC 数据</div>;
  }
  const maxAbs = Math.max(...data.map((point) => Math.abs(point.value)), 0.1);
  return (
    <div className="icChart" aria-label="Rank IC序列">
      {data.map((point) => {
        const size = Math.max((Math.abs(point.value) / maxAbs) * 48, 2);
        return (
          <span
            key={point.date}
            title={`${point.date}: ${point.value.toFixed(3)}`}
            className={point.value >= 0 ? "icPositive" : "icNegative"}
            style={{
              height: `${size}px`,
              transform: point.value >= 0 ? "translateY(-100%)" : undefined,
            }}
          />
        );
      })}
    </div>
  );
}

function MetricCard({
  label,
  value,
  context,
  tone = "default",
}: {
  label: string;
  value: string;
  context: string;
  tone?: "default" | "positive" | "warning";
}) {
  return (
    <article className={`metricCard ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{context}</small>
    </article>
  );
}

function ComparisonRow({
  label,
  strategy,
  benchmark,
  formatter = formatPercent,
}: {
  label: string;
  strategy: number;
  benchmark: number;
  formatter?: (value: number) => string;
}) {
  return (
    <div className="comparisonRow">
      <span>{label}</span>
      <strong>{formatter(strategy)}</strong>
      <em>{formatter(benchmark)}</em>
    </div>
  );
}

function RiskPanel({ strategy, benchmark }: { strategy: MetricSet; benchmark: MetricSet }) {
  return (
    <div className="comparison">
      <div className="comparisonHead">
        <span>指标</span>
        <strong>策略</strong>
        <em>QQQ</em>
      </div>
      <ComparisonRow label="年化收益" strategy={strategy.annualized_return} benchmark={benchmark.annualized_return} />
      <ComparisonRow
        label="年化波动"
        strategy={strategy.annualized_volatility}
        benchmark={benchmark.annualized_volatility}
      />
      <ComparisonRow
        label="夏普比率"
        strategy={strategy.sharpe}
        benchmark={benchmark.sharpe}
        formatter={(value) => decimal.format(value)}
      />
      <ComparisonRow label="最大回撤" strategy={strategy.max_drawdown} benchmark={benchmark.max_drawdown} />
      <ComparisonRow label="胜率" strategy={strategy.win_rate} benchmark={benchmark.win_rate} />
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chartWindow, setChartWindow] = useState<"all" | "1y" | "6m">("all");

  useEffect(() => {
    fetch("/data/dashboard.json")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(setData)
      .catch((reason) => setError(String(reason)));
  }, []);

  const latestDate = useMemo(() => data?.equity_curve.at(-1)?.date ?? "—", [data]);
  const visibleEquity = useMemo(() => {
    if (!data || chartWindow === "all") return data?.equity_curve ?? [];
    const periods = chartWindow === "1y" ? 52 : 26;
    return data.equity_curve.slice(-periods);
  }, [chartWindow, data]);

  if (error) {
    return <main className="state">数据加载失败：{error}</main>;
  }
  if (!data) {
    return <main className="state">正在载入研究数据…</main>;
  }

  return (
    <main>
      <header className="topbar">
        <div className="brand">
          <span className="brandMark">Q</span>
          <div>
            <strong>QLIB / RESEARCH</strong>
            <small>中低频策略控制台</small>
          </div>
        </div>
        <nav className="topNav" aria-label="页面导航">
          <a href="#performance">表现</a>
          <a href="#signals">信号</a>
          <a href="#positions">持仓</a>
        </nav>
        <div className="topMeta">
          <span className="statusDot" />
          数据截至 {latestDate}
        </div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">NASDAQ-100 · CROSS-SECTIONAL · WEEKLY</p>
          <h1>把模型信号，放回<br />真实的交易语境。</h1>
          <p className="heroCopy">
            一个与 Qlib 训练核心解耦的研究视图。追踪净值、风险、信号质量与每期持仓，
            所有数字来自可复现的样本外产物。
          </p>
        </div>
        <div className="runStamp">
          <span>RUN / 001</span>
          <strong>{data.configuration.horizon_trading_days}D</strong>
          <small>预测与调仓周期</small>
        </div>
      </section>

      {data.survivorship_bias_warning && (
        <aside className="warningBanner">
          <span>研究限制</span>
          当前使用 {data.universe.effective_date} 成分股快照回测历史，存在幸存者偏差。
          页面数字仅用于工程验证，不构成投资建议。
        </aside>
      )}

      <section className="metricGrid">
        <MetricCard
          label="策略累计收益"
          value={formatPercent(data.strategy_net.total_return)}
          context={`${data.strategy_net.periods} 个调仓周期 · 已扣成本`}
          tone="positive"
        />
        <MetricCard
          label="年化夏普"
          value={decimal.format(data.strategy_net.sharpe)}
          context={`QQQ ${decimal.format(data.benchmark_qqq.sharpe)}`}
        />
        <MetricCard
          label="最大回撤"
          value={formatPercent(data.strategy_net.max_drawdown)}
          context={`QQQ ${formatPercent(data.benchmark_qqq.max_drawdown)}`}
          tone="warning"
        />
        <MetricCard
          label="平均 Rank IC"
          value={decimal.format(data.prediction.mean_daily_rank_ic)}
          context={`${formatPercent(data.prediction.positive_rank_ic_rate)} 日期为正`}
        />
      </section>

      <section className="panel equityPanel" id="performance">
        <div className="panelHead">
          <div>
            <span className="sectionNumber">01</span>
            <h2>样本外净值</h2>
          </div>
          <div className="chartTools">
            <div className="legend">
              <span><i className="strategySwatch" />策略净值</span>
              <span><i className="benchmarkSwatch" />QQQ</span>
            </div>
            <div className="rangeControl" aria-label="净值展示区间">
              {(["6m", "1y", "all"] as const).map((window) => (
                <button
                  type="button"
                  className={chartWindow === window ? "active" : ""}
                  onClick={() => setChartWindow(window)}
                  key={window}
                >
                  {window === "all" ? "全部" : window.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        </div>
        <LineChart data={visibleEquity} />
      </section>

      <section className="splitGrid" id="signals">
        <article className="panel">
          <div className="panelHead">
            <div><span className="sectionNumber">02</span><h2>收益 / 风险</h2></div>
          </div>
          <RiskPanel strategy={data.strategy_net} benchmark={data.benchmark_qqq} />
        </article>

        <article className="panel">
          <div className="panelHead">
            <div><span className="sectionNumber">03</span><h2>信号脉搏</h2></div>
            <span className="microLabel">DAILY RANK IC</span>
          </div>
          <IcChart data={data.rank_ic_series} />
          <div className="signalStats">
            <div><span>观测数</span><strong>{data.prediction.observations.toLocaleString()}</strong></div>
            <div><span>IC IR</span><strong>{decimal.format(data.prediction.rank_ic_ir)}</strong></div>
            <div><span>平均换手</span><strong>{formatPercent(data.average_turnover)}</strong></div>
          </div>
        </article>
      </section>

      <section className="splitGrid lower" id="positions">
        <article className="panel">
          <div className="panelHead">
            <div><span className="sectionNumber">04</span><h2>最新模型持仓</h2></div>
            <span className="microLabel">TOP {data.configuration.top_k}</span>
          </div>
          <div className="holdingList">
            {data.latest_holdings.map((holding, index) => (
              <div className="holdingRow" key={holding.instrument}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{holding.instrument}</strong>
                <div>
                  <i style={{ width: `${Math.max(12, (holding.score / data.latest_holdings[0].score) * 100)}%` }} />
                </div>
                <em>{holding.score.toFixed(4)}</em>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="panelHead">
            <div><span className="sectionNumber">05</span><h2>运行参数</h2></div>
            <span className="microLabel">REPRODUCIBLE</span>
          </div>
          <dl className="configList">
            <div><dt>股票池</dt><dd>Nasdaq-100 / {data.downloaded_securities} 证券</dd></div>
            <div><dt>持仓数量</dt><dd>Top {data.configuration.top_k} / 等权</dd></div>
            <div><dt>交易成本</dt><dd>{data.configuration.one_way_cost_bps} bps / 单边</dd></div>
            <div><dt>训练区间</dt><dd>{data.segments.train.join(" → ")}</dd></div>
            <div><dt>测试区间</dt><dd>{data.segments.test.join(" → ")}</dd></div>
          </dl>
        </article>
      </section>

      <section className="panel periodsPanel">
        <div className="panelHead">
          <div><span className="sectionNumber">06</span><h2>近期调仓表现</h2></div>
          <span className="microLabel">NET OF COSTS</span>
        </div>
        <div className="periodTableWrap">
          <table className="periodTable">
            <thead>
              <tr>
                <th>信号日期</th>
                <th>策略净收益</th>
                <th>QQQ</th>
                <th>超额收益</th>
                <th>换手率</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_periods.slice(-8).reverse().map((period) => (
                <tr key={period.signal_date}>
                  <td>{period.signal_date}</td>
                  <td className={period.net_return >= 0 ? "valuePositive" : "valueNegative"}>
                    {formatPercent(period.net_return)}
                  </td>
                  <td>{formatPercent(period.benchmark_return)}</td>
                  <td className={period.excess_return >= 0 ? "valuePositive" : "valueNegative"}>
                    {formatPercent(period.excess_return)}
                  </td>
                  <td>{formatPercent(period.turnover)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <footer>
        <span>QLIB QUANT RESEARCH CONSOLE</span>
        <p>研究用途 · 非投资建议 · 数据与模型均可能失效</p>
        <span>GENERATED {data.generated_at.slice(0, 10)}</span>
      </footer>
    </main>
  );
}
