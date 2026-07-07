from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

MARKET_INDICATOR_BY_SYMBOL = {
    "VOO": "标普500涨跌幅",
    "QQQ": "纳斯达克涨跌幅",
    "510300.SS": "沪深300涨跌幅",
    "3067.HK": "恒生科技涨跌幅",
}


def load_strategy_settings(settings_path: Path | None = None) -> dict[str, Any]:
    """读取策略配置；失败时返回空配置，避免主程序中断。"""
    path = settings_path or SETTINGS_PATH
    if not path.exists():
        write_log("settings.yaml 不存在，策略模块使用空配置", filename="strategy.log")
        return {}

    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except Exception as exc:  # noqa: BLE001 - 配置失败不能影响日报生成
        write_log(f"settings.yaml 读取失败，策略模块使用空配置：{exc}", filename="strategy.log")
        return {}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_item_for_symbol(symbol: str, market_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    indicator = MARKET_INDICATOR_BY_SYMBOL.get(symbol, "")
    return market_data.get(indicator, {})


def _market_change(item: dict[str, Any]) -> float:
    change = _to_float(item.get("change"), default=0.0)
    if change != 0:
        return change
    return _to_float(item.get("value"), default=0.0)


def _vix_multiplier(vix_result: dict[str, Any]) -> tuple[float, str]:
    vix = vix_result.get("vix")
    if vix is None:
        return 1.0, "VIX 暂不可用，执行基础定投，不额外加仓。"

    vix_value = _to_float(vix)
    if vix_value < 20:
        return 1.0, "VIX < 20，市场波动正常，按计划正常定投。"
    if vix_value < 30:
        return 0.7, "VIX 20-30，风险升高，本期定投金额减少30%。"
    return 0.3, "VIX >= 30，暂停追涨，只允许小额分批低吸。"


def build_dca_plan(
    market_data: dict[str, dict[str, Any]],
    vix_result: dict[str, Any],
    macro_result: dict[str, Any],
    settings_path: Path | None = None,
) -> dict[str, Any]:
    """根据 VIX、估值和宏观事件生成定投建议。"""
    settings = load_strategy_settings(settings_path)
    plan = settings.get("dca_plan", {}) or {}
    enabled = bool(plan.get("enabled", False))
    monthly_budget = _to_float(plan.get("monthly_budget"), default=0.0)
    targets = plan.get("targets", []) or []

    if not enabled or not targets:
        return {
            "enabled": enabled,
            "monthly_budget": monthly_budget,
            "today_continue": False,
            "total_suggested_amount": 0.0,
            "targets": [],
            "summary": "未启用定投计划。",
            "discipline": "不自动交易，所有定投建议仅供投资辅助。",
            "disclaimer": "仅供投资辅助，不构成投资建议；系统不会自动交易，也不承诺收益。",
        }

    multiplier, vix_rule = _vix_multiplier(vix_result)
    high_macro = bool(macro_result.get("has_high_event_next_7_days"))
    macro_rule = (
        "未来7天有 high 级别宏观事件，不额外加仓；基础定投可继续。"
        if high_macro
        else "未来7天暂无 high 级别宏观事件，按纪律执行定投。"
    )

    total_base = sum(_to_float(item.get("base_amount"), default=0.0) for item in targets)
    budget_scale = min(1.0, monthly_budget / total_base) if total_base > 0 and monthly_budget > 0 else 1.0

    target_results: list[dict[str, Any]] = []
    for item in targets:
        symbol = str(item.get("symbol", "")).strip()
        name = str(item.get("name", symbol)).strip()
        base_amount = _to_float(item.get("base_amount"), default=0.0) * budget_scale
        market_item = _market_item_for_symbol(symbol, market_data)
        valuation = str(market_item.get("valuation", "未填写"))
        change = _market_change(market_item)

        suggested_amount = round(base_amount * multiplier, 2)
        action = "正常定投"
        if multiplier < 0.5:
            action = "小额定投"
        elif multiplier < 1:
            action = "减少定投"

        notes = [vix_rule, macro_rule]
        if valuation == "偏低" and change <= -5 and multiplier >= 0.7 and not high_macro:
            notes.append("标的估值偏低且下跌明显，可把本期金额拆成2-3笔分批执行。")
        elif valuation == "偏低":
            notes.append("估值偏低，可继续跟踪，但不做一次性重仓。")
        elif valuation == "偏高":
            notes.append("估值偏高，不建议追加计划外资金。")

        target_results.append(
            {
                "symbol": symbol,
                "name": name,
                "base_amount": round(base_amount, 2),
                "suggested_amount": suggested_amount,
                "action": action,
                "valuation": valuation,
                "change": round(change, 2),
                "reason": " ".join(notes),
            }
        )

    total_suggested = round(sum(item["suggested_amount"] for item in target_results), 2)
    today_continue = total_suggested > 0
    if multiplier >= 1:
        summary = "今日继续按计划定投，不额外追涨。"
    elif multiplier >= 0.7:
        summary = "今日继续定投，但按风险规则减少30%。"
    else:
        summary = "今日只保留小额定投，暂停追涨。"

    return {
        "enabled": enabled,
        "monthly_budget": monthly_budget,
        "today_continue": today_continue,
        "risk_multiplier": multiplier,
        "vix_rule": vix_rule,
        "macro_rule": macro_rule,
        "total_suggested_amount": total_suggested,
        "targets": target_results,
        "summary": summary,
        "discipline": "定投不等于加杠杆；重大事件前不额外加仓，风险升高时降低节奏。",
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不会自动交易，也不承诺收益。",
    }
