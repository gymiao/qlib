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

## 后续研究决定

- 2026-07-26：后续多股票联合建模与选股回测采用 Nasdaq-100 作为股票池。
- 研究原型可先使用当前成分股快速验证；正式历史回测必须使用各历史时点当时的成分股名单，避免幸存者偏差。
- 计划目标为预测未来多日相对收益/股票排名，再通过组合构建与含交易成本的滚动回测评估，而不是分别预测每只股票的绝对收盘价。
- 新增 `TODO.md` 管理执行清单，新增 `SUMMARY.md` 汇总项目状态；本文件继续记录会话上下文、重要决定和验证结果。

## 教学安排

- 2026-08-03：用户确认按“量化与机器学习基础 → Qlib 单股票流程 → Nasdaq-100 横截面建模与回测 → TradingAgents”的顺序学习。
- 教学面向初学者，以理解概念、观察代码、动手练习和结果复盘的方式逐课推进，不以项目代码已经实现等同于用户已经掌握。
- 学习进度单独记录在 `LEARNING_PROGRESS.md`；本文件继续保存重要决定和会话上下文。
- 当前进入第一阶段第一课：价格、复权价格与收益率。
- 2026-08-03：第 1 课快速通过；用户希望简单内容加快进度，后续采用更综合的判断题和实验案例。
- 2026-08-06：完成全部四阶段入门路线：量化与机器学习基础、Qlib 单股票流程、Nasdaq-100 横截面研究、TradingAgents 适用边界。后续转入真实项目实践，并继续在 `LEARNING_PROGRESS.md` 记录复盘。

## 2026-09-06 项目复盘与 QQQ 研究方向

- 当前项目已从 AAPL 单股票原型发展为 Nasdaq-100 横截面研究流程，主程序为 `examples/nasdaq100_medium_low_frequency.py`，并配有独立 Next.js Dashboard。
- 当前默认实验使用 101 只证券、37 个量价/风险/市场/宏观特征、未来 5 日 Beta 调整收益标签、年度扩展窗口 walk-forward，以及 Top 10 / Bottom 10 美元中性多空组合；每周调仓、单边成本 10 bps、净 Beta 上限为 ±0.10。
- 最新正式产物生成于 2026-07-28，测试区间为 2024-07 至 2026-07：平均 Rank IC 0.0296、Rank IC IR 0.205、方向准确率 49.59%；策略累计收益 5.94%、年化收益 2.92%、夏普 0.34、最大回撤 -8.31%，同期 QQQ 累计收益 43.76%、年化收益 19.86%、夏普 1.05。
- 当前结论：工程闭环已经跑通，但 Alpha 较弱，现有信号不能作为实时买卖依据；低波动和低回撤主要来自美元中性及 Beta 约束。
- 当前最大研究缺陷是使用 2026-05-01 成分股快照回测历史，`point_in_time=false`，存在幸存者偏差。正式研究必须建立 `symbol,start_date,end_date` 历史成员区间，并按实际生效日处理年度重构、季度再平衡和临时替换。
- `base17_result.json`、`lambdarank_result.json` 仍属于旧的偏多头回测口径，不能直接与当前美元中性结果比较；后续消融实验必须统一股票池、执行时点、成本、风险约束和组合模式。
- 用户希望研究 QQQ 成分股的具体操作。推荐主线改为“以 QQQ 为基准的多头选股”：预测未来 5 日相对 QQQ 的超额收益，每周收盘后生成排名、下一交易日开盘执行，持有 Top 10，并加入换仓缓冲、单股上限、行业偏离和多档交易成本压力测试。
- 当前美元中性 Top 10 / Bottom 10 策略保留为纯 Alpha 对照，不作为第一套实际操作策略；第一版不使用融资、卖空或期权，并先进行模拟交易。
- 建议比较的基准包括 QQQ 买入持有、Nasdaq-100 等权、简单 20 日动量 Top 10、LightGBM Top 10 和当前美元中性模型；主要观察扣费后超额收益、信息比率、最大回撤、跟踪误差、换手率、Rank IC 及跨年份稳定性。
- 下一步优先级：补齐历史时点成分股 → 增加 `long_only` 组合模式及相对 QQQ 标签 → 同口径重跑 17/37 特征与 MSE/LambdaRank → 检查跨窗口稳定性 → 更新数据并设置新的封存测试区间 → 最后再考虑动态对冲和 Protective Put。
- 环境现状与旧文档有漂移：`qlib-aapl` 环境已不存在，当前可用环境为 `/home/mgy/miniconda3/envs/qlib`，其中 Qlib 为 0.9.8.dev31、LightGBM 为 4.7.0；该环境缺少 pytest。
- 验证结果：4 个 Nasdaq 核心测试函数直接调用通过，Dashboard 数据导出 unittest 通过，TypeScript 检查及 Next.js 生产构建通过。
- Git 工作树显示大量修改，其中绝大多数是 CRLF/LF 行尾差异；忽略行尾后只有 9 个 tracked 文件存在实质内容变化，Nasdaq 主流程、测试、部分文档和 API 等关键成果仍为未跟踪文件，后续需谨慎整理并纳入版本控制。

## 2026-09-15 统一研究系统优化收口

- 创建独立项目环境 `/home/mgy/miniconda3/envs/qlib-project`，从既有 `qlib` 环境使用
  `conda --no-plugins create --clone ... --offline` 完成本地克隆；原环境未修改。环境包含
  Python 3.11.15、Node.js 22.23.1 和 npm 10.9.8，Qlib 正确指向当前仓库源码。
- 使用新环境完成烟雾验证：NumPy、Pandas、LightGBM、CVXPY 与 Qlib 均可导入，
  `apps/quant_research` 的 165 项 unittest 全部通过。
- 生产数据入口增加 readiness 审计、stable instrument ID、point-in-time 成分区间、canonical
  行情和 vintage 特征；不满足数据门时显式降级，禁止把快照股票池包装为无偏历史回测。
- 前向证据仓冻结数据 manifest、特征批次和信号，标签只能在持有期结束且可用后追加；当前
  信号导出会重新验 hash 和有效期，不回退到历史信号。
- 账户状态升级为 `paper_simulator.v3`：revision、内容 hash、事件链、跨进程锁、CAS、原子
  保存和历史归档共同保护并发及恢复。所有账户读写 CLI 已迁移到同一事务入口。
- 券商集成保持只读/人工边界：账户快照、订单包、订单状态、成交和现金活动均为内容寻址
  合约；订单包是 `manual_export_only`，代码恒报告系统提交订单数为零。
- 新写券商数值合约默认使用 canonical Decimal v2，既有 v1 证据仍可回放；Dashboard 账户
  与信号发布也避免二进制浮点改变跨语言 hash。
- 策略比较升级为 `baseline_comparison.v2`：Nasdaq 等权覆盖完整股票池，和动量/模型共享
  下一开盘执行口径，并统一检查 0/10/20/30 bps、市场状态及至少三个不重叠窗口。
- 当前本地工程不能替代外部证据。下一阶段需要权威 PIT 成分、真实行情/基本面、合格期权
  报价、真实券商导出及自然成熟的前向标签；在此之前保持 research/paper 定位。
- Git 工作树仍包含用户既有的广泛行尾和未跟踪噪声。本轮不替用户清理或提交；后续提交时
  必须只暂存审核过的研究系统路径。
- 同日追加 Covered Call、Collar 和趋势/波动率动态现金对冲。备兑 Call 必须有足额现股，
  卖出现股不能破坏覆盖，价内到期按执行价交割；情景回测使用理论报价并明确不是历史业绩。
- 新增未结算成交 correction/reversal：精确引用原 execution、追加事件、重复幂等、冲正后
  可由新 execution 补成交；若相关聚合现金桶已发生结算则拒绝自动冲正。
- 最终本地验收更新为应用 165 项、Nasdaq 13 项、Dashboard Python 5 项，TypeScript typecheck、
  Next.js production build、compileall、workflow YAML 和限定 diff 检查均通过。
- 后续又增加历史报价 Covered Call、canonical 期权合约/报价 join、来源能力降级，以及
  SPY/QQQ 联合因子现金对冲计划；合成或无来源 bid/ask 不再自动标成历史期权回放。
- Covered Call 新增观察型提前指派事件适配；无事件输入保持明确缺证据状态，不用启发式
  模型冒充历史生命周期。示例位于 `examples/data/option_lifecycle_events_demo.csv.example`。
- Nasdaq 预测输出补齐 Pearson IC、Rank IC 与同日五分组成熟标签诊断，且保留旧
  `rank_ic.csv` 兼容文件。
- walk-forward 窗口现在冻结归一化特征 gain，并输出跨窗口排名相关性与 Top-K 重合度；
  单窗口保持证据不足，不自动删除特征。
- Nasdaq 特征新增仅由当日 PIT 合格成员计算的市场宽度、正收益比例和横截面离散度；
  行业/市值排名继续等待带可用时点的真实分类数据。
- ETF 情景比较升级为 v3，新增滞后趋势/波动率触发的动态 Protective Put，并为 Put/Call
  理论价格加入冻结的波动率斜率；仍严格标记为 Black-Scholes scenario。
- v3 同时加入 Bear Put Spread：长腿 ask、短腿 bid，平仓反向计价并逐腿收取佣金，现共
  九种可比策略。
- 分类 vintage 新增独立生产审计与主流程接入，按可用时点产生行业内收益排名、行业相对
  收益和市值排名；真实供应商分类仍属外部输入。
- 新增普通/反向 ETF 与期货的观察收益对冲诊断，单列跟踪误差、基差、成本和保证金；
  不生成订单，真实换月、再平衡与追保仍等待外部数据。
