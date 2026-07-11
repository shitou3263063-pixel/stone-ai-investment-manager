from __future__ import annotations

from datetime import date
from typing import Any


def _yuan(value: Any) -> str:
    try:
        return f"{float(value):,.0f}元"
    except (TypeError, ValueError):
        return "0元"


def _wan(value: Any) -> str:
    try:
        return f"{float(value):.2f}万元"
    except (TypeError, ValueError):
        return "0.00万元"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _join(items: list[Any] | tuple[Any, ...] | None, fallback: str = "无") -> str:
    values = [str(item) for item in (items or []) if str(item).strip()]
    return "、".join(values) if values else fallback


def _amount_text(decision: dict[str, Any], key: str) -> str:
    amount = float(decision.get(key, 0) or 0)
    mode = decision.get("amount_mode")
    if mode == "upper_limit" and amount > 0:
        return f"不超过{_yuan(amount)}"
    if mode == "exact" and amount > 0:
        return _yuan(amount)
    if mode == "direction_only":
        return "仅给方向，不给具体金额"
    return "0元"


def generate_today_action(decision: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Stone CIO 今日行动摘要",
            "",
            f"操作等级：{decision.get('action_level', 'C')}级",
            f"基础定投：{_yes_no(decision.get('base_dca'))}（{decision.get('base_dca_status', 'unknown')}）",
            f"机会加仓：{_yes_no(decision.get('tactical_add'))}",
            f"今日再平衡：{_yes_no(decision.get('rebalance_today'))}",
            f"需要再平衡：{_yes_no(decision.get('rebalance_required'))}",
            f"今日减仓：{_yes_no(decision.get('reduce_positions'))}",
            f"DQS：{decision.get('dqs')}；金额模式：{decision.get('amount_label')}",
            "",
            f"今日买多少：{_amount_text(decision, 'today_buy_amount_yuan')}",
            f"本周买多少：{_amount_text(decision, 'week_buy_amount_yuan')}",
            f"本月买多少：{_amount_text(decision, 'month_buy_amount_yuan')}",
            f"条件性本月上限：{_yuan(decision.get('conditional_month_buy_upper_yuan', 0))}（仅在债券到期/赎回到账后考虑）",
            f"本周债券转权益上限：{_wan(decision.get('bond_weekly_transfer_wan', 0))}",
            f"本月债券转权益上限：{_wan(decision.get('bond_monthly_transfer_wan', 0))}",
            "",
            f"优先方向：{_join(decision.get('priority_assets'))}",
            f"暂停新增：{_join(decision.get('paused_assets'))}",
            f"最大风险：{_join(decision.get('warnings'))}",
            f"一句话结论：{decision.get('one_sentence', '')}",
            "",
            "声明：仅供投资辅助，不构成投资建议；系统不自动交易，所有操作必须人工确认。",
        ]
    )


def generate_daily_report(
    *,
    decision: dict[str, Any],
    portfolio_result: dict[str, Any],
    market_result: dict[str, Any],
    live_market_result: dict[str, Any],
    macro_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
    ai_advice_result: dict[str, Any],
    validation: dict[str, Any],
) -> str:
    source = decision.get("source_coverage", {}) or {}
    categories = portfolio_result.get("categories", []) or []
    events = macro_result.get("upcoming_events", []) or []
    quality = live_market_result.get("data_quality", {}) or {}
    audit = live_market_result.get("source_audit", {}) or quality.get("source_audit", {}) or {}

    lines = [
        "# Stone AI Investment Manager Pro V12 日报",
        "",
        f"日期：{date.today().isoformat()}",
        f"总资产：{_wan(decision.get('portfolio_value_wan'))}",
        f"操作等级：{decision.get('action_level', 'C')}级",
        f"DQS：{decision.get('dqs')}/100",
        "",
        "## 1. Stone CIO 今日结论",
        "",
        f"- 今日是否交易：{_yes_no(decision.get('today_buy_amount_yuan', 0) or decision.get('rebalance_today'))}",
        f"- 今日买多少：{_amount_text(decision, 'today_buy_amount_yuan')}",
        f"- 本周买多少：{_amount_text(decision, 'week_buy_amount_yuan')}",
        f"- 本月买多少：{_amount_text(decision, 'month_buy_amount_yuan')}",
        f"- 今日是否调仓：{_yes_no(decision.get('rebalance_today'))}",
        f"- 是否继续基础定投：{_yes_no(decision.get('base_dca'))}",
        f"- 是否机会加仓：{_yes_no(decision.get('tactical_add'))}",
        f"- 一句话结论：{decision.get('one_sentence')}",
        "",
        "## 2. 今日可执行清单",
        "",
        f"- 优先买入方向：{_join(decision.get('priority_assets'))}",
        f"- 暂停新增：{_join(decision.get('paused_assets'))}",
        f"- 债券转权益：本周上限 {_wan(decision.get('bond_weekly_transfer_wan', 0))}，本月上限 {_wan(decision.get('bond_monthly_transfer_wan', 0))}",
        f"- 现金安全线：当前 {_wan(decision.get('cash_current_wan', 0))}，底线 {_wan(decision.get('cash_floor_wan', 0))}，可动用 {_wan(decision.get('cash_available_wan', 0))}",
        "- 执行纪律：不自动下单，不满仓，不借钱，不在重大事件前追涨。",
        "",
        "## 3. 我的资产配置",
        "",
        "| 资产类别 | 当前占比 | 目标占比 | 偏离 | 状态 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for item in categories:
        lines.append(
            "| "
            f"{item.get('category', '')} | "
            f"{_pct(item.get('current_ratio', 0.0))} | "
            f"{_pct(item.get('target_ratio', 0.0))} | "
            f"{_pct(item.get('deviation_ratio', 0.0))} | "
            f"{item.get('status', '')} |"
        )

    lines.extend(
        [
            "",
            "## 4. 数据来源与质量",
            "",
            f"- 数据源覆盖率：{float(source.get('data_source_coverage', 0.0) or 0.0):.0%}",
            f"- 双源验证覆盖率：{float(source.get('dual_source_coverage', 0.0) or 0.0):.0%}",
            f"- 一级来源覆盖率：{float(source.get('tier1_coverage', 0.0) or 0.0):.0%}",
            f"- 数据可信度评分：{quality.get('score', decision.get('dqs'))}/100",
            f"- 数据覆盖结论：{audit.get('coverage_message', quality.get('quality_note', '以当前可用数据为准'))}",
            "",
            "## 5. 市场与宏观状态",
            "",
            f"- 市场风险评分：{decision.get('risk_score')}/100",
            f"- 市场摘要：{market_result.get('summary', '暂无')}",
            f"- 未来7天高等级宏观事件：{_yes_no(decision.get('macro_event_high_next_7_days'))}",
            "",
            "## 6. 风险与DQS硬门槛",
            "",
            f"- 金额模式：{decision.get('amount_label')}",
            f"- 最大风险：{_join(decision.get('warnings'))}",
            f"- 主要原因：{_join(decision.get('reasons'))}",
            "",
            "## 7. 未来7天事件",
            "",
        ]
    )
    if events:
        for event in events:
            lines.append(f"- {event.get('date')}：{event.get('name')}（{event.get('level')}）")
    else:
        lines.append("- 暂无已配置的高等级事件。")

    lines.extend(
        [
            "",
            "## 8. AI与规则模式",
            "",
            f"- AI状态：{decision.get('ai_status')}",
            f"- 实际模型/模式：{decision.get('llm_provider') or 'rule-only'}",
            f"- AI摘要：{ai_advice_result.get('summary', 'AI深度分析未启用，本次以本地规则和数据质量门槛为准。')}",
            "",
            "## 9. 一致性验证",
            "",
            f"- 验证结果：{'通过' if validation.get('ok') else '未通过，已降级'}",
            f"- 是否触发保守降级：{_yes_no(validation.get('fallback_applied'))}",
            f"- 错误：{_join(validation.get('errors') or validation.get('initial_errors'))}",
            f"- 警告：{_join(validation.get('warnings') or validation.get('initial_warnings'))}",
            "",
            "## 10. 免责声明",
            "",
            "本报告仅供投资辅助，不构成投资建议；系统不自动交易，不接券商下单权限，不承诺收益。所有操作必须由你人工确认后自行执行。",
        ]
    )
    return "\n".join(lines)


def generate_weekly_report(decision: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Stone AI 周报",
            "",
            f"- 本周操作等级：{decision.get('action_level')}级",
            f"- 本周买入计划：{_amount_text(decision, 'week_buy_amount_yuan')}",
            f"- 本周债券转权益上限：{_wan(decision.get('bond_weekly_transfer_wan', 0))}",
            f"- 本周风险：{_join(decision.get('warnings'))}",
            "- 下周关注：现金是否高于安全线、债券到期/赎回资金是否到账、DQS和双源验证是否恢复到可给精确金额的水平。",
            "",
            "声明：仅供投资辅助，不构成投资建议；系统不自动交易。",
        ]
    )


def generate_monthly_report(decision: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Stone AI 月报",
            "",
            f"- 本月买入计划：{_amount_text(decision, 'month_buy_amount_yuan')}",
            f"- 条件性本月上限：{_yuan(decision.get('conditional_month_buy_upper_yuan', 0))}",
            f"- 本月债券转权益上限：{_wan(decision.get('bond_monthly_transfer_wan', 0))}",
            "- 月度原则：不一次性大额切换，优先使用到期/赎回资金，保持现金安全线，分批把超配债券逐步转向权益。",
            "",
            "声明：历史和计划不代表未来收益，所有操作需人工确认。",
        ]
    )
