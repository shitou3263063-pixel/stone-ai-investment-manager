from __future__ import annotations

import hashlib
from typing import Any


def _id(category: str, affected: str, message: str) -> str:
    digest = hashlib.sha1(f"{category}|{affected}|{message}".encode("utf-8")).hexdigest()[:10]
    return f"ISSUE-{digest.upper()}"


def _issue(
    category: str,
    severity: str,
    affected: str,
    message: str,
    *,
    blocking: bool = False,
    source: str = "canonical_snapshot",
    action: str = "等待下次数据刷新并复核",
) -> dict[str, Any]:
    return {
        "issue_id": _id(category, affected, message),
        "category": category,
        "severity": severity,
        "affected_scenario": affected,
        "blocking": blocking,
        "message": message,
        "source": source,
        "suggested_action": action,
        "status": "OPEN",
    }


def build_issue_registry(decision: dict[str, Any]) -> dict[str, Any]:
    """Create the only user-visible error/warning inventory."""
    issues: list[dict[str, Any]] = []
    quality = decision.get("data_quality_snapshot") or decision.get("dqs", {}) or {}
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    comparability = decision.get("comparability", {}) or {}
    consistency = decision.get("consistency", {}) or {}
    for message in consistency.get("errors", []) or []:
        issues.append(_issue("CONSISTENCY_ERROR", "ERROR", "SYSTEM", str(message), blocking=True, source="consistency_check"))
    for message in consistency.get("warnings", []) or []:
        issues.append(_issue("CONSISTENCY_WARNING", "WARN", "SYSTEM", str(message), source="consistency_check"))
    for scope, rows in (quality.get("data_issues_by_scope", {}) or {}).items():
        for row in rows or []:
            status = str(row.get("data_status") or "DATA_INSUFFICIENT")
            message = f"{row.get('item')}: {status}"
            blocking = scope in {"scheduled_dca", "execution_reconciliation"} and status in {"DATA_INSUFFICIENT", "SOURCE_FAILED"}
            issues.append(_issue("DATA_QUALITY", "WARN", str(scope), message, blocking=blocking, source="data_quality_snapshot"))
    for row in snapshot.get("pending_valuation_assets", []) or []:
        name = str(row.get("official_symbol") or row.get("security_code") or row.get("security_name"))
        issues.append(_issue(
            "PENDING_VALUATION", "WARN", "STRATEGIC_REBALANCE",
            f"{name}待估值：{row.get('pending_reason') or row.get('valuation_status')}",
            blocking=True, source="portfolio_snapshot", action="取得官方收盘价与独立估值汇率后自动重算",
        ))
    for key, value in comparability.items():
        if key.endswith("_comparability") and value not in {"COMPARABLE", "NOT_EVALUATED"}:
            issues.append(_issue("COMPARABILITY", "WARN", key, f"{key}={value}", blocking=True, source="comparability_snapshot"))
    if str(((decision.get("risk_snapshot") or {}).get("market_risk") or {}).get("confidence") or "").lower() == "low":
        issues.append(_issue("RISK_CONFIDENCE", "WARN", "RISK_MONITORING", "市场风险置信度为low", source="risk_snapshot"))
    if decision.get("event_gate_result") in {"CONSERVATIVE_BLOCK", "PASS_WITH_LIMITATIONS"}:
        issues.append(_issue("EVENT_CALENDAR", "WARN", "EVENT_GATE", f"event_gate_result={decision.get('event_gate_result')}", source="macro_calendar"))
    unique = {item["issue_id"]: item for item in issues}
    rows = sorted(unique.values(), key=lambda item: (item["severity"] != "ERROR", item["issue_id"]))
    errors = [item for item in rows if item["severity"] == "ERROR"]
    warnings = [item for item in rows if item["severity"] == "WARN"]
    return {
        "issues": rows,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "blocking_count": sum(1 for item in rows if item["blocking"]),
        "errors": errors,
        "warnings": warnings,
    }


def refresh_issue_registry(decision: dict[str, Any]) -> dict[str, Any]:
    decision["issue_registry"] = build_issue_registry(decision)
    return decision
