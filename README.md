# Stone AI Investment Manager Pro V11

这是一个本地可运行的多 Agent 智能投资顾问系统。V11 不接入真实交易、不自动买卖，只生成日报、周报、调仓建议、定投提醒和风险预警。系统不再机械按仓位比例买卖，而是结合资产配置、目标配置、市场环境、估值、趋势、宏观风险和你的长期投资风格，给出更稳健的操作建议。

- `Market Agent`：市场分析
- `Portfolio Agent`：持仓和配置分析
- `Risk Agent`：只检查规则是否触发
- `Decision Agent`：综合市场、仓位、风险和长期风格，给出最终建议
- `Report Agent`：生成日报、周报、月度再平衡报告、紧急提醒

默认不依赖实时行情接口。如果没有实时市场数据，直接手动更新 `data/market_data.csv` 即可。

## 目录结构

```text
investment_ai_manager/
├── data/
│   ├── portfolio.csv
│   ├── market_data.csv
│   └── config.yaml
├── agents/
│   ├── market_agent.py
│   ├── portfolio_agent.py
│   ├── risk_agent.py
│   ├── decision_agent.py
│   └── report_agent.py
├── reports/
│   ├── daily_report.md
│   ├── weekly_report.md
│   ├── monthly_rebalance.md
│   └── emergency_alert.md
├── utils/
│   ├── data_loader.py
│   ├── email_sender.py
│   └── wechat_sender.py
├── main_daily.py
├── main_weekly.py
├── main_monthly.py
├── main_emergency.py
├── main_monitor.py
├── requirements.txt
└── README.md
```

## 1. 如何安装依赖

进入项目目录：

```bash
cd investment_ai_manager
```

安装依赖：

```bash
pip install -r requirements.txt
```

说明：系统优先使用 `PyYAML` 读取配置。如果没有安装，项目内置了简易 YAML 读取器，当前配置也能正常运行。

## 2. 如何更新资产数据

编辑：

```text
data/portfolio.csv
```

字段说明：

- `category`：资产类别，例如 `美股`、`A股`、`港股`、`债券`、`黄金`、`现金`
- `name`：持仓名称
- `amount_wan`：金额，单位是万元
- `currency`：币种，默认 `CNY`
- `note`：备注

示例：

```csv
category,name,amount_wan,currency,note
美股,VOO,13.0,CNY,标普500ETF
黄金,黄金,56.0,CNY,黄金资产
```

## 3. 如何更新市场数据

编辑：

```text
data/market_data.csv
```

V11 字段说明：

- `indicator`：市场指标，例如 `纳斯达克涨跌幅`、`标普500涨跌幅`、`沪深300涨跌幅`
- `value`：当前判断或数值
- `score_impact`：对市场评分的影响，正数加分，负数减分
- `risk_note`：这个指标对你组合的影响
- `valuation`：估值水平，例如 `偏低`、`中性`、`偏高`
- `trend`：趋势强弱，例如 `偏强`、`中性`、`偏弱`、`回落`
- `macro_risk`：宏观风险，例如 `中性`、`中高`、`高`
- `defense_support`：是否支持防守资产，例如 `是` 或 `否`
- `as_of`：数据日期或说明

如果暂时没有实时行情，保持手动填写即可。

## 4. 如何生成日报

在 `investment_ai_manager` 目录运行：

```bash
python main_daily.py
```

输出文件：

```text
reports/daily_report.md
```

日报会包含：

- 总资产
- 市场风险评分、进攻指数、防守指数
- 是否适合加仓、是否适合减仓
- 我的资产变化
- 我的组合影响分析
- 规则触发与例外机制
- 今日建议与置信度
- 今日最终结论

## 5. 如何生成周报

```bash
python main_weekly.py
```

输出文件：

```text
reports/weekly_report.md
```

## 6. 如何生成月度再平衡报告

```bash
python main_monthly.py
```

输出文件：

```text
reports/monthly_rebalance.md
```

## 7. 如何生成紧急提醒

```bash
python main_emergency.py
```

输出文件：

```text
reports/emergency_alert.md
```

也可以传入事件名称和原因：

```bash
python main_emergency.py "纳斯达克单日大跌3%" "美股低配较大，但短期波动升高，需要重新判断补仓节奏"
```

紧急提醒会固定输出：

- 事件
- 为什么重要
- 对我的资产影响
- 需要操作吗
- 买什么
- 卖什么
- 建议金额
- 为什么这样做

## 8. 如何开启准实时提醒

```bash
python main_monitor.py
```

输出文件：

```text
reports/emergency_alert.md
reports/last_emergency_state.json
```

`main_monitor.py` 会检查当前组合是否触发紧急条件。若风险状态和上次一样，它不会重复发送邮件，避免每小时刷屏。

只测试、不发邮件：

```bash
python main_monitor.py --no-email
```

## 9. 如何发送到邮箱

V11 推荐使用 `.env` 或 GitHub Secrets 保存邮箱配置，不要把 SMTP 授权码写进代码。

本地复制示例文件：

```bash
copy .env.example .env
```

填写：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=你的QQ邮箱SMTP授权码
EMAIL_TO=shili3263063@qq.com
```

再次运行：

```bash
python run.py
```

系统会先生成日报，再自动尝试发送 `reports/today_action.md` 和 `reports/daily_report.md`；如果 `reports/weekly_report.md` 存在，也会一起发送。日报默认发送到 `shili3263063@qq.com`。

## 10. 如何发送到企业微信

编辑：

```text
data/config.yaml
```

把 `wechat.enabled` 改成 `true`，并填写机器人 webhook：

```yaml
wechat:
  enabled: true
  webhook_url: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx
```

再次运行：

```bash
python main_daily.py
```

系统会把日报发送到企业微信群。

## 11. 如何配合 GitHub Actions 每天自动运行

当前推荐工作流：

```text
.github/workflows/daily.yml
```

运行时间：

- 每天北京时间早上 8:30
- GitHub Actions cron：`30 0 * * *`

工作流会执行：

```bash
cd investment_ai_manager
pip install -r requirements.txt
python run.py
```

然后把生成的报告保存为 artifact：

```text
stone-ai-investment-reports
```

注意：这个 workflow 只生成报告并保存到 `reports` 文件夹，不接入真实交易，也不会自动买卖。

## 12. 调仓规则

V11 中，`Risk Agent` 只负责检查规则是否触发，不直接决定买卖。最终建议由 `Decision Agent` 综合判断。

系统当前内置规则：

- 单一资产类别偏离目标超过 3%，提示调仓
- 单次卖出不超过该资产类别金额的 20%
- 黄金占比超过 15% 时，只提示分批减仓，不再加仓
- 港股不主动大幅加仓，除非占比低于 6%
- 英伟达占总资产超过 5% 时，停止继续加仓
- 现金低于 5% 时，暂停所有加仓，优先恢复现金
- 市场风险较高时，降低进攻型资产加仓比例

这些规则都可以在 `data/config.yaml` 中修改。

## 13. V11 决策原则

你的长期投资风格写在 `data/config.yaml`：

- 投资周期 5 年以上
- 不频繁交易
- 优先控制回撤
- 以 ETF 和多资产配置为核心
- 目标是稳健增长

因此，即使规则触发，系统也允许暂时不执行。例如黄金超过 15% 时，V11 会继续判断黄金趋势、美元、美债收益率、避险需求和组合对冲价值，再决定是立即减仓、分批减仓、继续持有，还是等待确认信号。

日报最后会固定输出：

```text
【今日最终结论】

操作等级：A/B/C/D
今日是否调仓：是/否
建议买入：
建议卖出：
建议继续持有：
建议等待：
最大风险：
一句话结论：
```

## 14. V11 邮件通知配置

当前推荐入口是：

```bash
python run.py
```

主程序会先生成 `reports/daily_report.md`、`reports/weekly_report.md` 等报告，然后自动尝试发送邮件。邮箱配置从 `.env` 或系统环境变量读取，不会写进代码。

先复制示例文件：

```bash
copy .env.example .env
```

然后编辑 `.env`：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=你的QQ邮箱SMTP授权码
EMAIL_TO=shili3263063@qq.com
```

说明：

- QQ 邮箱使用 `smtp.qq.com` 和 SSL 端口 `465`。
- `EMAIL_TO` 默认是 `shili3263063@qq.com`。
- `SMTP_PASSWORD` 填 QQ 邮箱 SMTP 授权码，不是邮箱登录密码。
- `.env` 已加入 `.gitignore`，不要提交到 GitHub。
- 如果 `.env` 缺失或配置不完整，系统只会提示“邮件未配置，跳过”，不会影响日报生成。

QQ 邮箱开启 SMTP 和获取授权码：

1. 登录 QQ 邮箱网页版。
2. 进入 `设置 -> 账户`。
3. 找到 `POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务`。
4. 开启 `POP3/SMTP服务` 或 `IMAP/SMTP服务`。
5. 按页面提示验证后生成 SMTP 授权码。
6. 把授权码填入 `.env` 的 `SMTP_PASSWORD`。

安全提醒：

- 不要把 QQ 邮箱登录密码填到 `SMTP_PASSWORD`。
- 不要把 SMTP 授权码提交到 GitHub。
- 本项目的 `.env` 已被 `.gitignore` 忽略。

## 15. GitHub Actions 自动运行

已新增：

```text
.github/workflows/daily.yml
```

运行时间：

- 每天北京时间早上 8:30
- GitHub cron：`30 0 * * *`

工作流会：

- 使用 Python 3.11
- 安装 `requirements.txt`
- 执行 `python run.py`
- 把 `investment_ai_manager/reports/` 保存为 artifact

## 16. GitHub Secrets 配置

在 GitHub 仓库页面进入：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

添加这些 Secrets：

```text
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
EMAIL_TO
```

如果不想让 GitHub Actions 发邮件，可以不配置这些 Secrets。日报仍会生成，邮件步骤会自动跳过。

## 17. 如何查看每日运行结果

进入 GitHub 仓库：

```text
Actions -> Stone AI Investment Daily -> 最近一次运行
```

在页面底部下载 artifact：

```text
stone-ai-investment-reports
```

里面会包含当天生成的日报、周报、调仓建议、定投提醒和风险预警。

## 18. 如何关闭邮件通知

本地关闭：

- 删除 `.env`；或
- 清空 `.env` 里的邮箱配置。

GitHub Actions 关闭：

- 删除仓库中的 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`EMAIL_TO` Secrets；或
- 保留 Secrets 为空。

关闭邮件不会影响报告生成，也不会触发任何真实交易。

## 19. 宏观事件与 VIX 风险预警

宏观事件配置在：

```text
config/settings.yaml
```

示例：

```yaml
macro_events:
  - name: FOMC利率决议
    date: 2026-07-29
    level: high
  - name: CPI数据
    date: 2026-07-15
    level: high
```

系统会自动检查未来 7 天是否有 `high` 级别事件。若有，会在日报中提示：

- 重大事件前不追涨
- 定投可以继续
- 不建议一次性重仓买入

VIX 风险规则：

- `VIX < 15`：市场情绪偏乐观
- `15 <= VIX < 20`：正常波动
- `20 <= VIX < 30`：风险升高
- `VIX >= 30`：高风险，暂停追涨

如果实时 VIX 获取失败，系统不会崩溃，会记录日志并按中性偏谨慎处理。

## 20. 定投提醒与再平衡建议

定投和本轮目标配置维护在：

```text
config/settings.yaml
```

定投配置示例：

```yaml
dca_plan:
  enabled: true
  monthly_budget: 10000
  targets:
    - symbol: VOO
      name: 标普500ETF
      base_amount: 4000
```

定投规则：

- `VIX < 20`：正常定投
- `VIX 20-30`：定投金额减少 30%
- `VIX >= 30`：暂停追涨，只保留小额定投
- 未来 7 天有 high 级别宏观事件：不额外加仓
- 下跌明显但风险未失控：提示分批定投

再平衡目标配置示例：

```yaml
target_allocation:
  us_stock: 18
  hk_stock: 10
  cn_stock: 8
  bond: 38
  gold: 18
  cash: 8
```

再平衡规则：

- 偏离目标比例小于 3%：不调仓
- 偏离 3%-5%：观察
- 偏离超过 5%：提示再平衡
- 优先用新增资金再平衡
- 不建议频繁卖出长期资产

日报会自动增加“本月定投计划”和“当前资产配置 vs 目标配置”。所有输出仅供投资辅助，不构成投资建议，系统不会自动交易，也不承诺收益。

## 21. 跨资产联动分析

跨资产模块在：

```text
src/analysis/cross_asset_engine.py
```

覆盖资产：

- 美股：`VOO`、`QQQ`、`^GSPC`、`^IXIC`
- 港股：`3067.HK`、`3033.HK`、`2800.HK`
- A股：`510300.SS`
- 黄金：`GLD`
- 美债：`TLT`、`IEF`
- 美元：`UUP`、`DX-Y.NYB`
- 波动率：`^VIX`

日报会输出：

- 跨资产联动分析
- 黄金当前判断
- 债券当前判断
- 美元当前判断
- 美股与港股强弱对比
- 对当前组合的影响

该模块只用于辅助判断加仓节奏和风险位置，不自动交易、不预测具体涨跌点位、不承诺收益。

## 22. OpenAI API 深度分析

AI 深度分析模块在：

```text
src/ai/openai_advisor.py
```

本地启用方式：

```bash
copy .env.example .env
```

然后在 `.env` 中填写：

```text
OPENAI_API_KEY=你的OpenAI API Key
```

说明：

- API Key 只能写在 `.env` 或系统环境变量里，不得写入代码。
- `.env` 已加入 `.gitignore`，不得提交到 GitHub。
- 如果没有配置 `OPENAI_API_KEY`，系统仍会正常运行，并在日报中写入：`AI深度分析未启用：未配置 OPENAI_API_KEY`。
- 如果已配置 API Key，`python run.py` 会把资产配置、市场数据、VIX、宏观事件、定投建议、再平衡建议和跨资产联动分析发送给 OpenAI，用于生成“AI 投资经理总结”。
- 如果使用 GitHub Actions，把 `OPENAI_API_KEY` 添加到仓库的 Actions Secrets 即可。
- AI 输出仅供投资辅助，不构成投资建议；系统不自动交易、不承诺收益、不预测具体点位，最终决策由用户自己负责。

## 23. 投资日志库与历史复盘

系统长期记忆库在：

```text
data/investment_log.csv
```

每次运行：

```bash
python run.py
```

系统会自动写入或更新当天记录，字段包括：

```text
date,total_assets,risk_score,stone_score,stock_ratio,bond_ratio,gold_ratio,cash_ratio,vix,main_advice,user_action,result_note
```

说明：

- `investment_log.csv` 是系统长期记忆库，历史数据越多，复盘越有价值。
- 同一天重复运行不会新增重复记录，而是更新当天的资产、风险和建议。
- 你可以手动在 `user_action` 里记录自己是否执行了建议。
- 你可以手动在 `result_note` 里记录后续结果、体感和复盘备注。
- 日报会显示最近 7 天风险变化、最近 30 天 Stone Score 变化、最近几次系统建议和策略是否需要调整。
- 周报会显示本周资产配置变化、本周风险评分变化、本周主要建议和下周关注事项。
- 历史复盘只用于辅助长期纪律，不自动交易、不承诺收益，也不根据短期结果频繁改策略。

## 24. 系统自检与一键运行

新手推荐方式：

```text
双击 install_and_run.bat
```

它会自动：

- 安装 `requirements.txt`
- 执行系统自检
- 自动创建可修复的缺失目录和模板文件
- 运行 `python run.py`
- 生成 `reports/daily_report.md`
- 运行结束后暂停窗口，方便查看提示

高级用户可以直接运行：

```bash
python run.py
```

自检模块在：

```text
src/system/health_check.py
```

自检状态说明：

- `OK`：正常
- `WARN`：可运行但功能不完整，例如邮件或 OpenAI 未配置
- `ERROR`：必须修复，主程序会停止并给出修复提示

自动修复范围：

- 自动创建 `reports/` 文件夹
- 自动生成 `data/portfolio.csv` 模板
- 自动生成 `config/settings.yaml` 模板
- 自动生成 `.env.example`
- 自动提示缺失依赖安装命令

安全说明：

- 系统不会自动交易。
- 系统不会把真实邮箱密码或 OpenAI API Key 写入代码。
- 邮件或 OpenAI 未配置不会中断基础日报生成。
- 所有内容仅供投资辅助，不构成投资建议。

## 25. 手机查看与远程控制闭环

每日运行后会生成两个核心文件：

```text
reports/daily_report.md
reports/today_action.md
```

`today_action.md` 是手机优先阅读的 5 行摘要，只包含：

- 今日是否交易
- 今日是否定投
- 今日是否再平衡
- 今日最大风险
- 一句话结论

手机查看 GitHub Actions 结果：

1. 打开 GitHub 手机 App 或手机浏览器。
2. 进入仓库页面。
3. 打开 `Actions`。
4. 选择 `Stone AI Investment Daily`。
5. 点进最近一次运行，在 `Artifacts` 下载 `stone-ai-investment-reports`。
6. 查看 `today_action.md` 或 `daily_report.md`。

手机查看邮箱日报：

- 如果配置了 SMTP，系统会把 `today_action.md` 摘要放在邮件正文最前面。
- `daily_report.md` 和 `weekly_report.md` 会作为完整报告一起发送。
- 没配置邮箱时，系统只跳过发送，不影响本地或 GitHub Actions 生成报告。

手机远程触发运行：

- 推荐方式：在 GitHub Actions 页面点击 `Run workflow`，让云端立即运行一次。
- 电脑本地方式：用远程桌面、向日葵、ToDesk 等工具连回电脑后，双击 `install_and_run.bat`，或在终端运行 `python run.py`。
- Codex 桌面端仍然适合做代码升级、检查和维护；每日自动运行建议交给 GitHub Actions。

远程推送预留：

```text
src/notifier/push_notifier.py
```

当前支持预留：

- Telegram：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`
- 企业微信/微信机器人：`QYWX_WEBHOOK_URL` 或 `WECHAT_WEBHOOK_URL`

没配置时会自动跳过，不影响主程序运行。

风控边界：

- 系统只提醒，不下单。
- 所有建议仅供投资辅助，不构成投资建议。
- 最终决策和实际执行必须由用户自己完成。

## 26. 邮件测试命令

测试邮件：

```bash
python scripts/test_email.py
```

正式运行：

```bash
python run.py
```

测试邮件会读取 `.env`，检查：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=你的QQ邮箱SMTP授权码
EMAIL_TO=shili3263063@qq.com
```

如果配置完整，会向 `shili3263063@qq.com` 发送一封标题为 `Stone AI 邮件测试` 的测试邮件。

如果 `SMTP_PASSWORD` 缺失，测试脚本只会提示“邮件未启用”，不会报错。授权码不要写进代码，不要提交 `.env` 到 GitHub。

## 27. GitHub Actions 自动部署与每日邮件推送

上传到 GitHub：

1. 在 GitHub 创建一个新仓库。
2. 把当前项目推送到仓库。
3. 确认仓库里存在 `.github/workflows/daily.yml`。
4. 确认 `.env` 没有被提交。

打开 Actions：

1. 进入 GitHub 仓库页面。
2. 点击 `Actions`。
3. 如果 GitHub 提示启用 workflows，点击允许。
4. 选择 `Stone AI Investment Daily`。

每日自动运行规则：

```yaml
cron: "30 0 * * *"
```

这表示 UTC `00:30`，也就是北京时间每天早上 `08:30`。

手动运行一次：

1. 打开 `Actions -> Stone AI Investment Daily`。
2. 点击 `Run workflow`。
3. 选择分支。
4. 点击绿色按钮运行。

添加 GitHub Secrets：

进入：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

添加：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的QQ邮箱
SMTP_PASSWORD=你的QQ邮箱SMTP授权码
EMAIL_TO=shili3263063@qq.com
OPENAI_API_KEY=你的OpenAI API Key
WECHAT_WORK_WEBHOOK=你的企业微信群机器人Webhook地址
WECOM_CORP_ID=你的企业ID
WECOM_AGENT_ID=1000002
WECOM_SECRET=你的应用Secret
WECOM_USER_ID=你的企业微信UserID
```

`OPENAI_API_KEY` 可选；不配置时 AI 深度分析会跳过。SMTP 授权码必须放在 Secrets 里，不要提交到代码仓库。

查看 artifact：

1. 打开最近一次 workflow 运行记录。
2. 在页面底部找到 `Artifacts`。
3. 下载 `stone-ai-investment-reports`。
4. 查看 `today_action.md` 和 `daily_report.md`。

确认邮件发送成功：

- 在 workflow 日志里搜索 `邮件已发送`。
- 如果显示 `邮件未配置，跳过`，说明 SMTP Secrets 不完整。
- 如果显示 `邮件发送失败`，检查 QQ 邮箱 SMTP 是否已开启，以及 `SMTP_PASSWORD` 是否为授权码。

安全要求：

- 不提交 `.env`。
- 不提交真实 SMTP 授权码。
- 不提交 OpenAI API Key。
- 邮件失败不影响日报生成。
- 系统只提醒，不下单，最终决策由用户自己执行。

## 28. 企业微信群机器人推送

企业微信推送使用独立配置：

```text
WECHAT_WORK_WEBHOOK=你的企业微信群机器人Webhook地址
```

创建企业微信群机器人：

1. 打开企业微信群。
2. 进入群设置，找到 `群机器人`。
3. 添加机器人，选择自定义机器人。
4. 复制生成的 Webhook 地址。
5. 把 Webhook 填入本地 `.env`，不要提交到 GitHub。

本地测试：

```bash
python scripts/test_wechat_work.py
```

正式运行：

```bash
python run.py
```

运行后，系统会先生成 `reports/today_action.md`，再尝试推送到企业微信群。未配置 `WECHAT_WORK_WEBHOOK` 时会提示“企业微信未配置，跳过”，不会影响日报生成。

GitHub Actions 配置：

1. 进入 `Settings -> Secrets and variables -> Actions`。
2. 新增 Secret：`WECHAT_WORK_WEBHOOK`。
3. 值填写企业微信群机器人的 Webhook 地址。
4. 手动运行一次 workflow，或等待每天北京时间早上 8:30 自动运行。

安全提醒：

- 不要把 Webhook 写进代码。
- 不要提交 `.env`。
- 推送失败不会中断日报生成。
- 系统只提醒，不下单；所有内容仅供投资辅助，不构成投资建议。

## 29. 企业微信应用点对点推送

V11 新增企业微信自建应用点对点推送，适合把 `today_action.md` 私发给你本人，而不是发到群里。

本地 `.env` 增加：

```text
WECOM_CORP_ID=你的企业ID
WECOM_AGENT_ID=1000002
WECOM_SECRET=你的应用Secret
WECOM_USER_ID=你的企业微信UserID
```

配置步骤：

1. 登录企业微信管理后台。
2. 找 `CorpID`：进入 `我的企业 -> 企业信息`，复制 `企业ID`，填入 `WECOM_CORP_ID`。
3. 找 `AgentID`：进入 `应用管理`，创建或打开一个自建应用，在应用详情页复制 `AgentId`，填入 `WECOM_AGENT_ID`。
4. 找 `Secret`：仍在该自建应用详情页，点击 `Secret` 的查看或发送按钮，按企业微信提示获取应用 Secret，填入 `WECOM_SECRET`。
5. 找 `UserID`：进入 `通讯录`，打开你的成员资料，复制账号或 UserID，填入 `WECOM_USER_ID`。
6. 确认应用可见范围包含你本人，否则消息会发送失败。
7. 如果使用 GitHub Actions，也要把同样四项添加到仓库 Secrets。

本地 `.env` 示例：

```text
WECOM_CORP_ID=你的企业ID
WECOM_AGENT_ID=1000002
WECOM_SECRET=你的应用Secret
WECOM_USER_ID=你的企业微信UserID
```

测试点对点推送：

```bash
python scripts/test_wecom.py
```

正式运行：

```bash
python run.py
```

运行后，系统会先生成 `reports/today_action.md`，再通过企业微信自建应用尝试点对点发送给 `WECOM_USER_ID`。如果配置缺失，只会提示“企业微信应用未配置，跳过”，不会影响日报生成。

GitHub Actions Secrets 增加：

```text
WECOM_CORP_ID
WECOM_AGENT_ID
WECOM_SECRET
WECOM_USER_ID
```

添加位置：

1. 打开 GitHub 仓库。
2. 进入 `Settings -> Secrets and variables -> Actions`。
3. 点击 `New repository secret`。
4. 分别添加 `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_SECRET`、`WECOM_USER_ID`。
5. 保存后手动运行一次 `Actions -> Stone AI Investment Daily -> Run workflow` 测试。

系统配置自检：

```bash
python scripts/final_check.py
```

输出文件：

```text
reports/system_check_report.md
```

报告会显示企业微信点对点推送状态、WECOM 四项参数是否已配置，以及测试命令。为了安全，报告只显示“已配置/未配置”，不会输出 Secret。

常见发送失败原因：

- `WECOM_SECRET` 填错，或复制了旧 Secret。
- `WECOM_USER_ID` 填错，不是通讯录中的企业微信 UserID。
- 自建应用的可见范围没有包含该用户。
- 企业微信账号不可用，或企业微信客户端没有正常登录。
- GitHub Actions 里没有配置对应 Secrets。
- 当前运行环境网络受限，无法访问企业微信 API。

安全提醒：

- 不要把 `WECOM_SECRET` 写进代码。
- 不要提交 `.env`。
- 配置缺失只是 WARN，不会阻止日报生成。
- 企业微信发送失败只记录日志，不中断主程序。
- 系统只提醒，不下单；所有内容仅供投资辅助，不构成投资建议。

## 30. 一键部署与每日自动推送闭环

本地运行：

```bash
python run.py
```

企业微信测试：

```bash
python scripts/test_wecom.py
```

最终验收：

```bash
python scripts/final_check.py
```

部署前检查：

```bash
python scripts/deploy_check.py
```

部署前检查会确认 Git、GitHub remote、Actions 文件、`.env` 是否被跟踪、日报是否能生成、`today_action.md` 是否能生成、企业微信配置是否完整，以及 README 是否包含部署说明。

GitHub 首次部署步骤：

```bash
cd investment_ai_manager
git init
git add .
git commit -m "Stone AI Investment Manager Pro V11"
git branch -M main
git remote add origin 你的GitHub仓库地址
git push -u origin main
```

GitHub Actions 手动运行：

1. 打开 GitHub 仓库。
2. 点击 `Actions`。
3. 选择 `Daily Stone AI Investment Report`。
4. 点击 `Run workflow`。
5. 运行完成后查看 artifact。
6. 下载 `stone-ai-investment-reports`。
7. 检查 `today_action.md` 和 `daily_report.md`。
8. 检查企业微信是否收到消息。

部署安全检查：

- 不要提交 `.env`。
- 不要提交 `WECOM_SECRET`。
- 不要提交 `OPENAI_API_KEY`。
- 不要提交 `SMTP_PASSWORD`。
- 以上密钥只放在本地 `.env` 或 GitHub Actions Secrets。
- 系统只提醒，不下单；所有内容仅供投资辅助，不构成投资建议。
