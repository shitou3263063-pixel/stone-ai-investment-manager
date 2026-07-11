from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.portfolio_snapshot import build_portfolio_snapshot
from utils.data_loader import load_config, project_root
from utils.logger import write_log


VERSION_NAME = "Stone AI Investment Manager Pro V12.5 Stable"

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


def _portfolio_snapshot() -> dict[str, Any]:
    try:
        return build_portfolio_snapshot()
    except Exception as exc:  # noqa: BLE001
        write_log(f"Portfolio Snapshot 读取失败：{exc}", filename="stone_ai.log")
        return {"holdings": [], "cash": {}, "gold": {}, "total_assets": 0, "asset_class_totals": {}}


def _snapshot_holdings() -> list[dict[str, Any]]:
    return list(_portfolio_snapshot().get("holdings", []) or [])


def _holding_amounts() -> dict[str, float]:
    amounts: dict[str, float] = {}
    for row in _snapshot_holdings():
        amount = _to_float(row.get("market_value_cny"))
        for key in [
            row.get("security_name"),
            row.get("security_code"),
            row.get("canonical_id"),
            row.get("pricing_proxy"),
        ]:
            text = str(key or "").strip()
            if text:
                amounts[text] = amount
        if row.get("asset_class") in {"黄金", "现金"}:
            amounts[row["asset_class"]] = amounts.get(row["asset_class"], 0.0) + amount
    return amounts


def build_opportunity_scores(allocation: list[dict[str, Any]], live_market: dict[str, Any], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    items = _market_items(live_market)
    amounts = _holding_amounts()
    asset_defs = [
        {"name": "VOO", "category": "美股", "symbol": "VOO", "holding_key": "VOO", "type": "core_etf", "reason": "核心宽基ETF，优先用于修复美股低配"},
        {"name": "QQQ", "category": "美股", "symbol": "QQQ", "holding_key": "QQQ", "type": "growth_etf", "reason": "成长宽基ETF，需控制估值和波动"},
        {"name": "NVDA", "category": "美股", "symbol": "NVDA", "holding_key": "NVDA", "type": "single_stock", "reason": "单股科技暴露较高，不因美股低配自动加仓"},
        {"name": "GOOG", "category": "美股", "symbol": "GOOG", "holding_key": "GOOG", "type": "single_stock", "reason": "大型科技股，需等待估值和财报确认"},
        {"name": "BABA", "category": "美股", "symbol": "BABA", "holding_key": "BABA", "type": "single_stock", "reason": "中概股风险与港股风险相关，不机械补仓"},
        {"name": "IBKR", "category": "美股", "symbol": "IBKR", "holding_key": "IBKR", "type": "single_stock", "reason": "券商周期股，需基本面和估值同时满足"},
        {"name": "XLF", "category": "美股", "symbol": "XLF", "holding_key": "XLF", "type": "sector_etf", "reason": "行业ETF，不优先于核心宽基"},
        {"name": "TLT", "category": "债券", "symbol": "TLT", "holding_key": "TLT", "type": "duration_bond_etf", "reason": "美国长期国债ETF，高久期利率资产"},
        {"name": "沪深300ETF", "category": "A股", "symbol": "510300.SS", "holding_key": "510300", "type": "core_etf", "reason": "A股核心宽基ETF"},
        {"name": "恒生科技ETF", "category": "港股", "symbol": "3067.HK", "holding_key": "03033", "type": "core_etf", "reason": "港股成长宽基，03033为实际持仓，3067.HK仅作行情代理"},
        {"name": "恒生医疗ETF", "category": "港股", "symbol": "513060.SS", "holding_key": "513060", "type": "thematic_etf", "reason": "主题ETF，只适合小额观察"},
        {"name": "香港证券ETF", "category": "港股", "symbol": "513090.SS", "holding_key": "513090", "type": "thematic_etf", "reason": "高弹性主题ETF，不作为优先补仓资产"},
        {"name": "黄金", "category": "黄金", "symbol": "GLD", "holding_key": "黄金", "type": "defensive_gold", "reason": "组合防守资产，当前超配时暂停新增"},
        {"name": "现金", "category": "现金", "symbol": "", "holding_key": "现金", "type": "cash", "reason": "流动性和安全垫"},
    ]
    rows = []
    weights = {
        "valuation": 25,
        "trend": 15,
        "fundamentals": 15,
        "macro": 10,
        "flow": 10,
        "portfolio_fit": 20,
        "data_confidence": 5,
    }
    for asset in asset_defs:
        name = asset["name"]
        category = asset["category"]
        symbol = asset["symbol"]
        asset_type = asset["type"]
        cat_row = next((row for row in allocation if row["category"] == category), {})
        deviation = _to_float(cat_row.get("deviation_ratio"))
        portfolio_fit = 65
        if deviation < -0.08:
            portfolio_fit = 90
        elif deviation < -0.05:
            portfolio_fit = 80
        elif deviation > 0.08:
            portfolio_fit = 15
        elif deviation > 0.05:
            portfolio_fit = 30
        item = items.get(symbol, {}) if symbol else {}
        data_ok = _is_ok_item(item) if symbol else True
        change = _to_float(item.get("change_pct")) if data_ok else 0.0

        valuation = 60 + (8 if change < -3 else 4 if change < -1 else -8 if change > 3 else 0)
        fundamentals = 72 if asset_type in {"core_etf", "growth_etf"} else 65 if asset_type in {"sector_etf", "thematic_etf"} else 58
        trend = 55 + (8 if change > 0 else -8 if change < -2 else 0)
        macro = 60
        flow = 55
        data_confidence = 80 if data_ok else 35

        if asset_type == "single_stock":
            portfolio_fit = min(portfolio_fit, 45)
            macro -= 5
            flow -= 5
        if asset_type == "duration_bond_etf":
            portfolio_fit = min(portfolio_fit, 35)
            fundamentals = 55
            macro = 45
        if category in {"黄金", "债券"} and deviation > 0:
            portfolio_fit = min(portfolio_fit, 25)
            valuation -= 5
        if category == "现金" and deviation < 0:
            portfolio_fit = 90
        if not data_ok and symbol:
            valuation -= 12
            trend -= 12
            macro -= 5
            flow -= 5

        component_scores = {
            "估值吸引力": max(0, min(100, valuation)),
            "趋势与市场宽度": max(0, min(100, trend)),
            "基本面或盈利质量": max(0, min(100, fundamentals)),
            "宏观环境适配": max(0, min(100, macro)),
            "资金流或成交结构": max(0, min(100, flow)),
            "组合适配度": max(0, min(100, portfolio_fit)),
            "数据置信度": max(0, min(100, data_confidence)),
        }
        raw_score = round(
            component_scores["估值吸引力"] * weights["valuation"] / 100
            + component_scores["趋势与市场宽度"] * weights["trend"] / 100
            + component_scores["基本面或盈利质量"] * weights["fundamentals"] / 100
            + component_scores["宏观环境适配"] * weights["macro"] / 100
            + component_scores["资金流或成交结构"] * weights["flow"] / 100
            + component_scores["组合适配度"] * weights["portfolio_fit"] / 100
            + component_scores["数据置信度"] * weights["data_confidence"] / 100
        )
        data_adjustment = 0 if data_ok or not symbol else -10
        score = max(0, min(100, raw_score + data_adjustment))

        limitations: list[str] = []
        if category in {"黄金", "债券"} and deviation > 0:
            limitations.append("资产类别已高于目标，占比修复优先于市场机会")
        if asset_type == "single_stock":
            limitations.append("个股不得仅因资产类别低配而加仓")
        if not data_ok and symbol:
            limitations.append("行情数据不足，仅供观察")

        if category in {"黄金", "债券"} and deviation > 0:
            advice = "暂停新增"
        elif asset_type == "duration_bond_etf":
            advice = "继续持有，暂停新增"
        elif asset_type == "single_stock":
            advice = "继续持有" if data_ok else "观察"
        elif category == "现金":
            advice = "维持现金安全垫" if deviation >= -0.03 else "优先补现金"
        elif score >= 78 and asset_type in {"core_etf", "growth_etf"} and deviation < -0.05:
            advice = "正常定投"
        elif score >= 68 and asset_type in {"core_etf", "growth_etf"} and deviation < -0.03:
            advice = "小额分批"
        else:
            advice = "继续持有"
        if asset_type in {"sector_etf", "thematic_etf"} and advice in {"正常定投", "小额分批"}:
            advice = "观察"

        holding_amount = round(amounts.get(asset["holding_key"], amounts.get(name, amounts.get(symbol, 0))))
        rows.append(
            {
                "symbol": symbol or name,
                "name": name,
                "category": category,
                "score": score,
                "raw_score": raw_score,
                "data_quality_adjustment": data_adjustment,
                "components": component_scores,
                "weights": weights,
                "current_holding_yuan": holding_amount,
                "portfolio_fit": portfolio_fit,
                "advice": advice,
                "limitations": limitations or ["无硬性限制"],
                "reason": f"{asset['reason']}；{category}当前偏离{deviation * 100:.1f}个百分点；数据状态：{'可用' if data_ok else '暂无可靠行情'}。",
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
    snapshot = _portfolio_snapshot()
    cash_detail = snapshot.get("cash", {}) or {}
    cash_yuan = _to_float(cash_detail.get("account_total_cash_cny"), _category_amount_yuan(allocation, "现金"))
    bond_yuan = _category_amount_yuan(allocation, "债券")
    bond_target_yuan = next(row["target_amount_yuan"] for row in allocation if row["category"] == "债券")
    cash_floor_yuan = _to_float(cash_detail.get("cash_safety_reserve_cny")) or max(total_yuan * strategy["cash"]["safety_ratio"], total_yuan * strategy["cash"]["hard_floor_ratio"])
    live_grid_cash_yuan = _to_float(cash_detail.get("live_grid_cash_cny"))
    reserved_cash_yuan = _to_float(cash_detail.get("other_reserved_cash_cny"))
    confirmed_cash_available = max(0.0, cash_yuan - cash_floor_yuan - live_grid_cash_yuan - reserved_cash_yuan)
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
    bond_cash_arrived = _to_float(cash_detail.get("unsettled_conditional_cash_cny")) == 0 and False
    conditional_month = bond_month_cap
    approved_bond_to_equity = conditional_month if dqs["score"] >= 60 and bond_cash_arrived else 0.0

    top_targets = [row for row in opportunity if row["advice"] in {"优先加仓", "正常定投", "小额分批"}][:3]
    target_text = "、".join(row["name"] for row in top_targets) or "暂无"
    rows = [
        {
            "budget_id": "BUDGET_BASE_DCA",
            "type": "基础定投",
            "execute": bool(base_amount > 0),
            "amount_yuan": round(base_amount),
            "targets": target_text if base_amount > 0 else "不适用",
            "funding_source": "现金安全线以上资金" if base_amount > 0 else "未使用资金",
            "reason": "今日是基础定投日且DQS允许金额。" if base_amount > 0 else ("今日不是基础定投执行日" if not is_dca_day else "现金不足、重大事件或DQS限制。"),
        },
        {
            "budget_id": "BUDGET_OPPORTUNITY_ADD",
            "type": "机会加仓",
            "execute": bool(opportunity_amount > 0),
            "amount_yuan": round(opportunity_amount),
            "targets": target_text if opportunity_amount > 0 else "不适用",
            "funding_source": "现金安全线以上资金" if opportunity_amount > 0 else "未使用资金",
            "reason": "高分机会且风险未超限。" if opportunity_amount > 0 else "未达到机会加仓条件或DQS/事件/现金约束不允许。",
        },
        {
            "budget_id": "BUDGET_CONDITIONAL_BOND_TO_EQUITY",
            "type": "再平衡",
            "execute": False,
            "amount_yuan": 0,
            "targets": "VOO/QQQ、沪深300ETF、恒生科技ETF",
            "funding_source": "待债券到期或赎回到账",
            "reason": "债券超配，但未到账资金不得视作可用现金。",
        },
        {
            "budget_id": "BUDGET_RISK_REDUCTION",
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
        "account_total_cash_yuan": round(cash_yuan),
        "cash_floor_yuan": round(cash_floor_yuan),
        "cash_safety_reserve_yuan": round(cash_floor_yuan),
        "live_grid_cash_yuan": round(live_grid_cash_yuan),
        "paper_grid_cash_yuan": 0,
        "other_reserved_cash_yuan": round(reserved_cash_yuan),
        "confirmed_cash_available_yuan": round(confirmed_cash_available),
        "investable_cash_yuan": round(confirmed_cash_available),
        "today_total_yuan": round(total_today),
        "week_confirmed_yuan": round(confirmed_week),
        "month_confirmed_yuan": round(confirmed_month),
        "conditional_bond_to_equity_month_yuan": round(conditional_month),
        "approved_bond_to_equity_month_yuan": round(approved_bond_to_equity),
        "actual_bond_cash_arrived_yuan": 0,
        "bond_to_equity_executed_this_month_yuan": 0,
        "bond_to_equity_remaining_this_month_yuan": round(approved_bond_to_equity),
        "bond_excess_yuan": round(bond_excess),
        "is_dca_day": bool(is_dca_day),
        "next_dca_date": next_dca.isoformat(),
        "rows": rows,
        "funding_note": "未到账债券赎回资金只列为条件性计划，不计入今日/本周可用现金；网格模拟现金不计入真实资产。",
        "cash_formula": "可投资现金 = 账户总现金 - 现金安全储备 - 网格实盘现金 - 其他已占用现金",
    }


def build_migration_plan(allocation: list[dict[str, Any]], budget: dict[str, Any]) -> dict[str, Any]:
    bond_row = next(row for row in allocation if row["category"] == "债券")
    current = bond_row["current_amount_yuan"]
    target = bond_row["target_amount_yuan"]
    transfer_needed = max(0, current - target)
    monthly_cap = max(0, budget["conditional_bond_to_equity_month_yuan"])
    approved_this_month = max(0, budget.get("approved_bond_to_equity_month_yuan", 0))
    actual_arrived = max(0, budget.get("actual_bond_cash_arrived_yuan", 0))
    executed_this_month = max(0, budget.get("bond_to_equity_executed_this_month_yuan", 0))
    remaining_this_month = max(0, approved_this_month - executed_this_month)
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
        "approved_this_month_yuan": round(approved_this_month),
        "actual_arrived_yuan": round(actual_arrived),
        "executed_this_month_yuan": round(executed_this_month),
        "remaining_this_month_yuan": round(remaining_this_month),
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
    for row in _snapshot_holdings():
        name = str(row.get("security_name", "")).strip()
        category = str(row.get("asset_class", "")).strip()
        amount_yuan = round(_to_float(row.get("market_value_cny")))
        symbol = str(row.get("pricing_proxy") or row.get("security_code") or "").strip()
        item = items.get(symbol, {})
        market_state = "暂无可靠行情" if symbol and not _is_ok_item(item) else "行情可用"
        category_ratio = _category_ratio(allocation, category)
        bucket = str(row.get("strategy_bucket", ""))
        risk = "主题或单一资产波动" if category in {"美股", "港股", "A股"} and bucket.startswith("single_stock") else "组合层面风险可控"
        advice = "继续持有"
        overlap = "与权益Beta相关" if category in {"美股", "港股", "A股"} else "与权益相关性较低"
        add_condition = "DQS>=85且资产仍低配，或到计划定投日"
        reduce_condition = "基本面恶化、仓位过度集中、或组合风控触发"
        fundamental_status = "长期逻辑未单独异常；需结合最新财报复核" if category in {"美股", "港股", "A股"} else "防守或流动性资产"

        if category == "黄金":
            advice = "继续持有，暂停新增"
            risk = "黄金已高于目标，实物金条流动性弱，避免追高"
            overlap = "组合防守资产，与权益相关性较低"
            add_condition = "仅当黄金回落至目标附近且组合需要防守时再评估"
            reduce_condition = "黄金显著超配且避险趋势转弱时，优先评估黄金ETF而非金条"
        if category == "债券":
            advice = "继续持有，暂停新增"
            risk = "债券总仓位超配，新增资金优先修复权益低配"
            add_condition = "债券总仓位回到目标附近后再评估"
            reduce_condition = "债券到期或赎回到账后，按路线图分批转权益ETF"
        if row.get("security_code") == "TLT":
            advice = "继续持有，暂停新增，关注久期风险"
            risk = "美国长期国债ETF，高久期利率资产；受美国长端利率、通胀、期限溢价和美元影响"
            overlap = "与中国债券同属利率/债券风险暴露，组合债券已明显超配"
            add_condition = "债券总仓位下降且出现明确战术利率配置理由"
            reduce_condition = "美国长端利率继续上行、期限溢价抬升或债券仓位需要压降"
        if "NVDA" in name:
            advice = "继续持有，暂停追高"
        if bucket == "single_stock_high_risk":
            advice = "观察，暂停新增"
            risk = "ST或高风险个股，必须人工复核"
        rows.append(
            {
                "name": name,
                "category": category,
                "amount_yuan": amount_yuan,
                "portfolio_ratio": amount_yuan / total_yuan if total_yuan else 0.0,
                "quantity": row.get("quantity") or "不适用",
                "fundamental_status": fundamental_status,
                "trend_status": market_state,
                "risk": risk,
                "overlap": overlap,
                "advice": advice,
                "add_condition": add_condition,
                "reduce_condition": reduce_condition,
            }
        )
    return rows


def build_scenarios(budget: dict[str, Any], opportunity: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    actionable = [row for row in opportunity if row.get("advice") in {"优先加仓", "正常定投", "小额分批"}]
    targets = "、".join(row["name"] for row in actionable[:3]) if actionable else "VOO/QQQ、沪深300ETF"
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


def describe_max_opportunity(opportunity: list[dict[str, Any]], dqs: dict[str, Any], today_trade: bool) -> str:
    if not opportunity:
        return "暂无可排序机会。"
    top = opportunity[0]
    if not today_trade:
        return f"{top['name']}是长期配置优先方向，但当前{dqs.get('mode_label')}，短期不追涨，等待资金和数据条件确认。"
    return f"{top['name']}：{top.get('advice')}，评分{top.get('score')}，需继续服从资金预算和DQS门槛。"


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


def apply_dqs_to_opportunity(opportunity: list[dict[str, Any]], dqs: dict[str, Any]) -> list[dict[str, Any]]:
    if dqs.get("mode") not in {"direction", "safe"}:
        return opportunity
    adjusted: list[dict[str, Any]] = []
    for row in opportunity:
        item = dict(row)
        if item.get("advice") in {"优先加仓", "正常定投", "小额分批"}:
            item["advice"] = "观察，等待数据质量恢复"
            limitations = list(item.get("limitations", []) or [])
            limitations.append(f"DQS={dqs.get('score')}，当前不允许新增仓位建议")
            item["limitations"] = list(dict.fromkeys(limitations))
            item["reason"] = f"{item.get('reason', '')} 当前仅代表长期配置优先方向，不代表今日买入机会。"
        adjusted.append(item)
    return adjusted


def build_consistency_checks(decision: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    snapshot = decision.get("portfolio_snapshot") or _portfolio_snapshot()
    allocation_sum = sum(row["current_ratio"] for row in decision["allocation"])
    allocation_total = sum(row["current_amount_yuan"] for row in decision["allocation"])
    snapshot_total = int(snapshot.get("total_assets", 0) or 0)
    holding_total = sum(int(row.get("market_value_cny", 0) or 0) for row in snapshot.get("holdings", []) or [])
    configured_totals = snapshot.get("asset_class_totals", {}) or {}
    holding_class_totals = snapshot.get("holding_class_totals", {}) or {}
    budget = decision["budget"]
    dqs = decision["dqs"]
    if abs(allocation_sum - 1.0) > 0.01:
        errors.append(f"资产占比合计为{allocation_sum:.2%}，不接近100%。")
    if abs(allocation_total - snapshot_total) > 10:
        errors.append(f"资产配置合计{allocation_total}元与Portfolio Snapshot总资产{snapshot_total}元不一致。")
    if abs(holding_total - snapshot_total) > 10:
        errors.append(f"真实持仓明细合计{holding_total}元与总资产{snapshot_total}元不一致。")
    for category, configured in configured_totals.items():
        detail_total = int(holding_class_totals.get(category, 0) or 0)
        if abs(detail_total - int(configured)) > 10:
            errors.append(f"{category}类别金额{configured}元与持仓明细{detail_total}元不一致。")
    cash = snapshot.get("cash", {}) or {}
    expected_investable = max(
        0,
        int(cash.get("account_total_cash_cny", 0) or 0)
        - int(cash.get("cash_safety_reserve_cny", 0) or 0)
        - int(cash.get("live_grid_cash_cny", 0) or 0)
        - int(cash.get("other_reserved_cash_cny", 0) or 0),
    )
    if abs(expected_investable - int(budget.get("investable_cash_yuan", 0) or 0)) > 10:
        errors.append("现金口径无法推导：可投资现金与现金公式不一致。")
    gold = snapshot.get("gold", {}) or {}
    if not gold.get("reconciled", False):
        errors.append("黄金分类金额与黄金持仓明细合计不一致。")
    holding_amounts = _holding_amounts()
    for item in decision.get("opportunity", []) or []:
        name = item.get("name")
        symbol = item.get("symbol")
        expected = holding_amounts.get(str(symbol), holding_amounts.get(str(name), item.get("current_holding_yuan", 0)))
        if abs(float(item.get("current_holding_yuan", 0) or 0) - float(expected or 0)) > 10:
            errors.append(f"Opportunity Score持仓金额不一致：{name}。")
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
    overweight_categories = {row["category"] for row in decision["allocation"] if row["deviation_ratio"] > 0}
    for item in decision.get("opportunity", []) or []:
        if item.get("category") in overweight_categories and item.get("advice") in {"优先加仓", "正常定投", "小额分批"}:
            errors.append(f"{item.get('category')}已超配，但{item.get('name')}仍出现加仓类建议。")
    single_stock_adds = [
        item["name"]
        for item in decision.get("opportunity", []) or []
        if item.get("advice") in {"优先加仓", "正常定投", "小额分批"}
        and item.get("name") in {"NVDA", "GOOG", "BABA", "IBKR"}
    ]
    if single_stock_adds:
        errors.append(f"美股低配不得自动触发个股加仓：{', '.join(single_stock_adds)}。")
    budget_ids = [row.get("budget_id") for row in budget.get("rows", []) or [] if row.get("budget_id")]
    if len(budget_ids) != len(set(budget_ids)):
        errors.append("存在重复budget_id，同一资金可能被重复使用。")
    if budget.get("paper_grid_cash_yuan", 0):
        errors.append("网格模拟现金不得进入真实资金预算。")
    if decision["macro_event_high_next_7_days"] and decision["budget"]["today_total_yuan"] > 0:
        warnings.append("重大事件前仍有买入计划，需人工复核。")
    status = "PASS" if not errors and not warnings else "WARNING" if not errors else "FAIL"
    return {
        "ok": not errors,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_v12_1_decision(
    *,
    portfolio_result: dict[str, Any],
    live_market_result: dict[str, Any],
    macro_result: dict[str, Any],
    ai_advice_result: dict[str, Any],
) -> dict[str, Any]:
    strategy = load_strategy()
    snapshot = _portfolio_snapshot()
    allocation = enrich_allocation(portfolio_result, strategy)
    dqs = compute_dqs(live_market_result, strategy)
    risk = compute_risk_score(live_market_result, macro_result, dqs, strategy)
    opportunity = apply_dqs_to_opportunity(build_opportunity_scores(allocation, live_market_result, strategy), dqs)
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
        "portfolio_snapshot": snapshot,
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
        "max_opportunity": describe_max_opportunity(opportunity, dqs, today_trade),
        "one_sentence": "；".join(no_trade_reasons) + "；待资金和数据条件满足后再执行分批计划。",
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不自动交易，不接券商下单权限，不承诺收益。",
    }
    decision["consistency"] = build_consistency_checks(decision)
    if not decision["consistency"].get("ok"):
        decision["today_trade"] = False
        decision["trade_type"] = "无操作"
        decision["today_amount_yuan"] = 0
        decision["targets"] = "不适用"
        decision["funding_source"] = "不适用"
        decision["budget"]["today_total_yuan"] = 0
        decision["no_trade_reasons"] = ["数据对账失败，今日不操作"] + decision.get("no_trade_reasons", [])
        decision["one_sentence"] = "数据对账失败，今日不操作；先修复持仓、现金或预算口径后再评估。"
    write_log(f"V12.5 决策生成完成：DQS={dqs['score']} risk={risk['score']} today={decision['budget']['today_total_yuan']}", filename="stone_ai.log")
    return decision


def build_system_audit_text(context: dict[str, Any], decision: dict[str, Any]) -> str:
    live = context.get("live_market_result", {})
    quality = live.get("data_quality", {}) or {}
    market_result = context.get("market_result", {}) or {}
    execution = context.get("execution_plan_result", {}) or {}
    lines = [
        "# Stone AI V12.5 Stable System Audit",
        "",
        f"- 审计时间：{datetime.now().isoformat(timespec='seconds')}",
        "- 当前实际运行入口：根目录 `main.py`（V12.5 Stable冻结入口）。",
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
        f"- 美股/A股/港股/黄金显示0.00：旧市场摘要使用 `market_data.csv` 默认变化值，缺失行情没有区分失败和真实0；V12.5继续保持“暂无可靠数据/请求失败/缓存”表达。",
        f"- 双源验证覆盖率：旧路由拿到第一个成功源就返回，导致候选源不足；V12.5按候选源和Source Audit区分覆盖率。",
        f"- 一级来源覆盖率：取决于本次实际成功来源，不再把配置占位算作成功。",
        f"- AI状态：{decision['ai']['mode']}，原因：{decision['ai'].get('fallback_reason') or 'OpenAI可用性和DQS共同决定'}。",
        f"- 本周0元、本月金额、债券转权益冲突：旧逻辑把现金预算和未到账债券资金混用；V12.5拆成账户总现金、可投资现金和条件性债券到账计划。",
        f"- 基础定投无金额：V12.5在资金计划中明确计划日、金额、资金来源和不执行原因。",
        f"- 风险评分明细：旧评分来自 MarketAgent 汇总值 {market_result.get('market_risk_score', '暂无')}；V12.5继续输出八项风险分解。",
        "",
        "## 关键运行快照",
        "",
        f"- 旧数据质量分：{quality.get('score', '暂无')}",
        f"- 旧执行计划：today={execution.get('today_buy_wan', '暂无')}万 week={execution.get('week_buy_wan', '暂无')}万 month={execution.get('month_buy_wan', '暂无')}万",
        f"- 新DQS：{decision['dqs']['score']} / {decision['dqs']['mode_label']}",
        f"- 新风险评分：{decision['risk']['score']} / {decision['risk']['level']}",
    ]
    return "\n".join(lines)
