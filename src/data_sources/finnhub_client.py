from __future__ import annotations

from datetime import datetime
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://finnhub.io/api/v1"


def _api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("FINNHUB_API_KEY 未配置")
    url = f"{BASE_URL}/{path}?{urlencode({**params, 'token': key})}"
    with urlopen(url, timeout=20) as response:  # noqa: S310 - controlled public API URL
        return json.loads(response.read().decode("utf-8"))


def get_quote(symbol: str) -> dict[str, Any]:
    payload = _get_json("quote", {"symbol": symbol})
    close = float(payload.get("c") or 0)
    previous_close = float(payload.get("pc") or close)
    if close <= 0:
        raise RuntimeError(f"Finnhub 返回无效行情: {payload}")

    change_pct = 0.0 if previous_close == 0 else (close / previous_close - 1) * 100
    return {
        "close": round(close, 4),
        "previous_close": round(previous_close, 4),
        "change_pct": round(change_pct, 2),
        "status": "ok",
        "source": "finnhub",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }


def get_company_news(symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    return list(_get_json("company-news", {"symbol": symbol, "from": start_date, "to": end_date}) or [])
