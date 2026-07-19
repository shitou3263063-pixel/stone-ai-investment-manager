from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.decision.v12_1_decision import (
    aggregate_comparable_market_changes,
    compute_dqs,
    enrich_allocation,
    load_strategy,
)
from src.domain.event_assessment import build_event_assessment
from src.macro.macro_calendar import analyze_macro_calendar
from src.portfolio_snapshot import build_portfolio_snapshot
from tests.test_v12_1_stable import _live_market


def test_household_reserve_is_excluded_from_investable_portfolio_denominator() -> None:
    snapshot = build_portfolio_snapshot()

    assert snapshot["household_total_assets"] == snapshot["total_valued_assets"] + snapshot["unvalued_cost_records"]
    assert snapshot["household_safety_reserve"] == 220_000
    assert snapshot["portfolio_cash"] == 21_000
    assert snapshot["investable_asset_class_values"]["现金"] == 21_000
    assert snapshot["investable_portfolio_assets"] == sum(
        snapshot["investable_asset_class_values"].values()
    )
    assert snapshot["household_total_assets"] - snapshot["investable_assets_estimated"] == 220_000


def test_allocation_targets_and_deviations_use_investable_portfolio_only() -> None:
    snapshot = build_portfolio_snapshot()
    allocation = enrich_allocation({}, load_strategy(), snapshot)
    cash = next(row for row in allocation if row["category"] == "现金")

    assert cash["current_amount_yuan"] == 21_000
    assert cash["current_ratio"] == pytest.approx(21_000 / snapshot["investable_portfolio_assets"])
    assert sum(row["current_ratio"] for row in allocation) == pytest.approx(1.0)
    assert sum(row["target_amount_yuan"] for row in allocation) == pytest.approx(
        snapshot["investable_portfolio_assets"], abs=2
    )


def test_correlated_voo_qqq_returns_are_weighted_once_not_summed() -> None:
    live = {
        "items": {
            "VOO": {"comparable_date": "2026-07-17", "price_stage": "OFFICIAL_CLOSE", "change_pct": -3.0},
            "QQQ": {"comparable_date": "2026-07-17", "price_stage": "OFFICIAL_CLOSE", "change_pct": -3.0},
        }
    }

    trend = aggregate_comparable_market_changes(live)

    assert trend["aggregation_method"] == "EXPLICIT_WEIGHTED_AVERAGE"
    assert trend["weighted_change_pct"] == pytest.approx(-3.0)
    assert trend["combined_change_pct"] == pytest.approx(-3.0)
    assert trend["combined_change_pct"] != pytest.approx(-6.0)
    assert trend["broad_market_trend"]["weight"] == pytest.approx(0.70)
    assert trend["growth_style_trend"]["weight"] == pytest.approx(0.30)


def test_ibkr_earnings_is_position_level_not_portfolio_level_event() -> None:
    result = analyze_macro_calendar(
        as_of=datetime(2026, 7, 19, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    position = result["position_level_event_risk"]
    portfolio = result["portfolio_level_event_risk"]
    ibkr = next(event for event in position["events"] if event.get("security_id") == "IBKR")

    assert position["status"] == "HIGH_RISK_EVENT_FOUND"
    assert portfolio["status"] == "CLEAR"
    assert ibkr["release_at"] == "2026-07-21T16:00:00-04:00"
    assert ibkr["source_timezone"] == "America/New_York"
    assert result["has_high_event_next_7_days"] is False
    assert result["calendar_confidence"] == "medium"


def test_released_data_failure_does_not_pollute_future_event_gate() -> None:
    macro = analyze_macro_calendar(
        as_of=datetime(2026, 7, 19, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assessment = build_event_assessment(macro)
    quality = compute_dqs(_live_market(), load_strategy(), macro, build_portfolio_snapshot())
    event_component = next(
        row for row in quality["dqs_results"]["core_dqs"]["breakdown"]
        if row["item"] == "事件状态"
    )

    assert assessment["status"] == "VALID_NO_HIGH_IMPACT_EVENT"
    assert assessment["released_data_issues"]
    assert all(item["missing_fields"] for item in assessment["released_data_issues"])
    assert all(item["data_source"] for item in assessment["released_data_issues"])
    assert all(item["last_success_at"] for item in assessment["released_data_issues"])
    assert all(item["score_deduction_item"] for item in assessment["released_data_issues"])
    assert event_component["reason"] == "可用"
    assert event_component["missing_data"] == []
    assert event_component["data_source"]
    assert event_component["last_success_at"]
    assert event_component["score_impact"] == 0


def test_valuation_audit_fields_control_precise_status_and_rebalance_dqs() -> None:
    snapshot = build_portfolio_snapshot()
    required = {"price", "currency", "fx_rate", "price_as_of", "source", "valuation_status"}

    assert all(required <= position.keys() for position in snapshot["positions"])
    assert all(
        position["precise_valuation"] == (not position["valuation_audit_missing_fields"] and not position.get("pending_reason"))
        for position in snapshot["positions"]
    )

    incomplete = deepcopy(snapshot)
    incomplete["valuation_audit"] = {
        "complete": False,
        "incomplete_positions": [
            {"security_id": "IBKR", "missing_fields": ["price_as_of"], "source": "UNKNOWN"}
        ],
    }
    quality = compute_dqs(_live_market(), load_strategy(), analyze_macro_calendar(), incomplete)
    timeliness = next(
        row for row in quality["dqs_results"]["rebalance_dqs"]["breakdown"]
        if row["item"] == "持仓时效"
    )

    assert timeliness["score"] < timeliness["max"]
    assert "IBKR:price_as_of" in timeliness["missing_data"]
    assert quality["execution_dqs"] == 100
