# QQQ / SPY / Put 研究改造实施记录

## 目标

在独立分支中把 `RESEARCH_SYSTEM_DESIGN.md` 的第一阶段研究能力落地：统一研究账户、QQQ/SPY 风险比较、保护性 Put 情景回测，以及可复用的 long-only 选股和因子暴露计算。所有结果都标记为研究用途，不连接券商、不产生交易指令。

## 分支与环境

- 分支：`work/qqq-sp500-put-research-complete`
- 工作区：`/mnt/c/quant/project/qlib`
- Python：`/home/mgy/miniconda3/envs/qlib/bin/python`
- 说明：系统默认 `python` 不在 PATH，因此测试和回测均使用上述 Qlib 环境解释器。

## 已实现内容

- `apps/quant_research/account.py`：现金、股票、期权仓位和事件账本；支持现金担保卖 Put 预留、物理交割到期结算、Delta 因子美元暴露。
- `apps/quant_research/options.py`：可验证的 Black-Scholes Put 估值与 Delta；到期和零波动率有显式处理。
- `apps/quant_research/risk.py`：SPY 市场因子与 QQQ 相对 SPY 残差因子的联合暴露估计，避免把两个相关指数当作独立 beta。
- `apps/quant_research/stock_selection.py`：等权 long-only 选股，支持已有持仓缓冲和最大权重约束。
- `apps/quant_research/backtest.py`：买入持有、降低现金暴露、波动率目标、保护性 Put 四种可比策略；显式计入现金利息、股票交易成本、期权手续费和买卖价差情景。
- `apps/quant_research/run_research.py`：支持 yfinance 下载或 `date,QQQ,SPY` CSV 固定输入，并导出价格、净值曲线、期权交易和 JSON 报告。

## 测试

执行命令：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m unittest discover \
  -s apps/quant_research/tests -v
```

结果：8 个测试全部通过，覆盖：

- 股票交易净值守恒；
- Long Put 物理交割；
- Cash-secured Put 资金隔离；
- 期权 Delta 因子暴露；
- Black-Scholes Put 价格和 Delta；
- 四策略回测输出及无 NaN 检查；
- SPY/QQQ 联合因子回归；
- long-only 持仓缓冲。

## 回测

执行命令：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.run_research \
  --start 2020-01-01 --end 2025-01-01 \
  --output-dir .artifacts/hedge_research
```

生成文件：

- `.artifacts/hedge_research/prices.csv`
- `.artifacts/hedge_research/equity_curves.csv`
- `.artifacts/hedge_research/option_trades.csv`
- `.artifacts/hedge_research/result.json`

实际样本区间为 2020-01-02 至 2024-12-31，共 1,258 个交易日。初始资金为 100,000，降低暴露策略为 70%，波动率目标为 12%。核心结果如下（累计收益 / 最大回撤 / Sharpe）：

| 标的 | 买入持有 | 现金减仓 | 波动率目标 | 保护性 Put 情景 |
| --- | --- | --- | --- | --- |
| QQQ | 136.51% / -35.62% / 0.80 | 100.40% / -28.02% / 0.83 | 93.63% / -16.38% / 1.10 | 75.92% / -27.10% / 0.84 |
| SPY | 80.40% / -34.10% / 0.67 | 61.14% / -24.05% / 0.72 | 68.98% / -14.81% / 0.93 | 55.75% / -14.55% / 0.91 |

## 解释与限制

当前 Put 结果的 `option_data_mode` 为 `black_scholes_scenario_not_historical_quotes`。波动率来自历史窗口并加上配置的 IV 倍数，买卖价差和手续费为情景参数；这不是历史可成交期权收益，也不替代真实 bid/ask、合约生命周期、提前行权和指派数据。

因此本次实现完成了可复现的第一阶段研究基线，但没有宣称保护性 Put 优于基准，也没有接入实盘或历史期权报价。下一阶段需要接入 point-in-time 成分股、退市行情、历史期权链和真实交易日历后，再进行正式无幸存者偏差的策略结论。
