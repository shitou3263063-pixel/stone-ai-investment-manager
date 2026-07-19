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
    action: str = "等待下一次数据刷新并复核",
    affected_scenarios: list[str] | None = None,
) -> dict[str, Any]:
    scenarios = affected_scenarios or [affected]
    issue_id = _id(category, affected, message)
    return {
        # New report contract.
        "warning_id": issue_id,
        "severity": severity,
        "scope": affected,
        "message": message,
        "affected_scenarios": scenarios,
        "is_hard_block": blocking,
        "recommended_action": action,
        # Compatibility field names point to the same fact; they do not create
        # a second issue or a second decision path.
        "issue_id": issue_id,
        "category": category,
        "affected_scenario": affected,
        "blocking": blocking,
        "source": source,
        "suggested_action": action,
        "status": "OPEN",
    }


def _is_event_item(item: Any) -> bool:
    text = str(item or "").lower()
    return "event" in text or "事件" in text


def build_issue_registry(decision: dict[str, Any]) -> dict[str, Any]:
    """Create the sole user-visible warning and blocking inventory."""
    issues: list[dict[str, Any]] = []
    quality = decision.get("data_quality_snapshot") or decision.get("dqs", {}) or {}
    snapshot = decision.get("portfolio_snapshot", {}) or {}
    comparability = decision.get("comparability", {}) or {}
    consistency = decision.get("consistency", {}) or {}
    scenario_context = (decision.get("decision_context", {}) or {}).get("contexts", {}) or {}

    for message in consistency.get("errors", []) or []:
        issues.append(
            _issue(
                "CONSISTENCY_ERROR",
                "ERROR",
                "SYSTEM",
                str(message),
                blocking=True,
                source="consistency_check",
                action="修复一致性错误后重新生成正式日报",
            )
        )
    for message in consistency.get("warnings", []) or []:
        issues.append(
            _issue(
                "CONSISTENCY_WARNING",
                "WARN",
                "SYSTEM",
                str(message),
                source="consistency_check",
            )
        )

    for scope, rows in (quality.get("data_issues_by_scope", {}) or {}).items():
        normalized_scope = (
            "transaction_reconciliation" if scope == "execution_reconciliation" else str(scope)
        )
        for row in rows or []:
            # Event effects are emitted from the canonical ScenarioDecision below,
            # where the dependency mode is known. This avoids a global event issue.
            if _is_event_item(row.get("item")):
                continue
            status = str(row.get("data_status") or "DATA_INSUFFICIENT")
            message = f"{row.get('item')}: {status}"
            context = scenario_context.get(normalized_scope, {}) or {}
            blocking = bool(
                status in {"DATA_INSUFFICIENT", "SOURCE_FAILED"}
                and context.get("final_permission") == "DENY"
            )
            issues.append(
                _issue(
                    "DATA_QUALITY",
                    "WARN",
                    normalized_scope,
                    message,
                    blocking=blocking,
                    source="data_quality_snapshot",
                )
            )

    for row in snapshot.get("pending_valuation_assets", []) or []:
        name = str(
            row.get("security_id")
            or row.get("official_symbol")
            or row.get("security_code")
            or row.get("security_name")
        )
        issues.append(
            _issue(
                "PENDING_VALUATION",
                "WARN",
                "strategic_rebalance",
                f"{name}待估值：{row.get('pending_reason') or row.get('valuation_status')}",
                blocking=True,
                source="portfolio_snapshot",
                action="取得有效收盘价与独立估值汇率后自动重算",
            )
        )

    for key, value in comparability.items():
        if key.endswith("_comparability") and value not in {"COMPARABLE", "NOT_EVALUATED"}:
            affected = {
                "core_decision_comparability": ["scheduled_dca", "strategic_rebalance"],
                "cross_asset_comparability": ["opportunity_add"],
                "grid_snapshot_comparability": ["grid"],
            }.get(key, [key])
            blocking = any(
                (scenario_context.get(name, {}) or {}).get("final_permission") == "DENY"
                for name in affected
            )
            issues.append(
                _issue(
                    "COMPARABILITY",
                    "WARN",
                    key,
                    f"{key}={value}",
                    blocking=blocking,
                    source="comparability_snapshot",
                    affected_scenarios=affected,
                )
            )

    if str(((decision.get("risk_snapshot") or {}).get("market_risk") or {}).get("confidence") or "").lower() == "low":
        issues.append(
            _issue(
                "RISK_CONFIDENCE",
                "WARN",
                "risk_monitoring",
                "市场风险置信度为low",
                source="risk_snapshot",
            )
        )

    # ScenarioDecision is the only source for decision warnings and blockers.
    for scenario, context in scenario_context.items():
        for message in context.get("warning_reasons", []) or []:
            issues.append(
                _issue(
                    "DECISION_WARNING",
                    "WARN",
                    scenario,
                    str(message),
                    source="scenario_decision",
                    affected_scenarios=[scenario],
                    action="按场景下一步动作复核，不自动交易",
                )
            )
        for message in context.get("rejection_reasons", []) or []:
            issues.append(
                _issue(
                    "DECISION_BLOCK",
                    "WARN",
                    scenario,
                    str(message),
                    blocking=True,
                    source="scenario_decision",
                    affected_scenarios=[scenario],
                    action="满足该场景硬门槛后重新评估",
                )
            )
        for message in context.get("live_rejection_reasons", []) or []:
            issues.append(
                _issue(
                    "LIVE_EXECUTION_BLOCK",
                    "WARN",
                    scenario,
                    str(message),
                    blocking=True,
                    source="scenario_decision",
                    affected_scenarios=[f"{scenario}:live"],
                    action="保持模拟与实盘隔离；满足实盘条件后再人工复核",
                )
            )

    unique = {item["issue_id"]: item for item in issues}
    rows = sorted(unique.values(), key=lambda item: (item["severity"] != "ERROR", item["issue_id"]))
    errors = [item for item in rows if item["severity"] == "ERROR"]
    warnings = [item for item in rows if item["severity"] == "WARN" and not item["blocking"]]
    blocking = [item for item in rows if item["blocking"]]
    return {
        "issues": rows,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "blocking_count": len(blocking),
        "errors": errors,
        "warnings": warnings,
        "blocking": blocking,
    }


def refresh_issue_registry(decision: dict[str, Any]) -> dict[str, Any]:
    decision["issue_registry"] = build_issue_registry(decision)
    return decision
