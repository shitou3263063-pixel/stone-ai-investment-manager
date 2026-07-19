from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from src.portfolio_snapshot import _canonical_portfolio_values


VALID_PRICE_STAGES = {"OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"}


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


def _number(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _item_value(item: dict[str, Any]) -> float | None:
    return _number(item.get("close", item.get("value")))


def _status_ok(item: dict[str, Any]) -> bool:
    return str(item.get("status") or item.get("data_status") or "").lower() in {
        "ok", "success", "valid", "valid_lagged_by_design",
    } and _item_value(item) is not None


def _timestamp(item: dict[str, Any]) -> str | None:
    return str(
        item.get("quote_timestamp")
        or item.get("observed_at")
        or item.get("published_at")
        or item.get("market_date")
        or item.get("comparable_date")
        or ""
    ) or None


def _stage(item: dict[str, Any]) -> str:
    return str(item.get("data_stage") or item.get("price_stage") or "").upper()


def _at_or_before(item: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    observed = _parse_datetime(_timestamp(item))
    if observed is None:
        return True
    if cutoff.tzinfo is None and observed.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=observed.tzinfo)
    return observed <= cutoff


def _market_items(live_market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return live_market.get("items", live_market.get("market", {})) or {}


def _standardize_holding(row: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    result = dict(row)
    currency = str(result.get("currency") or result.get("price_currency") or "CNY").upper()
    same_currency = currency == "CNY" or str(result.get("market_value_original_currency") or "").upper() == "CNY"
    result.update({
        "security_id": result.get("canonical_id") or result.get("asset_id"),
        "official_symbol": result.get("security_code") or result.get("pricing_proxy"),
        "quantity_source": result.get("quantity_source") or result.get("holding_source") or result.get("source"),
        "cost_basis_native": result.get("cost_basis_native"),
        "cost_basis_cny": result.get("cost_basis_cny", result.get("additional_cost_cny")),
        "latest_price": result.get("latest_price"),
        "price_currency": result.get("price_currency") or currency,
        "price_market_date": result.get("price_market_date"),
        "quote_timestamp": result.get("quote_timestamp"),
        "price_stage": result.get("price_stage"),
        "price_source": result.get("price_source"),
        "fx_rate": 1.0 if same_currency else result.get("fx_rate"),
        "fx_pair": "CNY/CNY" if same_currency else result.get("fx_pair"),
        "fx_market_date": result.get("fx_market_date"),
        "fx_timestamp": result.get("fx_timestamp"),
        "fx_source": "NOT_APPLICABLE" if same_currency else result.get("fx_source"),
        "fx_status": "NOT_APPLICABLE_SAME_CURRENCY" if same_currency else result.get("fx_status"),
        "market_value_native": result.get("market_value_native", result.get("market_value_original")),
        "valuation_as_of": result.get("valuation_as_of") or as_of,
        "valuation_confidence": result.get("valuation_confidence") or result.get("confidence") or "medium",
        "pending_reason": result.get("pending_reason"),
    })
    return result


def _valid_quote(item: dict[str, Any], cutoff: datetime | None) -> bool:
    return _status_ok(item) and _stage(item) in VALID_PRICE_STAGES and _at_or_before(item, cutoff)


def _valid_fx(item: dict[str, Any], cutoff: datetime | None) -> bool:
    return _status_ok(item) and _at_or_before(item, cutoff) and (_stage(item) in VALID_PRICE_STAGES or not _stage(item))


def apply_live_valuation(
    base_snapshot: dict[str, Any],
    live_market: dict[str, Any],
    *,
    valuation_as_of: str,
) -> dict[str, Any]:
    """Produce the only precise portfolio valuation used by decision/report layers.

    The VOO cost record is retained for audit but cannot enter market value.  It is
    merged into the original holding only when both an official-close price and an
    independent valuation FX quote are present at or before the decision cutoff.
    """
    snapshot = deepcopy(base_snapshot)
    holdings = [_standardize_holding(row, as_of=valuation_as_of) for row in snapshot.get("holdings", []) or []]
    cutoff = _parse_datetime(valuation_as_of)
    items = _market_items(live_market)
    quote = items.get("VOO", {}) or {}
    fx_pair, fx = next(
        ((pair, items.get(pair, {}) or {}) for pair in ("USD/CNY", "USD/CNH") if _valid_fx(items.get(pair, {}) or {}, cutoff)),
        (None, {}),
    )
    original = next((row for row in holdings if str(row.get("security_code")) == "VOO" and not row.get("reference_symbol")), None)
    cost_record = next((row for row in holdings if str(row.get("reference_symbol")) == "VOO"), None)
    cost_records: list[dict[str, Any]] = []
    resolved = False
    if original and cost_record:
        added_quantity = _number(cost_record.get("actual_quantity", cost_record.get("quantity"))) or 0.0
        total_quantity = (_number(original.get("quantity")) or 0.0) + added_quantity
        price = _item_value(quote)
        fx_rate = _item_value(fx)
        cost_records.append({
            "security_id": cost_record.get("asset_id"),
            "official_symbol": "VOO",
            "cost_basis_cny": _number(cost_record.get("additional_cost_cny", cost_record.get("market_value_cny"))) or 0.0,
            "quantity": added_quantity,
            "trade_date": cost_record.get("snapshot_date"),
            "included_in_precise_market_value": False,
        })
        if _valid_quote(quote, cutoff) and fx_pair and price is not None and fx_rate is not None:
            market_value_native_raw = total_quantity * price
            market_value_native = round(market_value_native_raw, 2)
            market_value_cny = round(market_value_native_raw * fx_rate, 2)
            original.update({
                "quantity": total_quantity,
                "quantity_source": "portfolio_master_plus_confirmed_execution",
                "latest_price": price,
                "price_currency": "USD",
                "price_market_date": quote.get("market_date") or quote.get("comparable_date"),
                "quote_timestamp": _timestamp(quote),
                "price_stage": _stage(quote),
                "price_source": quote.get("source"),
                "fx_rate": fx_rate,
                "fx_pair": fx_pair,
                "fx_market_date": fx.get("market_date") or fx.get("comparable_date"),
                "fx_timestamp": _timestamp(fx),
                "fx_source": fx.get("source"),
                "fx_status": "VALID_VALUATION_FX",
                "market_value_native": market_value_native,
                "market_value_cny": market_value_cny,
                "valuation_as_of": valuation_as_of,
                "valuation_status": "VALUED",
                "valuation_confidence": "high" if quote.get("source_tier") in {1, 2} else "medium",
                "pending_reason": None,
            })
            cost_record.update({
                "market_value_cny": 0.0,
                "market_value_native": 0.0,
                "valuation_status": "COST_RECORD_SUPERSEDED",
                "is_cost_record": True,
                "pending_reason": None,
            })
            resolved = True
        else:
            cost_record.update({
                "is_cost_record": True,
                "valuation_status": "trade_reconciled_valuation_fx_pending",
                "pending_reason": "MISSING_OFFICIAL_PRICE" if not _valid_quote(quote, cutoff) else "MISSING_INDEPENDENT_VALUATION_FX",
            })

    categories = list((snapshot.get("asset_class_values") or snapshot.get("asset_class_totals") or {}).keys())
    canonical = _canonical_portfolio_values(holdings, categories)
    denominator = canonical["total_valued_assets"] + canonical["pending_valuation_total"]
    coverage = canonical["total_valued_assets"] / denominator if denominator else 1.0
    return {
        **snapshot,
        **canonical,
        "holdings": holdings,
        "decision_total_assets": canonical["total_valued_assets"],
        "decision_asset_class_totals": canonical["asset_class_values"],
        "asset_class_totals": canonical["asset_class_values"],
        "allocation_base_cny": canonical["total_valued_assets"],
        "precise_market_value": canonical["total_valued_assets"],
        "pending_valuation_cost": canonical["pending_valuation_total"],
        "total_book_value": canonical["total_asset_including_cost_records"],
        "valuation_coverage_pct": round(coverage * 100, 4),
        "valuation_as_of": valuation_as_of,
        "valuation_status": "COMPLETE" if not canonical["pending_valuation_assets"] else "PARTIAL",
        "cost_records": cost_records,
        "voo_trade_merged_once": resolved,
        "valuation_engine": "canonical_root_fix_1",
        "patch_level": "root_fix_1",
        "has_provisional_values": bool(canonical["pending_valuation_assets"]),
        "provisional_value_cny": canonical["pending_valuation_total"],
    }
