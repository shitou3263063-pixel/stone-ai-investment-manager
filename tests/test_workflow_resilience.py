from pathlib import Path
from unittest.mock import patch

from src.notifier.email_notifier import send_workflow_failure_notification


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_workflow_runs_report_after_test_failure_and_finalizes_status() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")

    assert "- name: Run tests\n        id: tests\n        continue-on-error: true" in workflow
    assert "- name: Run Stone AI Investment Manager Pro V12.7.1 Final Freeze\n        id: production\n        if: always()\n        continue-on-error: true" in workflow
    assert "python main.py 2>&1 | tee logs/main.log" in workflow
    assert "if: always() && steps.production.outcome == 'failure'" in workflow
    assert "python scripts/send_workflow_failure_email.py" in workflow
    assert "name: stone-ai-investment-reports-${{ github.run_id }}" in workflow
    assert "test_outcome=\"${{ steps.tests.outcome }}\"" in workflow
    assert "production_outcome=\"${{ steps.production.outcome }}\"" in workflow


def test_independent_test_workflow_runs_pytest() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

    assert "name: Stone AI Test Suite" in workflow
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "run: pytest -q" in workflow
    assert "python main.py" not in workflow


def test_workflow_failure_notification_uses_existing_mail_channel(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SMTP_HOST=smtp.example.com\n"
        "SMTP_PORT=465\n"
        "SMTP_USER=test@example.com\n"
        "SMTP_PASSWORD=app-password\n"
        "EMAIL_TO=receiver@example.com\n",
        encoding="utf-8",
    )

    with patch("src.notifier.email_notifier._send_email") as sender:
        result = send_workflow_failure_notification(
            failed_stage="python main.py",
            run_url="https://github.com/example/repository/actions/runs/123",
            env_path=env_path,
        )

    assert result["sent"] is True
    assert sender.call_args.args[1] == "[失败] Stone AI 日报生产未完成"
    assert "python main.py" in sender.call_args.args[2]
    assert "actions/runs/123" in sender.call_args.args[2]
    assert sender.call_args.args[3] == []
