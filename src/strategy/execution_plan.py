from __future__ import annotations

from datetime import date
from typing import Any


EQUITY_CATEGORIES = ("美股", "港股", "A股")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _category_map(portfolio_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["category"]: item for item in portfolio_result.get("categories", [])}


def _category_amount(portfolio_result: dict[str, Any], category: str) -> float:
    return _to_float((portfolio_result.get("category_amounts") or {}).get(category), 0.0)


def _category_ratio(categories: dict[str, dict[str, Any]], category: str) -> float:
    return _to_float(categories.get(category, {}).get("current_ratio"), 0.0)


def _target_ratio(categories: dict[str, dict[str, Any]], category: str, fallback: float = 0.0) -> float:
    return _to_float(categories.get(category, {}).get("target_ratio"), fallback)


def _data_quality(live_market_result: dict[str, Any]) -> dict[str, Any]:
    return live_market_result.get("data_quality", {}) or {}


def _risk_multiplier(
    live_market_result: dict[str, Any],
    vix_result: dict[str, Any],
    macro_result: dict[str, Any],
) -> tuple[float, list[str]]:
    quality = _data_quality(live_market_result)
    multiplier = 1.0
    reasons: list[str] = []

    score = int(_to_float(quality.get("score"), 0.0))
    if quality.get("critical_missing") or score < 45:
        multiplier = min(multiplier, 0.50)
        reasons.append("关键数据缺失或可信度较低，今日和本周只执行半速计划。")
    elif quality.get("only_yfinance"):
        multiplier = min(multiplier, 0.75)
        reasons.append("市场数据主要来自 yfinance/缓存，降低执行强度。")

    vix = vix_result.get("vix")
    if vix is None:
        multiplier = min(multiplier, 0.80)
        reasons.append("VIX 暂不可用，按中性偏谨慎处理。")
    else:
        vix_value = _to_float(vix)
        if vix_value >= 30:
            multiplier = min(multiplier, 0.30)
            reasons.append("VIX >= 30，暂停追涨，只允许小额分批低吸。")
        elif vix_value >= 20:
            multiplier = min(multiplier, 0.70)
            reasons.append("VIX 20-30，定投金额降低30%。")
        else:
            reasons.append("VIX < 20，波动未失控，可以按纪律定投。")

    if macro_result.get("has_high_event_next_7_days"):
        multiplier = min(multiplier, 0.75)
        reasons.append("未来7天有 high 级别宏观事件，重大事件前不追涨。")

    if not reasons:
        reasons.append("数据和风险未触发降速规则，按正常计划执行。")
    return multiplier, reasons


def _configured_monthly_budget_wan(config: dict[str, Any], dca_result: dict[str, Any]) -> float:
    monthly = config.get("monthly_investment", {}) or {}
    configured = _to_float(monthly.get("total_wan"), 0.0)
    if configured > 0:
        return configured
    return _to_float(dca_result.get("monthly_budget"), 0.0) / 10000


def _equity_gaps(portfolio_result: dict[str, Any], categories: dict[str, dict[str, Any]]) -> dict[str, float]:
    total_assets = _to_float(portfolio_result.get("total_assets_wan"), 0.0)
    gaps: dict[str, float] = {}
    for category in EQUITY_CATEGORIES:
        target_amount = total_assets * _target_ratio(categories, category)
        gaps[category] = max(0.0, target_amount - _category_amount(portfolio_result, category))
    return gaps


def _allocate_equity_orders(total_wan: float, gaps: dict[str, float]) -> list[dict[str, Any]]:
    if total_wan <= 0:
        return []

    equity_gap_total = sum(gaps.values())
    if equity_gap_total <= 0:
        weights = {
            "VOO": 0.40,
            "QQQ": 0.15,
            "沪深300ETF": 0.25,
            "恒生科技ETF": 0.15,
            "恒生医疗ETF": 0.05,
        }
    else:
        us_weight = min(0.60, max(0.35, gaps.get("美股", 0.0) / equity_gap_total))
        cn_weight = min(0.30, max(0.15, gaps.get("A股", 0.0) / equity_gap_total))
        hk_weight = max(0.10, min(0.25, 1.0 - us_weight - cn_weight))
        normalizer = us_weight + cn_weight + hk_weight
        us_weight, cn_weight, hk_weight = us_weight / normalizer, cn_weight / normalizer, hk_weight / normalizer
        weights = {
            "VOO": us_weight * 0.70,
            "QQQ": us_weight * 0.30,
            "沪深300ETF": cn_weight,
            "恒生科技ETF": hk_weight * 0.75,
            "恒生医疗ETF": hk_weight * 0.25,
        }

    orders = []
    for name, weight in weights.items():
        amount_wan = round(total_wan * weight, 2)
        if amount_wan <= 0:
            continue
        orders.append(
            {
                "name": name,
                "amount_wan": amount_wan,
                "amount_yuan": round(amount_wan * 10000),
                "parts": 2 if amount_wan >= 0.3 and name in {"VOO", "QQQ", "恒生科技ETF"} else 1,
            }
        )
    return orders


def _gold_bar_summary(portfolio_result: dict[str, Any]) -> dict[str, Any]:
    for holding in portfolio_result.get("holdings", []):
        name = str(holding.get("name", ""))
        note = str(holding.get("note", ""))
        if holding.get("category") == "黄金" and ("金条" in name or "金条" in note):
            quantity = _to_float(holding.get("quantity"), 0.0)
            amount = _to_float(holding.get("amount_wan"), 0.0)
            price = holding.get("price_cny_per_gram")
            status = holding.get("valuation_status", "manual")
            if price:
                text = f"{quantity:.0f}克金条按{float(price):.2f}元/克估值，市值约{amount:.2f}万元。"
            elif amount > 0:
                text = f"{quantity:.0f}克金条使用持仓文件手动估值，市值约{amount:.2f}万元；建议继续接入每日金价校验。"
            else:
                text = f"{quantity:.0f}克金条暂未估值，今日不按黄金比例做激进调仓。"
            return {
                "status": status,
                "quantity_grams": quantity,
                "amount_wan": amount,
                "price_cny_per_gram": price,
                "text": text,
            }
    return {
        "status": "missing",
        "quantity_grams": 0.0,
        "amount_wan": 0.0,
        "price_cny_per_gram": None,
        "text": "未识别到实物金条持仓。",
    }


def build_execution_plan(
    portfolio_result: dict[str, Any],
    market_result: dict[str, Any],
    live_market_result: dict[str, Any],
    vix_result: dict[str, Any],
    macro_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """生成今日、周度、月度和债券转权益执行计划。"""

    categories = _category_map(portfolio_result)
    total_assets = _to_float(portfolio_result.get("total_assets_wan"), 0.0)
    cash_wan = _category_amount(portfolio_result, "现金")
    bond_wan = _category_amount(portfolio_result, "债券")
    gold_wan = _category_amount(portfolio_result, "黄金")
    cash_ratio = _category_ratio(categories, "现金")
    bond_ratio = _category_ratio(categories, "债券")
    gold_ratio = _category_ratio(categories, "黄金")
    target_cash = total_assets * _target_ratio(categories, "现金", 0.08)
    cash_floor_wan = max(target_cash, total_assets * 0.05)
    cash_available_wan = max(0.0, cash_wan - cash_floor_wan)
    bond_target_wan = total_assets * _target_ratio(categories, "债券", 0.25)
    bond_excess_wan = max(0.0, bond_wan - bond_target_wan)
    equity_gaps = _equity_gaps(portfolio_result, categories)
    equity_current = sum(_category_ratio(categories, category) for category in EQUITY_CATEGORIES)
    equity_target = sum(_target_ratio(categories, category) for category in EQUITY_CATEGORIES)
    equity_gap_ratio = max(0.0, equity_target - equity_current)

    monthly_base_wan = _configured_monthly_budget_wan(config, dca_result)
    monthly_base_wan = monthly_base_wan if monthly_base_wan > 0 else 2.0

    risk_multiplier, risk_reasons = _risk_multiplier(live_market_result, vix_result, macro_result)
    cash_plan_wan = min(cash_available_wan, monthly_base_wan)
    bond_transfer_month_wan = 0.0
    if bond_ratio > 0.30 and bond_excess_wan > 0:
        bond_transfer_month_wan = min(5.0, max(3.0, bond_excess_wan * 0.08))
    if _data_quality(live_market_result).get("critical_missing"):
        bond_transfer_month_wan = min(bond_transfer_month_wan, 3.0)

    month_total_wan = max(monthly_base_wan, cash_plan_wan + bond_transfer_month_wan)
    week_total_wan = min(month_total_wan * 0.35, month_total_wan)
    today_total_wan = min(week_total_wan * 0.45, cash_wan * 0.03)
    today_total_wan *= risk_multiplier
    week_total_wan *= max(0.5, risk_multiplier)
    month_execute_wan = month_total_wan * max(0.7, risk_multiplier)

    if cash_ratio < 0.05:
        today_total_wan = 0.0
        week_total_wan = 0.0
        month_execute_wan = 0.0
        risk_reasons.append("现金低于5%，暂停新增风险资产，优先恢复现金。")

    today_total_wan = round(max(0.0, today_total_wan), 2)
    week_total_wan = round(max(today_total_wan, week_total_wan), 2)
    month_execute_wan = round(max(week_total_wan, month_execute_wan), 2)

    today_orders = _allocate_equity_orders(today_total_wan, equity_gaps)
    week_orders = _allocate_equity_orders(week_total_wan, equity_gaps)
    month_orders = _allocate_equity_orders(month_execute_wan, equity_gaps)

    pause_list = ["债券", "黄金", "TLT"]
    if gold_ratio >= 0.15:
        pause_list.append("黄金ETF 518880")
    pause_list.append("NVDA单股追高")

    bond_path = {
        "this_month_transfer_wan": round(bond_transfer_month_wan, 2),
        "this_week_transfer_wan": round(min(3.0, bond_transfer_month_wan), 2),
        "three_month_transfer_wan": round(min(bond_excess_wan, bond_transfer_month_wan * 3), 2),
        "reason": (
            f"债券当前占比{bond_ratio * 100:.2f}%，目标{_target_ratio(categories, '债券', 0.25) * 100:.2f}%，"
            f"超配约{bond_excess_wan:.2f}万元；优先用到期/赎回资金转入权益定投池。"
        ),
    }

    return {
        "as_of": date.today().isoformat(),
        "action_level": "B级：建议本周执行" if today_total_wan > 0 or bond_transfer_month_wan > 0 else "C级：继续观察",
        "today_buy_wan": today_total_wan,
        "week_buy_wan": week_total_wan,
        "month_buy_wan": month_execute_wan,
        "today_orders": today_orders,
        "week_orders": week_orders,
        "month_orders": month_orders,
        "pause_list": pause_list,
        "risk_reasons": risk_reasons,
        "cash_policy": (
            f"现金{cash_wan:.2f}万元，占比{cash_ratio * 100:.2f}%；"
            f"保留底线约{cash_floor_wan:.2f}万元，可用于本月计划的现金弹药约{cash_available_wan:.2f}万元。"
        ),
        "bond_to_equity_path": bond_path,
        "gold_bar": _gold_bar_summary(portfolio_result),
        "equity_path": (
            f"权益当前约{equity_current * 100:.2f}%，目标{equity_target * 100:.2f}%，"
            f"缺口约{equity_gap_ratio * 100:.2f}%；新增资金优先补VOO、QQQ、沪深300ETF和恒生科技ETF。"
        ),
        "data_policy": (
            f"数据可信度{int(_to_float(_data_quality(live_market_result).get('score'), 0))}/100；"
            "关键数据缺失时只做基础/半速计划，不做激进调仓。"
        ),
        "rebalance_summary": allocation_rebalance_result.get("summary", "暂无再平衡结论。"),
        "market_summary": market_result.get("summary", "暂无市场摘要。"),
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。",
    }
