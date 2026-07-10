from __future__ import annotations

from datetime import datetime
from typing import Any


def get_quote(symbol: str) -> dict[str, Any]:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"yfinance 未安装: {exc}") from exc

    history = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
    if history.empty:
        raise RuntimeError(f"{symbol} 返回数据为空")

    closes = history["Close"].dropna()
    if len(closes) == 0:
        raise RuntimeError(f"{symbol} Close 数据为空")

    close = float(closes.iloc[-1])
    previous_close = float(closes.iloc[-2]) if len(closes) >= 2 else close
    change_pct = 0.0 if previous_close == 0 else (close / previous_close - 1) * 100
    last_index = closes.index[-1]
    published_at = last_index.isoformat() if hasattr(last_index, "isoformat") else str(last_index)
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    return {
        "close": round(close, 4),
        "previous_close": round(previous_close, 4),
        "change_pct": round(change_pct, 2),
        "status": "ok",
        "source": "yfinance",
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "fetched_at": retrieved_at,
        "freshness_status": "fresh",
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }
