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
    today_amount = float(budget.get("today_total_yuan", 0) or 0)
    account_cash = float(budget.get("account_total_cash_yuan", 0) or 0)
    funding_source = str(decision.get("funding_source") or "不适用")
    cash_funded_amount = today_amount if decision.get("today_trade") and "现金" in funding_source else 0
    post_execution_cash = max(0, account_cash - cash_funded_amount)

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
        "execute": bool(decision.get("today_trade")),
        "action_type": decision.get("trade_type") or "无操作",
        "targets": decision.get("targets") or "不适用",
        "amount_yuan": today_amount,
        "amount_or_range": _amount_mode_text(decision, today_amount),
        "funding_source": funding_source,
        "post_execution_cash_yuan": post_execution_cash,
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
    no_execute = "；".join(action["no_execute_reasons"]) if not action["execute"] else "不适用"
    anomalies = "；".join(action["data_anomalies_or_baseline_conflicts"]) or "无"
    return "\n".join(
        [
            "# Stone CIO 今日执行单",
            "",
            f"- 报告日期：{action['report_date']}",
            f"- 数据截止时间：{action['data_cutoff_time']}",
            f"- 今日是否执行：{_yes_no(action['execute'])}",
            f"- 操作类型：{action['action_type']}",
            f"- 标的：{action['targets']}",
            f"- 金额或金额区间：{action['amount_or_range']}",
            f"- 资金来源：真实可执行资金口径；{action['funding_source']}",
            f"- 执行后账户现金余额：{_yuan(action['post_execution_cash_yuan'])}",
            f"- 现金安全线：{_yuan(action['cash_safety_reserve_yuan'])}",
            f"- DQS：{action['dqs']}（{action['dqs_mode']}）",
            f"- Risk Score：{action['risk_score']}（{action['risk_level']}）",
            f"- Opportunity Score：{action['opportunity_score']}",
            f"- 下一复核日期：{action['next_review_date']}",
            f"- 不执行的核心原因：{no_execute}",
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
            "conditional_plan_bond_to_equity": budget.get("conditional_bond_to_equity_month_yuan"),
            "unsettled_bond_cash": budget.get("actual_bond_cash_arrived_yuan"),
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
        "| 标的 | 最终分 | 原始分 | 横截面 | 数据调整 | 组合约束 | 行情完整度 | 分析完整度 | 评分可信度 | 财务模型 | P1A输入 | 建议 | 当前持仓 | 主要原因 |",
        "| -- | ---: | ---: | ---: | ---: | ---: | --: | --: | -- | -- | -- | -- | ---: | -- |",
    ]
    for item in decision.get("opportunity", []) or []:
        completeness_value = item.get("market_data_completeness")
        completeness_display = "不适用" if completeness_value is None else f"{float(completeness_value):.1f}%"
        analysis_value = item.get("analysis_data_completeness")
        analysis_display = "不适用" if analysis_value is None else f"{float(analysis_value):.1f}%"
        rows.append(
            "| "
            f"{item['name']} | {item['score']} | {item.get('raw_score', item['score'])} | "
            f"{item.get('cross_section_adjustment', 0):+d} | {item.get('data_quality_adjustment', 0):+d} | "
            f"{item.get('portfolio_constraint_adjustment', 0):+d} | {completeness_display} | {analysis_display} | "
            f"{item.get('scoring_confidence', '可用')} | {item.get('financial_model', '不适用')} | "
            f"{'、'.join(item.get('p1a_inputs_used', []) or ['无'])} | {item['advice']} | "
            f"{_yuan(item['current_holding_yuan'])} | {item.get('reason', '暂无')} |"
        )
    return rows


def _cn_hk_completeness_table(decision: dict[str, Any]) -> list[str]:
    completeness = decision.get("market_completeness", {}) or {}
    rows = [
        "| 市场 | 数据完整度 | 评分可信度 | 是否限制决策 | 缺失字段 |",
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
        "previous_close": "上一交易日收盘",
        "official_lagged_macro": "最新官方滞后数据",
        "stale": "过期数据",
        "unavailable": "不可用",
    }
    pair_comparable = bool((decision.get("risk", {}).get("market_time_consistency", {}) or {}).get("comparable"))
    rows = [
        "| 指标 | 当前值 | 前值 | 涨跌幅 | 观察时间 | 数据口径 | 数据年龄 | 是否可横向比较 | 来源 | 来源等级 | 状态 |",
        "| -- | --: | --: | --: | -- | -- | -- | -- | -- | --: | -- |",
    ]
    for item in decision.get("market_table", []) or []:
        change = "暂无数据" if item.get("change_pct") is None else f"{float(item['change_pct']):.2f}%"
        previous = "暂无数据" if item.get("previous") is None else f"{float(item['previous']):.2f}"
        status = "成功" if item.get("success") else _text(item.get("error"), "请求失败")
        age = "暂无" if item.get("age_hours") is None else f"{float(item['age_hours']):.1f}小时"
        comparable = "是" if item.get("name") in {"VOO", "QQQ"} and pair_comparable else "否"
        rows.append(
            "| "
            f"{item['name']} | {item['display_value']} | {previous} | {change} | "
            f"{item['timestamp']} | {session_labels.get(item.get('data_session'), item.get('data_session', '未知'))} | "
            f"{age} | {comparable} | {item['source']} | {item['source_tier']} | {status} |"
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
            f"- {event.get('event_name', event.get('name', '暂无名称'))}（参考期{event.get('reference_period', '未知')}）："
            f"{event.get('release_at_report_timezone') or '发布时间未验证'}（报告时区），"
            f"源时区发布时间：{event.get('release_at') or '未验证'} {event.get('source_timezone', '未知')}，"
            f"风险等级：{event.get('risk_level', event.get('level', '暂无'))}，"
            f"核验状态：{event.get('verification_status', 'unverified')}，来源等级：{event.get('source_level', '未知')}，"
            f"来源：{event.get('source', '暂无可靠来源')}；"
            "纪律：事件前不追涨，定投可复核但不一次性重仓。"
        )
    return lines


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
        "| 情景 | 组合估算变化 | 估算金额 | 主要贡献项 | 是否超过25%—35%回撤容忍区间 |",
        "| -- | --: | --: | -- | -- |",
    ]
    for item in decision.get("stress_scenarios", []) or []:
        contributors = "；".join(
            f"{row['category']} {row['impact_yuan']:+,.0f}元" for row in item.get("largest_contributors", [])
        ) or "暂无"
        tolerance = "超过" if item.get("exceeds_tolerance_low") else "未超过"
        rows.append(
            f"| {item.get('name')} | {float(item.get('portfolio_return', 0)):.2%} | "
            f"{float(item.get('portfolio_change_yuan', 0)):+,.0f}元 | {contributors} | {tolerance} |"
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
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    grid_section = generate_grid_daily_section(decision.get("grid", {})).replace("## ", "### ")
    grid_section = grid_section.replace("# Stone Smart Grid", "## 15. Stone Smart Grid", 1)
    lines = [
        "# Stone AI Investment Manager Pro V12.6.1 Stable 日报",
        "",
        "## 0. 报告状态",
        "",
        f"- 报告日期：{decision.get('date')}",
        f"- 报告时区：{decision.get('report_timezone', 'Asia/Shanghai')}",
        f"- 报告生成时间（含时区）：{decision.get('generated_at')}",
        f"- 决策数据统一截止时间：{(decision.get('data_time_summary', {}) or {}).get('decision_data_cutoff', decision.get('data_cutoff'))}",
        f"- 是否存在不同步数据：{_yes_no((decision.get('data_time_summary', {}) or {}).get('has_unsynchronized_data'))}",
        f"- 最旧关键数据时间：{_text((decision.get('data_time_summary', {}) or {}).get('oldest_critical_data_at'))}",
        f"- 最新关键数据时间：{_text((decision.get('data_time_summary', {}) or {}).get('newest_critical_data_at'))}",
        f"- 当前交易日状态：{decision.get('trading_day_status')}",
        f"- 分析模式：{ai.get('mode')}（{ai.get('provider')}）",
        f"- DQS：{dqs.get('score')} / {dqs.get('mode_label')}",
        f"- 数据完整性结论：{dqs.get('conclusion')}",
        f"- 持仓快照：{snapshot.get('snapshot_date')}；来源：{snapshot.get('source')}；{snapshot.get('freshness_warning')}",
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
        f"- 条件性债券转权益月度额度：{_yuan(budget.get('conditional_bond_to_equity_month_yuan'))}",
        f"- 本月批准债券转权益额度：{_yuan(budget.get('approved_bond_to_equity_month_yuan'))}",
        f"- 说明：{budget.get('funding_note')} 条件性月度额度仅在债券资金实际到账、现金安全线满足、DQS和事件风控通过后生效。",
        "",
        "## 5. 下一触发条件",
        "",
        _items(decision.get("next_triggers")),
        "",
        "## 6. 资产配置与偏离",
        "",
        *_allocation_table(decision),
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
        f"- 降级原因：{'；'.join(dqs.get('blocking_errors', [])) or '无硬性降级'}",
        f"- 建议精度影响：{dqs.get('mode_label')}",
        f"- 核心必需数据缺失：{dqs.get('required_core_missing_count', 0)}项",
        f"- 核心必需缺失项目：{', '.join((dqs.get('required_core_data', {}) or {}).get('missing_items', [])) or '无'}",
        f"- 增强型数据缺失：{dqs.get('enhancement_missing_count', 0)}项",
        f"- 增强型缺失项目：{'、'.join(dqs.get('enhancement_missing_items', [])) or '无'}",
        f"- 可选解释数据缺失：{dqs.get('optional_explanation_missing_count', 0)}项",
        f"- 数据冲突：{len(dqs.get('conflicts', []))}项",
        f"- 过期数据：{len(dqs.get('stale_metrics', []))}项",
        f"- 不可横向比较数据：{len(dqs.get('non_comparable_metrics', []))}项（{'、'.join(dqs.get('non_comparable_metrics', [])) or '无'}）",
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
            "# Stone AI V12.6.1 Stable 周报",
            "",
            f"- 报告所属周：{iso_year}-W{iso_week:02d}",
            f"- 周期：{week_start.isoformat()} 至 {week_end.isoformat()}",
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
            "# Stone AI V12.6.1 Stable 月报",
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
