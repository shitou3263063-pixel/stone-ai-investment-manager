from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from src.app import write_weekly_report_if_due
from src.notifier.email_notifier import DAILY_EMAIL_SUBJECT, _send_email, send_daily_reports
from src.reports.report_center import build_run_status, generate_today_action
from tests.test_v12_5_freeze import _decision


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
    status = build_run_status(_decision(), report_files=FIXED_REPORTS, email_status="sent")
    (reports / "run_status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    (root / ".env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=465\nSMTP_USER=test@example.com\n"
        "SMTP_PASSWORD=app-password\nEMAIL_TO=receiver@example.com\n",
        encoding="utf-8",
    )
    return reports


def test_today_action_is_compact_and_has_only_required_fields() -> None:
    text = generate_today_action(_decision())
    required = [
        "报告日期",
        "数据截止时间",
        "今日是否执行",
        "操作类型",
        "标的",
        "金额或金额区间",
        "资金来源",
        "执行后账户现金余额",
        "固定现金安全储备",
        "DQS",
        "Risk Score",
        "Opportunity Score",
        "下一复核日期",
        "不执行的核心原因",
        "数据异常或资产基线冲突",
    ]
    for field in required:
        assert field in text
    assert len(text.splitlines()) <= 20
    assert "最大风险" not in text
    assert "下一触发条件" not in text


def test_run_status_contains_fixed_contract_and_baseline_separation() -> None:
    status = build_run_status(_decision(), report_files=FIXED_REPORTS, email_status="sent")
    required = {
        "run_time",
        "data_cutoff_time",
        "report_date",
        "status",
        "dqs",
        "risk_score",
        "total_assets",
        "total_cash",
        "cash_safety_reserve",
        "investable_cash",
        "today_action",
        "report_files",
        "email_status",
        "warnings",
        "errors",
    }
    assert required <= status.keys()
    assert status["total_assets"] == 2_821_100
    assert status["total_cash"] == 241_000
    assert status["cash_safety_reserve"] == 220_000
    assert status["investable_cash"] == 21_000
    assert status["fund_classification"]["unsettled_bond_cash"] == 0
    assert status["fund_classification"]["actual_arrived_bond_cash"] == 30_000
    assert status["fund_classification"]["remaining_bond_to_equity_cash"] == 21_000
    assert status["fund_classification"]["simulated_grid_cash"] == 0
    assert status["fund_classification"]["real_executable_today"] == 0
    assert status["report_files"] == FIXED_REPORTS


def test_weekly_report_only_refreshes_on_sunday() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reports = Path(tmp)
        weekly = reports / "weekly_report.md"
        weekly.write_text("existing weekly report", encoding="utf-8")
        monday = write_weekly_report_if_due(reports, _decision(), date(2026, 7, 13))
        assert monday["updated"] is False
        assert weekly.read_text(encoding="utf-8") == "existing weekly report"

        sunday = write_weekly_report_if_due(reports, _decision(), date(2026, 7, 19))
        assert sunday["updated"] is True
        assert "报告所属周" in weekly.read_text(encoding="utf-8")


def test_email_subject_body_and_attachments_are_fixed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = _write_email_fixture(root)
        with patch.dict("os.environ", {"REPORT_RUN_LABEL": ""}, clear=False):
            with patch("src.notifier.email_notifier._send_email") as sender:
                result = send_daily_reports(reports_dir=reports, env_path=root / ".env")
        assert result["sent"] is True
        args = sender.call_args.args
        assert args[1] == "Stone AI CIO Daily - 10%-15% Target"
        assert args[1] == DAILY_EMAIL_SUBJECT
        assert [path.name for path in args[3]] == [
            "today_action.md",
            "daily_report.md",
            "weekly_report.md",
            "run_status.json",
        ]
        for field in ["运行时段", "报告日期", "数据截止时间", "今日是否执行", "标的和金额", "可投资现金", "下一复核日期", "DQS", "是否存在警告或错误"]:
            assert field in args[2]


def test_dual_timezone_email_subject_identifies_us_market_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = _write_email_fixture(root)
        with patch.dict("os.environ", {"REPORT_RUN_LABEL": "美东时间 08:30"}, clear=False):
            with patch("src.notifier.email_notifier._send_email") as sender:
                result = send_daily_reports(reports_dir=reports, env_path=root / ".env")
        assert result["sent"] is True
        assert sender.call_args.args[1] == f"{DAILY_EMAIL_SUBJECT} | 美东时间 08:30"
        assert "运行时段：美东时间 08:30" in sender.call_args.args[2]


def test_github_daily_workflow_has_beijing_and_new_york_0830() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    assert 'cron: "30 8 * * *"' in workflow
    assert 'timezone: "Asia/Shanghai"' in workflow
    assert 'cron: "30 8 * * 0-6"' in workflow
    assert 'timezone: "America/New_York"' in workflow
    assert "Run Stone AI Investment Manager Pro V12.7.0 Stable" in workflow


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
