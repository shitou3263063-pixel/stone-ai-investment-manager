from __future__ import annotations

from datetime import datetime
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://www.alphavantage.co/query"


def _api_key() -> str:
    return os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()


def _get_json(params: dict[str, str]) -> dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY 未配置")
    params = {**params, "apikey": key}
    url = f"{BASE_URL}?{urlencode(params)}"
    with urlopen(url, timeout=20) as response:  # noqa: S310 - controlled public API URL
        return json.loads(response.read().decode("utf-8"))


def get_quote(symbol: str) -> dict[str, Any]:
    payload = _get_json({"function": "GLOBAL_QUOTE", "symbol": symbol})
    quote = payload.get("Global Quote") or {}
    if not quote:
        note = payload.get("Note") or payload.get("Information") or "Alpha Vantage 返回空行情"
        raise RuntimeError(str(note))

    close = float(quote.get("05. price"))
    previous_close = float(quote.get("08. previous close") or close)
    change_pct = 0.0 if previous_close == 0 else (close / previous_close - 1) * 100
    return {
        "close": round(close, 4),
        "previous_close": round(previous_close, 4),
        "change_pct": round(change_pct, 2),
        "status": "ok",
        "source": "alpha_vantage",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }
