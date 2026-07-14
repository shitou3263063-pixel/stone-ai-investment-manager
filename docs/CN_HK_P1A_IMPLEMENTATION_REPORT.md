# Stone AI A股与港股 P1A 故障定位与最小修复报告

## 1. 结论

本轮只修复 P1A 的 Tushare 故障可观测性、独立接口状态、下一复核时间和 UTF-8 验证。未重复 P0，未进入 P1B，未修改正式入口、目标配置、交易金额规则、美股、FRED、邮件或网格业务逻辑。

Tushare 的真实失败根因是账户接口权限不足。2026-07-14 23:58 的实际官方响应分别指出 `trade_cal`、`daily_basic`、`index_dailybasic`、`fina_indicator`、`income`、`balancesheet` 和 `cashflow` 均“没有接口访问权限”。这说明请求已到达官方接口，失败不由 Python 版本、JSON结构、SSL 或超时造成。

系统现在把该类错误明确记录为 `PERMISSION_DENIED`。若官方响应明确提到积分不足，则单独记录为 `INSUFFICIENT_POINTS`。二者不再合并为笼统的 `failed`。

## 2. 调用方式与兼容性

- 传输方式：直接调用 Tushare 官方 REST API `https://api.tushare.pro`。
- 是否使用官方 Python SDK：否。
- 依赖：Python 标准库 `urllib`、`json`、`ssl`，未依赖未声明的 `tushare` 包。
- `requirements.txt`：无需新增 Tushare SDK 依赖。
- Python 3.14.6：当前实现没有使用已移除或不兼容接口；直接 REST 方式不受 Tushare SDK 对 Python 3.14 支持状态影响。
- 凭证：只读取环境变量 `TUSHARE_TOKEN`，日志和报告不会输出 Token 原文。

## 3. 错误分类

已实现并测试以下稳定错误代码：

- `TOKEN_INVALID`
- `PERMISSION_DENIED`
- `INSUFFICIENT_POINTS`
- `RATE_LIMITED`
- `NETWORK_TIMEOUT`
- `SSL_ERROR`
- `RESPONSE_SCHEMA_ERROR`
- `EMPTY_RESPONSE`
- `UNKNOWN_ERROR`

错误摘要会移除 Token、Authorization 和 API Key 等敏感内容。主源失败时仍允许显式使用七天内缓存，但会保留上游失败代码、缓存年龄和 `fallback_used=true`，不会静默替换。

## 4. 接口独立状态

以下接口现在各自保存 API 名称、状态、数据日期或报告期、行数、字段状态、错误代码和脱敏摘要：

| 数据 | Tushare接口 | 当前真实结论 | 是否进入评分 |
| -- | -- | -- | -- |
| 上交所交易日历 | `trade_cal` | `PERMISSION_DENIED` | 否，仅用于日期校验 |
| 002558估值 | `daily_basic` | `PERMISSION_DENIED` | 否 |
| 002558财务 | `fina_indicator`、`income`、`balancesheet`、`cashflow` | `PERMISSION_DENIED` | 否 |
| 沪深300指数估值 | `index_dailybasic` | `PERMISSION_DENIED` | 否 |

指定诊断窗口 `exchange=SSE`、`start_date=20260701`、`end_date=20260715` 已纳入调用参数测试。当前 Codex 本地进程无法读取 GitHub Secrets，因此本地复跑显示 Token 未配置；GitHub 运行留下的官方权限响应用于判定真实根因。没有复制、显示或导出 Secret。

## 5. Token与权限判断

- Token读取：用户的 GitHub Actions 运行已确认 `tushare_configured=true` 且长度为56；系统不保存或显示原文。
- Token有效性：官方返回进入接口权限检查，而不是 Token 无效错误，因此认证链路有效。
- 权限/积分：当前账户不具备上述接口访问权限。官方文本没有明确写“积分不足”，程序保守归类为 `PERMISSION_DENIED`；若后续返回明确积分提示，会归类为 `INSUFFICIENT_POINTS`。
- 在权限开通前，Tushare数据不会进入 Opportunity Score，也不会填0或用价格分位替代估值分位。

## 6. 运行状态输出

`reports/run_status.json` 的 `cn_hk_p1a` 已增加：

- `tushare_status`
- `tushare_error_code`
- `tushare_error_summary`
- `tushare_trade_calendar_status`
- `tushare_002558_valuation_status`
- `tushare_002558_fundamental_status`
- `tushare_csi300_valuation_status`
- `tushare_last_success_at`

`outputs/cn_hk_p1a_validation.json` 同步输出这些机器可读字段。各接口失败互不覆盖。

## 7. 下一复核时间

已修复 `next_review_date` 早于 `run_time` 的问题：

1. 所有带时区候选时间先转换到 `Asia/Shanghai` 比较。
2. 已经过期的宏观事件复核时间会被丢弃。
3. 优先选择未来的宏观事件、A股下一开市日或基础定投复核日。
4. 没有可靠候选时，滚动到下一个工作日 08:30。
5. 一致性校验新增硬检查，禁止 `next_review_date <= run_time`。

本次运行：

- `run_time`: `2026-07-15T00:12:02+08:00`
- `next_review_date`: `2026-07-15T20:30:00+08:00`
- 校验结果：严格晚于运行时间。

## 8. 最新数据状态

本地完整运行的结果：

- A股整体分析完整度：45%。P0基础行情可用；交易日历、002558估值、002558财务、沪深300估值及本次A股公告请求不可用。
- 港股整体分析完整度：65%。P0行情和HKMA银行体系总结余可用；HIBOR、港元汇率过期，HKEX公告未形成可验证记录。
- DQS：65，模式为“只允许方向性建议”。
- 高置信度加仓：仍受限制。
- 实际进入评分的P1A数据：新鲜的 HKMA 银行体系总结余。
- 未进入评分：全部 Tushare 数据、过期 HIBOR、过期港元汇率和未核验公告。

上述完整度会随外部数据成功状态动态变化，不通过降低过期标准提高分数。

## 9. WARN与编码

- GDP过期和关键行情/宏观数据过期继续如实显示。
- 邮件超时只影响通知状态，不影响投资计算和报告保存。
- Tushare错误单独显示，不再混入其他数据源错误。
- 一致性 WARN 保留具体原因。
- `reports/run_status.json`、日报和全部 P1A JSON 均使用 UTF-8。
- 机器验证已对五个目标 JSON 执行 UTF-8 解码和 `json.loads`，全部通过；终端字体或 PowerShell代码页显示乱码不代表文件损坏。

## 10. 修改文件

- `src/data_sources/tushare_client.py`
- `src/data_sources/cn_hk_p1a.py`
- `src/decision/v12_1_decision.py`
- `src/reports/report_center.py`
- `tests/test_cn_hk_p1a.py`
- `tests/test_v12_6_1_consistency.py`
- `docs/CN_HK_P1A_IMPLEMENTATION_REPORT.md`

运行过程同时更新：

- `reports/run_status.json`
- `reports/daily_report.md`
- `outputs/cn_hk_p1a_validation.json`
- `outputs/cn_hk_fundamental_snapshot.json`
- `outputs/cn_hk_valuation_snapshot.json`
- `outputs/cn_hk_scoring_trace.json`

## 11. 测试

- P1A专项测试：20项通过。
- 全量测试：180项通过，0失败。
- 主程序：成功完成，报告正常生成。
- UTF-8与JSON解析：通过。
- 下一复核时间回归：通过。
- 邮件：本地连接超时，已记录 WARN，不影响报告。

结论：P1A最小修复完成。当前阻塞Tushare真实数据激活的是账户接口权限，而不是程序调用链或Python兼容性；在权限开通前，系统继续限制A股高置信度建议，不自动交易。
