from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from utils.data_loader import load_config, project_root


ASSET_CLASS_KEYS = ["us_stock", "hk_stock", "cn_stock", "china_bond", "gold", "cash"]
CANONICAL_CATEGORY = {
    "us_stock": "美股",
    "hk_stock": "港股",
    "cn_stock": "A股",
    "china_bond": "债券",
    "gold": "黄金",
    "cash": "现金",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_config(path)


def _security_lookup() -> dict[str, dict[str, Any]]:
    registry = _load_yaml(project_root() / "data" / "security_master.yaml")
    lookup: dict[str, dict[str, Any]] = {}
    for security in registry.get("securities", []) or []:
        canonical = str(security.get("canonical_id", "")).strip()
        keys = {canonical, str(security.get("ticker", "")).strip(), str(security.get("display_name", "")).strip()}
        keys.update(str(alias).strip() for alias in security.get("aliases", []) or [])
        for key in keys:
            if key:
                lookup[key.upper()] = security
    return lookup


def _security_for(holding: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in [
        holding.get("security_code"),
        holding.get("security_name"),
        holding.get("asset_id"),
        holding.get("pricing_proxy"),
    ]:
        text = str(key or "").strip().upper()
        if text and text in lookup:
            return lookup[text]
    return {}


def _snapshot_holding(holding: dict[str, Any], labels: dict[str, str], lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    security = _security_for(holding, lookup)
    asset_key = str(holding.get("asset_class", "")).strip()
    asset_class = labels.get(asset_key) or CANONICAL_CATEGORY.get(asset_key, asset_key)
    value_cny = round(_to_float(holding.get("market_value_cny")))
    original_value = _to_float(holding.get("market_value_original"), value_cny)
    raw_exchange_rate = holding.get("exchange_rate")
    exchange_rate = None if raw_exchange_rate in {None, ""} else _to_float(raw_exchange_rate)
    confirmed_at = str(holding.get("last_confirmed_at") or holding.get("valuation_time") or date.today().isoformat())
    valuation_method = str(
        holding.get("valuation_method")
        or ("manual_override" if holding.get("manual_override") else "user_confirmed_market_value")
    )
    return {
        "snapshot_date": str(holding.get("valuation_time") or date.today().isoformat()),
        "asset_id": holding.get("asset_id", ""),
        "asset_class_key": asset_key,
        "asset_class": asset_class,
        "security_name": holding.get("security_name") or security.get("display_name") or "",
        "security_code": str(holding.get("security_code") or security.get("ticker") or ""),
        "canonical_id": security.get("canonical_id") or holding.get("asset_id", ""),
        "pricing_proxy": holding.get("pricing_proxy") or security.get("pricing_proxy") or holding.get("security_code") or "",
        "market": holding.get("market") or security.get("exchange") or "",
        "currency": holding.get("currency") or security.get("currency") or "CNY",
        "quantity": holding.get("quantity"),
        "unit": holding.get("unit") or "",
        "market_value_original": original_value,
        "market_value_original_currency": holding.get("market_value_original_currency") or holding.get("currency") or "CNY",
        "exchange_rate": exchange_rate,
        "fx_status": "not_applied_user_confirmed_cny" if exchange_rate is None else "applied",
        "market_value_cny": value_cny,
        "data_source": holding.get("data_source") or "user_confirmed",
        "source": holding.get("data_source") or "user_confirmed",
        "last_confirmed_at": confirmed_at,
        "valuation_method": valuation_method,
        "valuation_time": str(holding.get("valuation_time") or date.today().isoformat()),
        "confidence": holding.get("confidence") or "medium",
        "account": holding.get("account") or "",
        "liquidity_status": holding.get("liquidity_status") or "",
        "strategy_bucket": holding.get("strategy_bucket") or security.get("strategy_type") or "",
        "manual_override": bool(holding.get("manual_override", False)),
        "gold_price_cny_per_gram": holding.get("gold_price_cny_per_gram"),
    }


def build_portfolio_snapshot() -> dict[str, Any]:
    root = project_root()
    master_path = root / "data" / "portfolio_master.yaml"
    master = _load_yaml(master_path)
    labels = master.get("asset_class_labels", {}) or {}
    totals = master.get("totals", {}) or {}
    lookup = _security_lookup()
    holdings = [
        _snapshot_holding(item, labels, lookup)
        for item in master.get("holdings", []) or []
        if _to_float(item.get("market_value_cny")) >= 0
    ]
    total_assets = round(_to_float(totals.get("total_assets")))
    class_totals: dict[str, int] = {}
    for holding in holdings:
        category = holding["asset_class"]
        class_totals[category] = class_totals.get(category, 0) + int(holding["market_value_cny"])

    configured_totals = {
        labels.get(key) or CANONICAL_CATEGORY[key]: round(_to_float(totals.get(key)))
        for key in ASSET_CLASS_KEYS
    }

    cash_policy = master.get("cash_policy", {}) or {}
    account_cash = round(_to_float(cash_policy.get("account_total_cash_cny"), configured_totals.get("现金", 0)))
    safety_ratio = _to_float(cash_policy.get("safety_reserve_ratio"), 0.08)
    safety_reserve = round(total_assets * safety_ratio)
    live_grid_cash = round(_to_float(cash_policy.get("live_grid_cash_cny")))
    other_reserved_cash = round(_to_float(cash_policy.get("other_reserved_cash_cny")))
    investable_cash = max(0, account_cash - safety_reserve - live_grid_cash - other_reserved_cash)

    gold_detail_total = sum(int(item["market_value_cny"]) for item in holdings if item["asset_class"] == "黄金")
    snapshot_date = str(master.get("as_of") or date.today().isoformat())
    try:
        holding_age_days = max(0, (date.today() - date.fromisoformat(snapshot_date[:10])).days)
    except ValueError:
        holding_age_days = 9999
    holdings_stale = holding_age_days > 31
    freshness_warning = "持仓市值可能滞后" if holdings_stale else "持仓数据在人工确认有效期内"
    return {
        "snapshot_date": snapshot_date,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(master_path),
        "source": str(master.get("source") or "user_confirmed"),
        "last_confirmed_at": snapshot_date,
        "valuation_method": "user_confirmed_portfolio_snapshot",
        "holding_age_days": holding_age_days,
        "holdings_stale": holdings_stale,
        "freshness_warning": freshness_warning,
        "total_assets": total_assets,
        "asset_class_totals": configured_totals,
        "holding_class_totals": class_totals,
        "holdings": holdings,
        "cash": {
            "account_total_cash_cny": account_cash,
            "cash_safety_reserve_cny": safety_reserve,
            "investable_cash_cny": investable_cash,
            "live_grid_cash_cny": live_grid_cash,
            "paper_grid_cash_cny": 0,
            "other_reserved_cash_cny": other_reserved_cash,
            "unsettled_conditional_cash_cny": round(_to_float(cash_policy.get("unsettled_conditional_cash_cny"))),
            "formula": "可投资现金 = 账户总现金 - 现金安全储备 - 网格实盘现金 - 其他已占用现金",
        },
        "gold": {
            "class_total_cny": configured_totals.get("黄金", 0),
            "detail_total_cny": gold_detail_total,
            "reconciled": abs(gold_detail_total - configured_totals.get("黄金", 0)) < 10,
        },
        "validation_inputs": {
            "configured_totals": configured_totals,
            "class_totals_from_holdings": class_totals,
        },
    }


def portfolio_rows_for_legacy_agents() -> list[dict[str, Any]]:
    snapshot = build_portfolio_snapshot()
    rows: list[dict[str, Any]] = []
    for holding in snapshot["holdings"]:
        rows.append(
            {
                "category": holding["asset_class"],
                "name": holding["security_name"],
                "symbol": holding["security_code"],
                "amount_wan": holding["market_value_cny"] / 10000,
                "currency": holding["currency"],
                "quantity": holding["quantity"],
                "unit": holding["unit"],
                "note": f"来自Portfolio Snapshot；strategy_bucket={holding['strategy_bucket']}",
                "valuation_status": "snapshot",
                "valuation_note": holding["data_source"],
                "price_cny_per_gram": holding.get("gold_price_cny_per_gram"),
                "canonical_id": holding["canonical_id"],
                "pricing_proxy": holding["pricing_proxy"],
                "strategy_bucket": holding["strategy_bucket"],
                "liquidity_status": holding["liquidity_status"],
                "account": holding["account"],
            }
        )
    return rows
