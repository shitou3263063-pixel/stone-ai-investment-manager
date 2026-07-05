from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any


def send_email_report(config: dict[str, Any], report_path: str | Path) -> bool:
    """可选：用 SMTP 发送报告。

    config.yaml 里的 email.enabled 为 false 时不会发送。
    """

    email_config = config.get("email", {})
    if not email_config.get("enabled", False):
        return False

    required_keys = ["smtp_server", "smtp_port", "sender", "password", "receiver"]
    missing = [key for key in required_keys if not email_config.get(key)]
    if missing:
        print(f"邮件发送已启用，但配置缺少字段：{', '.join(missing)}；本次跳过邮件发送。")
        return False

    report_path = Path(report_path)
    content = report_path.read_text(encoding="utf-8")

    message = EmailMessage()
    message["Subject"] = f"AI投资管家日报 - {report_path.stem}"
    message["From"] = email_config["sender"]
    message["To"] = email_config["receiver"]
    message.set_content(content)

    try:
        with smtplib.SMTP_SSL(email_config["smtp_server"], int(email_config["smtp_port"])) as server:
            server.login(email_config["sender"], email_config["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        print("邮件发送失败：邮箱认证未通过。Gmail 通常需要使用“应用专用密码”，不是普通登录密码。")
        return False
    except OSError as error:
        print(f"邮件发送失败：无法连接邮件服务器。错误信息：{error}")
        return False

    return True
