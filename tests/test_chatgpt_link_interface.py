from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from src.reports.bundle_report import render_period_report
from src.notifier.email_notifier import DAILY_EMAIL_SUBJECT, _send_email, send_daily_reports
from src.report_session import get_report_session_context
from src.reports.report_center import build_run_status, generate_today_action
from tests.test_v12_5_freeze import _decision
from tests.test_final_decision_bundle import _fixture_bundle


FIXED_REPORTS = [
    "reports/today_action.md",
    "reports/daily_report.md",
    "reports/weekly_report.md",
    "reports/run_status.json",
]


def _write_email_fixture(root: Path) -> Path:
    reports = root / "reports"
    reports.mkdir()
    (reports / "today_action.md").write_text("# action", encoding="utf-8")
    (reports / "daily_report.md").write_text("# daily", encoding="utf-8")
    (reports / "weekly_report.md").write_text("# weekly", encoding="utf-8")
    status = build_run_status(_fixture_bundle(), report_files=FIXED_REPORTS, email_status="sent")
    (reports / "run_status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    (root / ".env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=465\nSMTP_USER=test@example.com\n"
        "SMTP_PASSWORD=app-password\nEMAIL_TO=receiver@example.com\n",
        encoding="utf-8",
    )
    return reports


def test_today_action_reads_final_bundle_only() -> None:
    bundle = _fixture_bundle()
    text = generate_today_action(bundle)
    assert bundle["bundle_hash"] in text
    for row in bundle["scenario_decisions"]:
        assert row["scenario_name"] in text
        assert row["final_permission"] in text


def test_run_status_contains_fixed_contract_and_bundle_hash() -> None:
    bundle = _fixture_bundle()
    status = build_run_status(bundle, report_files=FIXED_REPORTS, email_status="sent")
    required = {"run_time", "data_cutoff_time", "report_date", "status", "bundle_hash", "dqs", "risk_score", "total_assets", "total_cash", "cash_safety_reserve", "investable_cash", "today_action", "report_files", "email_status", "warnings", "errors"}
    assert required <= status.keys()
    assert status["bundle_hash"] == bundle["bundle_hash"]
    assert status["total_assets"] == bundle["portfolio_snapshot"]["total_valued_assets"]
    assert status["report_files"] == FIXED_REPORTS


def test_weekly_report_uses_final_bundle() -> None:
    bundle = {"bundle_type": "FinalDecisionBundle", "bundle_hash": "abc", "scenario_decisions": []}
    assert "FinalDecisionBundle: `abc`" in render_period_report(bundle, "Weekly")

def test_email_subject_body_and_attachments_are_fixed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = _write_email_fixture(root)
        context = get_report_session_context(
            now=datetime.fromisoformat("2026-07-20T18:30:00+08:00"),
            environ={"STONE_REPORT_SESSION": "REGULAR", "STONE_REPORT_TRIGGER": "SCHEDULED"},
        )
        with patch("src.notifier.email_notifier._send_email") as sender:
            result = send_daily_reports(reports_dir=reports, env_path=root / ".env", session_context=context)
        assert result["sent"] is True
        args = sender.call_args.args
        assert args[1] == f"{DAILY_EMAIL_SUBJECT} | REGULAR 常规日报 | 2026-07-20 | 定时运行"
        assert [path.name for path in args[3]] == [
            "today_action.md",
            "daily_report.md",
            "weekly_report.md",
            "run_status.json",
        ]
        for field in ["session:", "scheduled_for:", "generated_at:", "timezone:", "delivery_delay_minutes:", "报告日期", "数据截止时间", "今日是否执行", "标的和金额", "可投资现金", "下一复核日期", "DQS", "是否存在警告或错误"]:
            assert field in args[2]


def test_dual_timezone_email_subject_identifies_us_market_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = _write_email_fixture(root)
        context = get_report_session_context(
            now=datetime.fromisoformat("2026-07-20T08:30:00-04:00"),
            environ={"STONE_REPORT_SESSION": "US_PREOPEN", "STONE_REPORT_TRIGGER": "MANUAL"},
        )
        for stem in ("today_action", "daily_report", "weekly_report"):
            (reports / f"{stem}_US_PREOPEN.md").write_text("test", encoding="utf-8")
        with patch("src.notifier.email_notifier._send_email") as sender:
            result = send_daily_reports(reports_dir=reports, env_path=root / ".env", session_context=context)
        assert result["sent"] is True
        assert sender.call_args.args[1] == f"{DAILY_EMAIL_SUBJECT} | US_PREOPEN 美股开盘前 | 2026-07-20 | 手动运行"
        assert "session: US_PREOPEN" in sender.call_args.args[2]
        assert "trigger: MANUAL" in sender.call_args.args[2]


def test_github_daily_workflow_has_one_unambiguous_regular_schedule() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    assert workflow.count('cron: "30 10 * * *"') == 1
    assert "timezone:" not in workflow
    assert "STONE_REPORT_SESSION: REGULAR" in workflow
    assert "REPORT_RUN_LABEL" not in workflow
    assert "Run Stone AI Investment Manager Pro V12.7.1 Final Freeze" in workflow


def test_user_facing_repository_version_is_v12_7_1_final_freeze() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    stable_readme = (root / "README_STABLE.md").read_text(encoding="utf-8")
    deploy_check = (root / "scripts" / "deploy_check.py").read_text(encoding="utf-8")
    final_check = (root / "scripts" / "final_check.py").read_text(encoding="utf-8")
    project_audit = (root / "scripts" / "project_audit.py").read_text(encoding="utf-8")

    assert readme.startswith("# Stone AI Investment Manager Pro V12.7.1 Final Freeze")
    assert "config_version: V12.7.1_FINAL_FREEZE" in readme
    assert "当前生产版本为V12.7.1 Final Freeze" in readme
    assert stable_readme.startswith("# Stone AI Investment Manager Pro V12.7.1 Final Freeze")
    assert "Stone AI Investment Manager Pro V12.7.1 Final Freeze 部署前检查报告" in deploy_check
    assert "Stone AI Investment Manager Pro V12.7.1 Final Freeze 系统检查报告" in final_check
    assert "Stone AI Investment Manager Pro V12.7.1 Final Freeze" in project_audit


def test_four_attachments_have_readable_mime_payloads() -> None:
    class FakeSMTP:
        message = None

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, *args):
            return None

        def send_message(self, message):
            FakeSMTP.message = message

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = _write_email_fixture(root)
        attachments = [reports / name for name in ["today_action.md", "daily_report.md", "weekly_report.md", "run_status.json"]]
        config = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "test@example.com",
            "SMTP_PASSWORD": "password",
            "EMAIL_TO": "receiver@example.com",
        }
        with patch("src.notifier.email_notifier.smtplib.SMTP_SSL", FakeSMTP):
            _send_email(config, DAILY_EMAIL_SUBJECT, "body", attachments)
        parts = list(FakeSMTP.message.iter_attachments())
        assert [part.get_filename() for part in parts] == [path.name for path in attachments]
        assert parts[-1].get_content_type() == "application/json"
        assert json.loads(parts[-1].get_payload(decode=True).decode("utf-8"))


def test_email_failure_is_returned_without_deleting_reports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = _write_email_fixture(root)
        with patch("src.notifier.email_notifier._send_email", side_effect=OSError("smtp offline")):
            result = send_daily_reports(reports_dir=reports, env_path=root / ".env")
        assert result["sent"] is False
        assert result["skipped"] is False
        assert result["error"].startswith("UNKNOWN_ERROR [smtp_send]")
        assert "smtp offline" not in result["error"]
        for name in ["today_action.md", "daily_report.md", "weekly_report.md", "run_status.json"]:
            assert (reports / name).exists()
