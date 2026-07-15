"""Time governance for one auditable daily decision snapshot."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.data_sources.time_normalization import normalize_to_utc


REPORT_TIMEZONE = "Asia/Shanghai"
STAGE_MAP = {
    "pre_market": "PRE_MARKET", "realtime": "INTRADAY", "intraday": "INTRADAY",
    "intraday_delayed": "INTRADAY", "delayed_intraday": "INTRADAY",
    "official_close": "OFFICIAL_CLOSE", "previous_close": "DELAYED_CLOSE",
    "delayed_close": "DELAYED_CLOSE", "official_lagged_macro": "OFFICIAL_LAGGED_MACRO",
    "stale": "STALE",
}


def now_report_time() -> datetime:
    return datetime.now(tz=ZoneInfo(REPORT_TIMEZONE))


def to_report_iso(value: datetime) -> str:
    return value.astimezone(ZoneInfo(REPORT_TIMEZONE)).isoformat(timespec="seconds")


def item_time_metadata(item: dict[str, Any], *, market_session_date: str | None = None) -> dict[str, Any]:
    source_timezone = item.get("source_timezone") or item.get("market_timezone") or item.get("timezone")
    source_value = item.get("source_observation_time") or item.get("observed_at_utc") or item.get("observed_at") or item.get("published_at") or item.get("date")
    retrieved_value = item.get("data_retrieval_time") or item.get("received_at_utc") or item.get("retrieved_at") or item.get("fetched_at")
    try:
        observed_iso = normalize_to_utc(source_value, source_timezone=str(source_timezone) if source_timezone else None).isoformat()
    except (TypeError, ValueError, KeyError):
        observed_iso = None
    try:
        retrieved_iso = normalize_to_utc(retrieved_value, source_timezone=str(source_timezone) if source_timezone else None).isoformat()
    except (TypeError, ValueError, KeyError):
        retrieved_iso = None
    raw_session = str(item.get("data_session") or item.get("data_stage") or "").lower()
    stage = STAGE_MAP.get(raw_session, "UNKNOWN")
    if item.get("cache_stale") or str(item.get("freshness_status") or "").lower() == "stale":
        stage = "STALE"
    return {
        "source_observation_time": observed_iso,
        "data_retrieval_time": retrieved_iso,
        "market_session_date": str(item.get("market_session_date") or item.get("comparable_date") or item.get("market_date") or market_session_date or "") or None,
        "data_stage": stage,
    }


def filter_market_for_cutoff(live_market: dict[str, Any], cutoff: datetime) -> dict[str, Any]:
    """Post-cutoff inputs are retained only in an audit appendix."""
    payload = deepcopy(live_market or {})
    cutoff_utc = cutoff.astimezone(timezone.utc)
    excluded: list[dict[str, Any]] = []
    for container_key, items_key in (("", "items"), ("macro", "items")):
        container = payload if not container_key else (payload.get(container_key) or {})
        eligible: dict[str, Any] = {}
        for symbol, source_item in (container.get(items_key, {}) or {}).items():
            item = dict(source_item or {})
            metadata = item_time_metadata(item)
            item.update(metadata)
            try:
                after_cutoff = bool(metadata["source_observation_time"]) and datetime.fromisoformat(str(metadata["source_observation_time"])).astimezone(timezone.utc) > cutoff_utc
            except ValueError:
                after_cutoff = True
            if after_cutoff:
                excluded.append({"symbol": symbol, "scope": "market" if not container_key else "macro", **metadata, "reason": "source_observation_time_after_decision_cutoff"})
            else:
                eligible[symbol] = item
        container[items_key] = eligible
        if container_key:
            payload[container_key] = container
    payload["decision_timing"] = {
        "report_generation_time": to_report_iso(cutoff),
        "decision_cutoff_time": to_report_iso(cutoff),
        "report_timezone": REPORT_TIMEZONE,
        "post_cutoff_data": excluded,
    }
    return payload
