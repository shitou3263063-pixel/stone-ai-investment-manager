from __future__ import annotations

from datetime import date, datetime
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://api.stlouisfed.org/fred"
FRESHNESS_DAYS = {
    "DGS10": 10,
    "DGS2": 10,
    "T10Y2Y": 10,
    "BAMLH0A0HYM2": 10,
    "CPIAUCSL": 60,
    "PPIACO": 60,
    "PCEPI": 60,
    "UNRATE": 60,
    "GDP": 150,
}


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
            "limit": "2",
        },
    )
    observations = payload.get("observations") or []
    if not observations:
        raise RuntimeError(f"FRED {series_id} 返回空数据")
    observation = observations[0]
    value = observation.get("value")
    if value in (None, "", "."):
        raise RuntimeError(f"FRED {series_id} 最新值不可用")
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    previous_value = None
    if len(observations) > 1 and observations[1].get("value") not in (None, "", "."):
        previous_value = float(observations[1]["value"])
    published_date = observation.get("date")
    try:
        age_days = max(0, (date.today() - date.fromisoformat(str(published_date))).days)
    except (TypeError, ValueError):
        age_days = 9999
    stale = age_days > FRESHNESS_DAYS.get(series_id, 60)
    return {
        "series_id": series_id,
        "value": float(value),
        "previous_value": previous_value,
        "date": published_date,
        "status": "ok",
        "source": "fred",
        "published_at": published_date,
        "retrieved_at": retrieved_at,
        "fetched_at": retrieved_at,
        "freshness_status": "stale" if stale else "fresh",
        "stale": stale,
        "age_days": age_days,
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }
