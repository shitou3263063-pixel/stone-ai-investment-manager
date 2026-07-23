from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

from src.domain.final_decision_bundle import validate_final_decision_bundle
from src.notifier.email_notifier import send_daily_reports
from src.pipeline import unified_pipeline
from src.report_session import get_report_session_context
from tests.test_final_decision_bundle import _fixture_bundle


def _context(session: str, value: str, trigger: str = "SCHEDULED"):
    return get_report_session_context(
        now=datetime.fromisoformat(value),
        environ={"STONE_REPORT_SESSION": session, "STONE_REPORT_TRIGGER": trigger},
    )


def _mail_fixture(root: Path, context) -> Path:
    reports = context.output_dir(root)
    reports.mkdir(parents=True)
    for path in context.email_attachment_paths(reports):
        if path.name == "run_status.json":
            path.write_text(
                json.dumps(
                    {
                        "report_date": context.local_report_date.isoformat(),
                        "data_cutoff_time": "2026-07-20T08:00:00+08:00",
                        "today_action": {"execute": False},
                        "investable_cash": 0,
                        "dqs": {},
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_text("test", encoding="utf-8")
    (root / ".env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=465\nSMTP_USER=test@example.com\n"
        "SMTP_PASSWORD=password\nEMAIL_TO=receiver@example.com\n",
        encoding="utf-8",
    )
    return reports


def test_cn_preopen_0830_is_ready_but_1741_is_skipped() -> None:
    ready = _context("CN_PREOPEN", "2026-07-20T08:30:00+08:00")
    late = _context("CN_PREOPEN", "2026-07-20T17:41:00+08:00")
    assert ready.schedule_status == "READY_SCHEDULED"
    assert ready.should_generate is True
    assert late.schedule_status == "SKIPPED_OUTSIDE_WINDOW"
    assert late.should_generate is False


def test_us_preopen_0830_is_ready_but_1304_is_skipped() -> None:
    ready = _context("US_PREOPEN", "2026-07-20T08:30:00-04:00")
    late = _context("US_PREOPEN", "2026-07-20T13:04:00-04:00")
    assert ready.schedule_status == "READY_SCHEDULED"
    assert late.schedule_status == "SKIPPED_OUTSIDE_WINDOW"


def test_us_schedule_converts_to_utc_for_edt_and_est() -> None:
    summer = _context("US_PREOPEN", "2026-07-20T08:30:00-04:00")
    winter = _context("US_PREOPEN", "2026-01-05T08:30:00-05:00")
    assert summer.scheduled_for.astimezone(timezone.utc).isoformat() == "2026-07-20T12:30:00+00:00"
    assert winter.scheduled_for.astimezone(timezone.utc).isoformat() == "2026-01-05T13:30:00+00:00"
    assert _context("US_PREOPEN", "2026-07-20T09:30:00-04:00").should_generate is False
    assert _context("US_PREOPEN", "2026-01-05T07:30:00-05:00").should_generate is False


def test_manual_run_is_explicit_and_does_not_fake_preopen_window() -> None:
    regular = _context("REGULAR", "2026-07-20T12:00:00+08:00", "MANUAL")
    preopen = _context("CN_PREOPEN", "2026-07-20T12:00:00+08:00", "MANUAL")
    assert regular.is_manual is True
    assert regular.schedule_status == "READY_MANUAL"
    assert preopen.is_manual is True
    assert preopen.schedule_status == "SKIPPED_OUTSIDE_WINDOW"


def test_delayed_pipeline_run_generates_no_report_or_email() -> None:
    context = _context("CN_PREOPEN", "2026-07-20T17:41:00+08:00")
    with (
        patch.object(unified_pipeline, "build_bundle") as build_bundle,
        patch.object(unified_pipeline, "send_daily_reports") as send_mail,
        patch.object(unified_pipeline, "write_log"),
    ):
        result = unified_pipeline.run(session_context=context)
    assert result.startswith("SKIPPED_OUTSIDE_WINDOW")
    build_bundle.assert_not_called()
    send_mail.assert_not_called()


def test_all_three_sessions_have_independent_dedupe_keys_and_markers(tmp_path: Path) -> None:
    contexts = [
        _context("REGULAR", "2026-07-20T18:30:00+08:00"),
        _context("CN_PREOPEN", "2026-07-20T08:30:00+08:00"),
        _context("US_PREOPEN", "2026-07-20T08:30:00-04:00"),
    ]
    assert len({context.dedupe_key for context in contexts}) == 3
    assert len({context.delivery_marker(tmp_path) for context in contexts}) == 3


def test_regular_email_is_deduplicated_for_same_session_date(tmp_path: Path) -> None:
    context = _context("REGULAR", "2026-07-20T18:30:00+08:00")
    reports = _mail_fixture(tmp_path, context)
    marker = context.delivery_marker(tmp_path)
    with patch("src.notifier.email_notifier._send_email") as sender:
        first = send_daily_reports(
            reports, context.local_report_date, tmp_path / ".env",
            session_context=context, dedupe_marker=marker,
        )
        second = send_daily_reports(
            reports, context.local_report_date, tmp_path / ".env",
            session_context=context, dedupe_marker=marker,
        )
    assert first["sent"] is True and first["attempted"] is True
    assert second["deduplicated"] is True and second["attempted"] is False
    assert sender.call_count == 1


def test_email_metadata_matches_actual_session_and_schedule(tmp_path: Path) -> None:
    context = _context("US_PREOPEN", "2026-07-20T08:42:00-04:00")
    reports = _mail_fixture(tmp_path, context)
    with patch("src.notifier.email_notifier._send_email") as sender:
        result = send_daily_reports(reports, env_path=tmp_path / ".env", session_context=context)
    subject = sender.call_args.args[1]
    body = sender.call_args.args[2]
    assert result["sent"] is True
    assert "US_PREOPEN 美股开盘前" in subject
    assert "session: US_PREOPEN" in body
    assert "scheduled_for: 2026-07-20T08:30:00-04:00" in body
    assert "generated_at: 2026-07-20T08:42:00-04:00" in body
    assert "timezone: America/New_York" in body
    assert "delivery_delay_minutes: 12.00" in body


def test_outside_window_email_is_suppressed_before_smtp(tmp_path: Path) -> None:
    context = _context("US_PREOPEN", "2026-07-20T13:04:00-04:00")
    with patch("src.notifier.email_notifier._send_email") as sender:
        result = send_daily_reports(env_path=tmp_path / ".env", session_context=context)
    assert result["schedule_status"] == "SKIPPED_OUTSIDE_WINDOW"
    assert result["attempted"] is False
    sender.assert_not_called()


def test_regular_report_rendering_does_not_change_business_bundle(tmp_path: Path) -> None:
    context = _context("REGULAR", "2026-07-20T18:30:00+08:00")
    bundle = _fixture_bundle()
    before = deepcopy(bundle)
    validation = validate_final_decision_bundle(bundle)
    assert unified_pipeline.write_report_artifacts(
        bundle,
        validation,
        reports=context.output_dir(tmp_path),
        session_context=context,
    ) is True
    assert bundle == before
    persisted = json.loads((tmp_path / "reports" / "final_decision_bundle.json").read_text(encoding="utf-8"))
    for field in ("bundle_hash", "portfolio_snapshot", "dqs_results", "scenario_decision_by_key"):
        assert persisted[field] == before[field]


def test_workflow_crons_are_utc_and_us_dst_is_dual_gated() -> None:
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    daily = (root / "daily.yml").read_text(encoding="utf-8")
    cn = (root / "cn-preopen-report.yml").read_text(encoding="utf-8")
    us = (root / "us-preopen-report.yml").read_text(encoding="utf-8")
    assert 'cron: "30 10 * * *"' in daily
    assert 'cron: "30 0 * * 1-5"' in cn
    assert 'cron: "30 12 * * 1-5"' in us
    assert 'cron: "30 13 * * 1-5"' in us
    assert "timezone:" not in daily + cn + us
