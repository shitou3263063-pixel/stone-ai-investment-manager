from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any
from zoneinfo import ZoneInfo

from src.analysis.scenario_analysis import calculate_portfolio_stress_scenarios
from src.data_sources.cn_hk_p1a import write_scoring_trace
from src.data_sources.decision_time import filter_market_for_cutoff, item_time_metadata
from src.data_sources.normalized_market import PRICE_STAGES, classify_price_stage, market_quote_reference
from src.macro.macro_calendar import get_upcoming_high_risk_events
from src.portfolio_snapshot import build_portfolio_snapshot
from utils.data_loader import load_config, project_root
from utils.logger import write_log


VERSION_NAME = "Stone AI Investment Manager Pro V12.7.0 Stable"

DEFAULT_STRATEGY: dict[str, Any] = {
    "cash": {"safety_ratio": 0.08, "hard_floor_ratio": 0.05},
    "dqs_thresholds": {
        "exact_amount": 85,
        "range_amount": 75,
        "direction_only": 60,
        "cap_when_dual_source_below": 0.25,
        "severe_conflict_cap": 59,
    },
    "dqs_weights": {
        "field_completeness": 20, "timeliness": 15, "source_quality": 15,
        "dual_source_validation": 15, "valuation_readiness": 15,
        "transaction_reconciliation_quality": 10, "consistency": 10,
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
        "valuation": 0.20,
        "trend_breadth": 0.15,
        "fundamentals": 0.20,
        "macro": 0.10,
        "flow": 0.10,
        "portfolio_fit": 0.20,
        "data_confidence": 0.05,
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
CRITICAL_MARKET = [
    "VOO", "QQQ", "TLT", "GLD", "^VIX", "DX-Y.NYB",
    "03033.HK", "510300.SS", "002558.SZ", "513060.SS", "513090.SS",
]
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
TRADE_ORIGINS = {
    "SCHEDULED_BASE_DCA", "SYSTEM_NEW_RECOMMENDATION", "USER_DISCRETIONARY_TRADE",
    "CONDITIONAL_PLAN_TRIGGERED", "RISK_REDUCTION", "UNKNOWN",
}


def scheduled_dca_event_window_policy(*, already_executed: bool, in_event_window: bool) -> str:
    """Apply event discipline prospectively without rewriting an executed fact."""
    if already_executed:
        return "PRE_AUTHORIZED_EXECUTED_NO_RETROACTIVE_RECLASSIFICATION"
    if in_event_window:
        return "PAUSE_AND_REVIEW_BEFORE_EXECUTION"
    return "ELIGIBLE_SUBJECT_TO_STANDARD_GATES"


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
        raise FileNotFoundError("config/strategy.yaml 缺失，无法确认目标配置和硬风控参数。")
    try:
        loaded = _deep_merge(DEFAULT_STRATEGY, load_config(path))
        target = loaded.get("target_allocation", {}) or {}
        if set(target) != set(CATEGORY_KEYS) or abs(sum(float(value) for value in target.values()) - 1.0) > 0.0001:
            raise ValueError("target_allocation必须包含六类资产且合计100%")
        return loaded
    except Exception as exc:  # noqa: BLE001
        write_log(f"strategy.yaml 读取失败：{exc}", filename="stone_ai.log")
        raise


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
    timing = item_time_metadata(item)
    value = item.get("close", item.get("value"))
    display = "暂无可靠数据"
    if _is_ok_item(item):
        display = f"{_to_float(value):.2f}"
    return {
        "name": name,
        "value": value if _is_ok_item(item) else None,
        "display_value": display,
        "previous": item.get("previous_close", item.get("previous_value")),
        "change_pct": item.get("change_pct"),
        "timestamp": timing.get("source_observation_time") or item.get("observed_at") or item.get("published_at") or item.get("date") or "暂无数据",
        "observed_at": timing.get("source_observation_time") or item.get("observed_at") or item.get("published_at") or item.get("date"),
        "observed_at_utc": item.get("observed_at_utc"),
        "fetched_at": item.get("fetched_at") or item.get("retrieved_at"),
        "received_at_utc": item.get("received_at_utc"),
        "source_timezone": item.get("source_timezone") or item.get("market_timezone") or "unknown",
        "time_status": item.get("time_status") or "ok",
        "market_timezone": item.get("market_timezone") or "unknown",
        "data_frequency": item.get("data_frequency") or "unknown",
        "data_session": item.get("data_session") or ("unavailable" if not _is_ok_item(item) else "unknown"),
        "freshness_status": item.get("freshness_status") or ("unavailable" if not _is_ok_item(item) else "unknown"),
        "age_hours": item.get("age_hours"),
        "data_age_hours": item.get("data_age_hours", item.get("age_hours")),
        "price_stage": item.get("price_stage") or timing.get("data_stage"),
        "data_basis": item.get("data_basis") or timing.get("data_basis"),
        "market_date": item.get("market_date") or timing.get("market_session_date"),
        "quote_timestamp": item.get("quote_timestamp") or timing.get("source_observation_time"),
        "is_finalized": bool(item.get("is_finalized", timing.get("is_finalized"))),
        "source_level": item.get("source_level", _source_tier(item.get("source"))),
        "comparable_date": item.get("comparable_date") or str(item.get("observed_at") or item.get("published_at") or item.get("date") or "")[:10] or None,
        "retrieved_at": timing.get("data_retrieval_time") or item.get("retrieved_at") or item.get("fetched_at") or "暂无数据",
        "source_observation_time": timing.get("source_observation_time"),
        "market_session_date": timing.get("market_session_date"),
        "data_retrieval_time": timing.get("data_retrieval_time"),
        "data_stage": timing.get("data_stage"),
        "source": item.get("source", "unavailable") if _is_ok_item(item) else "unavailable",
        "source_tier": _source_tier(item.get("source")),
        "success": _is_ok_item(item),
        "stale": not _fresh(item),
        "error": item.get("error") or item.get("warning") or ("数据拉取失败" if not _is_ok_item(item) else ""),
        "fallback_used": bool(item.get("cache_used")),
        "dual_verified": _verified_dual_source(item),
    }


COMPARABLE_INTRADAY_STAGES = {"INTRADAY"}
COMPARABLE_CLOSE_STAGES = {"OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"}


def market_points_comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Only equal dates with like-for-like trading sessions are comparable."""
    if not left.get("comparable_date") or left.get("comparable_date") != right.get("comparable_date"):
        return False
    stages = {
        str(left.get("price_stage") or left.get("data_stage") or "UNKNOWN").upper(),
        str(right.get("price_stage") or right.get("data_stage") or "UNKNOWN").upper(),
    }
    return stages <= COMPARABLE_INTRADAY_STAGES or stages <= COMPARABLE_CLOSE_STAGES


def aggregate_comparable_market_changes(
    live_market: dict[str, Any], symbols: tuple[str, str] = ("VOO", "QQQ")
) -> dict[str, Any]:
    items = _market_items(live_market)
    left, right = items.get(symbols[0], {}) or {}, items.get(symbols[1], {}) or {}
    comparable = market_points_comparable(left, right)
    changes_present = left.get("change_pct") is not None and right.get("change_pct") is not None
    if not comparable or not changes_present:
        return {
            "comparable": False,
            "combined_change_pct": None,
            "confidence": "low",
            "explanation": "行情时点不一致，暂不计算指数当日合计变化",
            "symbols": list(symbols),
        }
    return {
        "comparable": True,
        "combined_change_pct": _to_float(left.get("change_pct")) + _to_float(right.get("change_pct")),
        "confidence": "normal",
        "explanation": "行情日期和交易口径一致，可进行横向比较",
        "symbols": list(symbols),
        "comparable_date": left.get("comparable_date"),
    }


def build_market_table(live_market: dict[str, Any]) -> list[dict[str, Any]]:
    items = _market_items(live_market)
    macro = _macro_items(live_market)
    rows = [_metric_row(symbol, items.get(symbol, {})) for symbol in CRITICAL_MARKET]
    rows.extend(_metric_row(series, macro.get(series, {})) for series in CRITICAL_MACRO)
    return rows


def compute_dqs(
    live_market: dict[str, Any],
    strategy: dict[str, Any],
    macro_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    weights = strategy["dqs_weights"]
    items, macro = _market_items(live_market), _macro_items(live_market)
    all_rows = [items.get(symbol, {}) for symbol in CRITICAL_MARKET] + [macro.get(series, {}) for series in CRITICAL_MACRO]
    usable_rows = [item for item in all_rows if _is_ok_item(item)]
    market_ok = [_is_ok_item(items.get(symbol, {})) for symbol in CRITICAL_MARKET]
    macro_ok = [_is_ok_item(macro.get(series, {})) for series in CRITICAL_MACRO]
    tier1_rows = [item for item in usable_rows if _source_tier(item.get("source")) == 1]
    dual_rows = [item for item in all_rows if _verified_dual_source(item)]
    fresh_rows = [item for item in usable_rows if _fresh(item) and item_time_metadata(item).get("data_stage") != "STALE"]
    suspicious_zero = [name for name, item in {**{k: items.get(k, {}) for k in CRITICAL_MARKET}, **{k: macro.get(k, {}) for k in CRITICAL_MACRO}}.items() if _is_ok_item(item) and _to_float(item.get("close", item.get("value"))) == 0.0]
    conflicts = list((live_market.get("source_audit", {}) or {}).get("data_conflicts", []) or [])
    conflicts.extend((((live_market.get("cn_hk_p1a", {}) or {}).get("akshare", {}) or {}).get("source_conflicts", [])) or [])
    required_missing = [row["name"] for row in build_market_table(live_market) if not row["success"]]
    enhancement_missing = ["Put/Call Ratio", "市场宽度", "ETF资金流", "AAII情绪"]
    enhancement_rows = (live_market.get("market_context_status", {}) or {}).get("indicators", []) or []
    enhancement_missing = [name for name in enhancement_missing if not any(row.get("name") == name and row.get("status") in {"ok", "success"} for row in enhancement_rows)]
    stale_metrics = [row["name"] for row in build_market_table(live_market) if row.get("stale")]
    market_time = aggregate_comparable_market_changes(live_market)
    non_comparable = [] if market_time["comparable"] else market_time["symbols"]
    snapshot = _portfolio_snapshot()
    transactions = snapshot.get("confirmed_transactions", []) or []
    transaction_required = ["symbol", "action", "trade_datetime", "trade_date", "invested_amount_cny", "execution_price_usd", "quantity", "currency", "actual_fx_rate_cny_per_usd", "fee", "funding_source"]
    reconciliation: list[dict[str, Any]] = []
    for trade in transactions:
        missing = [field for field in transaction_required if trade.get(field) in {None, ""}]
        reconciliation.append({"id": trade.get("id"), "status": "pending_reconciliation" if missing else "reconciled", "missing_fields": missing})
    pending_valuations = [row.get("security_name") for row in snapshot.get("holdings", []) or [] if str(row.get("valuation_status")) != "confirmed_market_value"]
    calendar_confidence = str((macro_result or {}).get("calendar_confidence") or "unknown")
    field_score = round(((sum(market_ok) + sum(macro_ok)) / len(all_rows)) * weights["field_completeness"]) if all_rows else 0
    if calendar_confidence == "low": field_score = max(0, field_score - 1)
    timeliness_score = round((len(fresh_rows) / len(all_rows)) * weights["timeliness"]) if all_rows else 0
    source_score = round((len(tier1_rows) / len(all_rows)) * weights["source_quality"]) if all_rows else 0
    dual_score = round((len(dual_rows) / len(all_rows)) * weights["dual_source_validation"]) if all_rows else 0
    valuation_score = weights["valuation_readiness"] if not pending_valuations and not snapshot.get("holdings_stale") else max(0, round(weights["valuation_readiness"] * 0.35))
    transaction_score = weights["transaction_reconciliation_quality"] if not reconciliation else round(sum((len(transaction_required) - len(item["missing_fields"])) / len(transaction_required) for item in reconciliation) / len(reconciliation) * weights["transaction_reconciliation_quality"])
    consistency_score = weights["consistency"]
    if conflicts: consistency_score -= min(weights["consistency"], len(conflicts) * 3)
    if suspicious_zero: consistency_score = 0
    consistency_score = max(0, consistency_score)
    released_data_missing = [
        event.get("event_name") or event.get("name")
        for event in (macro_result or {}).get("released_events", []) or []
        if event.get("event_data_status") == "RELEASED_DATA_MISSING"
    ]
    macro_event_data_penalty = min(10, len(released_data_missing) * 5)
    raw_score = max(0, field_score + timeliness_score + source_score + dual_score + valuation_score + transaction_score + consistency_score - macro_event_data_penalty)
    dual_coverage = len(dual_rows) / len(all_rows) if all_rows else 0.0
    blocking_errors: list[str] = []
    if suspicious_zero: blocking_errors.append(f"关键数据出现异常0值：{', '.join(suspicious_zero)}")
    if conflicts: blocking_errors.append("关键数据存在来源冲突。")
    if not _is_ok_item(items.get("VOO", {})) or not _is_ok_item(items.get("^VIX", {})): blocking_errors.append("核心价格或VIX缺失。")
    if snapshot.get("holdings_stale"): blocking_errors.append("持仓市值可能滞后。")
    capped_score = raw_score
    if dual_coverage < strategy["dqs_thresholds"]["cap_when_dual_source_below"]: capped_score = min(capped_score, 74)
    if blocking_errors: capped_score = min(capped_score, strategy["dqs_thresholds"]["severe_conflict_cap"])
    legacy_components = [
        {"item": "field_completeness", "score": field_score, "max": weights["field_completeness"], "reason": f"核心行情与宏观可用{sum(market_ok) + sum(macro_ok)}/{len(all_rows)}"},
        {"item": "timeliness", "score": timeliness_score, "max": weights["timeliness"], "reason": f"新鲜且非STALE数据{len(fresh_rows)}/{len(all_rows)}"},
        {"item": "source_quality", "score": source_score, "max": weights["source_quality"], "reason": f"一级来源{len(tier1_rows)}/{len(all_rows)}"},
        {"item": "dual_source_validation", "score": dual_score, "max": weights["dual_source_validation"], "reason": f"双源验证{len(dual_rows)}/{len(all_rows)}"},
        {"item": "valuation_readiness", "score": valuation_score, "max": weights["valuation_readiness"], "reason": "存在待估值持仓或持仓市值滞后" if pending_valuations or snapshot.get("holdings_stale") else "持仓估值可用于配置口径"},
        {"item": "transaction_reconciliation_quality", "score": transaction_score, "max": weights["transaction_reconciliation_quality"], "reason": "无待对账实盘交易" if not reconciliation else f"{sum(1 for item in reconciliation if item['status'] == 'reconciled')}/{len(reconciliation)}笔实盘交易已完成对账"},
        {"item": "consistency", "score": consistency_score, "max": weights["consistency"], "reason": "无异常0值或严重冲突" if consistency_score == weights["consistency"] else "存在冲突或异常0值"},
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

    voo = items.get("VOO", {}) or {}
    voo_stage = item_time_metadata(voo).get("data_stage")
    scheduled_inputs = {
        "核心价格": _is_ok_item(voo) and voo_stage in {"INTRADAY", "OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"},
        "现金口径": _to_float((snapshot.get("cash", {}) or {}).get("account_total_cash_cny")) >= 0,
        "预算状态": bool(snapshot.get("bond_to_equity_plan") is not None),
        "事件状态": calendar_confidence not in {"low", "unknown"} and not released_data_missing,
    }
    scheduled_score = round(sum(25 for ok in scheduled_inputs.values() if ok))
    allocation_valid = abs(sum(float(value) for value in strategy.get("target_allocation", {}).values()) - 1.0) <= 1e-10
    strategic_score = round(
        40 * int(allocation_valid)
        + 30 * int(not snapshot.get("holdings_stale"))
        + 30 * (sum(market_ok) / len(market_ok) if market_ok else 0)
    )
    opportunity_score = int(capped_score)
    official_grid_quotes = sum(
        1 for symbol in ["VOO", "QQQ"]
        if item_time_metadata(items.get(symbol, {}) or {}).get("data_stage") == "OFFICIAL_CLOSE"
        and bool((items.get(symbol, {}) or {}).get("is_finalized"))
    )
    grid_score = min(int(capped_score), 50 * official_grid_quotes)
    hard_risk_inputs = [items.get("^VIX", {}) or {}, macro.get("DGS10", {}) or {}]
    risk_monitoring_score = round(sum(50 for item in hard_risk_inputs if _is_ok_item(item)))
    transaction_quality_score = round(
        100 * transaction_score / weights["transaction_reconciliation_quality"]
        if weights["transaction_reconciliation_quality"] else 0
    )
    use_cases = {
        "scheduled_dca": {"label": "Scheduled DCA DQS", "score": scheduled_score, "threshold": 65, "allowed": scheduled_score >= 65 and scheduled_inputs["核心价格"] and scheduled_inputs["现金口径"] and scheduled_inputs["预算状态"] and scheduled_inputs["事件状态"], "inputs": scheduled_inputs},
        "strategic_rebalance": {"label": "Strategic Rebalance DQS", "score": strategic_score, "threshold": 75, "allowed": strategic_score >= 75},
        "opportunity_add": {"label": "Opportunity Add DQS", "score": opportunity_score, "threshold": 85, "allowed": opportunity_score >= 85 and not blocking_errors},
        "grid": {"label": "Grid DQS", "score": grid_score, "threshold": 85, "allowed": grid_score >= 85 and official_grid_quotes == 2},
        "risk_monitoring": {"label": "Risk Monitoring DQS", "score": risk_monitoring_score, "threshold": 1, "allowed": risk_monitoring_score > 0},
        "transaction_reconciliation": {"label": "Transaction Reconciliation Quality", "score": transaction_quality_score, "threshold": 100, "allowed": transaction_quality_score >= 100},
    }

    return {
        "score": int(capped_score),
        "raw_score": int(raw_score),
        "mode": mode,
        "mode_label": mode_label,
        "components": legacy_components,
        "use_cases": use_cases,
        "market_coverage": sum(market_ok) / len(CRITICAL_MARKET),
        "macro_coverage": sum(macro_ok) / len(CRITICAL_MACRO),
        "tier1_coverage": len(tier1_rows) / len(all_rows) if all_rows else 0.0,
        "dual_source_coverage": dual_coverage,
        "freshness_coverage": len(fresh_rows) / len(all_rows) if all_rows else 0.0,
        "holding_freshness_ok": not bool(snapshot.get("holdings_stale")),
        "fx_available": _is_ok_item(items.get("DX-Y.NYB", {})),
        "event_calendar_confidence": calendar_confidence,
        "blocking_errors": blocking_errors,
        "missing_metrics": required_missing,
        "required_core_data": {"missing_count": len(required_missing), "missing_items": required_missing},
        "enhancement_data": {"missing_count": len(enhancement_missing), "missing_items": enhancement_missing},
        "optional_explanation_data": {"missing_count": 0, "missing_items": []},
        "required_core_missing_count": len(required_missing),
        "enhancement_missing_count": len(enhancement_missing),
        "optional_explanation_missing_count": 0,
        "enhancement_missing_items": enhancement_missing,
        "stale_metrics": stale_metrics,
        "non_comparable_metrics": non_comparable,
        "conflicts": conflicts,
        "suspicious_zero": suspicious_zero,
        "valuation_readiness": {"pending_holdings": pending_valuations, "ready": not pending_valuations and not bool(snapshot.get("holdings_stale"))},
        "transaction_reconciliation": reconciliation,
        "released_event_data_missing": released_data_missing,
        "macro_event_data_penalty": macro_event_data_penalty,
        "conclusion": (
            "核心决策数据基本可用，但增强型市场宽度、资金流和情绪数据不足，禁止生成精确新增仓位建议。"
            if not required_missing and enhancement_missing
            else "数据质量足够支持金额建议" if mode in {"exact", "range"}
            else "核心数据覆盖不足或验证不足，禁止新增仓位建议。"
        ),
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
        target_basis = "strategic_ratio"
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
                "target_basis": target_basis,
                "cash_safety_floor_yuan": (
                    round(_to_float((_portfolio_snapshot().get("cash", {}) or {}).get("cash_safety_reserve_cny")))
                    if category == "现金" else None
                ),
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
    market_time = aggregate_comparable_market_changes(live_market)
    snapshot = _portfolio_snapshot()
    total_assets = float(snapshot.get("total_assets", 0) or 0)
    class_totals = snapshot.get("asset_class_totals", {}) or {}
    bond_ratio = float(class_totals.get("债券", 0) or 0) / total_assets if total_assets else 0
    gold_ratio = float(class_totals.get("黄金", 0) or 0) / total_assets if total_assets else 0
    investable_cash = float((snapshot.get("cash", {}) or {}).get("investable_cash_cny", 0) or 0)
    single_stock_ratios = [
        float(row.get("market_value_cny", 0) or 0) / total_assets
        for row in snapshot.get("holdings", []) or []
        if str(row.get("strategy_bucket", "")).startswith("single_stock") and total_assets
    ]
    max_single_stock_ratio = max(single_stock_ratios, default=0.0)

    valuation = 12 if dqs["market_coverage"] < 0.6 else 10
    volatility = 5 if 0 <= vix < 20 else 10 if vix < 30 else 15
    if max_single_stock_ratio > 0.05:
        volatility = min(weights["volatility"], volatility + 2)
    interest = 6 if dgs10 and dgs10 < 4 else 10 if dgs10 < 4.8 else 15
    if bond_ratio - float(strategy["target_allocation"]["债券"]) > 0.08:
        interest = min(weights["interest_rate"], interest + 3)
    liquidity = 10 if investable_cash <= 0 else (5 if dqs["market_coverage"] >= 0.7 else 8)
    macro_event = weights["macro_event"] if macro_result.get("has_high_event_next_7_days") else 5
    combined_change = market_time.get("combined_change_pct")
    trend = 8 if combined_change is not None and combined_change < -2 else 5
    policy_geo = 7 if gold_ratio > float(strategy["target_allocation"]["黄金"]) else 6
    data_quality = round((100 - dqs["score"]) / 100 * weights["data_quality"])

    dgs10_point = macro.get("DGS10", {}) or {}
    dgs10_observed = dgs10_point.get("comparable_date") or dgs10_point.get("date") or "暂无可靠观察日期"
    interest_basis = (
        f"美国10年期收益率最新官方值为{dgs10:.2f}%，观察日期{dgs10_observed}；"
        "属于官方滞后日度数据，不代表报告生成时的实时收益率；风险评分已降低时效性置信度。"
        if dgs10 else "美国10年期收益率暂无可靠数据。"
    )
    trend_basis = (
        f"VOO与QQQ在{market_time.get('comparable_date')}口径一致，当日变化合计约{combined_change:.2f}%。"
        if market_time["comparable"] else
        "行情时点不一致，暂不计算指数当日合计变化；趋势按中性分处理，置信度低。"
    )
    components = [
        {"item": "估值", "score": min(valuation, weights["valuation"]), "weight": weights["valuation"], "basis": "估值数据不完整时按中性偏高风险处理。"},
        {"item": "波动率", "score": min(volatility, weights["volatility"]), "weight": weights["volatility"], "basis": f"VIX={vix if vix >= 0 else '暂无可靠数据'}；最大高风险/单股占比约{max_single_stock_ratio:.1%}。"},
        {"item": "利率", "score": min(interest, weights["interest_rate"]), "weight": weights["interest_rate"], "basis": interest_basis},
        {"item": "流动性", "score": min(liquidity, weights["liquidity"]), "weight": weights["liquidity"], "basis": f"真实可投资现金={investable_cash:,.0f}元；安全储备不可用于投资。"},
        {"item": "宏观事件", "score": min(macro_event, weights["macro_event"]), "weight": weights["macro_event"], "basis": "未来7天存在高等级事件。" if macro_result.get("has_high_event_next_7_days") else "未来7天暂无高等级事件。"},
        {"item": "趋势", "score": min(trend, weights["trend"]), "weight": weights["trend"], "basis": trend_basis},
        {"item": "政策与地缘", "score": min(policy_geo, weights["policy_geo"]), "weight": weights["policy_geo"], "basis": f"按中性偏谨慎处理；黄金占比{gold_ratio:.1%}，超配提高组合对避险行情反转的敏感度。"},
        {"item": "数据质量", "score": min(data_quality, weights["data_quality"]), "weight": weights["data_quality"], "basis": f"DQS={dqs['score']}。"},
    ]
    market_components = [row for row in components if row["item"] in {"估值", "波动率", "利率", "宏观事件", "趋势", "政策与地缘"}]
    score = int(sum(row["score"] for row in market_components))
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
    equity_ratio = sum(float(class_totals.get(category, 0) or 0) for category in ["美股", "港股", "A股"]) / total_assets if total_assets else 0.0
    tlt_value = sum(float(row.get("market_value_cny", 0) or 0) for row in snapshot.get("holdings", []) or [] if str(row.get("security_code")) == "TLT")
    portfolio_components = [
        {"item": "权益仓位", "score": min(25, round(equity_ratio * 45)), "weight": 25, "basis": f"权益资产占比{equity_ratio:.1%}。"},
        {"item": "单股集中度", "score": min(20, round(max_single_stock_ratio * 200)), "weight": 20, "basis": f"最大单股占比{max_single_stock_ratio:.1%}。"},
        {"item": "债券久期", "score": min(15, round((tlt_value / total_assets) * 120)), "weight": 15, "basis": f"TLT作为长久期债券计入债券配置，金额{tlt_value:,.0f}元。"},
        {"item": "黄金偏离", "score": min(15, round(max(0, gold_ratio - float(strategy['target_allocation']['黄金'])) * 100)), "weight": 15, "basis": f"黄金占比{gold_ratio:.1%}。"},
        {"item": "现金安全储备", "score": 0 if investable_cash >= 0 else 15, "weight": 15, "basis": "固定现金安全储备独立核算，未被交易建议占用。"},
        {"item": "相关性与静态压力", "score": min(10, round(max(0, bond_ratio - float(strategy['target_allocation']['债券'])) * 50)), "weight": 10, "basis": "债券、黄金与权益风险在静态压力测试中独立展示。"},
    ]
    portfolio_score = int(sum(row["score"] for row in portfolio_components))
    stages = [item_time_metadata(item).get("data_stage") for item in items.values() if _is_ok_item(item)]
    execution_components = [
        {"item": "重大事件窗口", "score": 25 if macro_result.get("has_high_event_next_48_hours") else 8 if macro_result.get("has_high_event_next_7_days") else 0, "weight": 25, "basis": "宏观事件状态机决定限制窗口，不把已发布事件继续当作UPCOMING。"},
        {"item": "正式收盘可用性", "score": 20 if any(stage not in {"OFFICIAL_CLOSE", "OFFICIAL_LAGGED_MACRO"} for stage in stages) else 0, "weight": 20, "basis": "盘中、延迟或未知数据不能作为正式收盘价使用。"},
        {"item": "价格缺失或过期", "score": 20 if dqs.get("stale_metrics") or dqs.get("missing_metrics") else 0, "weight": 20, "basis": "缺失或STALE行情会降低执行可行性。"},
        {"item": "可投资现金确认", "score": 0 if investable_cash >= 0 else 15, "weight": 15, "basis": f"真实可投资现金={investable_cash:,.0f}元，固定安全储备不占用。"},
        {"item": "预算隔离", "score": 0, "weight": 10, "basis": "实盘、条件性计划与模拟网格预算独立。"},
        {"item": "人工确认与对账", "score": 10 if any(item.get("status") == "pending_reconciliation" for item in dqs.get("transaction_reconciliation", [])) else 0, "weight": 10, "basis": "所有交易需要人工确认；待对账交易不生成精确收益或市值。"},
    ]
    execution_score = int(sum(row["score"] for row in execution_components))
    return {
        "score": score, "level": level, "components": market_components,
        "market_risk": {"score": score, "level": level, "components": market_components},
        "portfolio_risk": {"score": portfolio_score, "level": _risk_level(portfolio_score), "components": portfolio_components},
        "data_confidence": {"score": int(dqs["score"]), "level": dqs["mode_label"], "components": dqs.get("components", [])},
        "execution_risk": {"score": execution_score, "level": _risk_level(execution_score), "components": execution_components},
        "composite_conclusion": "市场、组合、数据与执行风险分别评估；DQS只表示数据质量，不等同于市场风险。",
        "market_time_consistency": market_time,
        "market_quote_contract": {
            symbol: market_quote_reference(items.get(symbol, {}), symbol)
            for symbol in ("VOO", "QQQ")
        },
    }


def _risk_level(score: int) -> str:
    if score <= 30:
        return "低风险"
    if score <= 50:
        return "中低风险"
    if score <= 70:
        return "中高风险"
    if score <= 85:
        return "高风险"
    return "极高风险"


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


def _p1a_record_usable(record: dict[str, Any]) -> bool:
    if str(record.get("status")) not in {"ok", "cached", "partial"}:
        return False
    if str(record.get("freshness", "fresh")) == "stale":
        return False
    if record.get("error_code") == "SOURCE_CONFLICT":
        return False
    if record.get("source") == "akshare" and not bool(record.get("scoring_eligible")):
        return False
    return True


def _p1a_source_label(record: dict[str, Any]) -> str:
    source = str(record.get("source") or record.get("provider") or "Tushare")
    underlying = str(record.get("underlying_provider") or "").strip()
    return f"{source}（底层：{underlying}）" if underlying else source


def _score_stock_valuation(metrics: dict[str, Any], fallback: float) -> tuple[float, list[str]]:
    pe = _to_float(metrics.get("pe_ttm"), -1)
    pb = _to_float(metrics.get("pb"), -1)
    evidence: list[str] = []
    scores: list[float] = []
    if pe > 0:
        scores.append(78 if pe <= 15 else 68 if pe <= 25 else 54 if pe <= 40 else 38 if pe <= 60 else 24)
        evidence.append(f"PE(TTM)={pe:.2f}")
    if pb > 0:
        scores.append(76 if pb <= 2 else 64 if pb <= 4 else 48 if pb <= 7 else 30)
        evidence.append(f"PB={pb:.2f}")
    return (sum(scores) / len(scores) if scores else fallback), evidence


def _score_002558_fundamentals(fundamental: dict[str, Any], fallback: float) -> tuple[float, list[str]]:
    indicators = ((fundamental.get("statements") or {}).get("financial_indicators") or {})
    metrics = indicators.get("metrics", {}) or {}
    if not _p1a_record_usable(indicators) or not metrics:
        return fallback, []
    evidence: list[str] = []
    scores: list[float] = []
    roe = _to_float(metrics.get("roe"), -1)
    margin = _to_float(metrics.get("netprofit_margin"), -1)
    debt = _to_float(metrics.get("debt_to_assets"), -1)
    revenue_yoy = _to_float(metrics.get("or_yoy"), -999)
    profit_yoy = _to_float(metrics.get("netprofit_yoy"), -999)
    if roe >= 0:
        scores.append(80 if roe >= 18 else 68 if roe >= 12 else 55 if roe >= 7 else 35)
        evidence.append(f"ROE={roe:.2f}%")
    if margin >= 0:
        scores.append(78 if margin >= 20 else 65 if margin >= 12 else 50 if margin >= 5 else 30)
        evidence.append(f"净利率={margin:.2f}%")
    if debt >= 0:
        scores.append(72 if debt <= 35 else 60 if debt <= 55 else 42 if debt <= 70 else 25)
        evidence.append(f"资产负债率={debt:.2f}%")
    if revenue_yoy > -900:
        scores.append(72 if revenue_yoy >= 15 else 62 if revenue_yoy >= 5 else 48 if revenue_yoy >= 0 else 30)
        evidence.append(f"营收同比={revenue_yoy:.2f}%")
    if profit_yoy > -900:
        scores.append(76 if profit_yoy >= 15 else 64 if profit_yoy >= 5 else 46 if profit_yoy >= 0 else 28)
        evidence.append(f"净利润同比={profit_yoy:.2f}%")
    return (sum(scores) / len(scores) if scores else fallback), evidence


def build_opportunity_scores(allocation: list[dict[str, Any]], live_market: dict[str, Any], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    items = _market_items(live_market)
    macro_items = _macro_items(live_market)
    market_completeness = live_market.get("market_completeness", {}) or {}
    p1a = live_market.get("cn_hk_p1a", {}) or {}
    p1a_completeness = p1a.get("analysis_completeness", {}) or {}
    # Older saved/test snapshots predate effective_data; keep read compatibility
    # while production snapshots always use the explicit provider selection.
    effective_p1a = p1a.get("effective_data", {}) or p1a.get("tushare", {}) or {}
    p1a_valuations = ((effective_p1a.get("valuation") or {}).get("items") or {})
    p1a_fundamentals = effective_p1a.get("fundamentals", {}) or {}
    hk_liquidity = p1a.get("hkma", {}) or {}
    hk_liquidity_metrics = hk_liquidity.get("metrics", {}) or {}
    amounts = _holding_amounts()
    asset_defs = [
        {"name": "VOO", "category": "美股", "symbol": "VOO", "holding_key": "VOO", "type": "core_etf", "reason": "核心宽基ETF，优先用于长期修复美股低配"},
        {"name": "QQQ", "category": "美股", "symbol": "QQQ", "holding_key": "QQQ", "type": "growth_etf", "reason": "成长宽基ETF，需兼顾估值、波动与科技重叠"},
        {"name": "NVDA", "category": "美股", "symbol": "NVDA", "holding_key": "NVDA", "type": "single_stock", "reason": "科技单股，不因美股低配自动加仓"},
        {"name": "GOOG", "category": "美股", "symbol": "GOOG", "holding_key": "GOOG", "type": "single_stock", "reason": "大型科技个股，需财报、估值与集中度共同确认"},
        {"name": "BABA", "category": "美股", "symbol": "BABA", "holding_key": "BABA", "type": "single_stock", "reason": "中概个股，与港股风险存在重叠"},
        {"name": "IBKR", "category": "美股", "symbol": "IBKR", "holding_key": "IBKR", "type": "single_stock", "reason": "券商周期个股，需盈利和估值数据确认"},
        {"name": "XLF", "category": "美股", "symbol": "XLF", "holding_key": "XLF", "type": "sector_etf", "reason": "行业ETF，不因美股低配获得宽基同等优先级"},
        {"name": "TLT", "category": "债券", "symbol": "TLT", "holding_key": "TLT", "type": "duration_bond_etf", "reason": "美国长期国债ETF，承受高久期利率风险"},
        {"name": "沪深300ETF", "category": "A股", "symbol": "510300.SS", "holding_key": "510300", "type": "core_etf", "reason": "A股核心宽基ETF"},
        {"name": "南方东英恒生科技指数ETF", "category": "港股", "symbol": "03033.HK", "holding_key": "03033.HK", "type": "core_etf", "reason": "真实持仓03033.HK；3033.HK仅为供应商代码格式，不使用代理ETF"},
        {"name": "恒生医疗ETF", "category": "港股", "symbol": "513060.SS", "holding_key": "513060", "type": "thematic_etf", "reason": "主题ETF，受行业周期和集中度约束"},
        {"name": "香港证券ETF", "category": "港股", "symbol": "513090.SS", "holding_key": "513090", "type": "thematic_etf", "reason": "高弹性主题ETF，不作为优先补仓资产"},
        {"name": "巨人网络", "category": "A股", "symbol": "002558.SZ", "holding_key": "002558", "type": "single_stock", "reason": "A股个股，需公司数据和集中度复核"},
        {"name": "*ST闻泰", "category": "A股", "symbol": "", "holding_key": "*ST闻泰", "type": "st_stock", "reason": "高风险ST持仓，永久禁止自动新增"},
        {"name": "中国债券", "category": "债券", "symbol": "", "holding_key": "中国债券组合（不含10年地债）", "type": "defensive_bond", "reason": "防守资产，当前债券超配时暂停新增"},
        {"name": "10年地债", "category": "债券", "symbol": "", "holding_key": "CN_LOCAL_BOND_10Y", "type": "defensive_bond", "reason": "已包含在中国债券总额中，不重复计算"},
        {"name": "黄金", "category": "黄金", "symbol": "GLD", "holding_key": "黄金", "type": "defensive_gold", "reason": "实物金和黄金ETF合并看待，超配时暂停新增"},
        {"name": "现金", "category": "现金", "symbol": "", "holding_key": "现金", "type": "cash", "reason": "用于安全储备与流动性，不等于可投资现金"},
    ]
    rows: list[dict[str, Any]] = []
    configured_weights = strategy.get("opportunity_weights", {}) or {}
    weights = {
        "valuation": float(configured_weights.get("valuation", 0.20)),
        "trend_breadth": float(configured_weights.get("trend_breadth", 0.15)),
        "fundamentals": float(configured_weights.get("fundamentals", 0.20)),
        "macro": float(configured_weights.get("macro", 0.10)),
        "flow": float(configured_weights.get("flow", 0.10)),
        "portfolio_fit": float(configured_weights.get("portfolio_fit", 0.20)),
        "data_confidence": float(configured_weights.get("data_confidence", 0.05)),
    }
    scoring = strategy.get("opportunity_scoring", {}) or {}
    total_yuan = sum(_to_float(row.get("current_amount_yuan")) for row in allocation)
    dgs10 = _to_float(macro_items.get("DGS10", {}).get("value"), default=-1)
    vix = _to_float(items.get("^VIX", {}).get("close"), default=-1)
    base_by_type = {
        "core_etf": (58, 74), "growth_etf": (52, 68), "sector_etf": (50, 60),
        "thematic_etf": (46, 54), "single_stock": (48, 56), "st_stock": (20, 10),
        "duration_bond_etf": (50, 58), "defensive_bond": (45, 64),
        "defensive_gold": (48, 58), "cash": (62, 78),
    }
    for asset in asset_defs:
        name = asset["name"]
        category = asset["category"]
        symbol = asset["symbol"]
        asset_type = asset["type"]
        cat_row = next((row for row in allocation if row["category"] == category), {})
        deviation = _to_float(cat_row.get("deviation_ratio"))
        portfolio_fit = 60
        if deviation < -0.08:
            portfolio_fit = 88
        elif deviation < -0.05:
            portfolio_fit = 78
        elif deviation > 0.08:
            portfolio_fit = 12
        elif deviation > 0.05:
            portfolio_fit = 28
        item = items.get(symbol, {}) if symbol else {}
        data_ok = _is_ok_item(item) if symbol else True
        completeness_key = "cn_data_completeness" if category == "A股" else "hk_data_completeness" if category == "港股" else ""
        market_gate = market_completeness.get(completeness_key, {}) if completeness_key else {}
        market_completeness_score = _to_float(market_gate.get("score_pct"), 100.0)
        analysis_key = "cn_analysis_completeness" if category == "A股" else "hk_analysis_completeness" if category == "港股" else ""
        analysis_gate = p1a_completeness.get(analysis_key, {}) if analysis_key else {}
        analysis_completeness_score = _to_float(analysis_gate.get("score_pct"), 100.0)
        effective_completeness_score = min(market_completeness_score, analysis_completeness_score)
        data_status = str(item.get("data_status") or ("VALID" if data_ok else "DATA_INSUFFICIENT")) if symbol else "NOT_APPLICABLE"
        decision_restricted = bool(
            symbol
            and category in {"A股", "港股"}
            and (effective_completeness_score < 60 or data_status != "VALID")
        )
        change = _to_float(item.get("change_pct")) if data_ok else 0.0
        holding_amount = round(amounts.get(asset["holding_key"], amounts.get(name, amounts.get(symbol, 0))))
        holding_ratio = holding_amount / total_yuan if total_yuan else 0.0
        valuation_base, fundamentals = base_by_type[asset_type]
        valuation = valuation_base + (10 if change <= -3 else 5 if change <= -1 else -8 if change >= 3 else -3 if change >= 1 else 0)
        p1a_inputs_used: list[str] = []
        p1a_positive: list[str] = []
        valuation_record = p1a_valuations.get(symbol, {}) if symbol else {}
        if _p1a_record_usable(valuation_record) and valuation_record.get("metrics"):
            valuation, evidence = _score_stock_valuation(valuation_record.get("metrics", {}), valuation)
            if evidence:
                p1a_inputs_used.append(
                    f"{_p1a_source_label(valuation_record)}估值:{valuation_record.get('valuation_basis')}"
                )
                p1a_positive.extend(evidence)
        financial_model = "not_applicable_etf" if asset_type in {"core_etf", "growth_etf", "sector_etf", "thematic_etf"} else "not_connected"
        if symbol == "002558.SZ":
            financial_model = "single_stock_fundamental"
            fundamental_record = p1a_fundamentals.get(symbol, {}) or {}
            fundamentals, evidence = _score_002558_fundamentals(fundamental_record, fundamentals - 8)
            if evidence:
                p1a_inputs_used.append(f"{_p1a_source_label(fundamental_record)}:002558财务指标")
                p1a_positive.extend(evidence)
        trend = 62 + max(-22, min(18, change * 5)) if data_ok else 22
        macro = 58
        if asset_type == "growth_etf" or (asset_type == "single_stock" and name in {"NVDA", "GOOG"}):
            macro += 6 if 0 <= dgs10 < 4 else -8 if dgs10 >= 4.8 else 0
        elif asset_type == "duration_bond_etf":
            macro += 10 if 0 <= dgs10 < 4 else -12 if dgs10 >= 4.8 else -3
        elif asset_type == "defensive_gold":
            macro += 6 if vix >= 25 else -3 if 0 <= vix < 15 else 0
        if category == "港股" and hk_liquidity.get("status") in {"ok", "partial"}:
            hibor_1m = _to_float(hk_liquidity_metrics.get("hibor_1m_pct"), -1)
            aggregate_balance = _to_float(hk_liquidity_metrics.get("aggregate_balance_hkd_mn"), -1)
            hkma_datasets = hk_liquidity.get("datasets", {}) or {}
            hibor_fresh = ((hkma_datasets.get("hibor") or {}).get("freshness") == "fresh")
            liquidity_fresh = ((hkma_datasets.get("liquidity") or {}).get("freshness") == "fresh")
            if hibor_1m >= 0 and hibor_fresh:
                macro += 5 if hibor_1m <= 2 else -7 if hibor_1m >= 4.5 else 0
                p1a_inputs_used.append("HKMA:1个月HIBOR")
                p1a_positive.append(f"1个月HIBOR={hibor_1m:.3f}%")
            if aggregate_balance >= 0 and liquidity_fresh:
                macro += 3 if aggregate_balance >= 100000 else -3 if aggregate_balance < 50000 else 0
                p1a_inputs_used.append("HKMA:银行体系总结余")
        volume_ratio = item.get("volume_ratio")
        flow = max(20, min(80, 50 + (_to_float(volume_ratio, 1.0) - 1.0) * 35)) if volume_ratio is not None else 24
        source_count = int(item.get("source_count", len(_candidate_values(item))) or 0) if symbol else 0
        if not symbol:
            data_confidence = 48
        elif not data_ok:
            data_confidence = 12
        elif not _fresh(item):
            data_confidence = 30
        elif source_count >= 2 and _verified_dual_source(item):
            data_confidence = 88
        elif _source_tier(item.get("source")) <= 2:
            data_confidence = 68
        else:
            data_confidence = 52
        if symbol and category in {"A股", "港股"}:
            if effective_completeness_score < 40:
                data_confidence = min(data_confidence, 20)
            elif effective_completeness_score < 60:
                data_confidence = min(data_confidence, 35)
            if data_status != "VALID":
                data_confidence = min(data_confidence, 15)

        if asset_type == "single_stock":
            portfolio_fit = min(portfolio_fit, 45)
            if holding_ratio > 0.05:
                portfolio_fit -= 12
            if symbol != "002558.SZ":
                fundamentals -= 8 if not item.get("fundamental_data_available") else 0
        if asset_type == "st_stock":
            portfolio_fit = 0
            valuation = min(valuation, 20)
            trend = min(trend, 20)
            macro = 15
            flow = 10
            data_confidence = min(data_confidence, 20)
        if asset_type == "duration_bond_etf":
            portfolio_fit = min(portfolio_fit, 35)
        if category in {"黄金", "债券"} and deviation > 0:
            portfolio_fit = min(portfolio_fit, 22)
        if category == "现金" and deviation < 0:
            portfolio_fit = 90
        if not data_ok and symbol:
            valuation -= 15
            macro -= 8

        component_scores = {
            "估值吸引力": max(0, min(100, valuation)),
            "趋势与市场宽度": max(0, min(100, trend)),
            "基本面或盈利质量": max(0, min(100, fundamentals)),
            "宏观环境适配": max(0, min(100, macro)),
            "资金流或成交结构": max(0, min(100, flow)),
            "组合适配度": max(0, min(100, portfolio_fit)),
            "数据置信度": max(0, min(100, data_confidence)),
        }
        raw_score = round(sum(component_scores[label] * weights[key] for label, key in [
            ("估值吸引力", "valuation"), ("趋势与市场宽度", "trend_breadth"),
            ("基本面或盈利质量", "fundamentals"), ("宏观环境适配", "macro"),
            ("资金流或成交结构", "flow"), ("组合适配度", "portfolio_fit"),
            ("数据置信度", "data_confidence"),
        ]))
        data_adjustment = 0
        if symbol and not data_ok:
            data_adjustment -= int(scoring.get("missing_data_penalty", 12))
        elif symbol and not _fresh(item):
            data_adjustment -= int(scoring.get("stale_data_penalty", 6))
        elif symbol and source_count < 2:
            data_adjustment -= int(scoring.get("single_source_penalty", 3))
        if decision_restricted:
            data_adjustment -= 15 if market_completeness_score < 40 or data_status != "VALID" else 8
        portfolio_adjustment = 0
        if deviation > 0.08:
            portfolio_adjustment -= int(scoring.get("overweight_8pct_penalty", 15))
        elif deviation > 0.05:
            portfolio_adjustment -= int(scoring.get("overweight_5pct_penalty", 10))
        elif deviation > 0 and category in {"黄金", "债券"}:
            portfolio_adjustment -= 5
        if asset_type == "single_stock":
            portfolio_adjustment -= int(scoring.get("single_stock_constraint", 5))
        elif asset_type == "sector_etf":
            portfolio_adjustment -= int(scoring.get("sector_constraint", 4))
        elif asset_type == "thematic_etf":
            portfolio_adjustment -= int(scoring.get("thematic_constraint", 5))
        elif asset_type == "st_stock":
            portfolio_adjustment -= int(scoring.get("st_constraint", 40))

        limitations: list[str] = []
        if category in {"黄金", "债券"} and deviation > 0:
            limitations.append("资产类别已高于目标，占比修复优先于市场机会")
        if asset_type == "single_stock":
            limitations.append("个股不得仅因资产类别低配而加仓")
        if asset_type == "st_stock":
            limitations.append("ST高风险股票永久禁止自动新增，必须人工风险复核")
        if not data_ok and symbol:
            limitations.append("行情数据不足，仅供观察")
        if decision_restricted:
            limitations.append(
                f"{category}基础行情完整度{market_completeness_score:.1f}%、P1A分析完整度{analysis_completeness_score:.1f}%，"
                f"状态{data_status}；限制高置信度买入建议"
            )
        if financial_model == "not_applicable_etf":
            limitations.append("ETF不适用个股财务评分")
        if volume_ratio is None and symbol:
            limitations.append("可靠ETF资金流未接入，成交结构项按低置信度处理")
        opportunity_group = (
            "core_etf" if name in {"VOO", "QQQ", "沪深300ETF", "南方东英恒生科技指数ETF"}
            else "satellite_holding" if name in {"NVDA", "GOOG", "BABA", "IBKR", "巨人网络", "XLF", "恒生医疗ETF", "香港证券ETF", "*ST闻泰"}
            else "strategic_allocation"
        )
        rows.append(
            {
                "symbol": symbol or name,
                "name": name,
                "category": category,
                "raw_score": raw_score,
                "data_quality_adjustment": data_adjustment,
                "portfolio_constraint_adjustment": portfolio_adjustment,
                "cross_section_adjustment": 0,
                "components": component_scores,
                "weights": weights,
                "current_holding_yuan": holding_amount,
                "portfolio_fit": portfolio_fit,
                "limitations": limitations or ["无硬性限制"],
                "positive_factors": [asset["reason"], f"{category}偏离目标{deviation * 100:+.1f}个百分点", *p1a_positive],
                "negative_factors": limitations or ["暂无额外硬性扣分"],
                "data_ok": data_ok,
                "data_status": data_status,
                "market_data_completeness": market_completeness_score if completeness_key else None,
                "analysis_data_completeness": analysis_completeness_score if analysis_key else None,
                "scoring_confidence": "低" if effective_completeness_score < 40 or data_status != "VALID" else "受限" if effective_completeness_score < 60 else "可用",
                "decision_restricted": decision_restricted,
                "missing_fields": item.get("missing_fields", []) if symbol else [],
                "asset_type": asset_type,
                "opportunity_group": opportunity_group,
                "deviation": deviation,
                "financial_model": financial_model,
                "p1a_inputs_used": list(dict.fromkeys(p1a_inputs_used)),
                "market_quote_ref": market_quote_reference(item, symbol) if symbol else None,
            }
        )

    ranked = []
    for group in ["strategic_allocation", "core_etf", "satellite_holding"]:
        group_rows = sorted([row for row in rows if row["opportunity_group"] == group], key=lambda row: row["raw_score"] + row["data_quality_adjustment"] + row["portfolio_constraint_adjustment"], reverse=True)
        ranked.extend(group_rows)
    max_adjustment = int(scoring.get("cross_section_max_adjustment", 8))
    for row in ranked:
        peers = [peer for peer in ranked if peer["opportunity_group"] == row["opportunity_group"]]
        index = peers.index(row)
        count = len(peers)
        percentile = 0.5 if count <= 1 else 1 - index / (count - 1)
        cross_adjustment = round((percentile - 0.5) * 2 * max_adjustment)
        row["cross_section_adjustment"] = cross_adjustment
        score = max(0, min(100, row["raw_score"] + row["data_quality_adjustment"] + row["portfolio_constraint_adjustment"] + cross_adjustment))
        row["score"] = score
        if score >= 80:
            band = "80—100：高优先级机会，但仍需通过预算和风控"
        elif score >= 70:
            band = "70—79：可分批关注"
        elif score >= 60:
            band = "60—69：持有或等待"
        elif score >= 50:
            band = "50—59：低优先级"
        elif score >= 40:
            band = "40—49：暂停新增"
        else:
            band = "0—39：风险复核或回避"
        row["advice_band"] = band
        if row.get("decision_restricted"):
            advice = "等待数据补齐" if row.get("data_status") != "VALID" else "观察"
        elif row["asset_type"] == "st_stock":
            advice = "风险复核或回避"
        elif row["category"] in {"黄金", "债券"} and row["deviation"] > 0:
            advice = "暂停新增"
        elif row["asset_type"] == "single_stock":
            advice = "继续持有" if row["data_ok"] and score >= 50 else "观察"
        elif not row["data_ok"] and row["symbol"] not in {"现金", "中国债券", "10年地债"}:
            advice = "观察"
        elif row["category"] == "现金":
            advice = "维持现金安全垫" if row["deviation"] >= -0.03 else "优先补现金"
        elif score >= 80 and row["asset_type"] in {"core_etf", "growth_etf"} and row["deviation"] < -0.05:
            advice = "优先加仓"
        elif score >= 70 and row["asset_type"] in {"core_etf", "growth_etf"} and row["deviation"] < -0.03:
            advice = "小额分批"
        elif score >= 60:
            advice = "继续持有"
        elif score >= 40:
            advice = "暂停新增"
        else:
            advice = "风险复核或回避"
        row["advice"] = advice
        row["allocation_priority"] = "高" if row["portfolio_fit"] >= 80 else "中" if row["portfolio_fit"] >= 50 else "低"
        row["valuation_attractiveness"] = row["components"]["估值吸引力"]
        row["tactical_entry_quality"] = round((row["components"]["趋势与市场宽度"] + row["components"]["资金流或成交结构"]) / 2)
        row["confidence"] = row["scoring_confidence"]
        row["final_action"] = advice
        row["reason"] = (
            f"{row['positive_factors'][0]}；原始分{row['raw_score']}，横截面{row['cross_section_adjustment']:+d}，"
            f"数据调整{row['data_quality_adjustment']:+d}，组合约束{row['portfolio_constraint_adjustment']:+d}。"
        )
    # Deliberately preserve group ordering: cash/bonds are not cross-sectionally
    # ranked against ETF or individual-stock purchase candidates.
    return sorted(ranked, key=lambda row: (row["opportunity_group"], -row["score"]))


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
    confirmed_plan = snapshot.get("bond_to_equity_plan", {}) or {}
    confirmed_transactions = snapshot.get("confirmed_transactions", []) or []
    scheduled_base_dca_trades = [
        trade for trade in confirmed_transactions
        if trade.get("status") == "executed" and trade.get("trade_origin") == "SCHEDULED_BASE_DCA"
    ]
    confirmed_base_dca_amount = sum(_to_float(trade.get("invested_amount_cny")) for trade in scheduled_base_dca_trades)
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
    dqs_allows_amount = bool(((dqs.get("use_cases", {}) or {}).get("scheduled_dca", {}) or {}).get("allowed"))
    high_event = bool(macro_result.get("has_high_event_next_7_days"))

    base_amount = float(strategy["budget"]["monthly_base_dca_yuan"]) / 2 if is_dca_day else 0.0
    if not dqs_allows_amount or confirmed_cash_available <= 0 or high_event:
        base_amount = 0.0
    base_amount = min(base_amount, confirmed_cash_available)

    opportunity_amount = 0.0
    opportunity_dqs_allows = bool(((dqs.get("use_cases", {}) or {}).get("opportunity_add", {}) or {}).get("allowed"))
    if opportunity_dqs_allows and risk["score"] <= 70 and not high_event and confirmed_cash_available > base_amount:
        top_score = opportunity[0]["score"] if opportunity else 0
        if top_score >= 82:
            opportunity_amount = min(confirmed_cash_available - base_amount, total_yuan * strategy["budget"]["single_trade_cash_ratio_cap"])

    rebalance_today = 0.0
    if confirmed_cash_available > base_amount + opportunity_amount:
        rebalance_today = 0.0

    total_today = base_amount + opportunity_amount + rebalance_today
    confirmed_week = confirmed_base_dca_amount + total_today
    confirmed_month = confirmed_base_dca_amount + total_today
    conditional_month = bond_month_cap
    actual_bond_cash_arrived = _to_float(confirmed_plan.get("bond_maturity_arrived_cny"))
    approved_bond_to_equity = min(
        conditional_month,
        _to_float(confirmed_plan.get("approved_amount_cny")),
    )
    executed_bond_to_equity = min(
        approved_bond_to_equity,
        _to_float(confirmed_plan.get("executed_amount_cny")),
    )
    remaining_bond_to_equity = max(0.0, approved_bond_to_equity - executed_bond_to_equity)

    top_targets = [row for row in opportunity if row["advice"] in {"优先加仓", "正常定投", "小额分批"}][:3]
    target_text = "、".join(row["name"] for row in top_targets) or "暂无"
    rows = [
        {
            "budget_id": "ACTUAL_BOND_TO_EQUITY_20260715",
            "type": "债券转权益资金来源/迁移属性",
            "execute": False,
            "amount_yuan": 0,
            "attributed_amount_yuan": round(executed_bond_to_equity),
            "targets": "VOO",
            "funding_source": "2026-07-15到期债券资金",
            "reason": "仅记录资金来源和债券转权益迁移属性；实际9,000元已在BUDGET_BASE_DCA中计算，不重复占用预算。",
            "record_type": "funding_and_migration_attribute",
            "counts_toward_actual_trade_total": False,
        },
        {
            "budget_id": "BUDGET_BASE_DCA",
            "type": "基础定投",
            "execute": bool(confirmed_base_dca_amount > 0 or base_amount > 0),
            "amount_yuan": round(confirmed_base_dca_amount if confirmed_base_dca_amount > 0 else base_amount),
            "targets": "VOO" if confirmed_base_dca_amount > 0 else (target_text if base_amount > 0 else "不适用"),
            "funding_source": "2026-07-15到期债券资金" if confirmed_base_dca_amount > 0 else ("现金安全线以上资金" if base_amount > 0 else "未使用资金"),
            "reason": "此前既定周三基础定投计划的用户确认执行结果。" if confirmed_base_dca_amount > 0 else ("今日是基础定投日且DQS允许金额。" if base_amount > 0 else ("今日不是基础定投执行日" if not is_dca_day else "现金不足、重大事件或DQS限制。")),
            "record_type": "confirmed_actual_trade" if confirmed_base_dca_amount > 0 else "current_recommendation",
            "counts_toward_actual_trade_total": bool(confirmed_base_dca_amount > 0),
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
            "type": "剩余债券转权益计划",
            "execute": False,
            "amount_yuan": round(remaining_bond_to_equity),
            "targets": "VOO/QQQ、沪深300ETF、恒生科技ETF",
            "funding_source": f"已到账专项可投资现金{remaining_bond_to_equity:,.0f}元",
            "reason": "资金已到账、可投资，但仍须服从后续市场与风险条件，不代表必须一次性投入。",
            "counts_toward_actual_trade_total": False,
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
        "actual_bond_cash_arrived_yuan": round(actual_bond_cash_arrived),
        "bond_to_equity_executed_this_month_yuan": round(executed_bond_to_equity),
        "bond_to_equity_remaining_this_month_yuan": round(remaining_bond_to_equity),
        "bond_to_equity_remaining_real_cash_yuan": round(_to_float(confirmed_plan.get("remaining_real_investable_cash_cny"), remaining_bond_to_equity)),
        "base_dca_executed_yuan": round(confirmed_base_dca_amount),
        "actual_trade_counted_once_yuan": round(confirmed_base_dca_amount),
        "bond_migration_attributed_yuan": round(executed_bond_to_equity),
        "event_window_policy_for_scheduled_dca": scheduled_dca_event_window_policy(
            already_executed=bool(confirmed_base_dca_amount > 0),
            in_event_window=bool(macro_result.get("has_high_event_next_48_hours")),
        ),
        "bond_excess_yuan": round(bond_excess),
        "is_dca_day": bool(is_dca_day),
        "next_dca_date": next_dca.isoformat(),
        "rows": rows,
        "funding_note": "本月债券到期资金30000元已实际到账，其中9000元用于此前既定周三基础定投；债券转权益只作为资金来源和迁移属性记录，交易金额只计算一次；剩余21000元为专项可投资现金。",
        "cash_formula": "可投资现金 = 账户总现金 - 固定现金安全储备 - 网格实盘现金 - 其他已占用现金",
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
    theoretical_full_months = math.ceil(transfer_needed / monthly_cap) if monthly_cap > 0 else 0
    twelve_month_transfer = min(transfer_needed, monthly_cap * 12)
    remaining_after_12 = max(0, transfer_needed - twelve_month_transfer)
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
        "theoretical_full_months": theoretical_full_months,
        "twelve_month_transfer_yuan": round(twelve_month_transfer),
        "remaining_after_12_months_yuan": round(remaining_after_12),
        "estimated_completion": f"预计第{theoretical_full_months}个月完成，但仍以DQS、到账资金和风险条件为准。" if theoretical_full_months else "当前无债券超配迁移需求。",
        "route_title": "未来12个月债券迁移第一阶段路线图",
        "conditional_cap_note": "路线图从下一笔可迁移债券本金起算，不重复计算本月已到账的30000元；仅表示未来月度上限，不保证每月执行，暂停月份的未用额度不得累积到下一月一次性执行。",
        "natural_maturity_or_redemption_route": "仅使用未来实际到期或赎回并已到账的债券本金；未到账资金不计入当前可投资现金。",
        "tlt_tactical_route": "TLT计入债券资产配置，但默认战术持有；除非人工确认主动减仓计划，不把TLT视为必须立即卖出的迁移资金。",
        "tlt_active_sale_planned": False,
        "future_unarrived_bond_principal_yuan": 0,
        "unused_monthly_cap_rollover": False,
        "quarterly_reviews": ["第3个月", "第6个月", "第9个月", "第12个月"],
        "pause_conditions": ["DQS低于60", "VIX高于30", "重大宏观事件前后", "市场或交易数据不足"],
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

        if bucket in {"core_etf", "growth_etf"}:
            fundamental_status = "核心宽基，重点复核估值、趋势与指数覆盖质量"
            risk = "指数估值、趋势和组合配置偏离风险"
            overlap = "与组合权益Beta重叠"
            add_condition = "到计划定投日，且DQS、现金、预算和事件风控均通过"
            reduce_condition = "资产类别严重超配、风险预算触发或长期目标变化"
        elif bucket in {"sector_etf", "thematic_etf"}:
            fundamental_status = "行业/主题工具，需复核行业周期与集中度"
            risk = "行业集中度和高波动风险"
            overlap = "与同类权益和主题持仓存在重叠"
            add_condition = "行业逻辑、估值和仓位上限均通过人工复核"
            reduce_condition = "行业逻辑恶化、主题仓位超限或流动性下降"
        elif bucket == "single_stock":
            fundamental_status = "普通个股，需结合最新财报、估值和公司风险人工复核"
            risk = "公司特有风险、财报风险和单股集中度"
            overlap = "计入所在行业及权益集中度"
            add_condition = "DQS>=85，财报和估值可验证，且单股与行业风险预算均有余量"
            reduce_condition = "基本面恶化、估值风险过高、集中度超限或公司事件触发"

        if category == "黄金":
            advice = "继续持有，暂停新增"
            risk = "黄金已高于目标，实物金条流动性弱，避免追高"
            overlap = "组合防守资产，与权益相关性较低"
            add_condition = "仅当黄金回落至目标附近且组合需要防守时再评估"
            reduce_condition = "黄金显著超配且避险趋势转弱时，优先评估黄金ETF而非金条"
            if str(row.get("market", "")) == "physical":
                fundamental_status = "实物黄金；关注保管、买卖价差和变现成本"
                risk = "流动性较弱、保管与变现成本；组合黄金已超配"
            else:
                fundamental_status = "黄金ETF；流动性优于实物金条，但统一计入黄金仓位"
                risk = "黄金价格与跟踪误差风险；组合黄金已超配"
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
            advice = "高风险人工复核，永久禁止自动新增"
            fundamental_status = "ST或高风险股票；退市、财务、监管和流动性风险待人工核查"
            risk = "ST或高风险个股，禁止进入自动定投、机会加仓和网格候选"
            add_condition = "系统不提供自动加仓条件；仅可记录用户明确人工批准的条件性计划"
            reduce_condition = "退市、财务、监管或流动性风险恶化时优先人工复核"
        if category == "现金":
            advice = "保留固定安全储备；专项现金分批待复核"
            fundamental_status = "流动性资产，不适用股票基本面模板"
            market_state = "不适用"
            risk = "固定安全储备220000元完整；专项可投资现金21000元不得与模拟网格资金混用"
            overlap = "不适用；用于安全储备和流动性管理"
            add_condition = "专项资金已到账；仅在市场、DQS和风险条件通过后分批评估"
            reduce_condition = "仅可使用安全储备以上且未被其他预算占用的现金"
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


def build_data_time_summary(market_table: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    observed = sorted(str(row["observed_at"]) for row in market_table if row.get("success") and row.get("observed_at"))
    comparable_dates = {str(row.get("comparable_date")) for row in market_table if row.get("success") and row.get("comparable_date")}
    sessions = {str(row.get("data_session")) for row in market_table if row.get("success") and row.get("data_session")}
    return {
        "report_timezone": "Asia/Shanghai",
        "report_generation_time": generated_at,
        "report_generated_at": generated_at,
        "decision_cutoff_time": generated_at,
        "decision_data_cutoff": generated_at,
        "has_unsynchronized_data": len(comparable_dates) > 1 or len(sessions) > 1,
        "oldest_critical_data_at": observed[0] if observed else None,
        "newest_critical_data_at": observed[-1] if observed else None,
        "data_stages": sorted({str(row.get("data_stage") or "UNKNOWN") for row in market_table}),
    }


def build_stress_exposures(allocation: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Split long-duration TLT from ordinary bonds for stress analysis only."""
    amounts = {row["category"]: _to_float(row.get("current_amount_yuan")) for row in allocation}
    tlt = sum(_to_float(row.get("market_value_cny")) for row in snapshot.get("holdings", []) or [] if str(row.get("security_code")) == "TLT")
    bonds = max(0, amounts.get("债券", 0) - tlt)
    return [
        {"category": "美股", "current_amount_yuan": amounts.get("美股", 0)},
        {"category": "港股", "current_amount_yuan": amounts.get("港股", 0)},
        {"category": "A股", "current_amount_yuan": amounts.get("A股", 0)},
        {"category": "普通债券", "current_amount_yuan": bonds},
        {"category": "TLT", "current_amount_yuan": tlt},
        {"category": "黄金", "current_amount_yuan": amounts.get("黄金", 0)},
        {"category": "现金", "current_amount_yuan": amounts.get("现金", 0)},
    ]


def build_scenarios(budget: dict[str, Any], opportunity: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    actionable = [row for row in opportunity if row.get("advice") in {"优先加仓", "正常定投", "小额分批"}]
    targets = "、".join(row["name"] for row in actionable[:3]) if actionable else "VOO/QQQ、沪深300ETF"
    conditional_cap = budget.get("bond_to_equity_remaining_real_cash_yuan", 0)
    no_cash = float(budget.get("investable_cash_yuan", 0) or 0) <= 0
    return [
        {
            "scenario": "市场平稳",
            "trigger": "主要指数波动未触发回撤阈值，VIX低于20或维持正常区间",
            "action": "基础定投按计划日执行；非计划日不交易。",
            "amount": (
                f"真实可执行0元；债券资金未到账前不可执行。"
                if no_cash
                else f"本月已确认执行{budget['month_confirmed_yuan']}元；已到账剩余专项现金{conditional_cap}元须继续通过市场、DQS与风险复核。"
            ),
            "targets": targets,
        },
        {
            "scenario": "指数回撤",
            "trigger": "以最近确认交易日收盘价为参考：回撤约3%观察，约5%小额分批，约8%及以上才考虑机会加仓",
            "action": "只有DQS>=85、长期逻辑未破坏、且资金来源确认时，才启用机会加仓。",
            "amount": (
                f"当前真实可执行0元；债券资金到账并通过复核后，单次条件性上限{int(strategy['budget']['bond_to_equity_single_cap_yuan'])}元。"
                if no_cash
                else f"单次不超过{int(strategy['budget']['bond_to_equity_single_cap_yuan'])}元，且不突破月度条件性上限。"
            ),
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
        f"下一个基础定投复核日：{budget['next_dca_date']}；若DQS>=75且市场与风险条件通过，可按计划复核。",
        f"本月债券资金已到账，剩余专项现金{budget.get('bond_to_equity_remaining_real_cash_yuan', 0):,.0f}元可分批评估，但不代表必须立即或一次性投入。",
        "若主要指数回撤约5%且DQS>=85，优先评估VOO/QQQ和沪深300ETF小额分批。",
        "若DQS低于60或关键价格缺失，继续禁止新增仓位建议。",
    ]


def describe_max_opportunity(opportunity: list[dict[str, Any]], dqs: dict[str, Any], today_trade: bool) -> str:
    candidates = [
        row for row in opportunity
        if row.get("category") != "现金" and row.get("advice") not in {"暂停新增", "风险复核或回避"}
    ]
    if not candidates:
        return "暂无可排序机会。"
    top = candidates[0]
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
        mode = "RULES_ONLY"
    return {
        "mode": mode,
        "provider": ai_advice.get("actual_provider", "stone_rule_engine"),
        "enabled": bool(ai_advice.get("enabled", False)),
        "called": bool(ai_advice.get("called", False)),
        "success": bool(ai_advice.get("success", False)),
        "openai_status": ai_advice.get("openai_status", "rules_only"),
        "call_failed": bool(ai_advice.get("call_failed", False)),
        "fallback_occurred": bool(ai_advice.get("fallback_occurred", False)),
        "description": ai_advice.get("description", "规则引擎独立完成分析。"),
        "openai_participated": ai_advice.get("ai_status") == "available",
        "fallback_reason": ai_advice.get("fallback_reason", ""),
        "error_category": ai_advice.get("error_category", ""),
        "conflict_with_rules": bool(ai_advice.get("conflict_with_rules", False)),
        "review_summary": ai_advice.get("review_summary", ""),
        "retry_count": ai_advice.get("retry_count", 0),
        "model": ai_advice.get("model", ""),
        "validation_errors": ai_advice.get("validation_errors", []),
        "impact": "AI仅解释，不覆盖DQS、资金预算和风控硬门槛。",
        "market_regime": ai_advice.get("market_regime", "由规则引擎判断"),
        "summary": ai_advice.get("cio_commentary") or ai_advice.get("summary", "Stone CIO规则引擎已完成分析。"),
        "most_important_risk": ai_advice.get("key_risk_3_7_days") or ai_advice.get("most_important_risk", "以规则引擎识别的首要风险为准。"),
        "best_action_today": ai_advice.get("portfolio_priority") or ai_advice.get("best_action_today", "服从DQS、现金安全线与资金来源约束。"),
        "avoid_action_today": ai_advice.get("avoid_action_today", "不绕过硬风控进行交易。"),
        "required_trigger_conditions": ai_advice.get("required_trigger_conditions", []),
        "best_opportunity": ai_advice.get("best_opportunity", "以Opportunity Score为观察线索"),
        "one_sentence": ai_advice.get("one_sentence_conclusion") or ai_advice.get("one_sentence", "规则引擎已完成今日复核。"),
    }


def build_rule_enhanced_analysis(decision: dict[str, Any]) -> dict[str, Any]:
    """用统一决策对象生成完整规则分析，OpenAI缺席时也不留空白。"""
    ai = dict(decision.get("ai", {}) or {})
    if ai.get("openai_participated"):
        return ai

    allocation = decision.get("allocation", []) or []
    underweight = min(allocation, key=lambda row: row.get("deviation_ratio", 0), default={})
    overweight = max(allocation, key=lambda row: row.get("deviation_ratio", 0), default={})
    budget = decision.get("budget", {}) or {}
    dqs = decision.get("dqs", {}) or {}
    risk = decision.get("risk", {}) or {}
    confirmed_executed = bool(decision.get("today_confirmed_trade_executed"))
    trade_text = "已记录用户确认的实盘交易，后续不追加操作" if confirmed_executed else ("执行已通过风控的计划" if decision.get("today_trade") else "今日不交易")
    investable_cash = float(budget.get("investable_cash_yuan", 0) or 0)

    ai.update(
        {
            "provider": "Stone CIO规则引擎",
            "summary": (
                f"组合总资产约{decision.get('portfolio_value_wan', 0):.2f}万元；"
                f"{underweight.get('category', '低配资产')}为{underweight.get('status', '待复核')}，"
                f"{overweight.get('category', '超配资产')}为{overweight.get('status', '待复核')}。"
                f"当前DQS={dqs.get('score')}、风险评分={risk.get('score')}，规则结论为{trade_text}。"
            ),
            "market_regime": f"规则风险等级为{risk.get('level')}，DQS={dqs.get('score')}，结论置信度服从数据质量门槛。",
            "most_important_risk": decision.get("max_risk", "暂无可靠风险结论"),
            "best_action_today": (
                f"{trade_text}；可投资现金为{investable_cash:,.0f}元。"
                + ("当前没有真实可执行买入预算。" if investable_cash <= 0 else "")
                +
                f"下一复核日为{decision.get('next_review_date')}，剩余专项资金只在DQS、市场和事件纪律同时满足后分批评估。"
            ),
            "avoid_action_today": (
                f"不要使用现金安全线以内资金；不要向{overweight.get('category', '超配资产')}追加常规资金；"
                "不要把已到账等同于必须立即投入；模拟网格资金仍与实盘严格隔离。"
            ),
            "one_sentence": decision.get("one_sentence", "规则引擎已完成今日复核。"),
            "best_opportunity": decision.get("max_opportunity", "暂无可执行机会"),
            "required_trigger_conditions": decision.get("next_triggers", []),
            "impact": "OpenAI是可选解释层；本次由规则引擎完成全部核心分析，DQS、预算和风控结论不受影响。",
        }
    )
    return ai


def apply_dqs_to_opportunity(opportunity: list[dict[str, Any]], dqs: dict[str, Any]) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    opportunity_gate = (dqs.get("use_cases", {}) or {}).get("opportunity_add", {}) or {}
    opportunity_allowed = bool(opportunity_gate.get("allowed"))
    for row in opportunity:
        item = dict(row)
        holding = float(item.get("current_holding_yuan", 0) or 0)
        item["long_term_allocation_priority"] = item.get("allocation_priority", "低")
        item["today_trade_permission"] = False
        item["current_holding_action"] = "继续持有" if holding > 0 else "不适用"
        item["observation_action"] = "保留观察，今日不建仓" if holding <= 0 else "继续观察持仓"
        if not opportunity_allowed and item.get("asset_type") in {"core_etf", "growth_etf", "sector_etf", "thematic_etf"} and item.get("advice") not in {"暂停新增", "风险复核或回避"}:
            item["advice"] = "等待条件，今日不交易"
            item["final_action"] = "等待条件，今日不交易"
            limitations = list(item.get("limitations", []) or [])
            limitations.append(f"Opportunity Add DQS={opportunity_gate.get('score', dqs.get('score'))}，当前不允许新增仓位建议")
            item["limitations"] = list(dict.fromkeys(limitations))
            item["reason"] = f"{item.get('reason', '')} 当前仅代表长期配置优先方向，不代表今日买入机会。"
        if holding <= 0:
            item["current_holding_action"] = "不适用"
        adjusted.append(item)
    return adjusted


def build_opportunity_groups(allocation: list[dict[str, Any]], opportunity: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Separate allocation priorities from ETF and satellite opportunity views."""
    strategic = []
    for row in allocation:
        priority = "高" if row.get("status") in {"严重低配", "严重超配"} else "中" if row.get("status") in {"低配", "超配"} else "低"
        strategic.append({
            "name": row["category"], "category": row["category"], "score": round(min(100, abs(float(row.get("deviation_ratio", 0) or 0)) * 600)),
            "allocation_priority": priority, "valuation_attractiveness": None, "tactical_entry_quality": None,
            "confidence": "配置台账", "final_action": "优先修复低配" if "低配" in row.get("status", "") else "暂停新增" if "超配" in row.get("status", "") else "维持",
            "reason": f"当前{row.get('status')}，偏离{float(row.get('deviation_ratio', 0) or 0):+.1%}。",
        })
    return {
        "strategic_allocation": strategic,
        "core_etf": [row for row in opportunity if row.get("opportunity_group") == "core_etf"],
        "satellite_holding": [row for row in opportunity if row.get("opportunity_group") == "satellite_holding"],
    }


def _build_consistency_checks_legacy(decision: dict[str, Any]) -> dict[str, Any]:
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
    target_sum = sum(float(row.get("target_ratio", 0) or 0) for row in decision["allocation"])
    if abs(target_sum - 1.0) > 0.0001:
        errors.append(f"目标资产占比合计为{target_sum:.2%}，不等于100%。")
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
    if not decision.get("today_trade") and any(
        float(budget.get(key, 0) or 0) > 0
        for key in ["today_total_yuan", "week_confirmed_yuan", "month_confirmed_yuan"]
    ):
        errors.append("今日结论为不交易，但真实执行额度不为0。")
    if float(budget.get("actual_bond_cash_arrived_yuan", 0) or 0) <= 0 and float(budget.get("approved_bond_to_equity_month_yuan", 0) or 0) > 0:
        errors.append("未到账债券资金被计入已批准额度。")
    if snapshot.get("holdings_stale"):
        warnings.append("持仓市值可能滞后，需人工更新Portfolio Snapshot。")
    grid = decision.get("grid", {}) or {}
    grid_budget = grid.get("grid_budget", {}) or {}
    if grid.get("paper_mode", True) and float(grid_budget.get("live_available_yuan", 0) or 0) > 0:
        errors.append("模拟网格出现真实可用预算。")
    if grid.get("paper_mode", True) and bool(grid.get("live_advice_enabled")):
        errors.append("模拟网格不得启用实盘建议。")
    if dqs.get("score", 0) < 85 and float(grid_budget.get("live_available_yuan", 0) or 0) > 0:
        errors.append("DQS不足却生成真实网格金额。")
    for event in decision.get("events", []) or []:
        try:
            if event.get("date") and date.fromisoformat(str(event["date"])[:10]) < date.today():
                errors.append(f"未来事件列表包含已过期事件：{event.get('name', '未命名事件')}。")
        except ValueError:
            warnings.append(f"事件日期无法验证：{event.get('name', '未命名事件')}。")
    if decision["macro_event_high_next_7_days"] and decision["budget"]["today_total_yuan"] > 0:
        warnings.append("重大事件前仍有买入计划，需人工复核。")
    status = "PASS" if not errors and not warnings else "PASS_WITH_WARNINGS" if not errors else "FAILED_VALIDATION"
    return {
        "ok": not errors,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_consistency_checks(decision: dict[str, Any]) -> dict[str, Any]:
    """Validate cross-section facts and return PASS/WARN/FAIL only."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, str]] = []

    def record(name: str, local_errors: list[str], local_warnings: list[str]) -> None:
        errors.extend(local_errors)
        warnings.extend(local_warnings)
        status = "FAIL" if local_errors else "WARN" if local_warnings else "PASS"
        explanation = "；".join(local_errors + local_warnings) or "口径一致。"
        checks.append({"check_item": name, "status": status, "description": explanation})

    budget = decision.get("budget", {}) or {}
    dqs = decision.get("dqs", {}) or {}
    snapshot = decision.get("portfolio_snapshot", {}) or {}

    canonical_errors: list[str] = []
    canonical_warnings: list[str] = []
    allocation = decision.get("allocation", []) or []
    target_weight_sum = sum(float(row.get("target_ratio", 0) or 0) for row in allocation)
    target_amount_sum = sum(float(row.get("target_amount_yuan", 0) or 0) for row in allocation)
    current_amount_sum = sum(float(row.get("current_amount_yuan", 0) or 0) for row in allocation)
    deviation_sum = sum(float(row.get("deviation_amount_yuan", 0) or 0) for row in allocation)
    total_assets = float(decision.get("portfolio_value_yuan", snapshot.get("total_assets", 0)) or 0)
    if allocation:
        if round(target_weight_sum, 10) != 1.0:
            canonical_errors.append(f"目标配置合计{target_weight_sum:.10f}，不等于100%。")
        if abs(target_amount_sum - total_assets) > 1:
            canonical_errors.append("目标金额合计不等于总资产。")
        if abs(deviation_sum) > 1 or abs(current_amount_sum - total_assets) > 1:
            canonical_errors.append("资产配置偏离金额无法闭合。")

    try:
        cutoff_dt = datetime.fromisoformat(str(decision.get("data_cutoff") or decision.get("generated_at")))
    except ValueError:
        cutoff_dt = None
        canonical_errors.append("决策截止时间无法解析。")
    quote_stages: dict[str, str] = {}
    for symbol, quote in (decision.get("normalized_market_quotes", {}) or {}).items():
        stage = str(quote.get("price_stage") or quote.get("data_stage") or "UNKNOWN").upper()
        quote_stages[str(symbol)] = stage
        if stage not in PRICE_STAGES:
            canonical_errors.append(f"{symbol}使用未知行情阶段{stage}。")
        for key in ["quote_timestamp", "retrieved_at"]:
            value = quote.get(key)
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    canonical_errors.append(f"{symbol}.{key}为无时区时间。")
            except ValueError:
                canonical_errors.append(f"{symbol}.{key}无法解析。")
        quote_ts = _parse_review_datetime(quote.get("quote_timestamp"), timezone_name=str(quote.get("market_timezone") or "Asia/Shanghai")) if quote.get("quote_timestamp") else None
        retrieved_ts = _parse_review_datetime(quote.get("retrieved_at"), timezone_name="Asia/Shanghai") if quote.get("retrieved_at") else None
        if quote_ts and retrieved_ts and quote_ts > retrieved_ts:
            canonical_errors.append(f"{symbol} quote_timestamp晚于retrieved_at。")
        if cutoff_dt and retrieved_ts and retrieved_ts > cutoff_dt:
            canonical_errors.append(f"{symbol} retrieved_at晚于报告决策截止时间。")
        if quote.get("data_age_hours") is not None and not quote.get("quote_timestamp"):
            canonical_errors.append(f"{symbol}没有可靠quote_timestamp却生成数据年龄。")
        if cutoff_dt and str(quote.get("market")) == "US" and quote.get("market_date"):
            expected_stage = classify_price_stage(
                market="US",
                market_timezone=str(quote.get("market_timezone") or "America/New_York"),
                market_date=str(quote.get("market_date")),
                decision_cutoff=cutoff_dt,
                source_finalized=bool(quote.get("is_finalized")),
                status_ok=str(quote.get("status")) in {"ok", "success", "cached"},
                stale=bool(quote.get("is_stale") or quote.get("stale")),
            )
            if stage == "OFFICIAL_CLOSE" and expected_stage != "OFFICIAL_CLOSE":
                canonical_errors.append(f"{symbol}美股未满足正式结算条件却标记OFFICIAL_CLOSE。")

    for row in decision.get("market_table", []) or []:
        symbol = str(row.get("name") or "")
        if symbol in quote_stages and str(row.get("price_stage") or row.get("data_stage") or "UNKNOWN").upper() != quote_stages[symbol]:
            canonical_errors.append(f"{symbol}在市场表和统一行情对象中的数据阶段不一致。")
    for event in decision.get("released_events", []) or []:
        if event.get("risk_level") == "high" and event.get("actual_value") is None and event.get("event_data_status") != "RELEASED_DATA_MISSING":
            canonical_errors.append(f"已公布重大事件{event.get('event_name')}缺少实际值且未标记RELEASED_DATA_MISSING。")
    forbidden_actions = ["小额分批", "建议加仓", "优先加仓", "立即配置", "买入"]
    for item in decision.get("opportunity", []) or []:
        if float(item.get("current_holding_yuan", 0) or 0) <= 0 and item.get("current_holding_action") == "继续持有":
            canonical_errors.append(f"零持仓标的{item.get('name')}错误显示继续持有。")
        if not item.get("today_trade_permission") and any(word in str(item.get("final_action") or "") for word in forbidden_actions):
            canonical_errors.append(f"{item.get('name')}今日交易权限为否但最终动作仍为买入类动作。")
    cash = snapshot.get("cash", {}) or {}
    simulated_cash = float(cash.get("paper_grid_cash_cny", 0) or 0)
    if simulated_cash and (simulated_cash <= total_assets or simulated_cash <= float(budget.get("investable_cash_yuan", 0) or 0)):
        canonical_errors.append("模拟网格现金进入真实资产或可投资现金。")
    for trade in decision.get("confirmed_transactions", []) or []:
        if trade.get("reconciliation_status") == "pending_reconciliation":
            if any(trade.get(key) is not None for key in ["quantity", "actual_fx_rate_cny_per_usd", "fee"]):
                canonical_errors.append("交易对账未完成却填入实际数量、汇率或费用。")
    grid = decision.get("grid", {}) or {}
    if grid.get("snapshot_comparable") and any(stage != "OFFICIAL_CLOSE" for stage in quote_stages.values() if stage == "STALE"):
        canonical_errors.append("使用过期行情生成精确网格价。")
    record("阻断级统一口径", canonical_errors, canonical_warnings)

    # Event consistency: all horizons and grid checks derive from the same event set.
    event_errors: list[str] = []
    event_warnings: list[str] = []
    try:
        as_of_raw = decision.get("generated_at") or decision.get("date") or date.today().isoformat()
        as_of = datetime.fromisoformat(str(as_of_raw))
        events = decision.get("events", []) or []
        actual_48h = bool(get_upcoming_high_risk_events(as_of, hours=48, events=events))
        actual_7d = bool(get_upcoming_high_risk_events(as_of, days=7, events=events))
        if actual_48h != bool(decision.get("macro_event_high_next_48_hours", actual_48h)):
            event_errors.append("未来48小时事件结论与统一事件列表不一致。")
        if actual_7d != bool(decision.get("macro_event_high_next_7_days", actual_7d)):
            event_errors.append("未来7天事件结论与统一事件列表不一致。")
        grid = decision.get("grid", {}) or {}
        if grid.get("enabled") and actual_48h:
            reasons = [
                str(reason)
                for item in (grid.get("symbols", {}) or {}).values()
                for reason in ((item.get("review", {}) or {}).get("reasons", []) or [])
            ]
            if not any("未来48小时" in reason for reason in reasons):
                event_errors.append("未来48小时存在高等级事件，但Smart Grid未进入谨慎模式。")
        if actual_48h and float(budget.get("today_total_yuan", 0) or 0) > 0:
            event_errors.append("高等级事件窗口内仍生成真实交易预算。")
    except (TypeError, ValueError, KeyError) as exc:
        event_warnings.append(f"事件时间无法完整核验：{exc}")
    record("事件一致性", event_errors, event_warnings)

    dqs_errors: list[str] = []
    dqs_warnings: list[str] = []
    required = dqs.get("required_core_data", {}) or {}
    enhancement = dqs.get("enhancement_data", {}) or {}
    if int(required.get("missing_count", 0) or 0) != len(required.get("missing_items", []) or []):
        dqs_errors.append("核心必需数据缺失统计与状态表不一致。")
    if int(enhancement.get("missing_count", 0) or 0) != len(enhancement.get("missing_items", []) or []):
        dqs_errors.append("增强型数据缺失统计与状态表不一致。")
    if dqs.get("mode") in {"direction", "safe"} and float(budget.get("today_total_yuan", 0) or 0) > 0:
        dqs_errors.append("DQS限制与最终交易建议冲突。")
    if dqs.get("stale_metrics"):
        dqs_warnings.append(f"存在过期数据：{', '.join(dqs['stale_metrics'])}。")
    record("DQS一致性", dqs_errors, dqs_warnings)

    cn_hk_errors: list[str] = []
    cn_hk_warnings: list[str] = []
    completeness = decision.get("market_completeness", {}) or {}
    for key, label in [("cn_data_completeness", "A股"), ("hk_data_completeness", "港股")]:
        if key not in completeness:
            continue
        gate = completeness.get(key, {}) or {}
        score = float(gate.get("score_pct", 0) or 0)
        if score < 60:
            cn_hk_warnings.append(f"{label}数据完整度{score:.1f}%，已限制高置信度买入建议。")
    for item in decision.get("opportunity", []) or []:
        if item.get("category") in {"A股", "港股"} and item.get("decision_restricted"):
            if item.get("advice") in {"优先加仓", "正常定投", "小额分批"}:
                cn_hk_errors.append(f"{item.get('name')}数据受限却仍出现加仓类建议。")
    record("A股与港股数据门槛", cn_hk_errors, cn_hk_warnings)

    cash_errors: list[str] = []
    cash_warnings: list[str] = []
    cash = snapshot.get("cash", {}) or {}
    expected_cash = max(
        0,
        float(cash.get("account_total_cash_cny", budget.get("account_total_cash_yuan", 0)) or 0)
        - float(cash.get("cash_safety_reserve_cny", budget.get("cash_safety_reserve_yuan", 0)) or 0)
        - float(cash.get("live_grid_cash_cny", budget.get("live_grid_cash_yuan", 0)) or 0)
        - float(cash.get("other_reserved_cash_cny", budget.get("other_reserved_cash_yuan", 0)) or 0),
    )
    investable = float(budget.get("investable_cash_yuan", 0) or 0)
    if abs(expected_cash - investable) > 10:
        cash_errors.append("可投资现金与账户现金减安全储备及占用资金的公式不一致。")
    if investable <= 0 and float(budget.get("today_total_yuan", 0) or 0) > 0:
        cash_errors.append("可投资现金为0时真实执行金额大于0。")
    if float(budget.get("actual_bond_cash_arrived_yuan", 0) or 0) <= 0 and float(budget.get("approved_bond_to_equity_month_yuan", 0) or 0) > 0:
        cash_errors.append("未到账债券资金被计入真实可投资现金。")
    conditional = float(budget.get("conditional_bond_to_equity_month_yuan", 0) or 0)
    if conditional > 0 and conditional == float(budget.get("today_total_yuan", 0) or 0):
        cash_errors.append("条件性预算被显示为今日执行预算。")
    is_user_snapshot_20260715 = str(snapshot.get("snapshot_date")) == "2026-07-15" and str(snapshot.get("source")) == "user_confirmed"
    if is_user_snapshot_20260715:
        arrived = float(budget.get("actual_bond_cash_arrived_yuan", 0) or 0)
        executed = float(budget.get("bond_to_equity_executed_this_month_yuan", 0) or 0)
        remaining = float(budget.get("bond_to_equity_remaining_this_month_yuan", 0) or 0)
        if abs(arrived - 30000) > 10 or abs(executed - 9000) > 10 or abs(remaining - 21000) > 10:
            cash_errors.append("本月债券到账、已执行和剩余额度未按30000/9000/21000元对账。")
        if abs(float(budget.get("cash_safety_reserve_yuan", 0) or 0) - 220000) > 10:
            cash_errors.append("固定现金安全储备不是用户确认的220000元。")
        if abs(float(budget.get("account_total_cash_yuan", 0) or 0) - 241000) > 10:
            cash_errors.append("交易后账户总现金不是241000元。")
    record("现金预算一致性", cash_errors, cash_warnings)

    confirmed_trade_errors: list[str] = []
    confirmed_trade_warnings: list[str] = []
    transactions = decision.get("confirmed_transactions", []) or []
    voo_trade = next((item for item in transactions if item.get("id") == "USERCONF-20260715-VOO-001"), None)
    if is_user_snapshot_20260715 and not voo_trade:
        confirmed_trade_errors.append("缺少2026-07-15用户确认的VOO实盘交易。")
    elif voo_trade:
        if float(voo_trade.get("execution_price_usd", 0) or 0) != 692.5:
            confirmed_trade_errors.append("VOO用户确认成交价格不是692.5美元/份。")
        if float(voo_trade.get("invested_amount_cny", 0) or 0) != 9000:
            confirmed_trade_errors.append("VOO用户确认投入金额不是9000元。")
        if any(voo_trade.get(key) is not None for key in ["quantity", "actual_fx_rate_cny_per_usd", "fee"]):
            confirmed_trade_errors.append("成交股数、实际汇率或手续费被未经确认地填入。")
        if not voo_trade.get("real_trade") or voo_trade.get("simulation_trade"):
            confirmed_trade_errors.append("VOO实盘交易与网格模拟交易未正确隔离。")
        if voo_trade.get("trade_origin") not in TRADE_ORIGINS:
            confirmed_trade_errors.append("VOO交易的trade_origin不在允许枚举中。")
        if voo_trade.get("trade_origin") == "SCHEDULED_BASE_DCA":
            expected_flags = {
                "execution_status": "USER_CONFIRMED_EXECUTED",
                "system_pre_authorized": True,
                "opportunity_add": False,
                "discretionary_trade": False,
                "event_chasing": False,
            }
            for key, expected in expected_flags.items():
                if voo_trade.get(key) != expected:
                    confirmed_trade_errors.append(f"计划内周三定投字段{key}与既定分类不一致。")
            expected_policy = scheduled_dca_event_window_policy(
                already_executed=True,
                in_event_window=bool(decision.get("macro_event_high_next_48_hours")),
            )
            if voo_trade.get("event_window_policy") != expected_policy:
                confirmed_trade_errors.append("已执行的事前批准定投被事件窗口事后改写分类。")

        counted_trade_total = sum(
            float(row.get("amount_yuan", 0) or 0)
            for row in budget.get("rows", []) or []
            if row.get("counts_toward_actual_trade_total")
        )
        attributed_migration_total = sum(
            float(row.get("attributed_amount_yuan", 0) or 0)
            for row in budget.get("rows", []) or []
            if row.get("record_type") == "funding_and_migration_attribute"
        )
        actual_trade_total = float(voo_trade.get("invested_amount_cny", 0) or 0)
        if abs(counted_trade_total - actual_trade_total) > 10:
            confirmed_trade_errors.append("基础定投实际金额未按单笔交易只计算一次。")
        if abs(attributed_migration_total - actual_trade_total) > 10:
            confirmed_trade_errors.append("债券转权益迁移属性金额与实际基础定投金额不一致。")
        arrived = float(budget.get("actual_bond_cash_arrived_yuan", 0) or 0)
        remaining = float(budget.get("bond_to_equity_remaining_this_month_yuan", 0) or 0)
        if abs(arrived - actual_trade_total - remaining) > 10:
            confirmed_trade_errors.append("基础定投金额、债券迁移额度与剩余现金变化不一致。")
        confirmed_trade_warnings.append("VOO成交股数、实际汇率和手续费待补充；新增9000元仅按成本暂记，禁止冒充实时市值。")
    if is_user_snapshot_20260715 or voo_trade:
        record("用户确认交易完整性", confirmed_trade_errors, confirmed_trade_warnings)

    time_errors: list[str] = []
    time_warnings: list[str] = []
    market_time = (decision.get("risk", {}) or {}).get("market_time_consistency", {}) or {}
    if market_time and not market_time.get("comparable"):
        time_warnings.append("行情时点不一致，未计算指数当日合计变化。")
    if any(row.get("stale") for row in decision.get("market_table", []) or []):
        time_warnings.append("关键行情或宏观数据存在过期项。")
    trend_basis = next((row.get("basis", "") for row in (decision.get("risk", {}) or {}).get("components", []) if row.get("item") == "趋势"), "")
    if market_time and not market_time.get("comparable") and "合计约" in str(trend_basis):
        time_errors.append("不同comparable_date的数据被直接形成当日合计结论。")
    try:
        run_time = _parse_review_datetime(decision.get("generated_at"))
        next_review = _parse_review_datetime(decision.get("next_review_date"))
        if run_time is None or next_review is None:
            time_errors.append("运行时间或下一复核时间无法解析。")
        elif next_review <= run_time:
            time_errors.append("下一复核时间不晚于本次运行时间。")
    except (TypeError, ValueError) as exc:
        time_errors.append(f"下一复核时间校验失败：{exc}")
    record("行情时点一致性", time_errors, time_warnings)

    trade_errors: list[str] = []
    grid = decision.get("grid", {}) or {}
    grid_budget = grid.get("grid_budget", {}) or {}
    if not decision.get("today_trade") and float(budget.get("today_total_yuan", 0) or 0) > 0:
        trade_errors.append("Stone CIO当前是否建议操作为否，但今日新增建议预算不为0。")
    if grid.get("paper_mode", True) and (float(grid_budget.get("live_available_yuan", 0) or 0) > 0 or grid.get("live_advice_enabled")):
        trade_errors.append("模拟网格影响了真实执行预算或建议。")
    record("模拟与实盘隔离", trade_errors, [])

    ai_errors: list[str] = []
    ai = decision.get("ai", {}) or {}
    if ai.get("openai_status") == "disabled" and (ai.get("call_failed") or ai.get("fallback_occurred") or ai.get("called")):
        ai_errors.append("OpenAI主动关闭却被标记为调用失败或回退。")
    if not ai.get("called") and ai.get("call_failed"):
        ai_errors.append("OpenAI未调用却显示调用失败。")
    record("OpenAI状态一致性", ai_errors, [])

    migration_errors: list[str] = []
    migration = decision.get("migration_plan", {}) or {}
    transfer = float(migration.get("theoretical_transfer_yuan", 0) or 0)
    cap = float(migration.get("monthly_cap_yuan", 0) or 0)
    expected_months = math.ceil(transfer / cap) if cap else 0
    if int(migration.get("theoretical_full_months", expected_months) or 0) != expected_months:
        migration_errors.append("债券理论转出总额、月度上限与预计完成月份不一致。")
    record("迁移路线一致性", migration_errors, [])

    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {
        "ok": not errors,
        "status": status,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "checks": checks,
        "checked_at": datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _parse_review_datetime(value: Any, *, timezone_name: str = "Asia/Shanghai") -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    timezone_value = ZoneInfo(timezone_name)
    if len(text) == 10:
        parsed = datetime.combine(date.fromisoformat(text), datetime.min.time()).replace(hour=8, minute=30)
    else:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone_value)
    return parsed.astimezone(timezone_value)


def resolve_next_review_datetime(
    generated_at: str,
    *,
    macro_candidate: Any = None,
    dca_candidate: Any = None,
    cn_next_open_date: Any = None,
    timezone_name: str = "Asia/Shanghai",
) -> str:
    """确保复核时间严格晚于运行时间，并正确比较带不同时区的候选时间。"""
    timezone_value = ZoneInfo(timezone_name)
    generated = _parse_review_datetime(generated_at, timezone_name=timezone_name)
    if generated is None:
        generated = datetime.now(tz=timezone_value)
    candidates: list[datetime] = []
    for value in [macro_candidate, cn_next_open_date, dca_candidate]:
        try:
            parsed = _parse_review_datetime(value, timezone_name=timezone_name)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and parsed > generated:
            candidates.append(parsed)
    if not candidates:
        candidate_date = generated.date() + timedelta(days=1)
        while candidate_date.weekday() >= 5:
            candidate_date += timedelta(days=1)
        candidates.append(datetime.combine(candidate_date, datetime.min.time(), tzinfo=timezone_value).replace(hour=8, minute=30))
    return min(candidates).isoformat(timespec="seconds")


def build_v12_1_decision(
    *,
    portfolio_result: dict[str, Any],
    live_market_result: dict[str, Any],
    macro_result: dict[str, Any],
    ai_advice_result: dict[str, Any],
) -> dict[str, Any]:
    strategy = load_strategy()
    snapshot = _portfolio_snapshot()
    timing = (live_market_result.get("decision_timing", {}) or {})
    generated_at = str(timing.get("report_generation_time") or datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"))
    try:
        cutoff = datetime.fromisoformat(str(timing.get("decision_cutoff_time") or generated_at))
        live_market_result = filter_market_for_cutoff(live_market_result, cutoff)
    except ValueError:
        # An invalid cutoff must never cause later-observed data to enter a decision.
        live_market_result = {**live_market_result, "items": {}, "macro": {"items": {}}, "decision_timing": {**timing, "invalid_cutoff": True}}
    allocation = enrich_allocation(portfolio_result, strategy)
    dqs = compute_dqs(live_market_result, strategy, macro_result)
    risk = compute_risk_score(live_market_result, macro_result, dqs, strategy)
    opportunity = apply_dqs_to_opportunity(build_opportunity_scores(allocation, live_market_result, strategy), dqs)
    opportunity_groups = build_opportunity_groups(allocation, opportunity)
    try:
        write_scoring_trace(opportunity, live_market_result.get("cn_hk_p1a", {}) or {})
    except Exception as exc:  # noqa: BLE001 - trace output must not break decisions
        write_log(f"P1A评分追踪文件写入失败：{exc}", filename="stone_ai.log")
    budget = build_budget_plan(allocation, dqs, risk, macro_result, opportunity, strategy)
    migration = build_migration_plan(allocation, budget)
    holding_diagnostics = build_holding_diagnostics(live_market_result, allocation)
    scenarios = build_scenarios(budget, opportunity, strategy)
    stress_scenarios = calculate_portfolio_stress_scenarios(allocation, strategy.get("scenario_stress", {}), build_stress_exposures(allocation, snapshot))
    ai_mode = build_ai_mode(ai_advice_result, dqs)
    market_table = build_market_table(live_market_result)
    time_summary = build_data_time_summary(market_table, generated_at)
    tushare_calendar = (
        (((live_market_result.get("cn_hk_p1a", {}) or {}).get("tushare", {}) or {}).get("trade_calendar", {}) or {})
    )
    next_review_date = resolve_next_review_datetime(
        generated_at,
        macro_candidate=macro_result.get("next_review_date"),
        dca_candidate=budget.get("next_dca_date"),
        cn_next_open_date=tushare_calendar.get("next_open_date"),
    )
    next_daily_review = resolve_next_review_datetime(
        generated_at,
        cn_next_open_date=tushare_calendar.get("next_open_date"),
    )
    scheduled_review_dt = _parse_review_datetime(budget.get("next_dca_date"))
    next_scheduled_dca_review = scheduled_review_dt.isoformat(timespec="seconds") if scheduled_review_dt else str(budget.get("next_dca_date"))
    next_event_trigger_review = {
        "mode": "IMMEDIATE_ON_TRIGGER",
        "time": macro_result.get("next_review_date"),
        "triggers": ["市场回撤", "VIX显著变化", "重大事件公布", "风险门槛触发"],
        "description": "满足回撤、波动率、重大事件或风险条件时即时进行。",
    }
    total_yuan = sum(row["current_amount_yuan"] for row in allocation)
    today_trade = budget["today_total_yuan"] > 0
    for row in opportunity:
        row["today_trade_permission"] = bool(today_trade and row.get("advice") in {"优先加仓", "小额分批", "正常定投"})
        if not row["today_trade_permission"] and any(word in str(row.get("final_action") or "") for word in ["加仓", "买入", "配置"]):
            row["final_action"] = "等待条件，今日不交易"
    confirmed_transactions = snapshot.get("confirmed_transactions", []) or []
    decision_date = str(snapshot.get("snapshot_date") or date.today().isoformat())
    today_confirmed_transactions = [item for item in confirmed_transactions if str(item.get("trade_date")) == decision_date]
    today_confirmed_trade_executed = bool(today_confirmed_transactions)
    confirmed_trade_amount = sum(_to_float(item.get("invested_amount_cny")) for item in today_confirmed_transactions)
    primary_confirmed_trade = today_confirmed_transactions[0] if today_confirmed_transactions else {}
    confirmed_trade_origin = str(primary_confirmed_trade.get("trade_origin") or "UNKNOWN")
    scheduled_base_dca_executed = confirmed_trade_origin == "SCHEDULED_BASE_DCA"

    no_trade_reasons = []
    if today_confirmed_trade_executed:
        no_trade_reasons.append("已完成用户确认的周三基础定投；剩余专项资金不强制立即投入")
    if not budget["is_dca_day"] and not today_confirmed_trade_executed:
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
        "date": decision_date,
        "generated_at": generated_at,
        "report_timezone": "Asia/Shanghai",
        "data_time_summary": time_summary,
        "data_cutoff": (live_market_result.get("decision_timing", {}) or {}).get("decision_cutoff_time") or generated_at,
        "trading_day_status": "周末/非交易时段需以下一交易日为准" if date.today().weekday() >= 5 else "交易日",
        "portfolio_value_yuan": round(total_yuan),
        "portfolio_value_wan": round(total_yuan / 10000, 2),
        "portfolio_snapshot": snapshot,
        "allocation": allocation,
        "dqs": dqs,
        "risk": risk,
        "opportunity": opportunity,
        "opportunity_groups": opportunity_groups,
        "budget": budget,
        "migration_plan": migration,
        "holding_diagnostics": holding_diagnostics,
        "scenarios": scenarios,
        "stress_scenarios": stress_scenarios,
        "market_context_status": live_market_result.get("market_context_status", {}),
        "market_completeness": live_market_result.get("market_completeness", {}),
        "cn_hk_p1a": live_market_result.get("cn_hk_p1a", {}),
        "cn_hk_analysis_completeness": live_market_result.get("cn_hk_analysis_completeness", {}),
        "market_table": market_table,
        "normalized_market_quotes": _market_items(live_market_result),
        "post_cutoff_data": (live_market_result.get("decision_timing", {}) or {}).get("post_cutoff_data", []),
        "ai": ai_mode,
        "macro_event_high_next_7_days": bool(macro_result.get("has_high_event_next_7_days")),
        "macro_event_high_next_48_hours": bool(macro_result.get("has_high_event_next_48_hours")),
        "high_risk_events_48h": macro_result.get("high_risk_events_48h", []) or [],
        "high_risk_events_7d": macro_result.get("high_risk_events_7d", []) or [],
        "events": macro_result.get("events", []) or [],
        "upcoming_events": macro_result.get("upcoming_events", []) or [],
        "released_events": macro_result.get("released_events", []) or [],
        "today_trade": today_trade,
        "today_confirmed_trade_executed": today_confirmed_trade_executed,
        "trade_origin": confirmed_trade_origin,
        "execution_status": primary_confirmed_trade.get("execution_status") if today_confirmed_trade_executed else None,
        "system_pre_authorized": bool(primary_confirmed_trade.get("system_pre_authorized")),
        "opportunity_add": bool(primary_confirmed_trade.get("opportunity_add")),
        "discretionary_trade": bool(primary_confirmed_trade.get("discretionary_trade")),
        "event_chasing": bool(primary_confirmed_trade.get("event_chasing")),
        "asset_migration_attribute": primary_confirmed_trade.get("asset_migration_attribute") if today_confirmed_trade_executed else None,
        "event_window_policy": primary_confirmed_trade.get("event_window_policy") if today_confirmed_trade_executed else None,
        "confirmed_transactions": confirmed_transactions,
        "today_confirmed_transactions": today_confirmed_transactions,
        "trade_type": (
            "周三基础定投（用户确认已执行）"
            if scheduled_base_dca_executed
            else "用户确认交易（已执行）"
            if today_confirmed_trade_executed
            else ("无操作" if not today_trade else "基础定投/机会加仓/再平衡")
        ),
        "today_amount_yuan": round(confirmed_trade_amount) if today_confirmed_trade_executed else budget["today_total_yuan"],
        "targets": "VOO" if today_confirmed_trade_executed else ("、".join(row["name"] for row in opportunity[:3]) if today_trade and opportunity else "不适用"),
        "funding_source": "2026-07-15到期债券资金；不占用固定现金安全储备" if today_confirmed_trade_executed else ("现金安全线以上资金" if today_trade else "今日不使用资金"),
        "decision_card": {
            "actual_trade_facts": today_confirmed_transactions,
            "actual_trade_classification": {
                "trade_origin": confirmed_trade_origin,
                "execution_status": primary_confirmed_trade.get("execution_status"),
                "system_pre_authorized": bool(primary_confirmed_trade.get("system_pre_authorized")),
                "trade_purpose": "基础定投" if scheduled_base_dca_executed else "待确认",
                "funding_source": primary_confirmed_trade.get("funding_source"),
                "asset_migration_attribute": primary_confirmed_trade.get("asset_migration_attribute"),
                "actual_amount_yuan": round(confirmed_trade_amount),
                "counting_rule": "实际交易金额只计算一次；债券转权益仅作为资金来源和迁移属性记录。",
            },
            "current_recommendation": {
                "continue_operation": bool(today_trade), "amount_yuan": round(budget.get("today_total_yuan", 0)),
                "targets": "、".join(row["name"] for row in opportunity_groups["core_etf"][:2]) if today_trade else "不建议继续操作",
                "funding_source": "已到账专项可投资现金" if today_trade else "不使用资金",
                "reason": "DQS、市场数据和风险门槛必须同时通过。", "next_review_time": next_review_date,
            },
            "conditional_plans": scenarios,
        },
        "no_trade_reasons": no_trade_reasons,
        "next_triggers": build_next_triggers(budget, dqs),
        "next_review_date": next_daily_review,
        "next_daily_review": next_daily_review,
        "next_scheduled_dca_review": next_scheduled_dca_review,
        "next_event_trigger_review": next_event_trigger_review,
        "next_review_reason": "下一日常复核为下一个交易日；计划定投和事件触发复核分别列示。",
        "max_risk": max(risk["components"], key=lambda row: row.get("score", 0))["basis"] if risk["components"] else "暂无",
        "max_opportunity": describe_max_opportunity(opportunity, dqs, today_trade),
        "one_sentence": (
            "本次9,000元VOO买入为此前既定周三基础定投计划的执行结果，资金来源为当日到账债券资金，不属于机会加仓、临时追涨或网格交易。"
            if today_confirmed_trade_executed
            else "；".join(no_trade_reasons) + "；待资金和数据条件满足后再执行分批计划。"
        ),
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不自动交易，不接券商下单权限，不承诺收益。",
    }
    decision["consistency"] = build_consistency_checks(decision)
    if not decision["consistency"].get("ok"):
        decision["today_trade"] = False
        if not decision.get("today_confirmed_trade_executed"):
            decision["trade_type"] = "无操作"
            decision["today_amount_yuan"] = 0
            decision["targets"] = "不适用"
            decision["funding_source"] = "不适用"
        decision["budget"]["today_total_yuan"] = 0
        decision["no_trade_reasons"] = ["数据对账失败，今日不操作"] + decision.get("no_trade_reasons", [])
        if not decision.get("today_confirmed_trade_executed"):
            decision["one_sentence"] = "数据对账失败，今日不操作；先修复持仓、现金或预算口径后再评估。"
    decision["ai"] = build_rule_enhanced_analysis(decision)
    write_log(f"V12.7.0 决策生成完成：DQS={dqs['score']} market_risk={risk['score']} today={decision['budget']['today_total_yuan']}", filename="stone_ai.log")
    return decision


def apply_ai_explanation(decision: dict[str, Any], ai_advice: dict[str, Any]) -> dict[str, Any]:
    """在规则裁决后挂载AI解释，并执行第二次硬一致性校验。"""
    decision["ai"] = build_ai_mode(ai_advice, decision["dqs"])
    decision["ai"] = build_rule_enhanced_analysis(decision)
    decision["consistency"] = build_consistency_checks(decision)
    if not decision["consistency"].get("ok"):
        decision["today_trade"] = False
        if not decision.get("today_confirmed_trade_executed"):
            decision["trade_type"] = "无操作"
            decision["today_amount_yuan"] = 0
            decision["targets"] = "不适用"
            decision["funding_source"] = "不适用"
        for key in ["today_total_yuan", "week_confirmed_yuan", "month_confirmed_yuan"]:
            decision["budget"][key] = 0
        if not decision.get("today_confirmed_trade_executed"):
            decision["one_sentence"] = "数据或规则一致性校验失败，今日不操作；等待人工排查。"
    return decision


def build_system_audit_text(context: dict[str, Any], decision: dict[str, Any]) -> str:
    live = context.get("live_market_result", {})
    quality = live.get("data_quality", {}) or {}
    market_result = context.get("market_result", {}) or {}
    execution = context.get("execution_plan_result", {}) or {}
    lines = [
        "# Stone AI V12.7.0 Stable System Audit",
        "",
        f"- 审计时间：{datetime.now().isoformat(timespec='seconds')}",
        "- 当前实际运行入口：根目录 `main.py`（V12.7.0 Stable唯一正式入口）。",
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
        f"- 美股/A股/港股/黄金显示0.00：旧市场摘要使用 `market_data.csv` 默认变化值，缺失行情没有区分失败和真实0；V12.6继续保持“暂无可靠数据/请求失败/缓存”表达。",
        f"- 双源验证覆盖率：旧路由拿到第一个成功源就返回，导致候选源不足；V12.6按候选源和Source Audit区分覆盖率。",
        f"- 一级来源覆盖率：取决于本次实际成功来源，不再把配置占位算作成功。",
        f"- 分析状态：{decision['ai']['mode']}，来源：{decision['ai'].get('provider')}；OpenAI仅为可选解释层。",
        f"- 本周0元、本月金额、债券转权益冲突：旧逻辑把现金预算和未到账债券资金混用；V12.6拆成账户总现金、可投资现金和条件性债券到账计划。",
        f"- 基础定投无金额：V12.6在资金计划中明确计划日、金额、资金来源和不执行原因。",
        f"- 风险评分明细：旧评分来自 MarketAgent 汇总值 {market_result.get('market_risk_score', '暂无')}；V12.6继续输出八项风险分解。",
        "",
        "## 关键运行快照",
        "",
        f"- 旧数据质量分：{quality.get('score', '暂无')}",
        f"- 旧执行计划：today={execution.get('today_buy_wan', '暂无')}万 week={execution.get('week_buy_wan', '暂无')}万 month={execution.get('month_buy_wan', '暂无')}万",
        f"- 新DQS：{decision['dqs']['score']} / {decision['dqs']['mode_label']}",
        f"- 新风险评分：{decision['risk']['score']} / {decision['risk']['level']}",
    ]
    return "\n".join(lines)
