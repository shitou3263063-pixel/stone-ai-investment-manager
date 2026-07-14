from __future__ import annotations

from datetime import date, datetime
import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from src.data_sources.data_cache import read_cache, write_cache
from utils.logger import write_log


SOURCE = "hkma_official"
SOURCE_LEVEL = 1
TIMEZONE = "Asia/Hong_Kong"
MAX_RETRIES = 2
CIRCUIT_FAILURE_THRESHOLD = 3
REQUEST_TIMEOUT_SECONDS = 12
_DATASET_FAILURES: dict[str, int] = {}
BASE_URL = "https://api.hkma.gov.hk/public/market-data-and-statistics"

ENDPOINTS = {
    "liquidity": f"{BASE_URL}/daily-monetary-statistics/daily-figures-interbank-liquidity",
    "hibor": f"{BASE_URL}/monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily",
    "exchange_rate": f"{BASE_URL}/monthly-statistical-bulletin/er-ir/er-eeri-daily",
}


def _now() -> str:
    return datetime.now(tz=ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")


def _get_json(url: str, params: dict[str, Any], timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "Stone-AI-Investment-Manager/12.6"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official HKMA endpoints
        return json.loads(response.read().decode("utf-8"))


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    header = payload.get("header") or {}
    if header and header.get("success") is False:
        raise RuntimeError(str(header.get("err_msg") or "HKMA API returned failure"))
    records = ((payload.get("result") or {}).get("records") or [])
    return [dict(row) for row in records]


def _safe_float(value: Any) -> float | None:
    try:
        return None if value in {None, "", "N.A."} else float(value)
    except (TypeError, ValueError):
        return None


def _classify_error(exc: BaseException | str) -> tuple[str, str]:
    message = str(exc or "unknown error")[:500]
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "NETWORK_TIMEOUT", "HKMA官方接口请求超时"
    if "ssl" in lowered or "certificate" in lowered:
        return "SSL_ERROR", f"HKMA官方接口SSL失败：{message}"
    if "proxy" in lowered or "connection" in lowered or "remote end" in lowered:
        return "NETWORK_PROXY_ERROR", f"HKMA官方接口网络或代理失败：{message}"
    if "empty_response" in lowered or "空" in message:
        return "EMPTY_RESPONSE", "HKMA官方接口返回空数据"
    if "returned failure" in lowered or "字段" in message or "schema" in lowered:
        return "RESPONSE_SCHEMA_ERROR", message
    return "UNKNOWN_ERROR", message


def _latest(records: list[dict[str, Any]], date_field: str) -> dict[str, Any]:
    return max(records, key=lambda row: str(row.get(date_field) or ""), default={})


def _fetch_dataset(name: str, params: dict[str, Any], date_field: str) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error_code = "UNKNOWN_ERROR"
    last_error = "HKMA官方接口不可用"
    attempts = 0
    if _DATASET_FAILURES.get(name, 0) < CIRCUIT_FAILURE_THRESHOLD:
        for attempt in range(MAX_RETRIES + 1):
            attempts = attempt + 1
            try:
                records = _records(_get_json(
                    ENDPOINTS[name],
                    {**params, "pagesize": 10, "offset": 0, "sortby": date_field, "sortorder": "desc"},
                ))
                row = _latest(records, date_field)
                if not row:
                    raise RuntimeError("empty_response")
                market_date = str(row.get(date_field) or "")[:10] or None
                if not market_date:
                    raise RuntimeError(f"response schema missing {date_field}")
                age_days = (datetime.now(tz=ZoneInfo(TIMEZONE)).date() - date.fromisoformat(market_date)).days
                stale = age_days > (3 if name == "liquidity" else 7)
                write_cache("hkma", name, {"record": row, "market_date": market_date}, SOURCE)
                _DATASET_FAILURES[name] = 0
                return row, {
                    "status": "ok", "source": SOURCE, "source_level": SOURCE_LEVEL,
                    "source_type": "official", "underlying_provider": "hkma_open_api",
                    "fetched_at": _now(), "market_date": market_date,
                    "timezone": TIMEZONE, "freshness": "stale" if stale else "fresh", "stale": stale,
                    "age_days": age_days, "fallback_used": False, "error_code": None,
                    "error_message": "", "attempts": attempts, "circuit_open": False,
                }
            except Exception as exc:  # noqa: BLE001 - official API must degrade
                last_error_code, last_error = _classify_error(exc)
                _DATASET_FAILURES[name] = _DATASET_FAILURES.get(name, 0) + 1
                write_log(
                    f"HKMA {name}失败[{last_error_code}] attempt={attempts}: {last_error}",
                    filename="stone_ai.log",
                )
                if _DATASET_FAILURES[name] >= CIRCUIT_FAILURE_THRESHOLD:
                    break
                if attempt < MAX_RETRIES:
                    time.sleep(0.25 * (attempt + 1))
    else:
        last_error_code = "CIRCUIT_OPEN"
        last_error = f"HKMA {name}本轮连续失败达到阈值，已停止继续请求"

    circuit_open = _DATASET_FAILURES.get(name, 0) >= CIRCUIT_FAILURE_THRESHOLD
    try:
        cached = read_cache("hkma", name, max_age_days=7)
        if cached and cached.get("record"):
            write_log(f"HKMA {name}失败，使用显式标记缓存：{last_error}", filename="stone_ai.log")
            cached_market_date = str(cached.get("market_date") or "")[:10] or None
            cached_market_age = (
                (datetime.now(tz=ZoneInfo(TIMEZONE)).date() - date.fromisoformat(cached_market_date)).days
                if cached_market_date else None
            )
            cached_stale = (
                bool(cached.get("cache_stale"))
                or cached_market_age is None
                or cached_market_age > (3 if name == "liquidity" else 7)
            )
            return dict(cached["record"]), {
                "status": "cached", "source": SOURCE, "source_level": SOURCE_LEVEL,
                "source_type": "official_cache", "underlying_provider": "hkma_open_api",
                "fetched_at": _now(), "market_date": cached.get("market_date"),
                "timezone": TIMEZONE, "freshness": "stale" if cached_stale else "fresh", "stale": cached_stale,
                "age_days": cached_market_age,
                "fallback_used": True, "error_code": last_error_code, "error_message": last_error,
                "cache_age_days": cached.get("cache_age_days"), "attempts": attempts, "circuit_open": circuit_open,
            }
        write_log(f"HKMA {name}不可用[{last_error_code}]：{last_error}", filename="stone_ai.log")
        return {}, {
            "status": "failed", "source": SOURCE, "source_level": SOURCE_LEVEL,
            "source_type": "official", "underlying_provider": "hkma_open_api",
            "fetched_at": _now(), "market_date": None,
            "timezone": TIMEZONE, "freshness": "unavailable", "stale": False,
            "fallback_used": False, "error_code": last_error_code, "error_message": last_error,
            "attempts": attempts, "circuit_open": circuit_open,
        }
    except Exception as exc:  # noqa: BLE001 - corrupt cache must not break report
        code, message = _classify_error(exc)
        return {}, {
            "status": "failed", "source": SOURCE, "source_level": SOURCE_LEVEL,
            "source_type": "official", "underlying_provider": "hkma_open_api",
            "fetched_at": _now(), "market_date": None, "timezone": TIMEZONE,
            "freshness": "unavailable", "stale": False, "fallback_used": False,
            "error_code": code, "error_message": message, "attempts": attempts,
            "circuit_open": circuit_open,
        }


def fetch_hkma_liquidity_snapshot() -> dict[str, Any]:
    _DATASET_FAILURES.clear()
    liquidity, liquidity_meta = _fetch_dataset("liquidity", {}, "end_of_date")
    hibor, hibor_meta = _fetch_dataset("hibor", {"segment": "hibor.fixing"}, "end_of_day")
    fx, fx_meta = _fetch_dataset("exchange_rate", {}, "end_of_day")
    metrics = {
        "hibor_overnight_pct": _safe_float(hibor.get("ir_overnight") or liquidity.get("hibor_overnight")),
        "hibor_1m_pct": _safe_float(hibor.get("ir_1m") or liquidity.get("hibor_fixing_1m")),
        "hibor_3m_pct": _safe_float(hibor.get("ir_3m")),
        "usd_hkd": _safe_float(fx.get("usd")),
        "cny_hkd": _safe_float(fx.get("cny")),
        "convertibility_weakside_usd_hkd": _safe_float(liquidity.get("cu_weakside")),
        "convertibility_strongside_usd_hkd": _safe_float(liquidity.get("cu_strongside")),
        "aggregate_balance_hkd_mn": _safe_float(liquidity.get("closing_balance")),
        "opening_balance_hkd_mn": _safe_float(liquidity.get("opening_balance")),
        "discount_window_base_rate_pct": _safe_float(liquidity.get("disc_win_base_rate")),
        "trade_weighted_index": _safe_float(liquidity.get("twi")),
    }
    successful = sum(
        meta["status"] in {"ok", "cached"} and meta.get("freshness") != "stale"
        for meta in [liquidity_meta, hibor_meta, fx_meta]
    )
    return {
        "provider": SOURCE,
        "source_level": SOURCE_LEVEL,
        "status": "ok" if successful == 3 else "partial" if successful else "failed",
        "fetched_at": _now(),
        "market_date": max((meta.get("market_date") or "" for meta in [liquidity_meta, hibor_meta, fx_meta]), default="") or None,
        "timezone": TIMEZONE,
        "currency": "HKD",
        "unit": {
            "hibor": "percent_per_annum", "exchange_rate": "HKD_per_foreign_currency",
            "aggregate_balance": "HKD_million",
        },
        "metrics": metrics,
        "datasets": {"liquidity": liquidity_meta, "hibor": hibor_meta, "exchange_rate": fx_meta},
        "fallback_used": any(meta.get("fallback_used") for meta in [liquidity_meta, hibor_meta, fx_meta]),
        "missing_fields": [key for key, value in metrics.items() if value is None],
    }
