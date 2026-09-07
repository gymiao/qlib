# TradingAgents 与 Qlib 融合工作记录

分支：`feature/tradingagents-qlib-integration`（Qlib）与 `feature/qlib-research-signal-export`（TradingAgents）。

## 本次完成

- 建立 `research_signal.v1` JSONL 协议和文档；记录信号 ID、运行 ID、标的、基准、分析/生成/可用/失效时间、评级、状态、模式、摘要和证据引用。
- TradingAgents 新增 `build_research_signal` 和 `write_research_signal` 独立适配器。导出记录要求完整时区和明确的真实可用时间；调用方在保存完整图状态后调用它，避免将现有核心图文件的行尾差异纳入融合提交。
- Qlib 新增 JSONL 导入、协议验证、可用时间和过期判断、重复 ID 检查、long-only 研究覆盖层及确定性组合回放。
- 覆盖层映射冻结为 Buy/Overweight 100%、Hold 70%、Underweight 35%、Sell 0%；减少仓位留在现金，不再分配给其他标的。
- 修正研究账户和 ETF/Put 原型中的两类问题：股票和长权利金交易不能占用现金担保 Put 预留；保护性 Put 的初始波动率不再读取后续价格，滚动平仓按当前时点重新估值。波动率目标策略在买入成本后不允许负现金。

## 自动测试

Qlib：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m unittest discover \
  -s apps/quant_research/tests -v
```

结果：13 个测试通过。覆盖账户预留、Put 到期、Delta 暴露、无未来期权估值、联合因子、long-only 缓冲、信号协议、未来信号不可见、覆盖层现金保留和冻结信号回放。

TradingAgents：

```bash
/home/mgy/miniconda3/envs/tradingagents/bin/python -m pytest \
  tests/test_qlib_research_signal.py -q
```

结果：2 个测试通过。覆盖导出字段/时区/有效期和重复信号 ID 拒绝。

## 回测

运行日期：2026-09-07。数据源为 yfinance 下载的 QQQ/SPY 日线，区间 2020-01-02 至 2024-12-31，实际 1,258 个交易日。

### 修正后的 ETF 与 Put 基线

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_research \
  --start 2020-01-01 --end 2025-01-01 \
  --output-dir .artifacts/hedge_research_v2
```

结果：QQQ 买入持有累计收益 136.46%，保护性 Put 情景累计收益 69.68%；SPY 买入持有累计收益 80.37%，保护性 Put 情景累计收益 50.99%。期权结果仍为 Black-Scholes 情景估值，不能视作真实历史成交回测。

### 冻结信号融合回放

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_signal_replay \
  --signals examples/data/research_signals_fixture.jsonl \
  --start 2020-01-01 --end 2025-01-01 \
  --output-dir .artifacts/tradingagents_signal_replay
```

结果：70% QQQ / 30% SPY 基础组合累计收益 119.63%；研究覆盖层累计收益 122.08%。报告的 `signal_mode` 固定为 `frozen_signal_replay_not_historical_llm_performance`。这些 fixture 不是当时由 LLM 生成的归档信号，结果仅验证端到端导入、时间过滤、交易成本、现金和回放是否正确，不能用于比较 TradingAgents 的真实历史预测能力。

## 产物

- `.artifacts/hedge_research_v2/result.json`
- `.artifacts/tradingagents_signal_replay/result.json`
- CSV 净值、订单和目标仓位与 JSON 放在相同目录；CSV 按现有忽略规则不提交 Git。

## 尚未完成

- TradingAgents 候选批量任务、费用/超时/缓存管理和前瞻定期归档。
- point-in-time 股票池、退市行情、真实财报/新闻快照及历史期权 bid/ask。
- Dashboard 融合工作区和真实研究信号的长期模拟运行。

当前实现是可复现的离线融合基础。真实效果验证需要先积累 `research_mode=forward` 的按时归档信号，并在冻结规则下开展前瞻模拟。
