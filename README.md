# Stone AI Investment Manager Pro V12

Stone AI Investment Manager 是个人投资管理系统，只做资产读取、市场分析、日报生成和 Gmail 邮件提醒。系统不自动交易，不接券商下单权限，不承诺收益；所有内容仅供投资辅助，不构成投资建议。

## 当前稳定目标

优先保证这条主链路每天稳定运行：

1. GitHub Actions 每天北京时间 8:30 自动运行成功。
2. Gmail SMTP 邮件推送可用。
3. 自动读取 `data/portfolio.csv`。
4. 自动获取市场数据，失败时回退到 `data/market_data.csv`。
5. 自动生成 `reports/daily_report.md`。
6. 自动输出定投、再平衡和风险评分。
7. 不开发视频、头条、内容工厂等无关功能。

## 核心产物

每天生成：

```text
reports/today_action.md
reports/daily_report.md
reports/weekly_report.md
reports/system_check_report.md
```

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

正式运行：

```bash
python run.py
```

兼容入口：

```bash
python main.py
```

测试 Gmail/SMTP 邮件：

```bash
python scripts/test_email.py
```

最终验收：

```bash
python scripts/final_check.py
```

部署前检查：

```bash
python scripts/deploy_check.py
```

## 持仓数据

编辑：

```text
data/portfolio.csv
```

推荐字段：

```text
category,name,amount_wan,currency,quantity,unit,note
```

说明：

- `amount_wan` 单位为万元人民币。
- 实物金条可填写 `quantity=565`、`unit=克`，系统会尝试按每日黄金价格自动估值。
- 如果无法获取实时金价，日报会单独列出 `565克金条，暂未估值`，程序不会报错。
- 如果存在未估值资产，系统会暂停比例驱动调仓，避免错误再平衡。

兼容字段名：

- 名称：`Asset`、`asset`、`标的`、`名称`、`name`
- 代码：`Symbol`、`symbol`、`ticker`、`代码`
- 类别：`Category`、`category`、`类型`、`资产类别`
- 金额：`Amount`、`amount`、`amount_wan`、`amount_cny`、`市值`、`金额`

## 市场数据

系统优先使用 `yfinance` 获取市场数据。如果获取失败，会继续使用手动数据：

```text
data/market_data.csv
```

行情失败不会中断日报生成。

## Gmail 邮件推送

GitHub Actions 必须使用 GitHub Secrets，不要提交 `.env`。本地测试可创建 `.env`。

本地 `.env` 示例：

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=你的Gmail邮箱
SMTP_PASSWORD=你的Gmail应用专用密码
EMAIL_TO=你的Gmail邮箱
```

Gmail 需要开启两步验证，然后创建“应用专用密码”。`SMTP_PASSWORD` 填应用专用密码，不是 Gmail 登录密码。

测试：

```bash
python scripts/test_email.py
```

未配置邮件时，系统只会 WARN 并跳过发送，不影响日报生成。

## OpenAI 深度总结

可选配置：

```text
OPENAI_API_KEY=你的OpenAI API Key
```

未配置时日报会显示 AI 深度分析未启用，基础分析仍可运行。

## GitHub Actions

工作流文件：

```text
.github/workflows/daily.yml
```

要求：

- 每天北京时间 8:30 自动运行。
- cron：`30 0 * * *`
- 支持 `workflow_dispatch` 手动运行。
- 使用 Python 3.11。
- 执行 `python main.py`。
- 上传 `reports/` 作为 artifact。
- 自动发送 `daily_report.md` 到 Gmail。

必须配置的 GitHub Secrets：

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=你的Gmail邮箱
SMTP_PASSWORD=你的Gmail应用专用密码
EMAIL_TO=你的Gmail邮箱
```

可选 Secrets：

```text
OPENAI_API_KEY
```

手动运行：

1. 打开 GitHub 仓库。
2. 点击 `Actions`。
3. 选择 `Daily Stone AI Investment Report`。
4. 点击 `Run workflow`。
5. 运行完成后查看 artifact：`stone-ai-investment-reports`。
6. 检查 Gmail 是否收到日报。

## 部署

```bash
git init
git add .
git commit -m "Stone AI Investment Manager Pro V12"
git branch -M main
git remote add origin 你的GitHub仓库地址
git push -u origin main
```

部署前运行：

```bash
python scripts/deploy_check.py
```

## 安全要求

- 不提交 `.env`。
- 不提交 `SMTP_PASSWORD`。
- 不提交 `OPENAI_API_KEY`。
- 密钥全部使用 GitHub Secrets。
- 不自动交易。
- 不接券商下单权限。
- 不承诺收益。
- 所有建议仅供投资辅助，不构成投资建议。
