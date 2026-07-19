from __future__ import annotations

from copy import deepcopy
from typing import Any


DQS_BINDINGS = {
    "scheduled_dca": "core_dqs",
    "opportunity_add": "opportunity_dqs",
    "strategic_rebalance": "rebalance_dqs",
    "grid": "grid_dqs",
    "risk_monitoring": "core_dqs",
    "transaction_reconciliation": "execution_dqs",
}


def build_dqs_result(name: str, breakdown: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = []
    for item in deepcopy(breakdown):
        score = int(item.get("score", 0) or 0)
        normalized.append({**item, "score": score})
    return {
        "name": name,
        "total": sum(item["score"] for item in normalized),
        "breakdown": normalized,
    }


def build_dqs_results(component_scores: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    required = {"core_dqs", "opportunity_dqs", "execution_dqs", "rebalance_dqs", "grid_dqs"}
    missing = required - set(component_scores)
    if missing:
        raise ValueError(f"Missing DQS breakdowns: {sorted(missing)}")
    return {name: build_dqs_result(name, component_scores[name]) for name in sorted(required)}


def dqs_totals(results: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {name: int(result["total"]) for name, result in results.items()}
