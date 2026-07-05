from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
import os
import smtplib
import ssl
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMAIL_TO = "shili3263063@qq.com"
DEFAULT_SMTP_HOST = "smtp.qq.com"
DEFAULT_SMTP_PORT = "465"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _config() -> dict[str, str]:
    _load_env(PROJECT_ROOT / ".env")
    return {
        "SMTP_HOST": os.getenv("SMTP_HOST", DEFAULT_SMTP_HOST).strip() or DEFAULT_SMTP_HOST,
        "SMTP_PORT": os.getenv("SMTP_PORT", DEFAULT_SMTP_PORT).strip() or DEFAULT_SMTP_PORT,
        "SMTP_USER": os.getenv("SMTP_USER", "").strip(),
        "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD", "").strip(),
        "EMAIL_TO": os.getenv("EMAIL_TO", DEFAULT_EMAIL_TO).strip() or DEFAULT_EMAIL_TO,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    config = _config()
    missing = [key for key in ["SMTP_USER", "SMTP_PASSWORD"] if not config[key]]
    if missing:
        print("邮件未启用：请在 .env 填写 QQ邮箱SMTP授权码和发件QQ邮箱。")
        print("缺失项：" + "、".join(missing))
        print(f"当前默认收件邮箱：{config['EMAIL_TO']}")
        return 0

    try:
        msg = EmailMessage()
        msg["Subject"] = "Stone AI 邮件测试"
        msg["From"] = config["SMTP_USER"]
        msg["To"] = config["EMAIL_TO"]
        msg.set_content("如果你收到这封邮件，说明 Stone AI Investment Manager 邮件配置成功。")

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            config["SMTP_HOST"],
            int(config["SMTP_PORT"]),
            context=context,
            timeout=30,
        ) as smtp:
            smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
            smtp.send_message(msg)

        print(f"测试邮件已发送到 {config['EMAIL_TO']}")
        return 0
    except Exception as exc:  # noqa: BLE001 - 测试命令友好提示，不打印敏感信息
        print(f"测试邮件发送失败：{exc}")
        print("请确认 QQ 邮箱已开启 SMTP，且 SMTP_PASSWORD 填写的是授权码，不是登录密码。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

