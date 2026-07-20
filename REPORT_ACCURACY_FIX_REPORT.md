# Stone AI V12.7.1 日报准确性修复报告

## 修复范围

本次为最小范围的数据准确性与稳定性修复，不修改投资策略、DQS门槛、Risk Score、Opportunity Score、交易门控或自动交易边界。

### 已修复问题

1. 修复 `src/decision/v12_1_decision.py` 因手工粘贴造成的缩进错误。
2. 美东盘前报告按 `America/New_York` 计算业务日期；北京时间报告按 `Asia/Shanghai` 计算业务日期。
3. 保留原始 `report_generated_at` 时区表达，仅将本地化时间用于业务日期，避免破坏数据截止时间语义。
4. 修复 `src/domain/event_assessment.py` 因手工粘贴造成的变量未定义、缩进和 `return outside function` 错误。
5. 宏观事件日历增加 `verified_event_coverage` 与 `last_success_at`，避免“状态有效但无成功记录”的自相矛盾。
6. 事件覆盖不完整、日历缺项或明确未验证时，不允许静默判定为 `VALID_NO_HIGH_IMPACT_EVENT`。
7. 已发布事件的数据抓取失败不污染未来7天事件门控，保持既有冻结逻辑。
8. 日报新增“估值回退警告”，明确静态/过期/缺价格/缺汇率估值不属于精确估值。
9. 增加业务日期、事件覆盖、估值精度回归测试。

## 验证结果

- Python 编译检查：通过
- GitHub Actions YAML 解析：通过
- 完整测试：`419 passed`
- 唯一生产入口仍为：`python main.py`

## 未修改内容

- 投资策略
- DQS评分门槛
- Risk Score逻辑
- Opportunity Score逻辑
- 场景交易门控
- Smart Grid的 `SIMULATION_ONLY` 边界
- 人工确认与不自动交易约束
