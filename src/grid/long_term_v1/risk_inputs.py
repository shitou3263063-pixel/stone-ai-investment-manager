from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping
import json
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


QuoteFetcher = Callable[[], Mapping[str, Any]]


def fetch_usd_cny(
    *,
    now: datetime,
    settings: Mapping[str, Any],
    primary_fetch: QuoteFetcher | None = None,
    fallback_fetch: QuoteFetcher | None = None,
) -> tuple[float | None, dict[str, Any], tuple[str, ...]]:
    """Fetch a current-day USD/CNY quote without using the report cache.

    The primary path is the existing data router with caching disabled.  The
    fallback is an independent public FX API path.  A quote is accepted only
    when it has a timestamp and is within the configured freshness window.
    """
    current = _aware(now)
    max_age = float(settings.get("fx_max_age_seconds", 900))
    primary = primary_fetch or _router_fetch
    fallback = fallback_fetch or _open_er_api_fetch
    failures: list[str] = []

    for label, fetcher in (("primary", primary), ("fallback", fallback)):
        try:
            raw = dict(fetcher())
        except Exception as exc:  # noqa: BLE001 - source isolation is intentional
            failures.append(f"USD_CNY_{label.upper()}_{type(exc).__name__.upper()}")
            continue
        value = _number(raw.get("value", raw.get("close", raw.get("current_price"))))
        timestamp = _timestamp(raw)
        metadata = _metadata(raw, value=value, timestamp=timestamp, now=current, max_age=max_age)
        if value is None or timestamp is None:
            failures.append(f"USD_CNY_{label.upper()}_MISSING")
            continue
        if metadata["validity"] != "VALID":
            failures.append(f"USD_CNY_{label.upper()}_STALE")
            continue
        metadata["fallback_used"] = label == "fallback"
        metadata["fallback_source"] = metadata.get("source") if label == "fallback" else None
        if label == "fallback":
            metadata["fallback_reason"] = ";".join(failures) or "PRIMARY_UNAVAILABLE"
        return value, metadata, tuple(failures)

    final_validity = "STALE" if any(item.endswith("_STALE") for item in failures) else "MISSING"
    return None, {
        "value": None,
        "source": None,
        "as_of": None,
        "age_minutes": None,
        "validity": final_validity,
        "unavailable_reason": ";".join(failures) or "USD_CNY_UNAVAILABLE",
        "fallback_used": bool(failures),
    }, tuple(failures or ["USD_CNY_UNAVAILABLE"])


def _router_fetch() -> Mapping[str, Any]:
    from src.data_sources.data_router import get_market_quote

    item = get_market_quote(
        "USD/CNY",
        allow_cache=False,
        write_through_cache=False,
        log_events=False,
    )
    if str(item.get("source") or "").lower().startswith("cache:"):
        raise RuntimeError("router_returned_cache")
    return item


def _open_er_api_fetch() -> Mapping[str, Any]:
    """Independent public FX fallback with an explicit provider timestamp."""
    request = Request(
        "https://open.er-api.com/v6/latest/USD",
        headers={"User-Agent": "StoneAI-grid-risk-input/1.0", "Accept": "application/json"},
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("result") != "success" or not payload.get("rates", {}).get("CNY"):
        raise RuntimeError("fx_fallback_unavailable")
    timestamp = payload.get("time_last_update_unix")
    if timestamp is None:
        raise RuntimeError("fx_fallback_timestamp_missing")
    as_of = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    return {
        "value": float(payload["rates"]["CNY"]),
        "source": "open_er_api",
        "quote_timestamp": as_of,
        "data_frequency": "daily",
    }


def _metadata(
    raw: Mapping[str, Any],
    *,
    value: float | None,
    timestamp: datetime | None,
    now: datetime,
    max_age: float,
) -> dict[str, Any]:
    age = None if timestamp is None else max(0.0, (now - _aware(timestamp)).total_seconds())
    same_day = timestamp is not None and timestamp.date() == now.date()
    validity = "VALID" if value is not None and timestamp is not None and age is not None and age <= max_age and same_day else "STALE"
    source = str(raw.get("source") or "unknown")
    return {
        "value": value,
        "source": source,
        "as_of": timestamp.isoformat() if timestamp else None,
        "age_minutes": round(age / 60.0, 3) if age is not None else None,
        "validity": validity,
        "timezone": timestamp.tzinfo.key if timestamp and hasattr(timestamp.tzinfo, "key") else (str(timestamp.tzinfo) if timestamp else None),
        "data_frequency": raw.get("data_frequency"),
        "unavailable_reason": None if validity == "VALID" else "USD_CNY_STALE",
    }


def _timestamp(raw: Mapping[str, Any]) -> datetime | None:
    for key in ("quote_timestamp", "published_at", "observed_at", "as_of", "daily_index_timestamp"):
        value = raw.get(key)
        parsed = _parse_time(value)
        if parsed is not None:
            return parsed
    market_date = raw.get("market_date")
    if market_date:
        return _parse_time(f"{market_date}T00:00:00+00:00")
    return None


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
