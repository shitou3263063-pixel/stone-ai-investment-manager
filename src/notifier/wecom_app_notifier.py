from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_KEYS = ["WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_SECRET", "WECOM_USER_ID"]
FAILURE_HINT = (
    "常见原因：Secret 错误；UserID 错误；应用可见范围没有包含该用户；"
    "企业微信未登录或账号不可用；GitHub Secrets 没配置；当前运行环境网络受限。"
)


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


def _get_config(env_path: Path | None = None) -> dict[str, str] | None:
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    config = {key: os.getenv(key, "").strip() for key in REQUIRED_KEYS}
    missing = [key for key, value in config.items() if not value]
    if missing:
        return None
    return config


def _safe_error_message(exc: Exception) -> str:
    """返回不包含 Secret、token 的错误说明。"""
    if isinstance(exc, error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, error.URLError):
        return f"{exc.reason}"
    return str(exc) or exc.__class__.__name__


def _failure_message(prefix: str, exc: Exception) -> str:
    detail = _safe_error_message(exc).rstrip("。.")
    return f"{prefix}：{detail}。{FAILURE_HINT}"


def _request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with request.urlopen(req, timeout=20) as response:  # noqa: S310 - 企业微信官方 API
        body = response.read().decode("utf-8", errors="replace")

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("企业微信返回内容不是 JSON") from exc


def _get_access_token(corp_id: str, secret: str) -> str:
    params = parse.urlencode({"corpid": corp_id, "corpsecret": secret})
    result = _request_json(f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{params}")
    if result.get("errcode") != 0:
        raise RuntimeError(
            f"获取 access_token 失败：errcode={result.get('errcode')}, errmsg={result.get('errmsg', 'unknown')}"
        )
    token = str(result.get("access_token", "")).strip()
    if not token:
        raise RuntimeError("企业微信未返回 access_token")
    return token


def _send_text_message(access_token: str, agent_id: str, user_id: str, content: str) -> None:
    payload = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": content},
        "safe": 0,
    }
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    result = _request_json(url, payload)
    if result.get("errcode") != 0:
        raise RuntimeError(
            f"企业微信发送失败：errcode={result.get('errcode')}, errmsg={result.get('errmsg', 'unknown')}"
        )


def _read_today_action(reports_dir: Path) -> str | None:
    path = reports_dir / "today_action.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def send_wecom_app_summary(
    reports_dir: Path | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """通过企业微信自建应用点对点推送 today_action.md。"""
    config = _get_config(env_path)
    if not config:
        message = "企业微信应用未配置，跳过"
        write_log(message, filename="wecom_app_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    reports_path = reports_dir or PROJECT_ROOT / "reports"
    content = _read_today_action(reports_path)
    if not content:
        message = "today_action.md 不存在，跳过企业微信应用推送"
        write_log(message, filename="wecom_app_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    try:
        token = _get_access_token(config["WECOM_CORP_ID"], config["WECOM_SECRET"])
        _send_text_message(token, config["WECOM_AGENT_ID"], config["WECOM_USER_ID"], content)
        message = "企业微信应用点对点消息已发送"
        write_log(message, filename="wecom_app_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 推送失败不能影响日报生成
        message = _failure_message("企业微信应用推送失败，已记录错误", exc)
        write_log(message, filename="wecom_app_notifier.log")
        return {"sent": False, "skipped": False, "message": message}


def send_test_message(
    message: str = "Stone AI 企业微信点对点推送测试成功。",
    env_path: Path | None = None,
) -> dict[str, Any]:
    """发送一条企业微信自建应用测试消息。"""
    config = _get_config(env_path)
    if not config:
        result_message = "企业微信应用未配置：请在 .env 填写 WECOM_CORP_ID、WECOM_AGENT_ID、WECOM_SECRET、WECOM_USER_ID"
        write_log(result_message, filename="wecom_app_notifier.log")
        return {"sent": False, "skipped": True, "message": result_message}

    try:
        token = _get_access_token(config["WECOM_CORP_ID"], config["WECOM_SECRET"])
        _send_text_message(token, config["WECOM_AGENT_ID"], config["WECOM_USER_ID"], message)
        result_message = "企业微信点对点测试消息已发送"
        write_log(result_message, filename="wecom_app_notifier.log")
        return {"sent": True, "skipped": False, "message": result_message}
    except Exception as exc:  # noqa: BLE001 - 测试命令友好返回，不泄露 Secret
        result_message = _failure_message("企业微信点对点测试消息发送失败", exc)
        write_log(result_message, filename="wecom_app_notifier.log")
        return {"sent": False, "skipped": False, "message": result_message}
