from __future__ import annotations

import json
from typing import Any


def _require_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("bundle_type") != "FinalDecisionBundle":
        raise TypeError("Report rendering requires FinalDecisionBundle")


def _join(values: list[Any] | None, empty: str = "无") -> str:
    return "；".join(str(value) for value in values or [] if str(value)) or empty


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
        direction = str(repair.get("portfolio_repair_direction") or "MAINTAIN")
        method = {
            "ADD": "优先使用新增资金",
            "REDUCE_OR_PAUSE": "暂停新增；是否卖出须人工确认",
            "MAINTAIN": "维持现有配置",
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
    rows = [
        "| 风险因子 | 原始值/依据 | 子分数 | 权重 | 对总风险分贡献 | 缺失指标处理 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in market_risk.get("components", []) or []:
        data_status = str(item.get("data_status") or "VALID")
        missing = (
            "按中性风险值计入并降低置信度"
            if data_status in {"NOT_CONNECTED", "DATA_INSUFFICIENT", "SOURCE_FAILED"}
            else "不适用（数据有效）"
        )
        rows.append(
            f"| {item.get('item')} | {item.get('raw_value', item.get('basis', '-'))} | "
            f"{item.get('score', 0)} | {item.get('weight', 0)}% | {item.get('score', 0)} | {missing} |"
        )
    rows.append(
        f"| **合计** | 置信度：{market_risk.get('confidence', 'unknown')} | "
        f"**{market_risk.get('score', risk.get('score', 0))}** | "
        f"**{market_risk.get('market_risk_weights_sum', risk.get('market_risk_weights_sum', 0))}%** | "
        f"**{market_risk.get('score', risk.get('score', 0))}** | - |"
    )
    return rows


def _dqs_lines(bundle: dict[str, Any]) -> list[str]:
    lines: list[str] = [
        *[f"- {name}: **{result['total']}**" for name, result in bundle["dqs_results"].items()],
        "",
    ]
    for name, result in bundle["dqs_results"].items():
        lines.extend(
            [
                f"### {name}",
                "",
                "| 维度 | 得分 | 满分 | 扣分原因 | 缺失数据 |",
                "|---|---:|---:|---|---|",
            ]
        )
        for item in result.get("breakdown", []) or []:
            score = int(item.get("score", 0) or 0)
            max_score = item.get("max", score)
            reason = item.get("reason") or item.get("basis") or "无扣分说明"
            missing = _join(item.get("missing_data") or item.get("missing_fields"), "无")
            lines.append(f"| {item.get('item')} | {score} | {max_score} | {reason} | {missing} |")
        expression = " + ".join(str(int(row.get("score", 0) or 0)) for row in result.get("breakdown", []) or [])
        lines.extend(["", f"最终求和：{expression} = **{result['total']}**", ""])
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
    scheduled = bundle["scenario_decision_by_key"]["scheduled_dca"]
    next_date = report_data.get("next_scheduled_dca_review") or report_data.get("next_review_date") or "待计划表确认"
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
        f"- 下一次 Scheduled DCA 日期：{next_date}",
        f"- 下一次需要复核的数据：{review_data}",
        f"- 可执行条件：{conditions}",
        f"- 预计档位：{expected}",
        "- 本报告不预测具体市场涨跌点位。",
    ]


def render_daily_report(bundle: dict[str, Any]) -> str:
    """Pure renderer: every business result is read from one bundle."""
    _require_bundle(bundle)
    portfolio = bundle["portfolio_snapshot"]
    metadata = bundle.get("report_metadata", {}) or {}
    issues = bundle["issues"]
    lines = [
        f"# {bundle['product_version']} 投资日报",
        "",
        f"- 报告业务日期：{metadata.get('report_business_date') or bundle.get('data_cutoff_at')}",
        f"- 运行模式：{metadata.get('report_run_mode_label') or metadata.get('report_run_mode') or 'SCHEDULED'}",
        f"- 决策截止时间：{metadata.get('decision_cutoff_at') or bundle.get('data_cutoff_at')}",
        f"- 历史成交日期：{metadata.get('actual_trade_date') or '无'}",
        f"- FinalDecisionBundle：`{bundle['bundle_hash']}`",
        "",
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
        f"- 精确估值资产：{portfolio.get('total_valued_assets', 0):,.2f} 元",
        f"- 待估值成本记录：{portfolio.get('pending_valuation_total', 0):,.2f} 元（不进入精确市值和配置占比）",
        f"- 包含待估值成本记录的非精确总额：{portfolio.get('total_asset_including_cost_records', 0):,.2f} 元",
        f"- 账户现金：{(portfolio.get('cash', {}) or {}).get('account_total_cash_cny', 0):,.2f} 元",
        f"- 固定安全储备：{portfolio.get('safety_cash', 0):,.2f} 元",
        f"- 专项可投资现金：{portfolio.get('investable_cash', 0):,.2f} 元",
        "",
        "### 最终持仓（每个 security_id 仅一行）",
        "",
        "| security_id | 数量 | 市值（CNY） | 资产分类 |",
        "|---|---:|---:|---|",
    ]
    for row in portfolio.get("positions", []):
        lines.append(
            f"| {row.get('security_id')} | {row.get('total_quantity', row.get('quantity')) or '-'} | "
            f"{float(row.get('market_value_cny', 0) or 0):,.2f} | {row.get('asset_class')} |"
        )
    lines.extend(
        [
            "",
            "## 事件与数据状态",
            "",
            f"- 事件状态：{bundle['event_assessment'].get('status')}",
            "- 事件结论：由各场景依赖矩阵独立解释，不作为全局总开关。",
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
