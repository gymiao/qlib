# Qlib Quant Dashboard

独立的 Next.js 前端，只消费量化流程导出的 JSON，不导入或调用 Qlib Python 代码。

## 数据契约

先从研究产物生成静态数据：

```bash
python scripts/export_dashboard_data.py \
  --artifacts ../../.artifacts/nasdaq100_medium_low_frequency \
  --output public/data/dashboard.json
```

因此，训练环境和前端部署环境可以完全分离。只要其他策略输出相同 JSON 结构，也可以直接复用界面。

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
