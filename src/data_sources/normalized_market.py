"""Canonical market quote model and the only market-stage/time policy.

Provider timestamps, especially a yfinance daily-bar index at local midnight,
must never be promoted to a trade timestamp or an official close timestamp.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from src.data_sources.time_normalization import TIMEZONE_UNKNOWN, normalize_to_utc


PRICE_STAGES = {
    "PRE_MARKET",
    "INTRADAY",
    "AFTER_HOURS_UNFINALIZED",
    "OFFICIAL_CLOSE",
    "PREVIOUS_OFFICIAL_CLOSE",
    "OFFICIAL_LAGGED_MACRO",
    "STALE",
    "UNKNOWN",
}

SESSION_BY_STAGE = {
    "PRE_MARKET": "pre_market",
    "INTRADAY": "intraday_delayed",
    "AFTER_HOURS_UNFINALIZED": "after_hours_unfinalized",
    "OFFICIAL_CLOSE": "official_close",
    "PREVIOUS_OFFICIAL_CLOSE": "previous_close",
    "OFFICIAL_LAGGED_MACRO": "official_lagged_macro",
    "STALE": "stale",
    "UNKNOWN": "unknown",
}


@dataclass(frozen=True)
class NormalizedMarketQuote:
    symbol: str
    formal_name: str
    market: str
    exchange: str
    currency: str
    market_timezone: str
    market_date: str | None
    quote_timestamp: str | None
    retrieved_at: str | None
    previous_official_close: float | None
    current_price: float | None
    price_stage: str
    source: str
    source_level: int
    is_finalized: bool
    is_stale: bool
    data_age_hours: float | None
    cross_validation_status: str
    validation_notes: tuple[str, ...]
    data_basis: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["validation_notes"] = list(self.validation_notes)
        return payload


def _aware(value: Any, *, default_timezone: str | None = None) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        return normalize_to_utc(value, source_timezone=default_timezone)
    except (TypeError, ValueError, KeyError):
        return None


def _float(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def exchange_close_timestamp(market_date: str, market_timezone: str, market: str) -> datetime:
    """Return the actual regular-session close with DST handled by ZoneInfo."""
    close_hour = 16 if market in {"US", "HK"} else 15 if market == "CN" else 16
    return datetime.combine(date.fromisoformat(market_date), time(close_hour, 0), tzinfo=ZoneInfo(market_timezone))


def classify_price_stage(
    *,
    market: str,
    market_timezone: str,
    market_date: str | None,
    decision_cutoff: datetime,
    source_finalized: bool,
    status_ok: bool = True,
    stale: bool = False,
    is_macro: bool = False,
    explicit_previous_close: bool = False,
) -> str:
    """Single authoritative stage classifier used by every downstream module."""
    if is_macro:
        return "OFFICIAL_LAGGED_MACRO" if status_ok else "UNKNOWN"
    if not status_ok:
        return "UNKNOWN"
    if stale:
        return "STALE"
    if not market_date:
        return "UNKNOWN"
    try:
        local_cutoff = decision_cutoff.astimezone(ZoneInfo(market_timezone))
        market_day = date.fromisoformat(str(market_date)[:10])
    except (ValueError, KeyError):
        return "UNKNOWN"
    if explicit_previous_close or market_day < local_cutoff.date():
        return "PREVIOUS_OFFICIAL_CLOSE"
    if market_day > local_cutoff.date():
        return "UNKNOWN"

    open_at = time(9, 30)
    close_at = time(16, 0) if market in {"US", "HK"} else time(15, 0)
    local_time = local_cutoff.timetz().replace(tzinfo=None)
    if local_time < open_at:
        return "PRE_MARKET"
    if local_time < close_at:
        return "INTRADAY"
    return "OFFICIAL_CLOSE" if source_finalized else "AFTER_HOURS_UNFINALIZED"


def normalize_market_quote(
    item: dict[str, Any],
    *,
    decision_cutoff: datetime,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one provider result without manufacturing time precision."""
    if decision_cutoff.tzinfo is None:
        raise ValueError(TIMEZONE_UNKNOWN)
    meta = metadata or {}
    source = str(item.get("source") or "unavailable")
    source_key = source.split(":", 1)[-1] if source.startswith("cache:") else source
    symbol = str(item.get("symbol") or meta.get("symbol") or "")
    market = str(item.get("market") or meta.get("market") or ("HK" if symbol.endswith(".HK") else "CN" if symbol.endswith((".SS", ".SZ")) else "US"))
    market_timezone = str(item.get("market_timezone") or item.get("timezone") or meta.get("timezone") or ("Asia/Hong_Kong" if market == "HK" else "Asia/Shanghai" if market == "CN" else "America/New_York"))
    status_ok = str(item.get("status") or "ok").lower() in {"ok", "success", "cached"}
    stale = bool(item.get("stale") or item.get("cache_stale") or str(item.get("freshness_status") or "").lower() == "stale")
    raw_session = str(item.get("data_session") or item.get("price_stage") or "").lower()
    is_macro = bool(item.get("series_id")) or raw_session == "official_lagged_macro" or source_key == "fred"
    explicit_previous = raw_session in {"previous_close", "previous_official_close"}
    source_finalized = bool(item.get("is_finalized") or item.get("daily_bar_finalized"))
    if raw_session == "official_close" and source_key != "yfinance":
        source_finalized = True

    market_date = item.get("market_date") or item.get("comparable_date") or item.get("market_session_date")
    if not market_date and source_key == "yfinance":
        market_date = item.get("daily_index_market_date") or (str(item.get("published_at"))[:10] if item.get("published_at") else None)
    if market_date:
        market_date = str(market_date)[:10]

    stage = classify_price_stage(
        market=market,
        market_timezone=market_timezone,
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        source_finalized=source_finalized,
        status_ok=status_ok,
        stale=stale,
        is_macro=is_macro,
        explicit_previous_close=explicit_previous,
    )

    notes: list[str] = []
    retrieved_raw = item.get("retrieved_at") or item.get("fetched_at") or item.get("received_at_utc")
    retrieved = _aware(retrieved_raw, default_timezone=str(item.get("received_timezone") or "Asia/Shanghai"))
    if retrieved is None:
        notes.append("retrieved_at缺失或无可靠时区")
    elif retrieved > decision_cutoff.astimezone(timezone.utc):
        notes.append("retrieved_at晚于决策截止时间")

    # yfinance daily indexes represent only the trading date.  Never parse the
    # local midnight index as a quote or close timestamp.
    quote_raw = item.get("quote_timestamp") or item.get("source_observation_time") or item.get("observed_at_utc")
    if is_macro:
        quote_raw = item.get("release_timestamp") or item.get("quote_timestamp")
        if not quote_raw:
            notes.append("宏观序列仅有观察期日期，未制造日内quote_timestamp")
    elif source_key != "yfinance" or str(item.get("data_frequency") or "").lower() not in {"daily", "1d"}:
        quote_raw = quote_raw or item.get("observed_at") or item.get("published_at")
    elif item.get("published_at") or item.get("daily_index_timestamp"):
        notes.append("yfinance日线午夜索引仅用于market_date")
    quote_timestamp = _aware(quote_raw, default_timezone=str(item.get("source_timezone") or market_timezone))
    if quote_timestamp is None and market_date and stage in {"OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"}:
        quote_timestamp = exchange_close_timestamp(market_date, market_timezone, market).astimezone(timezone.utc)
    elif quote_timestamp is None and stage in {"PRE_MARKET", "INTRADAY", "AFTER_HOURS_UNFINALIZED"} and retrieved is not None:
        quote_timestamp = retrieved
        notes.append("无独立报价时间，盘中数据年龄按retrieved_at计算")

    data_age: float | None = None
    if quote_timestamp is not None:
        if retrieved is not None and quote_timestamp > retrieved:
            notes.append("quote_timestamp晚于retrieved_at")
        elif quote_timestamp > decision_cutoff.astimezone(timezone.utc):
            notes.append("quote_timestamp晚于决策截止时间")
        else:
            data_age = round((decision_cutoff.astimezone(timezone.utc) - quote_timestamp).total_seconds() / 3600, 3)
    else:
        notes.append("无可靠quote_timestamp，data_age_hours=None")

    data_basis = {
        "PRE_MARKET": "盘前快照",
        "INTRADAY": "盘中或延迟盘中",
        "AFTER_HOURS_UNFINALIZED": "收盘后未完成结算",
        "OFFICIAL_CLOSE": "当日官方收盘",
        "PREVIOUS_OFFICIAL_CLOSE": "上一交易日官方收盘",
        "OFFICIAL_LAGGED_MACRO": "官方滞后宏观数据",
        "STALE": "过期行情",
        "UNKNOWN": "行情阶段未知",
    }[stage]
    cross_status = str(item.get("cross_validation_status") or ("VERIFIED" if item.get("verified_by_second_source") else "SINGLE_SOURCE"))
    quote = NormalizedMarketQuote(
        symbol=symbol,
        formal_name=str(item.get("formal_name") or item.get("official_name") or meta.get("official_name") or symbol),
        market=market,
        exchange=str(item.get("exchange") or meta.get("exchange") or "unknown"),
        currency=str(item.get("currency") or meta.get("currency") or "unknown"),
        market_timezone=market_timezone,
        market_date=market_date,
        quote_timestamp=quote_timestamp.isoformat() if quote_timestamp else None,
        retrieved_at=retrieved.isoformat() if retrieved else None,
        previous_official_close=_float(item.get("previous_official_close", item.get("previous_close", item.get("previous_value")))),
        current_price=_float(item.get("current_price", item.get("close", item.get("value")))),
        price_stage=stage,
        source=source,
        source_level=int(item.get("source_level") or 99),
        is_finalized=stage == "OFFICIAL_CLOSE",
        is_stale=stage == "STALE",
        data_age_hours=data_age,
        cross_validation_status=cross_status,
        validation_notes=tuple(notes),
        data_basis=data_basis,
    )
    normalized = quote.to_dict()
    return {
        **item,
        **normalized,
        "close": normalized["current_price"],
        "value": normalized["current_price"],
        "previous_close": normalized["previous_official_close"],
        "data_stage": stage,
        "data_session": SESSION_BY_STAGE[stage],
        "observed_at_utc": normalized["quote_timestamp"],
        "source_observation_time": normalized["quote_timestamp"],
        "received_at_utc": normalized["retrieved_at"],
        "data_retrieval_time": normalized["retrieved_at"],
        "fetched_at": normalized["retrieved_at"],
        "timestamp": normalized["quote_timestamp"],
        "source_timezone": str(item.get("source_timezone") or market_timezone),
        "time_status": "ok" if normalized["quote_timestamp"] or is_macro else TIMEZONE_UNKNOWN,
        "comparable_date": market_date,
        "age_hours": data_age,
        "freshness_status": "stale" if normalized["is_stale"] else "fresh" if status_ok else "unavailable",
        "stale": normalized["is_stale"],
        "status": "ok" if status_ok else str(item.get("status") or "missing"),
        "fallback_used": bool(item.get("fallback_used") or item.get("cache_used") or source.startswith("cache:")),
    }


def select_best_normalized_quote(
    candidates: Iterable[dict[str, Any]],
    *,
    price_conflict_threshold_pct: float = 1.0,
) -> dict[str, Any]:
    """Prefer a validated newer market date; older sources become cross-checks."""
    usable = [dict(item) for item in candidates if item.get("status") in {"ok", "success", "cached"} and item.get("market_date") and item.get("current_price", item.get("close")) is not None]
    if not usable:
        return {}
    symbols = {str(item.get("symbol") or "") for item in usable}
    currencies = {str(item.get("currency") or "") for item in usable if item.get("currency")}
    exchanges = {str(item.get("exchange") or "") for item in usable if item.get("exchange")}
    if len(symbols) > 1 or len(currencies) > 1 or len(exchanges) > 1:
        return {**usable[0], "status": "failed", "cross_validation_status": "IDENTITY_CONFLICT", "scoring_eligible": False}
    usable.sort(key=lambda item: (str(item.get("market_date")), -int(item.get("source_level") or 99)), reverse=True)
    selected = dict(usable[0])
    same_day = [item for item in usable if item.get("market_date") == selected.get("market_date")]
    values = [_float(item.get("current_price", item.get("close"))) for item in same_day]
    values = [value for value in values if value is not None and value > 0]
    conflict = len(values) >= 2 and (max(values) / min(values) - 1) * 100 > price_conflict_threshold_pct
    selected["cross_validation_status"] = "SOURCE_CONFLICT" if conflict else "VERIFIED" if len(same_day) >= 2 else "LATEST_DATE_SINGLE_SOURCE"
    selected["scoring_eligible"] = not conflict
    selected["comparison_sources"] = [item.get("source") for item in usable]
    return selected


def market_quote_reference(item: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
    """Return the immutable quote fields shared by risk, opportunity and grid."""
    return {
        "symbol": str(symbol or item.get("symbol") or ""),
        "current_price": _float(item.get("current_price", item.get("close", item.get("value")))),
        "market_date": item.get("market_date"),
        "price_stage": str(item.get("price_stage") or item.get("data_stage") or "UNKNOWN").upper(),
        "quote_timestamp": item.get("quote_timestamp") or item.get("observed_at_utc"),
        "retrieved_at": item.get("retrieved_at") or item.get("received_at_utc") or item.get("fetched_at"),
        "source": item.get("source", "unavailable"),
        "is_finalized": bool(item.get("is_finalized")),
    }
