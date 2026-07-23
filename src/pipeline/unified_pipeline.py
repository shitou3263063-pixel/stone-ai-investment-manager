from __future__ import annotations

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
from src.report_session import ReportSessionContext, get_report_session_context
from src.reports.bundle_report import render_daily_report, render_diagnostic_report, render_period_report, render_portfolio_snapshot, render_today_action
from src.system.health_check import format_health_report, run_health_check
from utils.data_loader import project_root
from utils.logger import write_log


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _record_schedule_skip(context: ReportSessionContext) -> str:
    message = (
        f"{context.schedule_status} session={context.report_session} "
        f"trigger={context.trigger_type} scheduled_for={context.scheduled_for.isoformat()} "
        f"generated_at={context.local_now.isoformat()} timezone={context.report_timezone}"
    )
    write_log(message, filename="report_schedule.log")
    return message


def build_bundle(
    snapshot: dict[str, Any] | None = None,
    *,
    session_context: ReportSessionContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_snapshot = snapshot or write_snapshot()
    context = build_context(source_snapshot)
    decision = context["decision"]
    if session_context is not None and session_context.report_session != "REGULAR":
        decision.setdefault("report_metadata", {}).update(session_context.as_dict())
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
    session_context: ReportSessionContext | None = None,
) -> bool:
    """Persist one validated bundle and render every report surface from it."""
    report_context = session_context or get_report_session_context(environ={})
    target = reports or report_context.output_dir(project_root())
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "final_decision_bundle.json", bundle)
    _write_json(target / "bundle_validation.json", validation)
    if not validation["ok"]:
        report_context.report_path(target, "error_diagnostic_report", ".md").write_text(
            render_diagnostic_report(bundle, validation), encoding="utf-8"
        )
        return False
    report_context.report_path(target, "today_action", ".md").write_text(render_today_action(bundle), encoding="utf-8")
    report_context.report_path(target, "daily_report", ".md").write_text(render_daily_report(bundle), encoding="utf-8")
    report_context.report_path(target, "portfolio_snapshot", ".md").write_text(render_portfolio_snapshot(bundle), encoding="utf-8")
    report_context.report_path(target, "weekly_report", ".md").write_text(render_period_report(bundle, "Weekly"), encoding="utf-8")
    report_context.report_path(target, "monthly_report", ".md").write_text(render_period_report(bundle, "Monthly"), encoding="utf-8")
    return True


def run(
    *,
    send_email: bool = True,
    snapshot: dict[str, Any] | None = None,
    session_context: ReportSessionContext | None = None,
) -> str:
    report_context = session_context or get_report_session_context()
    if not report_context.should_generate:
        return _record_schedule_skip(report_context)
    reports = report_context.output_dir(project_root())
    reports.mkdir(parents=True, exist_ok=True)
    bundle, validation = build_bundle(snapshot, session_context=report_context)
    if not write_report_artifacts(bundle, validation, reports=reports, session_context=report_context):
        return "FinalDecisionBundle validation failed; formal report was not generated."
    status = {
        "status": "success",
        "report_session": report_context.report_session,
        "report_label": report_context.report_label,
        "schedule": report_context.as_dict(),
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
        "report_files": [str(path) for path in report_context.email_attachment_paths(reports)],
    }
    _write_json(reports / "run_status.json", status)
    email = (
        send_daily_reports(
            reports_dir=reports,
            subject_date=report_context.local_report_date,
            session_context=report_context,
            dedupe_marker=report_context.delivery_marker(project_root()),
        )
        if send_email
        else {"sent": False, "skipped": True, "message": "email skipped"}
    )
    status["email"] = email
    status["mail_sent"] = bool(email.get("sent"))
    status["status"] = "success" if (email.get("sent") or email.get("skipped")) else "failed"
    _write_json(reports / "run_status.json", status)
    return f"{VERSION_NAME} completed; bundle={bundle['bundle_hash']}; email={email.get('message')}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report_context = get_report_session_context()
    if not report_context.should_generate:
        print(_record_schedule_skip(report_context))
        return 0
    health = run_health_check(auto_fix=True)
    print(format_health_report(health))
    if not health.get("can_run", False):
        return 1
    # Refresh after health checks so generated_at and delay reflect report production,
    # and re-apply the window if startup work crossed the deadline.
    report_context = get_report_session_context()
    if not report_context.should_generate:
        print(_record_schedule_skip(report_context))
        return 0
    result = run(send_email=True, session_context=report_context)
    print(result)
    try:
        status = json.loads((report_context.output_dir(project_root()) / "run_status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    return 0 if status.get("status") == "success" else 1
