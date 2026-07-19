from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.decision.issue_registry import build_issue_registry
from src.decision.v12_1_decision import compute_dqs, load_strategy
from src.macro.macro_calendar import analyze_macro_calendar
from src.valuation.valuation_engine import apply_live_valuation
from tests.test_v12_1_stable import _live_market


REPORT_TIME = datetime(2026, 7, 19, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _macro_item(*, value: float | None, previous: float | None, status: str = "ok") -> dict:
    return {
        "series_id": "CPIAUCSL",
        "status": status,
        "data_status": "VALID_LAGGED_BY_DESIGN" if status == "ok" else "DATA_INSUFFICIENT",
        "value": value,
        "previous_value": previous,
        "observation_date": "2026-06-01",
        "source": "fred",
        "fetched_at": "2026-07-19T08:00:00+08:00",
    }


def _portfolio(*, symbol: str = "VOO", currency: str = "CNY", value: float = 130_000) -> dict:
    asset_class = "港股" if symbol == "03033.HK" else "美股"
    quantity = 35_200 if symbol == "03033.HK" else 28
    return {
        "holdings": [
            {
                "asset_id": "position",
                "security_code": symbol,
                "pricing_proxy": symbol,
                "security_name": symbol,
                "asset_class": asset_class,
                "currency": currency,
                "quantity": quantity,
                "market_value_cny": value,
                "valuation_time": "2026-07-11",
                "source": "user_confirmed",
                "strategy_bucket": "core_etf",
            },
            {
                "asset_id": "cash",
                "security_code": "CASH_CNY",
                "security_name": "Cash",
                "asset_class": "现金",
                "currency": "CNY",
                "market_value_cny": 241_000,
                "valuation_time": "2026-07-15",
                "source": "user_confirmed",
            },
        ],
        "asset_class_values": {asset_class: value, "现金": 241_000},
        "cash": {"account_total_cash_cny": 241_000, "cash_safety_reserve_cny": 220_000},
        "investable_cash": 21_000,
        "safety_cash": 220_000,
        "confirmed_transactions": [],
    }


def _quote_market(symbol: str, *, include_price: bool = True, include_fx: bool = True) -> dict:
    items: dict[str, dict] = {}
    if include_price:
        items[symbol] = {
            "status": "ok",
            "close": 4.534 if symbol == "03033.HK" else 683.17,
            "currency": "HKD" if symbol == "03033.HK" else "USD",
            "data_stage": "PREVIOUS_OFFICIAL_CLOSE",
            "market_date": "2026-07-17",
            "observed_at": "2026-07-17T16:00:00-04:00",
            "source": "yfinance",
        }
    if include_fx:
        pair = "HKD/CNY" if symbol == "03033.HK" else "USD/CNY"
        items[pair] = {
            "status": "ok",
            "value": 0.864 if symbol == "03033.HK" else 6.7755,
            "data_stage": "PREVIOUS_OFFICIAL_CLOSE",
            "observed_at": "2026-07-17T16:00:00-04:00",
            "source": "official_fx",
        }
    return {"items": items}


def test_released_cpi_has_actual_and_previous_from_release_data() -> None:
    result = analyze_macro_calendar(
        as_of=REPORT_TIME,
        macro_snapshot={"items": {"CPIAUCSL": _macro_item(value=332.568, previous=333.979)}},
    )
    cpi = next(event for event in result["released_events"] if event["event_name"] == "CPI")
    release = cpi["economic_release_data"]

    assert release["actual_value"] == pytest.approx(332.568)
    assert release["previous_value"] == pytest.approx(333.979)
    assert release["source"] == "fred"
    assert cpi["status"] == "PARTIAL_DATA"


def test_released_event_fetch_failure_is_explicit() -> None:
    result = analyze_macro_calendar(
        as_of=REPORT_TIME,
        macro_snapshot={"items": {"CPIAUCSL": _macro_item(value=None, previous=None, status="error")}},
    )
    cpi = next(event for event in result["released_events"] if event["event_name"] == "CPI")

    assert cpi["status"] == "RELEASED_FETCH_FAILED"
    assert cpi["economic_release_data"]["fetch_attempted"] is True
    assert result["future_event_gate"]["status"] == "CLEAR"


def test_weekend_uses_previous_official_close_as_precise_valuation() -> None:
    snapshot = apply_live_valuation(
        _portfolio(), _quote_market("VOO"), valuation_as_of="2026-07-19T12:00:00+08:00"
    )
    voo = next(row for row in snapshot["positions"] if row["security_id"] == "VOO")

    assert voo["valuation_status"] == "VALUED_PREVIOUS_CLOSE"
    assert voo["precise_valuation"] is True
    assert voo["market_value_cny"] == pytest.approx(28 * 683.17 * 6.7755, abs=0.01)


def test_stale_valuation_separates_precise_from_estimated_total() -> None:
    snapshot = apply_live_valuation(
        _portfolio(), {}, valuation_as_of="2026-07-19T12:00:00+08:00"
    )

    assert snapshot["stale_valued_assets"] == 130_000
    assert snapshot["precise_valued_assets"] != snapshot["household_total_assets_estimated"]
    assert snapshot["valuation_coverage_ratio"] == pytest.approx(241_000 / 371_000)


@pytest.mark.parametrize(("include_price", "include_fx"), [(False, True), (True, False)])
def test_03033_missing_price_or_fx_degrades_to_user_confirmed_value(
    include_price: bool, include_fx: bool,
) -> None:
    snapshot = apply_live_valuation(
        _portfolio(symbol="03033.HK", currency="HKD", value=140_400),
        _quote_market("03033.HK", include_price=include_price, include_fx=include_fx),
        valuation_as_of="2026-07-19T12:00:00+08:00",
    )
    holding = next(row for row in snapshot["positions"] if row["security_id"] == "HK_03033")

    assert holding["valuation_status"] == "STALE_USER_CONFIRMED_VALUE"
    assert holding["market_value_cny"] == 140_400
    assert holding["precise_valuation"] is False
    assert snapshot["stale_valued_assets"] >= 140_400


def test_one_stale_position_reduces_rebalance_score_proportionally_not_to_zero() -> None:
    snapshot = apply_live_valuation(
        _portfolio(), {}, valuation_as_of="2026-07-19T12:00:00+08:00"
    )
    dqs = compute_dqs(_live_market(), load_strategy(), analyze_macro_calendar(), snapshot)
    holding_score = next(
        row["score"] for row in dqs["dqs_results"]["rebalance_dqs"]["breakdown"]
        if row["item"] == "持仓时效"
    )
    valuation_score = next(
        row["score"] for row in dqs["dqs_results"]["opportunity_dqs"]["breakdown"]
        if row["item"] == "valuation_readiness"
    )

    assert holding_score == round(30 * snapshot["precise_valuation_coverage"])
    assert 0 < holding_score < 30
    assert valuation_score == round(15 * snapshot["precise_valuation_coverage"])


def test_soft_warnings_and_hard_blocks_are_disjoint() -> None:
    registry = build_issue_registry(
        {
            "portfolio_snapshot": {
                "pending_valuation_assets": [{"security_code": "VOO", "pending_reason": "MISSING_FX"}]
            },
            "data_quality_snapshot": {},
            "comparability": {},
            "consistency": {"errors": [], "warnings": ["soft consistency warning"]},
        }
    )

    warning_ids = {row["issue_id"] for row in registry["warnings"]}
    blocking_ids = {row["issue_id"] for row in registry["blocking"]}
    assert warning_ids
    assert blocking_ids
    assert warning_ids.isdisjoint(blocking_ids)
