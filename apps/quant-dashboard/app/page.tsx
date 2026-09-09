"use client";

import { useEffect, useMemo, useState } from "react";
import type { DashboardData, MetricSet, ModelCatalog, ModelOption } from "../lib/types";

const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const decimal = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

type HorizonResponse = {
  error?: string;
  requested_horizon?: number;
  available_horizons?: number[];
  requires_training?: boolean;
  horizon_trading_days?: number;
  signal_date?: string;
  prediction_type?: string;
  price_prediction_available?: boolean;
  accuracy_definition?: string;
  metrics?: {
    direction_accuracy?: number;
    mae?: number;
    rmse?: number;
    mean_rank_ic: number;
    rank_ic_ir: number;
    observations: number;
  };
  historical_selections?: {
    top_10_long: Array<{ instrument: string; score: number; weight: number }>;
    bottom_10_short: Array<{ instrument: string; score: number; weight: number }>;
  };
  warning?: string;
};

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

  const all = data.flatMap((point) => [point.strategy, point.benchmark, point.risk_free]);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const range = Math.max(max - min, 0.0001);
  const chartMin = min - range * 0.08;
  const chartMax = max + range * 0.08;
  const x = (index: number) =>
    padX + (index / Math.max(data.length - 1, 1)) * (width - padX * 2);
  const y = (value: number) =>
    height - padY - ((value - chartMin) / (chartMax - chartMin)) * (height - padY * 2);
  const path = (key: "strategy" | "benchmark" | "risk_free") =>
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
        <path d={path("risk_free")} className="riskFreeLine" />
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
  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [horizonInput, setHorizonInput] = useState("5");
  const [horizonResult, setHorizonResult] = useState<HorizonResponse | null>(null);
  const [horizonLoading, setHorizonLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartWindow, setChartWindow] = useState<"all" | "1y" | "6m">("all");

  useEffect(() => {
    fetch("/data/models.json")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<ModelCatalog>;
      })
      .then((catalog) => {
        setModels(catalog.models);
        setSelectedModel(catalog.default_model);
      })
      .catch(() => {
        const fallback: ModelOption = {
          id: "nasdaq100-cross-sectional",
          name: "Nasdaq-100 横截面模型",
          description: "未来 5 日相对收益排序 · 每周调仓",
          category: "选股",
          status: "active",
          data_url: "/data/dashboard.json",
        };
        setModels([fallback]);
        setSelectedModel(fallback.id);
      });
  }, []);

  useEffect(() => {
    const model = models.find((item) => item.id === selectedModel);
    if (!model) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch(model.data_url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<DashboardData>;
      })
      .then((result) => {
        setData(result);
        setChartWindow("all");
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(String(reason));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [models, selectedModel]);

  const latestDate = useMemo(() => data?.equity_curve.at(-1)?.date ?? "—", [data]);
  const activeModel = useMemo(
    () => models.find((model) => model.id === selectedModel),
    [models, selectedModel],
  );
  const visibleEquity = useMemo(() => {
    if (!data || chartWindow === "all") return data?.equity_curve ?? [];
    const periods = chartWindow === "1y" ? 52 : 26;
    return data.equity_curve.slice(-periods);
  }, [chartWindow, data]);

  async function queryHorizon() {
    setHorizonLoading(true);
    try {
      const response = await fetch(`/api/prediction?horizon=${encodeURIComponent(horizonInput)}`);
      const result = await response.json() as HorizonResponse;
      setHorizonResult(result);
    } catch (reason) {
      setHorizonResult({ error: `接口请求失败：${String(reason)}` });
    } finally {
      setHorizonLoading(false);
    }
  }

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
          <a href="#methodology">模型</a>
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
          <div className="modelSelector">
            <label htmlFor="model-select">研究模型</label>
            <div className="selectWrap">
              <select
                id="model-select"
                value={selectedModel}
                disabled={loading}
                onChange={(event) => setSelectedModel(event.target.value)}
              >
                {models.map((model) => (
                  <option value={model.id} key={model.id} disabled={model.status === "planned"}>
                    {model.name}{model.status === "planned" ? "（规划中）" : ""}
                  </option>
                ))}
              </select>
              <span>
                {loading ? "LOADING" : `${activeModel?.category} / ${activeModel?.status.toUpperCase()}`}
              </span>
            </div>
            <small>{activeModel?.description}</small>
          </div>
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

      <section className="horizonQuery" aria-labelledby="horizon-query-title">
        <div>
          <span className="microLabel">PREDICTION API</span>
          <h2 id="horizon-query-title">选择预测期限</h2>
          <p>输入未来交易日数。系统只报告该期限独立训练后的样本外准确率。</p>
        </div>
        <div className="horizonForm">
          <label htmlFor="horizon-days">未来</label>
          <input
            id="horizon-days"
            type="number"
            min="1"
            max="252"
            step="1"
            value={horizonInput}
            onChange={(event) => setHorizonInput(event.target.value)}
          />
          <span>交易日</span>
          <button type="button" onClick={queryHorizon} disabled={horizonLoading}>
            {horizonLoading ? "查询中" : "查询准确率"}
          </button>
        </div>

        {horizonResult && (
          <div className={`horizonResult ${horizonResult.error ? "error" : ""}`}>
            {horizonResult.error ? (
              <>
                <strong>该期限暂不可用</strong>
                <p>{horizonResult.error}</p>
                <small>当前可用：{horizonResult.available_horizons?.join("、") ?? "5"}个交易日</small>
              </>
            ) : (
              <>
                <div>
                  <span>方向准确率</span>
                  <strong>{formatPercent(horizonResult.metrics?.direction_accuracy ?? 0)}</strong>
                  <small>{horizonResult.accuracy_definition}</small>
                </div>
                <div>
                  <span>收益 MAE</span>
                  <strong>{formatPercent(horizonResult.metrics?.mae ?? 0)}</strong>
                  <small>预测与实际Beta调整收益的平均绝对误差</small>
                </div>
                <div>
                  <span>收益 RMSE</span>
                  <strong>{formatPercent(horizonResult.metrics?.rmse ?? 0)}</strong>
                  <small>对较大预测误差更加敏感</small>
                </div>
                <div>
                  <span>平均 Rank IC</span>
                  <strong>{decimal.format(horizonResult.metrics?.mean_rank_ic ?? 0)}</strong>
                  <small>{horizonResult.metrics?.observations.toLocaleString()}个样本</small>
                </div>
                <p>{horizonResult.warning}</p>
                <div className="horizonSelections">
                  <div>
                    <header>
                      <span>TOP 10 / LONG</span>
                      <small>信号日 {horizonResult.signal_date}</small>
                    </header>
                    {horizonResult.historical_selections?.top_10_long.map((holding, index) => (
                      <div className="selectionRow" key={`long-${holding.instrument}`}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <strong>{holding.instrument}</strong>
                        <em>{holding.score.toFixed(4)}</em>
                        <small>{formatPercent(holding.weight)}</small>
                      </div>
                    ))}
                  </div>
                  <div className="short">
                    <header>
                      <span>BOTTOM 10 / SHORT</span>
                      <small>信号日 {horizonResult.signal_date}</small>
                    </header>
                    {horizonResult.historical_selections?.bottom_10_short.map((holding, index) => (
                      <div className="selectionRow" key={`short-${holding.instrument}`}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <strong>{holding.instrument}</strong>
                        <em>{holding.score.toFixed(4)}</em>
                        <small>{formatPercent(holding.weight)}</small>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </section>

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

      <section className="capmSection" aria-labelledby="capm-title">
        <div className="sectionIntro">
          <div><span className="sectionNumber">CAPM</span><h2 id="capm-title">市场风险归因</h2></div>
          <p>QQQ 作为市场组合</p>
        </div>
        <div className="rfDisclosure">
          <div>
            <span>Rf 代理</span>
            <strong>{data.risk_free.proxy}</strong>
          </div>
          <div>
            <span>期限 / 币种</span>
            <strong>{data.risk_free.tenor} / {data.risk_free.currency}</strong>
          </div>
          <div>
            <span>平均年化利率</span>
            <strong>{formatPercent(data.risk_free.average_annual_rate)}</strong>
          </div>
          <div>
            <span>数据来源</span>
            <strong>{data.risk_free.source}</strong>
          </div>
          <div>
            <span>计算方式</span>
            <strong>
              {data.risk_free.method === "historical_series" ? "按历史日期匹配" : "固定利率回退假设"}
            </strong>
          </div>
        </div>
        <div className="metricGrid capmGrid">
          <MetricCard
            label="年化 Alpha"
            value={formatPercent(data.capm.alpha_annualized)}
            context="扣除无风险收益与市场 Beta 后"
            tone={data.capm.alpha_annualized >= 0 ? "positive" : "warning"}
          />
          <MetricCard
            label="策略 Beta"
            value={decimal.format(data.capm.beta)}
            context="对 QQQ 超额收益的敏感度"
          />
          <MetricCard
            label="Rf 基准累计收益"
            value={formatPercent(data.risk_free.total_return)}
            context={`${data.risk_free.tenor} · ${
              data.risk_free.method === "historical_series" ? "历史序列" : "固定利率假设"
            }`}
          />
          <MetricCard
            label="CAPM R²"
            value={formatPercent(data.capm.r_squared)}
            context="策略波动中可由市场解释的比例"
          />
        </div>
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
              <span><i className="riskFreeSwatch" />Rf 基准</span>
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
            <div><span className="sectionNumber">04</span><h2>历史研究持仓</h2></div>
            <span className="microLabel">HISTORICAL TOP {data.configuration.top_k}</span>
          </div>
          <div className="holdingList">
            <div className="holdingHeader">
              <span>排名</span><span>方向</span><span>证券</span><span>模型分数</span><span>权重</span>
            </div>
            {data.latest_holdings.map((holding, index) => (
              <div className={`holdingRow ${holding.side}`} key={`${holding.side}-${holding.instrument}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <b>{holding.side === "long" ? "LONG" : "SHORT"}</b>
                <strong>{holding.instrument}</strong>
                <div>
                  <i style={{
                    width: `${Math.max(
                      8,
                      Math.abs(holding.score) /
                        Math.max(...data.latest_holdings.map((item) => Math.abs(item.score)), 0.0001) *
                        100,
                    )}%`,
                  }} />
                </div>
                <em>{formatPercent(holding.weight)}</em>
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
            <div><dt>组合结构</dt><dd>Top / Bottom {data.configuration.top_k} · 美元中性</dd></div>
            <div><dt>总 / 净敞口</dt><dd>100% / 0%</dd></div>
            <div><dt>净 Beta 上限</dt><dd>±{decimal.format(data.configuration.max_net_beta ?? 0.1)}</dd></div>
            <div><dt>交易成本</dt><dd>{data.configuration.one_way_cost_bps} bps / 单边</dd></div>
            <div><dt>训练区间</dt><dd>{data.segments.train.join(" → ")}</dd></div>
            <div><dt>测试区间</dt><dd>{data.segments.test.join(" → ")}</dd></div>
          </dl>
        </article>
      </section>

      <section className="panel annualPanel">
        <div className="panelHead">
          <div><span className="sectionNumber">06</span><h2>年度收益率</h2></div>
          <span className="microLabel">CALENDAR YEAR</span>
        </div>
        <div className="annualReturnList">
          <div className="annualReturnHead">
            <span>年份</span><span>策略</span><span>QQQ</span><span>Rf基准</span><span>相对QQQ</span>
          </div>
          {data.annual_returns.map((year) => (
            <div className="annualReturnRow" key={year.year}>
              <div>
                <strong>{year.year}</strong>
                <small>{year.complete_year ? "完整年度" : "部分年度"} · {year.periods}期</small>
              </div>
              <strong className={year.strategy_return >= 0 ? "valuePositive" : "valueNegative"}>
                {formatPercent(year.strategy_return)}
              </strong>
              <span>{formatPercent(year.benchmark_return)}</span>
              <span>{formatPercent(year.risk_free_return)}</span>
              <strong className={year.relative_to_benchmark >= 0 ? "valuePositive" : "valueNegative"}>
                {formatPercent(year.relative_to_benchmark)}
              </strong>
            </div>
          ))}
        </div>
        <p className="annualNote">
          收益按各自然年内调仓周期复合计算；部分年度仅覆盖 {data.annual_returns
            .filter((year) => !year.complete_year)
            .map((year) => `${year.start_date}—${year.end_date}`)
            .join("、")}。
        </p>
      </section>

      <section className="panel periodsPanel">
        <div className="panelHead">
          <div><span className="sectionNumber">07</span><h2>近期调仓表现</h2></div>
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

      <section className="methodologySection" id="methodology">
        <div className="methodologyHead">
          <div>
            <span className="sectionNumber">08</span>
            <h2>模型设计与已知问题</h2>
          </div>
          <span className="microLabel">MODEL CARD</span>
        </div>

        <div className="modelArchitecture">
          <div>
            <span>01 / UNIVERSE</span>
            <strong>Nasdaq-100</strong>
            <p>{data.downloaded_securities} 只证券 · 当前成分股快照</p>
          </div>
          <i>→</i>
          <div>
            <span>02 / FEATURES</span>
            <strong>{data.configuration.feature_count ?? 17} 维输入</strong>
            <p>量价、风险、QQQ状态与FRED宏观变量</p>
          </div>
          <i>→</i>
          <div>
            <span>03 / MODEL</span>
            <strong>LightGBM {data.configuration.objective?.toUpperCase() ?? "MSE"}</strong>
            <p>共享横截面模型 · 年度扩展窗口重训</p>
          </div>
          <i>→</i>
          <div>
            <span>04 / PORTFOLIO</span>
            <strong>Top / Bottom {data.configuration.top_k}</strong>
            <p>{data.configuration.horizon_trading_days} 日持有 · 净 Beta ±{
              decimal.format(data.configuration.max_net_beta ?? 0.1)
            }</p>
          </div>
        </div>

        <div className="modelDetailGrid">
          <article>
            <span className="microLabel">TRAINING DESIGN</span>
            <h3>模型如何学习</h3>
            <ul>
              <li>每条样本对应“交易日 × 股票”，所有股票联合训练一个模型。</li>
              <li>
                标签为未来 {data.configuration.horizon_trading_days} 日 Beta 调整收益，
                信号在收盘生成并在下一交易日开盘执行。
              </li>
              <li>股票特征按日进行横截面排名；市场与宏观变量使用仅依赖历史的滚动标准化。</li>
              <li>
                训练、验证和测试之间设置 {data.configuration.horizon_trading_days} 个交易日隔离，
                测试期按年度 walk-forward 重训。
              </li>
              <li>
                组合做多Top {data.configuration.top_k}、做空Bottom {data.configuration.top_k}，
                多空各占50%，并计入单边 {data.configuration.one_way_cost_bps} bps成本。
              </li>
            </ul>
          </article>

          <article className="issueCard">
            <span className="microLabel">KNOWN LIMITATIONS</span>
            <h3>结果可能在哪里失真</h3>
            <ul>
              <li><strong>幸存者偏差：</strong>历史回测仍使用当前成分股快照，可能高估收益。</li>
              <li><strong>基本面缺失：</strong>尚无按财报发布日期整理的公司级 point-in-time 数据。</li>
              <li><strong>宏观覆盖：</strong>高收益债利差在当前FRED返回中仅从2023-07-28起可用。</li>
              <li><strong>成本简化：</strong>固定bps无法模拟真实买卖价差、冲击成本和成交容量。</li>
              <li><strong>样本有限：</strong>测试期约两年，仍可能对少数市场状态过度适配。</li>
              <li><strong>模型不稳定：</strong>不同walk-forward窗口最佳迭代数差异较大，需要持续监控。</li>
            </ul>
          </article>
        </div>

        <div className="modelFootnote">
          <span>当前结论</span>
          <p>
            增强特征在现有样本外测试中优于基础17维，但在补齐历史成分股和point-in-time基本面之前，
            页面结果只能视为研究证据，不能视为可实现的实盘收益。
          </p>
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
