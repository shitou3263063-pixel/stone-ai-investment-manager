from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_KEY = "WECHAT_WORK_WEBHOOK"


def _load_env_file(env_path: Path) -> None:
    """读取 .env；不覆盖系统环境变量，方便 GitHub Secrets 接管。"""
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


def _read_today_action(reports_dir: Path) -> str | None:
    path = reports_dir / "today_action.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def _safe_error_message(exc: Exception) -> str:
    """返回不包含 webhook key 的错误说明。"""
    if isinstance(exc, error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, error.URLError):
        return f"{exc.reason}"
    return exc.__class__.__name__


def _post_markdown(webhook_url: str, content: str) -> None:
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=20) as response:  # noqa: S310 - 用户显式配置的企业微信 webhook
        body = response.read().decode("utf-8", errors="replace")

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        result = {}

    if result.get("errcode") not in (None, 0):
        errmsg = result.get("errmsg", "unknown error")
        raise RuntimeError(f"企业微信返回错误：errcode={result.get('errcode')}, errmsg={errmsg}")


def send_wechat_work_summary(
    reports_dir: Path | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """推送 today_action.md 到企业微信群；失败只记录日志，不中断主程序。"""
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    webhook_url = os.getenv(ENV_KEY, "").strip()

    if not webhook_url:
        message = "企业微信未配置，跳过"
        write_log(message, filename="wechat_work_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    reports_path = reports_dir or PROJECT_ROOT / "reports"
    content = _read_today_action(reports_path)
    if not content:
        message = "today_action.md 不存在，跳过企业微信推送"
        write_log(message, filename="wechat_work_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    try:
        _post_markdown(webhook_url, content)
        message = "企业微信消息已发送"
        write_log(message, filename="wechat_work_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 推送失败不能影响日报生成
        message = f"企业微信推送失败，已记录错误：{_safe_error_message(exc)}"
        write_log(message, filename="wechat_work_notifier.log")
        return {"sent": False, "skipped": False, "message": message}


def send_test_message(
    message: str = "Stone AI 企业微信推送测试成功。",
    env_path: Path | None = None,
) -> dict[str, Any]:
    """发送一条企业微信测试消息。"""
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    webhook_url = os.getenv(ENV_KEY, "").strip()
    if not webhook_url:
        result_message = "企业微信未配置：请在 .env 填写 WECHAT_WORK_WEBHOOK"
        write_log(result_message, filename="wechat_work_notifier.log")
        return {"sent": False, "skipped": True, "message": result_message}

    try:
        _post_markdown(webhook_url, message)
        result_message = "企业微信测试消息已发送"
        write_log(result_message, filename="wechat_work_notifier.log")
        return {"sent": True, "skipped": False, "message": result_message}
    except Exception as exc:  # noqa: BLE001 - 测试命令友好返回，不泄露 webhook
        result_message = f"企业微信测试消息发送失败：{_safe_error_message(exc)}"
        write_log(result_message, filename="wechat_work_notifier.log")
        return {"sent": False, "skipped": False, "message": result_message}
