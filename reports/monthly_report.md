# Monthly Review
FinalDecisionBundle: `6cf8c188155731269d51bec9fb5991537da4feac9f22397421fd64b6006df1ee`
本报告只引用本次运行的统一决策包，不重新计算资产、DQS或权限。

| 场景 | 使用DQS | 得分/门槛 | 风险门槛 | 数据状态 | 最终权限 | 硬阻断原因 | 软警告 | 下一步动作 |
|---|---|---:|---:|---|---|---|---|---|
| Scheduled DCA | core_dqs | 75/65 | 56/70 | event=DATA_INSUFFICIENT; comparability=PASS | DENY | 当前不在计划执行窗口 | 事件数据仅作软警告：事件数据不足，不能静默通过 | 满足硬阻断条件后重新评估 |
| Opportunity Add | opportunity_dqs | 63/85 | 56/50 | event=DATA_INSUFFICIENT; comparability=PASS | DENY | opportunity_dqs=63低于门槛85；风险分数56高于场景上限50；事件数据硬阻断：事件数据不足，不能静默通过 | 无 | 满足硬阻断条件后重新评估 |
| Strategic Rebalance | rebalance_dqs | 100/75 | 56/70 | event=DATA_INSUFFICIENT; comparability=PASS | ALLOW_EVALUATION_ONLY | 无 | 事件数据不足，不影响偏离评估：事件数据不足，不能静默通过；仅输出资产偏离与修复方向，不生成即时成交指令 | 输出偏离与修复优先级，不生成成交指令 |
| Grid Trading | grid_dqs | 100/85 | 56/50 | event=DATA_INSUFFICIENT; comparability=PASS | ALLOW_SIMULATION_ONLY | 实盘限制：实盘网格现金为0；风险分数56高于实盘上限50；实盘事件门槛未通过：事件数据不足，不能静默通过 | 事件数据不足仅阻断实盘，不阻断数据完整的模拟评估；Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离 | 仅记录模拟信号，保持实盘隔离 |
| Risk Monitoring | core_dqs | 75/1 | 56/100 | event=DATA_INSUFFICIENT; comparability=PASS | PARTIAL_MONITORING | 无 | 事件数据不足，仅降低监控完整度：事件数据不足，不能静默通过 | 继续监控可用指标并复核缺失项 |
| Transaction Reconciliation | execution_dqs | 100/100 | 56/100 | event=DATA_INSUFFICIENT; comparability=PASS | PASS | 无 | 无 | 对账通过，无需事件数据复核 |
