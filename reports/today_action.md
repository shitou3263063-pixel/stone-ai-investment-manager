# 今日决策卡
Bundle: `8d531f9dfcc94fedcfa283255a4af7e058fe7e0430eb2a6c132e3e17182ac8fd`

- 今日是否操作：不执行真实交易
- 是否定投：否
- 是否主动加仓：否
- 是否调仓：只进行资产偏离评估，不执行调仓
- 是否暂停：否；继续监控与评估
- 最主要的三条原因：
  1. 当前不在计划执行窗口
  2. opportunity_dqs=65低于门槛85
  3. 风险分数53高于场景上限50

| 场景 | 使用DQS | 得分/门槛 | 风险门槛 | 数据状态 | 最终权限 | 硬阻断原因 | 软警告 | 下一步动作 |
|---|---|---:|---:|---|---|---|---|---|
| Scheduled DCA | core_dqs | 100/65 | 53/70 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | DENY | 当前不在计划执行窗口 | 无 | 满足硬阻断条件后重新评估 |
| Opportunity Add | opportunity_dqs | 65/85 | 53/50 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | DENY | opportunity_dqs=65低于门槛85；风险分数53高于场景上限50 | 无 | 满足硬阻断条件后重新评估 |
| Strategic Rebalance | rebalance_dqs | 94/75 | 53/70 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | ALLOW_EVALUATION_ONLY | 无 | 仅输出资产偏离与修复方向，不生成即时成交指令 | 输出偏离与修复优先级，不生成成交指令 |
| Grid Trading | grid_dqs | 0/85 | 53/50 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=BLOCK | DENY | grid_dqs=0低于模拟信号门槛85；grid_snapshot_comparability=DATA_NOT_COMPARABLE；实盘限制：实盘网格现金为0；风险分数53高于实盘上限50 | Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离 | 满足硬阻断条件后重新评估 |
| Risk Monitoring | core_dqs | 100/1 | 53/100 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | ACTIVE | 无 | 无 | 持续监控并更新风险明细 |
| Transaction Reconciliation | execution_dqs | 100/100 | 53/100 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | PASS | 无 | 无 | 对账通过，无需事件数据复核 |
