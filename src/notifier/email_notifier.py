from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from pathlib import Path
import os
import smtplib
import ssl
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMTP_HOST = "smtp.qq.com"
DEFAULT_SMTP_PORT = "465"
DEFAULT_EMAIL_TO = "shili3263063@qq.com"
REQUIRED_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]


def _load_env_file(env_path: Path) -> None:
    """读取 .env 文件，不覆盖系统环境变量，方便 GitHub Secrets 接管。"""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _get_email_config(env_path: Path | None = None) -> dict[str, str] | None:
    _load_env_file(env_path or PROJECT_ROOT / ".env")

    config = {key: os.getenv(key, "").strip() for key in REQUIRED_KEYS}
    config["SMTP_HOST"] = config["SMTP_HOST"] or DEFAULT_SMTP_HOST
    config["SMTP_PORT"] = config["SMTP_PORT"] or DEFAULT_SMTP_PORT
    config["EMAIL_TO"] = config["EMAIL_TO"] or DEFAULT_EMAIL_TO
    missing = [key for key, value in config.items() if not value]
    if missing:
        return None
    return config


def _read_report(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def send_daily_reports(
    reports_dir: Path | None = None,
    subject_date: date | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """发送日报和可选周报；失败只记录日志，不中断主程序。"""
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，跳过"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    reports_path = reports_dir or PROJECT_ROOT / "reports"
    today_action_path = reports_path / "today_action.md"
    daily_path = reports_path / "daily_report.md"
    weekly_path = reports_path / "weekly_report.md"

    if not daily_path.exists():
        message = "daily_report.md 不存在，跳过邮件发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    try:
        send_date = subject_date or date.today()
        subject = f"Stone AI Investment Daily - {send_date.isoformat()}"
        today_action_content = _read_report(today_action_path) if today_action_path.exists() else ""
        daily_content = _read_report(daily_path)

        body_parts = [
            "Stone AI Investment Manager Pro V10 日报已生成。",
            "",
        ]
        if today_action_content:
            body_parts.extend(["【今日行动摘要】", "", today_action_content, "", "---", ""])
        body_parts.append(daily_content)

        attachments = [daily_path]
        if today_action_path.exists():
            attachments.insert(0, today_action_path)
        if weekly_path.exists():
            body_parts.extend(["", "---", "", _read_report(weekly_path)])
            attachments.append(weekly_path)

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config["SMTP_USER"]
        msg["To"] = config["EMAIL_TO"]
        msg.set_content("\n".join(body_parts))

        for path in attachments:
            msg.add_attachment(
                path.read_bytes(),
                maintype="text",
                subtype="markdown",
                filename=path.name,
            )

        port = int(config["SMTP_PORT"] or DEFAULT_SMTP_PORT)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config["SMTP_HOST"], port, context=context, timeout=30) as smtp:
            smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
            smtp.send_message(msg)

        message = f"邮件已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不能影响报告生成
        message = f"邮件发送失败，已记录错误：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message}
