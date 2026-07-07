from __future__ import annotations

from datetime import datetime
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://api.stlouisfed.org/fred"


def _api_key() -> str:
    return os.getenv("FRED_API_KEY", "").strip()


def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("FRED_API_KEY 未配置")
    url = f"{BASE_URL}/{path}?{urlencode({**params, 'api_key': key, 'file_type': 'json'})}"
    with urlopen(url, timeout=20) as response:  # noqa: S310 - controlled public API URL
        return json.loads(response.read().decode("utf-8"))


def get_series_latest(series_id: str) -> dict[str, Any]:
    payload = _get_json(
        "series/observations",
        {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": "1",
        },
    )
    observations = payload.get("observations") or []
    if not observations:
        raise RuntimeError(f"FRED {series_id} 返回空数据")
    observation = observations[0]
    value = observation.get("value")
    if value in (None, "", "."):
        raise RuntimeError(f"FRED {series_id} 最新值不可用")
    return {
        "series_id": series_id,
        "value": float(value),
        "date": observation.get("date"),
        "status": "ok",
        "source": "fred",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }
