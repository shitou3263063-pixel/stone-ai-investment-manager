from __future__ import annotations

from copy import deepcopy
from typing import Any


CANONICAL_PERMISSIONS = {
    "ALLOW_EXECUTION_REVIEW",
    "ALLOW_EVALUATION_ONLY",
    "ALLOW_MONITORING",
    "ALLOW_RECONCILIATION",
    "DENY",
}

LEGACY_PERMISSION_MAP = {
    "ALLOW_TRADE_SUBJECT_TO_MANUAL_CONFIRMATION": "ALLOW_EXECUTION_REVIEW",
    "ALLOW_REDUCED_REVIEW_ONLY": "ALLOW_EVALUATION_ONLY",
}


def finalize_permission_context(raw: dict[str, Any], *, today_trade: bool) -> dict[str, Any]:
    """Normalize scenario gates and choose a real current permission source.

    This layer cannot create an order.  ``ALLOW_EXECUTION_REVIEW`` means only
    that a human may review a proposal; it never means automatic execution.
    """
    context = deepcopy(raw)
    scenarios = context.get("scenarios", []) or []
    for row in scenarios:
        permission = LEGACY_PERMISSION_MAP.get(str(row.get("final_permission")), str(row.get("final_permission")))
        if permission not in CANONICAL_PERMISSIONS:
            permission = "DENY"
        row["final_permission"] = permission
        row["final_trade_permission"] = permission == "ALLOW_EXECUTION_REVIEW"
        row["manual_confirmation_required"] = permission in {"ALLOW_EXECUTION_REVIEW", "ALLOW_EVALUATION_ONLY"}
        row["blocking_reasons"] = list(dict.fromkeys(row.get("blocking_reasons", []) or []))
        row["warnings"] = list(dict.fromkeys(row.get("warnings", []) or []))
        row.setdefault("plan_gate", row.get("schedule_gate_passed", True))
        row.setdefault("cash_gate", row.get("cash_gate_passed", True))
        row.setdefault("risk_gate", row.get("risk_gate_passed", True))
        row.setdefault("event_gate", row.get("event_gate_passed", True))
        row.setdefault("comparability_gate", True)
    contexts = {row.get("scenario_key"): row for row in scenarios}
    context["contexts"] = contexts
    eligible = next((row for row in scenarios if row.get("final_permission") == "ALLOW_EXECUTION_REVIEW"), None)
    if today_trade and eligible:
        context["selected_scenario"] = eligible.get("scenario_key")
        context["global_final_permission"] = "ALLOW_EXECUTION_REVIEW"
        context["final_trade_permission"] = True
        context["final_trade_permission_source"] = eligible.get("scenario_name")
    else:
        context["selected_scenario"] = None
        context["global_final_permission"] = "DENY"
        context["final_trade_permission"] = False
        context["final_trade_permission_source"] = "NO_CURRENT_TRADE_SCENARIO"
    context["today_trade_permission"] = context["global_final_permission"]
    context["monitoring_permission"] = (contexts.get("risk_monitoring") or {}).get("final_permission", "DENY")
    context["reconciliation_permission"] = (contexts.get("transaction_reconciliation") or {}).get("final_permission", "DENY")
    context["automatic_trading_enabled"] = False
    return context
