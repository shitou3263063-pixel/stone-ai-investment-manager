# Stone AI A股与港股 P0 数据修复报告

生成时间：2026-07-14（Asia/Shanghai）

## 结论

P0 修复通过。`03033.HK` 已成为南方东英恒生科技指数ETF的唯一真实持仓代码；`3033.HK` 仅作为 Yahoo Finance 对同一证券去除前导零后的供应商代码；`3067.HK` 是独立的 iShares 恒生科技ETF，与 `03033.HK` 不存在替代、代理或回退关系。

本次联网验证中，五个目标持仓均获得 2026-07-14 的有效行情。A股 P0 数据完整度为 100.0%，港股及港股主题基金 P0 数据完整度为 100.0%。该完整度只衡量本轮七项基础门槛，不代表估值、财务、市场宽度和资金流等 P1/P2 数据已经接通。

## 03033 错误映射根因

1. `data/security_master.yaml` 曾把 `3067.HK` 同时写入 `03033` 的 aliases 和 pricing_proxy。
2. `src/data_sources/data_router.py` 同时抓取 `3067.HK` 与 `3033.HK`，但没有区分“供应商代码格式”与“另一只证券”。
3. `src/decision/v12_1_decision.py` 的 Opportunity Score 直接硬编码 `3067.HK`，导致真实持仓 `03033` 的评分使用了另一只 ETF 的行情。

## 修复范围

- 证券主表：拆分 `HK_03033` 与 `HK_03067`，删除双方全部替代关系。
- 持仓事实：真实代码统一为 `03033.HK`，正式名称统一为“南方东英恒生科技指数ETF”。
- 供应商代码：仅 yfinance 请求时将 `03033.HK` 规范化为同证券代码 `3033.HK`，返回结果仍保存为 `03033.HK`，并记录 `provider_symbol`。
- 行情覆盖：将 `002558.SZ`、`513060.SS`、`513090.SS`、`510300.SS`、`03033.HK` 纳入统一市场快照。
- 元数据：为目标标的统一记录正式名称、交易所、市场、币种、市场时区、市场日期、获取时间、来源、状态、新鲜度和备用源状态。
- 数据门槛：新增 `cn_data_completeness` 与 `hk_data_completeness`；低于60%限制高置信度买入，低于40%标记低可信度。
- 决策约束：`DATA_INSUFFICIENT`、`SYMBOL_MAPPING_ERROR`、`DATA_VALIDATION_FAILED` 均不能形成加仓类建议，也不能被 AI 解释覆盖。
- 报告：新增 A股与港股专项完整度表，并在 Opportunity Score 中显示完整度、可信度、数据状态和缺失字段。

## 实际行情验收

| 真实标的 | 正式名称 | 交易所 | 币种 | 时区 | 市场日期 | 成功来源 | 状态 |
| -- | -- | -- | -- | -- | -- | -- | -- |
| 510300.SS | 沪深300ETF | SSE | CNY | Asia/Shanghai | 2026-07-14 | yfinance（备用源） | VALID |
| 002558.SZ | 巨人网络 | SZSE | CNY | Asia/Shanghai | 2026-07-14 | yfinance（备用源） | VALID |
| 03033.HK | 南方东英恒生科技指数ETF | HKEX | HKD | Asia/Hong_Kong | 2026-07-14 | yfinance，供应商代码3033.HK（备用源） | VALID |
| 513060.SS | 恒生医疗ETF | SSE | CNY | Asia/Shanghai | 2026-07-14 | yfinance（备用源） | VALID |
| 513090.SS | 香港证券ETF | SSE | CNY | Asia/Shanghai | 2026-07-14 | yfinance（备用源） | VALID |

当前五个基础行情均不缺失。Finnhub 在本地验收环境未配置，因此 yfinance 被明确记录为备用源，未伪造双源验证。

## 数据完整度与评分可信度

- A股 P0 数据完整度：100.0%。
- 港股及港股主题基金 P0 数据完整度：100.0%。
- 映射验证：PASS。
- 当前完整日报 DQS：70，仍只允许方向性建议。
- 仍不具备高可信度的评分维度：A/H 股个股基本面、指数/个股估值、市场宽度、ETF资金流、互联互通资金、融资融券、卖空比例和盈利预测。
- 因此，P0 修复解决了“识别错标的”和“基础行情缺失”问题，但不会把单一 yfinance 行情包装成完整投资证据。

## 币种与汇率处理

`03033.HK` 的交易币种为 HKD，市场时区为 Asia/Hong_Kong。当前组合市值继续使用用户确认的 CNY 资产快照；该持仓明确标记 `exchange_rate: null`、`market_value_original_currency: CNY` 和 `fx_status: not_applied_user_confirmed_cny`。系统不会在缺少汇率时静默按 1:1 换算。

## 历史影响

- 本次运行后的 `daily_snapshot.json`、`source_audit.json`、日报和 Opportunity Score 已使用正确代码。
- 旧历史报告中若曾出现 `3067.HK` 作为 `03033` 代理，其历史结论不会被静默改写。
- 日常运行不强制重算历史评分；若未来进行 Opportunity Score 时间序列比较，应单独重算受影响日期并标注修订版本。

## 修改文件

- `data/security_master.yaml`
- `data/portfolio_master.yaml`
- `data/portfolio.csv`
- `config/settings.yaml`
- `config/strategy.yaml`
- `config/source_registry.yaml`
- `src/portfolio_snapshot.py`
- `src/data_sources/data_router.py`
- `src/data_sources/source_audit.py`
- `src/decision/v12_1_decision.py`
- `src/analysis/cross_asset_engine.py`
- `src/strategy/dca_engine.py`
- `src/reports/report_center.py`
- `src/system/health_check.py`
- `agents/report_agent.py`（仅修正旧展示列表，不改变正式入口）
- `tests/test_cn_hk_p0.py`
- `tests/test_v12_1_stable.py`
- `tests/test_v12_5_stable.py`
- `tests/test_v12_6_1_consistency.py`

## 测试结果

执行命令：

```text
python -m pytest -q
```

结果：160 passed in 20.84s。

专项覆盖包括：03033/3067隔离、供应商代码规范化、A/H币种与时区、缺失值不变成0、禁止代理ETF替代、汇率缺失不按1:1、完整度门槛、Opportunity Score限制，以及既有美股/FRED回归测试。

## 验收文件

- `outputs/cn_hk_data_coverage.json`
- `outputs/cn_hk_p0_validation.json`
- `outputs/symbol_mapping_validation.json`

## P1 状态

P0 已满足开始 P1 的技术前提，但本轮未接入 Tushare、AKShare 或任何新依赖，也未实施 P1/P2。P1 若后续获批，优先补充稳定的第二行情源、估值、财务和市场宽度；这不会要求修改美股、FRED、黄金、债券、邮件或网格的现有逻辑。

## 最终判定

**PASS**：所有测试通过，`03033.HK` 映射验证通过，五个真实持仓基础行情有效，缺失与低完整度降级规则有效。系统不自动交易，目标配置、收益目标、再平衡阈值和正式入口均未改变。
