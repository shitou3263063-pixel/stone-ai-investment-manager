from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from html import escape
import json
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
DAILY_EMAIL_SUBJECT = "Stone AI CIO Daily - 10%-15% Target"


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
          今日执行单、完整日报、最近有效周报和运行状态已放在附件中。所有操作必须由你人工确认后自行执行，不承诺收益。
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
        is_json = path.suffix.lower() == ".json"
        msg.add_attachment(
            path.read_bytes(),
            maintype="application" if is_json else "text",
            subtype="json" if is_json else "markdown",
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
        return {"sent": False, "skipped": True, "message": message, "error": ""}

    try:
        _send_email(
            config,
            "Stone AI 邮件测试",
            "如果你收到这封邮件，说明 Stone AI Investment Manager 邮件配置成功。",
            [],
        )
        message = f"邮件测试已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message, "error": ""}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不能影响主程序
        message = f"邮件测试发送失败，已跳过：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message, "error": str(exc)}


def send_daily_reports(
    reports_dir: Path | None = None,
    subject_date: date | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，跳过发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message, "error": ""}

    reports_path = reports_dir or PROJECT_ROOT / "reports"
    today_action_path = reports_path / "today_action.md"
    daily_path = reports_path / "daily_report.md"
    weekly_path = reports_path / "weekly_report.md"
    run_status_path = reports_path / "run_status.json"
    attachments = [today_action_path, daily_path, weekly_path, run_status_path]
    missing = [path.name for path in attachments if not path.exists()]
    if missing:
        message = f"固定邮件附件缺失，跳过发送：{', '.join(missing)}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message, "error": message}

    try:
        run_status = json.loads(run_status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"run_status.json 无法读取，跳过发送：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message, "error": str(exc)}

    action = run_status.get("today_action", {}) or {}
    has_issue = bool(run_status.get("warnings") or run_status.get("errors"))
    plain_body = "\n".join(
        [
            f"报告日期：{run_status.get('report_date')}",
            f"数据截止时间：{run_status.get('data_cutoff_time')}",
            f"今日是否执行：{'是' if action.get('execute') else '否'}",
            f"标的和金额：{action.get('targets', '不适用')} / {action.get('amount_or_range', '0元')}",
            f"可投资现金：{run_status.get('investable_cash', 0):,.0f}元",
            f"下一复核日期：{run_status.get('next_review_date') or action.get('next_review_date') or '暂无'}",
            f"DQS：{run_status.get('dqs')}",
            f"是否存在警告或错误：{'是' if has_issue else '否'}",
        ]
    )

    try:
        _send_email(
            config,
            DAILY_EMAIL_SUBJECT,
            plain_body,
            attachments,
            _html_body(DAILY_EMAIL_SUBJECT, plain_body),
        )
        message = f"邮件已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message, "error": ""}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不能影响报告生成
        message = f"邮件发送失败，已记录错误：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message, "error": str(exc)}
