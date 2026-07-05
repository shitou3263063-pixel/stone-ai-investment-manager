from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any


def send_wechat_report(config: dict[str, Any], report_path: str | Path) -> bool:
    """可选：通过企业微信机器人 webhook 发送报告。"""

    wechat_config = config.get("wechat", {})
    if not wechat_config.get("enabled", False):
        return False

    webhook_url = wechat_config.get("webhook_url")
    if not webhook_url:
        raise ValueError("企业微信配置缺少 webhook_url")

    content = Path(report_path).read_text(encoding="utf-8")
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content[:4000]},
    }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        return 200 <= response.status < 300
