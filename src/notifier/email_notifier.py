from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path
import os
import smtplib
import ssl
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = "465"
DEFAULT_EMAIL_TO = "shitou3263063@gmail.com"
REQUIRED_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]


def _load_env_file(env_path: Path) -> None:
    """读取本地 .env；GitHub Actions 中优先使用 Secrets 注入的环境变量。"""
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


def _read_text(path: Path, fallback: str = "") -> str:
    if not path.exists():
        return fallback
    return path.read_text(encoding="utf-8")


def _clip(text: str, limit: int = 2600) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _html_body(title: str, body: str) -> str:
    safe_title = escape(title)
    safe_body = escape(body).replace("\n", "<br>")
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,'Microsoft YaHei',sans-serif;color:#101828">
    <div style="max-width:760px;margin:0 auto;padding:24px 14px">
      <div style="background:#ffffff;border:1px solid #eaecf0;border-radius:12px;overflow:hidden">
        <div style="padding:20px 22px;background:#0f172a;color:#ffffff">
          <div style="font-size:22px;font-weight:800;line-height:1.3">{safe_title}</div>
          <div style="font-size:13px;opacity:.82;margin-top:8px">仅供投资辅助，不构成投资建议；系统不自动交易。</div>
        </div>
        <div style="padding:20px 22px;font-size:15px;line-height:1.72;white-space:normal">{safe_body}</div>
        <div style="padding:14px 22px;border-top:1px solid #eaecf0;background:#f9fafb;color:#475467;font-size:13px;line-height:1.6">
          完整日报、周报、月报和校验报告已放在附件中。所有操作必须由你人工确认后自行执行，不承诺收益。
        </div>
      </div>
    </div>
  </body>
</html>"""


def _send_email(
    config: dict[str, str],
    subject: str,
    body: str,
    attachments: list[Path],
    html_body: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config["SMTP_USER"]
    msg["To"] = config["EMAIL_TO"]
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for path in attachments:
        if not path.exists():
            continue
        msg.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=path.name,
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config["SMTP_HOST"], int(config["SMTP_PORT"]), context=context, timeout=30) as smtp:
        smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        smtp.send_message(msg)


def send_test_email(env_path: Path | None = None) -> dict[str, Any]:
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，跳过发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    try:
        _send_email(
            config,
            "Stone AI 邮件测试",
            "如果你收到这封邮件，说明 Stone AI Investment Manager 邮件配置成功。",
            [],
        )
        message = f"邮件测试已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不能影响主程序
        message = f"邮件测试发送失败，已跳过：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message}


def send_daily_reports(
    reports_dir: Path | None = None,
    subject_date: date | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，跳过发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    reports_path = reports_dir or PROJECT_ROOT / "reports"
    today_action_path = reports_path / "today_action.md"
    daily_path = reports_path / "daily_report.md"
    if not daily_path.exists():
        message = "daily_report.md 不存在，跳过邮件发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    send_date = subject_date or date.today()
    title = f"Stone AI CIO Daily - {send_date.isoformat()}"
    today_body = _read_text(
        today_action_path,
        fallback="今日行动摘要尚未生成，请查看附件 daily_report.md。",
    )
    plain_body = "\n".join(
        [
            title,
            "",
            _clip(today_body),
            "",
            "完整日报、周报、月报和校验报告已放在附件中。",
            "声明：仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。",
        ]
    )
    attachments = [
        today_action_path,
        daily_path,
        reports_path / "weekly_report.md",
        reports_path / "monthly_report.md",
        reports_path / "validation_report.md",
        reports_path / "service_health.md",
    ]
    attachments = [path for path in attachments if path.exists()]

    try:
        _send_email(config, title, plain_body, attachments, _html_body(title, _clip(today_body)))
        message = f"邮件已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不能影响报告生成
        message = f"邮件发送失败，已记录错误：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message}
