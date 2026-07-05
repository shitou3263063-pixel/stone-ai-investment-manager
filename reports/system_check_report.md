# Stone AI Investment Manager Pro V11 系统检查报告

- 生成时间：2026-07-05 23:52:59
- 总体状态：WARN
- 是否可运行：是

## 基础自检

Stone AI Investment Manager Pro V11 系统自检
总体状态：WARN

[OK] Python版本：当前 Python 3.12.13。
[WARN] requirements依赖：部分依赖未安装：yfinance>=0.2.40、openai
    修复建议：可运行：python -m pip install -r requirements.txt
[OK] 持仓文件：data/portfolio.csv 存在。
[OK] 策略配置：config/settings.yaml 存在。
[OK] 报告目录：reports 文件夹存在。
[OK] .env.example：.env.example 存在。
[OK] .env文件：.env 存在。
[OK] GitHub Actions：.github/workflows/daily.yml 存在。
[WARN] 邮件配置：邮件配置不完整，将跳过发送：SMTP_USER、SMTP_PASSWORD
    修复建议：需要邮件时，在 .env 或 GitHub Secrets 中补齐 SMTP 配置。
[WARN] 旧版密码配置：data/config.yaml 中存在旧版 password 字段；当前 V11 不需要把真实密码保存在配置文件里。
    修复建议：建议把授权码迁移到 .env 或 GitHub Secrets，然后清空 data/config.yaml 中的 password。
[WARN] OpenAI API Key：未配置 OPENAI_API_KEY，AI 深度分析会跳过，基础日报可运行。
    修复建议：需要 AI 深度分析时，在 .env 或 GitHub Secrets 中添加 OPENAI_API_KEY。
[OK] 企业微信应用推送：企业微信应用点对点推送配置完整。

## 企业微信点对点推送状态

- 状态：OK
- 说明：企业微信应用点对点推送配置完整。
- 是否已配置 WECOM 四项参数：是

## WECOM 参数检查

- WECOM_CORP_ID：已配置
- WECOM_AGENT_ID：已配置
- WECOM_SECRET：已配置
- WECOM_USER_ID：已配置

## GitHub Actions Secrets 映射检查

- WECOM_CORP_ID：daily.yml 已映射
- WECOM_AGENT_ID：daily.yml 已映射
- WECOM_SECRET：daily.yml 已映射
- WECOM_USER_ID：daily.yml 已映射

## 测试命令

```bash
python scripts/test_wecom.py
python run.py
```

## 企业微信发送失败常见原因

- Secret 错误。
- UserID 错误。
- 应用可见范围没有包含该用户。
- 企业微信未登录或账号不可用。
- GitHub Secrets 没配置。
- 当前运行环境网络受限，无法连接企业微信 API。

## 安全说明

- 本报告只显示是否配置，不输出 WECOM_SECRET 的具体值。
- 不要把 `.env` 提交到 GitHub。
- 企业微信未配置或发送失败不会影响日报生成。
- 系统只提醒，不自动交易；所有内容仅供投资辅助，不构成投资建议。
