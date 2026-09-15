# Project Summary

## 当前统一研究系统（2026-09-15）

仓库当前已经从 AAPL 原型扩展为 QQQ/SPY 多资产研究与 paper execution 系统。当前项目
环境是 `/home/mgy/miniconda3/envs/qlib-project`（Python 3.11.15、Node.js 22.23.1）；它于
2026-09-15 从已验证的 `qlib` 环境离线克隆，原环境未修改，`qlib-aapl` 已不存在。权威状态记录见
`RESEARCH_IMPLEMENTATION_PROGRESS.md`，可执行命令见 `RESEARCH_SYSTEM_USAGE.md`，设计约束见
`RESEARCH_SYSTEM_DESIGN.md`。

本地工程链路现已覆盖：生产数据就绪审计、point-in-time 成分区间、canonical 行情与数据
vintage、不可变前向信号证据、long-only 基线/成本/跨窗口证据门、Covered Call/Collar/
保护性 Put 与动态现金对冲情景、统一事件账本、并发安全的
模拟账户状态、只读券商账户/订单状态/成交/现金活动导入、人工订单包、对账、脱敏运维快照、
Dashboard 当前账户与信号 API，以及 Python/Node CI。外部执行能力仍是
`manual_export_only`，系统不会自行向券商提交订单。

当前不能宣称完成的不是本地代码骨架，而是外部证据：仍需权威历史 Nasdaq-100 成分区间、
真实复权行情和 as-reported 基本面、合格期权 bid/ask 与完整生命周期、真实券商导出，以及
随时间自然成熟的前向标签。没有这些输入时，结果保持 `point_in_time=false`、
`scenario_only`、`simulation_only` 或 `insufficient_evidence/not_robust`，不得解释为实盘能力或
alpha 证明。

## 历史 AAPL/Nasdaq 原型

> 下文保留早期原型快照，仅用于追溯，不代表当前环境或统一系统完成度。

## 项目目标

本项目基于 Microsoft Qlib 构建可复现的机器学习量化研究流程。当前已完成 AAPL 单股票预测原型，下一阶段将升级为 Nasdaq-100 多股票联合建模、横截面选股和完整回测。

## 当前环境

- 仓库：`/mnt/c/quant/project/qlib`
- Conda 环境：`qlib-aapl`
- Python：3.11
- Qlib：`0.9.8.dev31`
- LightGBM：`4.7.0`
- 主要附加依赖：yfinance、scikit-learn

## 已完成的 AAPL 原型

实现文件：`examples/aapl_prediction.py`

当前流程：

```text
Yahoo Finance AAPL 日线
→ Pandas 技术特征
→ 按时间划分训练/验证/测试集
→ Qlib LGBModel
→ 下一交易日收益与复权收盘价预测
→ MLflow 实验记录
```

2026-07-22 的运行结果：

- 最后观测日期：2026-07-22
- 最后复权收盘价：`$324.29`
- 下一交易日预测收盘价：`$324.75`
- 经验 90% 区间：`$317.78–$334.18`
- 测试集：最近 252 个交易日
- 方向准确率：`52.78%`
- 收益相关性：`-0.0337`
- MAE 与零收益基线接近

主要产物位于 `.artifacts/aapl_prediction/`：

- `aapl_daily.csv`：AAPL 日线行情
- `mlflow.db`：实验记录
- `prediction.json`：预测结果和测试指标

现阶段结论：代码和端到端流程已跑通，但模型没有显示出可靠的样本外交易优势，不应直接用于实盘。

## 下一阶段方案

股票池采用 Nasdaq-100。建模方式由“每只股票分别训练”升级为“全部股票联合训练一个共享模型”。

目标流程：

```text
Nasdaq-100 历史成分股与行情
→ 多股票统一特征和标签
→ 共享 LightGBM 模型
→ 预测未来 5 日相对收益/排名
→ 每周选择 Top 5～10
→ 加入交易成本与风险约束
→ 与 QQQ 比较的滚动样本外回测
```

正式回测必须根据每个历史日期使用当时的指数成分股，避免使用当前名单回测历史所造成的幸存者偏差。

## Qlib 在项目中的作用

Qlib 不只是预测模型库，而是覆盖以下环节的量化研究平台：

- 数据与特征处理
- 训练、验证和测试数据集管理
- 统一模型接口
- 实验和产物记录
- 信号到订单的策略转换
- 交易成本与市场约束模拟
- 账户、组合和回测评估

当前 AAPL 原型只使用了 Qlib 模型和实验记录体系；Nasdaq-100 阶段将逐步采用 `DatasetH`、标准数据格式、策略和完整回测能力。

## 文档分工

- `SESSION_NOTES.md`：记录会话上下文、重要决定和验证结果。
- `TODO.md`：维护后续执行清单与完成状态。
- `SUMMARY.md`：提供项目当前状态的独立概览。
