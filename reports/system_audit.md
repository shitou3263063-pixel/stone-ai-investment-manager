# Stone AI V12.7.0 Stable System Audit

- 审计时间：2026-07-16T02:08:27
- 当前实际运行入口：根目录 `main.py`（V12.7.0 Stable唯一正式入口）。
- GitHub Actions 应调用：`python main.py`。
- 报告生成模块：`src/reports/report_center.py`。
- 决策核心模块：`src/decision/v12_1_decision.py`。

## 数据源接入状态

- 已接入代码路径：FRED、Alpha Vantage、Finnhub、Cboe VIX、yfinance、本地缓存。
- 是否真正成功使用以 `reports/daily_report.md` 的数据来源章节为准，未成功请求的来源不会被列为成功来源。

## 当前旧报告问题原因

- 美股/A股/港股/黄金显示0.00：旧市场摘要使用 `market_data.csv` 默认变化值，缺失行情没有区分失败和真实0；V12.6继续保持“暂无可靠数据/请求失败/缓存”表达。
- 双源验证覆盖率：旧路由拿到第一个成功源就返回，导致候选源不足；V12.6按候选源和Source Audit区分覆盖率。
- 一级来源覆盖率：取决于本次实际成功来源，不再把配置占位算作成功。
- 分析状态：SAFE_MODE，来源：Stone CIO规则引擎；OpenAI仅为可选解释层。
- 本周0元、本月金额、债券转权益冲突：旧逻辑把现金预算和未到账债券资金混用；V12.6拆成账户总现金、可投资现金和条件性债券到账计划。
- 基础定投无金额：V12.6在资金计划中明确计划日、金额、资金来源和不执行原因。
- 风险评分明细：旧评分来自 MarketAgent 汇总值 78；V12.6继续输出八项风险分解。

## 关键运行快照

- 旧数据质量分：69
- 旧执行计划：today=0.0万 week=0.0万 month=0.0万
- 新DQS：51 / 禁止新增仓位建议
- 新风险评分：45 / 中低风险