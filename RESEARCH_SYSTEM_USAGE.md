# Qlib 多资产研究系统使用指南

更新时间：2026-09-09

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

在 WSL/bash 中执行：

```bash
cd /mnt/c/quant/project/qlib
source /home/mgy/miniconda3/etc/profile.d/conda.sh
conda activate qlib
python -c "import qlib, pandas, lightgbm; print(qlib.__version__)"
```

本项目当前使用 `/home/mgy/miniconda3/envs/qlib/bin/python`。旧文档中出现的
`qlib-aapl` 环境已经不存在。

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

`/api/v2/signals/latest` 当前默认返回 `CURRENT_SIGNAL_NOT_PUBLISHED`。这是刻意的安全
行为：没有有效的当前信号时，页面不会把历史回测名单伪装成最新持仓建议。

如需查看旧 Nasdaq 研究 Dashboard：

```bash
cd /mnt/c/quant/project/qlib
python apps/quant-dashboard/scripts/export_dashboard_data.py \
  --artifacts .artifacts/nasdaq100_medium_low_frequency \
  --output apps/quant-dashboard/public/data/dashboard.json \
  --annual-risk-free-rate 0.04
```

旧产物使用当前成分股快照，存在幸存者偏差，只应作为 legacy research baseline。

## 6. 运行 ETF 风险与 Put 情景研究

`run_research` 比较持有、现金减仓、波动率目标和 Protective Put 情景。为保证可复现，
建议总是提供固定 CSV：

```bash
python -m apps.quant_research.run_research \
  --prices-csv examples/data/qqq_spy_stage1_demo.csv.example \
  --output-dir .artifacts/hedge_research_demo \
  --initial-cash 100000 \
  --reduced-exposure 0.70 \
  --target-volatility 0.12
```

输出包括 `result.json`、`equity_curves.csv`、`option_trades.csv` 和 `prices.csv`。
这里的 Put 使用仅依赖过去信息的 Black-Scholes 情景价格，结果标记为
`black_scholes_scenario_not_historical_quotes`，不能声称是历史可成交回测。

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

## 8. 历史期权报价回放

真实报价 CSV 至少需要以下列：

```text
contract_id,underlying,quote_ts,available_at,expiration,strike,bid,ask,
underlying_price,multiplier
```

`quote_ts`、`available_at` 和 `expiration` 必须可解析为带时区时间。系统拒绝负报价、
`ask < bid`、未来才可见或超过允许时效的报价。Python 入口如下：

```python
import pandas as pd

from apps.quant_research.option_backtest import run_rolling_protective_put
from apps.quant_research.option_quotes import load_option_quotes

prices = (
    pd.read_csv("path/to/qqq_prices.csv", parse_dates=["date"])
    .set_index("date")["QQQ"]
)
quotes = load_option_quotes("path/to/qqq_option_quotes.csv")
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

只有 `quote_mode=historical_bid_ask` 且 `status=complete`、报价覆盖质量通过的报告，才可
称为历史报价回放。`quality_failed`、`infeasible` 或 `lifecycle_failed` 必须保留并解释，
不能删掉缺报价日期后重新计算收益。

## 9. long-only 选股与模拟盘

long-only 核心入口是 `run_long_only_backtest()`，输入约定：

- `scores`：索引名严格为 `datetime,instrument` 的 MultiIndex Series；
- `open_prices`：交易日 × 股票代码的开盘价 DataFrame；
- `benchmark_open`：与交易日对齐的 QQQ 开盘价 Series；
- 信号在下一交易日开盘执行，模型分数只负责排序；
- 可配置 Top-K、持仓缓冲、单股上限、行业偏离和交易费用。

统一比较入口为 `apps.quant_research.experiments.run_baseline_comparison()`，它让等权、
动量和模型策略使用共同信号日期、执行时点、费用及评估窗口。

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

常见问题：

- `env: node: No such file or directory`：先执行 `conda activate qlib`；不要只直接调用
  `node_modules/.bin/tsc` 而遗漏环境中的 Node。
- `snapshot manifest hash mismatch`：快照元数据被改动，应恢复原文件或用新输入创建新
  快照，不能编辑不可变快照。
- `published run artifact hash mismatch`：运行产物被改动，应保留证据并重新发布新 run。
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
