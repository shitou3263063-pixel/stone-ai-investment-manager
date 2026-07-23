from __future__ import annotations

import json
from typing import Any


def _require_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("bundle_type") != "FinalDecisionBundle":
        raise TypeError("Report rendering requires FinalDecisionBundle")


def _join(values: list[Any] | None, empty: str = "无") -> str:
    return "；".join(str(value) for value in values or [] if str(value)) or empty


def _event_coverage_message(bundle: dict[str, Any]) -> str:
    assessment = bundle.get("event_assessment", {}) or {}
    status = str(assessment.get("status") or "DATA_INSUFFICIENT")
    if status == "DATA_INSUFFICIENT":
        return "已获取数据中未发现高等级事件，但事件覆盖不足，不能确认未来7天不存在高等级事件。"
    if status == "VALID_NO_HIGH_IMPACT_EVENT":
        return "事件覆盖有效，未来7天未发现高等级事件。"
    if status == "VALID_EVENTS_FOUND":
        return "事件覆盖有效，已发现未来7天高等级事件，详见事件清单。"
    return f"事件覆盖状态为{status}，需按对应场景继续复核。"


def _next_action(permission: str) -> str:
    return {
        "ALLOW_EXECUTION": "进入人工确认，不自动下单",
        "ALLOW_REDUCED_EXECUTION": "按保守减额档位人工复核",
        "ALLOW_EVALUATION_ONLY": "输出偏离与修复优先级，不生成成交指令",
        "ALLOW_SIMULATION_ONLY": "仅记录模拟信号，保持实盘隔离",
        "ALLOW_RECONCILIATION": "执行成交、持仓与现金核对",
        "ACTIVE": "持续监控并更新风险明细",
        "PARTIAL_MONITORING": "继续监控可用指标并复核缺失项",
        "WARN": "补齐或复核所列数据",
        "PASS": "对账通过，无需事件数据复核",
        "FAIL": "核对成交、持仓与现金差异",
        "DENY": "满足硬阻断条件后重新评估",
    }.get(permission, "人工复核")


def _permission_rows(bundle: dict[str, Any]) -> list[str]:
    risk_score = int((bundle.get("risk_snapshot", {}) or {}).get("score", 0) or 0)
    rows = [
        "| 场景 | 使用DQS | 得分/门槛 | 风险门槛 | 数据状态 | 最终权限 | 硬阻断原因 | 软警告 | 下一步动作 |",
        "|---|---|---:|---:|---|---|---|---|---|",
    ]
    for item in bundle["scenario_decisions"]:
        risk_limit = f"{risk_score}/{item.get('risk_threshold', '-')}"
        data_status = (
            f"event={bundle['event_assessment'].get('status')}; "
            f"comparability={'PASS' if item.get('comparability_gate_passed') else 'BLOCK'}"
        )
        hard = _join(item.get("rejection_reasons"))
        live_hard = _join(item.get("live_rejection_reasons"), empty="")
        if live_hard:
            hard = f"{hard}；实盘限制：{live_hard}" if hard != "无" else f"实盘限制：{live_hard}"
        rows.append(
            f"| {item['scenario_name']} | {item['used_dqs_name']} | "
            f"{item['dqs_score']}/{item['required_dqs']} | {risk_limit} | {data_status} | "
            f"{item['final_permission']} | {hard} | {_join(item.get('warning_reasons'))} | "
            f"{_next_action(str(item['final_permission']))} |"
        )
    return rows


def _today_summary(bundle: dict[str, Any]) -> list[str]:
    contexts = bundle["scenario_decision_by_key"]
    scheduled = contexts["scheduled_dca"]
    opportunity = contexts["opportunity_add"]
    rebalance = contexts["strategic_rebalance"]
    operation = scheduled["final_permission"] in {"ALLOW_EXECUTION", "ALLOW_REDUCED_EXECUTION"}
    paused = any(
        token in str(reason)
        for reason in scheduled.get("rejection_reasons", []) or []
        for token in ("风险分数", "统一真实资产快照不可用", "低于硬门槛")
    )
    dca_text = {
        "ALLOW_EXECUTION": "是，进入正常定投人工确认",
        "ALLOW_REDUCED_EXECUTION": "是，但仅允许保守减额并人工复核",
    }.get(scheduled["final_permission"], "否")
    add_text = "是，进入人工确认" if opportunity["final_permission"] == "ALLOW_EXECUTION" else "否"
    rebalance_text = (
        "只进行资产偏离评估，不执行调仓"
        if rebalance["final_permission"] == "ALLOW_EVALUATION_ONLY"
        else "否"
    )
    reasons: list[str] = []
    for scenario in (scheduled, opportunity, rebalance):
        reasons.extend(str(reason) for reason in scenario.get("rejection_reasons", []) or [])
        reasons.extend(str(reason) for reason in scenario.get("warning_reasons", []) or [])
    reasons = list(dict.fromkeys(reasons))[:3]
    return [
        f"- 今日是否操作：{'进入人工确认' if operation else '不执行真实交易'}",
        f"- 是否定投：{dca_text}",
        f"- 是否主动加仓：{add_text}",
        f"- 是否调仓：{rebalance_text}",
        f"- 是否暂停：{'是' if paused else '否；继续监控与评估'}",
        "- 最主要的三条原因：",
        *[f"  {index}. {reason}" for index, reason in enumerate(reasons or ["当前无新增硬阻断"], start=1)],
    ]


def _allocation_rows(bundle: dict[str, Any]) -> list[str]:
    allocation = bundle.get("asset_allocation", []) or []
    priority_rows = (bundle.get("portfolio_snapshot", {}) or {}).get("portfolio_repair_priority", []) or []
    priority_by_category = {str(row.get("category")): row for row in priority_rows}
    rows = [
        "| 资产类别 | 当前金额 | 当前占比 | 目标占比 | 偏离百分点 | 偏离等级 | 修复优先级 | 修复方式 |",
        "|---|---:|---:|---:|---:|---|---:|---|",
    ]
    for item in allocation:
        category = str(item.get("category") or item.get("name") or "未知")
        repair = priority_by_category.get(category, {})
        direction = str(
            repair.get("repair_direction")
            or repair.get("portfolio_repair_direction")
            or "MAINTAIN"
        )
        deviation = float(item.get("deviation_ratio", 0) or 0)
        if category == "美股" and deviation < 0 and direction in {"ADD", "ADD_WITH_NEW_MONEY"}:
            method = "新增资金优先修复，优先宽基ETF，不强制一次性完成"
        elif category == "债券" and deviation > 0 and direction in {"REDUCE_OR_PAUSE", "REDUCE_OR_PAUSE_NEW_MONEY"}:
            method = "暂停新增，通过新增权益资金逐步稀释，不默认强制卖出"
        elif category == "黄金" and deviation > 0 and direction in {"REDUCE_OR_PAUSE", "REDUCE_OR_PAUSE_NEW_MONEY"}:
            method = "暂停新增，观察后续偏离"
        elif category in {"A股", "港股", "现金"} and direction == "MAINTAIN":
            method = "维持现有配置"
        else:
            method = {
                "ADD": "优先使用新增资金",
                "ADD_WITH_NEW_MONEY": "优先使用新增资金",
                "REDUCE_OR_PAUSE": "暂停新增；是否卖出须人工确认",
                "REDUCE_OR_PAUSE_NEW_MONEY": "暂停新增；是否卖出须人工确认",
                "MAINTAIN": "按当前偏离状态继续观察",
            }.get(direction, "按既定人工确认原则")
        rows.append(
            f"| {category} | {float(item.get('current_amount_yuan', 0) or 0):,.2f} | "
            f"{float(item.get('current_ratio', 0) or 0):.2%} | "
            f"{float(item.get('target_ratio', 0) or 0):.2%} | "
            f"{float(item.get('deviation_ratio', 0) or 0) * 100:+.2f} | "
            f"{item.get('status') or '未分级'} | "
            f"{repair.get('portfolio_repair_priority', '-')} | {method} |"
        )
    if not allocation:
        rows.append("| 无可展示配置 | - | - | - | - | - | - | - |")
    return rows


def _risk_rows(bundle: dict[str, Any]) -> list[str]:
    risk = bundle.get("risk_snapshot", {}) or {}
    market_risk = risk.get("market_risk", {}) or risk
    event_status = str((bundle.get("event_assessment", {}) or {}).get("status") or "")
    total_score = int(market_risk.get("score", risk.get("score", 0)) or 0)
    rows = [
        "| 风险因子 | 依据 | 风险得分 | 该项最高分 | 对总风险贡献 | 缺失处理 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in market_risk.get("components", []) or []:
        item_name = str(item.get("item") or "")
        basis = str(item.get("raw_value", item.get("basis", "-")))
        if event_status == "DATA_INSUFFICIENT" and ("事件" in item_name or "event" in item_name.lower()):
            basis = "已获取数据中未发现高等级事件，但事件覆盖不足，不能确认未来7天不存在高等级事件。"
        data_status = str(item.get("data_status") or "VALID").upper()
        missing_markers = (
            "缺失", "缺少", "不足", "不完整", "未连接",
            "missing", "insufficient", "not connected", "unavailable",
        )
        has_missing_data = (
            data_status.startswith("MISSING")
            or data_status in {
                "NOT_CONNECTED", "DATA_INSUFFICIENT", "SOURCE_FAILED",
                "SOURCE_ERROR", "NOT_AVAILABLE", "UNAVAILABLE",
            }
            or any(marker in basis.lower() for marker in missing_markers)
        )
        if has_missing_data:
            missing = "数据缺失，按中性风险计分并降低置信度。"
        elif data_status == "VALID_LAGGED_BY_DESIGN":
            missing = "按官方发布频率使用正常滞后数据。"
        else:
            missing = "数据有效，按现有值计分。"
        score = int(item.get("score", 0) or 0)
        max_score = int(item.get("weight", 0) or 0)
        rows.append(
            f"| {item_name} | {basis} | {score} | {max_score} | {score}点 | {missing} |"
        )
    max_total = int(
        market_risk.get("market_risk_weights_sum")
        or risk.get("market_risk_weights_sum")
        or sum(int(item.get("weight", 0) or 0) for item in market_risk.get("components", []) or [])
    )
    rows.append(
        f"| **合计** | 置信度：{market_risk.get('confidence', 'unknown')} | "
        f"**{total_score}** | **{max_total}** | **{total_score}点** | - |"
    )
    return rows


def _dqs_lines(bundle: dict[str, Any]) -> list[str]:
    lines: list[str] = [
        *[f"- {name}: **{result['total']}**" for name, result in bundle["dqs_results"].items()],
        "",
    ]
    for name, result in bundle["dqs_results"].items():
        breakdown = result.get("breakdown", []) or []
        audit_items = (
            [row for row in breakdown if row.get("item") == "released_macro_event_data_quality"]
            if name == "opportunity_dqs"
            else []
        )
        ordinary_items = [row for row in breakdown if row not in audit_items]
        lines.extend(
            [
                f"### {name}",
                "",
                "| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 | 数据源 | 最后成功时间 | 降分项 |",
                "|---|---:|---:|---|---|---|---|---:|",
            ]
        )
        for item in ordinary_items:
            score = int(item.get("score", 0) or 0)
            max_score = item.get("max", score)
            reason = item.get("reason") or item.get("basis") or "无扣分说明"
            missing = _join(item.get("missing_data") or item.get("missing_fields"), "无")
            lines.append(
                f"| {item.get('item')} | {score} | {max_score} | {reason} | {missing} | "
                f"{item.get('data_source') or '不适用'} | {item.get('last_success_at') or '不适用'} | "
                f"{item.get('score_impact', score - int(max_score or 0))} |"
            )
        ordinary_total = sum(int(row.get("score", 0) or 0) for row in ordinary_items)
        expression = " + ".join(str(int(row.get("score", 0) or 0)) for row in ordinary_items) or "0"
        if audit_items:
            lines.extend(
                [
                    "",
                    f"普通评分小计：{expression} = **{ordinary_total}**",
                    "",
                    "#### 审计扣分项",
                    "",
                    "| 审计项 | 扣分 | 满分 | 审计原因 |",
                    "|---|---:|---:|---|",
                ]
            )
            for item in audit_items:
                lines.append(
                    f"| {item.get('item')} | {int(item.get('score', 0) or 0)} | "
                    f"{item.get('max', 0)} | {item.get('reason') or item.get('basis') or '无'} |"
                )
            audit_total = sum(int(row.get("score", 0) or 0) for row in audit_items)
            lines.extend(
                [
                    "",
                    f"最终得分：普通评分小计 {ordinary_total} + 审计扣分 {audit_total} = **{result['total']}**",
                    "",
                ]
            )
        else:
            lines.extend(["", f"最终求和：{expression} = **{result['total']}**", ""])
    return lines


def _trade_reconciliation_lines(bundle: dict[str, Any]) -> list[str]:
    portfolio = bundle.get("portfolio_snapshot", {}) or {}
    report_data = bundle.get("report_data", {}) or {}
    trades = portfolio.get("confirmed_transactions", []) or []
    reconciliation = report_data.get("trade_reconciliation", {}) or {}
    reconciliation_by_id = {
        str(row.get("trade_id")): row for row in reconciliation.get("transactions", []) or []
    }
    execution_dqs = int((bundle.get("dqs_results", {}).get("execution_dqs", {}) or {}).get("total", 0) or 0)
    lines = [
        f"- execution_dqs：**{execution_dqs}**",
        f"- 成交对账总状态：**{reconciliation.get('status') or '待复核'}**",
        "",
        "| 交易日期 | 标的 | 交易前数量 | 成交数量 | 交易后数量 | 成交金额 | 费用 | 汇率 | 现金变化 | 对账状态 |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for trade in trades:
        trade_id = str(trade.get("id") or trade.get("trade_id") or "")
        audit = reconciliation_by_id.get(trade_id, {})
        quantity = float(trade.get("quantity", 0) or 0)
        post_quantity_value = audit.get("position_total_quantity")
        post_quantity = float(post_quantity_value) if post_quantity_value is not None else None
        side = str(trade.get("action") or trade.get("side") or "BUY").upper()
        if post_quantity is None:
            pre_quantity_text = post_quantity_text = "待补"
        else:
            pre_quantity = post_quantity - quantity if side == "BUY" else post_quantity + quantity
            pre_quantity_text = f"{pre_quantity:g}"
            post_quantity_text = f"{post_quantity:g}"
        trade_currency = str(trade.get("trade_currency") or trade.get("currency") or "")
        trade_amount = trade.get("trade_amount_usd")
        amount_text = f"{float(trade_amount):,.3f} USD" if trade_amount is not None else "待补"
        equivalent_cny = trade.get("invested_amount_cny")
        if equivalent_cny is not None:
            amount_text += f"（人民币等值记录{float(equivalent_cny):,.0f}元）"
        fee = trade.get("fee")
        fee_currency = str(trade.get("fee_currency") or trade_currency or "-")
        fee_text = f"{float(fee):g} {fee_currency}" if fee is not None else "待补"
        fx_status = str(trade.get("fx_status") or audit.get("fx_status") or "")
        actual_fx = trade.get("actual_fx_rate_cny_per_usd")
        if fx_status == "NOT_APPLICABLE_USD_CASH":
            fx_text = "不适用（美元账户现金）"
        elif actual_fx is not None:
            fx_text = f"{float(actual_fx):g} CNY/USD"
        else:
            fx_text = "待补"
        if trade_amount is not None and trade_currency == "USD":
            cash_change = (-float(trade_amount) if side == "BUY" else float(trade_amount))
            if fee is not None and fee_currency == "USD":
                cash_change -= float(fee)
            cash_change_text = f"{cash_change:,.3f} USD"
        else:
            cash_change_text = "待复核"
        status = audit.get("status") or trade.get("reconciliation_status") or reconciliation.get("status") or "待复核"
        lines.append(
            f"| {trade.get('trade_date') or '待补'} | {trade.get('symbol') or trade.get('security_id') or '-'} | "
            f"{pre_quantity_text} | {quantity:g} | {post_quantity_text} | {amount_text} | {fee_text} | "
            f"{fx_text} | {cash_change_text} | {status} |"
        )
    if not trades:
        lines.append("| 无成交记录 | - | - | - | - | - | - | - | - | - |")
    return lines


def _event_audit_lines(bundle: dict[str, Any]) -> list[str]:
    event = bundle.get("event_assessment", {}) or {}
    position_risk = event.get("position_level_event_risk", {}) or {}
    portfolio_risk = event.get("portfolio_level_event_risk", {}) or {}
    future_gate = event.get("future_event_gate", {}) or {}
    released_quality = event.get("released_data_quality", {}) or {}
    lines = [
        f"- position_level_event_risk：{position_risk.get('status') or 'UNKNOWN'}",
        f"- portfolio_level_event_risk：{portfolio_risk.get('status') or 'UNKNOWN'}",
        f"- future_event_gate：{future_gate.get('gate_result') or 'UNKNOWN'}（仅评估未来事件）",
        f"- released_data_quality：{released_quality.get('status') or 'NO_RELEVANT_EVENT'}（仅影响DQS与置信度）",
    ]
    for item in position_risk.get("events", []) or []:
        lines.append(
            f"- 持仓事件：{item.get('security_id') or '-'}｜{item.get('event_name') or item.get('name')}｜"
            f"{item.get('release_at')}（{item.get('source_timezone')}）｜{item.get('status')}"
        )
    lines.extend(
        [
            "",
            "| 已发布事件 | 状态 | actual | previous | consensus | revision | 发布数据源 | as_of |",
            "|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in released_quality.get("events", []) or []:
        release = item.get("economic_release_data", {}) or {}
        lines.append(
            f"| {item.get('event_name') or item.get('name')} | {item.get('status')} | "
            f"{release.get('actual_value') if release.get('actual_value') is not None else '-'} | "
            f"{release.get('previous_value') if release.get('previous_value') is not None else '-'} | "
            f"{release.get('consensus_value') if release.get('consensus_value') is not None else '-'} | "
            f"{release.get('revision') if release.get('revision') is not None else '-'} | "
            f"{release.get('source') or '-'} | {release.get('as_of') or '-'} |"
        )
    if not released_quality.get("events"):
        lines.append("| 无相关已发布事件 | NO_RELEVANT_EVENT | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "| 缺失项 | 具体缺失字段 | 数据源 | 最后成功时间 | 降分项 |",
            "|---|---|---|---|---|",
        ]
    )
    event_data_issues = [*(event.get("missing_data", []) or []), *(event.get("released_data_issues", []) or [])]
    for item in event_data_issues:
        lines.append(
            f"| {item.get('item')} | {_join(item.get('missing_fields'), '无')} | "
            f"{item.get('data_source') or 'UNKNOWN'} | {item.get('last_success_at') or '无成功记录'} | "
            f"{item.get('score_deduction_item') or '无'} |"
        )
    if not event_data_issues:
        lines.append("| 无 | 无 | 不适用 | 不适用 | 无 |")
    return lines


def _issue_rows(items: list[dict[str, Any]], empty_message: str) -> list[str]:
    rows = [
        "| warning_id | severity | scope | message | affected_scenarios | is_hard_block | recommended_action |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in items:
        rows.append(
            f"| {item.get('warning_id') or item.get('issue_id')} | {item.get('severity')} | "
            f"{item.get('scope') or item.get('affected_scenario')} | {item.get('message')} | "
            f"{_join(item.get('affected_scenarios'))} | "
            f"{'是' if item.get('is_hard_block', item.get('blocking')) else '否'} | "
            f"{item.get('recommended_action') or item.get('suggested_action')} |"
        )
    if not items:
        rows.append(f"| - | - | - | {empty_message} | - | - | - |")
    return rows


def _next_window_lines(bundle: dict[str, Any]) -> list[str]:
    report_data = bundle.get("report_data", {}) or {}
    portfolio = bundle.get("portfolio_snapshot", {}) or {}
    budget = report_data.get("budget", {}) or {}
    scheduled = bundle["scenario_decision_by_key"]["scheduled_dca"]
    next_date = (
        budget.get("next_dca_date")
        or report_data.get("next_scheduled_dca_review")
        or report_data.get("next_review_date")
        or "待计划表确认"
    )
    if isinstance(next_date, str) and "T" in next_date:
        next_date = next_date.split("T", 1)[0]
    scheduled_trades = [
        trade for trade in portfolio.get("confirmed_transactions", []) or []
        if trade.get("trade_origin") == "SCHEDULED_BASE_DCA" or trade.get("order_type") == "base_dca"
    ]
    last_execution_date = max(
        (str(trade.get("trade_date")) for trade in scheduled_trades if trade.get("trade_date")),
        default="无已确认记录",
    )
    review_data = _join(scheduled.get("warning_reasons"), "核心行情、持仓、现金、风险与事件数据")
    conditions = _join(
        report_data.get("next_triggers"),
        "进入执行窗口且核心DQS、现金、风险和数据可比性满足既定门槛",
    )
    if scheduled["final_permission"] == "ALLOW_REDUCED_EXECUTION":
        expected = "减额档位；事件数据补齐并通过复核后再评估正常档位"
    elif scheduled["dqs_score"] >= scheduled["required_dqs"]:
        expected = "待下一窗口按当时事件与风险状态复核正常或减额档位"
    else:
        expected = "暂停档位，直至core_dqs恢复至硬门槛以上"
    return [
        "- DCA cadence：每月两次（每月第1、3个周三；节假日顺延至下一有效交易日）",
        "- 周度规则说明：同一自然周最多执行一次是频率上限，不代表每周定投。",
        f"- 上一次执行日期：{last_execution_date}",
        f"- 下一次理论执行日期：{next_date}",
        "- 跳过中间日期的原因：第2、4、5周的周三不属于当前每月第1、3周执行计划，因此不是漏执行。",
        f"- 下一次需要复核的数据：{review_data}",
        f"- 可执行条件：{conditions}",
        f"- 预计档位：{expected}",
        "- 本报告不预测具体市场涨跌点位。",
    ]


def render_daily_report(
    bundle: dict[str, Any],
    *,
    intraday_summary: dict[str, Any] | None = None,
    grid_strategy_summary: dict[str, Any] | None = None,
) -> str:
    """Pure renderer: every business result is read from one bundle."""
    _require_bundle(bundle)
    portfolio = bundle["portfolio_snapshot"]
    metadata = bundle.get("report_metadata", {}) or {}
    issues = bundle["issues"]
    session_notice: list[str] = []
    if metadata.get("report_variant") == "MARKET_CLOSED":
        session_notice = [
            "## 休市版日报",
            "",
            f"- 市场休市：{metadata.get('market_holiday') or '交易所休市'}",
            f"- 下一交易日：{metadata.get('next_trading_day') or '待确认'}",
            "- 本报告仅更新数据与风险状态，不生成交易建议。",
            "",
        ]
    lines = [
        f"# {bundle['product_version']} 投资日报",
        "",
        f"- 报告业务日期：{metadata.get('report_business_date') or bundle.get('data_cutoff_at')}",
        f"- 运行模式：{metadata.get('report_run_mode_label') or metadata.get('report_run_mode') or 'SCHEDULED'}",
        f"- 决策截止时间：{metadata.get('decision_cutoff_at') or bundle.get('data_cutoff_at')}",
        f"- 历史成交日期：{metadata.get('actual_trade_date') or '无'}",
        f"- FinalDecisionBundle：`{bundle['bundle_hash']}`",
        "",
        *session_notice,
        "## 今日总决策",
        "",
        *_today_summary(bundle),
        "",
        "## 今日场景决策",
        "",
        *_permission_rows(bundle),
        "",
        "## 资产偏离表",
        "",
        *_allocation_rows(bundle),
        "",
        "## 风险分解",
        "",
        *_risk_rows(bundle),
        "",
        "## 数据质量评分",
        "",
        *_dqs_lines(bundle),
        "## 统一真实资产快照",
        "",
        f"- household_total_assets_estimated：{portfolio.get('household_total_assets_estimated', portfolio.get('household_total_assets', 0)):,.2f} 元",
        f"- investable_assets_estimated：{portfolio.get('investable_assets_estimated', portfolio.get('investable_portfolio_assets', 0)):,.2f} 元",
        f"- household_safety_reserve：{portfolio.get('household_safety_reserve', portfolio.get('safety_cash', 0)):,.2f} 元（不进入可投资组合分母）",
        f"- portfolio_cash：{portfolio.get('portfolio_cash', portfolio.get('investable_cash', 0)):,.2f} 元",
        f"- precise_valued_assets：{portfolio.get('precise_valued_assets', portfolio.get('precise_market_value', 0)):,.2f} 元",
        f"- stale_valued_assets：{portfolio.get('stale_valued_assets', 0):,.2f} 元",
        f"- unvalued_cost_records：{portfolio.get('unvalued_cost_records', portfolio.get('pending_valuation_total', 0)):,.2f} 元",
        f"- valuation_coverage_ratio：{float(portfolio.get('valuation_coverage_ratio', 0) or 0):.2%}",
        f"- 精确估值资产：{portfolio.get('precise_valued_assets', portfolio.get('precise_market_value', 0)):,.2f} 元",
        f"- 待估值成本记录：{portfolio.get('unvalued_cost_records', portfolio.get('pending_valuation_total', 0)):,.2f} 元（不进入精确市值和配置占比）",
        f"- 包含待估值成本记录的非精确总额：{portfolio.get('household_total_assets_estimated', portfolio.get('total_asset_including_cost_records', 0)):,.2f} 元",
        f"- 估算总额说明：存在非精确估值时，以上总资产仅为 estimated total，不称为全部精确估值。",
        f"- 账户现金：{(portfolio.get('cash', {}) or {}).get('account_total_cash_cny', 0):,.2f} 元",
        f"- 固定安全储备：{portfolio.get('safety_cash', 0):,.2f} 元",
        f"- 专项可投资现金：{portfolio.get('investable_cash', 0):,.2f} 元",
        "",
        "### 最终持仓（每个 security_id 仅一行）",
        "",
        "| security_id | 数量 | price | currency | fx_rate | price_as_of | source | valuation_status | precise valuation | 市值（CNY） | 资产分类 |",
        "|---|---:|---:|---|---:|---|---|---|---|---:|---|",
    ]
    for row in portfolio.get("positions", []):
        lines.append(
            f"| {row.get('security_id')} | {row.get('total_quantity', row.get('quantity')) or '-'} | "
            f"{row.get('price') if row.get('price') is not None else '-'} | {row.get('currency') or '-'} | "
            f"{row.get('fx_rate') if row.get('fx_rate') is not None else '-'} | {row.get('price_as_of') or '-'} | "
            f"{row.get('source') or '-'} | {row.get('valuation_status') or '-'} | "
            f"{'是' if row.get('precise_valuation') else '否'} | "
            f"{float(row.get('market_value_cny', 0) or 0):,.2f} | {row.get('asset_class')} |"
        )
    lines.extend(
        [
            "",
            "## 事件与数据状态",
            "",
            f"- 事件状态：{bundle['event_assessment'].get('status')}",
            f"- 事件覆盖结论：{_event_coverage_message(bundle)}",
            "- 场景解释：由各场景依赖矩阵独立解释，不作为全局总开关。",
            "",
            *_event_audit_lines(bundle),
            "",
            "## 成交对账审计",
            "",
            *_trade_reconciliation_lines(bundle),
            "",
            "## 警告明细",
            "",
            f"警告总数：**{issues['warning_count']}**",
            "",
            *_issue_rows(issues.get("warning_reasons", []) or [], "无警告"),
            "",
            "## 阻断明细",
            "",
            f"阻断总数：**{issues['blocking_count']}**",
            "",
            *_issue_rows(issues.get("blocking_reasons", []) or [], "无阻断"),
            "",
            f"一致性警告总数：**{issues['consistency_warning_count']}**",
            "",
            "## 下一执行窗口",
            "",
            *_next_window_lines(bundle),
            "",
            "## 附录：统一快照引用",
            "",
            f"主报告快照哈希：`{bundle['render_contract']['main_bundle_hash']}`",
            f"附录快照哈希：`{bundle['render_contract']['appendix_bundle_hash']}`",
            "",
            "Smart Grid 为 SIMULATION_ONLY；模拟资金、持仓和盈亏不进入真实资产与正式交易建议。系统不自动交易，所有执行均需人工确认。",
        ]
    )
    if intraday_summary is not None:
        from src.monitoring.report_summary import render_intraday_report_summary

        lines.extend(["", render_intraday_report_summary(intraday_summary)])
    if grid_strategy_summary is not None:
        from src.grid.long_term_v1.report_summary import render_grid_strategy_summary

        lines.extend(["", render_grid_strategy_summary(grid_strategy_summary)])
    return "\n".join(lines) + "\n"


def render_today_action(bundle: dict[str, Any]) -> str:
    _require_bundle(bundle)
    return "\n".join(
        [
            "# 今日决策卡",
            f"Bundle: `{bundle['bundle_hash']}`",
            "",
            *_today_summary(bundle),
            "",
            *_permission_rows(bundle),
            "",
        ]
    )


def render_portfolio_snapshot(bundle: dict[str, Any]) -> str:
    _require_bundle(bundle)
    portfolio = bundle["portfolio_snapshot"]
    return "\n".join(
        [
            "# PortfolioSnapshot",
            f"Bundle: `{bundle['bundle_hash']}`",
            f"精确总资产：{portfolio.get('total_valued_assets', 0):,.2f} 元",
            f"待估值成本：{portfolio.get('pending_valuation_total', 0):,.2f} 元",
            json.dumps(portfolio.get("asset_class_values", {}), ensure_ascii=False, indent=2),
            "",
        ]
    )


def render_period_report(bundle: dict[str, Any], period: str) -> str:
    _require_bundle(bundle)
    return "\n".join(
        [
            f"# {period} Review",
            f"FinalDecisionBundle: `{bundle['bundle_hash']}`",
            "本报告只引用本次运行的统一决策包，不重新计算资产、DQS或权限。",
            "",
            *_permission_rows(bundle),
            "",
        ]
    )


def render_diagnostic_report(bundle: dict[str, Any], validation: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# FinalDecisionBundle 阻断诊断",
            "",
            "正式日报未生成。以下不变量未通过：",
            *[f"- {error}" for error in validation.get("errors", [])],
            "",
            f"输入快照哈希：`{bundle.get('input_snapshot_hash')}`",
        ]
    ) + "\n"
