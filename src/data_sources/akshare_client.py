from __future__ import annotations

from datetime import date, datetime, timedelta
from importlib import metadata
import json
import math
import multiprocessing
from pathlib import Path
from queue import Empty
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.data_sources.data_cache import read_cache, write_cache
from utils.data_loader import project_root
from utils.logger import write_log


SOURCE = "akshare"
SOURCE_LEVEL = 3
TIMEZONE_CN = "Asia/Shanghai"
TIMEZONE_HK = "Asia/Hong_Kong"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_RETRIES = 2
CIRCUIT_FAILURE_THRESHOLD = 3
PRICE_CONFLICT_THRESHOLD_PCT = 5.0
VALUATION_CONFLICT_THRESHOLD_PCT = 20.0

UNDERLYING_SOURCES = {
    "trade_calendar": "sina_finance",
    "002558_valuation": "eastmoney",
    "002558_scale": "eastmoney",
    "002558_fundamental": "eastmoney",
    "002558_cashflow": "eastmoney",
    "csi300_valuation": "csindex_official",
    "002558_history": "eastmoney",
    "510300_history": "eastmoney",
    "513060_history": "eastmoney",
    "513090_history": "eastmoney",
    "03033_history": "sina_finance",
    "hstech_history": "sina_finance",
}

OFFICIAL_NAMES = {
    "002558.SZ": "巨人网络",
    "510300.SS": "沪深300ETF",
    "513060.SS": "恒生医疗ETF",
    "513090.SS": "香港证券ETF",
    "03033.HK": "南方东英恒生科技指数ETF",
    "HSTECH": "恒生科技指数",
    "000300.SH": "沪深300指数",
}

_INTERFACE_FAILURES: dict[str, int] = {}
_PROVIDER_FAILURES: dict[str, int] = {}


def _now(timezone_name: str = TIMEZONE_CN) -> str:
    return datetime.now(tz=ZoneInfo(timezone_name)).isoformat(timespec="seconds")


def installed_version() -> str | None:
    try:
        return metadata.version("akshare")
    except metadata.PackageNotFoundError:
        return None


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _worker(
    api_name: str,
    kwargs: dict[str, Any],
    row_mode: str,
    max_rows: int,
    output: multiprocessing.Queue,
) -> None:
    try:
        import akshare as ak

        function = getattr(ak, api_name, None)
        if function is None:
            raise AttributeError(f"AKShare接口不存在：{api_name}")
        frame = function(**kwargs)
        if frame is None or not hasattr(frame, "columns"):
            raise TypeError("AKShare响应不是DataFrame")
        if row_mode == "head":
            frame = frame.head(max_rows)
        elif row_mode == "tail":
            frame = frame.tail(max_rows)
        elif len(frame) > max_rows:
            frame = frame.head(max_rows)
        records = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
        output.put({"ok": True, "columns": [str(value) for value in frame.columns], "records": records})
    except Exception as exc:  # noqa: BLE001 - child process must return a safe error
        output.put({"ok": False, "error_type": type(exc).__name__, "error_message": str(exc)[:500]})


def _execute_api_with_timeout(
    api_name: str,
    kwargs: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    row_mode: str = "head",
    max_rows: int = 200,
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    output: multiprocessing.Queue = context.Queue(maxsize=1)
    process = context.Process(target=_worker, args=(api_name, kwargs, row_mode, max_rows, output), daemon=True)
    process.start()
    try:
        result = output.get(timeout=max(1, timeout_seconds))
    except Empty as exc:
        process.terminate()
        process.join(timeout=2)
        raise TimeoutError(f"AKShare接口{api_name}超过{timeout_seconds}秒未返回") from exc
    finally:
        if process.is_alive():
            process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        output.close()
    if not result.get("ok"):
        error_type = result.get("error_type") or "UnknownError"
        raise RuntimeError(f"{error_type}: {result.get('error_message') or 'AKShare调用失败'}")
    return result


def _classify_error(exc: BaseException | str) -> tuple[str, str]:
    message = str(exc or "未知错误")[:500]
    lowered = message.lower()
    if isinstance(exc, TimeoutError) or "timeout" in lowered or "超过" in message and "秒" in message:
        return "TIMEOUT", "AKShare接口超时"
    if "ssl" in lowered or "certificate" in lowered:
        return "SSL_ERROR", f"AKShare底层连接SSL失败：{message}"
    if "proxy" in lowered or "connection" in lowered or "remotedisconnected" in lowered:
        return "NETWORK_ERROR", f"AKShare底层网络失败：{message}"
    if "接口不存在" in message or "attributeerror" in lowered:
        return "SCHEMA_CHANGED", message
    if "dataframe" in lowered or "column" in lowered or "字段" in message:
        return "SCHEMA_CHANGED", message
    if "empty" in lowered or "空" in message:
        return "EMPTY_RESPONSE", "AKShare返回空数据"
    return "UNKNOWN_ERROR", message


def _safe_float(value: Any) -> float | None:
    try:
        if value in {None, "", "--", "-"}:
            return None
        result = float(value)
        return None if math.isnan(result) or math.isinf(result) else result
    except (TypeError, ValueError):
        return None


def _normalise_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text[:10]
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _column(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return _clean_value(row.get(name))
    return None


def _freshness(market_date: str | None, *, max_age_days: int, timezone_name: str = TIMEZONE_CN) -> str:
    if not market_date:
        return "unknown"
    try:
        observed = date.fromisoformat(market_date)
    except ValueError:
        return "invalid"
    current = datetime.now(tz=ZoneInfo(timezone_name)).date()
    if observed > current:
        return "invalid"
    return "fresh" if (current - observed).days <= max_age_days else "stale"


def _base_record(
    *,
    interface: str,
    symbol: str,
    official_name: str,
    underlying_provider: str,
    market_date: str | None,
    reporting_period: str | None,
    currency: str,
    unit: str,
    status: str,
    freshness: str,
    confidence: str,
    fallback_used: bool = False,
    error_code: str | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "interface": interface,
        "symbol": symbol,
        "official_name": official_name,
        "source": SOURCE,
        "source_level": SOURCE_LEVEL,
        "source_type": "monitored_aggregator_fallback",
        "underlying_provider": underlying_provider,
        "fetched_at": _now(TIMEZONE_HK if currency == "HKD" else TIMEZONE_CN),
        "market_date": market_date,
        "reporting_period": reporting_period,
        "currency": currency,
        "unit": unit,
        "status": status,
        "freshness": freshness,
        "confidence": confidence,
        "fallback_used": fallback_used,
        "error_code": error_code,
        "error_message": error_message[:500],
        "scoring_eligible": status in {"ok", "cached"} and freshness == "fresh" and confidence in {"medium", "high"},
    }


def _empty_record(interface: str, symbol: str, official_name: str, underlying_provider: str, error_code: str, error_message: str) -> dict[str, Any]:
    return _base_record(
        interface=interface,
        symbol=symbol,
        official_name=official_name,
        underlying_provider=underlying_provider,
        market_date=None,
        reporting_period=None,
        currency="HKD" if symbol.endswith(".HK") or symbol == "HSTECH" else "CNY",
        unit="not_available",
        status="failed" if error_code != "CIRCUIT_OPEN" else "circuit_open",
        freshness="unavailable",
        confidence="low",
        error_code=error_code,
        error_message=error_message,
    )


def _fetch_interface(
    *,
    interface: str,
    api_name: str,
    kwargs: dict[str, Any],
    symbol: str,
    official_name: str,
    normalizer: Callable[[dict[str, Any]], dict[str, Any]],
    row_mode: str = "head",
    max_rows: int = 200,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    provider = UNDERLYING_SOURCES[interface]
    cache_key = f"p1a_{interface}"
    if _INTERFACE_FAILURES.get(interface, 0) >= CIRCUIT_FAILURE_THRESHOLD:
        error_code, error_message = "CIRCUIT_OPEN", f"{interface}本轮连续失败达到阈值，已停止继续请求"
        live_record = _empty_record(interface, symbol, official_name, provider, error_code, error_message)
    else:
        live_record = {}
        last_error: tuple[str, str] = ("UNKNOWN_ERROR", "AKShare接口调用失败")
        for attempt in range(MAX_RETRIES + 1):
            if _INTERFACE_FAILURES.get(interface, 0) >= CIRCUIT_FAILURE_THRESHOLD:
                last_error = ("CIRCUIT_OPEN", f"{interface}本轮连续失败达到阈值，已停止继续请求")
                break
            try:
                payload = _execute_api_with_timeout(
                    api_name,
                    kwargs,
                    timeout_seconds=timeout_seconds,
                    row_mode=row_mode,
                    max_rows=max_rows,
                )
                if not payload.get("records"):
                    raise ValueError("AKShare返回空数据")
                live_record = normalizer(payload)
                if live_record.get("status") not in {"ok", "partial"}:
                    raise ValueError(live_record.get("error_message") or "AKShare字段结构异常")
                live_record["attempts"] = attempt + 1
                _INTERFACE_FAILURES[interface] = 0
                _PROVIDER_FAILURES[provider] = 0
                write_cache("akshare", cache_key, live_record, SOURCE)
                return live_record
            except Exception as exc:  # noqa: BLE001 - optional fallback must degrade
                last_error = _classify_error(exc)
                _INTERFACE_FAILURES[interface] = _INTERFACE_FAILURES.get(interface, 0) + 1
                _PROVIDER_FAILURES[provider] = _PROVIDER_FAILURES.get(provider, 0) + 1
                write_log(
                    f"AKShare {interface}失败[{last_error[0]}] attempt={attempt + 1}: {last_error[1]}",
                    filename="stone_ai.log",
                )
                if last_error[0] in {"SCHEMA_CHANGED", "EMPTY_RESPONSE"}:
                    break
        live_record = _empty_record(interface, symbol, official_name, provider, *last_error)

    cached = read_cache("akshare", cache_key, max_age_days=7)
    if cached and cached.get("symbol") == symbol:
        cache_stale = bool(cached.get("cache_stale")) or cached.get("freshness") == "stale"
        explicit_date_required = interface in {"002558_valuation", "002558_scale"}
        date_is_explicit = bool(cached.get("market_date")) and "provider_has_no_explicit" not in str(cached.get("date_basis") or "")
        return {
            **cached,
            "status": "cached",
            "fallback_used": True,
            "cache_age_days": cached.get("cache_age_days"),
            "cache_stale": cache_stale,
            "scoring_eligible": (
                bool(cached.get("scoring_eligible"))
                and not cache_stale
                and (not explicit_date_required or date_is_explicit)
            ),
            "upstream_error_code": live_record.get("error_code"),
            "upstream_error_message": live_record.get("error_message"),
        }
    return live_record


def _normalise_trade_calendar(payload: dict[str, Any]) -> dict[str, Any]:
    dates = sorted(filter(None, (_normalise_date(row.get("trade_date")) for row in payload["records"])))
    if not dates:
        return _empty_record("trade_calendar", "SSE", "A股交易日历", "sina_finance", "SCHEMA_CHANGED", "缺少trade_date字段")
    today = datetime.now(tz=ZoneInfo(TIMEZONE_CN)).date().isoformat()
    latest_open = max((value for value in dates if value <= today), default=None)
    next_open = min((value for value in dates if value > today), default=None)
    freshness = _freshness(latest_open, max_age_days=4)
    result = _base_record(
        interface="trade_calendar",
        symbol="SSE",
        official_name="A股交易日历",
        underlying_provider="sina_finance",
        market_date=latest_open,
        reporting_period=None,
        currency="CNY",
        unit="calendar_day",
        status="ok" if latest_open else "failed",
        freshness=freshness,
        confidence="medium",
    )
    result.update({"latest_open_date": latest_open, "next_open_date": next_open, "records_count": len(dates)})
    return result


def _normalise_002558_valuation(payload: dict[str, Any]) -> dict[str, Any]:
    target = next((row for row in payload["records"] if str(_column(row, "代码") or "").zfill(6) == "002558"), None)
    if not target:
        return _empty_record("002558_valuation", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "估值响应缺少002558")
    pe = _safe_float(_column(target, "市盈率-TTM"))
    pb = _safe_float(_column(target, "市净率-MRQ"))
    if pe is None and pb is None:
        return _empty_record("002558_valuation", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "缺少PE/PB字段")
    result = _base_record(
        interface="002558_valuation",
        symbol="002558.SZ",
        official_name=OFFICIAL_NAMES["002558.SZ"],
        underlying_provider="eastmoney",
        market_date=None,
        reporting_period=None,
        currency="CNY",
        unit="multiple",
        status="ok",
        freshness="unknown",
        confidence="low",
    )
    result.update({
        "valuation_basis": "security_itself_current_snapshot",
        "date_basis": "provider_has_no_explicit_market_date",
        "metrics": {"pe_ttm": pe, "pb": pb},
        "metric_units": {"pe_ttm": "multiple", "pb": "multiple"},
        "missing_fields": [key for key, value in {"pe_ttm": pe, "pb": pb}.items() if value is None],
    })
    return result


def _normalise_002558_scale(payload: dict[str, Any]) -> dict[str, Any]:
    target = next((row for row in payload["records"] if str(_column(row, "代码") or "").zfill(6) == "002558"), None)
    if not target:
        return _empty_record("002558_scale", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "规模响应缺少002558")
    total_market_value = _safe_float(_column(target, "总市值"))
    if total_market_value is None:
        return _empty_record("002558_scale", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "缺少总市值字段")
    result = _base_record(
        interface="002558_scale",
        symbol="002558.SZ",
        official_name=OFFICIAL_NAMES["002558.SZ"],
        underlying_provider="eastmoney",
        market_date=None,
        reporting_period=None,
        currency="CNY",
        unit="CNY",
        status="ok",
        freshness="unknown",
        confidence="low",
    )
    result.update({"metrics": {"total_market_value": total_market_value}, "date_basis": "provider_has_no_explicit_market_date"})
    return result


def _normalise_002558_fundamental(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in payload["records"] if str(row.get("SECURITY_CODE") or "").zfill(6) == "002558"]
    rows.sort(key=lambda row: str(row.get("REPORT_DATE") or ""), reverse=True)
    if not rows:
        return _empty_record("002558_fundamental", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "财务响应缺少002558")
    row = rows[0]
    period = _normalise_date(row.get("REPORT_DATE"))
    metrics = {
        "revenue": _safe_float(row.get("TOTALOPERATEREVE")),
        "net_profit_parent": _safe_float(row.get("PARENTNETPROFIT")),
        "or_yoy": _safe_float(row.get("TOTALOPERATEREVETZ")),
        "netprofit_yoy": _safe_float(row.get("PARENTNETPROFITTZ")),
        "roe": _safe_float(row.get("ROEJQ")),
        "operating_cash_flow_per_share": _safe_float(row.get("MGJYXJJE")),
        "debt_to_assets": _safe_float(row.get("ZCFZL")),
        "netprofit_margin": _safe_float(row.get("XSJLL")),
    }
    valid_count = sum(value is not None for value in metrics.values())
    if not period or valid_count < 4:
        return _empty_record("002558_fundamental", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "财务报告期或核心字段不足")
    freshness = _freshness(period, max_age_days=200)
    result = _base_record(
        interface="002558_fundamental",
        symbol="002558.SZ",
        official_name=OFFICIAL_NAMES["002558.SZ"],
        underlying_provider="eastmoney",
        market_date=None,
        reporting_period=period,
        currency=str(row.get("CURRENCY") or "CNY"),
        unit="mixed_financial_units",
        status="ok",
        freshness=freshness,
        confidence="medium" if freshness == "fresh" else "low",
    )
    indicator = {
        **result,
        "announcement_date": _normalise_date(row.get("NOTICE_DATE")),
        "metrics": metrics,
        "metric_units": {
            "revenue": "CNY",
            "net_profit_parent": "CNY",
            "or_yoy": "percent",
            "netprofit_yoy": "percent",
            "roe": "percent",
            "operating_cash_flow_per_share": "CNY_per_share",
            "debt_to_assets": "percent",
            "netprofit_margin": "percent",
        },
    }
    result.update({
        "model": "single_stock_fundamental",
        "validated_metric_count": valid_count,
        "successful_statement_count": 1,
        "statements": {"financial_indicators": indicator},
        "metrics": metrics,
        "missing_fields": [key for key, value in metrics.items() if value is None],
        "period_consistent": True,
    })
    return result


def _normalise_cashflow(payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload["records"])
    rows.sort(key=lambda row: str(row.get("REPORT_DATE") or row.get("REPORTDATE") or ""), reverse=True)
    if not rows:
        return _empty_record("002558_cashflow", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "EMPTY_RESPONSE", "现金流响应为空")
    row = rows[0]
    period = _normalise_date(row.get("REPORT_DATE") or row.get("REPORTDATE"))
    operating = _safe_float(_column(row, "NETCASH_OPERATE", "NET_CASH_OPERATE", "经营活动产生的现金流量净额"))
    if not period or operating is None:
        return _empty_record("002558_cashflow", "002558.SZ", OFFICIAL_NAMES["002558.SZ"], "eastmoney", "SCHEMA_CHANGED", "现金流响应缺少报告期或经营现金流")
    result = _base_record(
        interface="002558_cashflow",
        symbol="002558.SZ",
        official_name=OFFICIAL_NAMES["002558.SZ"],
        underlying_provider="eastmoney",
        market_date=None,
        reporting_period=period,
        currency="CNY",
        unit="CNY",
        status="ok",
        freshness=_freshness(period, max_age_days=200),
        confidence="medium",
    )
    result["metrics"] = {"operating_cash_flow": operating}
    return result


def _normalise_csi300_valuation(payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload["records"])
    rows.sort(key=lambda row: str(_column(row, "日期") or ""), reverse=True)
    if not rows:
        return _empty_record("csi300_valuation", "000300.SH", OFFICIAL_NAMES["000300.SH"], "csindex_official", "EMPTY_RESPONSE", "沪深300估值为空")
    row = rows[0]
    market_date = _normalise_date(_column(row, "日期"))
    pe = _safe_float(_column(row, "市盈率1"))
    dividend = _safe_float(_column(row, "股息率1"))
    if not market_date or pe is None:
        return _empty_record("csi300_valuation", "000300.SH", OFFICIAL_NAMES["000300.SH"], "csindex_official", "SCHEMA_CHANGED", "沪深300估值缺少日期或P/E1")
    freshness = _freshness(market_date, max_age_days=4)
    result = _base_record(
        interface="csi300_valuation",
        symbol="000300.SH",
        official_name=OFFICIAL_NAMES["000300.SH"],
        underlying_provider="csindex_official",
        market_date=market_date,
        reporting_period=None,
        currency="CNY",
        unit="mixed_valuation_units",
        status="ok",
        freshness=freshness,
        confidence="high" if freshness == "fresh" else "low",
    )
    result.update({
        "valuation_basis": "CSI300_total_share_capital_PE1_DP1",
        "metrics": {"pe_ttm": pe, "pb": None, "dividend_yield": dividend},
        "metric_units": {"pe_ttm": "multiple", "pb": "not_available", "dividend_yield": "percent"},
        "missing_fields": ["pb"],
        "price_percentile_used_as_valuation": False,
    })
    return result


def _normalise_history(interface: str, symbol: str, currency: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload["records"])
    date_names = ("日期", "date")
    close_names = ("收盘", "latest", "close")
    rows.sort(key=lambda row: str(_column(row, *date_names) or ""), reverse=True)
    if not rows:
        return _empty_record(interface, symbol, OFFICIAL_NAMES[symbol], UNDERLYING_SOURCES[interface], "EMPTY_RESPONSE", "历史行情为空")
    row = rows[0]
    market_date = _normalise_date(_column(row, *date_names))
    close = _safe_float(_column(row, *close_names))
    if not market_date or close is None or close <= 0:
        return _empty_record(interface, symbol, OFFICIAL_NAMES[symbol], UNDERLYING_SOURCES[interface], "SCHEMA_CHANGED", "历史行情缺少有效日期或收盘价")
    freshness = _freshness(market_date, max_age_days=4, timezone_name=TIMEZONE_HK if currency == "HKD" else TIMEZONE_CN)
    result = _base_record(
        interface=interface,
        symbol=symbol,
        official_name=OFFICIAL_NAMES[symbol],
        underlying_provider=UNDERLYING_SOURCES[interface],
        market_date=market_date,
        reporting_period=None,
        currency=currency,
        unit="index_points" if symbol == "HSTECH" else f"{currency}_per_share_or_unit",
        status="ok",
        freshness=freshness,
        confidence="medium" if freshness == "fresh" else "low",
    )
    result.update({
        "metrics": {
            "close": close,
            "volume": _safe_float(_column(row, "成交量", "volume")),
            "turnover": _safe_float(_column(row, "成交额", "amount")),
        },
        "adjustment": "qfq" if symbol != "HSTECH" else "not_applicable",
        "display_only": True,
    })
    return result


def _merge_valuation_and_scale(valuation: dict[str, Any], scale: dict[str, Any]) -> dict[str, Any]:
    if valuation.get("status") not in {"ok", "cached"}:
        return valuation
    metrics = dict(valuation.get("metrics", {}) or {})
    if scale.get("status") in {"ok", "cached"}:
        metrics.update(scale.get("metrics", {}) or {})
    return {
        **valuation,
        "metrics": metrics,
        "scale_status": scale.get("status"),
        "scale_error_code": scale.get("error_code"),
        "missing_fields": [key for key in ["pe_ttm", "pb", "total_market_value"] if metrics.get(key) is None],
    }


def _merge_fundamental_and_cashflow(fundamental: dict[str, Any], cashflow: dict[str, Any]) -> dict[str, Any]:
    if fundamental.get("status") not in {"ok", "cached"}:
        return fundamental
    metrics = dict(fundamental.get("metrics", {}) or {})
    period_consistent = True
    if cashflow.get("status") in {"ok", "cached"}:
        if cashflow.get("reporting_period") == fundamental.get("reporting_period"):
            metrics.update(cashflow.get("metrics", {}) or {})
        else:
            period_consistent = False
    statement = dict(((fundamental.get("statements") or {}).get("financial_indicators") or {}))
    statement["metrics"] = metrics
    return {
        **fundamental,
        "metrics": metrics,
        "statements": {"financial_indicators": statement},
        "cashflow_status": cashflow.get("status"),
        "cashflow_error_code": cashflow.get("error_code"),
        "period_consistent": period_consistent,
        "scoring_eligible": bool(fundamental.get("scoring_eligible")) and period_consistent,
        "missing_fields": [key for key in [
            "revenue", "net_profit_parent", "or_yoy", "netprofit_yoy", "roe",
            "operating_cash_flow", "debt_to_assets",
        ] if metrics.get(key) is None],
    }


def _apply_price_conflict(record: dict[str, Any], market_item: dict[str, Any]) -> dict[str, Any]:
    ak_close = _safe_float((record.get("metrics") or {}).get("close"))
    primary_close = _safe_float(market_item.get("close"))
    if record.get("status") not in {"ok", "cached"} or ak_close is None or primary_close is None or primary_close <= 0:
        return record
    ak_date = _normalise_date(record.get("market_date"))
    primary_date = _normalise_date(
        market_item.get("market_date")
        or market_item.get("comparable_date")
        or market_item.get("published_at")
    )
    primary_stale = bool(market_item.get("stale")) or str(
        market_item.get("freshness_status") or market_item.get("freshness") or "fresh"
    ) == "stale"
    primary_currency = str(market_item.get("currency") or record.get("currency") or "")
    if ak_date != primary_date or primary_stale or primary_currency != str(record.get("currency") or ""):
        return {
            **record,
            "comparison_source": market_item.get("source"),
            "comparison_status": "not_comparable",
            "comparison_reason": (
                f"交易日不一致({ak_date}/{primary_date})" if ak_date != primary_date
                else "主源数据过期" if primary_stale
                else f"币种不一致({record.get('currency')}/{primary_currency})"
            ),
        }
    difference = abs(ak_close - primary_close) / primary_close * 100
    result = {
        **record,
        "comparison_source": market_item.get("source"),
        "comparison_status": "verified",
        "comparison_difference_pct": round(difference, 4),
    }
    if difference > PRICE_CONFLICT_THRESHOLD_PCT:
        result.update({
            "status": "conflict",
            "error_code": "SOURCE_CONFLICT",
            "error_message": f"AKShare与{market_item.get('source')}收盘价差异{difference:.2f}%",
            "scoring_eligible": False,
        })
    return result


def _valuation_conflicts(tushare: dict[str, Any], akshare_items: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    primary_items = ((tushare.get("valuation") or {}).get("items") or {})
    pairs = [("002558.SZ", "002558.SZ"), ("510300.SS", "510300.SS")]
    for primary_symbol, fallback_symbol in pairs:
        primary = primary_items.get(primary_symbol, {}) or {}
        fallback = akshare_items.get(fallback_symbol, {}) or {}
        if primary.get("status") not in {"ok", "cached"} or fallback.get("status") not in {"ok", "cached"}:
            continue
        for field in ["pe_ttm", "pb"]:
            left = _safe_float((primary.get("metrics") or {}).get(field))
            right = _safe_float((fallback.get("metrics") or {}).get(field))
            if left is None or right is None or left == 0:
                continue
            difference = abs(right - left) / abs(left) * 100
            if difference > VALUATION_CONFLICT_THRESHOLD_PCT:
                conflicts.append({
                    "symbol": primary_symbol,
                    "field": field,
                    "primary_source": primary.get("source", "tushare_pro"),
                    "fallback_source": SOURCE,
                    "difference_pct": round(difference, 4),
                    "status": "SOURCE_CONFLICT",
                })
    return conflicts


def fetch_akshare_p1a_snapshot(
    tushare_snapshot: dict[str, Any],
    market_items: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _INTERFACE_FAILURES.clear()
    _PROVIDER_FAILURES.clear()
    version = installed_version()
    if not version:
        return {
            "provider": SOURCE,
            "installed": False,
            "version": None,
            "status": "disabled",
            "error_code": "IMPORT_ERROR",
            "error_message": "未安装akshare",
            "source_conflicts": [],
            "last_success_at": None,
        }

    today = datetime.now(tz=ZoneInfo(TIMEZONE_CN)).date()
    start = (today - timedelta(days=45)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    trade_calendar = _fetch_interface(
        interface="trade_calendar", api_name="tool_trade_date_hist_sina", kwargs={}, symbol="SSE",
        official_name="A股交易日历", normalizer=_normalise_trade_calendar, row_mode="tail", max_rows=400,
    )
    stock_valuation = _fetch_interface(
        interface="002558_valuation", api_name="stock_zh_valuation_comparison_em", kwargs={"symbol": "SZ002558"},
        symbol="002558.SZ", official_name=OFFICIAL_NAMES["002558.SZ"], normalizer=_normalise_002558_valuation,
        max_rows=20,
    )
    stock_scale = _fetch_interface(
        interface="002558_scale", api_name="stock_zh_scale_comparison_em", kwargs={"symbol": "SZ002558"},
        symbol="002558.SZ", official_name=OFFICIAL_NAMES["002558.SZ"], normalizer=_normalise_002558_scale,
        max_rows=10,
    )
    stock_valuation = _merge_valuation_and_scale(stock_valuation, stock_scale)
    stock_fundamental = _fetch_interface(
        interface="002558_fundamental", api_name="stock_financial_analysis_indicator_em", kwargs={"symbol": "002558.SZ"},
        symbol="002558.SZ", official_name=OFFICIAL_NAMES["002558.SZ"], normalizer=_normalise_002558_fundamental,
        max_rows=100,
    )
    cashflow = _fetch_interface(
        interface="002558_cashflow", api_name="stock_cash_flow_sheet_by_report_em", kwargs={"symbol": "SZ002558"},
        symbol="002558.SZ", official_name=OFFICIAL_NAMES["002558.SZ"], normalizer=_normalise_cashflow,
        max_rows=100,
    )
    stock_fundamental = _merge_fundamental_and_cashflow(stock_fundamental, cashflow)
    csi300 = _fetch_interface(
        interface="csi300_valuation", api_name="stock_zh_index_value_csindex", kwargs={"symbol": "000300"},
        symbol="000300.SH", official_name=OFFICIAL_NAMES["000300.SH"], normalizer=_normalise_csi300_valuation,
        max_rows=40,
    )

    history_specs = [
        ("002558_history", "stock_zh_a_hist", {"symbol": "002558", "period": "daily", "start_date": start, "end_date": end, "adjust": "qfq"}, "002558.SZ", "CNY"),
        ("510300_history", "fund_etf_hist_em", {"symbol": "510300", "period": "daily", "start_date": start, "end_date": end, "adjust": "qfq"}, "510300.SS", "CNY"),
        ("513060_history", "fund_etf_hist_em", {"symbol": "513060", "period": "daily", "start_date": start, "end_date": end, "adjust": "qfq"}, "513060.SS", "CNY"),
        ("513090_history", "fund_etf_hist_em", {"symbol": "513090", "period": "daily", "start_date": start, "end_date": end, "adjust": "qfq"}, "513090.SS", "CNY"),
        # 03033 is the held ETF; HSTECH is the index. They are fetched from
        # separate Sina endpoints and must never substitute for one another.
        ("03033_history", "stock_hk_daily", {"symbol": "03033", "adjust": "qfq"}, "03033.HK", "HKD"),
        ("hstech_history", "stock_hk_index_daily_sina", {"symbol": "HSTECH"}, "HSTECH", "HKD"),
    ]
    market_references: dict[str, Any] = {}
    for interface, api_name, kwargs, symbol, currency in history_specs:
        normalizer = lambda payload, i=interface, s=symbol, c=currency: _normalise_history(i, s, c, payload)
        record = _fetch_interface(
            interface=interface,
            api_name=api_name,
            kwargs=kwargs,
            symbol=symbol,
            official_name=OFFICIAL_NAMES[symbol],
            normalizer=normalizer,
            row_mode="tail",
            max_rows=60,
        )
        if market_items and symbol in market_items:
            record = _apply_price_conflict(record, market_items[symbol])
        market_references[symbol] = record

    valuation_items = {"002558.SZ": stock_valuation, "510300.SS": csi300}
    source_conflicts = _valuation_conflicts(tushare_snapshot, valuation_items)
    source_conflicts.extend(
        {
            "symbol": symbol,
            "field": "close",
            "primary_source": record.get("comparison_source"),
            "fallback_source": SOURCE,
            "difference_pct": record.get("comparison_difference_pct"),
            "status": "SOURCE_CONFLICT",
        }
        for symbol, record in market_references.items()
        if record.get("error_code") == "SOURCE_CONFLICT"
    )
    if source_conflicts:
        for record in valuation_items.values():
            if any(item.get("symbol") in {record.get("symbol"), "510300.SS" if record.get("symbol") == "000300.SH" else ""} for item in source_conflicts):
                record["scoring_eligible"] = False

    all_records = [trade_calendar, stock_valuation, stock_fundamental, csi300, *market_references.values()]
    success_records = [record for record in all_records if record.get("status") in {"ok", "cached", "partial"}]
    last_success = max((str(record.get("fetched_at")) for record in success_records if record.get("fetched_at")), default=None)
    snapshot = {
        "provider": SOURCE,
        "installed": True,
        "version": version,
        "status": "ok" if len(success_records) == len(all_records) else "partial" if success_records else "failed",
        "source_level": SOURCE_LEVEL,
        "fetched_at": _now(),
        "trade_calendar": trade_calendar,
        "valuation": {
            "status": "ok" if any(record.get("status") in {"ok", "cached"} for record in valuation_items.values()) else "failed",
            "items": valuation_items,
            "interfaces": {
                "002558_valuation": stock_valuation,
                "csi300_valuation": csi300,
            },
        },
        "fundamentals": {"002558.SZ": stock_fundamental},
        "market_references": market_references,
        "source_conflicts": source_conflicts,
        "last_success_at": last_success,
        "circuit_state": {
            "interfaces": dict(_INTERFACE_FAILURES),
            "underlying_providers": dict(_PROVIDER_FAILURES),
            "failure_threshold": CIRCUIT_FAILURE_THRESHOLD,
        },
        "policy": "AKShare仅为受监控备用源；底层来源、时效、字段、单位与冲突检查通过后方可进入评分。",
    }
    write_akshare_outputs(snapshot)
    return snapshot


def write_akshare_outputs(snapshot: dict[str, Any]) -> None:
    output_dir = project_root() / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    validation = {
        "generated_at": _now(),
        "akshare_status": snapshot.get("status"),
        "akshare_version": snapshot.get("version"),
        "akshare_trade_calendar_status": (snapshot.get("trade_calendar") or {}).get("status", "missing"),
        "akshare_002558_valuation_status": (((snapshot.get("valuation") or {}).get("interfaces") or {}).get("002558_valuation") or {}).get("status", "missing"),
        "akshare_002558_fundamental_status": ((snapshot.get("fundamentals") or {}).get("002558.SZ") or {}).get("status", "missing"),
        "akshare_csi300_valuation_status": (((snapshot.get("valuation") or {}).get("interfaces") or {}).get("csi300_valuation") or {}).get("status", "missing"),
        "akshare_source_conflicts": snapshot.get("source_conflicts", []),
        "akshare_last_success_at": snapshot.get("last_success_at"),
        "circuit_state": snapshot.get("circuit_state", {}),
        "no_missing_value_filled_with_zero": True,
        "etf_financial_model_prohibited": True,
        "csi300_price_percentile_used_as_valuation": False,
    }
    (output_dir / "akshare_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "akshare_data_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    traces = []
    for record in [
        snapshot.get("trade_calendar", {}),
        *(((snapshot.get("valuation") or {}).get("items") or {}).values()),
        *((snapshot.get("fundamentals") or {}).values()),
        *((snapshot.get("market_references") or {}).values()),
    ]:
        traces.append({
            "interface": record.get("interface"),
            "symbol": record.get("symbol"),
            "source": record.get("source"),
            "underlying_provider": record.get("underlying_provider"),
            "status": record.get("status"),
            "freshness": record.get("freshness"),
            "fallback_used": record.get("fallback_used"),
            "scoring_eligible": record.get("scoring_eligible"),
            "display_only": record.get("display_only", False),
            "error_code": record.get("error_code"),
            "error_message": record.get("error_message"),
        })
    (output_dir / "akshare_source_trace.json").write_text(json.dumps({
        "generated_at": _now(),
        "provider": SOURCE,
        "version": snapshot.get("version"),
        "records": traces,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
