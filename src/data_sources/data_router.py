from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Any, Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from src.data_sources import alpha_vantage_client, finnhub_client, fred_client, yfinance_client
from src.data_sources.cn_hk_p1a import build_cn_hk_p1a_snapshot
from src.data_sources.data_cache import read_cache, write_cache
from src.data_sources.normalized_market import normalize_market_quote, select_best_normalized_quote
from src.data_sources.source_registry import DataSourceRegistry
from src.data_sources.time_normalization import TIMEZONE_UNKNOWN, calculate_age_hours, normalize_to_utc
from utils.data_loader import project_root
from utils.logger import write_log


MARKET_TICKERS = [
    "VOO",
    "QQQ",
    "NVDA",
    "GOOG",
    "BABA",
    "IBKR",
    "XLF",
    "^GSPC",
    "^IXIC",
    "03033.HK",
    "2800.HK",
    "510300.SS",
    "002558.SZ",
    "513060.SS",
    "513090.SS",
    "GLD",
    "TLT",
    "IEF",
    "UUP",
    "DX-Y.NYB",
    "^VIX",
    "USD/CNY",
    "USD/CNH",
]

US_ETF_TICKERS = {"VOO", "QQQ", "NVDA", "GOOG", "BABA", "IBKR", "XLF", "GLD", "TLT", "IEF", "UUP"}
HK_CN_TICKERS = {"03033.HK", "2800.HK", "510300.SS", "002558.SZ", "513060.SS", "513090.SS"}
YFINANCE_ONLY_TICKERS = {"^GSPC", "^IXIC", "DX-Y.NYB", "USD/CNY", "USD/CNH"}

INSTRUMENT_METADATA: dict[str, dict[str, str]] = {
    "USD/CNY": {
        "official_name": "美元兑人民币估值汇率",
        "exchange": "FX",
        "market": "FX",
        "currency": "CNY",
        "timezone": "UTC",
    },
    "USD/CNH": {
        "official_name": "美元兑离岸人民币估值汇率",
        "exchange": "FX",
        "market": "FX",
        "currency": "CNH",
        "timezone": "UTC",
    },
    "03033.HK": {
        "official_name": "南方东英恒生科技指数ETF",
        "exchange": "HKEX",
        "market": "HK",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
    },
    "3067.HK": {
        "official_name": "iShares恒生科技ETF",
        "exchange": "HKEX",
        "market": "HK",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
    },
    "2800.HK": {
        "official_name": "盈富基金",
        "exchange": "HKEX",
        "market": "HK",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
    },
    "510300.SS": {
        "official_name": "沪深300ETF",
        "exchange": "SSE",
        "market": "CN",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
    },
    "002558.SZ": {
        "official_name": "巨人网络",
        "exchange": "SZSE",
        "market": "CN",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
    },
    "513060.SS": {
        "official_name": "恒生医疗ETF",
        "exchange": "SSE",
        "market": "CN",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
    },
    "513090.SS": {
        "official_name": "香港证券ETF",
        "exchange": "SSE",
        "market": "CN",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
    },
}

# 3033.HK 是同一只03033.HK证券在部分供应商中的去前导零代码，不是代理ETF。
PROVIDER_SYMBOLS: dict[str, dict[str, str]] = {
    "03033.HK": {"yfinance": "3033.HK"},
    "USD/CNY": {"yfinance": "CNY=X"},
    "USD/CNH": {"yfinance": "CNH=X"},
}

SOURCE_REGISTRY = DataSourceRegistry.load()

CN_HELD_MARKET_SYMBOLS = ["510300.SS", "002558.SZ"]
HK_ALLOCATION_SYMBOLS = ["03033.HK", "513060.SS", "513090.SS"]
QUALITY_MARKET_SYMBOLS = [
    "VOO", "QQQ", "TLT", "GLD", "^VIX",
    "03033.HK", "510300.SS", "002558.SZ", "513060.SS", "513090.SS",
]

MACRO_SERIES = {
    "DGS10": "美国10年国债收益率",
    "DGS2": "美国2年国债收益率",
    "T10Y2Y": "美国10年减2年利差",
    "BAMLH0A0HYM2": "美国高收益债利差",
    "CPIAUCSL": "CPI",
    "PPIACO": "PPI",
    "PCEPI": "PCE",
    "UNRATE": "失业率",
    "GDP": "GDP",
}
SOURCE_LEVELS = {
    "fred": 1,
    "treasury": 1,
    "cboe_official": 1,
    "alpha_vantage": 2,
    "finnhub": 2,
    "yfinance": 3,
    "tushare_pro": 2,
    "hkma_official": 1,
    "cninfo_official": 1,
    "hkex_official": 1,
    "unavailable": 99,
}

SOURCE_TIMEZONES = {
    # Cboe delayed quote API timestamps are emitted in UTC without an offset.
    "cboe_official": "UTC",
    "alpha_vantage": "America/New_York",
    "finnhub": "America/New_York",
    "fred": "America/New_York",
    "hkma_official": "Asia/Hong_Kong",
}


def provider_symbol_for(symbol: str, provider_name: str) -> str:
    """返回同一证券的供应商代码格式，绝不使用代理证券。"""
    return PROVIDER_SYMBOLS.get(symbol, {}).get(provider_name, symbol)


def _business_day_lag(market_date: str | None, timezone_name: str) -> int | None:
    if not market_date:
        return None
    try:
        start = date.fromisoformat(str(market_date)[:10])
        end = datetime.now(tz=ZoneInfo(timezone_name)).date()
    except (TypeError, ValueError, KeyError):
        return None
    if start > end:
        return -1
    return sum(1 for offset in range(1, (end - start).days + 1) if (start + timedelta(days=offset)).weekday() < 5)


def _legacy_normalize_point(item: dict[str, Any]) -> dict[str, Any]:
    """为行情和宏观点补齐统一审计时间口径，不用0替代缺失值。"""
    source = str(item.get("source") or "unavailable")
    source_key = source.split(":", 1)[1] if source.startswith("cache:") else source
    status = str(item.get("status") or ("ok" if item.get("close", item.get("value")) is not None else "missing"))
    stale = bool(item.get("cache_stale")) or item.get("freshness_status") == "stale"
    observed_at = item.get("observed_at") or item.get("published_at") or item.get("date")
    fetched_at = item.get("fetched_at") or item.get("retrieved_at") or datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat()
    comparable_date = item.get("comparable_date") or (str(observed_at)[:10] if observed_at else None)
    market_date = item.get("market_date") or comparable_date
    is_macro = source_key == "fred" or item.get("series_id") in MACRO_SERIES
    symbol = str(item.get("symbol") or "")
    metadata = INSTRUMENT_METADATA.get(symbol, {})
    declared_timezone = (
        item.get("source_timezone")
        or item.get("timezone")
        or item.get("market_timezone")
        or metadata.get("timezone")
        or SOURCE_TIMEZONES.get(source_key)
        or ("America/New_York" if symbol in US_ETF_TICKERS or symbol in YFINANCE_ONLY_TICKERS else None)
        or ("Asia/Hong_Kong" if symbol.endswith(".HK") else None)
        or ("Asia/Shanghai" if symbol.endswith((".SS", ".SZ")) else None)
    )
    market_timezone = str(declared_timezone or (
        "America/New_York" if is_macro or symbol in US_ETF_TICKERS or symbol in YFINANCE_ONLY_TICKERS else "Asia/Shanghai"
    ))
    business_day_lag = _business_day_lag(str(market_date) if market_date else None, market_timezone)
    if business_day_lag is not None and business_day_lag > 1 and not is_macro:
        stale = True
    if status not in {"ok", "success"}:
        data_session = "unavailable"
    elif stale:
        data_session = "stale"
    elif is_macro:
        data_session = "official_lagged_macro"
    elif bool(item.get("is_realtime")):
        data_session = "realtime"
    elif source_key in {"cboe_official", "finnhub"}:
        data_session = "intraday_delayed"
    elif comparable_date == datetime.now(tz=ZoneInfo(market_timezone)).date().isoformat():
        data_session = "official_close"
    else:
        data_session = "previous_close"
    observed_at_utc = None
    received_at_utc = None
    time_status = "ok"
    try:
        if observed_at:
            observed_at_utc = normalize_to_utc(observed_at, source_timezone=str(declared_timezone) if declared_timezone else None)
        received_at_utc = normalize_to_utc(fetched_at, source_timezone=str(item.get("received_timezone") or declared_timezone) if (item.get("received_timezone") or declared_timezone) else None)
    except (TypeError, ValueError, KeyError) as exc:
        if TIMEZONE_UNKNOWN in str(exc):
            time_status = TIMEZONE_UNKNOWN
            stale = True
        observed_at_utc = observed_at_utc if observed_at_utc and observed_at_utc.tzinfo else None
        received_at_utc = received_at_utc if received_at_utc and received_at_utc.tzinfo else None
    age_hours = item.get("age_hours")
    if age_hours is None and observed_at_utc and received_at_utc:
        age_hours = calculate_age_hours(observed_at_utc, received_at_utc)
    freshness_status = "unavailable" if data_session == "unavailable" else "stale" if stale else str(item.get("freshness_status") or "fresh")
    return {
        **item,
        **metadata,
        "symbol": symbol,
        "value": item.get("close", item.get("value")),
        "previous_value": item.get("previous_close", item.get("previous_value")),
        "timestamp": observed_at,
        "observed_at": observed_at,
        "observed_at_utc": observed_at_utc.isoformat() if observed_at_utc else None,
        "fetched_at": fetched_at,
        "received_at_utc": received_at_utc.isoformat() if received_at_utc else None,
        "source_timezone": str(declared_timezone or "unknown"),
        "time_status": time_status,
        "market_timezone": market_timezone,
        "timezone": market_timezone,
        "market_date": market_date,
        "latest_market_date": market_date,
        "business_day_lag": business_day_lag,
        "freshness": freshness_status,
        "data_frequency": str(item.get("data_frequency") or ("daily" if is_macro else "quote")),
        "data_session": data_session,
        "freshness_status": freshness_status,
        "age_hours": age_hours,
        "comparable_date": comparable_date,
        "source": source,
        "source_level": int(item.get("source_level") or SOURCE_LEVELS.get(source_key, 99)),
        "status": status,
        "stale": stale,
        "fallback_used": bool(item.get("cache_used")) or source.startswith("cache:"),
        "verified_by_second_source": bool(item.get("verified_by_second_source", False)),
    }


def _normalize_point(item: dict[str, Any]) -> dict[str, Any]:
    """Route every provider payload through the sole canonical quote model."""
    symbol = str(item.get("symbol") or "")
    metadata = INSTRUMENT_METADATA.get(symbol, {})
    cutoff = item.get("decision_cutoff_time")
    if cutoff:
        try:
            decision_cutoff = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        except ValueError:
            decision_cutoff = datetime.now(tz=timezone.utc)
    else:
        retrieved_raw = item.get("retrieved_at") or item.get("fetched_at") or item.get("received_at_utc")
        try:
            decision_cutoff = normalize_to_utc(
                retrieved_raw,
                source_timezone=str(item.get("received_timezone") or "Asia/Shanghai"),
            ) if retrieved_raw else datetime.now(tz=timezone.utc)
        except (TypeError, ValueError, KeyError):
            decision_cutoff = datetime.now(tz=timezone.utc)
    enriched = {
        **item,
        **metadata,
        "symbol": symbol,
        "source_level": int(item.get("source_level") or SOURCE_LEVELS.get(str(item.get("source") or "unavailable").replace("cache:", ""), 99)),
        "source_timezone": item.get("source_timezone") or SOURCE_TIMEZONES.get(str(item.get("source") or "").replace("cache:", "")) or metadata.get("timezone"),
    }
    return normalize_market_quote(enriched, decision_cutoff=decision_cutoff, metadata=metadata)


MACRO_FRESHNESS_DAYS = {
    "DGS10": 3, "DGS2": 3, "T10Y2Y": 3, "BAMLH0A0HYM2": 3,
    "CPIAUCSL": 50, "PPIACO": 50, "PCEPI": 50,
    "UNRATE": 45, "GDP": 130,
}


def _apply_macro_freshness(series_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Judge official macro data by publication frequency, including weekends."""
    row = dict(item)
    observed = row.get("market_date") or row.get("comparable_date") or row.get("date") or row.get("published_at")
    try:
        observation_date = date.fromisoformat(str(observed)[:10])
        reference_date = datetime.now(tz=ZoneInfo("America/New_York")).date()
        retrieved = row.get("retrieved_at") or row.get("fetched_at")
        if retrieved:
            reference_date = datetime.fromisoformat(str(retrieved).replace("Z", "+00:00")).date()
        age_days = (reference_date - observation_date).days
    except (TypeError, ValueError):
        age_days = None
    threshold = int(MACRO_FRESHNESS_DAYS.get(series_id, 3))
    usable = str(row.get("status") or "").lower() in {"ok", "success"} and row.get("value") is not None
    stale = not usable or age_days is None or age_days > threshold
    frequency = "quarterly" if series_id == "GDP" else "monthly" if threshold >= 45 else "daily_official"
    row.update({
        "data_frequency": frequency,
        "age_days": age_days,
        "freshness_threshold_days": threshold,
        "stale": stale,
        "freshness_status": "stale" if stale else "valid_lagged_by_design",
        "data_status": "DATA_INSUFFICIENT" if stale else "VALID_LAGGED_BY_DESIGN",
        "decision_eligible": bool(not stale),
    })
    return row


def merge_newer_validated_cn_hk_quotes(
    items: dict[str, dict[str, Any]],
    cn_hk_p1a: dict[str, Any],
    *,
    decision_cutoff: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Promote a newer validated AKShare close without mixing identities.

    AKShare remains a monitored fallback.  It may win only when its market
    reference is fresh, scoring-eligible and newer than the selected quote.
    """
    merged = {symbol: dict(item) for symbol, item in items.items()}
    references = (((cn_hk_p1a.get("akshare") or {}).get("market_references")) or {})
    cutoff = decision_cutoff or datetime.now(tz=timezone.utc)
    for symbol, record in references.items():
        if symbol not in merged or symbol not in INSTRUMENT_METADATA:
            continue
        if record.get("status") not in {"ok", "cached"} or not record.get("scoring_eligible"):
            continue
        if record.get("error_code") == "SOURCE_CONFLICT" or record.get("freshness") != "fresh":
            continue
        close = ((record.get("metrics") or {}).get("close"))
        if close is None or not record.get("market_date"):
            continue
        metadata = INSTRUMENT_METADATA[symbol]
        candidate = normalize_market_quote(
            {
                "symbol": symbol,
                "close": close,
                "status": "ok",
                "source": f"akshare:{record.get('underlying_provider') or 'unknown'}",
                "source_level": int(record.get("source_level") or 3),
                "market_date": record.get("market_date"),
                "retrieved_at": record.get("fetched_at"),
                "source_timezone": metadata["timezone"],
                "currency": record.get("currency") or metadata["currency"],
                "daily_bar_finalized": True,
                "data_frequency": "daily",
            },
            decision_cutoff=cutoff,
            metadata=metadata,
        )
        selected = select_best_normalized_quote([merged[symbol], candidate])
        if selected and selected.get("source") == candidate.get("source"):
            selected["promoted_from_p1a"] = True
            selected["underlying_provider"] = record.get("underlying_provider")
            merged[symbol] = selected
    return merged


def _dual_source_verified(candidates: list[dict[str, Any]], tolerance_pct: float = 1.0) -> bool:
    values: list[float] = []
    sources: set[str] = set()
    for candidate in candidates:
        raw = candidate.get("close", candidate.get("value"))
        try:
            values.append(float(raw))
            sources.add(str(candidate.get("source", "")))
        except (TypeError, ValueError):
            continue
    if len(sources) < 2 or len(values) < 2 or min(values) == 0:
        return False
    return (max(values) / min(values) - 1) * 100 <= tolerance_pct


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
    return {**data, **INSTRUMENT_METADATA.get(symbol, {}), "symbol": symbol, "status": data.get("status", "ok")}


def _try_provider(
    provider_name: str,
    fn: Callable[[str], dict[str, Any]],
    symbol: str,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        provider_symbol = provider_symbol_for(symbol, provider_name)
        data = fn(provider_symbol)
        data = {
            **data,
            "provider_symbol": provider_symbol,
            "symbol_alias_normalization": provider_symbol != symbol,
            "proxy_used": False,
        }
        write_cache("quote", symbol, data, provider_name)
        write_log(f"{symbol} 行情获取成功：{provider_name}（供应商代码 {provider_symbol}）", filename="data_router.log")
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
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
        "source_timezone": "UTC",
        "received_at_utc": retrieved_at,
        "freshness_status": "fresh",
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }


def validate_market_item(symbol: str, item: dict[str, Any]) -> dict[str, Any]:
    """校验真实标的、市场、币种和时间口径；旧流程仍使用原始status。"""
    metadata = INSTRUMENT_METADATA.get(symbol, {})
    missing_fields: list[str] = []
    validation_errors: list[str] = []
    value = item.get("close", item.get("value"))
    try:
        price_valid = value is not None and float(value) > 0
    except (TypeError, ValueError):
        price_valid = False
    if not price_valid:
        missing_fields.append("price")
    if not item.get("market_date"):
        missing_fields.append("market_date")
    if item.get("source") in {None, "", "unavailable"}:
        missing_fields.append("source")

    provider_symbol = str(item.get("provider_symbol") or symbol)
    if str(item.get("symbol") or symbol) != symbol:
        validation_errors.append("canonical_symbol_mismatch")
    if symbol == "03033.HK" and provider_symbol == "3067.HK":
        validation_errors.append("forbidden_proxy_3067")
    if item.get("proxy_used"):
        validation_errors.append("proxy_symbol_used")
    if metadata.get("currency") and item.get("currency") != metadata["currency"]:
        validation_errors.append("currency_mismatch")
    if metadata.get("timezone") and item.get("timezone") != metadata["timezone"]:
        validation_errors.append("timezone_mismatch")

    mapping_errors = {"canonical_symbol_mismatch", "forbidden_proxy_3067", "proxy_symbol_used"}
    validation_failures = {"currency_mismatch", "timezone_mismatch"}
    if mapping_errors.intersection(validation_errors):
        data_status = "SYMBOL_MAPPING_ERROR"
    elif validation_failures.intersection(validation_errors):
        data_status = "DATA_VALIDATION_FAILED"
    elif item.get("status") not in {"ok", "success"} or missing_fields:
        data_status = "DATA_INSUFFICIENT"
    else:
        data_status = "VALID"
    return {
        **item,
        "data_status": data_status,
        "validation_status": data_status,
        "missing_fields": list(dict.fromkeys(missing_fields)),
        "validation_errors": list(dict.fromkeys(validation_errors)),
        "decision_eligible": data_status == "VALID" and item.get("freshness_status") != "stale",
    }


def build_market_completeness(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """计算A股和港股持仓专项完整度，防止低质量行情进入高置信度建议。"""

    def group_result(name: str, symbols: list[str]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        passed = 0
        total = len(symbols) * 7
        group_missing: list[str] = []
        for symbol in symbols:
            item = items.get(symbol, {}) or {}
            metadata = INSTRUMENT_METADATA[symbol]
            value = item.get("close", item.get("value"))
            try:
                valid_price = value is not None and float(value) > 0
            except (TypeError, ValueError):
                valid_price = False
            checks = {
                "symbol_mapping": item.get("data_status") != "SYMBOL_MAPPING_ERROR" and item.get("symbol") == symbol,
                "valid_price": valid_price and item.get("status") in {"ok", "success"},
                "currency": item.get("currency") == metadata["currency"],
                "market_date": bool(item.get("market_date")) and item.get("freshness_status") != "stale",
                "registered_source": item.get("source") not in {None, "", "unavailable"},
                "no_proxy_substitution": not item.get("proxy_used") and not (
                    symbol == "03033.HK" and item.get("provider_symbol") == "3067.HK"
                ),
                "no_critical_anomaly": item.get("data_status") not in {"SYMBOL_MAPPING_ERROR", "DATA_VALIDATION_FAILED"},
            }
            passed += sum(bool(ok) for ok in checks.values())
            failed = [key for key, ok in checks.items() if not ok]
            group_missing.extend(f"{symbol}:{key}" for key in failed)
            rows.append({
                "symbol": symbol,
                "official_name": metadata["official_name"],
                "exchange": metadata["exchange"],
                "market": metadata["market"],
                "currency": metadata["currency"],
                "timezone": metadata["timezone"],
                "data_status": item.get("data_status", "DATA_INSUFFICIENT"),
                "source": item.get("source", "unavailable"),
                "market_date": item.get("market_date"),
                "fetched_at": item.get("fetched_at"),
                "freshness": item.get("freshness_status", "unavailable"),
                "fallback_used": bool(item.get("fallback_used")),
                "missing_fields": list(dict.fromkeys((item.get("missing_fields") or []) + failed)),
                "checks": checks,
            })
        score = round(passed / total * 100, 1) if total else 0.0
        return {
            "market": name,
            "score_pct": score,
            "confidence": "low" if score < 40 else "restricted" if score < 60 else "usable",
            "decision_restricted": score < 60,
            "high_confidence_buy_allowed": score >= 60,
            "missing_fields": list(dict.fromkeys(group_missing)),
            "items": rows,
        }

    return {
        "cn_data_completeness": group_result("A股", CN_HELD_MARKET_SYMBOLS),
        "hk_data_completeness": group_result("港股及港股主题基金", HK_ALLOCATION_SYMBOLS),
        "policy": {
            "below_60": "不得输出高置信度买入建议",
            "below_40": "Opportunity Score只能标记为低可信度",
            "mapping_or_validation_error": "不得被AI自然语言解释覆盖",
        },
    }


def write_cn_hk_p0_validation(items: dict[str, dict[str, Any]], completeness: dict[str, Any]) -> None:
    """保存P0机器可读验收结果，便于报告复查，不参与交易决策。"""
    output_dir = project_root() / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    mapping_validation = {
        "generated_at": generated_at,
        "canonical_symbol": "03033.HK",
        "yfinance_provider_symbol": provider_symbol_for("03033.HK", "yfinance"),
        "forbidden_substitute": "3067.HK",
        "mapping_passed": provider_symbol_for("03033.HK", "yfinance") == "3033.HK"
        and provider_symbol_for("03033.HK", "finnhub") == "03033.HK",
        "independent_symbol_3067": True,
        "quote_provider_symbol": (items.get("03033.HK", {}) or {}).get("provider_symbol"),
        "quote_data_status": (items.get("03033.HK", {}) or {}).get("data_status", "DATA_INSUFFICIENT"),
    }
    coverage = {
        "generated_at": generated_at,
        "scope": "CN_HK_P0_REAL_HOLDINGS",
        **completeness,
    }
    p0_validation = {
        "generated_at": generated_at,
        "mapping_validation_passed": mapping_validation["mapping_passed"],
        "cn_data_completeness": completeness["cn_data_completeness"]["score_pct"],
        "hk_data_completeness": completeness["hk_data_completeness"]["score_pct"],
        "all_target_symbols": {
            symbol: {
                "official_name": (items.get(symbol, {}) or {}).get("official_name", INSTRUMENT_METADATA[symbol]["official_name"]),
                "status": (items.get(symbol, {}) or {}).get("data_status", "DATA_INSUFFICIENT"),
                "source": (items.get(symbol, {}) or {}).get("source", "unavailable"),
                "market_date": (items.get(symbol, {}) or {}).get("market_date"),
                "currency": (items.get(symbol, {}) or {}).get("currency", INSTRUMENT_METADATA[symbol]["currency"]),
                "timezone": (items.get(symbol, {}) or {}).get("timezone", INSTRUMENT_METADATA[symbol]["timezone"]),
            }
            for symbol in CN_HELD_MARKET_SYMBOLS + HK_ALLOCATION_SYMBOLS
        },
    }
    (output_dir / "symbol_mapping_validation.json").write_text(
        json.dumps(mapping_validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "cn_hk_data_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "cn_hk_p0_validation.json").write_text(
        json.dumps(p0_validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_market_quote(symbol: str) -> dict[str, Any]:
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []

    if symbol == "^VIX":
        defaults = ["cboe_official", "yfinance"]
    elif symbol in US_ETF_TICKERS:
        defaults = ["alpha_vantage", "finnhub", "yfinance"]
    elif symbol in HK_CN_TICKERS:
        defaults = ["finnhub", "yfinance"]
    else:
        defaults = ["yfinance"]
    provider_order = SOURCE_REGISTRY.provider_order(symbol, default=defaults)
    provider_functions: dict[str, Callable[[str], dict[str, Any]]] = {
        "alpha_vantage": alpha_vantage_client.get_quote,
        "finnhub": finnhub_client.get_quote,
        "yfinance": yfinance_client.get_quote,
        "cboe_official": lambda _: _official_vix_quote(),
    }
    attempted: list[str] = []
    for provider_name in provider_order:
        if provider_name == "local_cache":
            continue
        fn = provider_functions.get(provider_name)
        if fn is None:
            continue
        attempted.append(provider_name)
        data = _try_provider(provider_name, fn, symbol, errors)
        if data:
            candidates.append(data)

    if candidates:
        normalized_candidates = [_normalize_point(item) for item in candidates]
        selected = select_best_normalized_quote(normalized_candidates) or normalized_candidates[0]
        verified = _dual_source_verified(normalized_candidates)
        selected_source = str(selected.get("source") or "unavailable").replace("cache:", "")
        primary_source = attempted[0] if attempted else selected_source
        fallback_used = selected_source != primary_source or bool(errors)
        return validate_market_item(symbol, {
            **selected,
            "candidates": normalized_candidates,
            "source_count": len({item.get("source") for item in normalized_candidates}),
            "verified_by_second_source": verified,
            "cross_validation_status": selected.get("cross_validation_status") or ("VERIFIED" if verified else "SINGLE_SOURCE"),
            "primary_source": primary_source,
            "primary_failed": bool(errors) and not any(str(row.get("source")) == primary_source for row in normalized_candidates),
            "fallback_used": bool(selected.get("fallback_used")) or fallback_used,
            "fallback_reason": "；".join(errors) if fallback_used and errors else "",
            "source_confidence_adjustment": -0.15 if fallback_used else 0.0,
            "provider_errors": errors,
        })

    cached = read_cache("quote", symbol)
    if cached:
        write_log(f"{symbol} 使用缓存行情：{cached.get('source')}", filename="data_router.log")
        return validate_market_item(
            symbol,
            _normalize_point(_with_symbol(symbol, {
                **cached,
                "status": "ok",
                "source": f"cache:{cached.get('source', 'unknown')}",
                "primary_source": attempted[0] if attempted else None,
                "primary_failed": True,
                "fallback_used": True,
                "fallback_reason": "；".join(errors) or "主源不可用，使用带时间戳缓存",
                "source_confidence_adjustment": -0.25,
            })),
        )

    write_log(f"{symbol} 行情全部失败，数据缺失，不做激进判断", filename="data_router.log")
    return validate_market_item(symbol, _normalize_point(_with_symbol(symbol, _failed_item(symbol, errors))))


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

    items = {key: _apply_macro_freshness(key, _normalize_point(value)) for key, value in items.items()}
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


def _status_point(
    name: str,
    *,
    value: Any = None,
    previous_value: Any = None,
    timestamp: Any = None,
    source: str = "unavailable",
    source_level: int = 99,
    status: str = "not_connected",
    stale: bool = False,
    verified: bool = False,
    note: str = "",
    data_category: str = "enhancement_data",
    observed_at: Any = None,
    fetched_at: Any = None,
    market_timezone: str = "America/New_York",
    data_frequency: str = "unknown",
    data_session: str = "unavailable",
    freshness_status: str = "unavailable",
    age_hours: Any = None,
    comparable_date: Any = None,
    data_status: str | None = None,
) -> dict[str, Any]:
    normalized_status = str(data_status or "").upper()
    if normalized_status not in {
        "VALID", "VALID_LAGGED_BY_DESIGN", "ESTIMATED", "DATA_INSUFFICIENT",
        "NOT_CONNECTED", "SOURCE_FAILED", "NOT_APPLICABLE",
    }:
        status_text = str(status or "").lower()
        if status_text in {"ok", "success", "cached"} and not stale:
            normalized_status = "VALID_LAGGED_BY_DESIGN" if data_frequency in {"monthly", "quarterly"} else "VALID"
        elif status_text in {"not_connected", "unavailable", "missing"} and source == "unavailable":
            normalized_status = "NOT_CONNECTED"
        elif status_text in {"failed", "error"}:
            normalized_status = "SOURCE_FAILED"
        else:
            normalized_status = "DATA_INSUFFICIENT"
    return {
        "name": name,
        "value": value,
        "previous_value": previous_value,
        "timestamp": timestamp,
        "source": source,
        "source_level": source_level,
        "status": status,
        "data_status": normalized_status,
        "stale": stale,
        "verified_by_second_source": verified,
        "note": note,
        "data_category": data_category,
        "observed_at": observed_at or timestamp,
        "fetched_at": fetched_at,
        "market_timezone": market_timezone,
        "data_frequency": data_frequency,
        "data_session": data_session,
        "freshness_status": freshness_status,
        "age_hours": age_hours,
        "comparable_date": comparable_date or (str(observed_at or timestamp)[:10] if observed_at or timestamp else None),
    }


def _build_market_context_status(items: dict[str, Any], macro: dict[str, Any]) -> dict[str, Any]:
    vix = items.get("^VIX", {}) or {}
    macro_items = macro.get("items", {}) or {}
    indicators = [
        _status_point("VIX", value=vix.get("close"), previous_value=vix.get("previous_close"),
                      timestamp=vix.get("timestamp"), source=str(vix.get("source", "unavailable")),
                      source_level=int(vix.get("source_level", 99) or 99), status=str(vix.get("status", "missing")),
                      stale=bool(vix.get("stale")), verified=bool(vix.get("verified_by_second_source")),
                      note="Cboe优先，其他行情源仅用于交叉验证。",
                      observed_at=vix.get("observed_at"), fetched_at=vix.get("fetched_at"),
                      market_timezone=str(vix.get("market_timezone") or "America/New_York"),
                      data_frequency=str(vix.get("data_frequency") or "quote"),
                      data_session=str(vix.get("data_session") or "unavailable"),
                      freshness_status=str(vix.get("freshness_status") or "unavailable"),
                      age_hours=vix.get("age_hours"), comparable_date=vix.get("comparable_date"),
                      data_status=vix.get("data_status")),
        _status_point("Put/Call Ratio", note="未找到当前流程可稳定复用的官方接口，本版不接入。"),
        _status_point("市场宽度", note="上涨/下跌家数与新高/新低未接入；不得以指数涨跌替代。"),
        _status_point("ETF资金流", note="未接入可靠许可数据；不得以价格或成交量替代净申赎。"),
        _status_point("AAII情绪", note="未接入稳定自动数据源，仅保留状态说明。"),
    ]
    for series_id in ["DGS10", "DGS2", "T10Y2Y", "BAMLH0A0HYM2"]:
        point = macro_items.get(series_id, {}) or {}
        indicators.append(
            _status_point(
                str(point.get("name") or series_id), value=point.get("value"),
                previous_value=point.get("previous_value"), timestamp=point.get("timestamp"),
                source=str(point.get("source", "unavailable")), source_level=int(point.get("source_level", 99) or 99),
                status=str(point.get("status", "missing")), stale=bool(point.get("stale")),
                verified=bool(point.get("verified_by_second_source")), note="FRED一级来源；属于官方滞后数据。",
                observed_at=point.get("observed_at"), fetched_at=point.get("fetched_at"),
                market_timezone=str(point.get("market_timezone") or "America/New_York"),
                data_frequency=str(point.get("data_frequency") or "daily"),
                data_session=str(point.get("data_session") or "official_lagged_macro"),
                freshness_status=str(point.get("freshness_status") or "official_lagged"),
                age_hours=point.get("age_hours"), comparable_date=point.get("comparable_date"),
                data_status=point.get("data_status"),
            )
        )
    return {
        "indicators": indicators,
        "breadth_status": "available" if any(row["name"] == "市场宽度" and row["status"] == "ok" for row in indicators) else "not_connected",
        "fund_flow_status": "not_connected",
        "sentiment_status": "partial" if vix.get("status") == "ok" else "missing",
        "note": "仅列出本次真实接通的数据；未接入项不参与高确定性买入判断。",
    }


def _quality_score(items: dict[str, dict[str, Any]], macro: dict[str, Any]) -> int:
    total = 0
    count = 0

    for symbol in QUALITY_MARKET_SYMBOLS:
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
    for symbol in QUALITY_MARKET_SYMBOLS:
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
    market_completeness = build_market_completeness(items)
    try:
        write_cn_hk_p0_validation(items, market_completeness)
    except Exception as exc:  # noqa: BLE001 - validation artifact failure must not break daily report
        write_log(f"A股与港股P0验证文件写入失败：{exc}", filename="stone_ai.log")
    try:
        cn_hk_p1a = build_cn_hk_p1a_snapshot(market_completeness, market_items=items)
    except Exception as exc:  # noqa: BLE001 - P1A enhancement must never break the core snapshot
        write_log(f"A股与港股P1A增强层失败，主流程降级继续：{exc}", filename="stone_ai.log")
        cn_hk_p1a = {
            "status": "failed",
            "error_message": str(exc),
            "analysis_completeness": {
                "cn_analysis_completeness": {"score_pct": 0, "decision_restricted": True, "missing_fields": ["P1A增强层失败"]},
                "hk_analysis_completeness": {"score_pct": 0, "decision_restricted": True, "missing_fields": ["P1A增强层失败"]},
            },
        }
    items = merge_newer_validated_cn_hk_quotes(items, cn_hk_p1a)
    market_completeness = build_market_completeness(items)
    try:
        write_cn_hk_p0_validation(items, market_completeness)
    except Exception as exc:  # noqa: BLE001
        write_log(f"A股与港股合并行情验证文件写入失败：{exc}", filename="stone_ai.log")
    macro = get_macro_snapshot()
    news = get_news_and_earnings()
    quality = _build_quality_report(items, macro, news)
    errors = []
    for item in items.values():
        if item.get("status") != "ok" and item.get("error"):
            errors.append(str(item["error"]))
    errors.extend(macro.get("errors", []))
    errors.extend(news.get("errors", []))

    snapshot = {
        "source": "layered_router",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "macro": macro,
        "news": news,
        "market_context_status": _build_market_context_status(items, macro),
        "market_completeness": market_completeness,
        "cn_hk_p1a": cn_hk_p1a,
        "cn_hk_analysis_completeness": cn_hk_p1a.get("analysis_completeness", {}),
        "cn_data_completeness": market_completeness["cn_data_completeness"]["score_pct"],
        "hk_data_completeness": market_completeness["hk_data_completeness"]["score_pct"],
        "data_quality": quality,
        "source_registry": SOURCE_REGISTRY.health_snapshot(),
        "source_registry_version": "root_fix_1",
        "errors": errors,
    }
    try:
        cache_path = project_root() / "data" / "cache" / "market_snapshot.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        write_log("统一市场快照已写入 data/cache/market_snapshot.json", filename="stone_ai.log")
    except Exception as exc:  # noqa: BLE001
        write_log(f"统一市场快照写入失败：{exc}", filename="stone_ai.log")
    return snapshot
