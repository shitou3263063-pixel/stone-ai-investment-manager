from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


VOLATILE_KEYS = {
    "generated_at", "report_generated_at", "built_at", "fetched_at", "retrieved_at",
    "checked_at", "updated_at", "response_timestamp", "bundle_hash", "render_contract",
}


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in sorted(value.items()) if key not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def stable_hash(value: Any) -> str:
    payload = json.dumps(_stable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _count_categories(issue_registry: dict[str, Any], consistency: dict[str, Any]) -> dict[str, Any]:
    warnings = list(issue_registry.get("warnings", []) or [])
    blocking = list(issue_registry.get("blocking", []) or []) or [
        item for item in (issue_registry.get("issues", []) or []) if item.get("blocking")
    ]
    consistency_warnings = list(consistency.get("warnings", []) or [])
    return {
        "warning_reasons": warnings,
        "blocking_reasons": blocking,
        "consistency_warning_reasons": consistency_warnings,
        "warning_count": len(warnings),
        "blocking_count": len(blocking),
        "consistency_warning_count": len(consistency_warnings),
        "count_semantics": {
            "warning_count": "soft warnings only; is_hard_block=false",
            "blocking_count": "hard blocks only; is_hard_block=true",
            "consistency_warning_count": "cross-object consistency warnings only",
        },
    }


def build_final_decision_bundle(
    *, product_version: str, patch_level: str, market_snapshot: dict[str, Any],
    portfolio_snapshot: dict[str, Any], dqs_results: dict[str, dict[str, Any]],
    event_assessment: dict[str, Any], scenario_context: dict[str, Any], decision: dict[str, Any],
    issue_registry: dict[str, Any] | None = None, consistency: dict[str, Any] | None = None,
    grid: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the sole immutable business-result object for one production run."""
    issues = _count_categories(issue_registry or {}, consistency or {})
    scenario_decisions = deepcopy(scenario_context.get("scenarios", []) or [])
    input_snapshot_hash = stable_hash({"market": market_snapshot, "portfolio": portfolio_snapshot})
    bundle: dict[str, Any] = {
        "bundle_type": "FinalDecisionBundle", "schema_version": 1,
        "product_version": product_version, "patch_level": patch_level,
        "data_cutoff_at": market_snapshot.get("as_of"), "input_snapshot_hash": input_snapshot_hash,
        "market_snapshot": deepcopy(market_snapshot), "portfolio_snapshot": deepcopy(portfolio_snapshot),
        "dqs_results": deepcopy(dqs_results), "event_assessment": deepcopy(event_assessment),
        "scenario_decisions": scenario_decisions,
        "scenario_decision_by_key": {row["scenario"]: row for row in scenario_decisions},
        "asset_allocation": deepcopy(decision.get("allocation", []) or []),
        "risk_snapshot": deepcopy(decision.get("risk_snapshot") or decision.get("risk", {}) or {}),
        "comparability": deepcopy(decision.get("comparability", {}) or {}), "issues": issues,
        "grid_simulation": deepcopy(grid or {}), "report_metadata": deepcopy(decision.get("report_metadata", {}) or {}),
        "report_data": {key: deepcopy(value) for key, value in decision.items() if key not in {
            "portfolio_snapshot", "data_quality_snapshot", "dqs", "risk_snapshot", "risk",
            "decision_context", "trade_permission_gates", "allocation", "comparability", "grid",
        }},
    }
    bundle["bundle_hash"] = stable_hash(bundle)
    bundle["render_contract"] = {"main_bundle_hash": bundle["bundle_hash"], "appendix_bundle_hash": bundle["bundle_hash"]}
    return bundle


def validate_final_decision_bundle(bundle: dict[str, Any], *, tolerance: float = 0.02) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, bool] = {}
    dqs_ok = all(int(result.get("total", 0)) == sum(int(row.get("score", 0)) for row in result.get("breakdown", []) or []) for result in (bundle.get("dqs_results", {}) or {}).values())
    checks["dqs_total_equals_breakdown"] = dqs_ok
    if not dqs_ok: errors.append("DQS total does not equal breakdown sum")
    portfolio = bundle.get("portfolio_snapshot", {}) or {}
    positions = portfolio.get("positions", portfolio.get("holdings", [])) or []
    ids = [str(row.get("security_id") or "") for row in positions]
    unique_ok = bool(all(ids)) and len(ids) == len(set(ids))
    checks["positions_unique_by_security_id"] = unique_ok
    if not unique_ok: errors.append("Final positions contain duplicate or empty security_id")
    asset_values = portfolio.get("asset_class_values", {}) or {}
    asset_total_ok = abs(sum(float(value or 0) for value in asset_values.values()) - float(portfolio.get("total_valued_assets", 0) or 0)) <= tolerance
    checks["asset_totals_reconcile"] = asset_total_ok
    if not asset_total_ok: errors.append("Asset-class values do not reconcile to total_valued_assets")
    precise_assets = float(portfolio.get("precise_valued_assets", portfolio.get("precise_market_value", 0)) or 0)
    stale_assets = float(portfolio.get("stale_valued_assets", 0) or 0)
    unvalued_cost = float(portfolio.get("unvalued_cost_records", portfolio.get("pending_valuation_total", 0)) or 0)
    household_estimated = float(
        portfolio.get("household_total_assets_estimated", portfolio.get("household_total_assets", 0)) or 0
    )
    expected_coverage = precise_assets / household_estimated if household_estimated else 1.0
    valuation_total_ok = (
        abs(precise_assets + stale_assets + unvalued_cost - household_estimated) <= tolerance
        and abs(float(portfolio.get("valuation_coverage_ratio", expected_coverage) or 0) - expected_coverage) <= 1e-8
    )
    checks["valuation_totals_and_coverage_reconcile"] = valuation_total_ok
    if not valuation_total_ok: errors.append("Estimated, precise, stale, cost-record totals or valuation coverage do not reconcile")
    investable_values = portfolio.get("investable_asset_class_values", {}) or {}
    investable_total = float(portfolio.get("investable_portfolio_assets", 0) or 0)
    investable_ok = (
        not investable_values
        or abs(sum(float(value or 0) for value in investable_values.values()) - investable_total) <= tolerance
    )
    safety_reserve = float(portfolio.get("household_safety_reserve", portfolio.get("safety_cash", 0)) or 0)
    household_cash = float(asset_values.get("现金", 0) or 0)
    portfolio_cash = float(portfolio.get("portfolio_cash", portfolio.get("investable_cash", 0)) or 0)
    safety_excluded = portfolio_cash <= max(0.0, household_cash - safety_reserve) + tolerance
    checks["investable_portfolio_totals_reconcile"] = investable_ok and safety_excluded
    if not checks["investable_portfolio_totals_reconcile"]:
        errors.append("Investable portfolio denominator includes excluded household cash or does not reconcile")
    cost_ok = all(not row.get("is_cost_record") for row in positions)
    checks["cost_records_excluded_from_market_value"] = cost_ok
    if not cost_ok: errors.append("Cost record entered final positions")
    decisions = bundle.get("scenario_decisions", []) or []
    deny_ok = all(row.get("final_permission") != "DENY" or bool(row.get("rejection_reasons")) for row in decisions)
    checks["deny_requires_rejection_reason"] = deny_ok
    if not deny_ok: errors.append("Denied scenario has no rejection reason")
    allow_ok = all(
        row.get("final_permission") == "DENY" or not bool(row.get("rejection_reasons"))
        for row in decisions
    )
    checks["allowed_has_no_failed_gate"] = allow_ok
    if not allow_ok: errors.append("Non-denied scenario contains a hard-block reason")
    event = bundle.get("event_assessment", {}) or {}
    event_ok = not (event.get("status") in {"DATA_INSUFFICIENT", "SOURCE_ERROR"} and event.get("event_gate_passed"))
    checks["event_insufficient_cannot_pass"] = event_ok
    if not event_ok: errors.append("Insufficient/error event data silently passed")
    event_missing_ok = event.get("status") != "DATA_INSUFFICIENT" or bool(event.get("missing_data"))
    checks["event_insufficient_has_audit_details"] = event_missing_ok
    if not event_missing_ok: errors.append("Insufficient event data has no source/field audit detail")
    scenario_event_ok = True
    for row in decisions:
        mode = str(row.get("event_data_mode") or "")
        permission = str(row.get("final_permission") or "")
        canonical_gate = bool(event.get("event_gate_passed"))
        if mode == "hard_block":
            scenario_event_ok = scenario_event_ok and bool(row.get("event_gate_applicable"))
            scenario_event_ok = scenario_event_ok and bool(row.get("event_gate_passed")) == canonical_gate
            if not canonical_gate:
                scenario_event_ok = scenario_event_ok and permission == "DENY"
        elif mode == "ignored":
            scenario_event_ok = scenario_event_ok and not bool(row.get("event_gate_applicable"))
            event_blocks = any("事件" in str(reason) for reason in row.get("rejection_reasons", []) or [])
            scenario_event_ok = scenario_event_ok and not event_blocks
        elif not canonical_gate:
            event_blocks = any("事件" in str(reason) and "硬阻断" in str(reason) for reason in row.get("rejection_reasons", []) or [])
            scenario_event_ok = scenario_event_ok and not event_blocks
    checks["scenario_event_gates_use_dependency_policy"] = scenario_event_ok
    if not scenario_event_ok: errors.append("A ScenarioDecision violated its canonical event dependency policy")
    indexed = bundle.get("scenario_decision_by_key", {}) or {}
    scenario_index_ok = len(indexed) == len(decisions) and all(indexed.get(row.get("scenario")) == row for row in decisions)
    checks["all_permission_surfaces_share_scenario_decision"] = scenario_index_ok
    if not scenario_index_ok: errors.append("Scenario permission surfaces do not share the canonical ScenarioDecision")
    contract = bundle.get("render_contract", {}) or {}
    report_hash_ok = contract.get("main_bundle_hash") == bundle.get("bundle_hash") == contract.get("appendix_bundle_hash")
    checks["main_and_appendix_share_bundle_hash"] = report_hash_ok
    if not report_hash_ok: errors.append("Main report and appendix do not share the bundle hash")
    grid = bundle.get("grid_simulation", {}) or {}
    simulation_ok = not grid.get("real_trade") and float(portfolio.get("simulation_assets_cny", 0) or 0) == 0
    checks["simulation_excluded_from_real_portfolio"] = simulation_ok
    if not simulation_ok: errors.append("Simulation assets leaked into the real portfolio")
    states_ok = all("market_state" in row and "freshness_state" in row and "comparability_state" in row for group in (bundle.get("market_snapshot", {}).get("market", {}), bundle.get("market_snapshot", {}).get("macro", {})) for row in group.values())
    checks["market_freshness_comparability_are_independent"] = states_ok
    if not states_ok: errors.append("Market/freshness/comparability states are not independently represented")
    issues = bundle.get("issues", {}) or {}
    count_ok = (
        all(key in issues for key in ("warning_count", "blocking_count", "consistency_warning_count"))
        and set((issues.get("count_semantics", {}) or {}).keys())
        == {"warning_count", "blocking_count", "consistency_warning_count"}
        and int(issues.get("warning_count", -1)) == len(issues.get("warning_reasons", []) or [])
        and int(issues.get("blocking_count", -1)) == len(issues.get("blocking_reasons", []) or [])
        and int(issues.get("consistency_warning_count", -1))
        == len(issues.get("consistency_warning_reasons", []) or [])
        and not (
            {str(item.get("issue_id")) for item in issues.get("warning_reasons", []) or []}
            & {str(item.get("issue_id")) for item in issues.get("blocking_reasons", []) or []}
        )
    )
    checks["warning_counts_have_distinct_semantics"] = count_ok
    if not count_ok: errors.append("Warning/blocking/consistency counts are not distinct")
    risk_snapshot = bundle.get("risk_snapshot", {}) or {}
    market_risk = risk_snapshot.get("market_risk", {}) or risk_snapshot
    risk_components = market_risk.get("components", []) or []
    risk_sum_ok = not risk_components or sum(int(row.get("score", 0) or 0) for row in risk_components) == int(
        market_risk.get("score", risk_snapshot.get("score", 0)) or 0
    )
    checks["risk_score_equals_component_contributions"] = risk_sum_ok
    if not risk_sum_ok: errors.append("Risk score does not equal the sum of component contributions")
    dqs_missing_ok = all(
        str(row.get("reason") or "") != "DATA_INSUFFICIENT" or bool(row.get("missing_data"))
        for result in (bundle.get("dqs_results", {}) or {}).values()
        for row in result.get("breakdown", []) or []
    )
    checks["dqs_insufficient_has_missing_details"] = dqs_missing_ok
    if not dqs_missing_ok: errors.append("DATA_INSUFFICIENT DQS row has missing_data=empty")
    audit = portfolio.get("valuation_audit", {}) or {}
    rebalance_rows = ((bundle.get("dqs_results", {}) or {}).get("rebalance_dqs", {}) or {}).get("breakdown", []) or []
    timeliness = next((row for row in rebalance_rows if row.get("item") == "持仓时效"), {})
    valuation_dqs_ok = bool(audit.get("complete", True)) or int(timeliness.get("score", 0) or 0) < int(timeliness.get("max", 30) or 30)
    checks["incomplete_valuation_cannot_get_full_rebalance_timeliness"] = valuation_dqs_ok
    if not valuation_dqs_ok: errors.append("Incomplete valuation audit received full rebalance timeliness score")
    hash_ok = stable_hash({key: value for key, value in bundle.items() if key not in {"bundle_hash", "render_contract"}}) == bundle.get("bundle_hash")
    checks["bundle_hash_valid"] = hash_ok
    if not hash_ok: errors.append("FinalDecisionBundle hash validation failed")
    return {"ok": not errors, "status": "PASS" if not errors else "FAIL", "errors": errors, "checks": checks}
