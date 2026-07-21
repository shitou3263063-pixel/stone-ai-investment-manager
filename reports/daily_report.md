# Stone AI Investment Manager Pro V12.7.1 Final Freeze 投资日报

- 报告业务日期：2026-07-21
- 运行模式：重新运行
- 决策截止时间：2026-07-21T01:55:57+08:00
- 历史成交日期：2026-07-15
- FinalDecisionBundle：`8d531f9dfcc94fedcfa283255a4af7e058fe7e0430eb2a6c132e3e17182ac8fd`

## 今日总决策

- 今日是否操作：不执行真实交易
- 是否定投：否
- 是否主动加仓：否
- 是否调仓：只进行资产偏离评估，不执行调仓
- 是否暂停：否；继续监控与评估
- 最主要的三条原因：
  1. 当前不在计划执行窗口
  2. opportunity_dqs=65低于门槛85
  3. 风险分数53高于场景上限50

## 今日场景决策

| 场景 | 使用DQS | 得分/门槛 | 风险门槛 | 数据状态 | 最终权限 | 硬阻断原因 | 软警告 | 下一步动作 |
|---|---|---:|---:|---|---|---|---|---|
| Scheduled DCA | core_dqs | 100/65 | 53/70 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | DENY | 当前不在计划执行窗口 | 无 | 满足硬阻断条件后重新评估 |
| Opportunity Add | opportunity_dqs | 65/85 | 53/50 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | DENY | opportunity_dqs=65低于门槛85；风险分数53高于场景上限50 | 无 | 满足硬阻断条件后重新评估 |
| Strategic Rebalance | rebalance_dqs | 94/75 | 53/70 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | ALLOW_EVALUATION_ONLY | 无 | 仅输出资产偏离与修复方向，不生成即时成交指令 | 输出偏离与修复优先级，不生成成交指令 |
| Grid Trading | grid_dqs | 0/85 | 53/50 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=BLOCK | DENY | grid_dqs=0低于模拟信号门槛85；grid_snapshot_comparability=DATA_NOT_COMPARABLE；实盘限制：实盘网格现金为0；风险分数53高于实盘上限50 | Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离 | 满足硬阻断条件后重新评估 |
| Risk Monitoring | core_dqs | 100/1 | 53/100 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | ACTIVE | 无 | 无 | 持续监控并更新风险明细 |
| Transaction Reconciliation | execution_dqs | 100/100 | 53/100 | event=VALID_NO_HIGH_IMPACT_EVENT; comparability=PASS | PASS | 无 | 无 | 对账通过，无需事件数据复核 |

## 资产偏离表

| 资产类别 | 当前金额 | 当前占比 | 目标占比 | 偏离百分点 | 偏离等级 | 修复优先级 | 修复方式 |
|---|---:|---:|---:|---:|---|---:|---|
| 债券 | 1,155,000.00 | 44.81% | 25.00% | +19.81 | 严重超配 | 69 | 暂停新增，通过新增权益资金逐步稀释，不默认强制卖出 |
| 美股 | 330,000.00 | 12.80% | 30.00% | -17.20 | 严重低配 | 86 | 新增资金优先修复，优先宽基ETF，不强制一次性完成 |
| 现金 | 21,000.00 | 0.81% | 8.00% | -7.19 | 低配 | 36 | 优先使用新增资金 |
| 黄金 | 547,000.00 | 21.22% | 15.00% | +6.22 | 超配 | 22 | 暂停新增，观察后续偏离 |
| 港股 | 263,048.00 | 10.21% | 12.00% | -1.79 | 接近目标 | 5 | 维持现有配置 |
| A股 | 261,338.00 | 10.14% | 10.00% | +0.14 | 接近目标 | 0 | 维持现有配置 |

## 风险分解

| 风险因子 | 依据 | 风险得分 | 该项最高分 | 对总风险贡献 | 缺失处理 |
|---|---|---:|---:|---:|---|
| 估值 | 估值数据不完整时按中性偏高风险处理。 | 10 | 20 | 10点 | 数据缺失，按中性风险计分并降低置信度。 |
| 波动率 | VIX=18.15；最大高风险/单股占比约2.6%。 | 5 | 15 | 5点 | 数据有效，按现有值计分。 |
| 利率 | 美国10年期收益率最新官方值为4.57%，观察日期2026-07-16；属于官方滞后日度数据，不代表报告生成时的实时收益率；风险评分已降低时效性置信度。 | 13 | 15 | 13点 | 数据有效，按现有值计分。 |
| 宏观事件 | 未来7天暂无高等级事件。 | 5 | 15 | 5点 | 数据有效，按现有值计分。 |
| 趋势 | VOO（宽基）0.19%×70% + QQQ（成长风格）0.75%×30% = 加权趋势0.36%；高度相关ETF只计入一次市场趋势。 | 5 | 10 | 5点 | 数据有效，按现有值计分。 |
| 政策与地缘 | 按中性偏谨慎处理；黄金占比21.2%，超配提高组合对避险行情反转的敏感度。 | 7 | 10 | 7点 | 数据有效，按现有值计分。 |
| 市场宽度与资金流 | 市场宽度与ETF资金流缺少可核验风险值，按中性风险处理并降低置信度；15%权重仍完整保留。 | 8 | 15 | 8点 | 数据缺失，按中性风险计分并降低置信度。 |
| **合计** | 置信度：low | **53** | **100** | **53点** | - |

## 数据质量评分

- core_dqs: **100**
- execution_dqs: **100**
- grid_dqs: **0**
- opportunity_dqs: **65**
- rebalance_dqs: **94**

### core_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 | 数据源 | 最后成功时间 | 降分项 |
|---|---:|---:|---|---|---|---|---:|
| 核心价格 | 25 | 25 | 可用 | 无 | yfinance | 2026-07-20T17:53:25+00:00 | 0 |
| 现金口径 | 25 | 25 | 可用 | 无 | user_confirmed | 2026-07-15 | 0 |
| 预算状态 | 25 | 25 | 可用 | 无 | execution_state | 2026-07-15 | 0 |
| 事件状态 | 25 | 25 | 可用 | 无 | EconomicCalendar | 无成功记录 | 0 |

最终求和：25 + 25 + 25 + 25 = **100**

### execution_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 | 数据源 | 最后成功时间 | 降分项 |
|---|---:|---:|---|---|---|---|---:|
| 成交、现金、汇率与持仓对账 | 100 | 100 | 由已确认交易标准字段现场计算。 | 无 | 不适用 | 不适用 | 0 |

最终求和：100 = **100**

### grid_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 | 数据源 | 最后成功时间 | 降分项 |
|---|---:|---:|---|---|---|---|---:|
| VOO finalized close snapshot | 0 | 50 | Official or previous official close; freshness is assessed independently. | 无 | 不适用 | 不适用 | -50 |
| QQQ finalized close snapshot | 0 | 50 | Official or previous official close; freshness is assessed independently. | 无 | 不适用 | 不适用 | -50 |

最终求和：0 + 0 = **0**

### opportunity_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 | 数据源 | 最后成功时间 | 降分项 |
|---|---:|---:|---|---|---|---|---:|
| field_completeness | 20 | 20 | 核心行情与宏观可用15/15 | 无 | MarketSnapshot | 2026-07-20T17:55:57.025191+00:00 | 0 |
| timeliness | 12 | 15 | 新鲜且非STALE数据12/15 | DGS10；UNRATE；GDP | MarketSnapshot | 2026-07-20T17:55:57.025191+00:00 | -3 |
| source_quality | 4 | 15 | 一级来源4/15 | 无 | 不适用 | 不适用 | -11 |
| dual_source_validation | 1 | 15 | 双源验证1/15 | 无 | 不适用 | 不适用 | -14 |
| valuation_readiness | 12 | 15 | 精确估值覆盖率79.02%，按覆盖率计分 | None；VOO:fx_rate；NVDA:fx_rate；GOOG:fx_rate；TLT:fx_rate；IBKR:fx_rate；XLF:fx_rate；BABA:fx_rate；HK_03033:fx_rate；CN_ST_WENTAI:price；GOLD_518880:price | PortfolioSnapshot.valuation_audit | 2026-07-21T01:55:57+08:00 | -3 |
| transaction_reconciliation_quality | 10 | 10 | 1/1笔实盘交易已完成对账 | 无 | 不适用 | 不适用 | 0 |
| consistency | 10 | 10 | 无异常0值或严重冲突 | 无 | 不适用 | 不适用 | 0 |

普通评分小计：20 + 12 + 4 + 1 + 12 + 10 + 10 = **69**

#### 审计扣分项

| 审计项 | 扣分 | 满分 | 审计原因 |
|---|---:|---:|---|
| released_macro_event_data_quality | -4 | 0 | 已发布宏观数据的抓取失败或非核心字段不完整，仅影响发布数据质量，不污染未来事件门控。 |

最终得分：普通评分小计 69 + 审计扣分 -4 = **65**

### rebalance_dqs

| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 | 数据源 | 最后成功时间 | 降分项 |
|---|---:|---:|---|---|---|---|---:|
| 目标配置完整性 | 40 | 40 | 目标权重合计必须为100% | 无 | 不适用 | 不适用 | 0 |
| 持仓时效 | 24 | 30 | 按精确估值市值覆盖率79.02%计分 | VOO:fx_rate；NVDA:fx_rate；GOOG:fx_rate；TLT:fx_rate；IBKR:fx_rate；XLF:fx_rate；BABA:fx_rate；HK_03033:fx_rate；CN_ST_WENTAI:price；GOLD_518880:price | PortfolioSnapshot.valuation_audit | 2026-07-21T01:55:57+08:00 | -6 |
| 核心市场覆盖 | 30 | 30 | 核心行情可用11/11 | 无 | 不适用 | 不适用 | 0 |

最终求和：40 + 24 + 30 = **94**

## 统一真实资产快照

- household_total_assets_estimated：2,806,385.90 元
- investable_assets_estimated：2,586,385.90 元
- household_safety_reserve：220,000.00 元（不进入可投资组合分母）
- portfolio_cash：21,000.00 元
- precise_valued_assets：2,217,485.90 元
- stale_valued_assets：579,900.00 元
- unvalued_cost_records：9,000.00 元
- valuation_coverage_ratio：79.02%
- 精确估值资产：2,217,485.90 元
- 待估值成本记录：9,000.00 元（不进入精确市值和配置占比）
- 包含待估值成本记录的非精确总额：2,806,385.90 元
- 估算总额说明：存在非精确估值时，以上总资产仅为 estimated total，不称为全部精确估值。
- 账户现金：241,000.00 元
- 固定安全储备：220,000.00 元
- 专项可投资现金：21,000.00 元

### 最终持仓（每个 security_id 仅一行）

| security_id | 数量 | price | currency | fx_rate | price_as_of | source | valuation_status | precise valuation | 市值（CNY） | 资产分类 |
|---|---:|---:|---|---:|---|---|---|---|---:|---|
| VOO | 30.166 | 684.4901 | USD | - | 2026-07-20T17:53:25+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 130,000.00 | 美股 |
| NVDA | 51.0 | 204.58 | USD | - | 2026-07-20T17:53:26+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 68,000.00 | 美股 |
| GOOG | 9.0 | 353.255 | USD | - | 2026-07-20T17:53:26+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 22,000.00 | 美股 |
| TLT | 95.0 | 83.8601 | USD | - | 2026-07-20T17:53:30+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 55,000.00 | 债券 |
| IBKR | 64.0 | 92.46 | USD | - | 2026-07-20T17:53:27+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 40,000.00 | 美股 |
| XLF | 112.0 | 56.115 | USD | - | 2026-07-20T17:53:27+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 42,000.00 | 美股 |
| BABA | 44.0 | 121.66 | USD | - | 2026-07-20T17:53:26+00:00 | yfinance | STALE_USER_CONFIRMED_VALUE | 否 | 28,000.00 | 美股 |
| HK_03033 | 35200.0 | 4.65 | HKD | - | 2026-07-20T08:00:00+00:00 | akshare:sina_finance | STALE_USER_CONFIRMED_VALUE | 否 | 140,400.00 | 港股 |
| HK_513060 | 177600.0 | 0.518 | CNY | 1.0 | 2026-07-17T07:00:00+00:00 | yfinance | VALUED_PREVIOUS_CLOSE | 是 | 91,996.80 | 港股 |
| HK_513090 | 17000.0 | 1.803 | CNY | 1.0 | 2026-07-17T07:00:00+00:00 | yfinance | VALUED_PREVIOUS_CLOSE | 是 | 30,651.00 | 港股 |
| CN_510300 | 42900.0 | 4.589 | CNY | 1.0 | 2026-07-17T07:00:00+00:00 | yfinance | VALUED_PREVIOUS_CLOSE | 是 | 196,868.10 | A股 |
| CN_002558 | 1500.0 | 29.98 | CNY | 1.0 | 2026-07-20T07:00:00+00:00 | akshare:eastmoney | VALUED_PREVIOUS_CLOSE | 是 | 44,970.00 | A股 |
| CN_ST_WENTAI | 500.0 | - | CNY | 1.0 | 2026-07-11 | user_confirmed_category_reconciled | STALE_USER_CONFIRMED_VALUE | 否 | 19,500.00 | A股 |
| CN_BOND_CORE | - | - | CNY | 1.0 | 2026-07-15 | user_confirmed | MANUAL_FIXED_VALUE | 是 | 967,800.00 | 债券 |
| CN_LOCAL_BOND_10Y | 1100.0 | - | CNY | 1.0 | 2026-07-11 | user_confirmed | MANUAL_FIXED_VALUE | 是 | 132,200.00 | 债券 |
| GOLD_BAR_565G | 565.0 | - | CNY | 1.0 | 2026-07-11 | user_confirmed_manual_override | MANUAL_FIXED_VALUE | 是 | 512,000.00 | 黄金 |
| GOLD_518880 | 4000.0 | - | CNY | 1.0 | 2026-07-11 | user_confirmed | STALE_USER_CONFIRMED_VALUE | 否 | 35,000.00 | 黄金 |
| CASH_CNY | - | - | CNY | 1.0 | 2026-07-15 | user_confirmed | MANUAL_FIXED_VALUE | 是 | 241,000.00 | 现金 |

## 事件与数据状态

- 事件状态：VALID_NO_HIGH_IMPACT_EVENT
- 事件覆盖结论：事件覆盖有效，未来7天未发现高等级事件。
- 场景解释：由各场景依赖矩阵独立解释，不作为全局总开关。

- position_level_event_risk：HIGH_RISK_EVENT_FOUND
- portfolio_level_event_risk：CLEAR
- future_event_gate：PASS（仅评估未来事件）
- released_data_quality：PARTIAL_DATA（仅影响DQS与置信度）
- 持仓事件：IBKR｜IBKR 2026年第二季度业绩｜2026-07-21T16:00:00-04:00（America/New_York）｜NOT_RELEASED

| 已发布事件 | 状态 | actual | previous | consensus | revision | 发布数据源 | as_of |
|---|---|---:|---:|---:|---:|---|---|
| CPI | PARTIAL_DATA | 332.568 | 333.979 | - | - | fred | 2026-07-20T17:55:51.523159+00:00 |
| PPI | PARTIAL_DATA | 286.827 | 290.489 | - | - | fred | 2026-07-20T17:55:52.922713+00:00 |

| 缺失项 | 具体缺失字段 | 数据源 | 最后成功时间 | 降分项 |
|---|---|---|---|---|
| CPI | consensus_value；revision | fred | 2026-07-20T17:55:51.523159+00:00 | opportunity_dqs.released_macro_event_data_quality |
| PPI | consensus_value；revision | fred | 2026-07-20T17:55:52.922713+00:00 | opportunity_dqs.released_macro_event_data_quality |

## 成交对账审计

- execution_dqs：**100**
- 成交对账总状态：**PASS**

| 交易日期 | 标的 | 交易前数量 | 成交数量 | 交易后数量 | 成交金额 | 费用 | 汇率 | 现金变化 | 对账状态 |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| 2026-07-15 | VOO | 28 | 2.166 | 30.166 | 1,499.955 USD（人民币等值记录9,000元） | 0 USD | 不适用（美元账户现金） | -1,499.955 USD | PASS |

## 警告明细

警告总数：**9**

| warning_id | severity | scope | message | affected_scenarios | is_hard_block | recommended_action |
|---|---|---|---|---|---|---|
| ISSUE-143E221AE3 | WARN | opportunity_add | Put/Call Ratio: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-14FE53A5F5 | WARN | opportunity_add | 市场宽度: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-1ECD2A4C09 | WARN | SYSTEM | 存在过期数据：DGS10, UNRATE, GDP。 | SYSTEM | 否 | 等待下一次数据刷新并复核 |
| ISSUE-366337498C | WARN | opportunity_add | AAII情绪: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-366BD4E7F2 | WARN | opportunity_add | ETF资金流: NOT_CONNECTED | opportunity_add | 否 | 等待下一次数据刷新并复核 |
| ISSUE-87DDEC42EA | WARN | strategic_rebalance | 仅输出资产偏离与修复方向，不生成即时成交指令 | strategic_rebalance | 否 | 按场景下一步动作复核，不自动交易 |
| ISSUE-96FA3FCD16 | WARN | risk_monitoring | 市场风险置信度为low | risk_monitoring | 否 | 等待下一次数据刷新并复核 |
| ISSUE-9DD95FD872 | WARN | SYSTEM | 关键行情或宏观数据存在过期项。 | SYSTEM | 否 | 等待下一次数据刷新并复核 |
| ISSUE-A45700B061 | WARN | grid | Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离 | grid | 否 | 按场景下一步动作复核，不自动交易 |

## 阻断明细

阻断总数：**9**

| warning_id | severity | scope | message | affected_scenarios | is_hard_block | recommended_action |
|---|---|---|---|---|---|---|
| ISSUE-18A3FB433E | WARN | grid | 风险分数53高于实盘上限50 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-5297D769E9 | WARN | scheduled_dca | 当前不在计划执行窗口 | scheduled_dca | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-690CE941BD | WARN | grid | grid_dqs=0低于模拟信号门槛85 | grid | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-6A8EB12F95 | WARN | strategic_rebalance | VOO待估值：PENDING_VALUATION | strategic_rebalance | 是 | 取得有效收盘价与独立估值汇率后自动重算 |
| ISSUE-86CF7F92BB | WARN | grid | grid_snapshot_comparability=DATA_NOT_COMPARABLE | grid | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-AA9EA0BADE | WARN | grid_snapshot_comparability | grid_snapshot_comparability=DATA_NOT_COMPARABLE | grid | 是 | 等待下一次数据刷新并复核 |
| ISSUE-C7501EAF80 | WARN | grid | 实盘网格现金为0 | grid:live | 是 | 保持模拟与实盘隔离；满足实盘条件后再人工复核 |
| ISSUE-D407CC5F81 | WARN | opportunity_add | opportunity_dqs=65低于门槛85 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |
| ISSUE-E02C8B17A9 | WARN | opportunity_add | 风险分数53高于场景上限50 | opportunity_add | 是 | 满足该场景硬门槛后重新评估 |

一致性警告总数：**2**

## 下一执行窗口

- DCA cadence：每月两次（每月第1、3个周三；节假日顺延至下一有效交易日）
- 周度规则说明：同一自然周最多执行一次是频率上限，不代表每周定投。
- 上一次执行日期：2026-07-15
- 下一次理论执行日期：2026-08-05
- 跳过中间日期的原因：第2、4、5周的周三不属于当前每月第1、3周执行计划，因此不是漏执行。
- 下一次需要复核的数据：核心行情、持仓、现金、风险与事件数据
- 可执行条件：下一个基础定投复核日：2026-08-05；core_dqs<65禁止，65–74仅减额复核，>=75进入正常定投评估。；本月债券资金已到账，剩余专项现金21,000元可分批评估，但不代表必须立即或一次性投入。；若主要指数回撤约5%且DQS>=85，优先评估VOO/QQQ和沪深300ETF小额分批。；若DQS低于60或关键价格缺失，继续禁止新增仓位建议。
- 预计档位：待下一窗口按当时事件与风险状态复核正常或减额档位
- 本报告不预测具体市场涨跌点位。

## 附录：统一快照引用

主报告快照哈希：`8d531f9dfcc94fedcfa283255a4af7e058fe7e0430eb2a6c132e3e17182ac8fd`
附录快照哈希：`8d531f9dfcc94fedcfa283255a4af7e058fe7e0430eb2a6c132e3e17182ac8fd`

Smart Grid 为 SIMULATION_ONLY；模拟资金、持仓和盈亏不进入真实资产与正式交易建议。系统不自动交易，所有执行均需人工确认。
