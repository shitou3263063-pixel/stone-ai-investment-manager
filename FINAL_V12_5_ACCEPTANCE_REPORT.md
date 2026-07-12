# Stone AI Investment Manager Pro V12.5 Stable 最终验收报告

## 1. 验收结论

- 核心系统结论：**PASS**
- 运行环境结论：**PASS_WITH_WARNINGS**
- 是否达到长期自动生成报告条件：**是**
- 是否建议冻结版本：**是**
- 是否自动交易：**否**
- 是否承诺收益：**否**
- 所有真实操作是否仍需人工确认：**是**

警告不来自投资计算或一致性错误，而来自本机外部服务状态：本地 Gmail SMTP 连接发生 SSL EOF；本地未配置 OpenAI API Key；本地 FRED 宏观数据未成功返回。系统已按设计保存完整报告、降低DQS并切换规则增强模式。

用户明确确认后，本轮代码已提交并推送到GitHub `main`。远程`main`已核对包含本次稳定版收尾代码。远程Actions尚未手动触发：本机没有`gh`命令，当前可控GitHub页面未登录，无法代替用户点击`Run workflow`。

## 2. 审计结果

### 唯一生产链路

```text
根目录 main.py
  -> src.app.main
  -> 每日统一快照
  -> Portfolio Snapshot
  -> DQS / 风险 / Opportunity / 预算 / 规则引擎
  -> 前置一致性校验
  -> OpenAI可选解释复核
  -> 二次一致性校验
  -> 报告 / 邮件
```

- 唯一正式入口：`main.py`
- 唯一运行命令：`python main.py`
- 核心业务入口：`src/app.py`
- GitHub Actions实际入口：`.github/workflows/daily.yml`中的`python main.py`
- 正式日报工作流数量：1
- 生产链路中的旧V4/V5/V10/V11入口：未发现
- 归档入口：`archive/legacy_entrypoints/run_deprecated.py`、`archive/legacy_entrypoints/src_main_deprecated.py`
- 归档入口未被import、未被Actions调用、未参与测试或报告。

### 唯一权威配置

| 内容 | 唯一权威来源 |
| -- | -- |
| 持仓、六大类金额、现金与黄金事实 | `data/portfolio_master.yaml` |
| 证券代码及代理行情映射 | `data/security_master.yaml` |
| 目标配置、现金、DQS、风险、定投、迁移上限 | `config/strategy.yaml` |
| 网格参数及模拟/实盘开关 | `config/smart_grid.yaml` |
| 数据源优先级及验证规则 | `config/source_registry.yaml` |
| 人工维护宏观事件 | `config/settings.yaml` |

`data/portfolio.csv`只保留为兼容镜像，不再作为生产决策事实源。目标配置已从旧兼容配置中移除，代码中不再保留第二份目标比例。

## 3. 正式目录结构

```text
main.py
.github/workflows/daily.yml
config/
  strategy.yaml
  settings.yaml
  smart_grid.yaml
  source_registry.yaml
data/
  portfolio_master.yaml
  security_master.yaml
  execution_state.json
  grid/
reports/
src/
  app.py
  ai/
  data_sources/
  decision/
  grid/
  macro/
  notifier/
  reports/
  strategy/
  validators/
tests/
archive/
```

## 4. 修改文件与内容

| 文件 | 修改内容 |
| -- | -- |
| `src/app.py` | 代理输入改由Portfolio Snapshot派生；规则裁决和前置校验完成后才调用OpenAI；增加二次校验及阶段日志；硬校验失败时主程序返回失败码。 |
| `src/portfolio_snapshot.py` | 增加source、last_confirmed_at、valuation_method、持仓年龄和滞后提示。 |
| `src/data_sources/data_router.py` | 统一数据点字段value、timestamp、source、source_level、status、stale、fallback_used；缺失值保持None。 |
| `src/decision/v12_1_decision.py` | 目标配置只从strategy.yaml读取；DQS纳入持仓新鲜度、汇率和事件日历置信度；风险依据补充现金、集中度、债券久期和黄金超配；加强持仓分类模板、情景预算及硬一致性校验。 |
| `src/ai/openai_advisor.py` | 改为规则裁决后的结构化JSON解释；最多重试2次；覆盖429、超时、认证、quota、非JSON和字段缺失回退；增加现金、DQS、事件和ST越权拒绝。 |
| `src/macro/macro_calendar.py` | 增加事件来源、时间、时区、确认状态；未确认日期显示“日期待确认”，不冒充确定事件。 |
| `src/reports/report_center.py` | 固定0至19节顺序；移除重复触发章节；新增Stone CIO Commentary；明确持仓快照、DQS降级和OpenAI回退。 |
| `src/reports/grid_report.py` | 保持参数不变；补充模拟总资金、储备、标的分配、四舍五入和真实现金隔离说明。 |
| `src/strategy/rebalance_engine.py` | 默认目标配置改为读取唯一权威`config/strategy.yaml`。 |
| `src/system/health_check.py` | 分开检查事件兼容配置和生产策略配置；缺失生产策略时ERROR，不自动编造目标比例。 |
| `src/validators/decision_validator.py` | 统一DQS阈值为60/75/85；验证报告使用PASS、PASS_WITH_WARNINGS、FAILED_VALIDATION及真实时间。 |
| `.github/workflows/daily.yml` | 保持唯一入口和北京时间8:30；增加concurrency避免重复并发。 |
| `.env.example` | 只保留实际使用的OpenAI、数据源和邮件变量；最大重试固定为2；不含真实密钥。 |
| `requirements.txt` | 为四个实际依赖增加稳定上限范围。 |
| `config/settings.yaml` | 事件增加来源、时区和confirmed字段；移除重复目标配置。 |
| `data/config.yaml` | 移除重复目标配置，保留旧代理兼容参数。 |
| `README.md` | 明确唯一入口、唯一资产/策略事实源、OpenAI结构化复核和事件确认纪律。 |
| `tests/test_v12_5_final_hardening.py` | 新增最终硬化测试。 |
| 既有测试文件 | 更新日报固定章节编号的回归断言。 |

本轮没有新增V13、平行主程序、付费数据源、自动交易、企业微信或新网格参数。

## 5. 修复问题

1. 消除旧CSV和Portfolio Snapshot同时作为生产输入的风险。
2. 消除目标配置在三个YAML及Python默认值中的重复维护。
3. OpenAI不再在规则裁决前产生意见，也不能覆盖现金、DQS、预算、事件、ST和网格硬规则。
4. OpenAI 429、quota、超时、网络、认证、非JSON和字段缺失均可回退。
5. 日报“下一触发条件”只保留一个正式章节。
6. 持仓诊断按核心ETF、行业ETF、个股、ST、债券、实物黄金、黄金ETF和现金分别处理。
7. 风险评分依据不再只显示VIX和利率，同时解释现金安全、集中度、久期和黄金仓位。
8. 宏观事件未确认时不再显示为确定事实。
9. 网格模拟资金的总额、储备、标的分配和真实现金隔离关系已解释。
10. FAILED_VALIDATION会阻断真实建议并让Actions返回失败；邮件和OpenAI非核心故障只产生警告。

## 6. 核心规则复核

- 目标配置未改变：美股30%、港股12%、A股10%、债券25%、黄金15%、现金8%。
- DQS门槛未改变：低于60禁止新增；60至74只给方向；75至84只给区间/分批；不低于85才可能给较具体金额。
- 现金安全储备不得投资；未到账债券资金和模拟网格资金不得进入真实现金。
- 基础定投、机会加仓、债券迁移、风险减仓、网格预算继续使用独立budget_id。
- 黄金超配暂停常规新增；债券超配按到账条件渐进迁移；TLT暂停新增并关注久期。
- 个股不因美股低配自动加仓；ST股票永久禁止自动新增。
- Smart Grid仍为SIMULATION，`auto_trade=false`，实盘预算为0。
- 所有真实操作仍需人工确认。

## 7. 测试证据

执行命令：

```text
python -m pytest -q
```

最终结果：

```text
119 passed in 19.42s
```

- 测试总数：119
- 通过：119
- 失败：0

关键新增或复核结果：

| 测试项 | 结果 |
| -- | -- |
| 总资产、分类金额、持仓明细三方相等 | PASS |
| 目标占比合计100% | PASS |
| 可投资现金为0时真实金额为0 | PASS |
| 未到账债券资金排除 | PASS |
| 模拟网格现金隔离、实盘预算为0 | PASS |
| DQS四档精度门槛 | PASS |
| 缺失数据不显示为0 | PASS |
| 数据源回退与缓存 | PASS |
| 周末/非交易时段 | PASS |
| 黄金、债券超配阻止新增 | PASS |
| 美股低配不触发个股自动加仓 | PASS |
| ST自动买入阻断 | PASS |
| OpenAI正常结构化JSON | PASS（模拟响应） |
| OpenAI 429、超时、非JSON、字段缺失 | PASS（回退） |
| OpenAI违反现金、DQS或ST规则 | PASS（拒绝并回退） |
| 网格核心仓、预算、状态和模拟隔离 | PASS |
| 日报固定顺序及单一触发章节 | PASS |
| 邮件固定主题、正文和四附件MIME | PASS（模拟SMTP） |
| 邮件失败不删除报告 | PASS |
| GitHub Actions唯一入口与并发锁 | PASS（静态及本地等价流程） |

## 8. 实际运行证据

执行命令：

```text
python main.py
```

实际结果摘要：

```text
总资产：2,821,100元
六类金额合计：2,821,100元
持仓明细合计：2,821,100元
目标占比合计：100%
账户总现金：220,000元
现金安全储备：225,688元
可投资现金：0元
今日是否交易：否
今日真实金额：0元
DQS：48（禁止新增仓位建议）
风险评分：58（中高风险）
Smart Grid：SIMULATION
网格实盘可用现金：0元
一致性验证：PASS
```

报告文件：

- 完整新日报样例：`reports/daily_report.md`
- 一页今日执行单：`reports/today_action.md`
- 最近有效周报：`reports/weekly_report.md`
- 机器可读状态：`reports/run_status.json`
- 统一决策对象：`reports/decision.json`
- 日志：`logs/stone_ai.log`

## 9. GitHub Actions验收

- 工作流路径：`.github/workflows/daily.yml`
- 工作流数量：1
- cron：`30 0 * * *`，即北京时间08:30
- 手动触发：已保留`workflow_dispatch`
- Python：3.11
- 唯一入口：`python main.py`
- 测试在日报之前运行：是
- 并发防重：是
- OpenAI失败不阻断规则日报：是
- 报告和日志artifact：是
- 硬一致性失败返回非零状态：是
- 邮件失败仍保留artifact：是

本轮代码已经推送到远程`main`。远程GitHub Actions尚未运行该提交，原因是当前环境无法通过已登录会话触发`workflow_dispatch`；本报告不会将本地静态检查冒充远程运行成功。

## 10. OpenAI验收

- 本机实际状态：未配置`OPENAI_API_KEY`。
- 实际运行模式：SAFE_MODE / Stone CIO规则增强分析。
- 规则评论完整：是。
- “当前没有真实可执行买入预算”明确显示：是。
- 结构化JSON、正常返回、429、超时、非JSON、字段缺失、现金越权、DQS越权和ST越权均已通过自动测试。
- OpenAI只负责解释；规则和二次校验拥有最终否决权。

## 11. 邮件验收

- 固定主题：`Stone AI CIO Daily - 10%-15% Target`
- 固定附件：`today_action.md`、`daily_report.md`、`weekly_report.md`、`run_status.json`
- 四附件编码及MIME自动测试：PASS
- 邮件失败时报告保留：PASS
- 本机实际发送结果：**FAILED**
- 错误：`SSL: UNEXPECTED_EOF_WHILE_READING`
- `reports/run_status.json`已记录`email_status=failed`和完整错误。

精简邮件正文样例：

```text
报告日期：2026-07-12
数据截止时间：2026-07-12T15:08:43
今日是否执行：否
标的和金额：不适用 / 0元
可投资现金：0元
下一复核日期：2026-07-15
DQS：48
是否存在警告或错误：是
```

本机SMTP失败是外部网络/TLS环境问题，不影响报告计算和保存；在远程Actions实际成功前，不能宣称本轮邮件生产推送已通过。

## 12. 仍存在但不影响核心运行的限制

1. 本机FRED数据未成功返回，DGS10、CPI、失业率和GDP缺失，DQS已降至48并禁止新增仓位建议。
2. 本机OpenAI Key未配置；系统使用完整规则增强分析，不影响核心风控。
3. 本机Gmail SMTP发生SSL EOF；报告保存正常，远程Actions邮件仍需本轮代码推送后实测。
4. 事件日历当前为人工维护且日期未获官方确认，日报已显示“日期待确认”。
5. IBKR仍未接通，系统不会声称已验证账户现金、保证金或成交记录，也不会自动更新已执行状态。
6. Smart Grid模拟期未满20个有效交易日前，不对策略效果下结论。
7. GitHub远程`main`已更新，但仍需在Actions页面手动运行一次，确认远程Secrets、邮件和artifact。

## 13. 最终判断

Stone AI Investment Manager Pro V12.5 Stable 已达到资产、预算、DQS、风险、规则、报告和模拟网格长期稳定运行条件，建议冻结代码并停止功能扩展。

当前最终状态为：**核心系统PASS，生产通知PASS_WITH_WARNINGS**。代码已经推送；只需在GitHub Actions手动运行一次确认远程邮件，这属于部署验收，不需要继续修改投资策略代码。
