# Session Notes

- 工作仓库：`/mnt/c/quant/project/qlib`（Qlib 项目）。
- 沟通语言：优先使用中文。
- 用户希望将会话中的重要决定、已完成工作、验证结果和后续事项持续记录在本文件中。
- 2026-07-22 的任务是创建独立环境、运行 Qlib，并对苹果（AAPL）股价做一次可复现预测。
- 已创建独立 Conda 环境 `qlib-aapl`（Python 3.11），位置为 `/home/mgy/miniconda3/envs/qlib-aapl`。
- 已从当前源码安装并验证 Qlib `0.9.8.dev31`，并安装 LightGBM `4.7.0`、yfinance 和 scikit-learn。
- 新增 `examples/aapl_prediction.py`：下载 AAPL 日线数据，构造技术特征，使用 Qlib `LGBModel` 训练，并预测下一交易日复权收盘价。
- 2026-07-22 实际运行结果：最后复权收盘价 `$324.29`，下一交易日点预测 `$324.75`，经验 90% 区间 `$317.78–$334.18`。
- 最近 252 个交易日留出集表现较弱：方向准确率 `52.78%`、收益相关性 `-0.0337`，MAE 与零收益基线几乎相同，因此结果不构成可靠交易信号。
- 运行产物位于 `.artifacts/aapl_prediction/`，主要结果文件为 `prediction.json`。

## 项目架构要点

- Qlib 是完整量化研究平台，不只是预测模型库；主链路是：行情数据 → 特征处理 → 数据集切分 → 模型预测 → 策略生成订单 → 执行/交易所模拟 → 账户与报告评估。
- `qlib.config` 与 `qlib.init()` 统一管理数据目录、市场规则、缓存、日志和实验记录，避免训练、策略和回测采用不一致的市场上下文。
- `qlib.data` 提供二进制列式存储、表达式引擎、Provider 和缓存；目标是在大量股票、长时间跨度和重复实验下高效复用数据与滚动特征。
- 数据采集脚本与内部数据接口分离：外部数据源可以替换，只要最终转换成 Qlib 数据格式，上层模型和策略就不需要修改。
- `DataHandler` 负责通用特征加工、标签和预处理状态，`Dataset` 负责训练/验证/测试切分及模型相关取数；分离设计有利于复用、序列化和减少数据泄漏。
- `qlib.model` 定义稳定模型接口，`qlib.contrib.model` 放具体或实验性算法；上层统一调用 `fit(dataset)` 和 `predict(dataset, segment)`。
- 模型通常预测收益率、风险或横截面排序分数，而非直接预测绝对价格，原因是收益序列尺度更统一，也更容易映射为组合决策。
- `qlib.workflow` 和全局记录器 `R` 使用 MLflow 管理参数、指标、模型、预测与回测产物；`qrun` 通过 YAML 和依赖注入实现配置驱动的可复现实验。
- `Strategy` 负责把模型信号转换为持仓或订单；`Exchange`、`Executor`、`Account` 分别负责市场约束、订单执行以及现金/持仓/费用核算，因此模型效果与交易策略效果可以独立研究。
- Strategy 与 Executor 支持嵌套，例如日频组合决策下嵌分钟级拆单执行；生成器式回测循环也能被强化学习环境复用。
- 目录定位：`qlib/` 是核心框架，`qlib/contrib/` 是具体模型和策略，`examples/` 是端到端示例，`scripts/` 是采集、转换和运维工具。

## 本次 AAPL 实现与标准流程的差异

- 当前 `examples/aapl_prediction.py` 是单股票轻量实现：Yahoo Finance → Pandas 技术特征 → `FrameDataset` 适配器 → Qlib `LGBModel` → MLflow。
- 它使用了 Qlib 模型与实验记录体系，但没有执行完整的 Yahoo 采集器 → Qlib 二进制数据 → `DataHandler` → `DatasetH` 流程。
- 轻量流程适合快速验证单只股票；正式多股票横截面研究更适合使用 Qlib 标准数据格式、`Alpha158/Alpha360`、`DatasetH`、策略和完整回测。
