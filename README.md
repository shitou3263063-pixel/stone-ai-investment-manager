# Stone AI Investment Manager Pro V12.5 Stable

Stone AI Investment Manager 是个人投资管理和投资日报系统。它负责读取资产、获取市场和宏观数据、计算资产偏离、执行风控规则、生成候选建议、调用 AI 做解释复核，并通过 Gmail 发送日报。

V12.5 Stable 是在 V12.2 Smart Grid 基础上的最终稳定版收尾升级，重点修复资产口径、现金口径、黄金对账、Opportunity Score持仓映射和一致性验证。网格模块默认只做模拟运行，不自动下单，不占用真实现金，不修改真实持仓。

系统不自动交易，不接券商下单权限，不使用杠杆，不做空，不使用期权，不承诺收益。所有内容仅供投资辅助，不构成投资建议，最终操作必须由用户人工确认。

## 唯一正式入口

本地运行、GitHub Actions 和定时任务都只调用同一个入口：

```bash
python main.py
```

根目录 `main.py` 只负责启动系统，核心业务逻辑在 `src/app.py` 和 `src/` 下的业务模块中。历史入口已经移动到 `archive/`，不再被正式流程调用。

禁止生产运行：`archive/legacy_entrypoints/run_deprecated.py` 和 `archive/legacy_entrypoints/src_main_deprecated.py`。这些文件只用于历史追溯。

## 生产配置权威来源

生产配置统一标记为 `config_version: V12.5_STABLE`。每类数据只有一个权威来源：

| 数据 | 权威来源 | 说明 |
| -- | -- | -- |
| 用户持仓、六大类资产金额、现金事实 | `data/portfolio_master.yaml` | 最高优先级资产事实；`portfolio.csv`仅为兼容读取镜像 |
| 目标配置、现金安全线、DQS、风险阈值、定投与债券迁移上限 | `config/strategy.yaml` | Stone CIO最终决策唯一策略配置 |
| 网格模拟账户与网格风控 | `config/smart_grid.yaml` | 默认SIMULATION，`auto_trade`必须为`false` |
| 数据源优先级与验证要求 | `config/source_registry.yaml` | 未登记来源不得形成交易结论 |
| 宏观事件和旧代理兼容输入 | `config/settings.yaml`、`data/config.yaml` | 兼容输入，不得覆盖上述权威配置 |

目标配置冻结为：美股30%、港股12%、A股10%、债券25%、黄金15%、现金8%。修改持仓时只维护`portfolio_master.yaml`，不要在Python代码中改数字。

## 每天自动运行

GitHub Actions 文件：

```text
.github/workflows/daily.yml
```

运行规则：

- 每天北京时间 8:30 自动运行。
- cron：`30 0 * * *`。
- 支持手动运行 `workflow_dispatch`。
- 使用 Python 3.11。
- 安装 `requirements.txt`。
- 运行服务健康检查、自动测试和 `python main.py`。
- 上传 `reports/` 和 `logs/` 为 artifact。
- 报告生成成功后发送 Gmail，邮件失败不影响报告保存。

## 手动运行 GitHub Actions

1. 打开 GitHub 仓库。
2. 点击 `Actions`。
3. 选择 `Daily Stone AI Investment Report`。
4. 点击 `Run workflow`。
5. 分支选择 `main`。
6. 运行完成后查看 `Artifacts`。
7. 检查 Gmail 是否收到日报。

## 本地运行

```bash
pip install -r requirements.txt
pytest
python main.py
```

常用检查：

```bash
python scripts/check_all_services.py
python scripts/final_check.py
python scripts/deploy_check.py
python scripts/test_email.py
```

## 每日生成文件

```text
reports/today_action.md
reports/daily_report.md
reports/weekly_report.md
reports/monthly_report.md
reports/grid_report.md
reports/grid_weekly_report.md
reports/grid_backtest_report.md
reports/system_check_report.md
reports/system_audit.md
reports/service_health.md
reports/validation_report.md
reports/project_audit.md
reports/decision.json
```

其中 `reports/decision.json` 是统一决策对象。今日行动、日报、周报、月报、网格报告和邮件正文都从同一份决策对象生成，避免互相矛盾。

## 智能网格模块

配置文件：

```text
config/smart_grid.yaml
```

默认状态：

```yaml
enabled: true
auto_trade: false
paper_mode: true
live_advice_enabled: false
```

含义：

- `paper_mode: true`：只模拟，不生成真实执行金额建议。
- `live_advice_enabled: false`：不输出实盘网格建议。
- `auto_trade: false`：永远不自动下单。

支持标的：

- VOO：标普500核心 ETF。
- QQQ：纳斯达克100网格交易标的。
- QQQM：保留为长期低费率替代配置，默认不与 QQQ 同时参与网格。

## VOO 默认网格参数

- 核心仓：75%
- 网格仓：25%
- 单标的网格资金上限：总资产 5%
- 正常波动买入间距：2.5% 至 3.5%
- 正常波动卖出间距：3.0% 至 4.0%
- 高波动间距：4.0% 至 6.0%
- 最大普通买入层级：5
- 单日最大交易次数：1

## QQQ 默认网格参数

- 核心仓：70%
- 网格仓：30%
- 单标的网格资金上限：总资产 4%
- 正常波动买入间距：3.5% 至 5.0%
- 正常波动卖出间距：4.0% 至 5.5%
- 高波动间距：5.0% 至 8.0%
- 最大普通买入层级：5
- 单日最大交易次数：1
- 科技集中度限制：18%

## 核心仓与网格仓隔离

核心仓用于长期持有，不参与普通网格卖出。

网格仓有独立状态：

```text
data/grid/grid_state.json
```

人工成交记录：

```text
data/grid/manual_trades.csv
```

只有 `manual_trades.csv` 中状态为 `confirmed` 的成交才会更新网格状态。`suggested`、`rejected`、`expired`、`cancelled` 都不会入账。

模拟信号记录：

```text
data/grid/simulation_trades.csv
```

## 网格资金规则

- 网格总资金默认使用总资产 4% 作为模拟预算。
- 网格总资金原则上不超过总资产 8%。
- 单标的不超过配置上限。
- 模拟模式不占用真实现金、基础定投资金、机会加仓资金或债券转权益预算。
- 现金安全线以内资金不得用于网格。
- 未到账债券赎回资金不得用于网格。

## 市场状态识别

网格模块综合以下指标识别市场状态：

- 20日、50日、200日均线
- 20日历史波动率
- VIX
- 近20日和60日最大回撤
- 当前价格与趋势位置

状态包括：

- 震荡：正常运行网格。
- 上升趋势：降低卖出频率，扩大卖出间距。
- 下降趋势：扩大买入间距，限制连续买入。
- 高波动/危机：暂停普通密集网格，降低单笔金额。

## 总风控否决

网格信号必须经过 Stone CIO 总风控。以下情况会否决：

- DQS 低于网格门槛。
- 核心行情数据缺失。
- 现金低于安全线。
- 网格预算不足。
- 未来48小时内存在高等级宏观事件。
- 科技集中度过高。
- QQQ 与 NVDA、GOOG 等科技持仓重叠过高。
- 卖出会触及核心仓。
- 美股仍严重低配时普通网格卖出。
- 交易成本和滑点后预期收益不足。

## 回测

回测报告：

```text
reports/grid_backtest_report.md
```

回测数据优先使用 Yahoo chart API 的复权收盘价，并缓存到：

```text
data/cache/grid_history_VOO.json
data/cache/grid_history_QQQ.json
```

如果数据源不可用，系统不会伪造历史行情，会在回测报告中明确写明数据覆盖不足。

回测比较：

- 买入并持有
- 固定网格
- 动态网格
- 核心仓加动态网格

输出指标包括收益、年化、最大回撤、波动率、夏普、卡玛、胜率、交易次数、已实现收益、超额收益和回撤改善。

## 关闭网格模块

编辑 `config/smart_grid.yaml`：

```yaml
smart_grid:
  enabled: false
```

关闭后主系统继续生成日报，网格章节显示未启用。

## 未来人工开启实盘建议模式

必须手动修改：

```yaml
smart_grid:
  auto_trade: false
  paper_mode: false
  live_advice_enabled: true
```

即使开启实盘建议：

- 仍然不自动下单。
- 仍然需要用户人工确认。
- 仍然受 DQS、现金安全线、总风控和重大事件过滤限制。

## 数据源

系统按分层和降级方式使用数据：

- FRED：宏观主数据源。
- Alpha Vantage：美股、ETF、外汇和技术数据来源。
- Finnhub：股票、ETF、公司新闻和基本面备用来源。
- CBOE：VIX 官方参考来源。
- yfinance：免费兜底行情源。
- Yahoo chart API：网格历史回测数据源。
- 本地缓存：`data/cache/`。
- 手工资产台账：`data/portfolio.csv` 和 `data/portfolio_master.yaml`。

## 环境变量与Secrets

邮件必需：

```text
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
EMAIL_TO
```

建议配置：

```text
OPENAI_API_KEY
FRED_API_KEY
ALPHA_VANTAGE_API_KEY
FINNHUB_API_KEY
```

OpenAI是可选解释层。未配置、额度不足或请求失败时，Stone CIO规则增强分析仍会完整生成，不影响DQS、资金预算、风控和邮件日报。

## 分支与版本冻结

- `main`：GitHub Actions当前生产部署分支，与冻结版本保持一致。
- `stable/v12.5`：V12.5稳定基线，只允许严重Bug、数据源接口失效和必要运行环境修复。
- `develop/v13`：未来功能的隔离开发起点，本次不实现任何V13功能。
- `feature/*`：单项功能分支，必须经过测试、日报生成、一致性验证、V12.5结果对比和人工确认后才能合并。

未经人工确认，不得把新功能合并到`stable/v12.5`。版本标签为`v12.5-stable`。

## 错误恢复

1. 先运行`pytest`和`python main.py`确认错误可复现。
2. 检查`reports/system_check_report.md`、`reports/validation_report.md`和`logs/stone_ai.log`。
3. 配置或数据错误时，按`CONFIG_GUIDE.md`修复，不要修改策略代码。
4. 严重代码故障时，可从`v12.5-stable`标签或`stable/v12.5`分支恢复，经测试后再部署。
5. 邮件或OpenAI失败不会阻止报告保存；先从GitHub Actions Artifact下载`reports/`和`logs/`排查。

## 上传到 GitHub

首次部署：

```bash
git init
git add .
git commit -m "Stone AI Investment Manager Pro V12.5 Stable"
git branch -M main
git remote add origin 你的GitHub仓库地址
git push -u origin main
```

日常更新：

```bash
git add .
git commit -m "Update Stone AI Investment Manager Pro V12.5 Stable"
git push origin main
```

## 安全要求

- 不提交 `.env`。
- 不提交 SMTP 授权码。
- 不提交 OpenAI API Key。
- 不提交任何真实 API Key。
- 不自动交易。
- 不接券商下单权限。
- 不承诺收益。
- Gmail 只作为报告通知渠道，不作为资产事实或成交事实来源。

## 免责声明

本系统输出仅供投资辅助和个人复盘使用，不构成投资建议、收益承诺或交易指令。所有买卖操作必须由用户自行判断和人工执行。
