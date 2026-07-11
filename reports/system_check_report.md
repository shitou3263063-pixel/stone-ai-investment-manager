Stone AI Investment Manager Pro V12.2 Smart Grid 系统自检
总体状态：WARN

[OK] Python版本：当前 Python 3.14.6。
[WARN] requirements依赖：部分依赖未安装：pytest>=8.0.0
    修复建议：可运行：python -m pip install -r requirements.txt
[OK] 持仓文件：data/portfolio.csv 存在且格式可读取。
[OK] 策略配置：config/settings.yaml 存在。
[OK] 报告目录：reports 文件夹存在。
[OK] .env.example：.env.example 存在。
[OK] .env文件：.env 存在。
[OK] GitHub Actions：.github/workflows/daily.yml 存在。
[OK] 邮件配置：邮件配置完整。
[WARN] OpenAI API Key：未配置 OPENAI_API_KEY，AI 深度分析会跳过，基础日报可运行。
    修复建议：需要 AI 深度分析时，在 .env 或 GitHub Secrets 中添加 OPENAI_API_KEY。