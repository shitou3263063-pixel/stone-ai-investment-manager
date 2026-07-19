from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

from scripts.build_daily_snapshot import write_snapshot
from src.pipeline.context_builder import build_context
from src.decision.issue_registry import build_issue_registry
from src.decision.v12_1_decision import VERSION_NAME
from src.domain.final_decision_bundle import build_final_decision_bundle, validate_final_decision_bundle
from src.domain.market_snapshot import build_market_snapshot
from src.notifier.email_notifier import send_daily_reports
from src.reports.bundle_report import render_daily_report, render_diagnostic_report, render_period_report, render_portfolio_snapshot, render_today_action
from src.system.health_check import format_health_report, run_health_check
from utils.data_loader import project_root


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def build_bundle(snapshot: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    source_snapshot = snapshot or write_snapshot()
    context = build_context(source_snapshot)
    decision = context["decision"]
    market = build_market_snapshot(
        context["live_market_result"],
        decision_cutoff_at=str(decision.get("decision_cutoff_at") or source_snapshot.get("decision_cutoff_time")),
    )
    event = context["event_assessment"]
    issues = build_issue_registry(decision)
    bundle = build_final_decision_bundle(
        product_version=VERSION_NAME,
        patch_level="root_fix_1",
        market_snapshot=market,
        portfolio_snapshot=decision["portfolio_snapshot"],
        dqs_results=decision["dqs"]["dqs_results"],
        event_assessment=event,
        scenario_context=decision["decision_context"],
        decision=decision,
        issue_registry=issues,
        consistency=decision.get("consistency", {}),
        grid=decision.get("grid", {}),
    )
    return bundle, validate_final_decision_bundle(bundle)


def write_report_artifacts(
    bundle: dict[str, Any],
    validation: dict[str, Any],
    *,
    reports: Path | None = None,
) -> bool:
    """Persist one validated bundle and render every report surface from it."""
    target = reports or project_root() / "reports"
    target.mkdir(exist_ok=True)
    _write_json(target / "final_decision_bundle.json", bundle)
    _write_json(target / "bundle_validation.json", validation)
    if not validation["ok"]:
        (target / "error_diagnostic_report.md").write_text(
            render_diagnostic_report(bundle, validation), encoding="utf-8"
        )
        return False
    (target / "today_action.md").write_text(render_today_action(bundle), encoding="utf-8")
    (target / "daily_report.md").write_text(render_daily_report(bundle), encoding="utf-8")
    (target / "portfolio_snapshot.md").write_text(render_portfolio_snapshot(bundle), encoding="utf-8")
    (target / "weekly_report.md").write_text(render_period_report(bundle, "Weekly"), encoding="utf-8")
    (target / "monthly_report.md").write_text(render_period_report(bundle, "Monthly"), encoding="utf-8")
    return True


def run(*, send_email: bool = True, snapshot: dict[str, Any] | None = None) -> str:
    reports = project_root() / "reports"
    reports.mkdir(exist_ok=True)
    bundle, validation = build_bundle(snapshot)
    if not write_report_artifacts(bundle, validation, reports=reports):
        return "FinalDecisionBundle validation failed; formal report was not generated."
    status = {
        "status": "success",
        "bundle_hash": bundle["bundle_hash"], "validation": validation,
        "email": {"sent": False, "skipped": not send_email, "message": "pending" if send_email else "email skipped"},
        "report_date": (bundle.get("report_metadata", {}) or {}).get("report_business_date"),
        "data_cutoff_time": bundle.get("data_cutoff_at"),
        "investable_cash": bundle["portfolio_snapshot"].get("investable_cash", 0),
        "dqs": {name: result["total"] for name, result in bundle["dqs_results"].items()},
        "today_action": {
            "execute": bundle["scenario_decision_by_key"]["scheduled_dca"]["final_permission"]
            in {"ALLOW_EXECUTION", "ALLOW_REDUCED_EXECUTION"},
            "targets": "manual review only", "amount_or_range": "0元",
        },
        "warnings": bundle["issues"].get("warning_reasons", []),
        "errors": bundle["issues"].get("blocking_reasons", []),
    }
    _write_json(reports / "run_status.json", status)
    email = send_daily_reports(reports_dir=reports, subject_date=date.today()) if send_email else {"sent": False, "skipped": True, "message": "email skipped"}
    status["email"] = email
    status["status"] = "success" if (email.get("sent") or email.get("skipped")) else "failed"
    _write_json(reports / "run_status.json", status)
    return f"{VERSION_NAME} completed; bundle={bundle['bundle_hash']}; email={email.get('message')}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    health = run_health_check(auto_fix=True)
    print(format_health_report(health))
    if not health.get("can_run", False):
        return 1
    result = run(send_email=True)
    print(result)
    try:
        status = json.loads((project_root() / "reports" / "run_status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    return 0 if status.get("status") == "success" else 1
