# ARCHITECTURE

## 唯一正式入口

```text
main.py -> src.pipeline.unified_pipeline.main()
```

GitHub Actions、本地运行和测试均使用同一入口。

## 数据流

```mermaid
flowchart TD
  A["数据采集"] --> B["规范化与质量验证"]
  B --> C["MarketSnapshot"]
  C --> D["交易账本合并"]
  D --> E["PortfolioSnapshot"]
  E --> F["DQSResult 与 RiskSnapshot"]
  F --> G["EventAssessment"]
  G --> H["ScenarioDecision"]
  H --> I["FinalDecisionBundle"]
  I --> J["阻断式不变量校验"]
  J --> K["纯展示日报与附录"]
  K --> L["邮件发送"]
```

## 职责边界

- Codex负责数据抓取、清洗、资产台账、规则校验和候选建议。
- GPT/OpenAI只做解释、风险复核和冲突提示。
- AI不得覆盖DQS、现金安全线、资产配置、资金来源、网格风控和人工确认规则。
- 主报告、任务卡、场景表和附录只读取同一个 `FinalDecisionBundle`；展示层不得重新计算业务结论。

## 归档规则

旧入口已归档在 `archive/`，生产流程不得引用。
