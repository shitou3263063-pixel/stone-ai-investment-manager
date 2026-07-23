from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

from src.notifier.email_notifier import _build_message, send_daily_reports
from src.decision.v12_1_decision import is_dca_execution_day, load_strategy
from src.domain.final_decision_bundle import validate_final_decision_bundle
from src.pipeline import unified_pipeline
from src.report_session import get_report_session_context
from src.reports.report_center import build_run_status
from tests.test_final_decision_bundle import _fixture_bundle


def _context(session: str, value: str):
    return get_report_session_context(
        now=datetime.fromisoformat(value), environ={"STONE_REPORT_SESSION": session}
    )


def _mail_fixture(root: Path, session: str) -> Path:
    reports = root / session.lower()
    reports.mkdir(parents=True)
    for stem in ("today_action", "daily_report", "weekly_report"):
        (reports / f"{stem}_{session}.md").write_text(f"# {stem}", encoding="utf-8")
    status = build_run_status(_fixture_bundle(), report_files=[], email_status="pending")
    (reports / "run_status.json").write_text(
        json.dumps(status, ensure_ascii=False), encoding="utf-8"
    )
    (root / ".env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=465\nSMTP_USER=test@example.com\n"
        "SMTP_PASSWORD=password\nEMAIL_TO=receiver@example.com\n",
        encoding="utf-8",
    )
    return reports


def test_unset_session_defaults_to_regular_and_preserves_report_directory(tmp_path: Path) -> None:
    context = get_report_session_context(
        now=datetime.fromisoformat("2026-07-20T08:30:00+08:00"), environ={}
    )
    assert context.report_session == "REGULAR"
    assert context.output_dir(tmp_path) == tmp_path / "reports"
    assert context.report_filename("daily_report", ".md") == "daily_report.md"


def test_regular_cn_and_us_report_artifacts_do_not_overlap(tmp_path: Path) -> None:
    bundle = _fixture_bundle()
    validation = validate_final_decision_bundle(bundle)
    contexts = {
        "regular": get_report_session_context(
            now=datetime.fromisoformat("2026-07-20T08:30:00+08:00"), environ={}
        ),
        "cn": _context("CN_PREOPEN", "2026-07-20T08:35:00+08:00"),
        "us": _context("US_PREOPEN", "2026-07-20T08:40:00-04:00"),
    }
    for context in contexts.values():
        target = context.output_dir(tmp_path)
        assert unified_pipeline.write_report_artifacts(
            bundle, validation, reports=target, session_context=context
        ) is True

    assert (tmp_path / "reports" / "daily_report.md").exists()
    assert (tmp_path / "outputs" / "2026-07-20" / "cn_preopen" / "daily_report_CN_PREOPEN.md").exists()
    assert (tmp_path / "outputs" / "2026-07-20" / "us_preopen" / "daily_report_US_PREOPEN.md").exists()
    assert not (tmp_path / "reports" / "daily_report_CN_PREOPEN.md").exists()
    assert not (tmp_path / "reports" / "daily_report_US_PREOPEN.md").exists()


def test_beijing_monday_is_not_skipped_while_new_york_is_still_sunday() -> None:
    utc_now = datetime(2026, 7, 20, 0, 35, tzinfo=timezone.utc)
    context = get_report_session_context(
        now=utc_now, environ={"STONE_REPORT_SESSION": "CN_PREOPEN"}
    )
    assert context.local_now.weekday() == 0
    assert context.local_report_date.isoformat() == "2026-07-20"
    assert utc_now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).weekday() == 6


def test_us_0840_is_stable_in_daylight_and_standard_time() -> None:
    summer = get_report_session_context(
        now=datetime(2026, 7, 20, 12, 40, tzinfo=timezone.utc),
        environ={"STONE_REPORT_SESSION": "US_PREOPEN"},
    )
    winter = get_report_session_context(
        now=datetime(2026, 1, 5, 13, 40, tzinfo=timezone.utc),
        environ={"STONE_REPORT_SESSION": "US_PREOPEN"},
    )
    assert (summer.local_now.hour, summer.local_now.minute, summer.local_now.utcoffset().total_seconds()) == (8, 40, -14400)
    assert (winter.local_now.hour, winter.local_now.minute, winter.local_now.utcoffset().total_seconds()) == (8, 40, -18000)


def test_same_date_two_sessions_send_independently_and_have_distinct_subjects(tmp_path: Path) -> None:
    cn = _context("CN_PREOPEN", "2026-07-20T08:35:00+08:00")
    us = _context("US_PREOPEN", "2026-07-20T08:40:00-04:00")
    cn_reports = _mail_fixture(tmp_path, "CN_PREOPEN")
    us_reports = _mail_fixture(tmp_path, "US_PREOPEN")
    with patch("src.notifier.email_notifier._send_email") as sender:
        cn_result = send_daily_reports(
            cn_reports, cn.local_report_date, tmp_path / ".env",
            session_context=cn, dedupe_marker=tmp_path / "cn.json",
        )
        us_result = send_daily_reports(
            us_reports, us.local_report_date, tmp_path / ".env",
            session_context=us, dedupe_marker=tmp_path / "us.json",
        )
    assert sender.call_count == 2
    assert cn_result["subject"] == "Stone AI CIO Daily - 10%-15% Target | CN_PREOPEN A股开盘前 | 2026-07-20 | 手动运行"
    assert us_result["subject"] == "Stone AI CIO Daily - 10%-15% Target | US_PREOPEN 美股开盘前 | 2026-07-20 | 手动运行"


def test_same_session_second_run_is_deduplicated(tmp_path: Path) -> None:
    context = _context("CN_PREOPEN", "2026-07-20T08:35:00+08:00")
    reports = _mail_fixture(tmp_path, "CN_PREOPEN")
    marker = tmp_path / "mail-state" / "cn.json"
    with patch("src.notifier.email_notifier._send_email") as sender:
        first = send_daily_reports(reports, context.local_report_date, tmp_path / ".env", session_context=context, dedupe_marker=marker)
        second = send_daily_reports(reports, context.local_report_date, tmp_path / ".env", session_context=context, dedupe_marker=marker)
    assert first["sent"] is True and first["attempted"] is True
    assert second["sent"] is True and second["deduplicated"] is True
    assert second["attempted"] is False
    assert sender.call_count == 1


def test_output_directories_and_attachment_names_do_not_overlap(tmp_path: Path) -> None:
    cn = _context("CN_PREOPEN", "2026-07-20T08:35:00+08:00")
    us = _context("US_PREOPEN", "2026-07-20T08:40:00-04:00")
    assert cn.output_dir(tmp_path) == tmp_path / "outputs" / "2026-07-20" / "cn_preopen"
    assert us.output_dir(tmp_path) == tmp_path / "outputs" / "2026-07-20" / "us_preopen"
    path = tmp_path / "run_status.json"
    path.write_text("{}", encoding="utf-8")
    config = {"SMTP_USER": "a@example.com", "EMAIL_TO": "b@example.com"}
    cn_message = _build_message(config, "cn", "body", [path], None, "CN_PREOPEN")
    us_message = _build_message(config, "us", "body", [path], None, "US_PREOPEN")
    assert next(cn_message.iter_attachments()).get_filename() == "run_status_CN_PREOPEN.json"
    assert next(us_message.iter_attachments()).get_filename() == "run_status_US_PREOPEN.json"


def test_mail_failure_makes_main_exit_nonzero(tmp_path: Path) -> None:
    context = _context("CN_PREOPEN", "2026-07-20T08:35:00+08:00")
    status_path = context.output_dir(tmp_path) / "run_status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"status": "failed", "mail_sent": False}), encoding="utf-8")
    with (
        patch.object(unified_pipeline, "run_health_check", return_value={"can_run": True}),
        patch.object(unified_pipeline, "format_health_report", return_value="ok"),
        patch.object(unified_pipeline, "get_report_session_context", return_value=context),
        patch.object(unified_pipeline, "project_root", return_value=tmp_path),
        patch.object(unified_pipeline, "run", return_value="mail failed"),
    ):
        assert unified_pipeline.main() == 1


def test_weekday_market_holiday_is_not_sent_as_preopen() -> None:
    context = _context("CN_PREOPEN", "2026-10-01T08:35:00+08:00")
    assert context.local_now.weekday() < 5
    assert context.market_is_trading_day is False
    assert context.should_generate is False
    assert context.schedule_status == "SKIPPED_NON_TRADING_DAY"


def test_existing_holiday_policy_moves_scheduled_dca_to_next_open_day() -> None:
    strategy = load_strategy()
    assert is_dca_execution_day(
        datetime.fromisoformat("2026-10-08T08:35:00+08:00").date(),
        strategy,
        "CN_PREOPEN",
    ) is True
