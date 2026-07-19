from __future__ import annotations

from typing import Any


# This is the only scenario dependency matrix used by the permission engine.
# It describes data dependencies and business effects; it does not calculate
# portfolio values, DQS scores, or trading amounts.
SCENARIO_DEPENDENCIES: dict[str, dict[str, Any]] = {
    "scheduled_dca": {
        "required_data": ("core_market_data", "portfolio_data"),
        "optional_data": ("event_data",),
        "requires_event_data": False,
        "event_data_mode": "soft_warning",
        "risk_threshold": 70,
        "allowed_permissions": (
            "ALLOW_EXECUTION",
            "ALLOW_REDUCED_EXECUTION",
            "WARN",
            "DENY",
        ),
    },
    "opportunity_add": {
        "required_data": ("core_market_data", "portfolio_data", "event_data"),
        "optional_data": (),
        "requires_event_data": True,
        "event_data_mode": "hard_block",
        "risk_threshold": 50,
        "allowed_permissions": ("ALLOW_EXECUTION", "DENY"),
    },
    "strategic_rebalance": {
        "required_data": ("portfolio_data", "target_allocation"),
        "optional_data": ("event_data",),
        "requires_event_data": False,
        "event_data_mode": "evaluation_only",
        "risk_threshold": 70,
        "allowed_permissions": ("ALLOW_EVALUATION_ONLY", "DENY"),
    },
    "grid": {
        "required_data": ("realtime_market_data",),
        "optional_data": ("event_data", "live_grid_cash"),
        "requires_event_data": False,
        "requires_event_data_for_live": True,
        "event_data_mode": "block_live_allow_simulation",
        "risk_threshold": 50,
        "allowed_permissions": ("ALLOW_SIMULATION_ONLY", "WARN", "DENY"),
    },
    "risk_monitoring": {
        "required_data": ("any_valid_risk_or_portfolio_data",),
        "optional_data": ("event_data",),
        "requires_event_data": False,
        "event_data_mode": "warning_only",
        "risk_threshold": 100,
        "allowed_permissions": ("ACTIVE", "PARTIAL_MONITORING", "WARN", "DENY"),
    },
    "transaction_reconciliation": {
        "required_data": ("execution_data", "portfolio_snapshot"),
        "optional_data": (),
        "requires_event_data": False,
        "event_data_mode": "ignored",
        "risk_threshold": 100,
        "allowed_permissions": ("ALLOW_RECONCILIATION", "PASS", "WARN", "FAIL", "DENY"),
    },
}


def scenario_dependency(scenario: str) -> dict[str, Any]:
    try:
        return SCENARIO_DEPENDENCIES[scenario]
    except KeyError as exc:
        raise ValueError(f"Unknown scenario dependency: {scenario}") from exc
