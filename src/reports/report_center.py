from __future__ import annotations

from typing import Any

from src.reports.grid_report import generate_grid_daily_section


def _yuan(value: Any) -> str:
    try:
        return f"{float(value):,.0f}元"
    except (TypeError, ValueError):
        return "暂无数据"


def _wan_from_yuan(value: Any) -> str:
    try:
        return f"{float(value) / 10000:.2f}万元"
    except (TypeError, ValueError):
        return "暂无数据"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "暂无数据"


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _text(value: Any, fallback: str = "暂无数据") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _items(values: list[Any] | None) -> str:
    values = values or []
    if not values:
        return "- 暂无数据"
    return "\n".join(f"- {_text(value)}" for value in values)


def _amount_mode_text(decision: dict[str, Any], amount: int | float) -> str:
    mode = decision.get("dqs", {}).get("mode")
    if mode == "exact" and amount > 0:
        return _yuan(amount)
    if mode == "range" and amount > 0:
        return f"不超过 {_yuan(amount)}，需分批"
    if mode == "direction":
        return "只给方向，不给金额"
    return "0元"


def generate_today_action(decision: dict[str, Any]) -> str:
    budget = decision.get("budget", {})
    return "\n".join(
        [
            "# Stone CIO 今日行动摘要",
            "",
            f"版本：{decision.get('version')}",
            f"今日是否交易：{_yes_no(decision.get('today_trade'))}",
            f"今日交易类型：{decision.get('trade_type')}",
            f"今日建议金额：{_amount_mode_text(decision, budget.get('today_total_yuan', 0))}",
            f"建议标的：{decision.get('targets')}",
            f"资金来源：{decision.get('funding_source')}",
            f"DQS：{decision.get('dqs', {}).get('score')}（{decision.get('dqs', {}).get('mode_label')}）",
            f"风险评分：{decision.get('risk', {}).get('score')}（{decision.get('risk', {}).get('level')}）",
            f"下一复核日期：{decision.get('next_review_date')}",
            f"最大风险：{decision.get('max_risk')}",
            f"最大机会：{decision.get('max_opportunity')}",
            "",
            "今日不操作原因：",
            _items(decision.get("no_trade_reasons")),
            "",
            "下一触发条件：",
            _items(decision.get("next_triggers")),
            "",
            f"一句话结论：{decision.get('one_sentence')}",
            "",
            decision.get("disclaimer", "仅供投资辅助，不构成投资建议。"),
        ]
    )


def _money_plan_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| budget_id | 类型 | 是否执行 | 金额 | 标的 | 资金来源 | 原因 |",
        "| -- | -- | ---- | -: | -- | ---- | -- |",
    ]
    for item in decision.get("budget", {}).get("rows", []) or []:
        rows.append(
            "| "
            f"{_text(item.get('budget_id'), '不适用')} | "
            f"{_text(item.get('type'))} | "
            f"{_yes_no(item.get('execute'))} | "
            f"{_yuan(item.get('amount_yuan', 0))} | "
            f"{_text(item.get('targets'), '不适用')} | "
            f"{_text(item.get('funding_source'), '不适用')} | "
            f"{_text(item.get('reason'))} |"
        )
    return rows


def _allocation_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 资产类别 | 当前金额 | 当前占比 | 目标金额 | 目标占比 | 偏离金额 | 偏离 | 状态 | 优先级 |",
        "| -- | -: | -: | -: | -: | -: | -: | -- | -- |",
    ]
    for item in decision.get("allocation", []) or []:
        rows.append(
            "| "
            f"{item['category']} | {_yuan(item['current_amount_yuan'])} | {_pct(item['current_ratio'])} | "
            f"{_yuan(item['target_amount_yuan'])} | {_pct(item['target_ratio'])} | "
            f"{_yuan(item['deviation_amount_yuan'])} | {_pct(item['deviation_ratio'])} | "
            f"{item['status']} | {item['priority']} |"
        )
    return rows


def _migration_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 月份 | 建议转出上限 | 剩余超配 | 复核 |",
        "| --: | -: | -: | -- |",
    ]
    for item in decision.get("migration_plan", {}).get("months", []) or []:
        rows.append(
            f"| {item['month']} | {_yuan(item['planned_transfer_yuan'])} | {_yuan(item['remaining_excess_yuan'])} | {item['review']} |"
        )
    return rows


def _cash_table(decision: dict[str, Any]) -> list[str]:
    budget = decision.get("budget", {}) or {}
    rows = [
        "| 现金口径 | 金额 | 说明 |",
        "| -- | -: | -- |",
        f"| 账户总现金 | {_yuan(budget.get('account_total_cash_yuan'))} | 用户确认的全部账户现金 |",
        f"| 现金安全储备 | {_yuan(budget.get('cash_safety_reserve_yuan'))} | 目标配置中必须保留，不用于投资 |",
        f"| 可投资现金 | {_yuan(budget.get('investable_cash_yuan'))} | 账户总现金扣除安全储备和占用资金后余额 |",
        f"| 网格实盘专用现金 | {_yuan(budget.get('live_grid_cash_yuan'))} | 当前未启用真实网格交易 |",
        f"| 网格模拟现金 | {_yuan(budget.get('paper_grid_cash_yuan'))} | 只用于SIMULATION，不计入真实资产 |",
        f"| 条件性未到账资金 | {_yuan(budget.get('actual_bond_cash_arrived_yuan'))} | 未到账债券资金不得当作现金使用 |",
    ]
    return rows


def _opportunity_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 标的 | 最终分 | 原始分 | 数据调整 | 当前持仓 | 组合适配 | 建议 | 分项得分 | 限制条件 | 原因 |",
        "| -- | ---: | ---: | ---: | ---: | ---- | -- | -- | -- | -- |",
    ]
    for item in decision.get("opportunity", []) or []:
        components = item.get("components", {}) or {}
        component_text = "；".join(f"{key}{value}" for key, value in components.items()) or "暂无"
        limitations = "；".join(item.get("limitations", []) or ["无"])
        rows.append(
            "| "
            f"{item['name']} | {item['score']} | {item.get('raw_score', item['score'])} | "
            f"{item.get('data_quality_adjustment', 0)} | {_yuan(item['current_holding_yuan'])} | "
            f"{item['portfolio_fit']} | {item['advice']} | {component_text} | {limitations} | {item['reason']} |"
        )
    return rows


def _holding_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 持仓 | 市值 | 占比 | 基本面 | 趋势 | 风险点 | 重叠度 | 建议 | 加仓条件 | 减仓条件 |",
        "| -- | -: | -: | -- | -- | -- | -- | -- | -- | -- |",
    ]
    for item in decision.get("holding_diagnostics", []) or []:
        rows.append(
            "| "
            f"{item['name']} | {_yuan(item['amount_yuan'])} | {_pct(item['portfolio_ratio'])} | "
            f"{item['fundamental_status']} | {item['trend_status']} | {item['risk']} | "
            f"{item['overlap']} | {item['advice']} | {item['add_condition']} | {item['reduce_condition']} |"
        )
    return rows


def _market_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 指标 | 当前值 | 前值 | 涨跌幅 | 时间戳 | 来源 | 来源等级 | 状态 |",
        "| -- | --: | --: | --: | -- | -- | --: | -- |",
    ]
    for item in decision.get("market_table", []) or []:
        change = "暂无数据" if item.get("change_pct") is None else f"{float(item['change_pct']):.2f}%"
        previous = "暂无数据" if item.get("previous") is None else f"{float(item['previous']):.2f}"
        status = "成功" if item.get("success") else _text(item.get("error"), "请求失败")
        rows.append(
            "| "
            f"{item['name']} | {item['display_value']} | {previous} | {change} | "
            f"{item['timestamp']} | {item['source']} | {item['source_tier']} | {status} |"
        )
    return rows


def _risk_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 风险项目 | 分数 | 权重 | 主要依据 |",
        "| ----- | -: | -: | ---- |",
    ]
    for item in decision.get("risk", {}).get("components", []) or []:
        rows.append(f"| {item['item']} | {item['score']} | {item['weight']} | {item['basis']} |")
    return rows


def _dqs_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| DQS项目 | 得分 | 满分 | 原因 |",
        "| ----- | -: | -: | -- |",
    ]
    for item in decision.get("dqs", {}).get("components", []) or []:
        rows.append(f"| {item['item']} | {item['score']} | {item['max']} | {item['reason']} |")
    return rows


def _events(decision: dict[str, Any]) -> list[str]:
    events = decision.get("events", []) or []
    if not events:
        return ["- 暂无已配置事件。"]
    lines = []
    for event in events:
        lines.append(
            f"- {event.get('date', '暂无日期')}：{event.get('name', '暂无名称')}，风险等级：{event.get('level', '暂无')}；"
            "纪律：事件前不追涨，定投可复核但不一次性重仓。"
        )
    return lines


def _scenarios(decision: dict[str, Any]) -> list[str]:
    lines = []
    for item in decision.get("scenarios", []) or []:
        lines.extend(
            [
                f"### {item['scenario']}",
                "",
                f"- 触发条件：{item['trigger']}",
                f"- 操作：{item['action']}",
                f"- 金额：{item['amount']}",
                f"- 标的：{item['targets']}",
                "",
            ]
        )
    return lines or ["- 暂无情景分析。"]


def _source_lines(decision: dict[str, Any]) -> list[str]:
    rows = []
    for item in decision.get("market_table", []) or []:
        if item.get("success"):
            rows.append(f"- {item['name']}：{item['source']}，时间：{item['timestamp']}，等级：{item['source_tier']}")
    return rows or ["- 本次没有可靠在线数据源成功返回，报告进入降级模式。"]


def generate_daily_report(
    *,
    decision: dict[str, Any],
    portfolio_result: dict[str, Any] | None = None,
    market_result: dict[str, Any] | None = None,
    live_market_result: dict[str, Any] | None = None,
    macro_result: dict[str, Any] | None = None,
    allocation_rebalance_result: dict[str, Any] | None = None,
    ai_advice_result: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> str:
    dqs = decision.get("dqs", {})
    risk = decision.get("risk", {})
    budget = decision.get("budget", {})
    migration = decision.get("migration_plan", {})
    consistency = decision.get("consistency", {})
    ai = decision.get("ai", {})
    lines = [
        "# Stone AI Investment Manager Pro V12.5 Stable 日报",
        "",
        "## 0. 报告状态",
        "",
        f"- 报告日期：{decision.get('date')}",
        f"- 运行时间：{decision.get('generated_at')}",
        f"- 数据截止时间：{decision.get('data_cutoff')}",
        f"- 当前交易日状态：{decision.get('trading_day_status')}",
        f"- AI模式：{ai.get('mode')}（{ai.get('provider')}）",
        f"- DQS：{dqs.get('score')} / {dqs.get('mode_label')}",
        f"- 数据完整性结论：{dqs.get('conclusion')}",
        "",
        "## 1. Stone CIO 今日决策卡",
        "",
        f"- 今日是否操作：{_yes_no(decision.get('today_trade'))}",
        f"- 今日操作类型：{decision.get('trade_type')}",
        f"- 今日操作金额：{_amount_mode_text(decision, budget.get('today_total_yuan', 0))}",
        f"- 今日操作标的：{decision.get('targets')}",
        f"- 资金来源：{decision.get('funding_source')}",
        f"- 一句话结论：{decision.get('one_sentence')}",
        "",
        "### 今日不操作或操作的三个核心原因",
        "",
        _items((decision.get("no_trade_reasons") or [])[:3]),
        "",
        f"- 当前最大风险：{decision.get('max_risk')}",
        f"- 当前最大机会：{decision.get('max_opportunity')}",
        f"- 下一次复核日期：{decision.get('next_review_date')}",
        "",
        "### 下一触发条件",
        "",
        _items((decision.get("next_triggers") or [])[:3]),
        "",
        "### 三种情景预案",
        "",
        "- 市场平稳：只在计划定投日复核，不追涨。",
        "- 下跌触发：指数回撤且DQS达标、资金到账后，优先评估核心ETF分批。",
        "- 快速上涨：不做机会追高，保留基础定投纪律。",
        "",
        "## 2. 今日资金计划",
        "",
        *_money_plan_table(decision),
        "",
        "### 现金口径",
        "",
        *_cash_table(decision),
        "",
        f"- 条件性债券转权益月度额度：{_yuan(budget.get('conditional_bond_to_equity_month_yuan'))}",
        f"- 本月批准债券转权益额度：{_yuan(budget.get('approved_bond_to_equity_month_yuan'))}",
        f"- 说明：{budget.get('funding_note')}",
        "",
        "## 3. 下一触发条件",
        "",
        _items(decision.get("next_triggers")),
        "",
        "## 4. 资产配置与偏离",
        "",
        *_allocation_table(decision),
        "",
        "## 5. 未来12个月资产迁移路线图",
        "",
        f"- 当前债券金额：{_yuan(migration.get('current_bond_yuan'))}",
        f"- 目标债券金额：{_yuan(migration.get('target_bond_yuan'))}",
        f"- 理论需转出金额：{_yuan(migration.get('theoretical_transfer_yuan'))}",
        f"- 每月建议转出上限：{_yuan(migration.get('monthly_cap_yuan'))}",
        f"- 本月批准额度：{_yuan(migration.get('approved_this_month_yuan'))}",
        f"- 实际到账资金：{_yuan(migration.get('actual_arrived_yuan'))}",
        f"- 本月已执行：{_yuan(migration.get('executed_this_month_yuan'))}",
        f"- 本月剩余额度：{_yuan(migration.get('remaining_this_month_yuan'))}",
        f"- 暂停转移条件：{'；'.join(migration.get('pause_conditions', []))}",
        f"- 加快转移条件：{'；'.join(migration.get('accelerate_conditions', []))}",
        f"- 优先配置方向：{'；'.join(migration.get('priority_targets', []))}",
        "",
        *_migration_table(decision),
        "",
        "## 6. Opportunity Score",
        "",
        *_opportunity_table(decision),
        "",
        "## 7. 持仓健康检查",
        "",
        *_holding_table(decision),
        "",
        "## 8. 市场与宏观",
        "",
        *_market_table(decision),
        "",
        "## 9. 市场风险评分",
        "",
        f"- 总分：{risk.get('score')} / 100",
        f"- 风险等级：{risk.get('level')}",
        "",
        *_risk_table(decision),
        "",
        "## 10. DQS数据质量",
        "",
        f"- 原始分：{dqs.get('raw_score')}",
        f"- 最终分：{dqs.get('score')}",
        f"- 缺失数据：{', '.join(dqs.get('missing_metrics', [])) or '无'}",
        f"- 冲突数据：{len(dqs.get('conflicts', []))}项",
        f"- 异常0值：{', '.join(dqs.get('suspicious_zero', [])) or '无'}",
        "",
        *_dqs_table(decision),
        "",
        "## 11. 未来7天事件",
        "",
        *_events(decision),
        "",
        "## 12. 三种市场情景",
        "",
        *_scenarios(decision),
        generate_grid_daily_section(decision.get("grid", {})),
        "",
        "## 13. AI分析与规则依据",
        "",
        f"- AI模式：{ai.get('mode')}",
        f"- 失败/降级原因：{ai.get('fallback_reason') or '无'}",
        f"- 重试次数：{ai.get('retry_count')}",
        f"- 可靠性影响：{ai.get('impact')}",
        f"- AI/规则摘要：{ai.get('summary')}",
        f"- 规则触发：DQS={dqs.get('score')}，风险={risk.get('score')}，现金可用={_yuan(budget.get('confirmed_cash_available_yuan'))}",
        "",
        "## 14. 数据来源",
        "",
        *_source_lines(decision),
        "",
        "## 15. 一致性验证",
        "",
        f"- 验证结果：{consistency.get('status', 'PASS' if consistency.get('ok') else 'FAIL')}",
        f"- 错误：{'; '.join(consistency.get('errors', [])) or '无'}",
        f"- 警告：{'; '.join(consistency.get('warnings', [])) or '无'}",
        "",
        "## 16. 免责声明",
        "",
        decision.get("disclaimer", "仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。"),
    ]
    return "\n".join(lines)


def generate_weekly_report(decision: dict[str, Any]) -> str:
    budget = decision.get("budget", {})
    return "\n".join(
        [
            "# Stone AI V12.5 Stable 周报",
            "",
            f"- 本周确认买入额度：{_yuan(budget.get('week_confirmed_yuan'))}",
            f"- 条件性债券转权益额度：{_yuan(budget.get('conditional_bond_to_equity_month_yuan'))}",
            f"- 下一复核日：{decision.get('next_review_date')}",
            f"- 当前风险：{decision.get('risk', {}).get('level')}，DQS={decision.get('dqs', {}).get('score')}",
            "- 本周纪律：未到账债券资金不得计入现金；重大事件前不追涨。",
            "",
            decision.get("disclaimer", ""),
        ]
    )


def generate_monthly_report(decision: dict[str, Any]) -> str:
    migration = decision.get("migration_plan", {})
    budget = decision.get("budget", {})
    return "\n".join(
        [
            "# Stone AI V12.5 Stable 月报",
            "",
            f"- 本月确认买入额度：{_yuan(budget.get('month_confirmed_yuan'))}",
            f"- 本月条件性债券转权益额度：{_yuan(budget.get('conditional_bond_to_equity_month_yuan'))}",
            f"- 理论需转出债券金额：{_yuan(migration.get('theoretical_transfer_yuan'))}",
            f"- 月度上限：{_yuan(migration.get('monthly_cap_yuan'))}",
            "- 月度原则：先确认资金到账，再分批配置权益ETF；不一次性大额切换。",
            "",
            decision.get("disclaimer", ""),
        ]
    )
