from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def validate_decision(decision: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    dqs = int(decision.get("dqs", 0) or 0)
    mode = str(decision.get("amount_mode", ""))
    today = float(decision.get("today_buy_amount_yuan", 0) or 0)
    week = float(decision.get("week_buy_amount_yuan", 0) or 0)
    month = float(decision.get("month_buy_amount_yuan", 0) or 0)
    cash_available = float(decision.get("cash_available_wan", 0) or 0)

    if dqs < 70 and (today > 0 or week > 0 or month > 0):
        errors.append("DQS低于70时不得输出买入金额。")
    if 70 <= dqs < 80 and (today > 0 or week > 0 or month > 0):
        errors.append("DQS 70-79只能输出方向，不得输出具体金额。")
    if 80 <= dqs < 90 and mode not in {"upper_limit", "blocked", "direction_only"}:
        errors.append("DQS 80-89必须使用金额上限或方向模式，不得使用精确金额。")
    if dqs < 90 and bool(decision.get("precise_amount_allowed")):
        errors.append("DQS低于90时不得允许精确金额。")
    if cash_available <= 0 and today > 0:
        errors.append("可投资现金不足时不得安排今日现金买入。")
    if today > week and week > 0:
        errors.append("今日买入金额不得大于本周计划。")
    if week > month and month > 0:
        errors.append("本周计划不得大于本月计划。")
    if decision.get("tactical_add") and int(decision.get("risk_score", 0) or 0) >= 75:
        errors.append("市场风险75以上不得允许机会加仓。")
    if decision.get("tactical_add") and decision.get("macro_event_high_next_7_days"):
        errors.append("高等级宏观事件前不得允许机会加仓。")

    source = decision.get("source_coverage", {}) or {}
    if float(source.get("dual_source_coverage", 0.0) or 0.0) == 0.0 and decision.get("precise_amount_allowed"):
        errors.append("双源验证覆盖率为0时不得允许精确金额。")
    if decision.get("ai_status") != "available" and decision.get("action_level") == "A":
        errors.append("AI不可用时不得输出A级交易。")

    if decision.get("rebalance_required") and not decision.get("rebalance_today"):
        warnings.append("需要再平衡，但今日只给方向或分批计划。")
    if decision.get("conditional_month_buy_upper_yuan", 0) and cash_available <= 0:
        warnings.append("本月买入依赖债券到期/赎回资金到账。")

    return {
        "validated_at": datetime.now().isoformat(timespec="seconds"),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "fallback_required": bool(errors),
    }


def conservative_decision(decision: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    if validation.get("ok"):
        return decision

    downgraded = dict(decision)
    downgraded.update(
        {
            "action_level": "C",
            "tactical_add": False,
            "rebalance_today": False,
            "reduce_positions": False,
            "observation_only": True,
            "amount_mode": "blocked",
            "amount_label": "一致性验证未通过，不输出交易金额",
            "precise_amount_allowed": False,
            "today_buy_amount_yuan": 0,
            "week_buy_amount_yuan": 0,
            "month_buy_amount_yuan": 0,
            "one_sentence": "本次建议因一致性检查未完全通过，已自动降级为保守观察方案。",
        }
    )
    warnings = list(downgraded.get("warnings", []) or [])
    warnings.append("一致性验证未通过，已降级为保守方案。")
    warnings.extend(validation.get("errors", []) or [])
    downgraded["warnings"] = list(dict.fromkeys(warnings))
    return downgraded


def _items(values: list[str] | None) -> list[str]:
    return [f"- {item}" for item in (values or [])] or ["- 无"]


def format_validation_report(validation: dict[str, Any], decision: dict[str, Any]) -> str:
    dqs_value = decision.get("dqs")
    if isinstance(dqs_value, dict):
        dqs_text = f"{dqs_value.get('score')} / {dqs_value.get('mode_label')}"
        amount_text = dqs_value.get("mode", "不适用")
    else:
        dqs_text = str(dqs_value)
        amount_text = f"{decision.get('amount_mode')} / {decision.get('amount_label')}"
    lines = [
        "# Validation Report",
        "",
        f"- 状态：{'通过' if validation.get('ok') else '未通过，已降级'}",
        f"- 时间：{validation.get('validated_at')}",
        f"- DQS：{dqs_text}",
        f"- 金额模式：{amount_text}",
        f"- 是否触发保守降级：{'是' if validation.get('fallback_applied') else '否'}",
        "",
        "## Final Errors",
    ]
    lines.extend(_items(validation.get("errors")))
    lines.extend(["", "## Final Warnings"])
    lines.extend(_items(validation.get("warnings")))
    lines.extend(["", "## Initial Errors Before Fallback"])
    lines.extend(_items(validation.get("initial_errors")))
    lines.extend(["", "## Initial Warnings Before Fallback"])
    lines.extend(_items(validation.get("initial_warnings")))
    lines.append("")
    return "\n".join(lines)


def write_validation_report(path: Path, validation: dict[str, Any], decision: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_validation_report(validation, decision), encoding="utf-8")
