from __future__ import annotations

from datetime import datetime
from typing import Any

from utils.logger import write_log


TICKERS = [
    "VOO",
    "QQQ",
    "^GSPC",
    "^IXIC",
    "3067.HK",
    "3033.HK",
    "2800.HK",
    "510300.SS",
    "GLD",
    "TLT",
    "IEF",
    "UUP",
    "DX-Y.NYB",
    "^VIX",
]


def fetch_yfinance_market_data() -> dict[str, Any]:
    """使用 yfinance 获取行情。

    任何 ticker 失败都不会让主程序崩溃，而是写入日志并继续。
    """

    result: dict[str, Any] = {
        "source": "yfinance",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "items": {},
        "errors": [],
    }

    try:
        import yfinance as yf  # type: ignore
    except ImportError as error:
        message = f"yfinance 未安装，跳过实时行情获取：{error}"
        write_log(message)
        result["source"] = "manual_fallback"
        result["errors"].append(message)
        return result

    for ticker in TICKERS:
        try:
            history = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
            if history.empty:
                raise ValueError("返回数据为空")

            closes = history["Close"].dropna()
            if len(closes) == 0:
                raise ValueError("Close 数据为空")

            latest_close = float(closes.iloc[-1])
            previous_close = float(closes.iloc[-2]) if len(closes) >= 2 else latest_close
            change_pct = 0.0 if previous_close == 0 else (latest_close / previous_close - 1) * 100

            result["items"][ticker] = {
                "close": round(latest_close, 4),
                "previous_close": round(previous_close, 4),
                "change_pct": round(change_pct, 2),
                "status": "ok",
            }
            write_log(f"{ticker} 获取成功：close={latest_close:.4f}, change={change_pct:.2f}%")
        except Exception as error:  # noqa: BLE001 - 行情失败不应中断日报
            message = f"{ticker} 获取失败：{error}"
            write_log(message)
            result["items"][ticker] = {
                "close": None,
                "previous_close": None,
                "change_pct": None,
                "status": "failed",
                "error": str(error),
            }
            result["errors"].append(message)

    return result
