from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from utils.data_loader import load_config, project_root
from utils.logger import write_log


VERSION_NAME = "Stone AI Investment Manager Pro V12.2 Smart Grid"

DEFAULT_STRATEGY: dict[str, Any] = {
    "target_allocation": {"美股": 0.30, "港股": 0.12, "A股": 0.10, "债券": 0.25, "黄金": 0.15, "现金": 0.08},
    "cash": {"safety_ratio": 0.08, "hard_floor_ratio": 0.05},
    "dqs_thresholds": {
        "exact_amount": 85,
        "range_amount": 75,
        "direction_only": 60,
        "cap_when_dual_source_below": 0.25,
        "severe_conflict_cap": 59,
    },
    "dqs_weights": {
        "market_completeness": 25,
        "macro_completeness": 15,
        "tier1_coverage": 15,
        "dual_source": 20,
        "freshness": 15,
        "consistency": 10,
    },
    "risk_weights": {
        "valuation": 20,
        "volatility": 15,
        "interest_rate": 15,
        "liquidity": 10,
        "macro_event": 15,
        "trend": 10,
        "policy_geo": 10,
        "data_quality": 5,
    },
    "opportunity_weights": {
        "valuation": 0.25,
        "fundamentals": 0.25,
        "trend": 0.15,
        "risk_reward": 0.20,
        "portfolio_fit": 0.15,
    },
    "budget": {
        "monthly_base_dca_yuan": 10000,
        "single_trade_cash_ratio_cap": 0.03,
        "weekly_cash_ratio_cap": 0.05,
        "monthly_cash_ratio_cap": 0.08,
        "bond_to_equity_monthly_cap_yuan": 30000,
        "bond_to_equity_single_cap_yuan": 15000,
        "per_symbol_cap_yuan": 12000,
    },
    "dca": {"enabled": True, "scheduled_weekday": 2, "scheduled_weeks": [1, 3]},
    "rebalance": {"no_action_deviation_pct": 0.05, "funding_only_deviation_pct": 0.08},
    "drawdown_triggers": {"mild_pct": -0.03, "medium_pct": -0.05, "severe_pct": -0.08},
}

CATEGORY_KEYS = ["美股", "港股", "A股", "债券", "黄金", "现金"]
CRITICAL_MARKET = ["VOO", "QQQ", "TLT", "GLD", "^VIX", "3067.HK", "510300.SS", "DX-Y.NYB"]
CRITICAL_MACRO = ["DGS10", "CPIAUCSL", "UNRATE", "GDP"]
SOURCE_TIERS = {
    "fred": 1,
    "treasury": 1,
    "cboe_official": 1,
    "alpha_vantage": 2,
    "finnhub": 2,
    "yfinance": 3,
    "market_data_csv": 4,
    "manual_fallback": 4,
    "unavailable": 99,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_strategy() -> dict[str, Any]:
    path = project_root() / "config" / "strategy.yaml"
    if not path.exists():
        return DEFAULT_STRATEGY
    try:
        return _deep_merge(DEFAULT_STRATEGY, load_config(path))
    except Exception as exc:  # noqa: BLE001
        write_log(f"strategy.yaml 读取失败，使用默认策略：{exc}", filename="stone_ai.log")
        return DEFAULT_STRATEGY


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_name(source: Any) -> str:
    text = str(source or "unavailable")
    if text.startswith("cache:"):
        return text.split(":", 1)[1]
    return text


def _source_tier(source: Any) -> int:
    return SOURCE_TIERS.get(_source_name(source), 99)


def _is_ok_item(item: dict[str, Any]) -> bool:
    if item.get("status") != "ok":
        return False
    value = item.get("close", item.get("value"))
    if value is None or value == "":
        return False
    return True


def _candidate_values(item: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else [item]
    return [candidate for candidate in candidates if isinstance(candidate, dict) and _is_ok_item(candidate)]


def _verified_dual_source(item: dict[str, Any], tolerance_pct: float = 1.0) -> bool:
    values = []
    sources = set()
    for candidate in _candidate_values(item):
        source = _source_name(candidate.get("source"))
        raw = candidate.get("close", candidate.get("value"))
        try:
            values.append(float(raw))
            sources.add(source)
        except (TypeError, ValueError):
            continue
    if len(sources) < 2 or len(values) < 2:
        return False
    low, high = min(values), max(values)
    if low == 0:
        return False
    return (high / low - 1) * 100 <= tolerance_pct


def _fresh(item: dict[str, Any]) -> bool:
    if item.get("cache_stale"):
        return False
    if item.get("freshness_status") == "stale":
        return False
    if item.get("status") in {"failed", "missing"}:
        return False
    return True


def _market_items(live_market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return live_market.get("items", {}) or {}


def _macro_items(live_market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return ((live_market.get("macro", {}) or {}).get("items", {}) or {})


def _metric_row(name: str, item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("close", item.get("value"))
    display = "暂无可靠数据"
    if _is_ok_item(item):
        display = f"{_to_float(value):.2f}"
    return {
        "name": name,
        "value": value if _is_ok_item(item) else None,
        "display_value": display,
        "previous": item.get("previous_close"),
        "change_pct": item.get("change_pct"),
        "timestamp": item.get("published_at") or item.get("fetched_at") or item.get("date") or "暂无数据",
        "retrieved_at": item.get("retrieved_at") or item.get("fetched_at") or "暂无数据",
        "source": item.get("source", "unavailable") if _is_ok_item(item) else "unavailable",
        "source_tier": _source_tier(item.get("source")),
        "success": _is_ok_item(item),
        "stale": not _fresh(item),
        "error": item.get("error") or item.get("warning") or ("数据拉取失败" if not _is_ok_item(item) else ""),
        "fallback_used": bool(item.get("cache_used")),
        "dual_verified": _verified_dual_source(item),
    }


def build_market_table(live_market: dict[str, Any]) -> list[dict[str, Any]]:
    items = _market_items(live_market)
    macro = _macro_items(live_market)
    rows = [_metric_row(symbol, items.get(symbol, {})) for symbol in CRITICAL_MARKET]
    rows.extend(_metric_row(series, macro.get(series, {})) for series in CRITICAL_MACRO)
    return rows


def compute_dqs(live_market: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    weights = strategy["dqs_weights"]
    items = _market_items(live_market)
    macro = _macro_items(live_market)
    market_ok = [_is_ok_item(items.get(symbol, {})) for symbol in CRITICAL_MARKET]
    macro_ok = [_is_ok_item(macro.get(series, {})) for series in CRITICAL_MACRO]
    all_rows = [items.get(symbol, {}) for symbol in CRITICAL_MARKET] + [macro.get(series, {}) for series in CRITICAL_MACRO]
    usable_rows = [item for item in all_rows if _is_ok_item(item)]
    tier1_rows = [item for item in usable_rows if _source_tier(item.get("source")) == 1]
    dual_rows = [item for item in all_rows if _verified_dual_source(item)]
    fresh_rows = [item for item in usable_rows if _fresh(item)]
    suspicious_zero = [
        name
        for name, item in {**{k: items.get(k, {}) for k in CRITICAL_MARKET}, **{k: macro.get(k, {}) for k in CRITICAL_MACRO}}.items()
        if _is_ok_item(item) and _to_float(item.get("close", item.get("value"))) == 0.0
    ]
    conflicts = (live_market.get("source_audit", {}) or {}).get("data_conflicts", []) or []

    market_score = round(sum(market_ok) / len(CRITICAL_MARKET) * weights["market_completeness"])
    macro_score = round(sum(macro_ok) / len(CRITICAL_MACRO) * weights["macro_completeness"])
    tier1_score = round((len(tier1_rows) / len(all_rows)) * weights["tier1_coverage"])
    dual_score = round((len(dual_rows) / len(all_rows)) * weights["dual_source"])
    freshness_score = round((len(fresh_rows) / len(all_rows)) * weights["freshness"]) if all_rows else 0
    consistency_score = weights["consistency"]
    if conflicts:
        consistency_score -= min(weights["consistency"], len(conflicts) * 3)
    if suspicious_zero:
        consistency_score = 0
    consistency_score = max(0, consistency_score)
    raw_score = market_score + macro_score + tier1_score + dual_score + freshness_score + consistency_score
    dual_coverage = len(dual_rows) / len(all_rows) if all_rows else 0.0

    blocking_errors = []
    if suspicious_zero:
        blocking_errors.append(f"关键数据出现异常0值：{', '.join(suspicious_zero)}")
    if conflicts:
        blocking_errors.append("关键数据存在来源冲突。")
    if not _is_ok_item(items.get("VOO", {})) or not _is_ok_item(items.get("^VIX", {})):
        blocking_errors.append("核心价格或VIX缺失。")

    capped_score = raw_score
    if dual_coverage < strategy["dqs_thresholds"]["cap_when_dual_source_below"]:
        capped_score = min(capped_score, 74)
    if blocking_errors:
        capped_score = min(capped_score, strategy["dqs_thresholds"]["severe_conflict_cap"])

    components = [
        {"item": "行情完整度", "score": market_score, "max": weights["market_completeness"], "reason": f"{sum(market_ok)}/{len(CRITICAL_MARKET)} 个核心行情可用"},
        {"item": "宏观完整度", "score": macro_score, "max": weights["macro_completeness"], "reason": f"{sum(macro_ok)}/{len(CRITICAL_MACRO)} 个核心宏观指标可用"},
        {"item": "一级来源", "score": tier1_score, "max": weights["tier1_coverage"], "reason": f"{len(tier1_rows)}/{len(all_rows)} 个关键指标来自一级来源"},
        {"item": "双源验证", "score": dual_score, "max": weights["dual_source"], "reason": f"{len(dual_rows)}/{len(all_rows)} 个关键指标通过双源验证"},
        {"item": "数据时效性", "score": freshness_score, "max": weights["freshness"], "reason": f"{len(fresh_rows)}/{len(all_rows)} 个可用指标未过期"},
        {"item": "数据一致性", "score": consistency_score, "max": weights["consistency"], "reason": "无异常0值或严重冲突" if consistency_score == weights["consistency"] else "存在冲突或异常0值"},
    ]

    if capped_score >= strategy["dqs_thresholds"]["exact_amount"]:
        mode = "exact"
        mode_label = "允许正常金额建议"
    elif capped_score >= strategy["dqs_thresholds"]["range_amount"]:
        mode = "range"
        mode_label = "只允许金额区间和分批计划"
    elif capped_score >= strategy["dqs_thresholds"]["direction_only"]:
        mode = "direction"
        mode_label = "只允许方向性建议"
    else:
        mode = "safe"
        mode_label = "禁止新增仓位建议"

    return {
        "score": int(capped_score),
        "raw_score": int(raw_score),
        "mode": mode,
        "mode_label": mode_label,
        "components": components,
        "market_coverage": sum(market_ok) / len(CRITICAL_MARKET),
        "macro_coverage": sum(macro_ok) / len(CRITICAL_MACRO),
        "tier1_coverage": len(tier1_rows) / len(all_rows) if all_rows else 0.0,
        "dual_source_coverage": dual_coverage,
        "freshness_coverage": len(fresh_rows) / len(all_rows) if all_rows else 0.0,
        "blocking_errors": blocking_errors,
        "missing_metrics": [row["name"] for row in build_market_table(live_market) if not row["success"]],
        "conflicts": conflicts,
        "suspicious_zero": suspicious_zero,
        "conclusion": "数据质量足够支持金额建议" if mode in {"exact", "range"} else "数据覆盖不足或验证不足，禁止激进建议",
    }


def _category_status(deviation_ratio: float) -> str:
    if deviation_ratio <= -0.08:
        return "严重低配"
    if deviation_ratio <= -0.05:
        return "低配"
    if deviation_ratio >= 0.08:
        return "严重超配"
    if deviation_ratio >= 0.05:
        return "超配"
    return "接近目标"


def enrich_allocation(portfolio_result: dict[str, Any], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    total = _to_float(portfolio_result.get("total_assets_wan")) * 10000
    amounts = portfolio_result.get("category_amounts", {}) or {}
    rows = []
    for category in CATEGORY_KEYS:
        current_amount = _to_float(amounts.get(category)) * 10000
        target_ratio = _to_float(strategy["target_allocation"].get(category))
        current_ratio = current_amount / total if total else 0.0
        target_amount = total * target_ratio
        deviation_ratio = current_ratio - target_ratio
        deviation_amount = current_amount - target_amount
        abs_dev = abs(deviation_ratio)
        if abs_dev > 0.08:
            priority = "高"
        elif abs_dev > 0.05:
            priority = "中"
        else:
            priority = "低"
        rows.append(
            {
                "category": category,
                "current_amount_yuan": round(current_amount),
                "current_ratio": current_ratio,
                "target_amount_yuan": round(target_amount),
                "target_ratio": target_ratio,
                "deviation_amount_yuan": round(deviation_amount),
                "deviation_ratio": deviation_ratio,
                "status": _category_status(deviation_ratio),
                "priority": priority,
            }
        )
    return sorted(rows, key=lambda row: abs(row["deviation_ratio"]), reverse=True)


def _category_amount_yuan(allocation: list[dict[str, Any]], category: str) -> float:
    for row in allocation:
        if row["category"] == category:
            return float(row["current_amount_yuan"])
    return 0.0


def _category_ratio(allocation: list[dict[str, Any]], category: str) -> float:
    for row in allocation:
        if row["category"] == category:
            return float(row["current_ratio"])
    return 0.0


def compute_risk_score(live_market: dict[str, Any], macro_result: dict[str, Any], dqs: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    weights = strategy["risk_weights"]
    items = _market_items(live_market)
    macro = _macro_items(live_market)
    vix = _to_float(items.get("^VIX", {}).get("close"), default=-1)
    dgs10 = _to_float(macro.get("DGS10", {}).get("value"), default=0)
    spx_change = _to_float(items.get("^GSPC", {}).get("change_pct"), default=0)
    nasdaq_change = _to_float(items.get("^IXIC", {}).get("change_pct"), default=0)

    valuation = 12 if dqs["market_coverage"] < 0.6 else 10
    volatility = 5 if 0 <= vix < 20 else 10 if vix < 30 else 15
    interest = 6 if dgs10 and dgs10 < 4 else 10 if dgs10 < 4.8 else 15
    liquidity = 5 if dqs["market_coverage"] >= 0.7 else 8
    macro_event = weights["macro_event"] if macro_result.get("has_high_event_next_7_days") else 5
    trend = 8 if (spx_change + nasdaq_change) < -2 else 5
    policy_geo = 6
    data_quality = round((100 - dqs["score"]) / 100 * weights["data_quality"])

    components = [
        {"item": "估值", "score": min(valuation, weights["valuation"]), "weight": weights["valuation"], "basis": "估值数据不完整时按中性偏高风险处理。"},
        {"item": "波动率", "score": min(volatility, weights["volatility"]), "weight": weights["volatility"], "basis": f"VIX={vix if vix >= 0 else '暂无可靠数据'}。"},
        {"item": "利率", "score": min(interest, weights["interest_rate"]), "weight": weights["interest_rate"], "basis": f"美国10年期收益率={dgs10 if dgs10 else '暂无可靠数据'}。"},
        {"item": "流动性", "score": min(liquidity, weights["liquidity"]), "weight": weights["liquidity"], "basis": "按数据可得性和行情覆盖估计。"},
        {"item": "宏观事件", "score": min(macro_event, weights["macro_event"]), "weight": weights["macro_event"], "basis": "未来7天存在高等级事件。" if macro_result.get("has_high_event_next_7_days") else "未来7天暂无高等级事件。"},
        {"item": "趋势", "score": min(trend, weights["trend"]), "weight": weights["trend"], "basis": f"标普和纳指当日变化合计约{spx_change + nasdaq_change:.2f}%。"},
        {"item": "政策与地缘", "score": min(policy_geo, weights["policy_geo"]), "weight": weights["policy_geo"], "basis": "按默认中性偏谨慎处理。"},
        {"item": "数据质量", "score": min(data_quality, weights["data_quality"]), "weight": weights["data_quality"], "basis": f"DQS={dqs['score']}。"},
    ]
    score = int(sum(row["score"] for row in components))
    if score <= 30:
        level = "低风险"
    elif score <= 50:
        level = "中低风险"
    elif score <= 70:
        level = "中高风险"
    elif score <= 85:
        level = "高风险"
    else:
        level = "极高风险"
    return {"score": score, "level": level, "components": components}


def _read_portfolio_csv() -> list[dict[str, Any]]:
    path = project_root() / "data" / "portfolio.csv"
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            rows.append(row)
    return rows


def _holding_amounts() -> dict[str, float]:
    amounts: dict[str, float] = {}
    for row in _read_portfolio_csv():
        name = str(row.get("name", "")).strip()
        amount = _to_float(row.get("amount_wan")) * 10000
        if name:
            amounts[name] = amount
    return amounts


def build_opportunity_scores(allocation: list[dict[str, Any]], live_market: dict[str, Any], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    items = _market_items(live_market)
    amounts = _holding_amounts()
    asset_defs = [
        ("VOO", "美股", "VOO", "核心宽基，适合长期底仓"),
        ("QQQ", "美股", "QQQ", "成长风格，波动更高"),
        ("NVDA", "美股", "NVDA", "单股科技暴露较高"),
        ("GOOG", "美股", "GOOG", "大型科技，盈利质量较高"),
        ("BABA", "美股", "BABA", "中概与港股风险相关"),
        ("IBKR", "美股", "IBKR", "金融科技和券商周期"),
        ("XLF", "美股", "XLF", "金融板块ETF"),
        ("TLT", "债券", "TLT", "长债久期风险"),
        ("沪深300ETF", "A股", "510300.SS", "A股核心宽基"),
        ("恒生科技ETF", "港股", "3067.HK", "港股成长主题"),
        ("恒生医疗ETF", "港股", "513060", "港股/中概医疗主题"),
        ("香港证券ETF", "港股", "513090", "港股券商弹性"),
        ("黄金", "黄金", "GLD", "组合防守资产"),
        ("现金", "现金", "", "流动性和等待机会"),
    ]
    rows = []
    for name, category, symbol, base_reason in asset_defs:
        cat_row = next((row for row in allocation if row["category"] == category), {})
        deviation = _to_float(cat_row.get("deviation_ratio"))
        portfolio_fit = 70
        if deviation < -0.08:
            portfolio_fit = 90
        elif deviation < -0.05:
            portfolio_fit = 80
        elif deviation > 0.08:
            portfolio_fit = 25
        elif deviation > 0.05:
            portfolio_fit = 40
        item = items.get(symbol, {}) if symbol else {}
        data_ok = _is_ok_item(item) if symbol else True
        change = _to_float(item.get("change_pct")) if data_ok else 0.0
        valuation = 60 + (8 if change < -3 else 3 if change < -1 else -5 if change > 3 else 0)
        fundamentals = 70 if name in {"VOO", "QQQ", "GOOG", "沪深300ETF"} else 60
        trend = 55 + (8 if change > 0 else -5 if change < -2 else 0)
        risk_reward = 65
        if category in {"黄金", "债券"} and deviation > 0.05:
            risk_reward -= 25
        if name == "NVDA":
            risk_reward -= 15
            portfolio_fit = min(portfolio_fit, 45)
        if not data_ok and symbol:
            valuation -= 12
            trend -= 12
            risk_reward -= 10
        weights = strategy["opportunity_weights"]
        score = round(
            valuation * weights["valuation"]
            + fundamentals * weights["fundamentals"]
            + trend * weights["trend"]
            + risk_reward * weights["risk_reward"]
            + portfolio_fit * weights["portfolio_fit"]
        )
        if score >= 80 and category in {"美股", "A股"}:
            advice = "优先加仓"
        elif score >= 70:
            advice = "正常定投"
        elif score >= 60:
            advice = "小额分批"
        elif category in {"黄金", "债券"} and deviation > 0.05:
            advice = "暂停新增"
        else:
            advice = "继续持有"
        if name == "NVDA":
            advice = "继续持有" if score >= 55 else "暂停新增"
        rows.append(
            {
                "symbol": symbol or name,
                "name": name,
                "category": category,
                "score": max(0, min(100, score)),
                "current_holding_yuan": round(amounts.get(name, 0)),
                "portfolio_fit": portfolio_fit,
                "advice": advice,
                "reason": f"{base_reason}；{category}当前偏离{deviation * 100:.1f}个百分点；数据状态：{'可用' if data_ok else '暂无可靠行情'}。",
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _week_of_month(day: date) -> int:
    return (day.day - 1) // 7 + 1


def _scheduled_weeks(strategy: dict[str, Any]) -> list[int]:
    raw = strategy["dca"].get("scheduled_weeks") or [1, 3]
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        values = [part.strip() for part in raw.strip("[]").split(",")]
    else:
        values = [raw]
    weeks: list[int] = []
    for value in values:
        try:
            weeks.append(int(value))
        except (TypeError, ValueError):
            continue
    return weeks or [1, 3]


def _next_dca_date(day: date, strategy: dict[str, Any]) -> date:
    weekday = int(strategy["dca"].get("scheduled_weekday", 2))
    weeks = _scheduled_weeks(strategy)
    for offset in range(0, 45):
        candidate = day + timedelta(days=offset)
        if candidate.weekday() == weekday and _week_of_month(candidate) in weeks:
            return candidate
    return day


def build_budget_plan(allocation: list[dict[str, Any]], dqs: dict[str, Any], risk: dict[str, Any], macro_result: dict[str, Any], opportunity: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
    total_yuan = sum(row["current_amount_yuan"] for row in allocation)
    cash_yuan = _category_amount_yuan(allocation, "现金")
    bond_yuan = _category_amount_yuan(allocation, "债券")
    bond_target_yuan = next(row["target_amount_yuan"] for row in allocation if row["category"] == "债券")
    cash_floor_yuan = max(total_yuan * strategy["cash"]["safety_ratio"], total_yuan * strategy["cash"]["hard_floor_ratio"])
    confirmed_cash_available = max(0.0, cash_yuan - cash_floor_yuan)
    today = date.today()
    is_dca_day = strategy["dca"].get("enabled", True) and today.weekday() == int(strategy["dca"].get("scheduled_weekday", 2)) and _week_of_month(today) in _scheduled_weeks(strategy)
    next_dca = _next_dca_date(today + timedelta(days=1), strategy)
    bond_excess = max(0.0, bond_yuan - bond_target_yuan)
    bond_month_cap = min(float(strategy["budget"]["bond_to_equity_monthly_cap_yuan"]), bond_excess)
    dqs_allows_amount = dqs["mode"] in {"exact", "range"}
    high_event = bool(macro_result.get("has_high_event_next_7_days"))

    base_amount = float(strategy["budget"]["monthly_base_dca_yuan"]) / 2 if is_dca_day else 0.0
    if not dqs_allows_amount or confirmed_cash_available <= 0 or high_event:
        base_amount = 0.0
    base_amount = min(base_amount, confirmed_cash_available)

    opportunity_amount = 0.0
    if dqs["mode"] == "exact" and risk["score"] <= 70 and not high_event and confirmed_cash_available > base_amount:
        top_score = opportunity[0]["score"] if opportunity else 0
        if top_score >= 82:
            opportunity_amount = min(confirmed_cash_available - base_amount, total_yuan * strategy["budget"]["single_trade_cash_ratio_cap"])

    rebalance_today = 0.0
    if confirmed_cash_available > base_amount + opportunity_amount:
        rebalance_today = 0.0

    total_today = base_amount + opportunity_amount + rebalance_today
    confirmed_week = total_today
    confirmed_month = total_today
    conditional_month = bond_month_cap if dqs["score"] >= 60 else 0.0

    top_targets = [row for row in opportunity if row["advice"] in {"优先加仓", "正常定投", "小额分批"}][:3]
    target_text = "、".join(row["name"] for row in top_targets) or "暂无"
    rows = [
        {
            "type": "基础定投",
            "execute": bool(base_amount > 0),
            "amount_yuan": round(base_amount),
            "targets": target_text if base_amount > 0 else "不适用",
            "funding_source": "现金安全线以上资金" if base_amount > 0 else "未使用资金",
            "reason": "今日是基础定投日且DQS允许金额。" if base_amount > 0 else ("今日不是基础定投执行日" if not is_dca_day else "现金不足、重大事件或DQS限制。"),
        },
        {
            "type": "机会加仓",
            "execute": bool(opportunity_amount > 0),
            "amount_yuan": round(opportunity_amount),
            "targets": target_text if opportunity_amount > 0 else "不适用",
            "funding_source": "现金安全线以上资金" if opportunity_amount > 0 else "未使用资金",
            "reason": "高分机会且风险未超限。" if opportunity_amount > 0 else "未达到机会加仓条件或DQS/事件/现金约束不允许。",
        },
        {
            "type": "再平衡",
            "execute": False,
            "amount_yuan": 0,
            "targets": "VOO/QQQ、沪深300ETF、恒生科技ETF",
            "funding_source": "待债券到期或赎回到账",
            "reason": "债券超配，但未到账资金不得视作可用现金。",
        },
        {
            "type": "风险减仓",
            "execute": False,
            "amount_yuan": 0,
            "targets": "不适用",
            "funding_source": "不适用",
            "reason": "当前未触发必须减仓规则。",
        },
    ]
    return {
        "cash_yuan": round(cash_yuan),
        "cash_floor_yuan": round(cash_floor_yuan),
        "confirmed_cash_available_yuan": round(confirmed_cash_available),
        "today_total_yuan": round(total_today),
        "week_confirmed_yuan": round(confirmed_week),
        "month_confirmed_yuan": round(confirmed_month),
        "conditional_bond_to_equity_month_yuan": round(conditional_month),
        "bond_excess_yuan": round(bond_excess),
        "is_dca_day": bool(is_dca_day),
        "next_dca_date": next_dca.isoformat(),
        "rows": rows,
        "funding_note": "未到账债券赎回资金只列为条件性计划，不计入今日/本周可用现金。",
    }


def build_migration_plan(allocation: list[dict[str, Any]], budget: dict[str, Any]) -> dict[str, Any]:
    bond_row = next(row for row in allocation if row["category"] == "债券")
    current = bond_row["current_amount_yuan"]
    target = bond_row["target_amount_yuan"]
    transfer_needed = max(0, current - target)
    monthly_cap = max(0, budget["conditional_bond_to_equity_month_yuan"])
    months = []
    remaining = transfer_needed
    for index in range(1, 13):
        planned = min(monthly_cap, remaining) if monthly_cap else 0
        remaining -= planned
        months.append(
            {
                "month": index,
                "planned_transfer_yuan": round(planned),
                "remaining_excess_yuan": round(max(0, remaining)),
                "review": "季度复核" if index in {3, 6, 9, 12} else "月度跟踪",
            }
        )
    return {
        "current_bond_yuan": round(current),
        "target_bond_yuan": round(target),
        "theoretical_transfer_yuan": round(transfer_needed),
        "monthly_cap_yuan": round(monthly_cap),
        "quarterly_reviews": ["第3个月", "第6个月", "第9个月", "第12个月"],
        "pause_conditions": ["DQS低于60", "现金低于安全线", "VIX高于30", "重大宏观事件前后", "债券赎回资金未到账"],
        "accelerate_conditions": ["DQS不低于85", "权益资产明显回撤且长期逻辑未变", "债券资金已到账", "现金仍高于安全线"],
        "priority_targets": ["VOO/QQQ", "沪深300ETF", "恒生科技ETF小额分批"],
        "months": months,
    }


def build_holding_diagnostics(live_market: dict[str, Any], allocation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    total_yuan = sum(row["current_amount_yuan"] for row in allocation)
    items = _market_items(live_market)
    symbol_map = {
        "VOO": "VOO",
        "NVDA": "NVDA",
        "GOOG": "GOOG",
        "TLT": "TLT",
        "IBKR": "IBKR",
        "XLF": "XLF",
        "BABA": "BABA",
        "沪深300ETF 510300": "510300.SS",
        "南方恒生科技ETF 03033": "3067.HK",
        "恒生医疗ETF 513060": "513060",
        "香港证券ETF 513090": "513090",
        "黄金ETF 518880": "GLD",
    }
    for row in _read_portfolio_csv():
        name = str(row.get("name", "")).strip()
        category = str(row.get("category", "")).strip()
        amount_yuan = round(_to_float(row.get("amount_wan")) * 10000)
        symbol = symbol_map.get(name, name)
        item = items.get(symbol, {})
        market_state = "暂无可靠行情" if symbol and not _is_ok_item(item) else "行情可用"
        category_ratio = _category_ratio(allocation, category)
        risk = "主题或单一资产波动" if category in {"美股", "港股", "A股"} and name not in {"VOO", "沪深300ETF 510300"} else "组合层面风险可控"
        advice = "继续持有"
        if category in {"债券", "黄金"} and category_ratio > 0.15:
            advice = "暂停新增"
        if "NVDA" in name:
            advice = "继续持有，暂停追高"
        rows.append(
            {
                "name": name,
                "category": category,
                "amount_yuan": amount_yuan,
                "portfolio_ratio": amount_yuan / total_yuan if total_yuan else 0.0,
                "quantity": row.get("quantity") or "不适用",
                "fundamental_status": "长期逻辑未单独异常；需结合最新财报复核" if category in {"美股", "港股", "A股"} else "防守或流动性资产",
                "trend_status": market_state,
                "risk": risk,
                "overlap": "与权益Beta相关" if category in {"美股", "港股", "A股"} else "与权益相关性较低",
                "advice": advice,
                "add_condition": "DQS>=85且资产仍低配，或到计划定投日",
                "reduce_condition": "基本面恶化、仓位过度集中、或组合风控触发",
            }
        )
    return rows


def build_scenarios(budget: dict[str, Any], opportunity: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    targets = "、".join(row["name"] for row in opportunity[:3]) if opportunity else "VOO/QQQ、沪深300ETF"
    conditional_cap = budget["conditional_bond_to_equity_month_yuan"]
    return [
        {
            "scenario": "市场平稳",
            "trigger": "主要指数波动未触发回撤阈值，VIX低于20或维持正常区间",
            "action": "基础定投按计划日执行；非计划日不交易。",
            "amount": f"确认现金买入上限 {budget['month_confirmed_yuan']} 元；债券到账后条件性上限 {conditional_cap} 元。",
            "targets": targets,
        },
        {
            "scenario": "指数回撤",
            "trigger": "回撤约3%观察，约5%小额分批，约8%及以上才考虑机会加仓",
            "action": "只有DQS>=85、长期逻辑未破坏、且资金来源确认时，才启用机会加仓。",
            "amount": f"单次不超过 {int(strategy['budget']['bond_to_equity_single_cap_yuan'])} 元，且不突破月度条件性上限。",
            "targets": targets,
        },
        {
            "scenario": "市场快速上涨",
            "trigger": "指数快速上涨但无回撤，估值吸引力下降",
            "action": "不追高；基础定投继续按计划日执行，机会加仓暂停。",
            "amount": "0元机会加仓；等待下一次计划定投或回撤触发。",
            "targets": "核心ETF继续观察",
        },
    ]


def build_next_triggers(budget: dict[str, Any], dqs: dict[str, Any]) -> list[str]:
    return [
        f"下一个基础定投复核日：{budget['next_dca_date']}；若DQS>=75且现金高于安全线，可按计划执行。",
        "若债券到期或赎回资金到账，本月可在条件性额度内分2-3次转向权益ETF。",
        "若主要指数回撤约5%且DQS>=85，优先评估VOO/QQQ和沪深300ETF小额分批。",
        "若DQS低于60或关键价格缺失，继续禁止新增仓位建议。",
    ]


def build_ai_mode(ai_advice: dict[str, Any], dqs: dict[str, Any]) -> dict[str, Any]:
    if dqs["score"] < 60:
        mode = "SAFE_MODE"
    elif ai_advice.get("ai_status") == "available" and dqs["score"] >= 85:
        mode = "AI_FULL"
    elif ai_advice.get("ai_status") == "available":
        mode = "AI_PARTIAL"
    else:
        mode = "RULE_ENHANCED"
    return {
        "mode": mode,
        "provider": ai_advice.get("actual_provider", "rule_only"),
        "fallback_reason": ai_advice.get("fallback_reason", ""),
        "retry_count": ai_advice.get("retry_count", 0),
        "impact": "AI仅解释，不覆盖DQS、资金预算和风控硬门槛。",
        "summary": ai_advice.get("summary", "AI不可用，使用规则增强模式。"),
    }


def build_consistency_checks(decision: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    allocation_sum = sum(row["current_ratio"] for row in decision["allocation"])
    budget = decision["budget"]
    dqs = decision["dqs"]
    if abs(allocation_sum - 1.0) > 0.01:
        errors.append(f"资产占比合计为{allocation_sum:.2%}，不接近100%。")
    if budget["today_total_yuan"] > budget["week_confirmed_yuan"]:
        errors.append("今日金额大于本周额度。")
    if budget["week_confirmed_yuan"] > budget["month_confirmed_yuan"]:
        errors.append("本周额度大于本月额度。")
    if budget["confirmed_cash_available_yuan"] <= 0 and budget["today_total_yuan"] > 0:
        errors.append("现金低于安全线却安排了今日买入。")
    if dqs["mode"] in {"direction", "safe"} and budget["today_total_yuan"] > 0:
        errors.append("建议违反DQS金额门槛。")
    empty_status = [row["category"] for row in decision["allocation"] if not row.get("status")]
    if empty_status:
        errors.append(f"资产配置状态为空：{', '.join(empty_status)}")
    if dqs["suspicious_zero"]:
        errors.append("存在价格0.00异常。")
    if decision["macro_event_high_next_7_days"] and decision["budget"]["today_total_yuan"] > 0:
        warnings.append("重大事件前仍有买入计划，需人工复核。")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "checked_at": datetime.now().isoformat(timespec="seconds")}


def build_v12_1_decision(
    *,
    portfolio_result: dict[str, Any],
    live_market_result: dict[str, Any],
    macro_result: dict[str, Any],
    ai_advice_result: dict[str, Any],
) -> dict[str, Any]:
    strategy = load_strategy()
    allocation = enrich_allocation(portfolio_result, strategy)
    dqs = compute_dqs(live_market_result, strategy)
    risk = compute_risk_score(live_market_result, macro_result, dqs, strategy)
    opportunity = build_opportunity_scores(allocation, live_market_result, strategy)
    budget = build_budget_plan(allocation, dqs, risk, macro_result, opportunity, strategy)
    migration = build_migration_plan(allocation, budget)
    holding_diagnostics = build_holding_diagnostics(live_market_result, allocation)
    scenarios = build_scenarios(budget, opportunity, strategy)
    ai_mode = build_ai_mode(ai_advice_result, dqs)
    market_table = build_market_table(live_market_result)
    total_yuan = sum(row["current_amount_yuan"] for row in allocation)
    today_trade = budget["today_total_yuan"] > 0

    no_trade_reasons = []
    if not budget["is_dca_day"]:
        no_trade_reasons.append("今日不是基础定投执行日")
    if dqs["mode"] in {"direction", "safe"}:
        no_trade_reasons.append(f"DQS={dqs['score']}，{dqs['mode_label']}")
    if budget["confirmed_cash_available_yuan"] <= 0:
        no_trade_reasons.append("现金低于或接近安全线")
    if macro_result.get("has_high_event_next_7_days"):
        no_trade_reasons.append("未来7天存在高等级宏观事件，事件前不追涨")
    if not no_trade_reasons and not today_trade:
        no_trade_reasons.append("未触发机会加仓或再平衡执行条件")

    decision = {
        "version": VERSION_NAME,
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_cutoff": live_market_result.get("fetched_at") or datetime.now().isoformat(timespec="seconds"),
        "trading_day_status": "周末/非交易时段需以下一交易日为准" if date.today().weekday() >= 5 else "交易日",
        "portfolio_value_yuan": round(total_yuan),
        "portfolio_value_wan": round(total_yuan / 10000, 2),
        "allocation": allocation,
        "dqs": dqs,
        "risk": risk,
        "opportunity": opportunity,
        "budget": budget,
        "migration_plan": migration,
        "holding_diagnostics": holding_diagnostics,
        "scenarios": scenarios,
        "market_table": market_table,
        "ai": ai_mode,
        "macro_event_high_next_7_days": bool(macro_result.get("has_high_event_next_7_days")),
        "events": macro_result.get("upcoming_events", []) or [],
        "today_trade": today_trade,
        "trade_type": "无操作" if not today_trade else "基础定投/机会加仓/再平衡",
        "today_amount_yuan": budget["today_total_yuan"],
        "targets": "、".join(row["name"] for row in opportunity[:3]) if today_trade and opportunity else "不适用",
        "funding_source": "现金安全线以上资金" if today_trade else "今日不使用资金",
        "no_trade_reasons": no_trade_reasons,
        "next_triggers": build_next_triggers(budget, dqs),
        "next_review_date": budget["next_dca_date"],
        "max_risk": risk["components"][0]["basis"] if risk["components"] else "暂无",
        "max_opportunity": opportunity[0]["reason"] if opportunity else "暂无",
        "one_sentence": "；".join(no_trade_reasons) + "；待资金和数据条件满足后再执行分批计划。",
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不自动交易，不接券商下单权限，不承诺收益。",
    }
    decision["consistency"] = build_consistency_checks(decision)
    write_log(f"V12.2 决策生成完成：DQS={dqs['score']} risk={risk['score']} today={budget['today_total_yuan']}", filename="stone_ai.log")
    return decision


def build_system_audit_text(context: dict[str, Any], decision: dict[str, Any]) -> str:
    live = context.get("live_market_result", {})
    quality = live.get("data_quality", {}) or {}
    market_result = context.get("market_result", {}) or {}
    execution = context.get("execution_plan_result", {}) or {}
    lines = [
        "# Stone AI V12.2 Smart Grid System Audit",
        "",
        f"- 审计时间：{datetime.now().isoformat(timespec='seconds')}",
        "- 当前实际运行入口：根目录 `main.py`（V12.2要求）。",
        "- GitHub Actions 应调用：`python main.py`。",
        "- 报告生成模块：`src/reports/report_center.py`。",
        "- 决策核心模块：`src/decision/v12_1_decision.py`。",
        "",
        "## 数据源接入状态",
        "",
        "- 已接入代码路径：FRED、Alpha Vantage、Finnhub、Cboe VIX、yfinance、本地缓存。",
        "- 是否真正成功使用以 `reports/daily_report.md` 的数据来源章节为准，未成功请求的来源不会被列为成功来源。",
        "",
        "## 当前旧报告问题原因",
        "",
        f"- 美股/A股/港股/黄金显示0.00：旧市场摘要使用 `market_data.csv` 默认变化值，缺失行情没有区分失败和真实0；V12.1已改为“暂无可靠数据/请求失败/缓存”。",
        f"- 双源验证覆盖率：旧路由拿到第一个成功源就返回，导致候选源不足；V12.1已收集 candidates 后再验证。",
        f"- 一级来源覆盖率：取决于本次实际成功来源，不再把配置占位算作成功。",
        f"- AI状态：{decision['ai']['mode']}，原因：{decision['ai'].get('fallback_reason') or 'OpenAI可用性和DQS共同决定'}。",
        f"- 本周0元、本月金额、债券转权益冲突：旧逻辑把现金预算和未到账债券资金混用；V12.1已拆成确认现金计划和条件性债券到账计划。",
        f"- 基础定投无金额：旧逻辑只写继续/暂停；V12.1新增计划日、金额、资金来源和不执行原因。",
        f"- 风险评分明细：旧评分来自 MarketAgent 汇总值 {market_result.get('market_risk_score', '暂无')}；V12.1已输出八项风险分解。",
        "",
        "## 关键运行快照",
        "",
        f"- 旧数据质量分：{quality.get('score', '暂无')}",
        f"- 旧执行计划：today={execution.get('today_buy_wan', '暂无')}万 week={execution.get('week_buy_wan', '暂无')}万 month={execution.get('month_buy_wan', '暂无')}万",
        f"- 新DQS：{decision['dqs']['score']} / {decision['dqs']['mode_label']}",
        f"- 新风险评分：{decision['risk']['score']} / {decision['risk']['level']}",
    ]
    return "\n".join(lines)
