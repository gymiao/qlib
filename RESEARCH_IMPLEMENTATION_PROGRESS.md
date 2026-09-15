# 研究系统实施与迭代进度

更新时间：2026-09-14

## 总目标

完成 `RESEARCH_SYSTEM_DESIGN.md` 规定的阶段 0 到阶段 4，并在完成基础版本后连续进行三轮优化。每一轮都记录发现的问题、修改、验证结果和仍存在的限制。

## 当前状态

状态：基础版本及三轮优化已完成；生产数据接入和真实账户/券商适配属于外部依赖，尚未启用。

当前工作区已完成 `apps/quant_research` 基础版本：阶段 0–4、编号验收和三轮优化均有代码及测试证据。系统支持事件账本、不可变数据快照、标签与推理解耦、历史期权 bid/ask 回放、Dashboard v2、可恢复的模拟运行和本地不可变前向信号归档。真实历史成分股、退市行情、生产级期权数据和券商账户仍需外部数据或权限；Black-Scholes 路径始终只标记为情景研究，本地归档也不冒充第三方可信时间证明。

使用入口和端到端命令见 `RESEARCH_SYSTEM_USAGE.md`；仓库内的
`examples/data/qqq_spy_stage1_demo.csv.example` 是明确标注的离线合成演示数据。

## 实施清单

| 阶段 | 状态 | 当前证据 | 下一步 |
| --- | --- | --- | --- |
| 0：基础拆分 | 完成 | contracts/config、快照、无标签推理、历史成员半开区间、Dashboard v2；manifest 含冻结配置和实际源码 hash；P01 与 TypeScript 已验证 | 进入优化审查 |
| 1：ETF 账户与风险 | 完成 | 事件账本、结算、预留、分红拆股、ETF 对照、联合风险、原子报告；完整换仓及同名单权重漂移均有精确费用测试 | 进入优化审查 |
| 2a：固定保护性 Put | 完成 | 理论情景和历史 bid/ask 分离；固定及滚动保护、逐日估值、预算拒绝、行权、到期和报价覆盖率质量门已实现 | 进入优化审查 |
| 2b：其他 Put | 完成 | 现金担保卖 Put、部分指派、担保转换及 Bear Put Spread 历史 bid/ask 回放与实物交割失败语义已实现 | 进入优化审查 |
| 3：模型与个股 | 完成 | long-only、历史成员、相对 QQQ 标签、逐日账户回测；等权/动量/模型共享日期、窗口、费用与执行规则并输出跨窗口指标 | 进入优化审查 |
| 4：模拟运行 | 完成 | 幂等成交、临时失败重试、部分成交、状态恢复、监测及 60 时段反复重启验收已完成 | 进入优化审查 |

## 三轮优化

| 版本 | 状态 | 优化目标 | 验证 |
| --- | --- | --- | --- |
| 优化 v1 | 完成 | 正确性、接口和异常路径 | 修复退选持仓漏卖、历史名单字段错配、计划 ID 复用；拒绝模糊时间及非有限数；账户约束使用稳定原因码 |
| 优化 v2 | 完成 | 性能、可复现性、数据质量和实验公平性 | 运行身份绑定依赖和相对源码 hash；快照含覆盖质量；公共信号样本显式记录；发布缓存逐文件复验 |
| 优化 v3 | 完成 | 使用流程、报告可解释性和维护成本 | 新增统一产物/模拟状态检查 CLI，README 记录运行、验证、状态和期权失败语义 |

## 工作记录

### 2026-09-08：现状审计

- 确认设计文档为 v0.2，共包含 15 个最小验收案例。
- 确认 `apps/quant_research` 已存在，但实现早于 v0.2 契约，不能直接视为阶段完成。
- 当前账户直接改变现金和持仓，没有应收、应付、订单预留、稳定事件 ID、幂等回放和快照恢复。
- 当前 Put 回测使用理论价格及人为价差，只能用于情景演示。
- 当前实现尚无统一数据 manifest、缓存身份和 Dashboard v2 schema。
- 下一项工作：运行现有测试建立基线，然后开始阶段 0/1 改造。

### 2026-09-09：阶段 0/1 与期权账本第一批实现

- 新增 `contracts.py`：数据快照、特征、标签、信号和订单计划的版本化契约。
- 新增 `config.py` 与 `configs/etf_cash_demo.yaml`：配置校验及稳定配置 hash。
- 新增 `data.py`：固定 CSV 的内容寻址快照、manifest 和载入时 hash 校验。
- Nasdaq 特征流程新增无标签入口；验证截断未来数据不改变此前特征，末端特征不包含 `target/forward_return`。
- 新增 `ledger.py`：Decimal 事件账本、幂等事件、应收应付、已结算现金、预留、担保、分红、拆股和恢复回放。
- 新增 `portfolio.py`：策略意图净额合并及整数目标持仓计划。
- 新增 `engine.py`：QQQ/SPY 持有和现金减仓的逐日账本对照。
- 新增 `reporting.py` 与 `run_stage1.py`：原子发布、结果 manifest 和离线运行入口。
- Put 成交、覆盖行权和现金担保卖 Put 指派已进入同一事件账本。
- 验证：`apps/quant_research/tests` 共 27 个测试全部通过；Nasdaq 5 个核心函数测试直接调用通过。
- 尚未开始三轮优化；必须先完成设计中的剩余阶段和验收。

### 2026-09-09：第二批实现

- 历史成员资格改为 `[effective_from, effective_to)`，在单证券特征预热完成后、横截面排名前过滤；D03 覆盖边界、非成员不影响排名和新成员预热。
- 新增联合 SPY 与 QQQ 残差因子风险快照；R01 精确验证 Put Delta 只计入一次。
- 新增当前模型信号生成器，只读取最新特征横截面，不要求未来标签。
- 修正旧 Dashboard 收益日期：优先按 `exit_date` 形成净值和年度归属；旧 `/api/prediction` 明确返回历史研究名单。
- 新增 Dashboard v2 研究报告导出及研究报告/当前信号分离接口；当前信号未发布时返回明确不可用状态。
- 新增期权报价适配器，区分 `quote_ts` 与 `available_at`，拒绝 crossed market、过期和未来报价；保护性 Put 按预算只返回整数合约。
- 新增幂等模拟执行器：检查账户版本、执行窗口、价格上限和缺价，先在临时账本验证全部成交，成功后整体提交；重复 `plan_id` 不会重复成交。
- 验证：应用测试 35 个通过，Nasdaq 核心测试 6 个通过，Dashboard Python 测试 4 个通过。
- 离线端到端验证通过：80 日固定 QQQ/SPY CSV生成数据快照、2 组账户曲线、订单、事件、运行 manifest 和 Dashboard v2 JSON，共输出 160 条曲线记录及 4 笔订单。
- 限制：当前环境找不到 `npm`，所以本轮新增 TypeScript 路由尚未完成编译验证；这不会被记为已通过。

### 2026-09-09：第三批实现

- 新增历史 bid/ask 驱动的固定保护性 Put 回放，逐日使用当时已可见的报价估值；无有效报价时保留日期并标记 `unavailable/stale`，不删除不利区间。
- Put 到期改为显式事件；价内覆盖行权形成股票交割应收，价外合约显式注销，避免内在价值重复确认。
- 现金担保 Put 支持部分指派，剩余合约继续保留对应担保；价差空腿指派后保留真实股票风险，再由长腿按独立事件处理。
- Nasdaq 默认研究标签改为未来 5 日相对 QQQ 收益，同时保留横截面中位数和 Beta 调整标签选项。
- long-only 组合加入行业相对基准权重的最大偏离约束，并在约束导致候选不足时明确拒绝。
- 验证：应用测试 40 个通过，Nasdaq 核心测试 7 个通过，Dashboard Python 测试 4 个此前已通过。
- 下一项工作：实现逐日 long-only 账户回测、Put 滚动和模拟状态持久化；之后进行基础版本完成审计。

### 2026-09-09：第四批实现

- 新增逐日 long-only 账户回测：模型分数仅负责选股，下一交易日按实际开盘价成交，持仓以股数漂移，换手和费用来自真实成交金额。
- 卖出应收可与同结算日买入应付配对，但仍在事件账本中分别记录；次日完成应收和应付结算。
- 验证修改未来分数不会改变此前账户历史，防止回测从后续模型结果反向影响已有持仓。
- 新增 `当前 SignalBatch → long-only 权重 → OrderPlan → PaperSimulator` 完整链路；过期信号不能生成计划。
- 模拟器状态支持原子保存和恢复，已完成计划在恢复后重试不会重复成交。
- 验证：应用测试 45 个通过；Python 编译和格式检查将在基础版本审计中统一重跑。
- 基础版本剩余：Put 滚动与价差报告、统一基线实验入口、部分成交、运行监测、源码 hash 和 TypeScript 编译验证。

### 2026-09-09：第五批实现

- 模拟盘新增计划进度状态：临时缺价和限价失败可在有效期内重试，部分成交记录剩余数量、累计成交事件及预期账户 hash。
- 部分成交后若账户只发生本计划产生的变化可继续执行；若发生外部账户变化则转为 `needs_revalidation`。进度可原子保存、恢复，完成后重复请求仍保持幂等。
- 新增模拟运行监测快照，汇总状态计数、待处理计划、账本事件数及账户 basis hash。
- Stage 1 运行 manifest 新增冻结配置、Python 版本和明确任务源码清单的逐文件 SHA-256；run id 同时绑定源码内容，未提交代码不再只靠 commit 追溯。
- 新增历史报价驱动的保护性 Put 滚动：旧腿按 bid 平仓、新腿按 ask 建仓；记录逐次 roll，报价缺失日期保留在净值序列中，并通过最低覆盖率质量门明确失败。
- 新增等权、动量、模型三类 long-only 统一比较入口；各策略共享信号日期交集、下一交易日开盘执行、费用和窗口口径，并形成跨窗口指标表。
- 定向验证：模拟盘/工作流/Stage 0-1 共 27 个测试通过，期权/模拟盘/Stage 0-1 共 29 个测试通过，统一基线与 long-only 共 4 个测试通过。
- 基础版本下一步：补齐价差历史报告、长周期模拟验收、P01 审计和 TypeScript 编译环境；完成后进入三轮优化。

### 2026-09-09：基础版本完成审计

- L04 修正目标权重订单遗漏退选持仓的问题；完整卖旧买新按双边各 10,000 精确计费 20，同名单因权重漂移仍生成实际买卖单。
- 新增 Bear Put Spread 历史 bid/ask 回放；双腿价内时先指派空腿形成真实股票和应付，再行权长腿。只有长腿价内但无可交割股票时返回 `MISSING_DELIVERABLE_UNDERLYING`，不做理想化现金结算。
- 60 个模拟交易时段中每 10 个时段保存、恢复并重复最后计划，最终 60 个计划全部完成且没有重复持仓或成交。
- P01 修正 API 与页面的 `historical_selections` 字段错配；历史研究名单不再显示为“最新模型持仓”。导出器按 objective 标注 `ranking_score`，LambdaRank 分数保持原值且不格式化为收益率。
- 编号验收证据：D01-D04 覆盖因果特征、报价可见时点、历史成员和缓存身份；L01-L05 覆盖结算、公司行动、期权账本、换仓费用和恢复；O01-O03 覆盖行权、指派和不可行生命周期；R01/E01/P01 均有精确或端到端测试。
- 验证：量化研究应用 54 个测试通过；Nasdaq 工作流 7 个函数测试通过；Dashboard Python 5 个测试通过；TypeScript `tsc --noEmit` 通过。
- 基础版本完成，开始优化 v1 正确性、接口和异常路径审查。

### 2026-09-09：优化 v1（正确性与接口）

- 修复目标权重规划遗漏已退选持仓，以及 Dashboard API 使用 `historical_selections` 但页面读取旧字段的问题。
- PaperSimulator 在返回已完成结果前核对完整计划内容，阻止相同 `plan_id` 绑定不同订单；部分成交仍只允许由本计划推进账户 hash。
- 所有执行和计划时间要求显式时区；配置权重、价格、费用、成交比例及限价拒绝 NaN/Inf。
- 账户资金或持仓约束失败改为原子 `infeasible/ACCOUNT_CONSTRAINT_VIOLATION`，不提交前半批成交，也可在计划有效期内重试。
- 数据快照载入同时校验 manifest 自身 hash、目录 snapshot ID 和固定表路径，防止只验证数据文件却接受被修改的元数据。

### 2026-09-09：优化 v2（复现、质量与公平比较）

- `ResearchConfig` 增加显式随机种子；运行身份绑定 NumPy、pandas、PyYAML 版本及实际源码内容。
- 源码清单使用仓库相对路径，避免同一代码仅因工作区绝对路径不同生成不同 run ID；重名的仓库外源码明确拒绝。
- 快照质量报告增加行数、实际首尾日期和最大自然日缺口；非有限价格在快照入口即被拒绝。
- 等权、动量和模型基线先过滤到价格资产集合，再使用公共可评估信号日；报告记录每个窗口的公共样本数和首尾日期。
- 新增已发布运行复验：manifest hash、完成状态、文件路径、大小和 SHA-256 均通过后才允许复用缓存；篡改订单文件的测试会明确失败。

### 2026-09-09：优化 v3（使用流程与可解释性）

- 新增 `inspect_artifacts.py`，一个入口即可验证研究运行或读取模拟盘监测状态，输出适合脚本消费的 JSON 摘要。
- README 增加固定环境的运行、产物复验、模拟状态检查命令，以及五类计划状态的重试和终态语义。
- 文档明确历史期权报价模式、滚动报价质量失败和缺少可交割股票时的生命周期失败，不把这些情况显示成有效收益。
- Dashboard 将事后名单统一称为“历史研究持仓”；LambdaRank 输出声明为排名分数并保留原值，不按百分比展示。
- 最终验证：量化研究应用 62 个测试、Nasdaq 工作流 7 个测试、Dashboard 导出 5 个测试全部通过；Dashboard TypeScript 编译、Python compileall 和本次范围内 `git diff --check` 均通过。

### 2026-09-09：使用交付与离线快速开始

- 新增 `RESEARCH_SYSTEM_USAGE.md`，统一说明环境、Stage 1、不可变产物校验、Dashboard、ETF/Put 情景、TradingAgents 信号回放、历史期权报价、long-only 和模拟盘。
- 新增 `examples/data/qqq_spy_stage1_demo.csv.example` 合成演示数据，使第一次运行不依赖网络或供应商账户；文档和输出均明确禁止将其当作策略证据。
- 新增仓库演示数据回归测试，并实际验证 Stage 1 → manifest 校验 → Dashboard v2 导出的离线链路。
- 更新早期 `SUMMARY.md`、`TODO.md` 和应用 README 的状态导航，避免把旧原型待办误认为当前统一系统状态。
- 本轮最终验证：量化研究应用 63 个测试、Nasdaq 工作流 7 个测试、Dashboard 导出 5 个测试全部通过；Dashboard 生产构建、Python compileall 和限定范围 `git diff --check` 通过。

### 2026-09-14：生产数据质量门第一批实现

- 新增 `data_readiness.py` 与 `audit_production_data.py`，把生产数据前检查清单实现为只读、
  可脚本化的阻断式审计；有效报告退出码为 0，存在阻断问题时退出码为 2。
- 股票数据检查覆盖 instrument master、带 `announced_at/available_at` 的半开成员区间、
  OHLCV/复权因子、上市区间、成员行情覆盖、公司行动和退市清算规则。
- 可选期权检查要求合约主表和报价成对输入，验证合约生命周期、标的与实物交割物、
  strike/乘数、bid/ask、报价可用时间以及请求区间内的合约报价覆盖。
- 能力状态保持分离：股票或期权只有无阻断问题才是 `historical_validated`；缺数据维持
  `prototype/not_provided`，真实执行始终为 `not_evaluated`。数据通过不代表策略有效。
- 验证：新增 8 个测试覆盖有效数据包、空表、缺列、成员重叠、未知标的、缺行情、
  无时区时间、退市规则、不完整期权数据包、合成来源不能升级能力状态，以及审计 ID
  不依赖本地绝对路径。

### 2026-09-14：point-in-time 成员主流程接入

- Nasdaq 主流程新增 canonical 成员表与 instrument master 适配，稳定 instrument ID 按
  symbol 有效区间映射为市场代码；多指数文件必须显式选择 `membership_index_id`。
- 行情获取列表改为请求区间内历史成员并集，不再在提供历史成员文件后继续只下载当前
  成分股；退出成员仍保留特征预热、估值和退出所需行情。
- canonical 成员在进入当日横截面前同时检查半开生效区间和 `available_at`；默认用纽约
  时间 16:00 构造决策时点并处理夏令时。收盘后才可用的变更最早在下一交易日进入。
- 旧三列成员格式继续兼容，但因缺少信息可用时间，结果保留历史数据警告，不会被当作
  availability-aware 的生产输入。
- Nasdaq 工作流新增 canonical 时点测试，验证稳定 ID 映射、退选边界、收盘后信息延迟
  和新成员预热。
- 最终验证：完整应用回归 71 个测试、Nasdaq 工作流 8 个测试、Dashboard 导出 5 个测试
  全部通过；Python compileall、合成数据 CLI 负向质量门和限定范围 `git diff --check`
  均通过。

### 2026-09-14：canonical bars 主流程接入

- 新增 `canonical_bars.py`，把稳定 instrument ID 的 raw OHLCV 按 bar 时点映射到当时有效
  symbol，并使用显式 `adjustment_factor` 同口径调整价格和成交量。
- Nasdaq CLI 新增 `--bars-file`、`--corporate-actions-file` 和冻结的
  `--max-bar-delay-hours`。canonical 路径要求成员表、instrument master、公司行动和
  带时区请求区间完整提供，并在训练前重新执行 readiness audit。
- 审计不通过时直接终止，不回退 Yahoo；通过后运行报告保存稳定 `audit_id`、能力状态、
  阈值和 `market_data_mode=canonical_audited`。原 Yahoo 路径明确保持 `prototype`。
- canonical 路径暂时强制关闭当前视角 FRED，并拒绝没有 `available_at` 的旧基本面入口，
  防止股票数据通过审计后又从宏观修订值或基本面发布时间产生未来信息泄漏。
- readiness audit 新增 bar 延迟阈值，阻止在冻结允许时间之后才到达的数据被提前用于
  信号；报告记录每个输入文件 SHA-256，`audit_id` 不绑定本地绝对路径。
- 新增 4 个定向测试，覆盖 bar 延迟、历史 symbol 切换、复权价量、决策时点可见性和
  缺基准拒绝。

### 2026-09-14：宏观与基本面 vintage 接入

- readiness audit 新增 `fundamental_vintages` 和 `macro_vintages` 独立 schema、时间顺序、
  revision 主键、稳定 instrument 外键、有限数值和来源能力检查。
- 所有生产表新增强制 `source_kind`，只有显式 `historical_observed` 且来源名称不含合成/
  演示标记时才可能解锁历史能力，避免依赖文件名或隐含约定判断数据性质。
- 宏观 schema 冻结 `feature_name/unit`：利率及利差必须是 `decimal_rate`，VIX 必须是
  `index_points`；未知特征或百分数/小数口径不一致直接拒绝。
- 新增 `vintage_features.py`：按统一决策时点解析已经可见的最新观察期；旧观察期在之后
  发布修订时不会覆盖更新观察期，基本面始终用 stable instrument ID 关联。
- canonical bars 保留 `instrument_id` 进入特征层；Nasdaq CLI 新增两个 vintage 文件入口
  和统一的 `decision_timezone/decision_time_local`，成员、bars、宏观、基本面共用同一时点。
- canonical 模式拒绝当前视角 FRED 和无 `available_at` 的旧基本面；用户可以提供已审计
  vintage 文件，或显式关闭对应特征，不能静默降级。
- 新增 5 个定向测试，覆盖 vintage 时间线、独立能力状态、未来数据不可见、旧期修订不
  覆盖新期，以及基本面 stable ID 特征接入。

### 2026-09-14：标签与历史推理解耦补强

- 模型训练继续只纳入成熟标签；测试推理改为面向全部合格特征候选，退市或未来价格缺失
  不再因为 `dropna(target)` 在打分之前消失。
- 回测选股只依据当时分数。若已选股票缺实现收益或基准退出价，该周期保留并标记
  `unavailable_missing_realization/benchmark`，不删除日期，也不由下一名替补。
- 报告区分 scored candidates 与可评估 observations，并新增 backtest status counts；只有
  数据审计通过且所有已选周期实现值完整时，`historical_evaluation_status` 才能升级为
  `historical_validated`。
- 新增缺标签候选测试，确认最高分但缺未来实现值的股票仍出现在 holdings，周期净收益为
  不可用。
- 本轮最终验证：完整应用回归 81 个测试、Nasdaq 工作流 10 个测试、Dashboard 导出
  5 个测试全部通过；Python compileall、合成全量数据包负向 CLI 和限定范围
  `git diff --check` 均通过。

### 2026-09-14：前向信号不可变证据仓

- 新增 `forward_archive.py` 和 `manage_forward_archive.py`；只允许在
  `decision_time <= recorded_at < valid_until` 时首次发布信号，并要求特征数据
  `data_as_of <= decision_time`，过期历史信号不能倒填成前向记录。
- 信号和成熟标签分开保存。当前信号必须有非空有限分数；标签必须声明持有期、
  `label_available_at` 和不可变数据快照，尚未到达的数据不能参与评估。
- partial 与 complete 评估使用不同内容 ID 追加保存，不覆盖原信号或较早评估；汇总优先
  展示覆盖率最高的成熟证据，缺标签不会用下一名替补。
- 信号、评估和 manifest 均有 SHA-256 与内容身份复验；能力边界明确标记为
  `local_content_addressed_not_third_party_timestamped`，不把本机时钟宣称为外部可信时间戳。
- 发布信号必须同时归档有效的生产数据审计或不可变数据快照 manifest；系统重算其内容
  ID，核对历史股票能力及核心表哈希，不能用孤立字符串冒充数据证据。
- 新增 `build_latest_feature_batch()` 和 `generate_signal_from_feature_batch()`；归档冻结实际
  输入的 stable instrument ID、特征 schema 与有限数值，并要求目标集合、快照和决策时点
  与信号完全一致。
- 新增 11 个前向归档测试并补充 1 个 stable ID 特征批次测试，覆盖有效期内发布、过期倒填拒绝、路径安全、数据时点、数据与特征 manifest、幂等、篡改检测、语义复验、标签
  成熟门、partial 到 complete 追加、信号契约和 CLI 发布/检查。
- 本轮最终验证：完整应用回归 93 个测试、Nasdaq 工作流 10 个测试、Dashboard 导出
  5 个测试全部通过；Python compileall 和限定范围 `git diff --check` 均通过。

### 2026-09-14：券商只读账户快照与对账门

- 新增 `broker_account.py` 与 `reconcile_broker_account.py`，定义脱敏、内容寻址的
  `broker_account_snapshot.v1`，支持从首次快照初始化事件账本和用后续快照逐项对账。
- 首版能力严格限制为 USD 无融资现金账户的多头股票/ETF；负持仓、期权、非 1 乘数、
  未来可见或过期快照均阻断，不推测未确认的融资、卖空或期权规则。
- 账本新增只能作用于 pristine zero-cash ledger 的 `account_snapshot_import` 事件；重复或
  中途全量覆盖账户被拒绝，后续变化仍须作为成交、结算、分红或现金事件追加。
- 对账检查账户/券商身份、五个现金桶、stable instrument ID 数量和乘数；结果使用稳定
  reconciliation ID，匹配为 0、差异为 2，两个 CLI 路径始终报告 `orders_submitted=0`。
- 合成来源保持 `simulation_only`；`broker_observed` 也仅标记
  `broker_export_contract_validated`，不等同于已认证来源或具备真实下单能力。
- 新增 7 个测试覆盖初始化、回放、来源能力、时间新鲜度、现金账户边界、身份伪造、
  差异对账和 CLI 不覆盖已有状态；并新增可执行的合成账户快照示例。
- 本轮最终验证：完整应用回归 100 个测试、Nasdaq 工作流 10 个测试、Dashboard 导出
  5 个测试全部通过；Python compileall、CLI 合成示例和限定范围 `git diff --check` 均通过。

### 2026-09-14：待人工审核的券商中立订单包

- 新增 `broker_orders.py` 和 `prepare_broker_orders.py`，把当前 `OrderPlan` 转为内容寻址的
  `broker_order_package.v1`；输出固定为 `pending_human_approval/manual_export_only`，且
  `orders_submitted=0`，没有网络发送或自动审批路径。
- 导出前同时复验计划 basis/state hash、账户/券商身份、24 小时内的只读快照和完整对账；
  账户发生任何未同步变化都阻断订单包生成。
- 每笔订单使用 stable instrument ID 和显式 broker symbol，冻结方向、整数数量、限价和
  预计费用，并重算 client order ID；累计卖出不能产生空头，买入按限价不能超过已结算
  现金，不使用尚未结算的卖出款。
- 输出文件原子写入；相同内容可幂等复验，已有不同内容不会被覆盖。symbol map 仍须由
  当时有效的 instrument master 生成并人工复核，不能视为具体券商适配已完成。
- 新增 5 个测试覆盖有效导出、账户变化、过期、缺 symbol、非整数股、卖空、限价购买力、
  client order 身份及 CLI 幂等无提交语义。
- 本轮最终验证：完整应用回归 105 个测试、Nasdaq 工作流 10 个测试、Dashboard 导出
  5 个测试全部通过；Python compileall 和限定范围 `git diff --check` 均通过。

### 2026-09-14：券商成交回报只读导入与订单对账

- 新增 `broker_fills.py` 和 `import_broker_fills.py`，定义内容寻址的
  `broker_fill_batch.v1` 与 `broker_fill_import.v1`；只读取人工下载的成交文件并更新本地
  账本，报告恒为 `orders_submitted_by_system=0`，不包含券商写接口。
- 订单包新增账户事件序列锚点。成交导入会重放锚点前缀并复验 state hash；锚点后的事件
  只能是本订单包的合规股票成交，否则以账户发生外部变化阻断，避免把陈旧计划套到新账户。
- 每笔成交严格绑定 broker execution ID、client order ID、stable instrument ID、symbol 和
  方向，并验证整数数量、限价、执行时间、可用时间及批次新鲜度。已有账本成交也重新执行
  相同语义校验，不盲信可编辑的 simulator state。
- 先在临时账本应用整批并检查跨批次累计数量，全部成功后才保存。相同 execution ID 和
  相同内容幂等；ID 重用但内容变化、累计超量、错方向、错标的或现金/持仓约束失败均不会
  留下半批状态。逐单报告 unfilled、partially_filled 或 filled。
- 成交沿用事件账本的真实现金桶：买入形成 trade payable，卖出形成 trade receivable；不
  自动假设结算，也不把未结算卖出款当作购买力。结算和后续券商快照仍是独立待接入事件。
- 新增 8 个测试和 3 个配套订单/成交示例，覆盖完整/部分成交、买卖现金桶、跨导出幂等、累计超量、
  限价与时间门、伪造已有成交、账户外变化和 CLI 保存恢复。固定合成端到端链实测得到
  QQQ 10→8 股、trade receivable 801，重复导入不改变账户。
- 本轮最终验证：完整应用回归 113 个测试、Nasdaq 工作流 10 个测试、Dashboard 导出
  5 个测试全部通过；Python compileall、固定合成 CLI 链和限定范围 `git diff --check`
  均通过。

### 2026-09-14：券商结算、分红与入出金活动导入

- 新增 `broker_activities.py` 和 `import_broker_activities.py`，定义内容寻址的
  `broker_activity_batch.v1` 与 `broker_activity_import.v1`，把券商只读现金活动映射到现有
  事件账本；命令不访问券商网络且恒报告 `orders_submitted_by_system=0`。
- 首版白名单仅包含 USD 交易应收/应付结算、分红应计/到账、现金入金/出金。未知利息、
  税费、冲正、融资和多币种活动明确拒绝，不通过近似名称或正负号猜测。
- 批次复验初始化账户/券商身份、原始导出 hash、新鲜度与双时间轴；活动必须发生在初始
  账户快照之后。数量统一为有限正数，出金负号由内部生成，避免供应商符号口径漂移。
- 活动按经济生效时间排序，同时间依赖再按入金、分红应计、交易结算/分红到账、出金
  排序；整批先在临时账本重放，超应收/应付、超分红应收或超现金出金时不留下部分事件。activity ID 跨重复导出
  幂等，同 ID 内容变化阻断。
- 成交导入门同步收紧：账户变化后仍允许完全相同 execution ID 的只读幂等复验，但拒绝
  新增成交，解决结算后重试与陈旧计划隔离之间的冲突。
- 新增 7 个活动测试并补充 1 个成交生命周期测试，覆盖买卖结算、分红依赖、入出金、
  混合证据降级、时间/币种/类型拒绝、整批原子、ID 冲突及 CLI 保存恢复；增加固定合成
  结算和后续账户快照示例，将卖出产生的 801 应收转成已结算现金，并重新对账为 matched。
- 本轮最终验证：完整应用回归 121 个测试、Nasdaq 工作流 10 个测试、Dashboard 导出
  5 个测试全部通过；Python compileall、完整合成 CLI 链和限定范围 `git diff --check`
  均通过。

### 2026-09-15：账户状态并发与完整性加固

- 模拟账户状态升级为 `paper_simulator.v3`，每版保存递增 revision、内容 SHA-256、上一版
  hash 和事件链头；加载时重算内容和事件链，手工篡改不再被静默接受。v1/v2 仍可读取，
  下一次成功保存时自动升级。
- 新增 `SimulatorStateStore`，使用 POSIX advisory lock 把读取、业务校验、账本更新和原子
  保存包在同一跨进程事务中。账户初始化、对账读取、订单导出、成交导入、活动导入和
  状态检查统一迁移到该入口，避免多个进程各自 load/save 丢失更新。
- 状态保存增加 CAS：陈旧的内存实例不能覆盖新文件；成交和活动 CLI 可通过
  `--expected-content-sha256` 绑定人工审核版本。事务异常不会推进 revision 或留下半批事件。
- 每次覆盖 v3 状态前，将上一版原文按内容 hash 归档到相邻 history 目录，并对临时文件、
  目标目录执行 fsync；文档补充了冲突处理、损坏停机和演练恢复步骤。
- 新增专用 `quant-research.yml` CI：Ubuntu/Python 3.11 运行应用、Nasdaq 与 Dashboard 导出
  回归及 compileall；Node 20 独立执行 Dashboard typecheck 和生产构建，并按分支取消旧作业。
- 新增 7 个状态/CAS 测试，覆盖内容及事件链篡改、陈旧写入、旧版本迁移、失败事务、历史
  归档、四进程并发更新及两个 CLI 的审核版本冲突。完整应用回归共 128 个测试。

### 2026-09-15：券商订单受理与终态生命周期

- 新增 `broker_order_status.py` 和 `import_broker_order_status.py`，定义内容寻址的
  `broker_order_status_batch.v1` 与导入报告；只读取券商导出，恒为零系统提交/撤销。
- 状态机显式记录 `accepted/rejected/cancelled/expired`、broker order ID、稳定原因码和双
  时间轴；终态不可重开，broker order ID 不可漂移，status ID 重复幂等、内容冲突阻断。
- 状态作为无财务影响的事件写入同一 hash 链和事务状态；它会推进事件序列但不改变账户
  basis。合成账户或状态来源仍降级为 `simulation_only`。
- 成交导入新增受理门：成交经济时点必须不早于 accepted，且必须早于撤销/拒绝/过期终态；
  只有成交文件而没有受理证据不再被接受。
- 新增固定 accepted 状态示例并把合成端到端链扩为“账户→订单包→受理→成交→结算→对账”；
  新增 4 个生命周期测试，覆盖受理/撤销、拒绝、非法迁移、ID 漂移、CLI 幂等和终态后成交。

### 2026-09-15：只读账户与当前信号发布接口

- 新增 `operations_snapshot.py`，从通过 v3 完整性检查的账户状态原子发布脱敏
  `operations_snapshot.v1`；金额/数量使用 canonical Decimal 字符串，避免 Dashboard JSON
  经二进制浮点往返改变账户证据。
- 账户视图包含 state revision/content hash/event chain、现金分桶、持仓、事件类型计数和
  可选订单包的受理/终态及累计成交；原始 account ID 与 broker order ID 不出现在文件中。
- 前向归档新增 `export-current`：逐目录重验 manifest、数据/特征/信号 hash，只选择请求时
  仍有效且策略匹配的最新信号，原子发布 `dashboard_signal.v1`；无有效信号明确失败。
- Dashboard 新增 `GET /api/v2/accounts/current`，并把 `signals/latest` 从固定 404 升级为
  hash 校验、实时有效期校验和 strategy/horizon 精确匹配的只读路由；两者均 `no-store`，
  缺失、损坏、过期使用不同稳定原因码，且没有用户可控文件路径。
- 新增 3 个 Python 测试覆盖账户脱敏/Decimal/hash、订单生命周期视图和当前信号有效期导出；
  TypeScript typecheck 已通过。

### 2026-09-15：券商 JSON Decimal v2 迁移

- `broker_account_snapshot.v2` 的现金桶与持仓、`broker_order_package.v2` 的数量/限价/费用、
  `broker_fill_batch.v2` 的数量/价格/费用，以及 `broker_activity_batch.v2` 的金额统一改为
  canonical Decimal 字符串；拒绝指数形式、多余尾零、布尔值和非有限数。
- 新订单导出默认使用 v2；成交和活动 v2 从内容 ID 校验到事件账本全程不经过 binary float，
  高精度小数测试可精确落入应收和现金。Dashboard 当前信号分数也使用 Decimal 字符串，
  避免 Python/JavaScript 科学计数格式不同导致误报 hash 损坏。
- v1 账户、订单、成交和活动仍可按原内容 ID 读取，保证已有不可变证据可回放；v1 不会被
  静默重写为 v2。迁移会产生新的 package/batch/snapshot ID，所有下游引用必须整体重建。
- 固定券商演示链已升级为 v2 订单、成交和活动并重新生成内容 ID；保留 v1 账户样例验证
  兼容入口。新增 3 个高精度与非规范字符串测试，并补充 v1/v2 订单 round-trip。

### 2026-09-15：策略证据与简单基线加固

- `baseline_comparison.v2` 将“等权”修正为覆盖整个可用股票池的等权基线，并预留 1% 费用
  空间；旧实现实际只按任意代码顺序取 Top-K，不能代表 Nasdaq 等权基准。
- 等权、动量、模型在每个窗口共享信号日与下一开盘规则，并统一重跑默认 0/10/20/30 bps
  成本情景；报告新增逐窗口超额收益、成本敏感度和 QQQ 涨/跌/横盘覆盖。
- 新增 `model_evidence` 结论门：至少 3 个不重叠窗口，且多数窗口正超额、战胜等权和动量、
  在最高成本下仍为正；否则输出稳定原因码和 `insufficient_evidence/not_robust`。candidate
  也明确不构成 alpha 或实盘证明。
- 测试确认等权持有完整股票池、成本提高不会改善模型净收益、重叠窗口与样本不足会阻断
  结论，同时保留未来价格不改变此前动量订单的因果性检查。

### 2026-09-15：Covered Call、Collar 与动态现金对冲

- `run_hedge_comparison()` 升级为 `hedge_comparison.v2`，在原有持有、现金减仓、波动率目标
  和保护性 Put 之外，增加趋势+波动率动态现金对冲、Covered Call 和 Collar；全部使用滞后
  价格/波动信息，并用未来价格扰动测试检查此前结果不变。
- Covered Call 只按已有整手现股卖 Call；轻量账户新增现股覆盖约束，禁止裸卖 Call 或卖出
  导致覆盖不足，并在价内到期时按执行价交割。Call 定价与 Delta 仅用于明确标记的
  Black-Scholes scenario。
- Collar 同时持有价外 Put 与备兑 Call，报告每腿开仓/平仓现金流；动态对冲只调整股票与
  现金比例并设置再平衡 deadband，不伪装成尚未实现借券/期货/反向 ETF 的线性空头对冲。
- 报告新增 claim boundary：合成价格路径和理论 bid/ask 只能比较机制，不能称为历史期权
  业绩、实盘就绪或投资建议。真实策略评价仍要求合格历史 bid/ask、合约调整和生命周期。

### 2026-09-15：未结算成交冲正

- 新增内容寻址的 `broker_fill_correction_batch.v1` 和事务 CLI。券商 correction 精确引用原
  execution，账本追加 `equity_fill_reversal`，不删除或改写原成交；持仓与未结算应收/应付
  恢复后，订单剩余数量可由新的 execution 再成交。
- correction ID 重试幂等，同一 execution 的第二个冲正、未知 execution、内容冲突和逆时序
  均阻断；买入、卖出、CLI 保存恢复和冲正后替代成交均有测试。
- 当前 settlement 是现金桶聚合事件，不能可靠归属到单笔 execution。因此相关应收/应付
  发生后续结算后，冲正明确 fail closed，要求人工对账或从可信快照重建，不猜测现金归属。

### 2026-09-15：本轮综合验收

- 完整 `apps/quant_research` 回归 165 个测试通过；Nasdaq 工作流 13 个测试、Dashboard
  Python 导出 5 个测试通过，Python compileall 与 CI workflow YAML 解析通过。
- Dashboard TypeScript typecheck 和 Next.js 生产构建通过；构建结果包含只读
  `/api/v2/accounts/current` 与 `/api/v2/signals/latest` 动态路由。
- 固定 CSV 的 `run_research` 已实跑并产出 `hedge_comparison.v2` 七策略报告；固定合成券商链
  已实跑“账户→订单包→accepted→成交→冲正”，冲正后订单恢复 `unfilled`、账户状态通过
  v3 hash/事件链检查。
- 限定研究系统路径的 `git diff --check` 通过。工作树仍存在用户原有的大量 CRLF/LF 噪声
  与其他未跟踪产物，本轮未清理、未提交，也未把这些无关变化纳入完成证据。

### 2026-09-15：历史报价 Covered Call

- `option_quotes` 正式输入增加显式 `option_type` 校验；保护性 Put 在混合链中只看 Put，
  Covered Call 只看 Call，不通过合约代码名称猜类型。
- 新增 `select_fixed_covered_call()` 和 `run_fixed_covered_call()`：按当时可见 bid 卖出完全由
  现股覆盖的整数张 Call，持有期用有效 bid/ask 中间价记录负债，到期价内按执行价交付股票。
- 没有 Call、现股不足一张、净权利金非正、价格历史未到期和报价覆盖不足都有显式状态；
  报告声明首版没有提前指派模型，历史 quote replay 不等于真实券商执行或策略推荐。

### 2026-09-15：联合因子现金对冲计划

- `plan_cash_etf_hedge()` 把 `RiskSnapshot` 转成内容寻址的 `cash_hedge_plan.v1`：先调整 QQQ
  残差因子，再扣除该交易通过 `qqq_spy_loading` 对 SPY 公共因子的影响，最后调整 SPY，
  避免分别按两个独立 Beta 全额对冲。
- 计划使用整数股和美元 deadband，只允许卖出现有 SPY/QQQ 到现金，不生成融资买入或 ETF
  裸空；目标不可达时给出持仓限制、风险增加禁用和目标残差原因码。
- 期权 Delta 已包含在风险快照因子金额时不会重复计入。该输出是 `research_plan_only`，仍需
  经过账户状态、现金、限价和人工订单包复验，不能直接提交券商。

### 2026-09-15：生产期权表连接与证据降级

- 新增 `load_option_market_data()`，直接连接 readiness gate 使用的 canonical 合约主表和
  报价表，复验 many-to-one contract ID、上市/最后交易/到期窗口、双时间轴、数值范围、
  option type、行权与结算类型。
- 扁平报价加载器要求 `source/source_kind/option_type`。合约与报价两侧只有同时声明
  `historical_observed` 且来源名不含合成标记时，回放才输出
  `historical_option_quote_replay`；synthetic、scenario 或无来源 DataFrame 自动降级。
- 这消除了“任何 bid/ask DataFrame 都被写成历史回放”的错误证据升级，也让生产审计输出
  可直接进入保护性 Put、Bear Put Spread 和 Covered Call 回放。
- 最新完整应用回归为 165 个测试，新增联合因子计划、历史 Covered Call、生产表连接、来源
  降级和非有限/非整数合约字段边界均已覆盖。

### 2026-09-15：期权生命周期输入边界收口

- 扁平 CSV 与 canonical join 都拒绝缺失生命周期/来源字段、非有限价格和非整数乘数；
  可交易链排除未上市或已经超过最后交易时点的合约。
- 固定/滚动 Put、Bear Put Spread 和 Covered Call 在建仓前复验实物结算及同标的交割物，
  不把现金结算或调整后交割物按普通股票指派记账。
- 历史证据要求完整且有效的上市/报价/可用/最后交易/到期时间、行权/结算/交割元数据；
  缺失或无效的直接 DataFrame 保持 `scenario_only`。增加两项加载/交易窗口测试，并补充
  Covered Call 不支持的结算/交割物与证据降级断言。

### 2026-09-15：Covered Call 观察型提前指派

- 新增 `option_lifecycle_events` canonical 输入与加载校验：事件 ID、合约、类型、发生/可见
  双时间轴、整数张数和来源证据均为必填；拒绝重复、未来倒挂及未知来源类型。
- 固定 Covered Call 支持全部或部分观察型提前指派，只在 `available_at` 到达后交付股票并
  按执行价增加现金；累计超过未平仓张数、建仓前或到期后事件 fail closed。
- 报告分别暴露报价证据和指派证据。未提供事件文件时标记 `NO_EARLY_ASSIGNMENT_DATA`；
  不根据价内程度或除息日做无法验证的启发式推断。
- 生产 readiness 升级为 `production_data_readiness.v2`，把观察型生命周期事件作为独立
  `option_lifecycle` 能力审计；旧 v1 manifest 仍可由前向归档验证器读取。

### 2026-09-15：Nasdaq 预测诊断补齐

- 新增日频 Pearson IC，并保留 Rank IC、标准差、IR 与正值比例；`daily_ic.csv` 同时保存
  两类时间序列，原 `rank_ic.csv` 保持兼容。
- 新增严格同日排名的五分组成熟标签均值与最高减最低组差值，不跨日期混排模型分数；
  报告明确它是标签诊断而非组合收益。
- Nasdaq 工作流增加分组、IC/Rank IC 和参数失败测试，当前共 11 项通过。

### 2026-09-15：跨窗口特征重要性稳定性

- walk-forward 每个模型窗口保存归一化 gain share；汇总报告计算两两重要性 Spearman、
  Top-K Jaccard，以及逐特征均值、标准差和窗口出现率。
- 不足两个有效模型窗口显式返回 `insufficient_windows`，不拿最后一个模型冒充跨期稳定；
  也不依据合成或单窗口证据自动删除特征。
- Nasdaq 工作流新增稳定/不足窗口测试，当前共 12 项通过。

### 2026-09-15：PIT 市场宽度特征

- 新增高于 20 日均线比例、20 日收益为正比例和横截面 20 日收益离散度，先应用当日
  point-in-time 成员可见性，再计算宽度，非成员不进入分母。
- 三项日级特征沿用 trailing-only 标准化，未来成员及未来行情不会影响历史宽度；既有
  “删除非成员后特征完全一致”因果测试同时覆盖该路径。
- 行业相对强弱与市值排名仍等待带 `available_at` 的分类/市值 vintage，不使用当前分类
  回填历史。

### 2026-09-15：动态 Put 触发与波动率斜率情景

- `hedge_comparison.v3` 增加 `dynamic_protective_put_model`：只用上一交易日价格、滞后趋势
  和滞后实现波动率决定开仓/平仓，信号关闭按理论 bid 卖出，展期继续计入双边价差和费用。
- Put 与 Call 理论估值增加冻结的价外程度波动率斜率参数，避免所有执行价机械共用同一
  IV；参数由 CLI 暴露并进入冻结配置。
- 比较现含九种策略，未来价格扰动测试覆盖动态 Put 和 Bear Put Spread。全部仍是
  Black-Scholes scenario，不提升为历史期权业绩。
- 固定演示 CSV 已实际运行并产出 `hedge_comparison.v3`、九策略曲线和逐腿交易文件；
  演示期很短时动态 Put 可因滞后窗口尚未成熟而保持现金降风险基线，这是显式因果行为。

### 2026-09-15：行业与市值 vintage

- readiness v2 新增 `classification_vintages` 独立能力：stable instrument ID、观察/发布/可用
  时间、revision、sector、正 market cap 和来源均接受阻断式审计。
- Nasdaq 主流程按每个决策时点解析最新可见分类，生成行业内 20 日收益排名、行业相对收益
  和市值排名；分类发布前的样本不会被当前分类回填。
- 新增 vintage 可见性和端到端特征测试；当时完整应用 163 项、Nasdaq 13 项通过。真实行业/
  市值历史仍需供应商文件，合成 schema 不解锁历史能力。

### 2026-09-15：跨工具对冲诊断

- 新增 `hedge_instrument_comparison.v1`，用统一观察收益口径比较普通/反向 ETF 和期货，
  报告基准暴露、跟踪误差、日均基差、开仓成本、费用率及初始/维持保证金。
- 普通 ETF 需要负名义头寸而没有借券能力时显式不可行；反向 ETF 可用正头寸，期货单列
  保证金资本要求。输出恒为 research-only、`execution_capability=none`。
- 当前仅是每日重置敞口诊断，不包含真实期货换月、再平衡滑点、融资和追保路径；新增
  2 项测试覆盖可比结果、借券限制、保证金和无效规格。

## 完成判定

只有设计中的交付物、编号验收项和三轮优化均有当前代码及测试证据时，才把本任务标为完成。测试未执行、只存在设计或只存在演示路径的项目不得标记完成。
