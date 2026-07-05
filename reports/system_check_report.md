Stone AI Investment Manager Pro V12 系统自检
总体状态：WARN

[OK] Python版本：当前 Python 3.12.13。
[WARN] requirements依赖：部分依赖未安装：yfinance>=0.2.40、openai
    修复建议：可运行：python -m pip install -r requirements.txt
[OK] 持仓文件：data/portfolio.csv 存在且格式可读取。
[OK] 策略配置：config/settings.yaml 存在。
[OK] 报告目录：reports 文件夹存在。
[OK] .env.example：.env.example 存在。
[OK] .env文件：.env 存在。
[OK] GitHub Actions：.github/workflows/daily.yml 存在。
[WARN] 邮件配置：邮件配置不完整，将跳过发送：SMTP_USER、SMTP_PASSWORD
    修复建议：需要邮件时，在 .env 或 GitHub Secrets 中补齐 SMTP 配置。
[WARN] OpenAI API Key：未配置 OPENAI_API_KEY，AI 深度分析会跳过，基础日报可运行。
    修复建议：需要 AI 深度分析时，在 .env 或 GitHub Secrets 中添加 OPENAI_API_KEY。
[OK] 企业微信应用推送：企业微信应用点对点推送配置完整。