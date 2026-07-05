from __future__ import annotations

from pathlib import Path
import json
import os
from typing import Any
from urllib import request

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(env_path: Path) -> None:
    """读取 .env，不覆盖系统环境变量。"""
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


def _post_json(url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=20) as response:  # noqa: S310 - 用户显式配置的 webhook
        response.read()


def _read_today_action(reports_dir: Path) -> str:
    path = reports_dir / "today_action.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "today_action.md 尚未生成。"


def send_push_summary(
    reports_dir: Path | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """预留 Telegram/微信/企业微信推送；没配置时自动跳过。"""
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    reports_path = reports_dir or PROJECT_ROOT / "reports"
    text = _read_today_action(reports_path)

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    wechat_webhook = (
        os.getenv("QYWX_WEBHOOK_URL", "").strip()
        or os.getenv("WECHAT_WEBHOOK_URL", "").strip()
    )

    if not ((telegram_token and telegram_chat_id) or wechat_webhook):
        message = "推送未配置，跳过 Telegram/企业微信发送"
        write_log(message, filename="push_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    sent_channels: list[str] = []
    errors: list[str] = []

    if telegram_token and telegram_chat_id:
        try:
            url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            _post_json(url, {"chat_id": telegram_chat_id, "text": text})
            sent_channels.append("Telegram")
        except Exception as exc:  # noqa: BLE001 - 推送失败不能影响主程序
            errors.append(f"Telegram 推送失败：{exc}")

    if wechat_webhook:
        try:
            _post_json(
                wechat_webhook,
                {
                    "msgtype": "text",
                    "text": {"content": text},
                },
            )
            sent_channels.append("企业微信/微信")
        except Exception as exc:  # noqa: BLE001 - 推送失败不能影响主程序
            errors.append(f"企业微信/微信推送失败：{exc}")

    if errors:
        message = "；".join(errors)
        write_log(message, filename="push_notifier.log")
        return {"sent": bool(sent_channels), "skipped": False, "message": message}

    message = "推送已发送：" + "、".join(sent_channels)
    write_log(message, filename="push_notifier.log")
    return {"sent": True, "skipped": False, "message": message}

