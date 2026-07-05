# Stone AI Investment Manager Pro V12

Stone AI Investment Manager Pro V12 是最终生产版：每天北京时间 8:30 自动运行，生成核心报告，并通过企业微信应用点对点推送 `reports/today_action.md` 给用户。

系统只做投资辅助提醒，不接入真实交易，不接券商下单权限，不承诺收益。所有内容仅供投资辅助，不构成投资建议。

## 核心产物

每日运行生成：

```text
reports/today_action.md
reports/daily_report.md
reports/weekly_report.md
reports/system_check_report.md
```

## 保留能力

- 持仓读取：`data/portfolio.csv`
- 手动市场数据：`data/market_data.csv`
- yfinance 行情获取，失败不影响主程序
- 宏观事件监控：`config/settings.yaml`
- VIX 风险判断
- Stone Score 与风险评分
- 定投建议
- 再平衡建议
- 跨资产联动分析
- OpenAI 深度总结，可选
- 企业微信应用点对点推送
- QQ 邮箱备用通知，可选
- 投资日志：`data/investment_log.csv`
- GitHub Actions 每日自动运行
- 最终验收：`scripts/final_check.py`
- 部署检查：`scripts/deploy_check.py`

## 本地命令

本地运行：

```bash
python run.py
```

兼容入口：

```bash
python main.py
```

企业微信测试：

```bash
python scripts/test_wecom.py
```

最终验收：

```bash
python scripts/final_check.py
```

部署检查：

```bash
python scripts/deploy_check.py
```

## 安装依赖

```bash
pip install -r requirements.txt
```

如果没有安装 `yfinance` 或 `openai`，系统会 WARN，但基础日报仍可生成。

## 更新持仓

编辑：

```text
data/portfolio.csv
```

字段：

```text
category,name,amount_wan,currency,note
```

金额单位为万元。

兼容表头：

- 持仓名称：`Asset`、`asset`、`标的`、`名称`、`name`
- 代码：`Symbol`、`symbol`、`代码`
- 资产类别：`Category`、`category`、`类型`、`资产类别`
- 金额：`Amount`、`amount`、`市值`、`金额`、`amount_wan`

如果 `portfolio.csv` 缺失，系统会自动生成模板；如果表头格式错误，系统会提示缺少的列，并生成 `portfolio_template.csv` 作为参考。

## 更新市场数据

编辑：

```text
data/market_data.csv
```

如果实时行情获取失败，系统会使用该文件继续分析，不会崩溃。

## 企业微信点对点推送

本地 `.env` 填写：

```text
WECOM_CORP_ID=你的企业ID
WECOM_AGENT_ID=1000002
WECOM_SECRET=你的应用Secret
WECOM_USER_ID=你的企业微信UserID
```

查找方式：

1. 企业微信管理后台 -> `我的企业 -> 企业信息`，复制企业 ID。
2. `应用管理` 中打开自建应用，复制 `AgentId`。
3. 在同一应用详情页查看或发送 `Secret`。
4. `通讯录` 中打开你的成员资料，复制 `UserID`。
5. 确认自建应用可见范围包含该用户。

测试：

```bash
python scripts/test_wecom.py
```

如果失败，常见原因是 Secret 错误、UserID 错误、应用可见范围没有包含该用户、企业微信账号不可用、GitHub Secrets 未配置，或当前环境网络受限。

## QQ 邮箱备用通知

本地 `.env` 可选填写：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=你的QQ邮箱SMTP授权码
EMAIL_TO=shili3263063@qq.com
```

`SMTP_PASSWORD` 是 QQ 邮箱 SMTP 授权码，不是登录密码。未配置时只 WARN，不影响日报生成。

## OpenAI 深度总结

本地 `.env` 可选填写：

```text
OPENAI_API_KEY=你的OpenAI API Key
```

未配置时只 WARN，并在日报中显示 AI 深度分析未启用。

## GitHub Actions

工作流文件：

```text
.github/workflows/daily.yml
```

规则：

- 每天北京时间 8:30 自动运行
- cron：`30 0 * * *`
- 支持 `workflow_dispatch`
- 使用 Python 3.11
- 执行 `python main.py`
- 上传 `reports/` 为 artifact

必填 Secrets：

```text
WECOM_CORP_ID
WECOM_AGENT_ID
WECOM_SECRET
WECOM_USER_ID
```

可选 Secrets：

```text
OPENAI_API_KEY
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
EMAIL_TO
```

手动运行：

1. 打开 GitHub 仓库。
2. 点击 `Actions`。
3. 选择 `Daily Stone AI Investment Report`。
4. 点击 `Run workflow`。
5. 运行完成后下载 artifact：`stone-ai-investment-reports`。
6. 检查企业微信是否收到 `today_action.md`。

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

- 不提交 `.env`
- 不提交 `WECOM_SECRET`
- 不提交 `OPENAI_API_KEY`
- 不提交 `SMTP_PASSWORD`
- 不自动交易
- 不接券商下单权限
- 不承诺收益
- 所有报告均写明：仅供投资辅助，不构成投资建议
