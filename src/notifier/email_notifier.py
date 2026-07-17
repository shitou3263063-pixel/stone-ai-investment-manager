from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from html import escape
import json
from pathlib import Path
import os
import smtplib
import socket
import ssl
import time
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = "465"
DEFAULT_EMAIL_TO = "shitou3263063@gmail.com"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 12
DEFAULT_SEND_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2
REQUIRED_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
OPTIONAL_KEYS = [
    "SMTP_SECURITY",
    "SMTP_CONNECT_TIMEOUT_SECONDS",
    "SMTP_SEND_TIMEOUT_SECONDS",
    "SMTP_MAX_RETRIES",
]
DAILY_EMAIL_SUBJECT = "Stone AI CIO Daily - 10%-15% Target"

RETRYABLE_ERROR_CATEGORIES = {
    "DNS_ERROR",
    "CONNECTION_TIMEOUT",
    "TLS_HANDSHAKE_ERROR",
    "NETWORK_PROXY_ERROR",
    "UNKNOWN_ERROR",
}


class EmailDeliveryError(RuntimeError):
    """A stage-aware error whose string representation is safe for logs and JSON."""

    def __init__(self, category: str, stage: str, summary: str) -> None:
        self.category = category
        self.stage = stage
        self.summary = summary
        super().__init__(f"{category} [{stage}]：{summary}")


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


def _bounded_int(value: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _get_email_config(env_path: Path | None = None) -> dict[str, str] | None:
    _load_env_file(env_path or PROJECT_ROOT / ".env")

    config = {key: os.getenv(key, "").strip() for key in REQUIRED_KEYS + OPTIONAL_KEYS}
    config["SMTP_HOST"] = config["SMTP_HOST"] or DEFAULT_SMTP_HOST
    config["SMTP_PORT"] = config["SMTP_PORT"] or DEFAULT_SMTP_PORT
    config["EMAIL_TO"] = config["EMAIL_TO"] or DEFAULT_EMAIL_TO

    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        return None

    port = _bounded_int(config["SMTP_PORT"], int(DEFAULT_SMTP_PORT), minimum=1, maximum=65535)
    security = (config.get("SMTP_SECURITY") or ("ssl" if port == 465 else "starttls")).lower()
    if security not in {"ssl", "starttls"}:
        security = "ssl" if port == 465 else "starttls"
    config["SMTP_PORT"] = str(port)
    config["SMTP_SECURITY"] = security
    config["SMTP_CONNECT_TIMEOUT_SECONDS"] = str(
        _bounded_int(config.get("SMTP_CONNECT_TIMEOUT_SECONDS", ""), DEFAULT_CONNECT_TIMEOUT_SECONDS, minimum=3, maximum=120)
    )
    config["SMTP_SEND_TIMEOUT_SECONDS"] = str(
        _bounded_int(config.get("SMTP_SEND_TIMEOUT_SECONDS", ""), DEFAULT_SEND_TIMEOUT_SECONDS, minimum=5, maximum=300)
    )
    config["SMTP_MAX_RETRIES"] = str(
        _bounded_int(config.get("SMTP_MAX_RETRIES", ""), DEFAULT_MAX_RETRIES, minimum=0, maximum=2)
    )
    return config


def _mask_email(value: str) -> str:
    if "@" not in value:
        return "***"
    return f"***@{value.rsplit('@', 1)[1]}"


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


def _safe_error(exc: BaseException, stage: str) -> EmailDeliveryError:
    if isinstance(exc, EmailDeliveryError):
        return exc
    if isinstance(exc, socket.gaierror) or stage == "dns":
        return EmailDeliveryError("DNS_ERROR", stage, "SMTP服务器域名解析失败。")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailDeliveryError("AUTHENTICATION_FAILED", stage, "SMTP账号或应用专用密码未被服务器接受。")
    if isinstance(exc, ssl.SSLError):
        return EmailDeliveryError("TLS_HANDSHAKE_ERROR", stage, "TLS安全连接握手失败。")
    if isinstance(
        exc,
        (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
            smtplib.SMTPResponseException,
        ),
    ):
        code = getattr(exc, "smtp_code", None)
        suffix = f"（SMTP {code}）" if code else ""
        return EmailDeliveryError("SMTP_REJECTED", stage, f"SMTP服务器拒绝了请求{suffix}。")
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in str(exc).lower():
        return EmailDeliveryError("CONNECTION_TIMEOUT", stage, "SMTP连接或服务器响应超时。")
    if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, BrokenPipeError)):
        return EmailDeliveryError("NETWORK_PROXY_ERROR", stage, "连接被本地网络、防火墙、VPN或远端中断。")
    text = str(exc).lower()
    if any(marker in text for marker in ("proxy", "tunnel", "socks", "connection reset", "closed by remote")):
        return EmailDeliveryError("NETWORK_PROXY_ERROR", stage, "连接可能被代理、VPN、防火墙或远端中断。")
    return EmailDeliveryError("UNKNOWN_ERROR", stage, f"未识别的邮件异常（{type(exc).__name__}）。")


def _validate_provider_pair(config: dict[str, str]) -> None:
    user_domain = config["SMTP_USER"].rsplit("@", 1)[-1].lower()
    host = config["SMTP_HOST"].lower()
    if user_domain == "gmail.com" and host != "smtp.gmail.com":
        raise EmailDeliveryError(
            "AUTHENTICATION_FAILED",
            "configuration",
            "Gmail发件账号必须配合smtp.gmail.com，当前SMTP主机与账号类型不匹配。",
        )


def _set_send_timeout(smtp: smtplib.SMTP, timeout_seconds: int) -> None:
    sock = getattr(smtp, "sock", None)
    if sock is not None and hasattr(sock, "settimeout"):
        sock.settimeout(timeout_seconds)


def _open_smtp(config: dict[str, str]) -> smtplib.SMTP:
    host = config["SMTP_HOST"]
    port = int(config["SMTP_PORT"])
    connect_timeout = int(config["SMTP_CONNECT_TIMEOUT_SECONDS"])
    send_timeout = int(config["SMTP_SEND_TIMEOUT_SECONDS"])
    security = config["SMTP_SECURITY"]
    context = ssl.create_default_context()

    smtp: smtplib.SMTP | None = None
    try:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, context=context, timeout=connect_timeout)
        else:
            smtp = smtplib.SMTP(host, port, timeout=connect_timeout)
            _set_send_timeout(smtp, send_timeout)
            smtp.ehlo()
            try:
                smtp.starttls(context=context)
                smtp.ehlo()
            except Exception as exc:  # noqa: BLE001
                raise _safe_error(exc, "tls_handshake") from exc
        _set_send_timeout(smtp, send_timeout)
        return smtp
    except EmailDeliveryError:
        if smtp is not None:
            smtp.close()
        raise
    except Exception as exc:  # noqa: BLE001
        if smtp is not None:
            smtp.close()
        stage = "tls_handshake" if isinstance(exc, ssl.SSLError) else "connection"
        raise _safe_error(exc, stage) from exc


def _close_smtp(smtp: smtplib.SMTP, *, delivery_completed: bool) -> None:
    """A QUIT timeout after DATA acceptance must not turn a sent mail into a failure."""
    try:
        smtp.quit()
    except Exception as exc:  # noqa: BLE001 - cleanup is best effort
        try:
            smtp.close()
        except Exception:  # noqa: BLE001
            pass
        if delivery_completed:
            safe = _safe_error(exc, "connection_close")
            write_log(f"邮件正文已提交，关闭SMTP连接时出现非致命异常：{safe}", filename="email_notifier.log")


def _send_once(config: dict[str, str], msg: EmailMessage) -> None:
    _validate_provider_pair(config)
    smtp = _open_smtp(config)
    delivered = False
    try:
        try:
            smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        except Exception as exc:  # noqa: BLE001
            raise _safe_error(exc, "authentication") from exc
        try:
            smtp.send_message(msg)
            delivered = True
        except Exception as exc:  # noqa: BLE001
            raise _safe_error(exc, "smtp_send") from exc
    finally:
        _close_smtp(smtp, delivery_completed=delivered)


def _build_message(
    config: dict[str, str],
    subject: str,
    body: str,
    attachments: list[Path],
    html_body: str | None,
) -> EmailMessage:
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
    return msg


def _send_email(
    config: dict[str, str],
    subject: str,
    body: str,
    attachments: list[Path],
    html_body: str | None = None,
) -> None:
    config = dict(config)
    port = _bounded_int(config.get("SMTP_PORT", ""), int(DEFAULT_SMTP_PORT), minimum=1, maximum=65535)
    config["SMTP_PORT"] = str(port)
    config.setdefault("SMTP_SECURITY", "ssl" if port == 465 else "starttls")
    config["SMTP_SECURITY"] = config["SMTP_SECURITY"].lower()
    config.setdefault("SMTP_CONNECT_TIMEOUT_SECONDS", str(DEFAULT_CONNECT_TIMEOUT_SECONDS))
    config.setdefault("SMTP_SEND_TIMEOUT_SECONDS", str(DEFAULT_SEND_TIMEOUT_SECONDS))
    config.setdefault("SMTP_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))
    msg = _build_message(config, subject, body, attachments, html_body)
    retries = _bounded_int(config["SMTP_MAX_RETRIES"], DEFAULT_MAX_RETRIES, minimum=0, maximum=2)

    for attempt in range(retries + 1):
        try:
            _send_once(config, msg)
            return
        except Exception as exc:  # noqa: BLE001
            safe = _safe_error(exc, "smtp_send")
            # If DATA may already have reached Gmail, retrying can create duplicates.
            # Retry only failures that happened before message submission.
            should_retry = (
                safe.category in RETRYABLE_ERROR_CATEGORIES
                and safe.stage not in {"smtp_send", "connection_close"}
                and attempt < retries
            )
            write_log(
                f"邮件发送尝试{attempt + 1}/{retries + 1}失败：{safe}；是否重试={'是' if should_retry else '否'}",
                filename="email_notifier.log",
            )
            if not should_retry:
                raise safe from exc
            time.sleep(1 if attempt == 0 else 2)


def _probe_tcp(host: str, port: int, timeout_seconds: int) -> None:
    with socket.create_connection((host, port), timeout=timeout_seconds):
        return


def _probe_tls(config: dict[str, str]) -> None:
    host = config["SMTP_HOST"]
    port = int(config["SMTP_PORT"])
    timeout_seconds = int(config["SMTP_CONNECT_TIMEOUT_SECONDS"])
    context = ssl.create_default_context()
    if config["SMTP_SECURITY"] == "ssl":
        with socket.create_connection((host, port), timeout=timeout_seconds) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return
    smtp = smtplib.SMTP(host, port, timeout=timeout_seconds)
    try:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
    finally:
        smtp.close()


def diagnose_email_connection(env_path: Path | None = None) -> dict[str, Any]:
    """Probe each SMTP stage without exposing credentials or sending a message."""
    config = _get_email_config(env_path)
    if not config:
        return {
            "configured": False,
            "status": "skipped",
            "stages": {},
            "error_category": "AUTHENTICATION_FAILED",
            "error_stage": "configuration",
            "error_summary": "邮件配置不完整。",
        }

    result: dict[str, Any] = {
        "configured": True,
        "status": "running",
        "smtp_host": config["SMTP_HOST"],
        "smtp_port": int(config["SMTP_PORT"]),
        "security": config["SMTP_SECURITY"],
        "connect_timeout_seconds": int(config["SMTP_CONNECT_TIMEOUT_SECONDS"]),
        "send_timeout_seconds": int(config["SMTP_SEND_TIMEOUT_SECONDS"]),
        "max_retries": int(config["SMTP_MAX_RETRIES"]),
        "sender_masked": _mask_email(config["SMTP_USER"]),
        "proxy_environment_detected": any(os.getenv(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")),
        "stages": {},
        "error_category": "",
        "error_stage": "",
        "error_summary": "",
    }

    probes = [
        ("configuration", lambda: _validate_provider_pair(config)),
        ("dns", lambda: socket.getaddrinfo(config["SMTP_HOST"], int(config["SMTP_PORT"]), type=socket.SOCK_STREAM)),
        (
            "tcp_connection",
            lambda: _probe_tcp(
                config["SMTP_HOST"],
                int(config["SMTP_PORT"]),
                int(config["SMTP_CONNECT_TIMEOUT_SECONDS"]),
            ),
        ),
        ("tls_handshake", lambda: _probe_tls(config)),
    ]
    for stage, probe in probes:
        try:
            probe()
            result["stages"][stage] = {"status": "passed"}
        except Exception as exc:  # noqa: BLE001
            safe = _safe_error(exc, stage)
            result["stages"][stage] = {"status": "failed", "error_category": safe.category, "summary": safe.summary}
            result.update(status="failed", error_category=safe.category, error_stage=stage, error_summary=safe.summary)
            return result

    smtp: smtplib.SMTP | None = None
    try:
        smtp = _open_smtp(config)
        smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        result["stages"]["authentication"] = {"status": "passed"}
    except Exception as exc:  # noqa: BLE001
        safe = _safe_error(exc, "authentication")
        result["stages"]["authentication"] = {
            "status": "failed",
            "error_category": safe.category,
            "summary": safe.summary,
        }
        result.update(status="failed", error_category=safe.category, error_stage="authentication", error_summary=safe.summary)
        return result
    finally:
        if smtp is not None:
            _close_smtp(smtp, delivery_completed=False)

    result["status"] = "passed"
    return result


def _failure_result(prefix: str, exc: BaseException) -> dict[str, Any]:
    safe = _safe_error(exc, "smtp_send")
    message = f"{prefix}：{safe}"
    write_log(message, filename="email_notifier.log")
    return {
        "sent": False,
        "skipped": False,
        "message": message,
        "error": str(safe),
        "error_category": safe.category,
        "error_stage": safe.stage,
    }


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
        message = f"邮件测试已发送到 {_mask_email(config['EMAIL_TO'])}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return _failure_result("邮件测试发送失败，已跳过", exc)


def send_workflow_failure_notification(
    *,
    failed_stage: str,
    run_url: str = "",
    env_path: Path | None = None,
) -> dict[str, Any]:
    """Send a dedicated alert when report production fails in GitHub Actions."""
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，无法发送日报生产失败通知"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message, "error": ""}

    subject = "[失败] Stone AI 日报生产未完成"
    body_lines = [
        "Stone AI Investment Manager 的日报生产流程执行失败。",
        f"失败阶段：{failed_stage}",
        "完整运行日志已由 GitHub Actions 上传为 artifact，请及时检查。",
    ]
    if run_url:
        body_lines.append(f"运行详情：{run_url}")
    body_lines.extend(
        [
            "本次失败不代表系统执行了任何交易。",
            "系统不自动交易，现有投资策略和交易规则未发生变化。",
        ]
    )

    try:
        _send_email(config, subject, "\n".join(body_lines), [])
        message = f"日报生产失败通知已发送到 {_mask_email(config['EMAIL_TO'])}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return _failure_result("日报生产失败通知发送失败，已记录错误", exc)


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
        safe = EmailDeliveryError("UNKNOWN_ERROR", "attachment_read", f"run_status.json无法读取（{type(exc).__name__}）。")
        message = f"run_status.json 无法读取，跳过发送：{safe}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message, "error": str(safe)}

    action = run_status.get("today_action", {}) or {}
    has_issue = bool(run_status.get("warnings") or run_status.get("errors"))
    run_label = os.getenv("REPORT_RUN_LABEL", "").strip()
    email_subject = f"{DAILY_EMAIL_SUBJECT} | {run_label}" if run_label else DAILY_EMAIL_SUBJECT
    plain_body = "\n".join(
        [
            f"运行时段：{run_label or '本地或未标记运行'}",
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
        _send_email(config, email_subject, plain_body, attachments, _html_body(email_subject, plain_body))
        message = f"邮件已发送到 {_mask_email(config['EMAIL_TO'])}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return _failure_result("邮件发送失败，已记录错误", exc)
