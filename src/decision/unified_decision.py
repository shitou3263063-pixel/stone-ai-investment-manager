from __future__ import annotations

from datetime import date, datetime
from typing import Any


CN_LABELS = {
    "us_stock": ("美股", "缇庤偂"),
    "hk_stock": ("港股", "娓偂"),
    "cn_stock": ("A股", "A鑲"),
    "bond": ("债券", "鍊哄埜"),
    "gold": ("黄金", "榛勯噾"),
    "cash": ("现金", "鐜伴噾"),
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _category_item(portfolio_result: dict[str, Any], logical_name: str) -> dict[str, Any]:
    labels = CN_LABELS.get(logical_name, (logical_name,))
    for item in portfolio_result.get("categories", []) or []:
        category = str(item.get("category", ""))
        if any(label in category for label in labels):
            return item
    return {}


def _category_amount(portfolio_result: dict[str, Any], logical_name: str) -> float:
    labels = CN_LABELS.get(logical_name, (logical_name,))
    amounts = portfolio_result.get("category_amounts", {}) or {}
    for key, value in amounts.items():
        if any(label in str(key) for label in labels):
            return _to_float(value)
    item = _category_item(portfolio_result, logical_name)
    ratio = _to_float(item.get("current_ratio"))
    total = _to_float(portfolio_result.get("total_assets_wan"))
    return ratio * total


def _category_ratio(portfolio_result: dict[str, Any], logical_name: str) -> float:
    item = _category_item(portfolio_result, logical_name)
    return _to_float(item.get("current_ratio"))


def _category_target(portfolio_result: dict[str, Any], logical_name: str, fallback: float) -> float:
    item = _category_item(portfolio_result, logical_name)
    return _to_float(item.get("target_ratio"), fallback)


def _amount_policy(score: int) -> tuple[str, bool, bool]:
    if score >= 90:
        return "exact", True, True
    if score >= 80:
        return "upper_limit", True, False
    if score >= 70:
        return "direction_only", False, False
    return "blocked", False, False


def _risk_policy(risk_score: int) -> dict[str, Any]:
    if risk_score >= 85:
        return {
            "base_dca": "reduced",
            "tactical_add": False,
            "rebalance_today": False,
            "risk_note": "市场风险极高，暂停战术加仓，基础定投降档。",
        }
    if risk_score >= 75:
        return {
            "base_dca": "allowed",
            "tactical_add": False,
            "rebalance_today": False,
            "risk_note": "市场风险偏高，基础定投可继续，但禁止额外战术加仓。",
        }
    if risk_score >= 60:
        return {
            "base_dca": "allowed",
            "tactical_add": True,
            "rebalance_today": True,
            "risk_note": "市场风险中高，战术加仓需要减半并分批。",
        }
    return {
        "base_dca": "allowed",
        "tactical_add": True,
        "rebalance_today": True,
        "risk_note": "市场风险未触发高风险限制，可按纪律执行。",
    }


def build_unified_decision(
    *,
    portfolio_result: dict[str, Any],
    market_result: dict[str, Any],
    live_market_result: dict[str, Any],
    macro_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
    execution_plan_result: dict[str, Any],
    ai_advice_result: dict[str, Any],
) -> dict[str, Any]:
    total_assets = _to_float(portfolio_result.get("total_assets_wan"))
    cash_wan = _category_amount(portfolio_result, "cash")
    bond_wan = _category_amount(portfolio_result, "bond")
    cash_target_ratio = _category_target(portfolio_result, "cash", 0.08)
    cash_floor_wan = max(total_assets * cash_target_ratio, total_assets * 0.05)
    cash_available_wan = max(0.0, cash_wan - cash_floor_wan)
    bond_target_wan = total_assets * _category_target(portfolio_result, "bond", 0.25)
    bond_excess_wan = max(0.0, bond_wan - bond_target_wan)

    quality = live_market_result.get("data_quality", {}) or {}
    audit = live_market_result.get("source_audit", {}) or quality.get("source_audit", {}) or {}
    dqs_score = int(_to_float(quality.get("score"), _to_float((execution_plan_result.get("dqs") or {}).get("score"))))
    amount_mode, trade_allowed_by_dqs, precise_amount_allowed = _amount_policy(dqs_score)
    risk_score = int(_to_float(market_result.get("market_risk_score"), 50))
    risk_policy = _risk_policy(risk_score)

    base_dca_allowed = risk_policy["base_dca"] in {"allowed", "reduced"} and dqs_score >= 70
    tactical_add_allowed = bool(risk_policy["tactical_add"]) and trade_allowed_by_dqs
    if float(audit.get("dual_source_coverage", 0.0) or 0.0) == 0.0:
        precise_amount_allowed = False
        if amount_mode == "exact":
            amount_mode = "upper_limit"

    has_high_event = bool(macro_result.get("has_high_event_next_7_days"))
    if has_high_event:
        tactical_add_allowed = False

    rebalance_required = bool(allocation_rebalance_result.get("need_rebalance"))
    rebalance_today = rebalance_required and bool(risk_policy["rebalance_today"]) and dqs_score >= 80

    bond_monthly_transfer_wan = 0.0
    if bond_excess_wan > 0:
        bond_monthly_transfer_wan = min(4.5, max(3.0, bond_excess_wan * 0.04))

    planned_today_wan = _to_float(execution_plan_result.get("today_buy_wan"))
    planned_week_wan = _to_float(execution_plan_result.get("week_buy_wan"))
    planned_month_wan = _to_float(execution_plan_result.get("month_buy_wan"))

    cash_funded_today_wan = 0.0
    conditional_month_wan = min(planned_month_wan, bond_monthly_transfer_wan)
    if cash_available_wan > 0 and base_dca_allowed:
        cash_funded_today_wan = min(planned_today_wan, cash_available_wan)

    if amount_mode in {"blocked", "direction_only"}:
        cash_funded_today_wan = 0.0
        planned_week_wan = 0.0
        planned_month_wan = 0.0
    elif amount_mode == "upper_limit":
        planned_week_wan = min(planned_week_wan, max(0.0, cash_available_wan + bond_monthly_transfer_wan))
        planned_month_wan = min(planned_month_wan, max(0.0, cash_available_wan + bond_monthly_transfer_wan))

    if cash_available_wan <= 0:
        cash_funded_today_wan = 0.0
        planned_week_wan = 0.0

    action_level = "C"
    if dqs_score < 70 or quality.get("blocking_errors"):
        action_level = "C"
    elif tactical_add_allowed and risk_score < 60 and amount_mode == "exact":
        action_level = "A"
    elif base_dca_allowed or rebalance_required:
        action_level = "B"

    if amount_mode == "upper_limit":
        amount_label = "金额上限"
    elif amount_mode == "exact":
        amount_label = "精确金额"
    elif amount_mode == "direction_only":
        amount_label = "方向建议"
    else:
        amount_label = "不输出交易金额"

    ai_status = ai_advice_result.get("ai_status") or ("available" if ai_advice_result.get("enabled") else "rule_only")
    if ai_status != "available" and action_level == "A":
        action_level = "B"

    paused_assets = list(dict.fromkeys(execution_plan_result.get("pause_list", []) or ["债券", "黄金", "TLT"]))
    priority_assets = ["VOO/QQQ", "沪深300ETF", "恒生科技ETF小额分批"]

    warnings: list[str] = []
    reasons: list[str] = []
    if dqs_score < 90:
        reasons.append(f"DQS={dqs_score}，{amount_label}模式。")
    if float(audit.get("dual_source_coverage", 0.0) or 0.0) == 0.0:
        warnings.append("双源验证率为0，禁止精确金额建议。")
    if cash_available_wan <= 0:
        warnings.append("当前现金低于或接近安全底线，今日不安排直接现金买入。")
    if has_high_event:
        warnings.append("未来7天有高等级宏观事件，重大事件前不追涨。")
    reasons.append(risk_policy["risk_note"])
    if bond_monthly_transfer_wan > 0:
        reasons.append("债券明显超配，采用月度渐进转权益路径，优先使用到期或赎回资金。")

    observation_only = amount_mode == "blocked" or (not base_dca_allowed and not rebalance_required)
    one_sentence = (
        "今日不做大额交易；基础定投可按纪律评估，债券转权益只走月度渐进路径，所有操作需人工确认。"
    )

    return {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "portfolio_value_wan": round(total_assets, 2),
        "base_dca": base_dca_allowed,
        "base_dca_status": risk_policy["base_dca"],
        "tactical_add": tactical_add_allowed,
        "rebalance_required": rebalance_required,
        "rebalance_today": rebalance_today,
        "reduce_positions": False,
        "observation_only": observation_only,
        "risk_score": risk_score,
        "dqs": dqs_score,
        "action_level": action_level,
        "amount_mode": amount_mode,
        "amount_label": amount_label,
        "precise_amount_allowed": precise_amount_allowed,
        "today_buy_amount_yuan": round(cash_funded_today_wan * 10000),
        "week_buy_amount_yuan": round(planned_week_wan * 10000),
        "month_buy_amount_yuan": round(planned_month_wan * 10000),
        "conditional_month_buy_upper_yuan": round(conditional_month_wan * 10000),
        "cash_current_wan": round(cash_wan, 2),
        "cash_floor_wan": round(cash_floor_wan, 2),
        "cash_available_wan": round(cash_available_wan, 2),
        "cash_after_today_wan": round(cash_wan - cash_funded_today_wan, 2),
        "bond_monthly_transfer_wan": round(bond_monthly_transfer_wan, 2),
        "bond_weekly_transfer_wan": round(min(3.0, bond_monthly_transfer_wan), 2),
        "paused_assets": paused_assets,
        "priority_assets": priority_assets,
        "ai_status": ai_status,
        "llm_provider": ai_advice_result.get("actual_provider", ai_advice_result.get("model", "")),
        "confidence": max(35, min(85, dqs_score - (10 if ai_status != "available" else 0))),
        "reasons": reasons,
        "warnings": warnings,
        "source_coverage": {
            "data_source_coverage": audit.get("data_source_coverage", audit.get("critical_metric_coverage", 0.0)),
            "dual_source_coverage": audit.get("dual_source_coverage", 0.0),
            "tier1_coverage": audit.get("tier1_coverage", 0.0),
        },
        "macro_event_high_next_7_days": has_high_event,
        "one_sentence": one_sentence,
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不自动交易，不接券商下单权限，不承诺收益。",
    }
