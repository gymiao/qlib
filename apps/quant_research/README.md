# QQQ / S&P 500 对冲、Put 与个股研究核心

本目录实现统一研究账户的第一版核心能力：

- QQQ、SPY 买入持有、现金减仓、波动率目标和保护性 Put 情景回测；
- 股票与期权现金账本、现金担保卖 Put 资金预留及到期结算；
- SPY 与 QQQ 相对 SPY 残差因子的联合风险估计；
- 可接入现有 Qlib 预测分数的 long-only 选股及持仓缓冲。

当前 Put 回测使用仅依赖过去波动率的 Black-Scholes 情景估值，输出明确标记为
`black_scholes_scenario_not_historical_quotes`。它用于比较架构、现金流和风险，不能作为
历史可成交收益证据。接入历史 bid/ask 后，主回测应由真实报价适配器替换该估值路径。

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
