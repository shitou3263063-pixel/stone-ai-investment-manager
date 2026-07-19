"""Public report API backed exclusively by FinalDecisionBundle renderers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.reports.bundle_report import (
    render_daily_report,
    render_period_report,
    render_portfolio_snapshot,
    render_today_action,
)


def _require_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("bundle_type") != "FinalDecisionBundle":
        raise TypeError("report_center requires FinalDecisionBundle")


def generate_daily_report(*, decision: dict[str, Any], **_: Any) -> str:
    _require_bundle(decision)
    return render_daily_report(decision)


def generate_today_action(decision: dict[str, Any]) -> str:
    _require_bundle(decision)
    return render_today_action(decision)


def generate_portfolio_snapshot_report(decision: dict[str, Any]) -> str:
    _require_bundle(decision)
    return render_portfolio_snapshot(decision)


def generate_weekly_report(decision: dict[str, Any]) -> str:
    _require_bundle(decision)
    return render_period_report(decision, "Weekly")


def generate_monthly_report(decision: dict[str, Any]) -> str:
    _require_bundle(decision)
    return render_period_report(decision, "Monthly")


def _allocation_table(bundle: dict[str, Any]) -> list[str]:
    _require_bundle(bundle)
    snapshot = bundle["portfolio_snapshot"]
    weights = snapshot.get("asset_class_weights", {}) or {}
    return [
        f"| {category} | {float(value or 0):,.0f}元 | {float(weights.get(category, 0) or 0):.2%} |"
        for category, value in (snapshot.get("asset_class_values", {}) or {}).items()
    ]


def build_run_status(
    decision: dict[str, Any],
    *, report_files: list[str], email_status: str,
    email_error: str = "",
) -> dict[str, Any]:
    _require_bundle(decision)
    snapshot = decision["portfolio_snapshot"]
    issues = decision["issues"]
    return {
        "run_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_cutoff_time": decision.get("data_cutoff_at"),
        "report_date": (decision.get("report_metadata", {}) or {}).get("report_business_date"),
        "status": "failed" if issues.get("blocking_count") else ("warning" if issues.get("warning_count") else "success"),
        "bundle_hash": decision["bundle_hash"],
        "dqs": {name: result["total"] for name, result in decision["dqs_results"].items()},
        "risk_score": (decision.get("risk_snapshot", {}) or {}).get("score"),
        "total_assets": snapshot.get("total_valued_assets"),
        "total_cash": (snapshot.get("cash", {}) or {}).get("account_total_cash_cny"),
        "cash_safety_reserve": snapshot.get("safety_cash"),
        "investable_cash": snapshot.get("investable_cash"),
        "today_action": {
            "execute": decision.get("scenario_decision_by_key", {}).get("scheduled_dca", {}).get("final_permission")
            in {"ALLOW_EXECUTION", "ALLOW_REDUCED_EXECUTION"},
            "targets": "manual review only",
            "amount_or_range": "0元",
        },
        "report_files": report_files,
        "email_status": email_status,
        "email_error": email_error,
        "warnings": issues.get("warning_reasons", []),
        "errors": issues.get("blocking_reasons", []),
    }
