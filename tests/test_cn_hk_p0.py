from __future__ import annotations

from datetime import date

import pytest

from src.data_sources import data_router
from src.data_sources.data_router import (
    INSTRUMENT_METADATA,
    _failed_item,
    _normalize_point,
    _with_symbol,
    build_market_completeness,
    provider_symbol_for,
    validate_market_item,
)
from src.decision.v12_1_decision import build_opportunity_scores, load_strategy
from src.portfolio_snapshot import build_portfolio_snapshot
from utils.data_loader import load_config, project_root


def _valid_item(symbol: str, price: float = 10.0) -> dict:
    provider_symbol = provider_symbol_for(symbol, "yfinance")
    normalized = _normalize_point(
        _with_symbol(
            symbol,
            {
                "close": price,
                "previous_close": price * 0.99,
                "change_pct": 1.01,
                "status": "ok",
                "source": "yfinance",
                "provider_symbol": provider_symbol,
                "proxy_used": False,
                "published_at": date.today().isoformat(),
                "fetched_at": f"{date.today().isoformat()}T08:00:00+08:00",
                "freshness_status": "fresh",
            },
        )
    )
    return validate_market_item(symbol, normalized)


def _allocation() -> list[dict]:
    values = {
        "美股": (385000, -0.16),
        "港股": (272600, -0.02),
        "A股": (266500, -0.01),
        "债券": (1130000, 0.15),
        "黄金": (547000, 0.04),
        "现金": (220000, 0.00),
    }
    return [
        {"category": category, "current_amount_yuan": amount, "deviation_ratio": deviation}
        for category, (amount, deviation) in values.items()
    ]


def test_03033_never_maps_to_3067() -> None:
    assert provider_symbol_for("03033.HK", "yfinance") == "3033.HK"
    assert provider_symbol_for("03033.HK", "finnhub") == "03033.HK"
    assert provider_symbol_for("03033.HK", "yfinance") != "3067.HK"


def test_03033_and_3067_are_distinct_securities() -> None:
    master = load_config(project_root() / "data" / "security_master.yaml")
    rows = {item["canonical_id"]: item for item in master["securities"]}
    assert rows["HK_03033"]["ticker"] == "03033.HK"
    assert "3067.HK" not in rows["HK_03033"]["aliases"]
    assert rows["HK_03067"]["ticker"] == "3067.HK"
    assert rows["HK_03033"]["canonical_id"] != rows["HK_03067"]["canonical_id"]


def test_03033_yfinance_uses_same_security_vendor_code(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    def finnhub_failure(_: str) -> dict:
        raise RuntimeError("not available")

    def yfinance_quote(symbol: str) -> dict:
        requested.append(symbol)
        return {
            "close": 4.5,
            "previous_close": 4.4,
            "change_pct": 2.27,
            "status": "ok",
            "source": "yfinance",
            "published_at": date.today().isoformat(),
            "fetched_at": f"{date.today().isoformat()}T08:00:00+08:00",
        }

    monkeypatch.setattr(data_router.finnhub_client, "get_quote", finnhub_failure)
    monkeypatch.setattr(data_router.yfinance_client, "get_quote", yfinance_quote)
    monkeypatch.setattr(data_router, "write_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(data_router, "read_cache", lambda *args, **kwargs: None)
    item = data_router.get_market_quote("03033.HK")
    assert requested == ["3033.HK"]
    assert item["symbol"] == "03033.HK"
    assert item["provider_symbol"] == "3033.HK"
    assert item["proxy_used"] is False
    assert item["fallback_used"] is True
    assert item["data_status"] == "VALID"


@pytest.mark.parametrize(
    ("symbol", "currency", "timezone"),
    [
        ("002558.SZ", "CNY", "Asia/Shanghai"),
        ("513060.SS", "CNY", "Asia/Shanghai"),
        ("513090.SS", "CNY", "Asia/Shanghai"),
        ("510300.SS", "CNY", "Asia/Shanghai"),
        ("03033.HK", "HKD", "Asia/Hong_Kong"),
    ],
)
def test_market_currency_and_timezone(symbol: str, currency: str, timezone: str) -> None:
    item = _valid_item(symbol)
    assert item["currency"] == currency
    assert item["timezone"] == timezone
    assert item["market_date"] == date.today().isoformat()


def test_missing_data_is_never_zero() -> None:
    item = validate_market_item("513060.SS", _normalize_point(_with_symbol("513060.SS", _failed_item("513060.SS", ["failed"]))))
    assert item["value"] is None
    assert item["close"] is None
    assert item["data_status"] == "DATA_INSUFFICIENT"


def test_proxy_etf_price_is_rejected_for_real_holding() -> None:
    item = _valid_item("03033.HK")
    invalid = validate_market_item("03033.HK", {**item, "provider_symbol": "3067.HK", "proxy_used": True})
    assert invalid["data_status"] == "SYMBOL_MAPPING_ERROR"
    assert invalid["decision_eligible"] is False


def test_missing_fx_is_not_silently_one_to_one() -> None:
    snapshot = build_portfolio_snapshot()
    holding = next(item for item in snapshot["holdings"] if item["security_code"] == "03033.HK")
    assert holding["currency"] == "HKD"
    assert holding["market_value_original_currency"] == "CNY"
    assert holding["exchange_rate"] is None
    assert holding["fx_status"] == "not_applied_user_confirmed_cny"


def test_cn_completeness_below_threshold_restricts_buy() -> None:
    completeness = build_market_completeness({})
    cn = completeness["cn_data_completeness"]
    assert cn["score_pct"] < 60
    assert cn["decision_restricted"] is True


def test_hk_completeness_below_threshold_restricts_buy() -> None:
    completeness = build_market_completeness({})
    hk = completeness["hk_data_completeness"]
    assert hk["score_pct"] < 60
    assert hk["decision_restricted"] is True


def test_low_cn_hk_completeness_blocks_opportunity_buy_language() -> None:
    items = {
        symbol: _valid_item(symbol)
        for symbol in ["510300.SS", "002558.SZ", "03033.HK", "513060.SS", "513090.SS"]
    }
    low_gate = {
        "score_pct": 35.0,
        "confidence": "low",
        "decision_restricted": True,
        "missing_fields": ["market_breadth"],
    }
    live_market = {
        "items": items,
        "macro": {"items": {}},
        "market_completeness": {
            "cn_data_completeness": low_gate,
            "hk_data_completeness": low_gate,
        },
    }
    rows = build_opportunity_scores(_allocation(), live_market, load_strategy())
    target_symbols = {"510300.SS", "002558.SZ", "03033.HK", "513060.SS", "513090.SS"}
    target_rows = [row for row in rows if row["symbol"] in target_symbols]
    assert target_rows
    assert all(row["decision_restricted"] for row in target_rows)
    assert all(row["advice"] not in {"优先加仓", "正常定投", "小额分批"} for row in target_rows)
    assert all(row["scoring_confidence"] == "低" for row in target_rows)


def test_us_symbol_routing_is_unchanged() -> None:
    assert provider_symbol_for("VOO", "yfinance") == "VOO"
    assert INSTRUMENT_METADATA.get("VOO") is None
