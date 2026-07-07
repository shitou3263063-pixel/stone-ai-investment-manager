from __future__ import annotations

from typing import Any

from src.data_sources.data_router import MARKET_TICKERS, fetch_layered_market_data
from utils.logger import write_log


TICKERS = MARKET_TICKERS


def fetch_yfinance_market_data() -> dict[str, Any]:
    """Compatibility wrapper for the new layered data router.

    Older code still imports this function name, but the implementation now
    routes through Alpha Vantage, Finnhub, official sources, yfinance, and cache.
    """

    try:
        return fetch_layered_market_data()
    except Exception as error:  # noqa: BLE001 - data failures must never break reports
        message = f"多源数据路由异常，回退到手动 market_data.csv：{error}"
        write_log(message, filename="data_router.log")
        return {
            "source": "manual_fallback",
            "fetched_at": "",
            "items": {},
            "macro": {},
            "news": {},
            "data_quality": {
                "score": 0,
                "key_rows": [],
                "market_available": False,
                "macro_available": False,
                "only_yfinance": False,
                "critical_missing": True,
                "stale_cache_used": False,
                "news_available": False,
                "missing_count": 999,
                "warnings": [message],
            },
            "errors": [message],
        }
