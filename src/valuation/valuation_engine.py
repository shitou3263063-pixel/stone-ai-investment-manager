from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from src.domain.security_ids import canonical_security_id, security_definition


VALID_PRICE_STAGES = {"OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"}
PENDING_STATUSES = {"pending_actual_quantity_fx_fee", "pending_actual_fx_rate", "pending_quantity_fx_fee", "trade_reconciled_valuation_fx_pending"}


def _number(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


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


def _timestamp(item: dict[str, Any]) -> str | None:
    value = item.get("quote_timestamp") or item.get("observed_at") or item.get("published_at") or item.get("market_date") or item.get("comparable_date")
    return str(value) if value not in {None, ""} else None


def _stage(item: dict[str, Any]) -> str:
    return str(item.get("data_stage") or item.get("price_stage") or "").upper()


def _value(item: dict[str, Any]) -> float | None:
    return _number(item.get("close", item.get("value", item.get("current_price"))))


def _normalized_currency(*values: Any, default: str = "CNY") -> str:
    """Return the first meaningful ISO-like currency value.

    Providers commonly emit ``unknown`` for otherwise valid quotes.  That is
    missing metadata, not a real currency, so the security master remains the
    authority in that case.
    """
    missing = {"", "UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "N/A"}
    for value in values:
        currency = str(value or "").strip().upper()
        if currency not in missing:
            return currency
    return default


def _at_or_before(item: dict[str, Any], cutoff: datetime | None) -> bool:
    observed = _parse_datetime(_timestamp(item))
    if cutoff is None or observed is None:
        return True
    if cutoff.tzinfo is None and observed.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=observed.tzinfo)
    elif cutoff.tzinfo is not None and observed.tzinfo is None:
        return observed.date() <= cutoff.date()
    return observed <= cutoff


def _usable(item: dict[str, Any], cutoff: datetime | None, *, price: bool) -> bool:
    status = str(item.get("status") or item.get("data_status") or "").lower()
    stage_ok = _stage(item) in VALID_PRICE_STAGES if price else (_stage(item) in VALID_PRICE_STAGES or not _stage(item))
    return status in {"ok", "success", "valid", "valid_lagged_by_design"} and _value(item) is not None and stage_ok and not bool(item.get("stale")) and _at_or_before(item, cutoff)


def _market_items(live_market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return live_market.get("items", live_market.get("market", {})) or {}


def _is_cost_record(row: dict[str, Any]) -> bool:
    method = str(row.get("valuation_method") or "").lower()
    return bool(row.get("is_cost_record") or row.get("reference_symbol") or "cost" in method and "pending" in method)


def _standardize_position(row: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    position = dict(row)
    security_id = canonical_security_id(
        row.get("security_id"), row.get("canonical_id"), row.get("security_code"), row.get("pricing_proxy"), row.get("asset_id")
    )
    currency = str(row.get("price_currency") or row.get("currency") or "CNY").upper()
    position.update({
        "security_id": security_id,
        "official_symbol": row.get("pricing_proxy") or row.get("security_code") or security_id,
        "total_quantity": _number(row.get("total_quantity", row.get("quantity"))),
        "quantity": _number(row.get("total_quantity", row.get("quantity"))),
        "market_value_cny": round(_number(row.get("market_value_cny")) or 0.0, 2),
        "market_price": _number(row.get("latest_price")),
        "price_currency": currency,
        "valuation_as_of": row.get("valuation_as_of") or as_of,
        "is_cost_record": False,
        "fx_rate": 1.0 if currency == "CNY" else row.get("fx_rate"),
        "fx_status": "NOT_APPLICABLE_SAME_CURRENCY" if currency == "CNY" else row.get("fx_status"),
    })
    return position


def _merge_base_positions(rows: list[dict[str, Any]], *, as_of: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        position = _standardize_position(row, as_of=as_of)
        security_id = str(position["security_id"])
        if security_id not in merged:
            merged[security_id] = position
            continue
        current = merged[security_id]
        quantities = [current.get("quantity"), position.get("quantity")]
        current["quantity"] = current["total_quantity"] = sum(float(value) for value in quantities if value is not None)
        current["market_value_cny"] = round(float(current.get("market_value_cny", 0)) + float(position.get("market_value_cny", 0)), 2)
        current["merged_source_rows"] = int(current.get("merged_source_rows", 1)) + 1
    return list(merged.values())


def _cost_record(row: dict[str, Any]) -> dict[str, Any]:
    security_id = canonical_security_id(row.get("reference_symbol"), row.get("security_code"), row.get("canonical_id"), row.get("asset_id"))
    return {
        "record_type": "COST_RECORD",
        "security_id": security_id,
        "trade_date": row.get("snapshot_date") or row.get("valuation_time"),
        "cost_basis_cny": _number(row.get("cost_basis_cny", row.get("additional_cost_cny", row.get("market_value_cny")))) or 0.0,
        "quantity": _number(row.get("actual_quantity", row.get("quantity"))),
        "included_in_market_value": False,
        "source_asset_id": row.get("asset_id"),
    }


def _trade_security_id(trade: dict[str, Any]) -> str:
    return canonical_security_id(trade.get("security_id"), trade.get("symbol"), trade.get("ticker"))


def _trade_already_in_position(trade: dict[str, Any], position: dict[str, Any]) -> bool:
    trade_time = _parse_datetime(trade.get("trade_datetime") or trade.get("trade_date"))
    confirmed = _parse_datetime(position.get("last_confirmed_at") or position.get("valuation_time"))
    if not trade_time or not confirmed:
        return False
    if (trade_time.tzinfo is None) != (confirmed.tzinfo is None):
        return trade_time.date() <= confirmed.date()
    return trade_time <= confirmed


def _apply_transactions(positions: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> set[str]:
    by_id = {str(row["security_id"]): row for row in positions}
    affected: set[str] = set()
    for trade in transactions:
        if str(trade.get("status") or "executed").lower() != "executed":
            continue
        security_id = _trade_security_id(trade)
        position = by_id.get(security_id)
        quantity = _number(trade.get("quantity"))
        if position is None or quantity is None or trade.get("id") in (position.get("merged_trade_ids", []) or []) or _trade_already_in_position(trade, position):
            continue
        side = str(trade.get("side") or trade.get("action") or "BUY").upper()
        signed = -quantity if side == "SELL" else quantity
        current = _number(position.get("quantity")) or 0.0
        position["quantity"] = position["total_quantity"] = round(current + signed, 8)
        position["quantity_source"] = "portfolio_master_plus_confirmed_ledger"
        position.setdefault("merged_trade_ids", []).append(trade.get("id"))
        affected.add(security_id)
    return affected


def _valuation_fx(currency: str, items: dict[str, dict[str, Any]], cutoff: datetime | None) -> tuple[str | None, dict[str, Any], float | None]:
    if currency == "CNY":
        return "CNY/CNY", {"source": "NOT_APPLICABLE"}, 1.0
    candidates = ("USD/CNY", "USD/CNH") if currency == "USD" else ("HKD/CNY",)
    for pair in candidates:
        item = items.get(pair, {}) or {}
        if _usable(item, cutoff, price=False):
            return pair, item, _value(item)
    return None, {}, None


def _revalue_affected(positions: list[dict[str, Any]], affected: set[str], items: dict[str, dict[str, Any]], cutoff: datetime | None, as_of: str) -> set[str]:
    valued: set[str] = set()
    for position in positions:
        security_id = str(position["security_id"])
        if security_id not in affected:
            continue
        symbol = str(position.get("official_symbol") or security_id)
        quote = items.get(symbol, items.get(security_id, {})) or {}
        currency = _normalized_currency(
            quote.get("currency"),
            security_definition(security_id).get("currency"),
            position.get("price_currency"),
        )
        pair, fx_item, fx_rate = _valuation_fx(currency, items, cutoff)
        price = _value(quote)
        quantity = _number(position.get("quantity"))
        if not _usable(quote, cutoff, price=True) or fx_rate is None or price is None or quantity is None:
            position["pending_reason"] = "MISSING_OFFICIAL_PRICE_OR_INDEPENDENT_VALUATION_FX"
            continue
        native = quantity * price
        position.update({
            "market_price": price, "latest_price": price, "price_currency": currency,
            "market_value_native": round(native, 2), "market_value_cny": round(native * fx_rate, 2),
            "fx_rate": fx_rate, "fx_pair": pair, "fx_source": fx_item.get("source"),
            "price_market_date": quote.get("market_date") or quote.get("comparable_date"),
            "quote_timestamp": _timestamp(quote), "price_stage": _stage(quote), "price_source": quote.get("source"),
            "valuation_as_of": as_of, "valuation_status": "VALUED", "pending_reason": None,
        })
        valued.add(security_id)
    return valued


def _portfolio_values(positions: list[dict[str, Any]], categories: list[str]) -> dict[str, Any]:
    asset_class_values = {category: round(sum(float(row.get("market_value_cny", 0) or 0) for row in positions if str(row.get("asset_class")) == category), 2) for category in categories}
    total = round(sum(asset_class_values.values()), 2)
    return {
        "valued_assets": positions,
        "total_valued_assets": total,
        "asset_class_values": asset_class_values,
        "asset_class_weights": {category: (value / total if total else 0.0) for category, value in asset_class_values.items()},
    }


def apply_live_valuation(base_snapshot: dict[str, Any], live_market: dict[str, Any], *, valuation_as_of: str) -> dict[str, Any]:
    """Ledger-first, symbol-normalized construction of the sole PortfolioSnapshot."""
    snapshot = deepcopy(base_snapshot)
    raw_holdings = list(snapshot.get("holdings", []) or [])
    cost_records = deepcopy(snapshot.get("cost_records", []) or []) or [_cost_record(row) for row in raw_holdings if _is_cost_record(row)]
    positions = _merge_base_positions([row for row in raw_holdings if not _is_cost_record(row)], as_of=valuation_as_of)
    transactions = deepcopy(snapshot.get("confirmed_transactions", []) or [])
    affected = _apply_transactions(positions, transactions)
    affected.update(str(row.get("security_id")) for row in cost_records if row.get("security_id"))
    cutoff = _parse_datetime(valuation_as_of)
    valued_affected = _revalue_affected(positions, affected, _market_items(live_market), cutoff, valuation_as_of)
    pending = [{**row, "valuation_status": "PENDING_VALUATION"} for row in cost_records if row["security_id"] in affected - valued_affected]
    categories = list((snapshot.get("asset_class_values") or snapshot.get("asset_class_totals") or {}).keys())
    canonical = _portfolio_values(positions, categories)
    cost_total = round(sum(float(row.get("cost_basis_cny", 0) or 0) for row in cost_records), 2)
    denominator = canonical["total_valued_assets"] + sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending)
    coverage = canonical["total_valued_assets"] / denominator if denominator else 1.0
    return {
        **snapshot, **canonical,
        "configured_total_assets": snapshot.get("total_assets"),
        "total_assets": canonical["total_valued_assets"],
        "snapshot_type": "PortfolioSnapshot", "positions": positions, "holdings": positions,
        "transaction_ledger": transactions, "cost_records": cost_records,
        "pending_valuation_assets": pending,
        "pending_valuation_total": round(sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending), 2),
        "total_cost_records_cny": cost_total,
        "total_asset_including_cost_records": round(canonical["total_valued_assets"] + sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending), 2),
        "decision_total_assets": canonical["total_valued_assets"], "decision_asset_class_totals": canonical["asset_class_values"],
        "asset_class_totals": canonical["asset_class_values"], "allocation_base_cny": canonical["total_valued_assets"],
        "precise_market_value": canonical["total_valued_assets"], "pending_valuation_cost": round(sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending), 2),
        "total_book_value": round(canonical["total_valued_assets"] + sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending), 2),
        "valuation_coverage_pct": round(coverage * 100, 4), "valuation_as_of": valuation_as_of,
        "valuation_status": "COMPLETE" if not pending else "PARTIAL", "valuation_engine": "canonical_ledger_first",
        "has_provisional_values": bool(pending), "provisional_value_cny": round(sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending), 2),
        "simulation_assets_cny": 0,
    }
