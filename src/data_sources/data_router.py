from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Any, Callable
from urllib.request import Request, urlopen

from src.data_sources import alpha_vantage_client, finnhub_client, fred_client, yfinance_client
from src.data_sources.data_cache import read_cache, write_cache
from utils.logger import write_log


MARKET_TICKERS = [
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

US_ETF_TICKERS = {"VOO", "QQQ", "GLD", "TLT", "IEF", "UUP"}
HK_CN_TICKERS = {"3067.HK", "3033.HK", "2800.HK", "510300.SS"}
YFINANCE_ONLY_TICKERS = {"^GSPC", "^IXIC", "DX-Y.NYB"}

MACRO_SERIES = {
    "DGS10": "美国10年国债收益率",
    "CPIAUCSL": "CPI",
    "PPIACO": "PPI",
    "PCEPI": "PCE",
    "UNRATE": "失业率",
    "GDP": "GDP",
}


def _failed_item(symbol: str, errors: list[str]) -> dict[str, Any]:
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    return {
        "close": None,
        "previous_close": None,
        "change_pct": None,
        "status": "failed",
        "source": "unavailable",
        "published_at": None,
        "retrieved_at": retrieved_at,
        "fetched_at": retrieved_at,
        "freshness_status": "missing",
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
        "error": "；".join(errors[-3:]) if errors else "数据缺失，不做激进判断",
    }


def _with_symbol(symbol: str, data: dict[str, Any]) -> dict[str, Any]:
    return {**data, "symbol": symbol, "status": data.get("status", "ok")}


def _try_provider(
    provider_name: str,
    fn: Callable[[str], dict[str, Any]],
    symbol: str,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        data = fn(symbol)
        write_cache("quote", symbol, data, provider_name)
        write_log(f"{symbol} 行情获取成功：{provider_name}", filename="data_router.log")
        return _with_symbol(symbol, data)
    except Exception as exc:  # noqa: BLE001 - any source failure should degrade
        message = f"{symbol} {provider_name} 获取失败：{exc}"
        errors.append(message)
        write_log(message, filename="data_router.log")
        return None


def _official_vix_quote() -> dict[str, Any]:
    url = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - controlled Cboe URL
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or {}
    close = float(data.get("current_price") or data.get("close"))
    previous_close = close - float(data.get("price_change") or 0)
    change_pct = 0.0 if previous_close == 0 else (close / previous_close - 1) * 100
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    published_at = payload.get("timestamp") or retrieved_at
    return {
        "close": round(close, 4),
        "previous_close": round(previous_close, 4),
        "change_pct": round(change_pct, 2),
        "status": "ok",
        "source": "cboe_official",
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "fetched_at": retrieved_at,
        "freshness_status": "fresh",
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }


def get_market_quote(symbol: str) -> dict[str, Any]:
    errors: list[str] = []

    if symbol == "^VIX":
        data = _try_provider("cboe_official", lambda _: _official_vix_quote(), symbol, errors)
        if data:
            return data

    if symbol in US_ETF_TICKERS:
        for provider_name, fn in [
            ("alpha_vantage", alpha_vantage_client.get_quote),
            ("finnhub", finnhub_client.get_quote),
            ("yfinance", yfinance_client.get_quote),
        ]:
            data = _try_provider(provider_name, fn, symbol, errors)
            if data:
                return data
    elif symbol in HK_CN_TICKERS:
        for provider_name, fn in [
            ("finnhub", finnhub_client.get_quote),
            ("yfinance", yfinance_client.get_quote),
        ]:
            data = _try_provider(provider_name, fn, symbol, errors)
            if data:
                return data
    else:
        for provider_name, fn in [
            ("yfinance", yfinance_client.get_quote),
        ]:
            data = _try_provider(provider_name, fn, symbol, errors)
            if data:
                return data

    cached = read_cache("quote", symbol)
    if cached:
        write_log(f"{symbol} 使用缓存行情：{cached.get('source')}", filename="data_router.log")
        return _with_symbol(symbol, {**cached, "status": "ok", "source": f"cache:{cached.get('source', 'unknown')}"})

    write_log(f"{symbol} 行情全部失败，数据缺失，不做激进判断", filename="data_router.log")
    return _failed_item(symbol, errors)


def get_macro_snapshot() -> dict[str, Any]:
    items: dict[str, Any] = {}
    errors: list[str] = []

    for series_id, name in MACRO_SERIES.items():
        try:
            data = fred_client.get_series_latest(series_id)
            write_cache("macro", series_id, data, "fred")
            items[series_id] = {**data, "name": name}
            write_log(f"宏观数据获取成功：{series_id} from FRED", filename="data_router.log")
        except Exception as exc:  # noqa: BLE001 - macro missing should not break reports
            message = f"宏观数据 {series_id} FRED 获取失败：{exc}"
            errors.append(message)
            write_log(message, filename="data_router.log")
            cached = read_cache("macro", series_id)
            if cached:
                items[series_id] = {
                    **cached,
                    "name": name,
                    "status": "ok",
                    "source": f"cache:{cached.get('source', 'unknown')}",
                }
            else:
                items[series_id] = {
                    "series_id": series_id,
                    "name": name,
                    "value": None,
                    "date": None,
                    "status": "missing",
                    "source": "unavailable",
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "cache_used": False,
                    "cache_stale": False,
                    "warning": "数据缺失，不做激进判断。",
                }

    return {
        "source": "fred_cache_router",
        "items": items,
        "errors": errors,
        "missing": [key for key, value in items.items() if value.get("status") == "missing"],
    }


def get_news_and_earnings(symbols: list[str] | None = None) -> dict[str, Any]:
    symbols = symbols or ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"]
    end = date.today()
    start = end - timedelta(days=7)
    results: dict[str, Any] = {}
    errors: list[str] = []
    for symbol in symbols:
        try:
            results[symbol] = finnhub_client.get_company_news(symbol, start.isoformat(), end.isoformat())[:5]
        except Exception as exc:  # noqa: BLE001 - news is optional
            errors.append(f"{symbol} 新闻/财报获取失败：{exc}")
            write_log(errors[-1], filename="data_router.log")
    return {"source": "finnhub", "items": results, "errors": errors, "optional": True}


def _quality_score(items: dict[str, dict[str, Any]], macro: dict[str, Any]) -> int:
    total = 0
    count = 0

    for symbol in ["VOO", "QQQ", "TLT", "GLD", "^VIX", "3067.HK", "510300.SS"]:
        item = items.get(symbol, {})
        count += 1
        if item.get("status") != "ok":
            continue
        score = 100
        source = str(item.get("source", ""))
        if source.startswith("cache"):
            score -= 30
        if item.get("cache_stale"):
            score -= 30
        if source == "yfinance":
            score -= 15
        total += max(0, score)

    macro_items = macro.get("items", {})
    for series_id in ["DGS10", "CPIAUCSL", "UNRATE", "GDP"]:
        item = macro_items.get(series_id, {})
        count += 1
        if item.get("status") == "missing":
            continue
        score = 100
        source = str(item.get("source", ""))
        if source.startswith("cache"):
            score -= 30
        if item.get("cache_stale"):
            score -= 30
        total += max(0, score)

    return round(total / count) if count else 0


def _build_quality_report(items: dict[str, dict[str, Any]], macro: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    key_rows: list[dict[str, Any]] = []
    for symbol in ["VOO", "QQQ", "TLT", "GLD", "^VIX", "3067.HK", "510300.SS"]:
        item = items.get(symbol, {})
        key_rows.append(
            {
                "name": symbol,
                "source": item.get("source", "unavailable"),
                "fetched_at": item.get("fetched_at"),
                "value": item.get("close"),
                "is_realtime": bool(item.get("is_realtime", False)),
                "cache_used": bool(item.get("cache_used", False)),
                "cache_stale": bool(item.get("cache_stale", False)),
                "missing": item.get("status") != "ok",
                "warning": item.get("warning", "") or item.get("error", ""),
            }
        )

    for series_id, label in [("DGS10", "10Y Treasury"), ("CPIAUCSL", "CPI"), ("UNRATE", "Unemployment"), ("GDP", "GDP")]:
        item = macro.get("items", {}).get(series_id, {})
        key_rows.append(
            {
                "name": label,
                "source": item.get("source", "unavailable"),
                "fetched_at": item.get("fetched_at") or item.get("date"),
                "value": item.get("value"),
                "is_realtime": False,
                "cache_used": bool(item.get("cache_used", False)),
                "cache_stale": bool(item.get("cache_stale", False)),
                "missing": item.get("status") == "missing",
                "warning": item.get("warning", "") or item.get("error", ""),
            }
        )

    market_available = any(item.get("status") == "ok" for item in items.values())
    macro_available = not macro.get("missing")
    only_yfinance = market_available and all(
        item.get("status") != "ok" or item.get("source") in {"yfinance"} or str(item.get("source", "")).startswith("cache")
        for item in items.values()
    )
    critical_missing = any(row["missing"] for row in key_rows if row["name"] in {"VOO", "QQQ", "^VIX", "10Y Treasury"})
    stale_cache_used = any(row["cache_stale"] for row in key_rows)
    blocking_errors = [
        f"{row['name']} 数据缺失"
        for row in key_rows
        if row["missing"] and row["name"] in {"VOO", "QQQ", "^VIX", "10Y Treasury"}
    ]

    return {
        "score": _quality_score(items, macro),
        "key_rows": key_rows,
        "market_available": market_available,
        "macro_available": macro_available,
        "only_yfinance": only_yfinance,
        "critical_missing": critical_missing,
        "stale_cache_used": stale_cache_used,
        "news_available": bool(news.get("items")),
        "missing_count": sum(1 for row in key_rows if row["missing"]),
        "blocking_errors": blocking_errors,
        "warnings": [row["warning"] for row in key_rows if row["warning"]],
    }


def fetch_layered_market_data() -> dict[str, Any]:
    items = {ticker: get_market_quote(ticker) for ticker in MARKET_TICKERS}
    macro = get_macro_snapshot()
    news = get_news_and_earnings()
    quality = _build_quality_report(items, macro, news)
    errors = []
    for item in items.values():
        if item.get("status") != "ok" and item.get("error"):
            errors.append(str(item["error"]))
    errors.extend(macro.get("errors", []))
    errors.extend(news.get("errors", []))

    return {
        "source": "layered_router",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "macro": macro,
        "news": news,
        "data_quality": quality,
        "errors": errors,
    }
