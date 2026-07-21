from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from src.domain.security_ids import canonical_security_id, security_definition


VALID_PRICE_STAGES = {"INTRADAY", "OFFICIAL_CLOSE", "PREVIOUS_OFFICIAL_CLOSE"}
PENDING_STATUSES = {"pending_actual_quantity_fx_fee", "pending_actual_fx_rate", "pending_quantity_fx_fee", "trade_reconciled_valuation_fx_pending"}
PRECISE_VALUATION_STATUSES = {"VALUED_REALTIME", "VALUED_PREVIOUS_CLOSE", "MANUAL_FIXED_VALUE"}
MARKET_VALUATION_STATUSES = {
    "VALUED_REALTIME", "VALUED_PREVIOUS_CLOSE", "STALE_MARKET_PRICE",
    "STALE_USER_CONFIRMED_VALUE", "MISSING_PRICE", "MISSING_FX",
}


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
    items = dict(live_market.get("items", live_market.get("market", {})) or {})
    if "HKD/CNY" not in items:
        hkma = ((live_market.get("cn_hk_p1a", {}) or {}).get("hkma", {}) or {})
        metrics = hkma.get("metrics", {}) or {}
        usd_hkd = _number(metrics.get("usd_hkd"))
        cny_hkd = _number(metrics.get("cny_hkd"))
        usd_cny_item = items.get("USD/CNY", items.get("USD/CNH", {})) or {}
        usd_cny = _value(usd_cny_item)
        rate = usd_cny / usd_hkd if usd_cny and usd_hkd else (1 / cny_hkd if cny_hkd else None)
        if rate is not None:
            items["HKD/CNY"] = {
                "status": "ok",
                "value": rate,
                "source": "HKMA+USD/CNY" if usd_cny and usd_hkd else "HKMA",
                "observed_at": _timestamp(usd_cny_item) or hkma.get("generated_at"),
                "data_stage": _stage(usd_cny_item),
                "stale": bool(usd_cny_item.get("stale")),
            }
    return items


def _is_cost_record(row: dict[str, Any]) -> bool:
    method = str(row.get("valuation_method") or "").lower()
    return bool(row.get("is_cost_record") or row.get("reference_symbol") or "cost" in method and "pending" in method)


def _standardize_position(row: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    position = dict(row)
    security_id = canonical_security_id(
        row.get("security_id"), row.get("canonical_id"), row.get("security_code"), row.get("pricing_proxy"), row.get("asset_id")
    )
    definition = security_definition(security_id)
    currency = _normalized_currency(
        row.get("price_currency"), definition.get("currency"), row.get("currency")
    )
    official_symbol = (
        row.get("pricing_proxy") or definition.get("pricing_proxy")
        or row.get("security_code") or definition.get("ticker") or security_id
    )
    confirmed_value = round(_number(row.get("market_value_cny")) or 0.0, 2)
    position.update({
        "security_id": security_id,
        "official_symbol": official_symbol,
        "total_quantity": _number(row.get("total_quantity", row.get("quantity"))),
        "quantity": _number(row.get("total_quantity", row.get("quantity"))),
        "market_value_cny": confirmed_value,
        "user_confirmed_market_value_cny": confirmed_value,
        "user_confirmed_value_as_of": row.get("valuation_time") or row.get("last_confirmed_at"),
        "market_price": _number(row.get("latest_price")),
        "price_currency": currency,
        "valuation_as_of": row.get("valuation_as_of") or as_of,
        "is_cost_record": False,
        "fx_rate": 1.0 if currency == "CNY" else row.get("fx_rate"),
        "fx_status": "NOT_APPLICABLE_SAME_CURRENCY" if currency == "CNY" else row.get("fx_status"),
    })
    return position


def _is_marketable(position: dict[str, Any]) -> bool:
    definition = security_definition(str(position.get("security_id") or ""))
    exchange = str(definition.get("exchange") or position.get("listing_market") or "").lower()
    strategy_type = str(definition.get("strategy_type") or position.get("strategy_bucket") or "").lower()
    method = str(position.get("valuation_method") or "").lower()
    if position.get("manual_override") or exchange in {"cash", "physical"}:
        return False
    if "portfolio_level_principal" in method or strategy_type in {"defensive_bond", "cash_reserve"}:
        return False
    return _number(position.get("quantity")) is not None and strategy_type in {
        "core_etf", "growth_etf", "duration_bond_etf", "sector_etf",
        "thematic_etf", "single_stock", "single_stock_high_risk", "defensive_gold",
    }


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


def _revalue_positions(positions: list[dict[str, Any]], items: dict[str, dict[str, Any]], cutoff: datetime | None, as_of: str) -> set[str]:
    precisely_valued: set[str] = set()
    for position in positions:
        security_id = str(position["security_id"])
        if not _is_marketable(position):
            position.update({
                "valuation_status": "MANUAL_FIXED_VALUE",
                "valuation_as_of": position.get("last_confirmed_at") or position.get("valuation_time") or as_of,
                "price_source": position.get("source") or position.get("data_source") or "user_confirmed",
                "pending_reason": None,
            })
            precisely_valued.add(security_id)
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
        quote_usable = _usable(quote, cutoff, price=True)
        if not quote_usable or fx_rate is None or price is None or quantity is None:
            fallback = _number(position.get("user_confirmed_market_value_cny"))
            missing_status = "MISSING_PRICE" if price is None or not quote else "MISSING_FX"
            if price is not None and bool(quote.get("stale")) and fx_rate is not None and quantity is not None:
                position["market_value_cny"] = round(quantity * price * fx_rate, 2)
                valuation_status = "STALE_MARKET_PRICE"
            elif fallback is not None:
                position["market_value_cny"] = round(fallback, 2)
                valuation_status = "STALE_USER_CONFIRMED_VALUE"
            else:
                position["market_value_cny"] = 0.0
                valuation_status = missing_status
            position.update({
                "market_price": price,
                "latest_price": price,
                "price_currency": currency,
                "fx_rate": fx_rate,
                "fx_pair": pair,
                "fx_source": fx_item.get("source"),
                "quote_timestamp": _timestamp(quote),
                "price_market_date": quote.get("market_date") or quote.get("comparable_date"),
                "price_stage": _stage(quote),
                "price_source": quote.get("source") or position.get("source") or position.get("data_source"),
                "valuation_as_of": as_of,
                "valuation_status": valuation_status,
                "pending_reason": missing_status,
            })
            continue
        native = quantity * price
        position.update({
            "market_price": price, "latest_price": price, "price_currency": currency,
            "market_value_native": round(native, 2), "market_value_cny": round(native * fx_rate, 2),
            "fx_rate": fx_rate, "fx_pair": pair, "fx_source": fx_item.get("source"),
            "price_market_date": quote.get("market_date") or quote.get("comparable_date"),
            "quote_timestamp": _timestamp(quote), "price_stage": _stage(quote), "price_source": quote.get("source"),
            "valuation_as_of": as_of,
            "valuation_status": "VALUED_REALTIME" if _stage(quote) == "INTRADAY" else "VALUED_PREVIOUS_CLOSE",
            "pending_reason": None,
        })
        precisely_valued.add(security_id)
    return precisely_valued


def _portfolio_values(positions: list[dict[str, Any]], categories: list[str]) -> dict[str, Any]:
    asset_class_values = {category: round(sum(float(row.get("market_value_cny", 0) or 0) for row in positions if str(row.get("asset_class")) == category), 2) for category in categories}
    total = round(sum(asset_class_values.values()), 2)
    precise_total = round(sum(
        float(row.get("market_value_cny", 0) or 0) for row in positions if row.get("precise_valuation")
    ), 2)
    stale_total = round(total - precise_total, 2)
    return {
        "valued_assets": positions,
        "total_valued_assets": total,
        "precise_valued_assets": precise_total,
        "stale_valued_assets": stale_total,
        "asset_class_values": asset_class_values,
        "asset_class_weights": {category: (value / total if total else 0.0) for category, value in asset_class_values.items()},
    }


def _valuation_audit(positions: list[dict[str, Any]], *, as_of: str) -> dict[str, Any]:
    """Attach the factual inputs needed to reproduce every position valuation."""
    incomplete: list[dict[str, Any]] = []
    for position in positions:
        price = _number(position.get("market_price", position.get("latest_price")))
        currency = _normalized_currency(position.get("price_currency"), position.get("currency"))
        price_basis = "MARKET_QUOTE"
        fx_rate = _number(position.get("fx_rate", position.get("exchange_rate")))
        if currency == "CNY":
            fx_rate = 1.0
        price_as_of = (
            position.get("quote_timestamp")
            or position.get("price_market_date")
            or position.get("valuation_time")
            or position.get("last_confirmed_at")
            or position.get("valuation_as_of")
            or as_of
        )
        source = position.get("price_source") or position.get("source") or position.get("data_source")
        status = str(position.get("valuation_status") or "").upper()
        fixed_value = status == "MANUAL_FIXED_VALUE"
        required = {
            "price": price,
            "currency": currency,
            "fx_rate": fx_rate,
            "price_as_of": price_as_of,
            "source": source,
            "valuation_status": position.get("valuation_status"),
        }
        missing = [
            name for name, value in required.items()
            if value in {None, ""} and not (fixed_value and name in {"price", "fx_rate"})
        ]
        original_status = str(position.get("valuation_status") or "").lower()
        pending = original_status in PENDING_STATUSES or bool(position.get("pending_reason"))
        precise = status in PRECISE_VALUATION_STATUSES and not missing and not pending
        position.update(
            {
                "price": price,
                "currency": currency,
                "fx_rate": fx_rate,
                "price_as_of": str(price_as_of) if price_as_of not in {None, ""} else None,
                "source": source,
                "price_basis": price_basis,
                "precise_valuation": precise,
                "is_precise": precise,
                "valuation_precision_status": "PRECISE_VALUATION" if precise else "DATA_INSUFFICIENT",
                "valuation_audit_missing_fields": missing,
            }
        )
        if not precise:
            incomplete.append(
                {
                    "security_id": position.get("security_id"),
                    "missing_fields": missing or ["valuation_status"],
                    "source": source or "UNKNOWN",
                    "price_as_of": price_as_of,
                }
            )
    return {
        "required_fields": ["price", "currency", "fx_rate", "price_as_of", "source", "valuation_status"],
        "positions_checked": len(positions),
        "precise_positions": sum(1 for row in positions if row.get("precise_valuation")),
        "incomplete_positions": incomplete,
        "complete": not incomplete,
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
    precisely_valued = _revalue_positions(positions, _market_items(live_market), cutoff, valuation_as_of)
    pending = [
        {**row, "valuation_status": "PENDING_VALUATION"}
        for row in cost_records if row["security_id"] in affected - precisely_valued
    ]
    valuation_audit = _valuation_audit(positions, as_of=valuation_as_of)
    categories = list((snapshot.get("asset_class_values") or snapshot.get("asset_class_totals") or {}).keys())
    canonical = _portfolio_values(positions, categories)
    pending_total = round(sum(float(row.get("cost_basis_cny", 0) or 0) for row in pending), 2)
    household_total_assets_estimated = round(canonical["total_valued_assets"] + pending_total, 2)
    household_asset_class_values = dict(canonical["asset_class_values"])
    cash_detail = snapshot.get("cash", {}) or {}
    portfolio_cash = float(
        snapshot.get("investable_cash", cash_detail.get("investable_cash_cny", 0)) or 0
    )
    investable_asset_class_values = dict(household_asset_class_values)
    if "现金" in investable_asset_class_values:
        investable_asset_class_values["现金"] = round(portfolio_cash, 2)
    investable_portfolio_assets = round(sum(investable_asset_class_values.values()), 2)
    investable_asset_class_weights = {
        category: (value / investable_portfolio_assets if investable_portfolio_assets else 0.0)
        for category, value in investable_asset_class_values.items()
    }
    household_safety_reserve = float(
        snapshot.get("safety_cash", cash_detail.get("cash_safety_reserve_cny", 0)) or 0
    )
    cost_total = round(sum(float(row.get("cost_basis_cny", 0) or 0) for row in cost_records), 2)
    coverage = (
        canonical["precise_valued_assets"] / household_total_assets_estimated
        if household_total_assets_estimated else 1.0
    )
    return {
        **snapshot, **canonical,
        "household_total_assets": household_total_assets_estimated,
        "household_total_assets_estimated": household_total_assets_estimated,
        "household_asset_class_values": household_asset_class_values,
        "household_asset_class_weights": canonical["asset_class_weights"],
        "household_safety_reserve": household_safety_reserve,
        "portfolio_cash": round(portfolio_cash, 2),
        "investable_portfolio_assets": investable_portfolio_assets,
        "investable_assets_estimated": round(investable_portfolio_assets + pending_total, 2),
        "investable_asset_class_values": investable_asset_class_values,
        "investable_asset_class_weights": investable_asset_class_weights,
        "valuation_audit": valuation_audit,
        "configured_total_assets": snapshot.get("total_assets"),
        "total_assets": canonical["total_valued_assets"],
        "snapshot_type": "PortfolioSnapshot", "positions": positions, "holdings": positions,
        "transaction_ledger": transactions, "cost_records": cost_records,
        "pending_valuation_assets": pending,
        "pending_valuation_total": pending_total,
        "unvalued_cost_records": pending_total,
        "unvalued_cost_record_details": pending,
        "total_cost_records_cny": cost_total,
        "total_asset_including_cost_records": household_total_assets_estimated,
        "decision_total_assets": investable_portfolio_assets, "decision_asset_class_totals": investable_asset_class_values,
        "asset_class_totals": canonical["asset_class_values"], "allocation_base_cny": investable_portfolio_assets,
        "precise_market_value": canonical["precise_valued_assets"], "pending_valuation_cost": pending_total,
        "total_book_value": household_total_assets_estimated,
        "valuation_coverage_ratio": coverage,
        "precise_valuation_coverage": coverage,
        "valuation_coverage_pct": round(coverage * 100, 4), "valuation_as_of": valuation_as_of,
        "valuation_status": "COMPLETE" if coverage >= 0.999999 else "PARTIAL", "valuation_engine": "canonical_ledger_first",
        "has_provisional_values": bool(canonical["stale_valued_assets"] or pending),
        "provisional_value_cny": round(canonical["stale_valued_assets"] + pending_total, 2),
        "simulation_assets_cny": 0,
    }
