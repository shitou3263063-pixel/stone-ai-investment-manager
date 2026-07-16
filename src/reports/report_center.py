from __future__ import annotations

from datetime import date, timedelta
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
    if decision.get("today_confirmed_trade_executed") and float(amount or 0) > 0:
        return _yuan(amount)
    if not decision.get("today_trade") or float(amount or 0) <= 0:
        return "0元"
    mode = decision.get("dqs", {}).get("mode")
    if mode == "exact" and amount > 0:
        return _yuan(amount)
    if mode == "range" and amount > 0:
        return f"不超过 {_yuan(amount)}，需分批"
    if mode == "direction":
        return "只给方向，不给金额"
    return "0元"


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def build_today_action_payload(decision: dict[str, Any]) -> dict[str, Any]:
    budget = decision.get("budget", {})
    dqs = decision.get("dqs", {}) or {}
    risk = decision.get("risk", {}) or {}
    consistency = decision.get("consistency", {}) or {}
    opportunity = decision.get("opportunity", []) or []
    ranked_candidates = [
        row for row in opportunity
        if row.get("category") != "现金" and row.get("advice") not in {"暂停新增", "风险复核或回避"}
    ]
    top_opportunity = ranked_candidates[0] if ranked_candidates else {}
    confirmed_executed = bool(decision.get("today_confirmed_trade_executed"))
    today_amount = float(decision.get("today_amount_yuan", 0) or 0) if confirmed_executed else float(budget.get("today_total_yuan", 0) or 0)
    account_cash = float(budget.get("account_total_cash_yuan", 0) or 0)
    funding_source = str(decision.get("funding_source") or "不适用")
    cash_funded_amount = today_amount if decision.get("today_trade") and "现金" in funding_source else 0
    cash_snapshot = (decision.get("portfolio_snapshot", {}) or {}).get("cash", {}) or {}
    post_execution_cash = account_cash if confirmed_executed else max(0, account_cash - cash_funded_amount)

    anomalies: list[Any] = []
    anomalies.extend(consistency.get("errors", []) or [])
    anomalies.extend(consistency.get("warnings", []) or [])
    anomalies.extend(dqs.get("blocking_errors", []) or [])
    if dqs.get("missing_metrics"):
        anomalies.append(f"缺失数据：{', '.join(dqs['missing_metrics'])}")
    if dqs.get("conflicts"):
        anomalies.append(f"数据冲突：{len(dqs['conflicts'])}项")
    for row in decision.get("market_table", []) or []:
        if not row.get("success"):
            anomalies.append(f"{row.get('name', '关键数据')}：{row.get('error') or '数据拉取失败'}")
        elif row.get("stale"):
            anomalies.append(f"{row.get('name', '关键数据')}：使用过期数据")

    return {
        "report_date": decision.get("date"),
        "data_cutoff_time": decision.get("data_cutoff"),
        "execute": confirmed_executed or bool(decision.get("today_trade")),
        "confirmed_executed": confirmed_executed,
        "trade_origin": decision.get("trade_origin", "UNKNOWN"),
        "execution_status": decision.get("execution_status"),
        "system_pre_authorized": bool(decision.get("system_pre_authorized")),
        "opportunity_add": bool(decision.get("opportunity_add")),
        "discretionary_trade": bool(decision.get("discretionary_trade")),
        "event_chasing": bool(decision.get("event_chasing")),
        "asset_migration_attribute": decision.get("asset_migration_attribute"),
        "action_type": decision.get("trade_type") or "无操作",
        "targets": decision.get("targets") or "不适用",
        "amount_yuan": today_amount,
        "amount_or_range": _amount_mode_text(decision, today_amount),
        "funding_source": funding_source,
        "post_execution_cash_yuan": post_execution_cash,
        "pre_transaction_cash_yuan": float(cash_snapshot.get("opening_cash_before_bond_maturity_cny", 0) or 0),
        "cash_safety_reserve_yuan": float(budget.get("cash_safety_reserve_yuan", 0) or 0),
        "dqs": dqs.get("score"),
        "dqs_mode": dqs.get("mode_label"),
        "risk_score": risk.get("score"),
        "risk_level": risk.get("level"),
        "opportunity_score": (
            f"{top_opportunity.get('name')} {top_opportunity.get('score')}分（{top_opportunity.get('advice')}）"
            if top_opportunity
            else "暂无可靠评分"
        ),
        "next_review_date": decision.get("next_review_date"),
        "no_execute_reasons": _unique_text((decision.get("no_trade_reasons") or [])[:3]),
        "data_anomalies_or_baseline_conflicts": _unique_text(anomalies)[:5],
    }


def generate_today_action(decision: dict[str, Any]) -> str:
    action = build_today_action_payload(decision)
    no_execute = "；".join(action["no_execute_reasons"]) if (not action["execute"] or action["confirmed_executed"]) else "不适用"
    anomalies = "；".join(action["data_anomalies_or_baseline_conflicts"]) or "无"
    return "\n".join(
        [
            "# Stone CIO 今日交易确认单" if action["confirmed_executed"] else "# Stone CIO 今日执行单",
            "",
            f"- 报告日期：{action['report_date']}",
            f"- 数据截止时间：{action['data_cutoff_time']}",
            f"- 今日已确认执行：{_yes_no(action['execute'])}" if action["confirmed_executed"] else f"- 今日是否执行：{_yes_no(action['execute'])}",
            f"- 今日是否执行（Stone CIO当前建议）：{_yes_no(bool(decision.get('today_trade')))}" if action["confirmed_executed"] else "",
            f"- 操作类型：{action['action_type']}",
            f"- 标的：{action['targets']}",
            f"- 金额或金额区间：{action['amount_or_range']}",
            f"- 资金来源：真实可执行资金口径；{action['funding_source']}",
            f"- 交易前账户现金：{_yuan(action['pre_transaction_cash_yuan'])}" if action["confirmed_executed"] else "- 交易前账户现金：不适用",
            f"- 执行后账户现金余额：{_yuan(action['post_execution_cash_yuan'])}",
            f"- 固定现金安全储备：{_yuan(action['cash_safety_reserve_yuan'])}",
            f"- DQS：{action['dqs']}（{action['dqs_mode']}）",
            f"- Risk Score：{action['risk_score']}（{action['risk_level']}）",
            f"- Opportunity Score：{action['opportunity_score']}",
            f"- 下一复核日期：{action['next_review_date']}",
            f"- 后续不追加投入的核心约束：{no_execute}" if action["confirmed_executed"] else f"- 不执行的核心原因：{no_execute}",
            f"- 不执行的核心原因：{no_execute}" if action["confirmed_executed"] else "",
            f"- 数据异常或资产基线冲突：{anomalies}",
        ]
    )


def build_run_status(
    decision: dict[str, Any],
    *,
    report_files: list[str],
    email_status: str,
    email_error: str = "",
) -> dict[str, Any]:
    action = build_today_action_payload(decision)
    budget = decision.get("budget", {}) or {}
    consistency = decision.get("consistency", {}) or {}
    warnings = list(action["data_anomalies_or_baseline_conflicts"])
    errors = _unique_text(consistency.get("errors", []) or [])
    p1a = decision.get("cn_hk_p1a", {}) or {}
    tushare = p1a.get("tushare", {}) or {}
    akshare = p1a.get("akshare", {}) or {}
    akshare_valuation_interfaces = ((akshare.get("valuation") or {}).get("interfaces") or {})
    akshare_fundamental = ((akshare.get("fundamentals") or {}).get("002558.SZ") or {})
    akshare_conflicts = list(akshare.get("source_conflicts", []) or [])
    akshare_market_references = akshare.get("market_references", {}) or {}
    hkma = p1a.get("hkma", {}) or {}
    hkma_datasets = hkma.get("datasets", {}) or {}
    trade_calendar = tushare.get("trade_calendar", {}) or {}
    valuation = tushare.get("valuation", {}) or {}
    valuation_interfaces = valuation.get("interfaces", {}) or {}
    fundamental = ((tushare.get("fundamentals") or {}).get("002558.SZ") or {})
    if tushare.get("configured") and tushare.get("status") in {"failed", "partial"}:
        warnings.append(
            f"Tushare {tushare.get('error_code') or 'UNKNOWN_ERROR'}："
            f"{tushare.get('error_summary') or '部分或全部P1A接口不可用'}"
        )
    if akshare_conflicts:
        warnings.append(f"AKShare与主源存在{len(akshare_conflicts)}项SOURCE_CONFLICT，相关数据禁止进入评分。")
    if email_status == "failed" and email_error:
        warnings.append(f"邮件发送失败：{email_error}")
    warnings = _unique_text(warnings)
    status = "failed" if errors else ("warning" if warnings or email_status == "failed" else "success")
    return {
        "run_time": decision.get("generated_at"),
        "data_cutoff_time": decision.get("data_cutoff"),
        "report_date": decision.get("date"),
        "report_business_date": decision.get("report_business_date", decision.get("date")),
        "report_generated_at": decision.get("report_generated_at", decision.get("generated_at")),
        "decision_cutoff_at": decision.get("decision_cutoff_at", decision.get("data_cutoff")),
        "actual_trade_date": decision.get("actual_trade_date"),
        "report_run_mode": decision.get("report_run_mode"),
        "status": status,
        "dqs": decision.get("dqs", {}).get("score"),
        "risk_score": decision.get("risk", {}).get("score"),
        "total_assets": decision.get("portfolio_value_yuan"),
        "total_cash": budget.get("account_total_cash_yuan"),
        "cash_safety_reserve": budget.get("cash_safety_reserve_yuan"),
        "investable_cash": budget.get("investable_cash_yuan"),
        "today_action": {
            "execute": action["execute"],
            "action_type": action["action_type"],
            "targets": action["targets"],
            "amount_yuan": action["amount_yuan"],
            "amount_or_range": action["amount_or_range"],
            "funding_source": action["funding_source"],
            "manual_confirmation_required": True,
        },
        "next_review_date": decision.get("next_review_date"),
        "fund_classification": {
            "confirmed_fact_total_cash": budget.get("account_total_cash_yuan"),
            "rule_engine_investable_cash": budget.get("investable_cash_yuan"),
            "approved_bond_to_equity": budget.get("approved_bond_to_equity_month_yuan"),
            "actual_arrived_bond_cash": budget.get("actual_bond_cash_arrived_yuan"),
            "unsettled_bond_cash": 0,
            "remaining_bond_to_equity_cash": budget.get("bond_to_equity_remaining_real_cash_yuan"),
            "confirmed_executed_trade_today": decision.get("today_amount_yuan") if decision.get("today_confirmed_trade_executed") else 0,
            "simulated_grid_cash": budget.get("paper_grid_cash_yuan"),
            "real_executable_today": budget.get("today_total_yuan"),
        },
        "report_files": report_files,
        "email_status": email_status,
        "email_error": email_error,
        "cn_hk_p1a": {
            "cn_analysis_completeness": ((decision.get("cn_hk_analysis_completeness", {}) or {}).get("cn_analysis_completeness", {}) or {}).get("score_pct"),
            "hk_analysis_completeness": ((decision.get("cn_hk_analysis_completeness", {}) or {}).get("hk_analysis_completeness", {}) or {}).get("score_pct"),
            "tushare_configured": bool(((decision.get("cn_hk_p1a", {}) or {}).get("tushare", {}) or {}).get("configured")),
            "tushare_status": tushare.get("status", "missing"),
            "tushare_error_code": tushare.get("error_code"),
            "tushare_error_summary": tushare.get("error_summary"),
            "tushare_trade_calendar_status": trade_calendar.get("status", "missing"),
            "tushare_002558_valuation_status": (valuation_interfaces.get("002558_valuation") or {}).get("status", "missing"),
            "tushare_002558_fundamental_status": fundamental.get("status", "missing"),
            "tushare_csi300_valuation_status": (valuation_interfaces.get("csi300_valuation") or {}).get("status", "missing"),
            "tushare_last_success_at": tushare.get("last_success_at"),
            "akshare_status": akshare.get("status", "missing"),
            "akshare_version": akshare.get("version"),
            "akshare_trade_calendar_status": (akshare.get("trade_calendar") or {}).get("status", "missing"),
            "akshare_002558_valuation_status": (akshare_valuation_interfaces.get("002558_valuation") or {}).get("status", "missing"),
            "akshare_002558_fundamental_status": akshare_fundamental.get("status", "missing"),
            "akshare_csi300_valuation_status": (akshare_valuation_interfaces.get("csi300_valuation") or {}).get("status", "missing"),
            "akshare_source_conflicts": akshare_conflicts,
            "akshare_last_success_at": akshare.get("last_success_at"),
            "akshare_03033_history_status": (akshare_market_references.get("03033.HK") or {}).get("status", "missing"),
            "akshare_03033_history_date": (akshare_market_references.get("03033.HK") or {}).get("market_date"),
            "akshare_03033_underlying_provider": (akshare_market_references.get("03033.HK") or {}).get("underlying_provider"),
            "akshare_hstech_history_status": (akshare_market_references.get("HSTECH") or {}).get("status", "missing"),
            "akshare_hstech_history_date": (akshare_market_references.get("HSTECH") or {}).get("market_date"),
            "akshare_hstech_underlying_provider": (akshare_market_references.get("HSTECH") or {}).get("underlying_provider"),
            "effective_sources": ((p1a.get("effective_data") or {}).get("selected_sources") or {}),
            "hkma_status": hkma.get("status", "missing"),
            "hkma_hibor_status": (hkma_datasets.get("hibor") or {}).get("status", "missing"),
            "hkma_hibor_date": (hkma_datasets.get("hibor") or {}).get("market_date"),
            "hkma_hibor_freshness": (hkma_datasets.get("hibor") or {}).get("freshness", "unavailable"),
            "hkma_exchange_rate_status": (hkma_datasets.get("exchange_rate") or {}).get("status", "missing"),
            "hkma_exchange_rate_date": (hkma_datasets.get("exchange_rate") or {}).get("market_date"),
            "hkma_exchange_rate_freshness": (hkma_datasets.get("exchange_rate") or {}).get("freshness", "unavailable"),
            "official_announcement_status": (((decision.get("cn_hk_p1a", {}) or {}).get("announcements", {}) or {}).get("status", "missing")),
            "high_confidence_buy_restricted": any(
                float(((decision.get("cn_hk_analysis_completeness", {}) or {}).get(key, {}) or {}).get("score_pct", 0) or 0) < 60
                for key in ["cn_analysis_completeness", "hk_analysis_completeness"]
            ) or bool(akshare_conflicts) or float(decision.get("dqs", {}).get("score", 0) or 0) < 85,
        },
        "warnings": warnings,
        "errors": errors,
    }


def _money_plan_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| budget_id | 类型 | 是否执行 | 交易计入金额 | 迁移属性金额 | 计入实际交易合计 | 标的 | 资金来源 | 原因 |",
        "| -- | -- | ---- | -: | -: | ---- | -- | ---- | -- |",
    ]
    for item in decision.get("budget", {}).get("rows", []) or []:
        rows.append(
            "| "
            f"{_text(item.get('budget_id'), '不适用')} | "
            f"{_text(item.get('type'))} | "
            f"{_yes_no(item.get('execute'))} | "
            f"{_yuan(item.get('amount_yuan', 0))} | "
            f"{_yuan(item.get('attributed_amount_yuan', 0))} | "
            f"{_yes_no(item.get('counts_toward_actual_trade_total'))} | "
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
        f"| 固定现金安全储备 | {_yuan(budget.get('cash_safety_reserve_yuan'))} | 用户明确确认的固定金额，不再按总资产8%动态上调 |",
        f"| 债券转权益专项可投资现金 | {_yuan(budget.get('bond_to_equity_remaining_real_cash_yuan'))} | 已到账，不占用固定安全储备 |",
        f"| 当前真实可投资现金 | {_yuan(budget.get('investable_cash_yuan'))} | 账户总现金扣除固定安全储备和占用资金后余额 |",
        f"| 网格实盘专用现金 | {_yuan(budget.get('live_grid_cash_yuan'))} | 当前未启用真实网格交易 |",
        f"| 网格模拟现金 | {_yuan(budget.get('paper_grid_cash_yuan'))} | 只用于SIMULATION，不计入真实资产 |",
        "| 条件性未到账资金 | 0元 | 本次30,000元债券到期资金已实际到账 |",
    ]
    return rows


def _transaction_change_table(decision: dict[str, Any]) -> list[str]:
    budget = decision.get("budget", {}) or {}
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    cash = snapshot.get("cash", {}) or {}
    allocation = {row.get("category"): row for row in decision.get("allocation", []) or []}
    before_cash = float(cash.get("opening_cash_before_bond_maturity_cny", 0) or 0)
    arrival = float(cash.get("bond_maturity_arrival_cny", 0) or 0)
    purchase = float(cash.get("voo_purchase_outflow_cny", 0) or 0)
    transactions = decision.get("confirmed_transactions", []) or []
    trade = transactions[0] if transactions else {}
    quantity = trade.get("quantity")
    fx_rate = trade.get("actual_fx_rate_cny_per_usd")
    fee = trade.get("fee")
    return [
        "| 项目 | 交易前 | 变动 | 交易后 / 当前口径 |",
        "| -- | --: | --: | -- |",
        f"| 账户总现金 | {_yuan(before_cash)} | 债券到期 +{arrival:,.0f}元；VOO买入 -{purchase:,.0f}元 | {_yuan(budget.get('account_total_cash_yuan'))} |",
        f"| 固定现金安全储备 | {_yuan(budget.get('cash_safety_reserve_yuan'))} | 0元 | {_yuan(budget.get('cash_safety_reserve_yuan'))}，未占用 |",
        "| 到期债券本金（不含TLT） | 1,130,000元 | -30,000元 | 1,100,000元 |",
        f"| 债券资产配置（含TLT） | 原报告漏计TLT 55,000元 | TLT重分类为债券 | {_yuan((allocation.get('债券') or {}).get('current_amount_yuan'))} |",
        "| VOO原确认市值 | 130,000元 | 不机械增加9,000元 | 仍为130,000元（原确认口径）；最新市值待实际股数、最新价格和实际汇率补齐后重算 |",
        f"| VOO本次新增投入成本 | 0元 | +{purchase:,.0f}元 | {purchase:,.0f}元，单列待估值，不等同于新增市值 |",
        f"| 当前真实可投资现金 | 0元 | 债券到账后净增加{budget.get('investable_cash_yuan', 0):,.0f}元 | {_yuan(budget.get('investable_cash_yuan'))} |",
        "",
        f"- 成交价格：{trade.get('execution_price_usd', '待补充')}美元/份",
        f"- 成交股数：{quantity if quantity is not None else '待补充'}",
        f"- 实际换汇汇率：{fx_rate if fx_rate is not None else '待补充'}",
        f"- 手续费：{fee if fee is not None else '待补充'}",
        "- VOO最新市值：待实际成交股数、最新市场价格和实际汇率补齐后重新计算。",
    ]


def _opportunity_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 市场内分组 | 标的 | Market Attractiveness Score | Portfolio Repair Priority | 今日交易权限 | 当前持仓动作 | 可信度 | 最终动作 | 当前持仓 | 主要原因 |",
        "| -- | -- | ---: | ---: | -- | -- | -- | -- | ---: | -- |",
    ]
    for item in decision.get("opportunity", []) or []:
        completeness_value = item.get("market_data_completeness")
        completeness_display = "不适用" if completeness_value is None else f"{float(completeness_value):.1f}%"
        analysis_value = item.get("analysis_data_completeness")
        analysis_display = "不适用" if analysis_value is None else f"{float(analysis_value):.1f}%"
        rows.append(
            "| "
            f"{item.get('market_internal_rank_scope', item.get('category', '未分组'))} | {item['name']} | {item.get('market_attractiveness_score', item['score'])} | {item.get('portfolio_repair_priority_score', 0)} | "
            f"{_yes_no(item.get('today_trade_permission'))} | {item.get('current_holding_action', '不适用')} | "
            f"{item.get('confidence', item.get('scoring_confidence', '可用'))} | "
            f"{item.get('final_action', item['advice'])} | {_yuan(item['current_holding_yuan'])} | {item.get('reason', '暂无')} |"
        )
    return rows


def _portfolio_repair_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 资产类别 | 当前占比 | 目标占比 | 偏离 | 偏离金额 | 修复方向 | Portfolio Repair Priority | 优先宽基 | 今日权限 |",
        "| -- | --: | --: | --: | --: | -- | --: | -- | -- |",
    ]
    for item in decision.get("portfolio_repair_priority", []) or []:
        rows.append(
            f"| {item.get('category')} | {_pct(item.get('current_ratio'))} | {_pct(item.get('target_ratio'))} | "
            f"{float(item.get('deviation_ratio', 0) or 0):+.1%} | {_yuan(item.get('deviation_amount_yuan'))} | "
            f"{item.get('repair_direction')} | {item.get('portfolio_repair_priority')} | "
            f"{item.get('preferred_broad_market_instrument')} | {_yes_no(item.get('today_trade_permission'))} |"
        )
    return rows


def _scenario_permission_table(decision: dict[str, Any]) -> list[str]:
    gates = decision.get("trade_permission_gates", {}) or {}
    rows = [
        "| 操作类型 | 当前DQS | 要求DQS | DQS | 计划 | 现金 | 风险 | 事件 | 最终权限 | 主要拒绝原因 |",
        "| -- | --: | --: | -- | -- | -- | -- | -- | -- | -- |",
    ]
    for item in gates.get("scenarios", []) or []:
        reasons = "；".join(item.get("exact_denial_reasons", []) or []) or "无"
        rows.append(
            f"| {item.get('scenario_name')} | {item.get('scenario_dqs')} | {item.get('required_dqs')} | "
            f"{_yes_no(item.get('dqs_gate_passed'))} | {_yes_no(item.get('schedule_gate_passed'))} | "
            f"{_yes_no(item.get('cash_gate_passed'))} | {_yes_no(item.get('risk_gate_passed'))} | "
            f"{_yes_no(item.get('event_gate_passed'))} | {item.get('final_permission')} | {reasons} |"
        )
    return rows


def _cn_hk_completeness_table(decision: dict[str, Any]) -> list[str]:
    completeness = decision.get("market_completeness", {}) or {}
    rows = [
        "| 市场 | 行情字段覆盖率 | 评分可信度 | 是否限制决策 | 缺失字段 |",
        "| -- | --: | -- | -- | -- |",
    ]
    for key, label in [("cn_data_completeness", "A股"), ("hk_data_completeness", "港股及港股主题基金")]:
        item = completeness.get(key, {}) or {}
        rows.append(
            f"| {label} | {float(item.get('score_pct', 0) or 0):.1f}% | {item.get('confidence', 'low')} | "
            f"{'是' if item.get('decision_restricted', True) else '否'} | "
            f"{'、'.join(item.get('missing_fields', []) or ['无'])} |"
        )
    rows.extend(["", "| 标的 | 正式名称 | 代码 | 交易所 | 市场 | 币种 | 时区 | 市场日期 | 来源 | 数据状态 |", "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |"])
    for key in ["cn_data_completeness", "hk_data_completeness"]:
        for item in (completeness.get(key, {}) or {}).get("items", []) or []:
            rows.append(
                f"| {item.get('symbol')} | {item.get('official_name')} | {item.get('symbol')} | "
                f"{item.get('exchange')} | {item.get('market')} | {item.get('currency')} | {item.get('timezone')} | "
                f"{item.get('market_date') or '暂无可靠数据'} | {item.get('source')} | {item.get('data_status')} |"
            )
    return rows


def _cn_hk_p1a_table(decision: dict[str, Any]) -> list[str]:
    snapshot = decision.get("cn_hk_p1a", {}) or {}
    analysis = snapshot.get("analysis_completeness", {}) or {}
    tushare = snapshot.get("tushare", {}) or {}
    akshare = snapshot.get("akshare", {}) or {}
    effective = snapshot.get("effective_data", {}) or {}
    hkma = snapshot.get("hkma", {}) or {}
    announcements = snapshot.get("announcements", {}) or {}
    rows = [
        "| P1A项目 | 状态/完整度 | 来源 | 是否进入评分 | 缺失或限制 |",
        "| -- | -- | -- | -- | -- |",
    ]
    for key, label in [("cn_analysis_completeness", "A股整体分析"), ("hk_analysis_completeness", "港股整体分析")]:
        item = analysis.get(key, {}) or {}
        rows.append(
            f"| {label} | {float(item.get('score_pct', 0) or 0):.1f}%（{item.get('confidence', 'low')}） | 多源汇总 | "
            f"{'受门槛约束' if item.get('decision_restricted', True) else '是'} | {'、'.join(item.get('missing_fields', []) or ['无'])} |"
        )
    fundamentals = ((tushare.get("fundamentals") or {}).get("002558.SZ") or {})
    valuation = ((tushare.get("valuation") or {}).get("items") or {})
    trade_calendar = tushare.get("trade_calendar") or {}
    rows.extend([
        f"| A股交易日历 | {trade_calendar.get('status', 'missing')} | Tushare Pro | 否，仅用于日期校验 | "
        f"{trade_calendar.get('error_code') or '无'}：{trade_calendar.get('error_summary') or '无'} |",
        f"| 002558估值 | {(valuation.get('002558.SZ') or {}).get('status', 'missing')} | Tushare Pro | 是（成功时） | {(valuation.get('002558.SZ') or {}).get('error_message') or '无'} |",
        f"| 002558财务 | {fundamentals.get('status', 'missing')} | Tushare Pro | 是（成功时） | {fundamentals.get('note') or '无'} |",
        f"| 港元流动性/HIBOR/汇率 | {hkma.get('status', 'missing')} | HKMA官方 | 是（成功时） | {'、'.join(hkma.get('missing_fields', []) or ['无'])} |",
        f"| A股官方公告 | {(announcements.get('cn') or {}).get('status', 'missing')} | 巨潮资讯 | 否，仅作风险解释 | {(announcements.get('cn') or {}).get('error_message') or '无'} |",
        f"| 港股官方公告 | {(announcements.get('hk') or {}).get('status', 'missing')} | HKEX | 否，仅作风险解释 | {(announcements.get('hk') or {}).get('error_message') or '无'} |",
    ])
    ak_valuation = ((akshare.get("valuation") or {}).get("items") or {})
    ak_fundamental = ((akshare.get("fundamentals") or {}).get("002558.SZ") or {})
    selected = effective.get("selected_sources", {}) or {}
    for key, label, record, scoring_note in [
        ("trade_calendar", "AKShare A股交易日历", akshare.get("trade_calendar", {}) or {}, "否，仅用于日期校验"),
        ("002558_valuation", "AKShare 002558估值", ak_valuation.get("002558.SZ", {}) or {}, "通过校验且被选中时"),
        ("002558_fundamental", "AKShare 002558财务", ak_fundamental, "通过校验且被选中时"),
        ("csi300_valuation", "AKShare 沪深300估值", ak_valuation.get("510300.SS", {}) or {}, "通过校验且被选中时"),
    ]:
        underlying = record.get("underlying_provider") or "未识别"
        used = selected.get(key) == "akshare" and bool(record.get("scoring_eligible"))
        rows.append(
            f"| {label} | {record.get('status', 'missing')} | AKShare（底层：{underlying}） | "
            f"{'是' if used else scoring_note} | "
            f"{record.get('error_code') or record.get('error_message') or ('市场日期未明确或未通过评分校验，仅展示' if not record.get('scoring_eligible') else '无')} |"
        )
    market_references = akshare.get("market_references", {}) or {}
    successful_display = [
        f"{symbol}({record.get('underlying_provider')})"
        for symbol, record in market_references.items()
        if record.get("status") in {"ok", "cached"}
    ]
    rows.append(
        f"| AKShare基础行情核验 | {akshare.get('status', 'missing')} | AKShare受监控备用 | 否，仅展示/冲突核验 | "
        f"{'成功：' + '、'.join(successful_display) if successful_display else '无成功行情；不以代理或0值替代'} |"
    )
    for symbol, label in [("03033.HK", "03033.HK历史行情"), ("HSTECH", "恒生科技指数历史行情")]:
        record = market_references.get(symbol, {}) or {}
        rows.append(
            f"| {label} | {record.get('status', 'missing')}（{record.get('market_date') or '无日期'}） | "
            f"AKShare（底层：{record.get('underlying_provider') or '未识别'}） | 否，仅展示/同日核验 | "
            f"{record.get('error_code') or record.get('comparison_reason') or record.get('error_message') or '无'} |"
        )
    hkma_datasets = hkma.get("datasets", {}) or {}
    for key, label in [("hibor", "HKMA HIBOR"), ("exchange_rate", "HKMA港元汇率")]:
        record = hkma_datasets.get(key, {}) or {}
        usable = record.get("status") in {"ok", "cached"} and record.get("freshness") == "fresh"
        rows.append(
            f"| {label} | {record.get('status', 'missing')}（{record.get('market_date') or '无日期'}；{record.get('freshness', 'unavailable')}） | "
            f"HKMA官方（底层：{record.get('underlying_provider') or 'hkma_open_api'}） | {'是' if usable else '否，过期或失败仅展示'} | "
            f"{record.get('error_code') or record.get('error_message') or '无'} |"
        )
    if akshare.get("source_conflicts"):
        rows.append(
            f"| AKShare来源冲突 | SOURCE_CONFLICT | 主源 vs AKShare | 否 | "
            f"{len(akshare.get('source_conflicts') or [])}项；已降低DQS并限制高置信度加仓 |"
        )
    rows.append("")
    rows.append("- ETF财务规则：510300、513060、513090及03033均不套用002558个股财务评分。")
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
    session_labels = {
        "realtime": "实时",
        "intraday_delayed": "盘中或延迟盘中",
        "official_close": "当日官方收盘",
            "previous_close": "延迟/上一交易时段收盘（非盘中正式收盘）",
        "official_lagged_macro": "最新官方滞后数据",
        "stale": "过期数据",
        "unavailable": "不可用",
    }
    pair_comparable = bool((decision.get("risk", {}).get("market_time_consistency", {}) or {}).get("comparable"))
    rows = [
        "| 指标 | 当前值 | 前值 | 涨跌幅 | market_date | price_stage | quote_timestamp | retrieved_at | data_age_hours | 数据依据 | 可比较 | 来源 | 状态 |",
        "| -- | --: | --: | --: | -- | -- | -- | -- | --: | -- | -- | -- | -- |",
    ]
    for item in decision.get("market_table", []) or []:
        change = "暂无数据" if item.get("change_pct") is None else f"{float(item['change_pct']):.2f}%"
        previous = "暂无数据" if item.get("previous") is None else f"{float(item['previous']):.2f}"
        status = "成功" if item.get("success") else _text(item.get("error"), "请求失败")
        age = "暂无" if item.get("age_hours") is None else f"{float(item['age_hours']):.1f}小时"
        comparable = "是" if item.get("name") in {"VOO", "QQQ"} and pair_comparable else "否"
        rows.append(
            "| "
            f"{item['name']} | {item['display_value']} | {previous} | {change} | {item.get('market_date') or '未知'} | "
            f"{item.get('price_stage') or item.get('data_stage', 'UNKNOWN')} | {item.get('quote_timestamp') or 'None'} | "
            f"{item.get('retrieved_at') or 'None'} | {age} | {item.get('data_basis') or session_labels.get(item.get('data_session'), '未知')} | "
            f"{comparable} | {item['source']} | {status} |"
        )
    return rows


def _risk_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 风险维度 | 分数 | 等级/含义 |",
        "| -- | --: | -- |",
    ]
    risk = decision.get("risk", {}) or {}
    for key, label, meaning in [
        ("market_risk", "Market Risk Score", "市场条件"),
        ("portfolio_risk", "Portfolio Risk Score", "组合暴露与压力测试"),
        ("data_confidence", "Data Confidence Score (DQS)", "仅代表数据质量"),
        ("execution_risk", "Execution Risk Score", "执行条件与人工确认"),
    ]:
        item = risk.get(key, {}) or {}
        rows.append(f"| {label} | {item.get('score', '暂无')} | {item.get('level', '暂无')}；{meaning} |")
    rows.extend([
        "",
        f"- 市场风险权重合计：{(risk.get('market_risk', {}) or {}).get('market_risk_weights_sum', '暂无')}%",
        f"- 市场风险置信度：{(risk.get('market_risk', {}) or {}).get('confidence', '暂无')}",
        "",
        "| 市场风险项目 | 分数 | 权重 | 主要依据 |",
        "| ----- | -: | -: | ---- |",
    ])
    for item in risk.get("market_risk", risk).get("components", []) or []:
        rows.append(f"| {item['item']} | {item['score']} | {item['weight']} | {item['basis']} |")
    return rows


def _dqs_table(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| DQS项目 | 得分 | 满分 | 原因 |",
        "| ----- | -: | -: | -- |",
    ]
    for item in decision.get("dqs", {}).get("components", []) or []:
        rows.append(f"| {item['item']} | {item['score']} | {item['max']} | {item['reason']} |")
    rows.extend([
        "",
        "| 使用场景 | 得分 | 门槛 | DQS门槛通过 | 计划门槛通过 | 现金门槛通过 | 风险门槛通过 | 事件门槛通过 | 最终权限 | 拒绝原因 |",
        "| -- | -: | -: | -- | -- | -- | -- | -- | -- | -- |",
    ])
    for item in (decision.get("dqs", {}).get("use_cases", {}) or {}).values():
        rows.append(
            f"| {item.get('label')} | {item.get('score')} | {item.get('threshold')} | "
            f"{_yes_no(item.get('dqs_gate_passed', item.get('allowed')))} | "
            f"{_yes_no(item.get('schedule_gate_passed')) if 'schedule_gate_passed' in item else '不适用'} | "
            f"{_yes_no(item.get('cash_gate_passed')) if 'cash_gate_passed' in item else '不适用'} | "
            f"{_yes_no(item.get('risk_gate_passed')) if 'risk_gate_passed' in item else '不适用'} | "
            f"{_yes_no(item.get('event_gate_passed')) if 'event_gate_passed' in item else '不适用'} | "
            f"{item.get('final_permission', '是' if item.get('allowed') else '否')} | "
            f"{item.get('denial_reason', '无')} |"
        )
    return rows


def _events(decision: dict[str, Any]) -> list[str]:
    events = decision.get("upcoming_events", []) or [
        event for event in (decision.get("events", []) or []) if event.get("status") == "UPCOMING"
    ]
    if not events:
        return ["- 未来7天暂无已核验待发布事件。"]
    lines = []
    for event in events:
        lines.append(
            f"- {event.get('event_name', event.get('name', '暂无名称'))}（参考期{event.get('reference_period', '未知')}）："
            f"{event.get('release_at_report_timezone') or '发布时间未验证'}（报告时区），"
            f"源时区发布时间：{event.get('release_at') or '未验证'} {event.get('source_timezone', '未知')}，"
            f"风险等级：{event.get('risk_level', event.get('level', '暂无'))}，"
            f"核验状态：{event.get('verification_status', 'unverified')}，来源等级：{event.get('source_level', '未知')}，"
            f"事件状态：{event.get('status', 'INVALID_TIME')}，"
            f"来源：{event.get('source', '暂无可靠来源')}；"
            "纪律：事件前不追涨，定投可复核但不一次性重仓。"
        )
    return lines


def _released_events(decision: dict[str, Any]) -> list[str]:
    events = decision.get("released_events", []) or [
        event for event in (decision.get("events", []) or []) if event.get("status") in {"RELEASED", "RELEASED_DATA_MISSING"}
    ]
    if not events:
        return ["- 本次日历中暂无已发布事件。"]
    return [
        f"- {event.get('event_name', event.get('name', '暂无名称'))}："
        f"已于{event.get('release_at_report_timezone') or '时间无效'}到达发布时间，状态{event.get('status', 'RELEASED')}；"
        f"实际值：{event.get('actual_value') if event.get('actual_value') is not None else '缺失'}，"
        f"前值：{event.get('previous_value') if event.get('previous_value') is not None else '缺失'}，"
        f"修正值：{event.get('revised_value') if event.get('revised_value') is not None else '缺失'}，"
        f"预期：{event.get('consensus_value') if event.get('consensus_value') is not None else '无可靠预期'}；"
        f"数据状态：{event.get('event_data_status', 'RELEASED_DATA_MISSING')}；{event.get('rule_interpretation', '')}"
        for event in events
    ]


def _consistency_table(decision: dict[str, Any]) -> list[str]:
    rows = ["| 检查项 | 状态 | 说明 |", "| -- | -- | -- |"]
    for item in decision.get("consistency", {}).get("checks", []) or []:
        rows.append(f"| {item.get('check_item')} | {item.get('status')} | {item.get('description')} |")
    return rows


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


def _stress_scenarios(decision: dict[str, Any]) -> list[str]:
    rows = [
        "| 情景 | 组合收益/损失率 | 组合收益/损失金额 | 最大正贡献/负贡献资产 | 超过25% | 超过35% | 是否需调整长期配置 |",
        "| -- | --: | --: | -- | -- | -- | -- |",
    ]
    for item in decision.get("stress_scenarios", []) or []:
        contributors = "；".join(
            f"{row['category']} {row['impact_yuan']:+,.0f}元" for row in item.get("largest_contributors", [])
        ) or "暂无"
        largest = item.get("largest_contributor", {}) or {}
        rows.append(
            f"| {item.get('name')} | {float(item.get('portfolio_return', 0)):.2%} | "
            f"{float(item.get('portfolio_change_yuan', 0)):+,.0f}元 | {largest.get('category', '暂无')} {largest.get('impact_yuan', 0):+,.0f}元 | "
            f"{'是' if item.get('exceeds_tolerance_low') else '否'} | {'是' if item.get('exceeds_tolerance_high') else '否'} | {'是' if item.get('long_term_allocation_review_required') else '否'} |"
        )
    rows.append("")
    rows.append("- 说明：以上均为静态假设测算，不是预测，不直接形成自动交易指令。")
    return rows


def _market_context_status(decision: dict[str, Any]) -> list[str]:
    status = decision.get("market_context_status", {}) or {}
    rows = [
        "| 数据项 | 当前值 | 前值 | 时间 | 来源 | 等级 | 状态 | 双源验证 | 说明 |",
        "| -- | --: | --: | -- | -- | --: | -- | -- | -- |",
    ]
    for item in status.get("indicators", []) or []:
        value = "暂无可靠数据" if item.get("value") is None else str(item.get("value"))
        previous = "暂无可靠数据" if item.get("previous_value") is None else str(item.get("previous_value"))
        rows.append(
            f"| {item.get('name')} | {value} | {previous} | {item.get('timestamp') or '暂无'} | "
            f"{item.get('source') or 'unavailable'} | {item.get('source_level', 99)} | "
            f"{item.get('status', 'missing')} | {'是' if item.get('verified_by_second_source') else '否'} | "
            f"{item.get('note') or '暂无'} |"
        )
    if len(rows) == 2:
        rows.append("| 市场宽度/资金流/情绪 | 暂无可靠数据 | 暂无可靠数据 | 暂无 | unavailable | 99 | 未接入 | 否 | 本次输入未提供状态数据。 |")
    return rows or ["- 市场宽度、资金流和情绪状态暂不可用。"]


def _source_lines(decision: dict[str, Any]) -> list[str]:
    rows = []
    for item in decision.get("market_table", []) or []:
        if item.get("success"):
            rows.append(f"- {item['name']}：{item['source']}，时间：{item['timestamp']}，等级：{item['source_tier']}")
    return rows or ["- 本次没有可靠在线数据源成功返回，报告进入降级模式。"]


def _generate_final_freeze_daily_report(decision: dict[str, Any]) -> str:
    dqs = decision.get("dqs", {}) or {}
    risk = decision.get("risk", {}) or {}
    budget = decision.get("budget", {}) or {}
    consistency = decision.get("consistency", {}) or {}
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    reconciliation = decision.get("trade_reconciliation", {}) or {}
    comparability = decision.get("comparability", {}) or {}
    gates = decision.get("trade_permission_gates", {}) or {}
    ai = decision.get("ai", {}) or {}
    transactions = decision.get("confirmed_transactions", []) or []
    provisional = bool(snapshot.get("has_provisional_values") or reconciliation.get("status") == "WARN")

    trade_rows = [
        "| 交易日期 | 成交时间 | 标的 | 方向 | 数量 | 成交价/币种 | 人民币投入 | 资金来源 | 来源类型 | 对账状态 |",
        "| -- | -- | -- | -- | --: | -- | --: | -- | -- | -- |",
    ]
    for trade in transactions:
        trade_rows.append(
            f"| {trade.get('trade_date')} | {trade.get('trade_datetime') or '待补'} | {trade.get('symbol')} | "
            f"{trade.get('side') or trade.get('action')} | {trade.get('quantity') if trade.get('quantity') is not None else '待补'} | "
            f"{trade.get('execution_price') or trade.get('execution_price_usd')} {trade.get('trade_currency') or 'USD'} | "
            f"{_yuan(trade.get('invested_amount_cny'))} | {trade.get('funding_source')} | {trade.get('trade_origin')} | "
            f"{trade.get('reconciliation_status')} |"
        )
    if len(trade_rows) == 2:
        trade_rows.append("| 无 | 无 | 无 | 无 | 无 | 无 | 0元 | 无 | 无 | NOT_APPLICABLE |")

    anomalies: list[str] = []
    if reconciliation.get("status") == "WARN":
        anomalies.append("VOO成交字段待补：" + "、".join(reconciliation.get("missing_fields", []) or []))
    if snapshot.get("unconfirmed_holdings"):
        anomalies.append(
            "UNCONFIRMED_HOLDING："
            + "、".join(str(row.get("security_name") or row.get("asset_id")) for row in snapshot["unconfirmed_holdings"])
            + "（已排除正式计算）"
        )
    if comparability.get("non_comparable_items_count", 0):
        anomalies.append(
            f"不可比较项目{comparability.get('non_comparable_items_count')}项："
            + "、".join(comparability.get("non_comparable_items", []) or [])
        )
    anomalies.extend(str(item) for item in consistency.get("warnings", []) or [])
    anomalies.extend("错误：" + str(item) for item in consistency.get("errors", []) or [])
    anomalies = list(dict.fromkeys(anomalies)) or ["无阻断级异常。"]

    risk_gate_lines: list[str] = []
    for item in gates.get("scenarios", []) or []:
        contributors = "、".join(item.get("risk_top_contributors", []) or []) or "无"
        risk_blocks = "；".join(item.get("risk_blocking_factors", []) or []) or "无"
        event_blocks = "；".join(item.get("event_blocking_factors", []) or []) or "无"
        risk_gate_lines.append(
            f"- {item.get('scenario_name')}：risk_threshold={item.get('risk_threshold')}，"
            f"current_risk_score={item.get('current_risk_score')}，主要贡献={contributors}，"
            f"risk_blocking_factors={risk_blocks}，event_blocking_factors={event_blocks}，"
            f"阻断依据={item.get('risk_blocking_basis')}。"
        )

    grid_section = generate_grid_daily_section(decision.get("grid", {})).replace("## ", "### ")
    grid_section = grid_section.replace("# Stone Smart Grid", "## 附录 E. Stone Smart Grid（SIMULATION_ONLY）", 1)
    lines = [
        f"# Stone AI Investment Manager Pro V12.7.1 Final Freeze 日报（{decision.get('report_run_mode_label', '正常运行')}）",
        "",
        "## 0. 报告状态",
        "",
        f"- 业务日期：{decision.get('report_business_date', decision.get('date'))}",
        f"- 运行模式：{decision.get('report_run_mode')}（{decision.get('report_run_mode_label')}）",
        f"- 生成时间：{decision.get('report_generated_at', decision.get('generated_at'))}",
        f"- 数据截止时间：{decision.get('decision_cutoff_at', decision.get('data_cutoff'))}",
        f"- 数据质量：DQS={dqs.get('score')}（{dqs.get('mode_label')}）",
        f"- 是否存在暂估数据：{_yes_no(provisional)}",
        f"- 全局最终交易权限：{gates.get('global_final_permission', 'DENY')}（来源场景：{gates.get('final_trade_permission_source', 'Scheduled DCA')}）",
        f"- 历史实盘交易日期：{decision.get('actual_trade_date') or '无'}",
        "- 所有真实交易均须用户人工确认；系统不自动交易。",
        "",
        *( ["> **存在未完成对账的实盘交易。总资产、美股市值和资产占比包含暂估口径，不作为精确再平衡依据。**", ""] if provisional else [] ),
        *_scenario_permission_table(decision),
        "",
        "## 1. 今日决策卡",
        "",
        f"- 今日是否操作：{_yes_no(decision.get('today_trade'))}",
        f"- 操作类型：{decision.get('trade_type')}",
        f"- 标的：{decision.get('targets', '不适用')}",
        f"- 建议金额：{_amount_mode_text(decision, budget.get('today_total_yuan', 0))}",
        f"- 资金来源：{decision.get('funding_source', '今日不使用资金')}",
        f"- 可投资现金：{_yuan(budget.get('investable_cash_yuan'))}",
        f"- DQS：{dqs.get('score')}；Market Risk Score：{risk.get('score')}",
        f"- 最大风险：{decision.get('max_risk')}",
        f"- 下一复核时间：{decision.get('next_daily_review')}",
        f"- 警告：{'；'.join(consistency.get('warnings', []) or []) or '无'}",
        f"- 错误：{'；'.join(consistency.get('errors', []) or []) or '无'}",
        "",
        "## 2. 已执行交易事实",
        "",
        *trade_rows,
        "",
        "- 历史已执行交易：以上记录仅按真实交易日期归档，不冒充今日建议。",
        "- 今日建议：由第1节和场景权限表单独给出。",
        "- 条件性计划：仅为未来触发条件，不是当前交易指令。",
        "- Smart Grid：保持SIMULATION_ONLY，模拟信号不进入实盘资产、现金或建议。",
        "",
        "## 3. 资产配置偏离",
        "",
        *_allocation_table(decision),
        "",
        f"- 精确再平衡资产基数：{_yuan(snapshot.get('decision_total_assets', decision.get('portfolio_value_yuan')))}；待估值成本{_yuan(snapshot.get('provisional_value_cny'))}已排除。",
        "",
        "## 4. 下一触发条件",
        "",
        _items((decision.get("next_triggers", []) or [])[:5]),
        "",
        "## 5. 异常与待补数据",
        "",
        _items(anomalies),
        "",
        "# 附录",
        "",
        "## 附录 A. 市场吸引力与组合修复优先级",
        "",
        "- Market Attractiveness Score只评价标的自身；Portfolio Repair Priority单独评价组合偏离与长期资金方向。",
        f"- cross_asset_comparability={comparability.get('cross_asset_comparability', 'NOT_EVALUATED')}；不可比时不生成跨资产统一第一名。",
        "- 当前组合修复结论：美股宽基是长期第一优先方向；A股标的仅在A股市场内部排名。",
        "",
        *_portfolio_repair_table(decision),
        "",
        *_opportunity_table(decision),
        "",
        "## 附录 B. 正式持仓与白名单",
        "",
        f"- 白名单状态：{snapshot.get('holding_validation_status')}；未经确认持仓数量：{len(snapshot.get('unconfirmed_holdings', []) or [])}",
        "- *ST闻泰来源：data/portfolio_master.yaml（user_confirmed_category_reconciled）；仅允许人工风险复核，永久禁止自动新增。",
        "",
        *_holding_table(decision),
        "",
        "## 附录 C. 市场、宏观与研究数据",
        "",
        *_market_table(decision),
        "",
        "### A股/港股行情字段覆盖率",
        "",
        *_cn_hk_completeness_table(decision),
        "",
        "### A股研究数据完整度 / 港股研究数据完整度",
        "",
        *_cn_hk_p1a_table(decision),
        "",
        "### 市场宽度、资金流与情绪数据状态",
        "",
        *_market_context_status(decision),
        "",
        "## 附录 D. DQS、风险门槛与可比较性",
        "",
        f"- core_decision_comparability={comparability.get('core_decision_comparability')}",
        f"- cross_asset_comparability={comparability.get('cross_asset_comparability')}",
        f"- grid_snapshot_comparability={comparability.get('grid_snapshot_comparability')}",
        f"- non_comparable_items_count={comparability.get('non_comparable_items_count', 0)}",
        "",
        *_risk_table(decision),
        "",
        *risk_gate_lines,
        "",
        *_dqs_table(decision),
        "",
        "## 附录 E. 事件、压力测试与模拟网格",
        "",
        *_events(decision),
        "",
        "### 组合情景压力测试",
        "",
        *_stress_scenarios(decision),
        "",
        grid_section,
        "",
        "## 附录 F. 系统状态、来源与一致性",
        "",
        f"- OpenAI状态：{ai.get('openai_status', 'rules_only')}；分析来源：{ai.get('provider')}；仅作解释，不覆盖规则。",
        "",
        *_source_lines(decision),
        "",
        f"- 一致性结果：{consistency.get('status')}；错误{len(consistency.get('errors', []) or [])}项；警告{len(consistency.get('warnings', []) or [])}项。",
        *_consistency_table(decision),
        "",
        decision.get("disclaimer", "仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。"),
    ]
    return "\n".join(lines)


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
    return _generate_final_freeze_daily_report(decision)

    dqs = decision.get("dqs", {})
    risk = decision.get("risk", {})
    budget = decision.get("budget", {})
    migration = decision.get("migration_plan", {})
    consistency = decision.get("consistency", {})
    ai = decision.get("ai", {})
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    reconciliation = decision.get("trade_reconciliation", {}) or {}
    comparability = decision.get("comparability", {}) or {}
    grid_section = generate_grid_daily_section(decision.get("grid", {})).replace("## ", "### ")
    grid_section = grid_section.replace("# Stone Smart Grid", "## 15. Stone Smart Grid", 1)
    lines = [
        f"# Stone AI Investment Manager Pro V12.7.1 Final Freeze 日报（{decision.get('report_run_mode_label', '正常运行')}）",
        "",
        "## 0. 报告状态",
        "",
        f"- 报告业务日期（report_business_date）：{decision.get('report_business_date', decision.get('date'))}",
        f"- 运行模式（report_run_mode）：{decision.get('report_run_mode', 'SCHEDULED')}（{decision.get('report_run_mode_label', '自动定时运行')}）",
        f"- 报告时区：{decision.get('report_timezone', 'Asia/Shanghai')}",
        f"- 报告生成时间（report_generated_at）：{decision.get('report_generated_at', decision.get('generated_at'))}",
        f"- 决策数据截止时间（decision_cutoff_at）：{decision.get('decision_cutoff_at', decision.get('data_cutoff'))}",
        f"- 实盘交易发生日期（actual_trade_date）：{_text(decision.get('actual_trade_date'), '无')}",
        f"- 数据阶段：{'、'.join((decision.get('data_time_summary', {}) or {}).get('data_stages', []) or ['UNKNOWN'])}",
        f"- 截止时间后获取的数据：{len(decision.get('post_cutoff_data', []) or [])}项（仅附录，不参与DQS、风险、评分、网格或今日建议）",
        f"- 是否存在不同步数据：{_yes_no((decision.get('data_time_summary', {}) or {}).get('has_unsynchronized_data'))}",
        f"- 最旧关键数据时间：{_text((decision.get('data_time_summary', {}) or {}).get('oldest_critical_data_at'))}",
        f"- 最新关键数据时间：{_text((decision.get('data_time_summary', {}) or {}).get('newest_critical_data_at'))}",
        f"- 当前交易日状态：{decision.get('trading_day_status')}",
        f"- 分析模式：{ai.get('mode')}（{ai.get('provider')}）",
        f"- DQS：{dqs.get('score')} / {dqs.get('mode_label')}",
        f"- dqs_gate_passed：{_yes_no((decision.get('trade_permission_gates', {}) or {}).get('dqs_gate_passed'))}",
        f"- schedule_gate_passed：{_yes_no((decision.get('trade_permission_gates', {}) or {}).get('schedule_gate_passed'))}",
        f"- cash_gate_passed：{_yes_no((decision.get('trade_permission_gates', {}) or {}).get('cash_gate_passed'))}",
        f"- risk_gate_passed：{_yes_no((decision.get('trade_permission_gates', {}) or {}).get('risk_gate_passed'))}",
        f"- final_trade_permission：{_yes_no((decision.get('trade_permission_gates', {}) or {}).get('final_trade_permission'))}",
        f"- denial_reason：{(decision.get('trade_permission_gates', {}) or {}).get('denial_reason', '无')}",
        f"- 数据完整性结论：{dqs.get('conclusion')}",
        f"- 持仓快照：{snapshot.get('snapshot_date')}；来源：{snapshot.get('source')}；{snapshot.get('freshness_warning')}",
        "",
        "## 1. Stone CIO 今日决策卡",
        "",
        "### A. 实盘交易事实（独立于报告业务日期）",
        "",
        f"- USER_CONFIRMED_ACTUAL_TRADE：{_yes_no(decision.get('actual_trade_recorded'))}",
        f"- 交易发生日期：{_text(decision.get('actual_trade_date'), '无')}；报告业务日期：{decision.get('report_business_date', decision.get('date'))}",
        f"- 交易：{decision.get('trade_type')}；实际金额：{_yuan(decision.get('actual_trade_amount_yuan'))}；标的：{_text(decision.get('actual_trade_symbol'), '无')}",
        f"- trade_origin：{decision.get('trade_origin', 'UNKNOWN')}",
        f"- execution_status：{_text(decision.get('execution_status'))}",
        f"- 是否由系统事前批准：{'是，属于此前既定周三基础定投计划' if decision.get('system_pre_authorized') else '否'}",
        f"- 是否机会加仓：{_yes_no(decision.get('opportunity_add'))}；是否自主临时交易：{_yes_no(decision.get('discretionary_trade'))}；是否事件追涨：{_yes_no(decision.get('event_chasing'))}",
        f"- 交易目的：{'基础定投' if decision.get('trade_origin') == 'SCHEDULED_BASE_DCA' else '待确认'}；资金来源：{decision.get('funding_source')}；资产迁移属性：{_text(decision.get('asset_migration_attribute'))}",
        "- 2026-07-15的9,000元VOO买入是独立历史交易事实；手动补运行不会把它改写成报告业务日期的新交易。",
        f"- 交易对账状态：{reconciliation.get('status', 'NOT_APPLICABLE')}",
        f"- 待补字段：{'、'.join(reconciliation.get('missing_fields', []) or ['无'])}",
        f"- 自动重算：{_yes_no(reconciliation.get('auto_recalculated'))}；交易对账质量：{reconciliation.get('transaction_reconciliation_quality', '不适用')}",
        f"- VOO总股数：{_text(reconciliation.get('voo_total_quantity'))}；VOO最新市值：{_yuan(reconciliation.get('voo_latest_market_value_cny')) if reconciliation.get('voo_latest_market_value_cny') is not None else '待字段和最新行情齐备后重算'}",
        f"- 美股总市值：{_yuan(reconciliation.get('us_stock_total_market_value_cny')) if reconciliation.get('us_stock_total_market_value_cny') is not None else '待字段和最新行情齐备后重算'}",
        "",
        "### B. Stone CIO当前建议",
        "",
        f"- 当前是否建议继续操作：{_yes_no(decision.get('today_trade'))}",
        f"- 建议金额：{_amount_mode_text(decision, budget.get('today_total_yuan', 0))}",
        f"- 建议标的：{(decision.get('decision_card', {}).get('current_recommendation', {}) or {}).get('targets', '不适用')}",
        f"- 资金来源：{(decision.get('decision_card', {}).get('current_recommendation', {}) or {}).get('funding_source', '不使用资金')}",
        f"- 主要原因：{(decision.get('decision_card', {}).get('current_recommendation', {}) or {}).get('reason', '等待复核')}",
        f"- 下一日常复核：{decision.get('next_daily_review')}（下一个交易日或下一次日报运行）",
        f"- 下一计划定投复核：{decision.get('next_scheduled_dca_review')}",
        f"- 事件触发复核：{(decision.get('next_event_trigger_review', {}) or {}).get('description')}",
        "",
        "### C. 条件性计划",
        "",
        "- 条件性计划仅表示未来可能触发，不等于真实交易、不等于当前买入建议，也不会使“今日是否操作”变为“是”。",
        f"- 一句话结论：{decision.get('one_sentence')}",
        "",
        "### 今日交易事实与后续约束",
        "",
        _items((decision.get("no_trade_reasons") or [])[:3]),
        "",
        f"- 当前最大风险：{decision.get('max_risk')}",
        f"- 当前最大机会：{decision.get('max_opportunity')}",
        f"- 下一日常复核：{decision.get('next_daily_review')}",
        f"- 下一计划定投复核：{decision.get('next_scheduled_dca_review')}",
        f"- 事件触发复核：{(decision.get('next_event_trigger_review', {}) or {}).get('description')}",
        f"- 下一次复核依据：{decision.get('next_review_reason')}",
        "",
        "## 2. Stone CIO Commentary",
        "",
        f"- 当前市场状态：{ai.get('market_regime', '由规则引擎依据DQS和风险评分判断')}",
        f"- 今天为什么操作或不操作：{ai.get('best_action_today')}",
        f"- 未来3—7天最重要风险：{ai.get('most_important_risk')}",
        f"- 当前组合优先事项：{ai.get('summary')}",
        f"- 当前最大机会：{ai.get('best_opportunity', decision.get('max_opportunity'))}",
        f"- 下一次允许操作条件：{'；'.join(ai.get('required_trigger_conditions', [])) or '以第5节触发条件为准'}",
        f"- 一句话结论：{ai.get('one_sentence')}",
        "",
        "## 3. 今日资金计划",
        "",
        *_money_plan_table(decision),
        "",
        "## 4. 现金与预算口径",
        "",
        *_cash_table(decision),
        "",
        "### 本次交易前后变化",
        "",
        *_transaction_change_table(decision),
        "",
        f"- 本月债券到期到账金额：{_yuan(budget.get('actual_bond_cash_arrived_yuan'))}",
        f"- 本月债券转权益月度额度：{_yuan(budget.get('conditional_bond_to_equity_month_yuan'))}",
        f"- 本月批准债券转权益额度：{_yuan(budget.get('approved_bond_to_equity_month_yuan'))}",
        f"- 本月已执行金额：{_yuan(budget.get('bond_to_equity_executed_this_month_yuan'))}",
        f"- 本月剩余可投资额度：{_yuan(budget.get('bond_to_equity_remaining_this_month_yuan'))}",
        f"- 本月剩余真实可投资现金：{_yuan(budget.get('bond_to_equity_remaining_real_cash_yuan'))}",
        f"- 说明：{budget.get('funding_note')} 剩余资金已到账、可投资，但仍须服从后续市场与风险条件，不代表必须一次性投入。",
        "",
        "## 5. 下一触发条件",
        "",
        _items(decision.get("next_triggers")),
        "",
        "## 6. 资产配置与偏离",
        "",
        *_allocation_table(decision),
        "",
        "- 资产配置现金目标严格按总资产×8%计算；固定安全底线220,000元是独立风控口径，只参与真实可投资现金计算。",
        "- 美股权益当前金额339,000元；TLT 55,000元已从美股权益移入债券配置。VOO新增9,000元仍是待估值成本，VOO最新市值未机械更新为139,000元。",
        "",
        "## 7. 未来12个月债券迁移第一阶段路线图",
        "",
        f"- 当前债券金额：{_yuan(migration.get('current_bond_yuan'))}",
        f"- 目标债券金额：{_yuan(migration.get('target_bond_yuan'))}",
        f"- 理论需转出金额：{_yuan(migration.get('theoretical_transfer_yuan'))}",
        f"- 每月建议转出上限：{_yuan(migration.get('monthly_cap_yuan'))}",
        f"- 理论完整迁移周期：{migration.get('theoretical_full_months')}个月",
        f"- 12个月预计转出：{_yuan(migration.get('twelve_month_transfer_yuan'))}",
        f"- 12个月后预计剩余超配：{_yuan(migration.get('remaining_after_12_months_yuan'))}",
        f"- 完成周期说明：{migration.get('estimated_completion')}",
        f"- 路线图纪律：{migration.get('conditional_cap_note')}",
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
        "## 8. Opportunity Score",
        "",
        "- 权重：估值20%、趋势与市场宽度15%、基本面20%、宏观10%、资金流与成交结构10%、组合适配20%、数据置信度5%。",
        "- 最终分 = 原始绝对分 + 温和横截面调整 + 数据质量调整 + 组合约束调整；评分不覆盖现金、预算、DQS和硬风控。",
        "- 组合约束已使用更新后的美股、债券和现金配置；VOO个券持仓仍保留原确认市值130,000元，新增9,000元成本不冒充实时市值。",
        "",
        *_opportunity_table(decision),
        "",
        "## 9. 持仓健康检查",
        "",
        *_holding_table(decision),
        "",
        "## 10. 市场与宏观",
        "",
        *_market_table(decision),
        "",
        "### A股与港股专项数据完整度",
        "",
        *_cn_hk_completeness_table(decision),
        "",
        "### A股与港股P1A权威基础数据",
        "",
        *_cn_hk_p1a_table(decision),
        "",
        "### 市场宽度、资金流与情绪数据状态",
        "",
        *_market_context_status(decision),
        "",
        "## 11. 市场风险评分",
        "",
        f"- 总分：{risk.get('score')} / 100",
        f"- 风险等级：{risk.get('level')}",
        "",
        *_risk_table(decision),
        "",
        "## 12. DQS数据质量",
        "",
        f"- 原始分：{dqs.get('raw_score')}",
        f"- 最终分：{dqs.get('score')}",
        "- DQS处理：本次用户确认交易只更新事实台账，没有手工提高DQS；评分仍按本次最新数据源重新计算。",
        f"- 降级原因：{'；'.join(dqs.get('blocking_errors', [])) or '无硬性降级'}",
        f"- 建议精度影响：{dqs.get('mode_label')}",
        f"- 核心必需数据缺失：{dqs.get('required_core_missing_count', 0)}项",
        f"- 核心必需缺失项目：{', '.join((dqs.get('required_core_data', {}) or {}).get('missing_items', [])) or '无'}",
        f"- 增强型数据缺失：{dqs.get('enhancement_missing_count', 0)}项",
        f"- 增强型缺失项目：{'、'.join(dqs.get('enhancement_missing_items', [])) or '无'}",
        f"- 可选解释数据缺失：{dqs.get('optional_explanation_missing_count', 0)}项",
        f"- 数据冲突：{len(dqs.get('conflicts', []))}项",
        f"- 过期数据：{len(dqs.get('stale_metrics', []))}项",
        f"- core_decision_comparability：{comparability.get('core_decision_comparability', 'NOT_EVALUATED')}",
        f"- cross_asset_comparability：{comparability.get('cross_asset_comparability', 'NOT_EVALUATED')}",
        f"- grid_snapshot_comparability：{comparability.get('grid_snapshot_comparability', 'NOT_EVALUATED')}",
        f"- non_comparable_items_count：{comparability.get('non_comparable_items_count', 0)}",
        f"- 不可比较项目明细：{'、'.join(comparability.get('non_comparable_items', []) or []) or '无'}",
        f"- 异常0值：{', '.join(dqs.get('suspicious_zero', [])) or '无'}",
        "",
        *_dqs_table(decision),
        "",
        "## 13. 未来7天事件",
        "",
        f"- 未来48小时高等级事件结论：{'存在' if decision.get('macro_event_high_next_48_hours') else '无'}",
        f"- 未来7天高等级事件结论：{'存在' if decision.get('macro_event_high_next_7_days') else '无'}",
        *_events(decision),
        "",
        "### 已公布宏观事件",
        "",
        *_released_events(decision),
        "",
        "## 14. 三种市场情景",
        "",
        *_scenarios(decision),
        "### 组合情景压力测试",
        "",
        *_stress_scenarios(decision),
        grid_section,
        "",
        "## 16. OpenAI状态与回退说明",
        "",
        f"- OpenAI状态：{ai.get('openai_status', 'rules_only')}",
        f"- 分析模式：{ai.get('mode')}",
        f"- 分析来源：{ai.get('provider')}",
        f"- OpenAI可选复核：{'已参与' if ai.get('openai_participated') else '本次未参与，规则引擎已完成完整分析'}",
        f"- 是否启用：{'是' if ai.get('enabled') else '否'}",
        f"- 是否实际调用：{'是' if ai.get('called') else '否'}",
        f"- 是否发生调用失败：{'是' if ai.get('call_failed') else '否'}",
        f"- 模型：{ai.get('model') or '不适用'}",
        f"- 是否发生回退：{'是' if ai.get('fallback_occurred') else '否'}",
        f"- 回退原因：{ai.get('fallback_reason') or '无'}",
        f"- 失败类别：{ai.get('error_category') or '无'}",
        f"- 重试次数：{ai.get('retry_count', 0)}",
        f"- 是否与规则结论冲突：{'是，AI复核存在分歧，规则风控优先' if ai.get('conflict_with_rules') else '否'}",
        f"- AI复核摘要：{ai.get('review_summary') or ai.get('summary') or '规则引擎独立完成复核'}",
        f"- 验证拒绝原因：{'；'.join(ai.get('validation_errors', [])) or '无'}",
        f"- 可靠性说明：{ai.get('impact')}",
        f"- 说明：{ai.get('description', '规则引擎独立完成分析。')}",
        f"- 规则触发：DQS={dqs.get('score')}，风险={risk.get('score')}，现金可用={_yuan(budget.get('confirmed_cash_available_yuan'))}",
        "",
        "## 17. 数据来源",
        "",
        *_source_lines(decision),
        "",
        "## 18. 一致性验证",
        "",
        f"- 验证结果：{consistency.get('status', 'PASS' if consistency.get('ok') else 'FAIL')}",
        f"- 错误：{'; '.join(consistency.get('errors', [])) or '无'}",
        f"- 警告：{'; '.join(consistency.get('warnings', [])) or '无'}",
        "",
        *_consistency_table(decision),
        "",
        "## 19. 免责声明",
        "",
        decision.get("disclaimer", "仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。"),
    ]
    return "\n".join(lines)


def generate_portfolio_snapshot_report(decision: dict[str, Any]) -> str:
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    budget = decision.get("budget", {}) or {}
    holdings = decision.get("holding_diagnostics", []) or []
    holding_rows = [
        "| 持仓 | 类别 | 当前对账金额 | 数量 | 估值说明 |",
        "| -- | -- | --: | --: | -- |",
    ]
    raw_holdings = {row.get("security_name"): row for row in snapshot.get("holdings", []) or []}
    for item in holdings:
        raw = raw_holdings.get(item.get("name"), {}) or {}
        status = raw.get("valuation_status") or raw.get("valuation_method") or "user_confirmed"
        quantity = raw.get("quantity")
        holding_rows.append(
            f"| {item.get('name')} | {item.get('category')} | {_yuan(item.get('amount_yuan'))} | "
            f"{quantity if quantity is not None else '待补充/不适用'} | {status} |"
        )
    return "\n".join(
        [
            "# Stone AI 最新持仓快照",
            "",
            f"- 持仓确认日期：{snapshot.get('snapshot_date')}",
            f"- 来源：{snapshot.get('source')}",
            f"- 组合总资产对账值：{_yuan(snapshot.get('total_assets'))}",
            f"- 账户总现金：{_yuan(budget.get('account_total_cash_yuan'))}",
            f"- 固定现金安全储备：{_yuan(budget.get('cash_safety_reserve_yuan'))}",
            f"- 当前真实可投资现金：{_yuan(budget.get('investable_cash_yuan'))}",
            "- 估值限制：VOO新增交易的股数、实际汇率和手续费待补充，9,000元仅作为新增投入成本暂记，不是实时市值。",
            "",
            "## 资产配置",
            "",
            *_allocation_table(decision),
            "",
            "## 持仓明细",
            "",
            *holding_rows,
            "",
            "## 2026-07-15交易前后变化",
            "",
            *_transaction_change_table(decision),
            "",
            "## 本月债券转权益状态",
            "",
            f"- 到期到账：{_yuan(budget.get('actual_bond_cash_arrived_yuan'))}",
            f"- 批准额度：{_yuan(budget.get('approved_bond_to_equity_month_yuan'))}",
            f"- 已执行：{_yuan(budget.get('bond_to_equity_executed_this_month_yuan'))}（VOO，真实实盘）",
            f"- 剩余额度及真实可投资现金：{_yuan(budget.get('bond_to_equity_remaining_real_cash_yuan'))}",
            "- 剩余资金已到账，但不代表必须立即或一次性投入；Smart Grid模拟资金继续与实盘严格隔离。",
        ]
    )


def generate_weekly_report(decision: dict[str, Any]) -> str:
    budget = decision.get("budget", {})
    try:
        report_date = date.fromisoformat(str(decision.get("date")))
    except (TypeError, ValueError):
        report_date = date.today()
    iso_year, iso_week, _ = report_date.isocalendar()
    week_start = report_date - timedelta(days=report_date.weekday())
    week_end = week_start + timedelta(days=6)
    return "\n".join(
        [
            "# Stone AI V12.7.1 Final Freeze 周报",
            "",
            f"- 报告所属周：{iso_year}-W{iso_week:02d}",
            f"- 周期：{week_start.isoformat()} 至 {week_end.isoformat()}",
            f"- 本周确认买入额度：{_yuan(budget.get('week_confirmed_yuan'))}",
            f"- 本月债券到期到账：{_yuan(budget.get('actual_bond_cash_arrived_yuan'))}",
            f"- 本月债券转权益已执行：{_yuan(budget.get('bond_to_equity_executed_this_month_yuan'))}",
            f"- 本月债券转权益剩余额度：{_yuan(budget.get('bond_to_equity_remaining_this_month_yuan'))}",
            f"- 下一复核日：{decision.get('next_review_date')}",
            f"- 当前风险：{decision.get('risk', {}).get('level')}，DQS={decision.get('dqs', {}).get('score')}",
            "- 本周纪律：剩余专项资金已到账但不代表必须立即投入；重大事件前不追涨；模拟网格与实盘隔离。",
            "",
            decision.get("disclaimer", ""),
        ]
    )


def generate_monthly_report(decision: dict[str, Any]) -> str:
    migration = decision.get("migration_plan", {})
    budget = decision.get("budget", {})
    return "\n".join(
        [
            "# Stone AI V12.7.1 Final Freeze 月报",
            "",
            f"- 本月确认买入额度：{_yuan(budget.get('month_confirmed_yuan'))}",
            f"- 本月债券到期到账：{_yuan(budget.get('actual_bond_cash_arrived_yuan'))}",
            f"- 本月批准债券转权益额度：{_yuan(budget.get('approved_bond_to_equity_month_yuan'))}",
            f"- 本月已执行：{_yuan(budget.get('bond_to_equity_executed_this_month_yuan'))}（VOO）",
            f"- 本月剩余额度及真实可投资现金：{_yuan(budget.get('bond_to_equity_remaining_real_cash_yuan'))}",
            f"- 理论需转出债券金额：{_yuan(migration.get('theoretical_transfer_yuan'))}",
            f"- 月度上限：{_yuan(migration.get('monthly_cap_yuan'))}",
            "- 月度原则：资金已到账；剩余部分继续按市场与风险条件分批复核，不强制一次性投入。",
            "",
            decision.get("disclaimer", ""),
        ]
    )
