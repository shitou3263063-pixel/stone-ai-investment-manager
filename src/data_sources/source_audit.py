from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

import yaml

from utils.data_loader import project_root
from utils.logger import write_log


REGISTRY_PATH = project_root() / "config" / "source_registry.yaml"
AUDIT_PATH = project_root() / "data" / "source_audit.json"


def load_source_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"source registry not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _source_key(source: str | None) -> str:
    value = str(source or "unavailable").strip()
    if value.startswith("cache:"):
        return "local_cache"
    return value


def _source_tier(source: str | None, registry: dict[str, Any]) -> int | None:
    key = _source_key(source)
    return ((registry.get("sources", {}) or {}).get(key, {}) or {}).get("tier")


def _is_registered(source: str | None, registry: dict[str, Any]) -> bool:
    key = _source_key(source)
    return key in (registry.get("sources", {}) or {})


def _is_tier1(source: str | None, registry: dict[str, Any]) -> bool:
    return _source_tier(source, registry) == 1


def _parse_limit(text: str | int | float | None) -> timedelta:
    if text is None:
        return timedelta(days=7)
    if isinstance(text, (int, float)):
        return timedelta(days=float(text))
    value = str(text).strip().lower()
    if value.endswith("h"):
        return timedelta(hours=float(value[:-1]))
    if value.endswith("d"):
        return timedelta(days=float(value[:-1]))
    return timedelta(days=float(value))


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _is_stale(fetched_at: str | None, freshness_limit: str | int | float | None, now: datetime) -> bool:
    parsed = _parse_timestamp(fetched_at)
    if parsed is None:
        return True
    return now - parsed > _parse_limit(freshness_limit)


def choose_preferred_source(candidates: list[dict[str, Any]], registry: dict[str, Any]) -> dict[str, Any]:
    """Pick the most authoritative candidate; official tier-1 beats media/aggregators."""

    valid = [item for item in candidates if item.get("status", "ok") == "ok"]
    if not valid:
        return {}
    return sorted(
        valid,
        key=lambda item: (
            _source_tier(item.get("source"), registry) or 99,
            1 if item.get("cache_used") else 0,
            str(item.get("fetched_at", "")),
        ),
    )[0]


def detect_data_conflict(
    metric: str,
    candidates: list[dict[str, Any]],
    registry: dict[str, Any],
    tolerance_pct: float = 1.0,
) -> dict[str, Any] | None:
    values = []
    for item in candidates:
        if item.get("status", "ok") != "ok":
            continue
        raw = item.get("close", item.get("value"))
        try:
            values.append((float(raw), item))
        except (TypeError, ValueError):
            continue
    if len(values) < 2:
        return None
    low = min(value for value, _ in values)
    high = max(value for value, _ in values)
    if low == 0:
        return None
    spread_pct = (high / low - 1) * 100
    if spread_pct <= tolerance_pct:
        return None
    preferred = choose_preferred_source([item for _, item in values], registry)
    return {
        "metric": metric,
        "spread_pct": round(spread_pct, 2),
        "preferred_source": preferred.get("source", "unavailable"),
        "resolution": "official_source_preferred" if _is_tier1(preferred.get("source"), registry) else "higher_tier_source_preferred",
    }


def _metric_item(metric: str, market: dict[str, Any]) -> dict[str, Any]:
    if metric in (market.get("items", {}) or {}):
        return (market.get("items", {}) or {}).get(metric, {}) or {}
    return ((market.get("macro", {}) or {}).get("items", {}) or {}).get(metric, {}) or {}


def _source_plan(metric: str, spec: dict[str, Any]) -> list[str]:
    sources = [spec.get("primary_source"), spec.get("backup_source")]
    return [str(item) for item in sources if item]


def _success_sources(item: dict[str, Any]) -> list[str]:
    if item.get("status") not in {"ok", None}:
        return []
    source = item.get("source")
    if not source or source == "unavailable":
        return []
    return [str(source)]


def _source_summary(metric: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "metric": metric,
        "source": item.get("source", "unavailable"),
        "published_at": item.get("published_at") or item.get("date"),
        "retrieved_at": item.get("retrieved_at"),
        "freshness_status": item.get("freshness_status", "stale" if item.get("cache_stale") else "unknown"),
        "fetched_at": item.get("fetched_at") or item.get("date"),
        "status": item.get("status", "missing"),
        "value": item.get("close", item.get("value")),
        "previous_close": item.get("previous_close"),
        "change_pct": item.get("change_pct"),
        "cache_used": bool(item.get("cache_used", False)),
        "cache_stale": bool(item.get("cache_stale", False)),
        "summary": item.get("warning", "") or item.get("error", ""),
    }


def build_source_audit(
    market: dict[str, Any],
    registry: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    try:
        registry = registry or load_source_registry()
    except Exception as exc:  # noqa: BLE001 - audit failure must degrade safely
        return {
            "scan_status": "failed",
            "scan_complete": False,
            "message": f"全球权威数据扫描失败：{exc}",
            "planned_sources": [],
            "successful_sources": [],
            "failed_sources": [],
            "stale_sources": [],
            "tier1_coverage": 0.0,
            "critical_metric_coverage": 0.0,
            "data_conflicts": [],
            "blocking_errors": ["全球权威数据扫描未完成"],
            "dqs_cap": 69,
            "precision_allowed": False,
            "source_coverage_summary": "全球权威数据扫描未完成，禁止精确金额建议。",
        }

    critical_metrics = registry.get("critical_metrics", {}) or {}
    planned_sources: list[dict[str, Any]] = []
    successful_sources: list[dict[str, Any]] = []
    failed_sources: list[dict[str, Any]] = []
    stale_sources: list[dict[str, Any]] = []
    unregistered_sources: list[dict[str, Any]] = []
    missing_double_source: list[str] = []
    data_conflicts: list[dict[str, Any]] = []
    metric_audit: list[dict[str, Any]] = []
    response_summaries: list[dict[str, Any]] = []
    evidence_groups: set[str] = set()

    for metric, spec in critical_metrics.items():
        item = _metric_item(metric, market)
        candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else [item]
        conflict = detect_data_conflict(metric, candidates, registry)
        if conflict:
            data_conflicts.append(conflict)
            preferred = choose_preferred_source(candidates, registry)
            if preferred:
                item = {**item, **preferred}
        plan = _source_plan(metric, spec)
        planned_sources.append({"metric": metric, "sources": plan})
        response_summaries.append(_source_summary(metric, item))

        successes = []
        for candidate in candidates:
            successes.extend(_success_sources(candidate))
        successes = list(dict.fromkeys(successes))
        if successes:
            for source in successes:
                row = {"metric": metric, "source": source, "fetched_at": item.get("fetched_at") or item.get("date")}
                successful_sources.append(row)
                if not _is_registered(source, registry):
                    unregistered_sources.append(row)
                elif _is_tier1(source, registry):
                    evidence_groups.add(str(spec.get("evidence_group", "")))
        else:
            failed_sources.append({"metric": metric, "planned_sources": plan, "error": item.get("error") or item.get("warning") or "missing"})

        stale = bool(item.get("cache_stale", False)) or _is_stale(
            item.get("published_at") or item.get("date") or item.get("fetched_at"),
            spec.get("freshness_limit"),
            now,
        )
        if successes and stale:
            stale_sources.append({"metric": metric, "source": successes[0], "fetched_at": item.get("fetched_at") or item.get("date")})

        if str(spec.get("verification_requirement", "")).lower() == "dual_source" and len(set(map(_source_key, successes))) < 2:
            missing_double_source.append(metric)

        metric_audit.append(
            {
                "metric": metric,
                "category": spec.get("category"),
                "metric_type": spec.get("metric_type"),
                "source": item.get("source", "unavailable"),
                "published_at": item.get("published_at") or item.get("date"),
                "retrieved_at": item.get("retrieved_at"),
                "freshness_status": item.get("freshness_status", "stale" if stale else "fresh"),
                "fetched_at": item.get("fetched_at") or item.get("date"),
                "registered": all(_is_registered(source, registry) for source in successes) if successes else False,
                "tier": _source_tier(successes[0], registry) if successes else None,
                "stale": stale,
                "double_source_verified": len(set(map(_source_key, successes))) >= 2,
                "value": item.get("close", item.get("value")),
            }
        )

    planned_count = len(critical_metrics) or 1
    tier1_success_count = sum(1 for row in successful_sources if _is_tier1(row.get("source"), registry))
    usable_critical_count = sum(
        1
        for row in metric_audit
        if row["registered"] and not row["stale"] and row["value"] is not None and row["metric"] not in missing_double_source
    )
    tier1_coverage = tier1_success_count / planned_count
    critical_metric_coverage = usable_critical_count / planned_count

    blocking_errors: list[str] = []
    if unregistered_sources:
        blocking_errors.append("存在未登记来源，禁止形成交易结论")
    if missing_double_source:
        blocking_errors.append("关键数据未完成双源验证")

    low_coverage = (
        tier1_coverage < float((registry.get("policy", {}) or {}).get("tier1_coverage_min", 0.8))
        or critical_metric_coverage < float((registry.get("policy", {}) or {}).get("critical_metric_coverage_min", 0.85))
    )
    dqs_cap = int((registry.get("policy", {}) or {}).get("low_coverage_dqs_cap", 69)) if low_coverage else 100
    if low_coverage:
        blocking_errors.append("数据覆盖不足")

    required_groups = set(((registry.get("policy", {}) or {}).get("trade_candidate_evidence", {}) or {}).get("required_groups", []))
    evidence_ready = required_groups.issubset(evidence_groups)
    if not evidence_ready:
        blocking_errors.append("交易候选缺少三类独立证据")

    audit = {
        "scan_status": "complete",
        "scan_complete": True,
        "message": "全球权威数据扫描完成" if not low_coverage else "数据覆盖不足",
        "planned_sources": planned_sources,
        "successful_sources": successful_sources,
        "failed_sources": failed_sources,
        "stale_sources": stale_sources,
        "tier1_coverage": round(tier1_coverage, 4),
        "critical_metric_coverage": round(critical_metric_coverage, 4),
        "data_conflicts": data_conflicts,
        "unregistered_sources": unregistered_sources,
        "missing_double_source": missing_double_source,
        "evidence_groups": sorted(group for group in evidence_groups if group),
        "trade_evidence_ready": evidence_ready,
        "metric_audit": metric_audit,
        "response_summaries": response_summaries,
        "blocking_errors": list(dict.fromkeys(blocking_errors)),
        "dqs_cap": dqs_cap,
        "precision_allowed": dqs_cap >= 85 and not blocking_errors,
        "source_coverage_summary": (
            f"一级来源覆盖率{tier1_coverage:.0%}，关键指标覆盖率{critical_metric_coverage:.0%}；"
            + ("数据覆盖不足，禁止精确金额建议。" if low_coverage else "覆盖率达标。")
        ),
    }
    return audit


def apply_source_audit_to_market(market: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(market or {})
    quality = dict(adjusted.get("data_quality", {}) or {})
    existing_score = int(quality.get("score", 0) or 0)
    dqs_cap = int(audit.get("dqs_cap", 100) or 100)
    quality["score"] = min(existing_score, dqs_cap)
    quality["source_audit"] = {
        "scan_status": audit.get("scan_status"),
        "scan_complete": audit.get("scan_complete"),
        "tier1_coverage": audit.get("tier1_coverage", 0.0),
        "critical_metric_coverage": audit.get("critical_metric_coverage", 0.0),
        "source_coverage_summary": audit.get("source_coverage_summary", ""),
        "precision_allowed": audit.get("precision_allowed", False),
        "trade_evidence_ready": audit.get("trade_evidence_ready", False),
    }
    warnings = list(quality.get("warnings", []) or [])
    if audit.get("source_coverage_summary"):
        warnings.append(str(audit["source_coverage_summary"]))
    quality["warnings"] = list(dict.fromkeys(warnings))
    blocking_errors = list(quality.get("blocking_errors", []) or [])
    blocking_errors.extend(audit.get("blocking_errors", []) or [])
    quality["blocking_errors"] = list(dict.fromkeys(blocking_errors))
    if audit.get("blocking_errors"):
        quality["critical_missing"] = True
    adjusted["data_quality"] = quality
    adjusted["source_audit"] = audit
    return adjusted


def write_source_audit(audit: dict[str, Any], path: Path = AUDIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def build_and_write_source_audit(market: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = build_source_audit(market)
    write_source_audit(audit)
    adjusted_market = apply_source_audit_to_market(market, audit)
    if audit.get("scan_status") != "complete":
        write_log("全球权威数据扫描未完成，已降级DQS。", filename="source_audit.log")
    return adjusted_market, audit
