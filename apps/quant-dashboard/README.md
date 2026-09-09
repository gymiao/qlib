# Qlib Quant Dashboard

独立的 Next.js 前端，只消费量化流程导出的 JSON，不导入或调用 Qlib Python 代码。

## 数据与模型更新策略

先从研究产物生成静态数据：

```bash
python scripts/export_dashboard_data.py \
  --artifacts ../../.artifacts/nasdaq100_medium_low_frequency \
  --output public/data/dashboard.json \
  --annual-risk-free-rate 0.04
```

当前 Nasdaq-100 周频策略采用美元中性多空组合：50%做多Top 10、50%做空Bottom 10，
总敞口100%、净敞口0%，并约束净Beta。策略使用美国3个月期国债收益率作为 \(R_f\)
代理，并以QQQ为市场组合计算CAPM年化Alpha、Beta和R²。生产研究应提供历史利率：

```bash
python scripts/export_dashboard_data.py \
  --artifacts ../../.artifacts/nasdaq100_medium_low_frequency \
  --output public/data/dashboard.json \
  --risk-free-csv path/to/us_3m_treasury.csv
```

历史 CSV 包含 `date,annual_rate` 两列，利率使用小数（例如 `0.0435`）。程序对每个调仓日
采用不晚于该日的最近一期观测。未提供历史文件时，`--annual-risk-free-rate` 只作为明确
标注的固定利率回退假设，不能视为真实历史利率。

因此，训练环境和前端部署环境可以完全分离。只要其他策略输出相同 JSON 结构，也可以直接复用界面。

`public/data/models.json` 是模型清单，页面根据它生成模型选择器。每个可用模型指向一个独立的
dashboard JSON；新增模型时：

1. 使用导出脚本生成该模型的数据文件，例如 `public/data/dynamic-hedging.json`。
2. 在 `public/data/models.json` 中新增或更新模型条目，并将状态设为 `active` 或 `research`。
3. 运行验证并重新部署前端。

状态为 `planned` 的模型会显示在选择器中但不可选择。当前的
`public/data/dashboard.json` 继续作为默认横截面模型数据，保持向后兼容。

## 本地运行

```bash
npm install
npm run dev
```

打开 `http://localhost:3000`。

## 验证

```bash
(cd scripts && python -m unittest test_export_dashboard_data.py)
npm run lint
npm run build
```
