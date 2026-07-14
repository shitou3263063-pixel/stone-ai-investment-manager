from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.data_sources.akshare_client import fetch_akshare_p1a_snapshot
from src.data_sources.announcement_client import fetch_official_announcement_snapshot
from src.data_sources.hkma_client import fetch_hkma_liquidity_snapshot
from src.data_sources.tushare_client import fetch_tushare_p1a_snapshot
from utils.data_loader import project_root
from utils.logger import write_log


def _now() -> str:
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _ok(value: dict[str, Any]) -> bool:
    return str(value.get("status")) in {"ok", "cached"}


def _scoring_usable(value: dict[str, Any]) -> bool:
    if not _ok(value) and str(value.get("status")) != "partial":
        return False
    if str(value.get("freshness", "fresh")) == "stale":
        return False
    if value.get("error_code") == "SOURCE_CONFLICT":
        return False
    if value.get("source") == "akshare":
        return bool(value.get("scoring_eligible"))
    return True


def _build_effective_data(tushare: dict[str, Any], akshare: dict[str, Any]) -> dict[str, Any]:
    """Choose actual usable records without hiding either provider's status."""
    tushare_valuations = ((tushare.get("valuation") or {}).get("items") or {})
    akshare_valuations = ((akshare.get("valuation") or {}).get("items") or {})
    tushare_fundamentals = tushare.get("fundamentals", {}) or {}
    akshare_fundamentals = akshare.get("fundamentals", {}) or {}

    def choose(primary: dict[str, Any], fallback: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if _scoring_usable(primary):
            return primary, "tushare_pro"
        if _scoring_usable(fallback):
            return fallback, "akshare"
        return primary if primary else fallback, "unavailable"

    calendar, calendar_source = choose(
        tushare.get("trade_calendar", {}) or {},
        akshare.get("trade_calendar", {}) or {},
    )
    valuation_002558, valuation_002558_source = choose(
        tushare_valuations.get("002558.SZ", {}) or {},
        akshare_valuations.get("002558.SZ", {}) or {},
    )
    valuation_csi300, valuation_csi300_source = choose(
        tushare_valuations.get("510300.SS", {}) or {},
        akshare_valuations.get("510300.SS", {}) or {},
    )
    fundamental_002558, fundamental_002558_source = choose(
        tushare_fundamentals.get("002558.SZ", {}) or {},
        akshare_fundamentals.get("002558.SZ", {}) or {},
    )
    return {
        "trade_calendar": calendar,
        "valuation": {
            "items": {
                "002558.SZ": valuation_002558,
                "510300.SS": valuation_csi300,
            }
        },
        "fundamentals": {"002558.SZ": fundamental_002558},
        "selected_sources": {
            "trade_calendar": calendar_source,
            "002558_valuation": valuation_002558_source,
            "002558_fundamental": fundamental_002558_source,
            "csi300_valuation": valuation_csi300_source,
        },
        "source_conflicts": list(akshare.get("source_conflicts", []) or []),
    }


def _analysis_completeness(
    p0: dict[str, Any],
    effective: dict[str, Any],
    hkma: dict[str, Any],
    announcements: dict[str, Any],
) -> dict[str, Any]:
    cn_p0 = float(((p0.get("cn_data_completeness") or {}).get("score_pct") or 0))
    hk_p0 = float(((p0.get("hk_data_completeness") or {}).get("score_pct") or 0))
    trade_calendar = effective.get("trade_calendar", {}) or {}
    valuations = ((effective.get("valuation") or {}).get("items") or {})
    fundamentals = ((effective.get("fundamentals") or {}).get("002558.SZ") or {})
    cn_announcement = announcements.get("cn", {}) or {}
    hk_announcement = announcements.get("hk", {}) or {}
    hk_metrics = hkma.get("metrics", {}) or {}

    cn_checks = {
        "P0真实持仓基础行情": cn_p0 >= 60,
        "A股交易日历": _scoring_usable(trade_calendar) and bool(trade_calendar.get("latest_open_date")),
        "002558个股估值": _scoring_usable(valuations.get("002558.SZ", {})) and bool((valuations.get("002558.SZ", {}).get("metrics") or {})),
        "沪深300基准估值": _scoring_usable(valuations.get("510300.SS", {})) and bool((valuations.get("510300.SS", {}).get("metrics") or {})),
        "002558财务报表": _scoring_usable(fundamentals) and (
            int(fundamentals.get("successful_statement_count", 0) or 0) >= 2
            or int(fundamentals.get("validated_metric_count", 0) or 0) >= 4
        ),
        "A股官方公告": cn_announcement.get("status") == "ok" and int(cn_announcement.get("record_count", 0) or 0) > 0,
    }
    cn_weights = {
        "P0真实持仓基础行情": 45,
        "A股交易日历": 10,
        "002558个股估值": 10,
        "沪深300基准估值": 10,
        "002558财务报表": 20,
        "A股官方公告": 5,
    }
    hk_checks = {
        "P0真实持仓基础行情": hk_p0 >= 60,
        "HKMA HIBOR": hk_metrics.get("hibor_1m_pct") is not None and ((hkma.get("datasets") or {}).get("hibor") or {}).get("freshness") != "stale",
        "HKMA 港元汇率": hk_metrics.get("usd_hkd") is not None and ((hkma.get("datasets") or {}).get("exchange_rate") or {}).get("freshness") != "stale",
        "HKMA 银行体系流动性": hk_metrics.get("aggregate_balance_hkd_mn") is not None and ((hkma.get("datasets") or {}).get("liquidity") or {}).get("freshness") != "stale",
        "港交所官方公告": hk_announcement.get("status") == "ok" and int(hk_announcement.get("record_count", 0) or 0) > 0,
    }
    hk_weights = {
        "P0真实持仓基础行情": 50,
        "HKMA HIBOR": 15,
        "HKMA 港元汇率": 15,
        "HKMA 银行体系流动性": 15,
        "港交所官方公告": 5,
    }

    def result(market: str, checks: dict[str, bool], weights: dict[str, int]) -> dict[str, Any]:
        score = round(sum(weights[name] for name, passed in checks.items() if passed), 1)
        return {
            "market": market,
            "score_pct": score,
            "confidence": "low" if score < 40 else "restricted" if score < 60 else "usable",
            "decision_restricted": score < 60,
            "high_confidence_buy_allowed": score >= 60,
            "checks": checks,
            "weights": weights,
            "missing_fields": [name for name, passed in checks.items() if not passed],
        }

    return {
        "cn_analysis_completeness": result("A股", cn_checks, cn_weights),
        "hk_analysis_completeness": result("港股及港股主题基金", hk_checks, hk_weights),
        "policy": {
            "below_60": "不得输出高置信度买入建议",
            "below_40": "Opportunity Score只能标记为低可信度",
            "no_ai_override": True,
        },
    }


def build_cn_hk_p1a_snapshot(
    p0_completeness: dict[str, Any],
    market_items: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    generated_at = _now()
    try:
        tushare = fetch_tushare_p1a_snapshot()
    except Exception as exc:  # noqa: BLE001 - P1A must never break the main flow
        write_log(f"P1A Tushare总适配器失败：{exc}", filename="stone_ai.log")
        tushare = {"provider": "tushare_pro", "configured": False, "status": "failed", "error_message": str(exc)}
    try:
        akshare = fetch_akshare_p1a_snapshot(tushare, market_items=market_items)
    except Exception as exc:  # noqa: BLE001
        write_log(f"P1A AKShare总适配器失败：{exc}", filename="stone_ai.log")
        akshare = {
            "provider": "akshare",
            "installed": False,
            "status": "failed",
            "error_code": "UNKNOWN_ERROR",
            "error_message": str(exc),
            "source_conflicts": [],
        }
    try:
        hkma = fetch_hkma_liquidity_snapshot()
    except Exception as exc:  # noqa: BLE001
        write_log(f"P1A HKMA总适配器失败：{exc}", filename="stone_ai.log")
        hkma = {"provider": "hkma_official", "status": "failed", "metrics": {}, "error_message": str(exc)}
    try:
        announcements = fetch_official_announcement_snapshot()
    except Exception as exc:  # noqa: BLE001
        write_log(f"P1A官方公告总适配器失败：{exc}", filename="stone_ai.log")
        announcements = {"status": "failed", "cn": {}, "hk": {}, "error_message": str(exc)}

    effective = _build_effective_data(tushare, akshare)
    completeness = _analysis_completeness(p0_completeness, effective, hkma, announcements)
    snapshot = {
        "schema_version": 1,
        "scope": "CN_HK_P1A_AUTHORITY_BASE_DATA",
        "generated_at": generated_at,
        "tushare": tushare,
        "akshare": akshare,
        "effective_data": effective,
        "hkma": hkma,
        "announcements": announcements,
        "analysis_completeness": completeness,
        "decision_policy": "增强数据只调整估值、基本面、港股流动性和置信度，不改变预算或交易金额规则。",
    }
    write_p1a_outputs(snapshot, p0_completeness)
    return snapshot


def write_p1a_outputs(snapshot: dict[str, Any], p0_completeness: dict[str, Any]) -> None:
    output_dir = project_root() / "outputs"
    tushare = snapshot.get("tushare", {}) or {}
    akshare = snapshot.get("akshare", {}) or {}
    effective = snapshot.get("effective_data", {}) or {}
    valuation_interfaces = ((tushare.get("valuation") or {}).get("interfaces") or {})
    fundamental = ((tushare.get("fundamentals") or {}).get("002558.SZ") or {})
    validation = {
        "generated_at": snapshot.get("generated_at"),
        "scope": snapshot.get("scope"),
        "tushare_configured": bool(tushare.get("configured")),
        "tushare_transport": tushare.get("transport"),
        "tushare_status": tushare.get("status", "missing"),
        "tushare_error_code": tushare.get("error_code"),
        "tushare_error_summary": tushare.get("error_summary"),
        "tushare_trade_calendar_status": (tushare.get("trade_calendar") or {}).get("status", "missing"),
        "tushare_002558_valuation_status": (valuation_interfaces.get("002558_valuation") or {}).get("status", "missing"),
        "tushare_002558_fundamental_status": fundamental.get("status", "missing"),
        "tushare_csi300_valuation_status": (valuation_interfaces.get("csi300_valuation") or {}).get("status", "missing"),
        "tushare_last_success_at": tushare.get("last_success_at"),
        "akshare_status": akshare.get("status", "missing"),
        "akshare_version": akshare.get("version"),
        "akshare_source_conflicts": akshare.get("source_conflicts", []),
        "effective_sources": effective.get("selected_sources", {}),
        "fundamental_002558_status": fundamental.get("status", "missing"),
        "etf_financial_model_prohibited": True,
        "hkma_status": (snapshot.get("hkma") or {}).get("status", "missing"),
        "cn_announcement_status": ((snapshot.get("announcements") or {}).get("cn") or {}).get("status", "missing"),
        "hk_announcement_status": ((snapshot.get("announcements") or {}).get("hk") or {}).get("status", "missing"),
        **(snapshot.get("analysis_completeness") or {}),
        "safe_degradation_passed": True,
    }
    _write_json(output_dir / "cn_hk_p1a_validation.json", validation)
    _write_json(output_dir / "cn_hk_fundamental_snapshot.json", {
        "generated_at": snapshot.get("generated_at"),
        "items": effective.get("fundamentals", {}),
        "selected_sources": effective.get("selected_sources", {}),
        "etf_policy": "ETF不得套用个股财务评分。",
    })
    _write_json(output_dir / "cn_hk_valuation_snapshot.json", {
        "generated_at": snapshot.get("generated_at"),
        **(effective.get("valuation", {}) or {}),
        "selected_sources": effective.get("selected_sources", {}),
    })
    _write_json(output_dir / "hk_liquidity_snapshot.json", snapshot.get("hkma", {}) or {})
    _write_json(output_dir / "cn_hk_announcement_snapshot.json", snapshot.get("announcements", {}) or {})

    coverage_path = output_dir / "cn_hk_data_coverage.json"
    coverage = {
        "generated_at": snapshot.get("generated_at"),
        "scope": "CN_HK_P0_PLUS_P1A",
        "p0_market_completeness": p0_completeness,
        **(snapshot.get("analysis_completeness") or {}),
    }
    _write_json(coverage_path, coverage)


def write_scoring_trace(rows: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    symbols = {"002558.SZ", "510300.SS", "513060.SS", "513090.SS", "03033.HK"}
    trace_rows = []
    for row in rows:
        if row.get("symbol") not in symbols:
            continue
        trace_rows.append({
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "asset_type": row.get("asset_type"),
            "financial_model": row.get("financial_model"),
            "p1a_inputs_used": row.get("p1a_inputs_used", []),
            "components": row.get("components", {}),
            "data_quality_adjustment": row.get("data_quality_adjustment"),
            "portfolio_constraint_adjustment": row.get("portfolio_constraint_adjustment"),
            "final_score": row.get("score"),
            "advice": row.get("advice"),
            "limitations": row.get("limitations", []),
        })
    _write_json(project_root() / "outputs" / "cn_hk_scoring_trace.json", {
        "generated_at": _now(),
        "p1a_snapshot_generated_at": snapshot.get("generated_at"),
        "rows": trace_rows,
        "policy": "只有实际成功的数据进入评分；ETF不使用个股财务模型。",
    })
