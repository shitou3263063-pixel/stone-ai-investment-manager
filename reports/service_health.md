# Service Health

- 检查时间：2026-07-16T02:08:27
- 可用服务：4/13
- API Key 不会写入报告；这里只显示是否已配置。

| 服务 | 状态 | 响应时间ms | 最后成功时间 | 错误类型 | 降级可用 | 当前用途 |
| --- | --- | ---: | --- | --- | --- | --- |
| OpenAI | not_configured | 0.97 |  | missing_config_or_dependency | yes | LLM primary |
| Gemini | not_configured | 0.97 |  | missing_config_or_dependency | yes | LLM fallback |
| Claude | not_configured | 0.97 |  | missing_config_or_dependency | yes | LLM fallback |
| DeepSeek | not_configured | 0.97 |  | missing_config_or_dependency | yes | LLM fallback |
| Qwen | not_configured | 0.97 |  | missing_config_or_dependency | yes | LLM fallback |
| Ollama | optional | 0.97 |  |  | yes | local fallback |
| FRED | not_configured | 0.97 |  | missing_config_or_dependency | yes | macro primary |
| Alpha Vantage | not_configured | 0.97 |  | missing_config_or_dependency | yes | market backup |
| Finnhub | not_configured | 0.97 |  | missing_config_or_dependency | yes | market backup/news |
| CBOE | available | 0.97 | 2026-07-16T02:08:27 |  | yes | VIX official reference |
| yfinance | available | 0.97 | 2026-07-16T02:08:27 |  | yes | market fallback |
| AkShare | available | 0.97 | 2026-07-16T02:08:27 |  | yes | A/H optional fallback |
| Gmail SMTP | configured | 0.97 | 2026-07-16T02:08:27 |  | yes | notification |