# Stone AI Investment Manager Pro V12.7.1 Final Freeze 投资日报

- 报告业务日期：2026-07-19
- 运行模式：重新运行
- 决策截止时间：2026-07-19T16:29:59+08:00
- 历史成交日期：2026-07-15
- FinalDecisionBundle：`6cf8c188155731269d51bec9fb5991537da4feac9f22397421fd64b6006df1ee`

## 今日总决策

- 今日是否操作：不执行真实交易
- 是否定投：否
- 是否主动加仓：否
- 是否调仓：只进行资产偏离评估，不执行调仓
- 是否暂停：否；继续监控与评估
- 最主要的三条原因：
  1. 当前不在计划执行窗口
  2. 事件数据仅作软警告：事件数据不足，不能静默通过
  3. opportunity_dqs=63低于门槛85

## 今日场景决策

| 场景 | 使用DQS | 得分/门槛 | 风险门槛 | 数据状态 | 最终权限 | 硬阻断原因 | 软警告 | 下一步动作 |
|---|---|---:|---:|---|---|---|---|---|
| Scheduled DCA | core_dqs | 75/65 | 56/70 | event=DATA_INSUFFICIENT; comparability=PASS | DENY | 当前不在计划执行窗口 | 事件数据仅作软警告：事件数据不足，不能静默通过 | 满足硬阻断条件后重新评估 |
| Opportunity Add | opportunity_dqs | 63/85 | 56/50 | event=DATA_INSUFFICIENT; comparability=PASS | DENY | opportunity_dqs=63低于门槛85；风险分数56高于场景上限50；事件数据硬阻断：事件数据不足，不能静默通过 | 无 | 满足硬阻断条件后重新评估 |
| Strategic Rebalance | rebalance_dqs | 100/75 | 56/70 | event=DATA_INSUFFICIENT; comparability=PASS | ALLOW_EVALUATION_ONLY | 无 | 事件数据不足，不影响偏离评估：事件数据不足，不能静默通过；仅输出资产偏离与修复方向，不生成即时成交指令 | 输出偏离与修复优先级，不生成成交指令 |
| Grid Trading | grid_dqs | 100/85 | 56/50 | event=DATA_INSUFFICIENT; comparability=PASS | ALLOW_SIMULATION_ONLY | 实盘限制：实盘网格现金为0；风险分数56高于实盘上限50；实盘事件门槛未通过：事件数据不足，不能静默通过 | 事件数据不足仅阻断实盘，不阻断数据完整的模拟评估；Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离 | 仅记录模拟信号，保持实盘隔离 |
| Risk Monitoring | core_dqs | 75/1 | 56/100 | event=DATA_INSUFFICIENT; comparability=PASS | PARTIAL_MONITORING | 无 | 事件数据不足，仅降低监控完整度：事件数据不足，不能静默通过 | 继续监控可用指标并复核缺失项 |
| Transaction Reconciliation | execution_dqs | 100/100 | 56/100 | event=DATA_INSUFFICIENT; comparability=PASS | PASS | 无 | 无 | 对账通过，无需事件数据复核 |

## 资产偏离表

| 资产类别 | 当前金额 | 当前占比 | 目标占比 | 偏离百分点 | 偏离等级 | 修复优先级 | 修复方式 |
|---|---:|---:|---:|---:|---|---:|---|
| 美股 | 339,664.00 | 12.04% | 30.00% | -17.96 | 严重低配 | 90 | 维持现有配置 |
| 债券 | 1,155,000.00 | 40.93% | 25.00% | +15.93 | 严重超配 | 56 | 维持现有配置 |
| 黄金 | 547,000.00 | 19.39% | 15.00% | +4.39 | 接近目标 | 15 | 维持现有配置 |
| 港股 | 272,600.00 | 9.66% | 12.00% | -2.34 | 接近目标 | 7 | 维持现有配置 |
| A股 | 266,500.00 | 9.44% | 10.00% | -0.56 | 接近目标 | 2 | 维持现有配置 |
| 现金 | 241,000.00 | 8.54% | 8.00% | +0.54 | 接近目标 | 2 | 维持现有配置 |

## 风险分解

| 风险因子 | 原始值/依据 | 子分数 | 权重 | 对总风险分贡献 | 缺失指标处理 |
|---|---|---:|---:|---:|---|
| 估值 | 估值数据不完整时按中性偏高风险处理。 | 10 | 20% | 10 | 不适用（数据有效） |
| 波动率 | VIX=18.77；最大高风险/单股占比约2.4%。 | 5 | 15% | 5 | 不适用（数据有效） |
| 利率 | 美国10年期收益率最新官方值为4.57%，观察日期2026-07-16；属于官方滞后日度数据，不代表报告生成时的实时收益率；风险评分已降低时效性置信度。 | 13 | 15% | 13 | 不适用（数据有效） |
| 宏观事件 | 未来7天暂无高等级事件。 | 5 | 15% | 5 | 不适用（数据有效） |
| 趋势 | VOO与QQQ在2026-07-17口径一致，当日变化合计约-2.51%。 | 8 | 10% | 8 | 不适用（数据有效） |
| 政策与地缘 | 按中性偏谨慎处理；黄金占比19.4%，超配提高组合对避险行情反转的敏感度。 | 7 | 10% | 7 | 不适用（数据有效） |
| 市场宽度与资金流 | 市场宽度与ETF资金流缺少可核验风险值，按中性风险处理并降低置信度；15%权重仍完整保留。 | 8 | 15% | 8 | 不适用（数据有效） |
| **合计** | 置信度：low | **56** | **100%** | **56** | - |

## 数据质量评分

- core_dqs: **75**
- execution_dqs: **100**
- grid_dqs: **100**
- opportunity_dqs: **63**
- rebalance_dqs: **100**

### core_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 |
|---|---:|---:|---|---|
| 核心价格 | 25 | 25 | 可用 | 无 |
| 现金口径 | 25 | 25 | 可用 | 无 |
| 预算状态 | 25 | 25 | 可用 | 无 |
| 事件状态 | 0 | 25 | DATA_INSUFFICIENT | 无 |

最终求和：25 + 25 + 25 + 0 = **75**

### execution_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 |
|---|---:|---:|---|---|
| 成交、现金、汇率与持仓对账 | 100 | 100 | 由已确认交易标准字段现场计算。 | 无 |

最终求和：100 = **100**

### grid_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 |
|---|---:|---:|---|---|
| VOO finalized close snapshot | 50 | 50 | Official or previous official close; freshness is assessed independently. | 无 |
| QQQ finalized close snapshot | 50 | 50 | Official or previous official close; freshness is assessed independently. | 无 |

最终求和：50 + 50 = **100**

### opportunity_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 |
|---|---:|---:|---|---|
| field_completeness | 20 | 20 | 核心行情与宏观可用15/15 | 无 |
| timeliness | 13 | 15 | 新鲜且非STALE数据13/15 | 无 |
| source_quality | 4 | 15 | 一级来源4/15 | 无 |
| dual_source_validation | 1 | 15 | 双源验证1/15 | 无 |
| valuation_readiness | 15 | 15 | 持仓估值可用于配置口径 | 无 |
| transaction_reconciliation_quality | 10 | 10 | 1/1笔实盘交易已完成对账 | 无 |
| consistency | 10 | 10 | 无异常0值或严重冲突 | 无 |
| released_macro_event_data_quality | -10 | 0 | Auditable deduction for released macro events missing factual data. | 无 |

最终求和：20 + 13 + 4 + 1 + 15 + 10 + 10 + -10 = **63**

### rebalance_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 |
|---|---:|---:|---|---|
| 目标配置完整性 | 40 | 40 | 目标权重合计必须为100% | 无 |
| 持仓时效 | 30 | 30 | 使用统一PortfolioSnapshot | 无 |
| 核心市场覆盖 | 30 | 30 | 核心行情可用11/11 | 无 |

最终求和：40 + 30 + 30 = **100**

## 统一真实资产快照

- 精确估值资产：2,821,763.85 元
- 待估值成本记录：0.00 元（不进入精确市值和配置占比）
- 包含待估值成本记录的非精确总额：2,821,763.85 元
- 账户现金：241,000.00 元
- 固定安全储备：220,000.00 元
- 专项可投资现金：21,000.00 元

### 最终持仓（每个 security_id 仅一行）

| security_id | 数量 | 市值（CNY） | 资产分类 |
|---|---:|---:|---|
| VOO | 30.166 | 139,663.85 | 美股 |
| NVDA | 51.0 | 68,000.00 | 美股 |
| GOOG | 9.0 | 22,000.00 | 美股 |
| TLT | 95.0 | 55,000.00 | 债券 |
| IBKR | 64.0 | 40,000.00 | 美股 |
| XLF | 112.0 | 42,000.00 | 美股 |
| BABA | 44.0 | 28,000.00 | 美股 |
| HK_03033 | 35200.0 | 140,400.00 | 港股 |
| HK_513060 | 177600.0 | 99,000.00 | 港股 |
| HK_513090 | 17000.0 | 33,200.00 | 港股 |
| CN_510300 | 42900.0 | 206,000.00 | A股 |
| CN_002558 | 1500.0 | 41,000.00 | A股 |
| CN_ST_WENTAI | 500.0 | 19,500.00 | A股 |
| CN_BOND_CORE | - | 967,800.00 | 债券 |
| CN_LOCAL_BOND_10Y | 1100.0 | 132,200.00 | 债券 |
| GOLD_BAR_565G | 565.0 | 512,000.00 | 黄金 |
| GOLD_518880 | 4000.0 | 35,000.00 | 黄金 |
| CASH_CNY | - | 241,000.00 | 现金 |

## 事件与数据状态

- 事件状态：DATA_INSUFFICIENT
- 事件结论：由各场景依赖矩阵独立解释，不作为全局总开关。

## 警告明细

警告总数：**20**

| warning_id | severity | scope | message | affected_scenarios | is_hard_block | recommended_action |
|---|---|---|---|---|---|---|
| ISSUE-01790210DE | WARN | grid | 风险分数56高于实盘上限50 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-0421B303C9 | WARN | grid | 事件数据不足仅阻断实盘，不阻断数据完整的模拟评估 | grid | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-0FB546B3E5 | WARN | scheduled_dca | 事件数据仅作软警告：事件数据不足，不能静默通过 | scheduled_dca | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-143E221AE3 | WARN | opportunity_add | Put/Call Ratio: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-14FE53A5F5 | WARN | opportunity_add | 市场宽度: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-366337498C | WARN | opportunity_add | AAII情绪: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-366BD4E7F2 | WARN | opportunity_add | ETF资金流: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-5297D769E9 | WARN | scheduled_dca | 当前不在计划执行窗口 | scheduled_dca | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-5CAD17E392 | WARN | opportunity_add | 风险分数56高于场景上限50 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-6746D3242C | WARN | opportunity_add | 事件数据硬阻断：事件数据不足，不能静默通过 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-76EC3F6606 | WARN | strategic_rebalance | 事件数据不足，不影响偏离评估：事件数据不足，不能静默通过 | strategic_rebalance | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-87DDEC42EA | WARN | strategic_rebalance | 仅输出资产偏离与修复方向，不生成即时成交指令 | strategic_rebalance | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-96FA3FCD16 | WARN | risk_monitoring | 市场风险置信度为low | risk_monitoring | 否 | 等待下一次数据刷新并复核 |
| ISSUE-9DD95FD872 | WARN | SYSTEM | 关键行情或宏观数据存在过期项。 | SYSTEM | 否 | 等待下一次数据刷新并复核 |
| ISSUE-A45700B061 | WARN | grid | Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离 | grid | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-C7501EAF80 | WARN | grid | 实盘网格现金为0 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-C9D560B70A | WARN | SYSTEM | 存在过期数据：UNRATE, GDP。 | SYSTEM | 否 | 等待下一次数据刷新并复核 |
| ISSUE-D2FDFDBB63 | WARN | risk_monitoring | 事件数据不足，仅降低监控完整度：事件数据不足，不能静默通过 | risk_monitoring | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-D6F5FE2531 | WARN | grid | 实盘事件门槛未通过：事件数据不足，不能静默通过 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-F891DD458E | WARN | opportunity_add | opportunity_dqs=63低于门槛85 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |

## 阻断明细

阻断总数：**7**

| warning_id | severity | scope | message | affected_scenarios | is_hard_block | recommended_action |
|---|---|---|---|---|---|---|
| ISSUE-01790210DE | WARN | grid | 风险分数56高于实盘上限50 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-5297D769E9 | WARN | scheduled_dca | 当前不在计划执行窗口 | scheduled_dca | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-5CAD17E392 | WARN | opportunity_add | 风险分数56高于场景上限50 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-6746D3242C | WARN | opportunity_add | 事件数据硬阻断：事件数据不足，不能静默通过 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-C7501EAF80 | WARN | grid | 实盘网格现金为0 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-D6F5FE2531 | WARN | grid | 实盘事件门槛未通过：事件数据不足，不能静默通过 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-F891DD458E | WARN | opportunity_add | opportunity_dqs=63低于门槛85 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |

一致性警告总数：**2**

## 下一执行窗口

- 下一次 Scheduled DCA 日期：2026-08-05T08:30:00+08:00
- 下一次需要复核的数据：事件数据仅作软警告：事件数据不足，不能静默通过
- 可执行条件：下一个基础定投复核日：2026-08-05；core_dqs<65禁止，65–74仅减额复核，>=75进入正常定投评估。；本月债券资金已到账，剩余专项现金21,000元可分批评估，但不代表必须立即或一次性投入。；若主要指数回撤约5%且DQS>=85，优先评估VOO/QQQ和沪深300ETF小额分批。；若DQS低于60或关键价格缺失，继续禁止新增仓位建议。
- 预计档位：待下一窗口按当时事件与风险状态复核正常或减额档位
- 本报告不预测具体市场涨跌点位。

## 附录：统一快照引用

主报告快照哈希：`6cf8c188155731269d51bec9fb5991537da4feac9f22397421fd64b6006df1ee`
附录快照哈希：`6cf8c188155731269d51bec9fb5991537da4feac9f22397421fd64b6006df1ee`

Smart Grid 为 SIMULATION_ONLY；模拟资金、持仓和盈亏不进入真实资产与正式交易建议。系统不自动交易，所有执行均需人工确认。
