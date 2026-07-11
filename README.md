# Stone AI Investment Manager Pro V12

Stone AI Investment Manager 是个人投资管理和投资日报系统。它只做数据读取、质量审计、资产配置分析、候选建议、报告生成和 Gmail 邮件提醒。

系统不自动交易，不接券商下单权限，不承诺收益；所有内容仅供投资辅助，不构成投资建议，最终操作必须由用户人工确认。

## 正式入口

本项目的唯一正式运行入口：

```bash
python src/main.py
```

根目录 `main.py` 只保留为提示文件，不再运行投资系统。GitHub Actions 也必须调用 `python src/main.py`。

## 每日自动运行

GitHub Actions 文件：

```text
.github/workflows/daily.yml
```

运行规则：

- 每天北京时间 8:30 自动运行。
- cron：`30 0 * * *`
- 支持手动运行 `workflow_dispatch`。
- 使用 Python 3.11。
- 先运行服务健康检查。
- 再运行自动测试。
- 最后运行 `python src/main.py`。
- 上传 `reports/` 为 artifact。
- 邮件发送今日摘要和完整报告附件。

## 每日生成文件

```text
reports/today_action.md
reports/daily_report.md
reports/weekly_report.md
reports/monthly_report.md
reports/system_check_report.md
reports/service_health.md
reports/validation_report.md
reports/project_audit.md
reports/decision.json
```

其中 `reports/decision.json` 是统一决策对象。日报、周报、月报、邮件正文都从它生成，避免互相矛盾。

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

服务健康检查：

```bash
python scripts/check_all_services.py
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

正式运行：

```bash
python src/main.py
```

测试 Gmail：

```bash
python scripts/test_email.py
```

## 数据源

系统按分层和降级方式使用数据：

- FRED：宏观主数据源。
- Alpha Vantage：美股、ETF、外汇和技术数据备份源。
- Finnhub：全球股票、ETF、新闻和基本面备份源。
- CBOE：VIX 官方参考。
- yfinance：免费兜底行情源。
- 本地缓存和 `data/market_data.csv`：所有在线数据失败时兜底。

如果缺少 API Key，系统会降级运行，但会降低 DQS，并在报告里写明数据覆盖不足。

## DQS 硬门槛

- DQS >= 90：允许精确金额。
- DQS 80-89：只允许金额上限或区间。
- DQS 70-79：只允许方向，不给具体金额。
- DQS < 70：不得给交易建议。
- `blocking_errors` 非空：停止执行单。

## Gmail 配置

本地可创建 `.env`，GitHub Actions 必须使用 GitHub Secrets。不要提交 `.env`。

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=你的Gmail邮箱
SMTP_PASSWORD=你的Gmail应用专用密码
EMAIL_TO=shitou3263063@gmail.com
```

Gmail 需要开启两步验证，然后创建“应用专用密码”。`SMTP_PASSWORD` 填应用专用密码，不是 Gmail 登录密码。

## GitHub Secrets

必需：

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

可选 LLM 备用：

```text
GEMINI_API_KEY
ANTHROPIC_API_KEY
DEEPSEEK_API_KEY
QWEN_API_KEY
OLLAMA_BASE_URL
LLM_PROVIDER_PRIORITY
ALLOW_RULE_ONLY_MODE
MAX_LLM_RETRIES
```

## 手动运行 GitHub Actions

1. 打开 GitHub 仓库。
2. 点击 `Actions`。
3. 选择 `Daily Stone AI Investment Report`。
4. 点击 `Run workflow`。
5. 分支选择 `main`。
6. 运行完成后查看 `Artifacts`。
7. 检查 Gmail 是否收到邮件。

## 资产数据

持仓文件：

```text
data/portfolio.csv
```

系统兼容常见字段名：

- 名称：`Asset`、`asset`、`标的`、`名称`、`name`
- 代码：`Symbol`、`symbol`、`ticker`、`代码`
- 类别：`Category`、`category`、`类型`、`资产类别`
- 金额：`Amount`、`amount`、`amount_wan`、`amount_cny`、`市值`、`金额`

用户确认的主资产台账优先级高于市场数据、新闻和邮件。

## 安全要求

- 不提交 `.env`。
- 不提交 SMTP 授权码。
- 不提交 OpenAI API Key。
- 不提交任何真实 API Key。
- 不自动交易。
- 不接券商下单权限。
- 不承诺收益。
- Gmail 只作为报告通知渠道，不作为资产事实或成交事实来源。
