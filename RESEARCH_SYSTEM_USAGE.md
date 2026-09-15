# Qlib 多资产研究系统使用指南

更新时间：2026-09-15

## 1. 这套系统能做什么

当前实现位于 `apps/quant_research`，用于可复现的研究和模拟执行，主要包括：

- QQQ/SPY 持有与现金减仓对照、联合风险暴露和事件账本；
- Nasdaq-100 long-only 选股、基准比较和样本外实验；
- 固定或滚动 Protective Put、Bear Put Spread、Cash-Secured Put 生命周期回放；
- 当前信号到订单计划、部分成交、重试、保存和恢复的模拟盘流程；
- 内容寻址的数据快照、运行 manifest、产物 hash 校验和 Dashboard v2 导出。

这是研究系统，不是券商交易客户端。它不会自动连接真实账户或下单。示例数据和
Black-Scholes 输出仅用于验证流程，不能作为历史可成交收益或投资建议。

## 2. 环境准备

项目当前使用独立 Conda 环境 `qlib-project`，位置为
`/home/mgy/miniconda3/envs/qlib-project`。该环境于 2026-09-15 从已经验证的本机
`qlib` 环境离线克隆，原环境没有被修改：

```bash
/home/mgy/miniconda3/bin/conda --no-plugins create \
  --name qlib-project \
  --clone /home/mgy/miniconda3/envs/qlib \
  --offline \
  --yes
```

`--no-plugins` 用于避免纯本地克隆被 Conda 的远端渠道条款插件阻断；这条命令不访问
网络。环境已经存在时不要重复创建。日常在 WSL/bash 中执行：

```bash
cd /mnt/c/quant/project/qlib
source /home/mgy/miniconda3/etc/profile.d/conda.sh
conda activate qlib-project
python -c "import qlib, pandas, lightgbm; print(qlib.__version__)"
node --version
npm --version
```

当前已验证版本为 Python 3.11.15、Node.js 22.23.1、npm 10.9.8、NumPy 2.4.6、
Pandas 2.3.3、LightGBM 4.7.0 和 CVXPY 1.9.2。Qlib 从当前仓库源码导入。
旧文档中出现的 `qlib-aapl` 环境已经不存在；`qlib` 现在只作为已验证的克隆来源保留，
项目命令统一在 `qlib-project` 中运行。

快速验证新环境：

```bash
python -m unittest discover -s apps/quant_research/tests -p 'test_*.py'
```

2026-09-15 的实际结果为 165 项测试全部通过。

## 3. 五分钟离线快速开始

仓库提供 `examples/data/qqq_spy_stage1_demo.csv.example`，这是合成数据，只用于验证命令、
账本和报告。先运行 Stage 1：

```bash
RUN_DIR=$(python -m apps.quant_research.run_stage1 \
  --prices-csv examples/data/qqq_spy_stage1_demo.csv.example \
  --config apps/quant_research/configs/etf_cash_demo.yaml \
  --as-of 2024-02-14T00:00:00Z)
echo "$RUN_DIR"
```

`RUN_DIR` 是内容寻址的不可变运行目录。相同数据、配置、依赖和源码会得到相同的
run ID。随后验证 manifest 及全部文件 hash：

```bash
python -m apps.quant_research.inspect_artifacts --run-dir "$RUN_DIR"
```

成功输出中的 `status` 应为 `valid`。运行目录包含：

| 文件 | 含义 |
| --- | --- |
| `manifest.json` | run ID、数据快照、配置、依赖、源码及产物 hash |
| `config.json` | 本次冻结配置 |
| `result.json` | 策略指标和风险快照 |
| `equity_curves.csv` | `initial_hold` 与 `cash_reduced` 的逐日账户曲线 |
| `orders.csv` | 建仓订单和费用 |
| `event_log.json` | 可重放的账户事件 |

如果已存在同一 run，程序会先复验产物再复用；文件被修改后会明确报错，不会静默使用。

## 4. 使用自己的 QQQ/SPY 数据

CSV 必须使用以下结构：

```csv
date,QQQ,SPY
2024-01-02,402.83,472.65
2024-01-03,400.21,470.26
```

要求：

- 日期升序且不能重复；
- QQQ、SPY 价格必须完整、有限且大于零；
- 价格口径必须一致，并在数据来源记录中说明是否复权；
- `--as-of` 必须带时区，表示该输入在什么时候可供研究使用，例如
  `2026-09-09T00:00:00Z`。

运行命令与快速开始相同，只需替换 `--prices-csv`。默认配置为 10 bps 单边费用、
T+1 结算、10 万美元模拟资金、45% QQQ + 45% SPY 初始组合，以及
30% QQQ + 30% SPY 的现金减仓组合。修改前复制
`apps/quant_research/configs/etf_cash_demo.yaml`，不要覆盖已有实验引用的配置。

配置中两组权重之和都不能超过 1；系统当前不允许融资、负权重或碎股。

## 4.1 生产数据质量门

在把供应商数据用于正式历史实验前，先运行统一质量审计。该命令只读取固定 CSV，
不会联网、修改源文件或自动把缺失数据降级为可用：

```bash
python -m apps.quant_research.audit_production_data \
  --instrument-master path/to/instrument_master.csv \
  --membership path/to/universe_membership.csv \
  --bars path/to/bars.csv \
  --corporate-actions path/to/corporate_actions.csv \
  --start 2015-01-01T00:00:00Z \
  --end 2026-09-01T00:00:00Z \
  --option-contracts path/to/option_contracts.csv \
  --option-quotes path/to/option_quotes.csv \
  --option-lifecycle-events path/to/option_lifecycle_events.csv \
  --fundamentals path/to/fundamental_vintages.csv \
  --classification-vintages path/to/classification_vintages.csv \
  --macro-vintages path/to/macro_vintages.csv \
  --output path/to/readiness-report.json
```

股票数据的四个输入是必需的；期权合约主表和报价必须同时提供，也可以同时省略。
所有表都必须显式声明 `source` 和
`source_kind=historical_observed|synthetic|scenario`，所有事件时间和 `available_at` 都必须
带时区。只有 `historical_observed` 可能解锁历史能力。审计检查：

- 稳定 instrument ID、主键、外键和上市区间；
- `[effective_from, effective_to)` 成员区间是否重叠，以及成员在请求区间内是否有行情；
- OHLCV、复权因子、公司行动和退市清算规则；
- 期权合约生命周期、交割物、整数乘数、bid/ask、报价可用时间及请求区间覆盖；
- 可选提前指派事件的合约外键、发生/可见双时间轴、张数和来源证据。

无阻断问题时退出码为 0，并把股票能力标记为 `historical_validated`；否则退出码为 2，
能力维持 `prototype`，报告中的 `issues` 给出稳定原因码和 CSV 行号。期权未提供时为
`not_provided`，不会被推断为已验证；`live_execution` 始终为 `not_evaluated`。
提前指派文件单独输出 `option_lifecycle=observed_historical_events|scenario_only|invalid|not_provided`，
报价通过不会自动冒充生命周期数据已经齐备。

最小字段契约由 `apps/quant_research/data_readiness.py` 中的 `*_COLUMNS` 常量定义。
这个检查只证明数据结构、时间和关系约束通过，不证明策略有效，也不替代数据授权、
交易日完整率、供应商抽样规则和人工抽样核对。

仓库中的 `examples/data/production_bundle_demo` 是合成 schema 样例。它应返回退出码 2、
`NON_HISTORICAL_SOURCE`，并保持股票 `prototype`、期权 `scenario_only`；这个失败案例用于
验证“格式正确的演示数据也不能解锁历史能力”。

旧 Nasdaq 示例现在也能读取 canonical 成员表：

```bash
python examples/nasdaq100_medium_low_frequency.py \
  --membership-file path/to/universe_membership.csv \
  --instrument-master-file path/to/instrument_master.csv \
  --bars-file path/to/bars.csv \
  --corporate-actions-file path/to/corporate_actions.csv \
  --fundamental-vintages-file path/to/fundamental_vintages.csv \
  --classification-vintages-file path/to/classification_vintages.csv \
  --macro-vintages-file path/to/macro_vintages.csv \
  --membership-index-id NDX \
  --start 2015-01-01T00:00:00Z \
  --end 2026-09-01T00:00:00Z \
  --max-bar-delay-hours 12 \
  --decision-timezone America/New_York \
  --decision-time-local 18:00:00
```

程序会使用请求区间内全部历史成员的并集下载行情，而不是继续使用当前成分股快照；
稳定 instrument ID 根据 symbol 有效区间映射为下载代码。成员进入当日横截面前同时满足
`effective_from <= decision_time < effective_to` 和 `available_at <= decision_time`，默认
决策时点为纽约时间 16:00，并自动处理夏令时。旧的 `symbol,start_date,end_date` 格式
仍可用于原型兼容，但因为没有 `available_at`，结果继续显示历史数据警告，不能视为
`historical_validated`。

提供 `--bars-file` 时，主程序会在训练前重新运行同一质量门；任何阻断问题都会终止，
不会回退到 Yahoo 数据。canonical bars 保存 raw OHLCV 和 `adjustment_factor`，加载器将
价格乘以该因子、成交量除以该因子，并根据 bar 时点选择当时有效的 symbol。报告保存
`audit_id`、能力状态、冻结阈值和 `market_data_mode=canonical_audited`。不提供该参数时
继续使用 `yfinance_research_cache`，能力维持 `prototype`。

训练只使用已经成熟的标签，推理会覆盖测试区间内所有合格特征候选。若某个实际选中的
股票缺少未来退出价格或清算实现值，该周期保留为 `unavailable_missing_realization`，不会
用排名下一位替补。只有数据审计通过且所有已选周期实现值完整，报告中的
`historical_evaluation_status` 才是 `historical_validated`；否则为 `quality_failed/prototype`。

canonical bars 模式拒绝旧 `--fundamentals-file`，并要求宏观输入使用
`--macro-vintages-file`，否则必须显式 `--disable-fred`。普通 FRED 查询反映当前修订后的
历史，旧基本面入口没有 `available_at`，两者都不能进入声称 point-in-time 的特征集。

macro vintage 使用 long format：`series_id,feature_name,observation_at,published_at,available_at,
revision_id,value,unit,source,source_kind`；基本面使用 stable instrument ID、相同时间/版本/
来源字段及一个或多个 `fundamental_*` 数值列。当前宏观单位冻结为：利率和利差使用
`decimal_rate`，VIX 使用 `index_points`。每个决策时点只使用已经可见的最新观察期；较晚
发布的旧期修订不会覆盖更新观察期。bars、成员、宏观和基本面共享决策时点。

行业/市值文件格式为 `instrument_id,observation_at,published_at,available_at,revision_id,sector,
market_cap,source,source_kind`。`market_cap` 必须为正有限值，分类按 stable instrument ID 和
决策时点解析最新可见观察期。解析后生成行业内 20 日收益排名、相对行业平均的 20 日收益
和全市场市值排名；分类未发布前对应股票不会进入这些特征的训练/推理样本。

## 5. 导出并查看 Dashboard

把 Stage 1 运行转换为 Dashboard v2 静态数据：

```bash
python apps/quant-dashboard/scripts/export_research_v2.py \
  --run-dir "$RUN_DIR" \
  --output apps/quant-dashboard/public/data/research-v2.json
```

启动前端：

```bash
cd apps/quant-dashboard
npm run dev
```

浏览器打开 `http://localhost:3000`。研究报告接口为
`http://localhost:3000/api/v2/research`。

`/api/v2/signals/latest` 只读取由前向证据仓重新验证后导出的当前信号：

```bash
python -m apps.quant_research.manage_forward_archive export-current \
  --archive-root .artifacts/forward_signals \
  --as-of <timezone-aware-current-time> \
  --strategy-id qqq-long-only \
  --output apps/quant-dashboard/public/data/signal-current.json
```

接口会再次验证导出文件的内容 hash，并在请求时检查 `decision_time <= now < valid_until`；
支持 `strategy_id` 和 `horizon` 查询参数，但不会缩放其他周期的结果。未发布文件时返回
`CURRENT_SIGNAL_NOT_PUBLISHED`，文件过期时返回 `CURRENT_SIGNAL_STALE`，绝不会回退历史
名单。生产部署也可用固定环境变量 `QLIB_CURRENT_SIGNAL_PATH` 指向发布文件。

如需查看旧 Nasdaq 研究 Dashboard：

```bash
cd /mnt/c/quant/project/qlib
python apps/quant-dashboard/scripts/export_dashboard_data.py \
  --artifacts .artifacts/nasdaq100_medium_low_frequency \
  --output apps/quant-dashboard/public/data/dashboard.json \
  --annual-risk-free-rate 0.04
```

旧产物使用当前成分股快照，存在幸存者偏差，只应作为 legacy research baseline。

## 6. 运行 ETF 风险、对冲与期权覆盖情景研究

`run_research` 现在统一比较持有、现金减仓、波动率目标、趋势+波动率动态现金对冲、固定与
动态 Protective Put、Bear Put Spread、Covered Call 和 Collar。Covered Call 只覆盖已有整手股票，不允许裸卖 Call；
Collar 同时买入价外 Put、卖出价外 Call。为保证可复现，建议总是提供固定 CSV：

```bash
python -m apps.quant_research.run_research \
  --prices-csv examples/data/qqq_spy_stage1_demo.csv.example \
  --output-dir .artifacts/hedge_research_demo \
  --initial-cash 100000 \
  --reduced-exposure 0.70 \
  --target-volatility 0.12 \
  --put-coverage 1.0 --put-moneyness 0.95 \
  --put-spread-width 0.10 \
  --call-coverage 1.0 --call-moneyness 1.05 \
  --option-dte 63 --option-roll-dte 21 \
  --put-volatility-skew 1.50 --call-volatility-skew 0.50 \
  --dynamic-put-volatility-trigger 0.20 \
  --dynamic-trend-window 50 \
  --dynamic-risk-off-exposure 0.35 \
  --dynamic-rebalance-threshold 0.05
```

输出包括 `result.json`、`equity_curves.csv`、`option_trades.csv` 和 `prices.csv`。
这里的 Put/Call 使用仅依赖过去信息的 Black-Scholes 情景价格，并按冻结的价外程度斜率
调整情景隐含波动率，结果标记为
`black_scholes_scenario_not_historical_quotes`，不能声称是历史可成交回测。

`dynamic_trend_volatility_hedge` 只在上一交易日价格、移动趋势和已实现波动率基础上调整
股票/现金比例，并使用再平衡阈值抑制换手；它不是卖空 ETF、期货或反向 ETF 回测。
`dynamic_protective_put_model` 只有在上一交易日跌破滞后趋势或滞后实现波动率超过阈值时
持有 Put，信号关闭时按情景 bid 平仓，并继续计入价差、佣金和展期成本。
`bear_put_spread_model` 同时按 ask 买入较高执行价 Put、按 bid 卖出较低执行价 Put；平仓时
反向使用 bid/ask，并逐腿计佣金。它降低保护成本，也把下跌保护限制在执行价宽度内。
`covered_call_model` 的上涨收益会被执行价附近封顶且仍承担大部分下跌风险；
`collar_model` 用 Call 权利金补贴 Put，但同时牺牲部分上涨空间。三者都只是情景比较，
不是期权业绩证明或账户建议。

如果已经用 `build_risk_snapshot()` 得到 SPY 公共因子与 QQQ 残差因子的账户美元敞口，
可生成只减仓、不融资、不卖空的整数股现金对冲研究计划：

```python
from apps.quant_research.risk import plan_cash_etf_hedge

plan = plan_cash_etf_hedge(
    risk_snapshot,
    spy_price=500,
    qqq_price=500,
    current_spy_shares=100,
    current_qqq_shares=100,
    target_spy_factor_dollars=55_000,
    target_qqq_residual_dollars=25_000,
    deadband_dollars=500,
)
```

计划先求 QQQ 残差调整，再扣除该交易对 SPY 公共因子的联动影响，最后求 SPY 调整，因此
不会把 QQQ 的 SPY Beta 重复对冲。Put/Call Delta 已经进入 `RiskSnapshot` 时也不会再次另加。
输出是内容寻址的 `cash_hedge_plan.v1/research_plan_only`；目标需要加仓、卖空或超过当前持仓
时返回 `partially_constrained` 和原因码，不会悄悄生成融资或裸空订单。它仍需经过统一账户、
限价和人工订单包检查后才可能执行。

需要比较其他对冲载体时，使用 `compare_hedge_instruments()` 输入对齐后的组合、基准和工具
日收益，并为每个工具提供 `HedgeInstrumentSpec`。结果比较年化跟踪误差、日均基差、开仓
成本、费用率、初始/维持保证金及对冲后风险指标。无借券能力时做空普通 QQQ/ETF 返回
`BORROW_REQUIRED_BUT_UNAVAILABLE`；反向 ETF 和期货保持独立资本约束。该 v1 采用“每日
重置名义敞口、只计开仓成本与费用率”的诊断口径，不包含真实换月、再平衡滑点、融资或
追保路径，因此 `execution_capability=none`，不能生成订单或作为工具推荐。

不传 `--prices-csv` 时程序会通过 yfinance 下载数据：

```bash
python -m apps.quant_research.run_research \
  --start 2018-01-01 \
  --end 2026-09-01 \
  --output-dir .artifacts/hedge_research
```

联网下载会随供应商修订而变化，正式实验应保存原始输入并使用固定 CSV 重跑。

## 7. 回放冻结的 TradingAgents 信号

仓库中的 JSONL 只是协议 fixture，不是历史 LLM 业绩。离线回放：

```bash
python -m apps.quant_research.run_signal_replay \
  --signals examples/data/research_signals_fixture.jsonl \
  --prices-csv examples/data/qqq_spy_stage1_demo.csv.example \
  --output-dir .artifacts/signal_replay_demo
```

正式信号必须遵守 `docs/integration/research_signal_v1.md`，尤其是
`available_at <= decision_time < valid_until`。输出包括账户曲线、目标权重、订单和指标。

### 7.1 前向信号归档与成熟评估

历史 walk-forward 只能提供样本外回测证据；要积累真实前向记录，先把当前生成的纯
`signal_batch.v1` JSON 在有效窗口内写入不可变证据仓：

```bash
python -m apps.quant_research.manage_forward_archive publish \
  --archive-root .artifacts/forward_signals \
  --signal-file .artifacts/current_signal.json \
  --data-snapshot-id <immutable-feature-snapshot-id> \
  --data-as-of <UTC-time-not-after-decision-time> \
  --data-manifest .artifacts/production_data_audit.json \
  --feature-batch .artifacts/current_feature_batch.json
```

信号必须包含非空有限 `scores`，不能携带 `target/forward_return`；发布时本机 UTC 时间
必须满足 `decision_time <= recorded_at < valid_until`。过期信号不能倒填成前向记录。
数据 manifest 必须是 `production_data_readiness.v1/v2` 或 `data_snapshot.v1`；系统会重算
audit/snapshot ID、检查有效状态，并把 manifest 本身纳入信号目录的文件哈希。
`feature_batch.v1` 冻结本次信号实际使用的 stable instrument ID、特征列和值；它不能包含
标签，且 `data_snapshot_id/decision_time` 必须分别与数据 manifest 和 signal 一致。代码内
使用 `build_latest_feature_batch()` 后，应把同一对象传给
`generate_signal_from_feature_batch()`，再将二者一起发布，避免特征与分数来自不同批次。
持有期结束后，准备以下 `label_batch.v1` JSON：

```json
{
  "schema_version": "label_batch.v1",
  "data_snapshot_id": "matured-label-snapshot-id",
  "rows": [
    {
      "instrument_id": "stable-instrument-id",
      "label_start": "2026-09-15T13:30:00Z",
      "label_end": "2026-09-22T13:30:00Z",
      "label_available_at": "2026-09-22T20:05:00Z",
      "value": 0.0123
    }
  ]
}
```

只在 `label_available_at <= evaluated_at` 后追加评估：

```bash
python -m apps.quant_research.manage_forward_archive evaluate \
  --archive-root .artifacts/forward_signals \
  --signal-id <signal-id> \
  --labels-file .artifacts/matured_labels.json

python -m apps.quant_research.manage_forward_archive inspect \
  --archive-root .artifacts/forward_signals
```

部分成熟标签生成 `partial` 版本，全部成熟后追加 `complete` 版本，历史版本不覆盖。
本地 archive 的 SHA-256 与 manifest 可检查篡改，但本机时钟不是独立可信时间源；因此
`evidence_scope` 始终声明为 `local_content_addressed_not_third_party_timestamped`。

## 8. 历史期权报价回放

生产回放使用分离的合约主表与报价表。核心列如下：

```text
option_contracts: contract_id,underlying_id,listed_at,expiration,last_trade_at,
                  strike,option_type,multiplier,exercise_style,settlement_type,...
option_quotes:    contract_id,quote_ts,available_at,bid,ask,underlying_price,...
```

时间列必须可解析为带时区时间。系统拒绝负报价、
`ask < bid`、未来才可见或超过允许时效的报价。Python 入口如下：

```python
import pandas as pd

from apps.quant_research.option_backtest import run_rolling_protective_put
from apps.quant_research.option_quotes import load_option_market_data

prices = (
    pd.read_csv("path/to/qqq_prices.csv", parse_dates=["date"])
    .set_index("date")["QQQ"]
)
quotes = load_option_market_data(
    "path/to/option_contracts.csv",
    "path/to/option_quotes.csv",
)
report = run_rolling_protective_put(
    underlying_prices=prices,
    quotes=quotes,
    underlying="QQQ",
    protected_shares=100,
    initial_cash=100_000,
    premium_budget_per_roll=3_000,
    roll_before_dte=5,
    target_dte=60,
    target_moneyness=0.95,
    min_quote_coverage=0.80,
)
print(report["status"], report["quote_quality"])
```

只有 `quote_mode=historical_bid_ask`、
`evidence_capability=historical_option_quote_replay` 且 `status=complete`、报价覆盖质量通过
的报告，才可
称为历史报价回放。`quality_failed`、`infeasible` 或 `lifecycle_failed` 必须保留并解释，
不能删掉缺报价日期后重新计算收益。

历史 Covered Call 使用同一时点过滤规则，但报价表必须显式包含 `option_type=call`，不能
从合约代码字符串猜 Call/Put。首版固定持有到期，不假设提前指派：

```python
from apps.quant_research.option_backtest import run_fixed_covered_call
from apps.quant_research.option_quotes import load_option_market_data

report = run_fixed_covered_call(
    underlying_prices=prices,
    quotes=load_option_market_data(
        "path/to/option_contracts.csv",
        "path/to/option_quotes.csv",
    ),
    underlying="QQQ",
    owned_shares=100,
    initial_cash=5_000,
    target_dte=45,
    target_moneyness=1.05,
    min_quote_coverage=0.80,
    assignment_events=None,  # 或 load_option_lifecycle_events("option_lifecycle_events.csv")
)
print(report["status"], report["quote_quality"])
```

选择器只使用当时可见的 Call，以 bid 计算开仓权利金；持仓期间按有效 bid/ask 中间价记录
空头负债，到期价内按执行价交付被覆盖股票。价格历史未覆盖到期返回 `open_position`，报价
覆盖不足返回 `quality_failed`。可选 `assignment_events` 使用
`option_lifecycle_events_demo.csv.example` 所示 canonical 格式，只有事件的 `available_at`
已到达时才记入提前指派；支持部分指派并拒绝累计超过未平仓张数。系统不会根据价内程度、
除息日或启发式规则虚构提前指派。未提供事件数据时报告明确标记
`NO_EARLY_ASSIGNMENT_DATA`，不能把结果视为完全复刻券商账户。

`load_option_market_data()` 用 `contract_id` 做 many-to-one join，并复验上市、最后交易、
到期、报价可用时间、Call/Put、行权/结算类型和数值范围。只有合约及报价两侧都为
`source_kind=historical_observed`，且 source 名称不含 demo/synthetic/fixture/scenario 标记
时，报告才获得历史回放能力；其他来源自动降为 `scenario_only`。

扁平 CSV 同样必须提供 `listed_at/last_trade_at/expiration`、`exercise_style`、
`settlement_type` 和 `deliverable_instrument_id`，缺值、非有限价格或非整数乘数会被拒绝。
可交易链会排除未上市及超过最后交易时点的合约。当前回放只支持交割物等于标的的实物
交割合约；现金结算和调整后交割物明确拒绝。直接传入缺少生命周期元数据的 DataFrame
只能得到 `scenario_only`，不能作为历史期权收益证据。

## 9. long-only 选股与模拟盘

long-only 核心入口是 `run_long_only_backtest()`，输入约定：

- `scores`：索引名严格为 `datetime,instrument` 的 MultiIndex Series；
- `open_prices`：交易日 × 股票代码的开盘价 DataFrame；
- `benchmark_open`：与交易日对齐的 QQQ 开盘价 Series；
- 信号在下一交易日开盘执行，模型分数只负责排序；
- 可配置 Top-K、持仓缓冲、单股上限、行业偏离和交易费用。

统一比较入口为 `apps.quant_research.experiments.run_baseline_comparison()`。v2 让 Nasdaq
股票池等权、Top-K 动量和 Top-K 模型使用共同信号日期、下一开盘执行及评估窗口；等权
基线覆盖整个可用股票池，并保留 1% 费用缓冲，不再是任意挑选前 K 个代码。

默认同时重跑 0/10/20/30 bps 成本情景，输出 `cost_sensitivity`；主指标按窗口记录 QQQ
涨/跌/横盘状态。`model_evidence` 要求至少 3 个不重叠窗口，并检查模型在多数窗口能否取得
正超额、战胜两个简单基线，以及在最高成本下保持正超额。未满足时状态为
`insufficient_evidence/not_robust` 并给出原因码；即使为 `candidate` 也明确不等于 alpha 证明
或实盘就绪。

Nasdaq 主流程的预测诊断同时输出普通 Pearson IC、Rank IC、两者的日频波动、Rank IC
正值比例、方向准确率，以及按每个交易日预测排名划分的五组平均成熟标签和最高组减最低组
差值。`daily_ic.csv` 保存两类逐日相关性，兼容文件 `rank_ic.csv` 继续保留。分组使用同日
排名，不能跨日期把不同时期的分数绝对值混排；这些仍是标签诊断，不等同于可交易组合收益。

启用 walk-forward 时，每个窗口还会在 `walk_forward_windows[].feature_gain_share` 保存归一化
特征 gain。`feature_importance_stability` 汇总窗口间 Spearman 排名相关性、Top-10 Jaccard
重合度，以及每个特征的平均 gain、波动和窗口出现率。少于两个有效窗口会返回
`insufficient_windows`；系统不会根据合成数据或单窗口结果自动删除特征。

市场宽度特征按每个决策日已经通过 PIT 成员过滤的股票计算，包括高于 20 日均线比例、
20 日收益为正比例和 20 日收益横截面离散度，再用仅向后看的滚动统计标准化。非成员不会
进入宽度分母。行业内排名和市值排名仍要求带 `available_at` 的历史分类/市值 vintage；
当前分类不得回填历史。

当前信号到模拟执行的调用顺序是：

```text
generate_latest_signal
  -> plan_signal_orders
  -> PaperSimulator.execute
  -> PaperSimulator.save
  -> inspect_artifacts --simulator-state
```

模拟状态检查：

```bash
python -m apps.quant_research.inspect_artifacts \
  --simulator-state path/to/paper-simulator.json
```

计划状态含义：

| 状态 | 含义及处理 |
| --- | --- |
| `filled` | 已完成；相同 plan ID 重试不会重复成交 |
| `partially_filled` | 部分成交；有效期内可继续执行剩余数量 |
| `infeasible` | 缺价、限价或资金/持仓约束失败；检查原因码后再重试 |
| `needs_revalidation` | 账户发生计划外变化；必须重新核对并生成/确认计划 |
| `expired` | 已过执行窗口；不能继续成交 |

模拟盘状态是本地研究记录，不会向券商发送订单。

### 9.1 券商只读账户快照与对账

外部执行前先通过 `broker_account_snapshot.v2` 适配只读导出。输入包含脱敏账户 ID、
券商适配器 ID、采集/可用时间、五个现金桶、stable instrument ID 持仓，以及原始导出的
SHA-256；不得包含 API key、token 或密码。余额使用 canonical Decimal 字符串，持仓使用
`quantity_decimal/multiplier_decimal`。首版只接受 USD 现金账户的多头股票/ETF。

用仓库合成示例初始化本地模拟状态：

```bash
python -m apps.quant_research.reconcile_broker_account bootstrap \
  --snapshot examples/data/broker_account_snapshot_demo.json.example \
  --simulator-state .artifacts/broker_demo/paper-simulator.json \
  --as-of 2026-09-14T20:10:00Z
```

后续只读对账：

```bash
python -m apps.quant_research.reconcile_broker_account reconcile \
  --snapshot examples/data/broker_account_snapshot_demo.json.example \
  --simulator-state .artifacts/broker_demo/paper-simulator.json \
  --as-of 2026-09-14T20:10:00Z
```

默认拒绝超过 24 小时的快照，也拒绝未来才可见、身份 hash 不符、负持仓、期权、非 USD
或融资账户。bootstrap 只允许创建新状态，不覆盖已有账本；reconcile 匹配退出 0，存在
差异退出 2，并逐项报告 broker/ledger 数值。两个命令均输出 `orders_submitted=0`，不会
访问券商网络或发送订单。`broker_observed` 只升级为
`broker_export_contract_validated`，实际交易能力仍为未评估。

旧 `broker_account_snapshot.v1` 数字字段仍可读取，用于已有不可变证据回放；新适配器应调用
`attach_broker_account_snapshot_id()` 生成 v2 内容 ID，不能把 v1 文件原地改名为 v2。

### 9.2 待人工审核订单包

只有快照对账为 `matched` 后，才允许把当前 `order_plan.v1` 导出为本地订单包：

```bash
python -m apps.quant_research.prepare_broker_orders \
  --plan .artifacts/current_order_plan.json \
  --snapshot .artifacts/current_broker_snapshot.json \
  --simulator-state .artifacts/paper-simulator.json \
  --symbol-map .artifacts/current_instrument_symbols.json \
  --generated-at <timezone-aware-current-time> \
  --output .artifacts/pending_broker_orders.json
```

`symbol-map` 是 stable instrument ID 到当前券商代码的 JSON 对象，必须从当时有效且已审计
的 instrument master 生成；导出后仍需人工复核。系统拒绝过期计划、账户 hash 变化、
快照不一致、缺代码、非整数股、超出现有持仓的卖单，以及按限价超过已结算现金的买单；
不会假设同日卖出未结算款可立即用于买入。

订单包状态恒为 `pending_human_approval`，能力恒为 `manual_export_only`，并保存
`orders_submitted=0`。它只是内容寻址的审核材料，没有网络发送、审批后回调或券商下单
实现；因此不能把文件生成成功报告成真实执行能力。新导出默认为
`broker_order_package.v2`，数量、限价和费用分别使用 `quantity_decimal,
limit_price_decimal,estimated_fee_decimal`；v1 只保留为显式兼容读取路径。

### 9.3 券商订单状态回报

人工审批和人工录单都发生在系统之外。录单后先下载券商的只读订单状态导出，并转换为
`broker_order_status_batch.v1`：

```bash
python -m apps.quant_research.import_broker_order_status \
  --package .artifacts/pending_broker_orders.json \
  --statuses .artifacts/current_broker_order_statuses.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of <timezone-aware-current-time> \
  --expected-content-sha256 <hash-from-inspect>
```

每行必须绑定 `broker_status_id,client_order_id,broker_order_id`，并提供 `status,reason_code,
effective_at,available_at`。首版状态机只接受 `accepted/rejected/cancelled/expired`：拒绝和撤销
必须有原因码；订单一旦终态不能再次打开；同一 client order 的 broker order ID 不能改变。
重复 status ID 与相同内容幂等，ID 内容冲突会阻断。

状态事件只进入不可变事件流，不改变现金或持仓。它不代表本系统提交或撤销了订单，输出
仍固定 `orders_submitted_by_system=0`。后续成交必须在 `accepted` 生效后且终态生效前发生；
因此只有成交文件、没有受理状态文件时会拒绝导入。

### 9.4 券商成交回报导入

人工录单后，只从券商下载成交回报，不向券商写入任何内容。适配器把回报转成
`broker_fill_batch.v2` 后执行：

```bash
python -m apps.quant_research.import_broker_fills \
  --package .artifacts/pending_broker_orders.json \
  --fills .artifacts/current_broker_fills.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of <timezone-aware-current-time> \
  --expected-content-sha256 <hash-from-inspect>
```

每个 fill 必须提供 `broker_execution_id,client_order_id,instrument_id,symbol,side,
quantity_decimal,price_decimal,fee_decimal,executed_at,available_at`。
批次还必须绑定原订单包、账户和券商，记录采集/可用时间及原导出的 SHA-256。适配器应先
把数量、价格和费用规范成无指数、无多余尾零的十进制字符串，再调用
`broker_fills.attach_broker_fill_batch_id()` 生成内容 ID。v1 浮点输入仍能回放，但不会被
重新发布成 v2，也不能提供原始字符串已经丢失的精度。

导入按 `(available_at, executed_at, broker_execution_id)` 排序，先在临时账本验证整批，
成功后才原子保存 simulator state。它拒绝错订单、错标的、错方向、非整数股、买单高于
限价、卖单低于限价、未来可见数据、累计超量和订单包导出后的非本包账户变化。同一个
券商 execution ID 重复出现时只复验内容，不重复入账；报告逐单给出 `unfilled`、
`partially_filled` 或 `filled`，并恒有 `orders_submitted_by_system=0`。

股票买入成交进入 `trade_payable`，卖出成交进入 `trade_receivable`。本命令不会假设 T+1
已经结算，也不会用卖出未结算款购买其他证券。结算应使用下一节的只读券商活动导入；在
此之前，简单拿成交后账本和“已结算后的新快照”相比可能出现预期差异。

如果券商明确撤销了一笔已导入成交，适配器应生成 `broker_fill_correction_batch.v1`，精确
引用原 `broker_execution_id`，并提供唯一 `broker_correction_id`、稳定原因码和双时间轴：

```bash
python -m apps.quant_research.import_broker_fill_corrections \
  --package .artifacts/pending_broker_orders.json \
  --corrections .artifacts/current_broker_fill_corrections.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of <timezone-aware-current-time> \
  --expected-content-sha256 <hash-from-inspect>
```

冲正不会删除或改写原成交，而是追加 `equity_fill_reversal`。它恢复该笔成交的持仓和未结算
应收/应付，使订单数量可以由新的 execution 再次成交。同一 correction ID 幂等；同一
execution 不可被两个 correction 重复冲正。因为当前活动合约尚未逐笔绑定 settlement，
相关现金桶一旦出现后续结算，系统会拒绝自动冲正并要求人工对账/重建，而不会从聚合现金
余额猜测归属。固定合成输入见 `broker_fill_correction_batch_demo.json.example`；它是正常
“成交→结算”演示链的替代分支，冲正后不要继续导入原成交的结算活动。

仓库固定合成链可离线验证：

```bash
python -m apps.quant_research.reconcile_broker_account bootstrap \
  --snapshot examples/data/broker_account_snapshot_demo.json.example \
  --simulator-state .artifacts/broker_fill_demo/paper-simulator.json \
  --as-of 2026-09-14T20:10:00Z
python -m apps.quant_research.prepare_broker_orders \
  --plan examples/data/broker_order_plan_demo.json.example \
  --snapshot examples/data/broker_account_snapshot_demo.json.example \
  --simulator-state .artifacts/broker_fill_demo/paper-simulator.json \
  --symbol-map examples/data/broker_symbol_map_demo.json.example \
  --generated-at 2026-09-14T20:10:00Z \
  --output .artifacts/broker_fill_demo/pending-orders.json
python -m apps.quant_research.import_broker_order_status \
  --package .artifacts/broker_fill_demo/pending-orders.json \
  --statuses examples/data/broker_order_status_batch_demo.json.example \
  --simulator-state .artifacts/broker_fill_demo/paper-simulator.json \
  --as-of 2026-09-14T20:12:00Z
python -m apps.quant_research.import_broker_fills \
  --package .artifacts/broker_fill_demo/pending-orders.json \
  --fills examples/data/broker_fill_batch_demo.json.example \
  --simulator-state .artifacts/broker_fill_demo/paper-simulator.json \
  --as-of 2026-09-14T20:20:00Z
python -m apps.quant_research.import_broker_activities \
  --activities examples/data/broker_activity_batch_demo.json.example \
  --simulator-state .artifacts/broker_fill_demo/paper-simulator.json \
  --as-of 2026-09-15T20:20:00Z
python -m apps.quant_research.reconcile_broker_account reconcile \
  --snapshot examples/data/broker_account_snapshot_after_settlement_demo.json.example \
  --simulator-state .artifacts/broker_fill_demo/paper-simulator.json \
  --as-of 2026-09-15T20:20:00Z
```

成交步骤后应得到 QQQ 10→8 股、`trade_receivable=801.0` 和订单 `filled`；结算步骤后应为
QQQ 8 股、`settled_cash=10801.0`、`trade_receivable=0.0`，最终 reconcile 应为 `matched`。
再次运行成交或活动导入只报告 duplicate，不改变账户；结算后的旧成交允许幂等复验，
但禁止追加新的迟到成交。所有示例
均为 synthetic，只验证协议、时点、账本和幂等语义，不是券商连接或真实执行证据。

### 9.5 券商结算与现金活动

`broker_activity_batch.v2` 每行字段为
`broker_activity_id,activity_type,amount_decimal,currency,reference_id,effective_at,available_at`。首版只
接受以下 USD 活动：

| activity_type | 账本变化 |
| --- | --- |
| `trade_receivable_settlement` | 交易应收转入已结算现金 |
| `trade_payable_settlement` | 已结算现金支付交易应付 |
| `dividend_entitlement` | 增加分红应收 |
| `dividend_payment` | 分红应收转入已结算现金 |
| `cash_deposit` | 增加已结算现金 |
| `cash_withdrawal` | 减少已结算现金 |

导入命令：

```bash
python -m apps.quant_research.import_broker_activities \
  --activities .artifacts/current_broker_activities.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of <timezone-aware-current-time> \
  --expected-content-sha256 <hash-from-inspect>
```

每批必须绑定账本初始化时的账户和券商，活动发生时间必须晚于初始账户快照，并满足
`effective_at <= row.available_at <= captured_at <= batch.available_at <= as_of`。数量必须有限
且为正，出金的负号由适配器内部统一生成。活动先按经济生效时间排序；同时间活动再按入金、
分红应计、结算/到账、出金的顺序处理。整批先在临时账本重放；任何超额结算、超额分红
支付或超现金出金都会使整批无写入失败。

适配器使用 `broker_activities.attach_broker_activity_batch_id()` 对规范化并排序后的活动生成
内容 ID。相同 broker activity ID 与相同内容可跨重复导出幂等复验；同 ID 内容变化会阻断。
只有初始账户快照和活动来源均为 `broker_observed` 时，输出能力才是
`broker_activity_contract_validated`；synthetic 或混合证据保持 `simulation_only`。所有输出
恒有 `orders_submitted_by_system=0`，未知的利息、税费、冲正、融资或多币种活动不会被猜测
映射，需先扩展并验证契约。

### 9.6 账户状态并发、完整性与恢复

当前账户文件为 `paper_simulator.v3`。每次成功写入都会增加 `state_revision`，保存本版
`content_sha256`、上一版 hash 和事件链头；加载时会同时重算内容 hash 与完整事件链。文件
内容或历史事件被手工修改后会明确失败，不会继续交易。

所有账户写命令都通过同一个跨进程事务入口，在同目录使用
`.paper-simulator.json.lock` 做 POSIX advisory lock，覆盖完整的“读取、验证、更新、落盘”
过程。落盘仍使用同目录临时文件、`fsync` 和原子替换；事务中任一校验失败时，线上状态
保持原样。该实现面向当前 Linux/WSL 运行环境。

先读取当前状态 hash：

```bash
python -m apps.quant_research.inspect_artifacts \
  --simulator-state .artifacts/paper-simulator.json
```

成交和活动导入可把输出中的 `content_sha256` 传给
`--expected-content-sha256`。若人工复核后账户已被其他进程推进，命令会以 CAS 冲突失败，
避免把旧审核结果写到新状态。该参数可省略；跨进程锁和内部版本校验仍始终启用。

覆盖已有 v3 状态前，系统把前一版原文按内容 hash 保存到
`.paper-simulator.json.history/<content_sha256>.json`。v1/v2 状态仍可只读加载，并在下一次
成功事务中升级到 v3。历史目录用于审计和人工恢复依据，不会被系统自动回滚。恢复时应
先停止全部写入进程，保留损坏文件，单独检查候选历史版本，再用新的状态路径演练和对账；
不要直接编辑 JSON 或在写入进程运行时覆盖线上文件。

可从已验证状态生成脱敏的 Dashboard 账户快照；多个 `--package` 可重复提供：

```bash
python -m apps.quant_research.operations_snapshot \
  --simulator-state .artifacts/paper-simulator.json \
  --generated-at <timezone-aware-current-time> \
  --package .artifacts/pending_broker_orders.json \
  --output apps/quant-dashboard/public/data/account-current.json
```

输出使用 `operations_snapshot.v1`，金额与数量采用 `canonical_string` Decimal 编码，包含状态
revision/hash、现金分桶、持仓、事件计数及经过复验的订单生命周期。原始账户 ID 和
broker order ID 不会输出；后者只保留短 hash 引用。`GET /api/v2/accounts/current` 会在每次
读取时重算内容 hash，缺文件返回 `ACCOUNT_SNAPSHOT_NOT_PUBLISHED`，损坏返回 503。部署时
可用固定环境变量 `QLIB_ACCOUNT_SNAPSHOT_PATH` 指向发布文件；查询参数不能选择任意路径。

## 10. 验证与故障排查

完整验证：

```bash
cd /mnt/c/quant/project/qlib
python -m unittest discover -s apps/quant_research/tests -v
python -c "import tests.test_nasdaq100_medium_low_frequency as t; fs=[getattr(t,n) for n in dir(t) if n.startswith('test_')]; [f() for f in fs]; print(f'Ran {len(fs)} Nasdaq workflow tests: OK')"
python -m unittest discover -s apps/quant-dashboard/scripts -p 'test_*.py' -v
cd apps/quant-dashboard
npm run lint
npm run build
```

同一组检查已固化到 `.github/workflows/quant-research.yml`：Ubuntu Python 作业运行应用、
Nasdaq 工作流和 Dashboard 导出测试及 compileall；独立 Node 20 作业执行 Dashboard typecheck
与生产构建。账户文件锁依赖 POSIX `fcntl`，因此该专用 Python 作业固定在 Ubuntu。

常见问题：

- `env: node: No such file or directory`：先执行 `conda activate qlib-project`；不要只直接调用
  `node_modules/.bin/tsc` 而遗漏环境中的 Node。
- `snapshot manifest hash mismatch`：快照元数据被改动，应恢复原文件或用新输入创建新
  快照，不能编辑不可变快照。
- `published run artifact hash mismatch`：运行产物被改动，应保留证据并重新发布新 run。
- `simulator state content hash mismatch` / `simulator event chain mismatch`：账户文件或事件历史
  被修改；停止导入，保留现场并从已验证历史版本演练恢复。
- `simulator state changed since it was loaded` / `expected version`：另一个进程已经更新账户；
  重新 inspect、重新对账并人工确认后再导入，不能绕过 CAS。
- `CURRENT_SIGNAL_NOT_PUBLISHED`：当前没有有效实时信号，这是正常的不可用状态。
- 期权报告 `quality_failed`：报价覆盖不足；先补数据，不要降低门槛来美化结果。

## 11. 上生产数据前的检查清单

- 使用 point-in-time Nasdaq-100/S&P 500 成分区间和退市标的行情；
- 保存原始/复权价格口径、公司行动版本和数据 `available_at`；
- 使用真实期权 bid/ask、合约主表、乘数、交割方式和完整生命周期；
- 冻结成本、滑点、持仓缓冲、行业约束及测试窗口；
- 将历史研究报告、当前信号、订单计划和账户状态分开；
- 先做模拟盘和账户对账，再考虑人工审核后的外部执行；
- 不把示例数据、情景定价或存在幸存者偏差的结果当成投资建议。

设计细节见 `RESEARCH_SYSTEM_DESIGN.md`，阶段和验收记录见
`RESEARCH_IMPLEMENTATION_PROGRESS.md`。
