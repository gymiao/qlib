# QQQ / S&P 500 对冲、Put 与个股研究核心

本目录实现了 `RESEARCH_SYSTEM_DESIGN.md` 中的统一研究账户。详细完成状态见
`RESEARCH_IMPLEMENTATION_PROGRESS.md`，从环境准备到 Dashboard、历史报价和模拟盘的
完整操作步骤见 `RESEARCH_SYSTEM_USAGE.md`。当前已落地：

- QQQ、SPY 买入持有、现金减仓、波动率目标和保护性 Put 情景回测；
- 股票与期权现金账本、现金担保卖 Put 资金预留及到期结算；
- SPY 与 QQQ 相对 SPY 残差因子的联合风险估计；
- 可接入现有 Qlib 预测分数的 long-only 选股及持仓缓冲。
- 内容寻址的离线价格快照、稳定配置 hash 和运行 manifest；
- Decimal 事件账本，包括交易应收应付、T+1 配置、预留、分红、拆股和幂等回放；
- 不需要未来标签的 Nasdaq 特征生成入口。

当前 Put 回测使用仅依赖过去波动率的 Black-Scholes 情景估值，输出明确标记为
`black_scholes_scenario_not_historical_quotes`。它用于比较架构、现金流和风险，不能作为
历史可成交收益证据。接入历史 bid/ask 后，主回测应由真实报价适配器替换该估值路径。

`option_quotes.py` 和 `option_backtest.py` 另提供带 `quote_ts/available_at` 的历史 bid/ask
回放路径。只有通过字段、时点、连续性和合约生命周期检查的数据才能使用该路径；
Black-Scholes 输出始终保留为 `scenario_only`，不会自动升级为历史成交证据。

## 阶段 1：离线 ETF 账本

输入必须是包含 `date,QQQ,SPY` 的固定 CSV，并明确声明数据可用时刻。命令会先生成
不可变数据快照，再原子发布账本、订单、净值和 manifest：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_stage1 \
  --prices-csv path/to/prices.csv \
  --config apps/quant_research/configs/etf_cash_demo.yaml \
  --as-of 2026-09-09T00:00:00Z
```

同一份数据和配置会得到相同的快照 ID 与运行目录。缺失、非正价格、重复日期和内容
hash 不一致都会拒绝运行。

运行：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_research \
  --start 2018-01-01 \
  --output-dir .artifacts/hedge_research
```

也可提供包含 `date,QQQ,SPY` 的 CSV，以固定输入复现：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_research \
  --prices-csv path/to/prices.csv
```

测试使用标准库 `unittest`，不依赖当前环境中缺少的 pytest：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m unittest discover \
  -s apps/quant_research/tests -v
```

## TradingAgents 信号回放

TradingAgents 通过 `research_signal.v1` JSONL 记录导出已完成的研究意见；Qlib 只在
`available_at <= decision_time < valid_until` 时读取它们。导入协议见
`docs/integration/research_signal_v1.md`。使用冻结信号进行机制回放：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_signal_replay \
  --signals examples/data/research_signals_fixture.jsonl \
  --start 2020-01-01 --end 2025-01-01 \
  --output-dir .artifacts/tradingagents_signal_replay
```

这个 fixture 只验证信号导入、时间校验、仓位覆盖层和账本回放，不是历史 LLM 策略业绩。

## 检查运行产物和模拟盘状态

Stage 1 命令打印不可变运行目录。以下命令会重新校验 manifest 及每个发布文件，并输出
数据快照、配置和源码数量摘要：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.inspect_artifacts \
  --run-dir .artifacts/quant_research_runs/<run-id>
```

模拟盘状态使用同一个入口：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.inspect_artifacts \
  --simulator-state paper-simulator.json
```

模拟结果包括 `filled`、`partially_filled`、`infeasible`、`needs_revalidation` 和
`expired`。缺价、限价或账户约束导致的 `infeasible` 可在有效期内重试；
`needs_revalidation` 表示账户发生了本计划之外的变化。完成及过期的计划 ID 保持幂等。

历史期权报告明确使用 `quote_mode=historical_bid_ask`。滚动报告在报价覆盖不足时返回
`quality_failed`。实物交割价差在价内长 Put 没有可交割股票时返回
`MISSING_DELIVERABLE_UNDERLYING`，不会假设现金结算。
