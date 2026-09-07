# research_signal.v1

`research_signal.v1` 是 TradingAgents 和 Qlib 之间的 JSON Lines 文件协议。每行是一条不可变研究记录；Qlib 可以在没有 TradingAgents、网络或 LLM 密钥的环境中重新导入同一文件并回放组合。

必填字段：`schema_version`、`signal_id`、`run_id`、`symbol`、`benchmark`、`analysis_as_of`、`generated_at`、`available_at`、`valid_until`、`rating`、`status`、`research_mode`。时间使用带时区的 ISO-8601 UTC 时间；`generated_at >= analysis_as_of`、`available_at >= generated_at`、`valid_until > available_at`。

`rating` 只能是 `Buy`、`Overweight`、`Hold`、`Underweight` 或 `Sell`。`status=completed` 的信号在 `available_at <= decision_time < valid_until` 才可参与决策。其他状态用于记录失败或不完整任务，不能隐式视为 Hold。

`research_mode=forward` 表示在当时运行并归档；`frozen_fixture` 只用于协议和回放测试；`retrospective` 表示事后重建，默认不能进入策略。历史回测必须保存当时可用数据、研究报告与真实入库时间，不能把今天生成的旧日期报告写成 forward 信号。

第一版组合规则为 long-only 风险覆盖层：Buy/Overweight 保留基础仓位，Hold 乘以 0.70，Underweight 乘以 0.35，Sell 乘以 0。减少的资金保留为现金，不重新放大其他股票权重。任何实验必须保存这条规则版本和基础候选池。
