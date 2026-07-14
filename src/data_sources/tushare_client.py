from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import os
import re
import socket
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from src.data_sources.data_cache import read_cache, write_cache
from utils.logger import write_log


API_URL = "https://api.tushare.pro"
SOURCE = "tushare_pro"
SOURCE_LEVEL = 2
TIMEZONE = "Asia/Shanghai"

ERROR_CODES = {
    "TOKEN_INVALID",
    "PERMISSION_DENIED",
    "INSUFFICIENT_POINTS",
    "RATE_LIMITED",
    "NETWORK_TIMEOUT",
    "SSL_ERROR",
    "RESPONSE_SCHEMA_ERROR",
    "EMPTY_RESPONSE",
    "UNKNOWN_ERROR",
}


class TushareClientError(RuntimeError):
    """可安全写入日志的 Tushare 错误，不包含 Token 或请求头。"""

    def __init__(self, error_code: str, summary: str) -> None:
        self.error_code = error_code if error_code in ERROR_CODES else "UNKNOWN_ERROR"
        self.summary = _sanitize_error(summary)
        super().__init__(f"{self.error_code}: {self.summary}")


def _configured_token(token: str | None = None) -> str:
    return (token if token is not None else os.getenv("TUSHARE_TOKEN", "")).strip()


def _sanitize_error(message: Any) -> str:
    """删除可能出现在异常文本中的 Token、Authorization 和请求头。"""
    text = str(message or "未知错误")
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if token:
        text = text.replace(token, "***")
    text = re.sub(r"(?i)(token|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+", r"\1=***", text)
    return text[:500]


def classify_tushare_error(exc: BaseException | str) -> tuple[str, str]:
    """把 SDK/HTTP/官方响应统一映射为稳定、可审计的错误类别。"""
    if isinstance(exc, TushareClientError):
        return exc.error_code, exc.summary
    message = _sanitize_error(exc)
    lowered = message.lower()
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        return "NETWORK_TIMEOUT", "连接 Tushare 官方接口超时"
    if isinstance(exc, ssl.SSLError) or "ssl" in lowered or "certificate" in lowered:
        return "SSL_ERROR", f"Tushare SSL 连接失败：{message}"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "NETWORK_TIMEOUT", "连接 Tushare 官方接口超时"
        if isinstance(reason, ssl.SSLError):
            return "SSL_ERROR", f"Tushare SSL 连接失败：{_sanitize_error(reason)}"
        return "UNKNOWN_ERROR", f"Tushare 网络请求失败：{_sanitize_error(reason or message)}"
    if "tushare_token_not_configured" in lowered:
        return "TOKEN_INVALID", "未配置 TUSHARE_TOKEN"
    if any(key in message for key in ["无效token", "Token无效", "token不对", "token错误", "验证失败"]):
        return "TOKEN_INVALID", "TUSHARE_TOKEN 无效或认证失败"
    if "积分" in message and any(key in message for key in ["不足", "不够", "至少", "需要"]):
        return "INSUFFICIENT_POINTS", f"Tushare 积分不足：{message}"
    if any(key in message for key in ["没有接口", "无接口", "访问权限", "无权访问", "permission"]):
        return "PERMISSION_DENIED", f"Tushare 接口权限不足：{message}"
    if any(key in message for key in ["频率", "每分钟", "访问次数", "rate limit", "too many request"]):
        return "RATE_LIMITED", f"Tushare 请求频率受限：{message}"
    if "empty_response" in lowered or "empty response" in lowered:
        return "EMPTY_RESPONSE", "Tushare 返回空数据"
    if isinstance(exc, (json.JSONDecodeError, TypeError, KeyError, ValueError)) or "schema" in lowered:
        return "RESPONSE_SCHEMA_ERROR", f"Tushare 响应结构异常：{message}"
    if isinstance(exc, HTTPError):
        return "UNKNOWN_ERROR", f"Tushare HTTP错误：{exc.code}"
    return "UNKNOWN_ERROR", message


def _now() -> str:
    return datetime.now(tz=ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")


def _post_json(payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    request = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Stone-AI-Investment-Manager/12.6"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official API endpoint
        raw = response.read().decode("utf-8")
    if not raw.strip():
        raise TushareClientError("EMPTY_RESPONSE", "Tushare 返回空响应")
    try:
        payload_out = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare 返回的内容不是有效 JSON") from exc
    if not isinstance(payload_out, dict):
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare 顶层响应不是对象")
    return payload_out


def _rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare 响应不是对象")
    try:
        code = int(payload.get("code", -1) or 0)
    except (TypeError, ValueError) as exc:
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare 响应缺少有效 code") from exc
    if code != 0:
        message = str(payload.get("msg") or f"Tushare error code {code}")
        error_code, summary = classify_tushare_error(message)
        raise TushareClientError(error_code, summary)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare 响应缺少 data 对象")
    fields = list(data.get("fields") or [])
    items = data.get("items")
    if not isinstance(items, list):
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare data.items 不是数组")
    if not items:
        raise TushareClientError("EMPTY_RESPONSE", "Tushare 返回空数据")
    if not fields:
        raise TushareClientError("RESPONSE_SCHEMA_ERROR", "Tushare data.fields 为空")
    return [dict(zip(fields, row, strict=False)) for row in items]


def query(api_name: str, params: dict[str, Any], fields: list[str], *, token: str | None = None) -> list[dict[str, Any]]:
    api_token = _configured_token(token)
    if not api_token:
        raise TushareClientError("TOKEN_INVALID", "未配置 TUSHARE_TOKEN (TUSHARE_TOKEN_NOT_CONFIGURED)")
    payload = {
        "api_name": api_name,
        "token": api_token,
        "params": params,
        "fields": ",".join(fields),
    }
    return _rows_from_payload(_post_json(payload))


def _safe_float(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _normalise_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10] or None


def _latest_row(rows: list[dict[str, Any]], *date_fields: str) -> dict[str, Any]:
    return max(
        rows,
        key=lambda row: max((str(row.get(field) or "") for field in date_fields), default=""),
        default={},
    )


def _first_error(*values: dict[str, Any]) -> tuple[str | None, str | None]:
    for value in values:
        if value.get("error_code"):
            return str(value["error_code"]), str(value.get("error_summary") or value.get("error_message") or "")
    return None, None


def _last_success(*values: dict[str, Any]) -> str | None:
    timestamps = [str(value.get("last_success_at")) for value in values if value.get("last_success_at")]
    return max(timestamps, default=None)


def _status(
    *,
    status: str,
    api_name: str = "",
    market_date: str | None = None,
    error_code: str = "",
    error: str = "",
    fallback_used: bool = False,
    records_count: int = 0,
    last_success_at: str | None = None,
) -> dict[str, Any]:
    return {
        "api_name": api_name,
        "source": SOURCE,
        "source_type": "authorized_api",
        "source_level": SOURCE_LEVEL,
        "fetched_at": _now(),
        "market_date": market_date,
        "timezone": TIMEZONE,
        "currency": "CNY",
        "status": status,
        "freshness": "fresh" if status == "ok" else "unavailable",
        "fallback_used": fallback_used,
        "records_count": records_count,
        "fields_status": "available" if records_count else "missing",
        "error_code": error_code or None,
        "error_summary": _sanitize_error(error) if error else None,
        "error_message": _sanitize_error(error) if error else "",
        "last_success_at": last_success_at,
    }


def _query_with_cache(api_name: str, cache_key: str, params: dict[str, Any], fields: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        rows = query(api_name, params, fields)
        newest = _latest_row(rows, "trade_date", "ann_date", "f_ann_date", "end_date", "cal_date")
        data = {"rows": rows, "api_name": api_name, "market_date": _normalise_date(
            newest.get("trade_date")
            or newest.get("ann_date")
            or newest.get("f_ann_date")
            or newest.get("end_date")
            or newest.get("cal_date")
        )}
        write_cache("tushare", cache_key, data, SOURCE)
        success_at = _now()
        return rows, _status(
            status="ok",
            api_name=api_name,
            market_date=data["market_date"],
            records_count=len(rows),
            last_success_at=success_at,
        )
    except Exception as exc:  # noqa: BLE001 - optional source must degrade
        error_code, error = classify_tushare_error(exc)
        cached = read_cache("tushare", cache_key, max_age_days=7)
        if cached and cached.get("rows"):
            write_log(f"Tushare {api_name}失败[{error_code}]，使用显式标记缓存：{error}", filename="stone_ai.log")
            return list(cached.get("rows") or []), {
                **_status(
                    status="cached",
                    api_name=api_name,
                    market_date=_normalise_date(cached.get("market_date")),
                    error_code=error_code,
                    error=error,
                    fallback_used=True,
                    records_count=len(cached.get("rows") or []),
                    last_success_at=cached.get("fetched_at"),
                ),
                "freshness": cached.get("freshness_status", "stale"),
                "cache_age_days": cached.get("cache_age_days"),
                "upstream_status": "failed",
            }
        write_log(f"Tushare {api_name}不可用[{error_code}]：{error}", filename="stone_ai.log")
        disabled = error_code == "TOKEN_INVALID" and not _configured_token()
        return [], _status(
            status="disabled" if disabled else "failed",
            api_name=api_name,
            error_code=error_code,
            error=error,
        )


def fetch_trade_calendar(
    today: date | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(tz=ZoneInfo(TIMEZONE)).date()
    start = start_date or (today - timedelta(days=20)).strftime("%Y%m%d")
    end = end_date or (today + timedelta(days=10)).strftime("%Y%m%d")
    rows, meta = _query_with_cache(
        "trade_cal",
        "cn_trade_calendar",
        {"exchange": "SSE", "start_date": start, "end_date": end},
        ["exchange", "cal_date", "is_open", "pretrade_date"],
    )
    open_dates = sorted(_normalise_date(row.get("cal_date")) for row in rows if int(row.get("is_open", 0) or 0) == 1)
    open_dates = [value for value in open_dates if value]
    today_text = today.isoformat()
    latest_open = max((value for value in open_dates if value <= today_text), default=None)
    next_open = min((value for value in open_dates if value > today_text), default=None)
    return {
        **meta,
        "exchange": "SSE",
        "latest_open_date": latest_open,
        "next_open_date": next_open,
        "today_is_open": today_text in open_dates,
        "records_count": len(rows),
        "requested_start_date": start,
        "requested_end_date": end,
        "unit": "calendar_day",
    }


def fetch_valuation_snapshot() -> dict[str, Any]:
    stock_fields = [
        "ts_code", "trade_date", "close", "turnover_rate", "volume_ratio", "pe", "pe_ttm", "pb",
        "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_mv", "circ_mv",
    ]
    stock_rows, stock_meta = _query_with_cache(
        "daily_basic", "valuation_002558_SZ", {"ts_code": "002558.SZ"}, stock_fields
    )
    stock = _latest_row(stock_rows, "trade_date")

    index_fields = ["ts_code", "trade_date", "turnover_rate", "pe", "pe_ttm", "pb"]
    index_rows, index_meta = _query_with_cache(
        "index_dailybasic", "valuation_000300_SH", {"ts_code": "000300.SH"}, index_fields
    )
    index_row = _latest_row(index_rows, "trade_date")
    items = {
        "002558.SZ": {
            **stock_meta,
            "symbol": "002558.SZ",
            "official_name": "巨人网络",
            "instrument_type": "single_stock",
            "valuation_basis": "security_itself",
            "market_date": _normalise_date(stock.get("trade_date")) or stock_meta.get("market_date"),
            "metrics": {key: _safe_float(stock.get(key)) for key in stock_fields if key not in {"ts_code", "trade_date"}},
        },
        "510300.SS": {
            **index_meta,
            "symbol": "510300.SS",
            "official_name": "沪深300ETF",
            "instrument_type": "etf",
            "valuation_basis": "benchmark_index_000300.SH",
            "market_date": _normalise_date(index_row.get("trade_date")) or index_meta.get("market_date"),
            "metrics": {key: _safe_float(index_row.get(key)) for key in index_fields if key not in {"ts_code", "trade_date"}},
            "note": "仅使用沪深300指数估值作为ETF基准，不替代ETF真实行情。",
        },
        "513060.SS": {
            **_status(status="not_applicable", error="ETF不套用个股估值或财务模型"),
            "symbol": "513060.SS", "official_name": "恒生医疗ETF", "instrument_type": "etf",
            "valuation_basis": "not_connected", "metrics": {},
        },
        "513090.SS": {
            **_status(status="not_applicable", error="ETF不套用个股估值或财务模型"),
            "symbol": "513090.SS", "official_name": "香港证券ETF", "instrument_type": "etf",
            "valuation_basis": "not_connected", "metrics": {},
        },
    }
    error_code, error_summary = _first_error(stock_meta, index_meta)
    successful = sum(1 for value in [stock_meta, index_meta] if value.get("status") in {"ok", "cached"})
    return {
        "source": SOURCE,
        "configured": bool(_configured_token()),
        "status": "ok" if successful == 2 else "partial" if successful else stock_meta.get("status", "disabled"),
        "error_code": error_code,
        "error_summary": error_summary,
        "last_success_at": _last_success(stock_meta, index_meta),
        "interfaces": {
            "002558_valuation": {
                "api_name": "daily_basic",
                "status": stock_meta.get("status"),
                "market_date": stock_meta.get("market_date"),
                "rows_count": stock_meta.get("records_count", 0),
                "fields_status": stock_meta.get("fields_status"),
                "error_code": stock_meta.get("error_code"),
                "error_summary": stock_meta.get("error_summary"),
            },
            "csi300_valuation": {
                "api_name": "index_dailybasic",
                "status": index_meta.get("status"),
                "market_date": index_meta.get("market_date"),
                "rows_count": index_meta.get("records_count", 0),
                "fields_status": index_meta.get("fields_status"),
                "error_code": index_meta.get("error_code"),
                "error_summary": index_meta.get("error_summary"),
            },
        },
        "items": items,
    }


def fetch_002558_fundamentals() -> dict[str, Any]:
    calls = {
        "financial_indicators": (
            "fina_indicator",
            ["ts_code", "ann_date", "end_date", "eps", "roe", "roe_dt", "roa", "roic", "debt_to_assets", "grossprofit_margin", "netprofit_margin", "current_ratio", "quick_ratio", "or_yoy", "netprofit_yoy"],
        ),
        "income_statement": (
            "income",
            ["ts_code", "ann_date", "f_ann_date", "end_date", "revenue", "operate_profit", "total_profit", "n_income", "n_income_attr_p"],
        ),
        "balance_sheet": (
            "balancesheet",
            ["ts_code", "ann_date", "f_ann_date", "end_date", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int", "money_cap"],
        ),
        "cash_flow": (
            "cashflow",
            ["ts_code", "ann_date", "f_ann_date", "end_date", "n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act", "free_cashflow"],
        ),
    }
    statements: dict[str, Any] = {}
    successful = 0
    for name, (api_name, fields) in calls.items():
        rows, meta = _query_with_cache(api_name, f"002558_SZ_{api_name}", {"ts_code": "002558.SZ", "limit": 1}, fields)
        row = _latest_row(rows, "ann_date", "f_ann_date", "end_date")
        if meta.get("status") in {"ok", "cached"} and row:
            successful += 1
        statements[name] = {
            **meta,
            "report_period": _normalise_date(row.get("end_date")),
            "announcement_date": _normalise_date(row.get("ann_date") or row.get("f_ann_date")),
            "metrics": {
                key: (_normalise_date(value) if key in {"ann_date", "f_ann_date", "end_date"} else _safe_float(value))
                for key, value in row.items() if key != "ts_code"
            },
        }
    statement_values = list(statements.values())
    error_code, error_summary = _first_error(*statement_values)
    return {
        "symbol": "002558.SZ",
        "official_name": "巨人网络",
        "model": "single_stock_fundamental",
        "source": SOURCE,
        "source_level": SOURCE_LEVEL,
        "configured": bool(_configured_token()),
        "status": "ok" if successful >= 2 else "partial" if successful else "disabled" if not _configured_token() else "failed",
        "successful_statement_count": successful,
        "total_statement_count": len(calls),
        "error_code": error_code,
        "error_summary": error_summary,
        "last_success_at": _last_success(*statement_values),
        "statements": statements,
        "fetched_at": _now(),
        "note": "仅用于002558个股基本面；ETF不得继承本模型。",
    }


def fetch_tushare_p1a_snapshot() -> dict[str, Any]:
    trade_calendar = fetch_trade_calendar()
    valuation = fetch_valuation_snapshot()
    fundamental = fetch_002558_fundamentals()
    components = [trade_calendar, valuation, fundamental]
    successes = sum(1 for value in components if value.get("status") in {"ok", "cached", "partial"})
    error_code, error_summary = _first_error(*components)
    return {
        "provider": SOURCE,
        "transport": "official_rest_api",
        "uses_python_sdk": False,
        "configured": bool(_configured_token()),
        "status": "ok" if successes == len(components) else "partial" if successes else "disabled" if not _configured_token() else "failed",
        "error_code": error_code,
        "error_summary": error_summary,
        "last_success_at": _last_success(*components),
        "fetched_at": _now(),
        "trade_calendar": trade_calendar,
        "valuation": valuation,
        "fundamentals": {"002558.SZ": fundamental},
        "policy": "ETF不得套用个股财务评分；无Token或接口失败时只降级P1A增强分项。",
    }
