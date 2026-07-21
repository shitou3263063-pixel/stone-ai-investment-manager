from __future__ import annotations

from datetime import datetime
from typing import Any


FINAL_CLOSE_STAGES = {"OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"}


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(f"{value}T23:59:59")
        except ValueError:
            return None


def _normalized_item(name: str, item: dict[str, Any] | Any, cutoff: datetime | None) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = {"value": item, "status": "DATA_INSUFFICIENT"}
    stage = str(item.get("data_stage") or item.get("price_stage") or "DATA_INSUFFICIENT").upper()
    timestamp = item.get("quote_timestamp") or item.get("observed_at") or item.get("published_at") or item.get("market_date") or item.get("comparable_date")
    observed = _parse_datetime(timestamp)
    status = str(item.get("data_status") or item.get("status") or "DATA_INSUFFICIENT").upper()
    is_stale = bool(item.get("stale")) or stage == "STALE"
    weekend = bool(cutoff and cutoff.weekday() >= 5)
    at_or_before = True
    if observed is not None and cutoff is not None:
        if (observed.tzinfo is None) != (cutoff.tzinfo is None):
            at_or_before = observed.date() <= cutoff.date()
        else:
            at_or_before = observed <= cutoff
    if weekend and stage in FINAL_CLOSE_STAGES and not is_stale:
        market_state = "MARKET_CLOSED"
    elif stage in FINAL_CLOSE_STAGES:
        market_state = "PREVIOUS_OFFICIAL_CLOSE" if stage == "PREVIOUS_OFFICIAL_CLOSE" else "OFFICIAL_CLOSE"
    elif stage == "INTRADAY":
        market_state = "MARKET_OPEN"
    else:
        market_state = "UNKNOWN"
    return {
        **item,
        "name": name,
        "data_stage": stage,
        "observed_at": str(timestamp or "") or None,
        "observed_at_or_before_cutoff": at_or_before,
        "market_state": market_state,
        "freshness_state": "STALE" if is_stale else ("VALID_LAGGED_BY_DESIGN" if status == "VALID_LAGGED_BY_DESIGN" else "VALID"),
        "comparability_state": str(item.get("comparability_state") or "NOT_EVALUATED").upper(),
    }


def build_market_snapshot(live_market: dict[str, Any], *, decision_cutoff_at: str) -> dict[str, Any]:
    """Normalize data states without producing a trading conclusion."""
    cutoff = _parse_datetime(decision_cutoff_at)
    items = live_market.get("items", live_market.get("market", {})) or {}
    macro = live_market.get("macro", live_market.get("macro_items", {})) or {}
    return {
        "snapshot_type": "MarketSnapshot",
        "as_of": decision_cutoff_at,
        "market": {name: _normalized_item(name, item or {}, cutoff) for name, item in sorted(items.items())},
        "macro": {name: _normalized_item(name, item or {}, cutoff) for name, item in sorted(macro.items())},
        "state_contract": {
            "market_state": ["MARKET_OPEN", "MARKET_CLOSED", "OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE", "UNKNOWN"],
            "freshness_state": ["VALID", "VALID_LAGGED_BY_DESIGN", "STALE"],
            "comparability_state": ["COMPARABLE", "DATA_NOT_COMPARABLE", "NOT_EVALUATED"],
        },
    }
