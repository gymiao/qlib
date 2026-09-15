# QQQ / S&P 500 对冲、Put 与个股研究核心

本目录实现了 `RESEARCH_SYSTEM_DESIGN.md` 中的统一研究账户。详细完成状态见
`RESEARCH_IMPLEMENTATION_PROGRESS.md`，从环境准备到 Dashboard、历史报价和模拟盘的
完整操作步骤见 `RESEARCH_SYSTEM_USAGE.md`。当前已落地：

- QQQ、SPY 买入持有、现金减仓、波动率目标、趋势/波动率动态现金对冲，以及保护性 Put、
  Covered Call、Collar 情景比较；
- 股票与期权现金账本、现金担保卖 Put 资金预留、Covered Call 现股覆盖检查及到期交割；
- SPY 与 QQQ 相对 SPY 残差因子的联合风险估计；
- 内容寻址的 SPY/QQQ 联合因子现金对冲计划，只减持现有 ETF，不重复计算 QQQ 的 SPY Beta；
- 可接入现有 Qlib 预测分数的 long-only 选股及持仓缓冲。
- 内容寻址的离线价格快照、稳定配置 hash 和运行 manifest；
- Decimal 事件账本，包括交易应收应付、T+1 配置、预留、分红、拆股和幂等回放；
- `paper_simulator.v3` 状态存储，包括跨进程事务锁、CAS、内容/事件链校验和历史版本归档；
- 不需要未来标签的 Nasdaq 特征生成入口。

当前 Put/Call 覆盖回测使用仅依赖过去波动率的 Black-Scholes 情景估值，输出明确标记为
`black_scholes_scenario_not_historical_quotes`。它用于比较架构、现金流和风险，不能作为
历史可成交收益证据。接入历史 bid/ask 后，主回测应由真实报价适配器替换该估值路径。

`option_quotes.py` 和 `option_backtest.py` 另提供带 `quote_ts/available_at` 的历史 bid/ask
回放路径。只有通过字段、时点、连续性和合约生命周期检查的数据才能使用该路径；
Black-Scholes 输出始终保留为 `scenario_only`，不会自动升级为历史成交证据。
历史 Covered Call 路径要求显式 `option_type=call`，按 bid 卖出、按中间价记录空头负债，
并在价内到期时交付足额现股；首版不模拟提前指派，相关假设会写入报告。
`load_option_market_data()` 把 canonical 合约表和报价表按 contract ID 连接，并验证合约生命
周期。合约与报价必须同时是 `historical_observed` 才能得到历史回放能力，合成或来源不明的
bid/ask 只标为 `scenario_only`。

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

## 生产数据质量审计

`data_readiness.py` 和 `audit_production_data.py` 将设计文档的数据约束实现为阻断式质量门。
它检查 instrument master、point-in-time 成分区间、OHLCV/复权、公司行动/退市清算，
并可同时检查期权合约主表、历史 bid/ask 和观察型提前指派事件。示例：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.audit_production_data \
  --instrument-master path/to/instrument_master.csv \
  --membership path/to/universe_membership.csv \
  --bars path/to/bars.csv \
  --corporate-actions path/to/corporate_actions.csv \
  --start 2015-01-01T00:00:00Z \
  --end 2026-09-01T00:00:00Z
```

通过时仅把对应历史数据能力标记为 `historical_validated`；它不会把研究系统标记为实盘
可用，也不会把数据质量通过解释成策略有效。完整字段和期权参数见
`RESEARCH_SYSTEM_USAGE.md`。
每张表必须显式提供 `source_kind`；合成和情景输入不能通过改文件名或 source 名称升级能力。

`examples/nasdaq100_medium_low_frequency.py` 同时支持 canonical 成员表和 instrument master；
它按请求区间内历史成员并集获取行情，并在横截面排名前检查成员的生效区间及
`available_at`。缺少 `available_at` 的旧三列成员表仍可兼容，但不会被视为生产级历史数据。
提供 `--bars-file` 和 `--corporate-actions-file` 后，主流程会先执行 readiness audit，再由
`canonical_bars.py` 按时点 symbol 映射加载 raw OHLCV 和复权因子；失败时不会静默回退网络数据。
`vintage_features.py` 解析 macro/fundamental vintages，并按决策时刻解析当时已知的最新观察期。
它也解析带版本的 sector/market-cap classification vintages，用于行业内和市值横截面排名。
canonical 模式拒绝当前视角 FRED 和无 `available_at` 的旧基本面输入。

`experiments.run_baseline_comparison()` v2 使用全股票池等权、Top-K 动量和 Top-K 模型做
同日历比较，并输出多档成本压力、市场状态覆盖和跨窗口证据门。少于 3 个不重叠窗口、
多数窗口无正超额、不能稳定战胜简单基线或高成本下失效时，报告不会标记为候选策略。

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

所有会改变账户的 CLI 都通过 `SimulatorStateStore` 完成一个锁内事务；成交和活动导入还可
使用 `--expected-content-sha256` 绑定人工复核时看到的版本。状态恢复和冲突处理步骤见
`RESEARCH_SYSTEM_USAGE.md` 的“账户状态并发、完整性与恢复”。

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

## 前向信号证据仓

`forward_archive.py` 和 `manage_forward_archive.py` 用于积累真正先发布、后成熟的模型证据。
发布命令只接受当前仍在有效窗口内的 `signal_batch.v1`，并要求 `data_as_of` 不晚于
`decision_time`：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.manage_forward_archive publish \
  --archive-root .artifacts/forward_signals \
  --signal-file .artifacts/current_signal.json \
  --data-snapshot-id <immutable-feature-snapshot-id> \
  --data-as-of 2026-09-14T20:55:00Z \
  --data-manifest .artifacts/production_data_audit.json \
  --feature-batch .artifacts/current_feature_batch.json
```

`--data-manifest` 必须是通过质量门的 `production_data_readiness.v1/v2` 报告，或可验证的
`data_snapshot.v1` manifest；其内容 ID 必须等于 `--data-snapshot-id`。数据 manifest 会随
信号一起冻结，不能只填写一个无法追溯的 ID。
`--feature-batch` 使用 `feature_batch.v1`，保存本次打分实际消费的 stable instrument ID、
特征列和值；其数据快照和决策时点必须分别与数据 manifest、signal 完全一致。可使用
`signals.build_latest_feature_batch()` 与 `signals.generate_signal_from_feature_batch()` 从同一
对象先生成特征批次、再生成信号，避免写盘前后输入漂移。

信号目录一经发布不可改写。持有期结束且标签已到达后，用 `label_batch.v1` 追加评估：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.manage_forward_archive evaluate \
  --archive-root .artifacts/forward_signals \
  --signal-id <signal-id> \
  --labels-file .artifacts/matured_labels.json

/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.manage_forward_archive inspect \
  --archive-root .artifacts/forward_signals
```

评估允许从 partial 追加到 complete，但旧版本不会删除；未来才可用的标签会被拒绝。
manifest 会校验信号、精确特征批次、标签、输入数据 manifest 和每个文件的 SHA-256。该证据仓用于发现本地归档
内容是否改变，报告明确标记 `local_content_addressed_not_third_party_timestamped`；若需要
独立可信时间证明，应把 manifest hash 同步到受控对象存储、Git 签名或外部时间戳服务。

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

## 券商只读快照导入与对账

`broker_account.py` 冻结券商无关的 `broker_account_snapshot.v2`，
`reconcile_broker_account.py` 可从首次快照初始化模拟账本，并用后续快照检查现金、应收/
应付、持仓和乘数差异。首版只接受 USD 无融资现金账户及多头股票/ETF；期权、卖空、
融资和自动下单均拒绝。离线示例：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.reconcile_broker_account bootstrap \
  --snapshot examples/data/broker_account_snapshot_demo.json.example \
  --simulator-state .artifacts/broker_demo/paper-simulator.json \
  --as-of 2026-09-14T20:10:00Z

/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.reconcile_broker_account reconcile \
  --snapshot examples/data/broker_account_snapshot_demo.json.example \
  --simulator-state .artifacts/broker_demo/paper-simulator.json \
  --as-of 2026-09-14T20:10:00Z
```

bootstrap 不覆盖已有状态；reconcile 完全只读，匹配返回 0，差异返回 2。输出恒有
`orders_submitted=0`。示例的 `source_kind=synthetic` 只能得到 `simulation_only`；真实适配器
可标记 `broker_observed`，但其含义仅为 `broker_export_contract_validated`，不是交易权限。
账户 ID 应使用脱敏稳定标识，快照和日志禁止保存访问令牌、账号密码或 API secret。

在账户快照与本地账本完全匹配后，可把已有 `order_plan.v1` 转成只供人工审核的文件：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.prepare_broker_orders \
  --plan .artifacts/current_order_plan.json \
  --snapshot .artifacts/current_broker_snapshot.json \
  --simulator-state .artifacts/paper-simulator.json \
  --symbol-map .artifacts/current_instrument_symbols.json \
  --generated-at 2026-09-14T20:10:00Z \
  --output .artifacts/pending_broker_orders.json
```

输出固定为 `pending_human_approval/manual_export_only` 且 `orders_submitted=0`。计划 hash、
账户 state hash、有效期、整数股数、累计卖出数量、限价现金购买力和每个 client order ID
都会复验。symbol map 必须来自该时点有效的 instrument master，并在人工录单前再次核对；
本命令不会打开券商连接，也没有“确认后自动发送”的隐藏路径。

人工在券商端录单后，先导入只读订单状态；只有券商已受理且未进入拒绝、撤销或过期终态
的订单才允许接收成交：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.import_broker_order_status \
  --package .artifacts/pending_broker_orders.json \
  --statuses .artifacts/current_broker_order_statuses.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of 2026-09-14T20:12:00Z
```

该入口只记录 `accepted/rejected/cancelled/expired` 券商回报，不会提交或撤销订单。随后可把
只读成交导出按 `broker_fill_batch.v2` 导入本地账本：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.import_broker_fills \
  --package .artifacts/pending_broker_orders.json \
  --fills .artifacts/current_broker_fills.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of 2026-09-14T20:20:00Z
```

`broker_fills.py` 会复验订单包的账户序列、execution/client order ID、方向、stable
instrument ID、symbol、整数股数、限价和双时间轴，并先在临时账本验证整批。重复
execution ID 不会重复入账；累计超量或订单包生成后夹杂其他账户事件时整批拒绝。输出
仍固定 `orders_submitted_by_system=0`。成交只增加交易应收/应付，不会伪造券商结算。

券商明确 bust/cancel 某笔成交时，使用 `import_broker_fill_corrections.py` 导入
`broker_fill_correction_batch.v1`。它只追加不可变 reversal，精确恢复尚未结算的持仓与
应收/应付，并重新开放对应订单数量；重复 correction 幂等。相关现金桶发生过结算后会
fail closed，因为当前聚合 settlement 不能可靠证明具体清偿了哪一笔 execution。

结算、分红和入出金使用独立的只读活动入口：

```bash
/home/mgy/miniconda3/envs/qlib/bin/python -m apps.quant_research.import_broker_activities \
  --activities .artifacts/current_broker_activities.json \
  --simulator-state .artifacts/paper-simulator.json \
  --as-of 2026-09-15T20:20:00Z
```

`broker_activities.py` 首版只接受 USD 的交易应收/应付结算、分红应计/到账、现金入金/出金。
未知活动类型拒绝导入，所有行先在临时账本验证，重复 activity ID 幂等，整批失败不留下
部分状态。只有账户初始快照和活动导出都为 `broker_observed` 时，能力才标记为
`broker_activity_contract_validated`；这仍不代表来源已认证或系统拥有交易权限。

新账户、订单、成交和现金活动契约使用 v2 canonical Decimal 字符串，避免 JSON 浮点往返
改变券商原始金额；v1 文件仅用于不可变历史证据兼容读取。v1→v2 会改变内容 ID，必须作为
新版本重新生成并重新绑定下游文件，禁止直接改 `schema_version`。

`examples/data/broker_*_demo.json.example` 组成一条固定合成演示链，并包含结算后的匹配账户
快照。`source_kind=synthetic` 始终只有 `simulation_only` 能力。

历史期权报告明确使用 `quote_mode=historical_bid_ask`。滚动报告在报价覆盖不足时返回
`quality_failed`。实物交割价差在价内长 Put 没有可交割股票时返回
`MISSING_DELIVERABLE_UNDERLYING`，不会假设现金结算。
