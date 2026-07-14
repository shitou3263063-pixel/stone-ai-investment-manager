from __future__ import annotations

from datetime import date

from src.ai.openai_advisor import generate_openai_advice
from src.data_sources import fred_client
from src.decision.v12_1_decision import (
    aggregate_comparable_market_changes,
    build_consistency_checks,
    build_migration_plan,
    compute_dqs,
    load_strategy,
)
from src.grid.validator import _event_within_48h
from src.macro.macro_calendar import analyze_macro_calendar, get_upcoming_high_risk_events, load_macro_events


def _event(name: str) -> dict:
    return next(item for item in load_macro_events() if item["event_name"] == name)


def _point(value: float, *, day: str = "2026-07-10", session: str = "official_close", source: str = "alpha_vantage") -> dict:
    return {
        "close": value,
        "value": value,
        "status": "ok",
        "source": source,
        "source_level": 2 if source != "fred" else 1,
        "published_at": f"{day}T16:00:00-04:00",
        "observed_at": f"{day}T16:00:00-04:00",
        "fetched_at": "2026-07-13T08:50:00+08:00",
        "data_session": session,
        "freshness_status": "fresh",
        "comparable_date": day,
        "market_timezone": "America/New_York",
        "change_pct": -0.5,
    }


def _complete_live() -> dict:
    market_names = [
        "VOO", "QQQ", "TLT", "GLD", "^VIX", "03033.HK", "510300.SS", "002558.SZ",
        "513060.SS", "513090.SS", "DX-Y.NYB",
    ]
    macro_names = ["DGS10", "CPIAUCSL", "UNRATE", "GDP"]
    indicators = [
        {"name": name, "status": "not_connected", "data_category": "enhancement_data"}
        for name in ["Put/Call Ratio", "市场宽度", "ETF资金流", "AAII情绪"]
    ]
    return {
        "items": {name: _point(100.0) for name in market_names},
        "macro": {
            "items": {
                name: {
                    **_point(4.5, day="2026-07-09", session="official_lagged_macro", source="fred"),
                    "series_id": name,
                }
                for name in macro_names
            }
        },
        "market_context_status": {"indicators": indicators},
    }


def _consistent_decision(*, comparable: bool = True) -> dict:
    events = load_macro_events()
    as_of = date(2026, 7, 13)
    high_48 = get_upcoming_high_risk_events(as_of, hours=48, events=events)
    high_7 = get_upcoming_high_risk_events(as_of, days=7, events=events)
    return {
        "date": as_of.isoformat(),
        "generated_at": "2026-07-13T08:50:00+08:00",
        "next_review_date": "2026-07-14T08:30:00+08:00",
        "events": events,
        "macro_event_high_next_48_hours": bool(high_48),
        "macro_event_high_next_7_days": bool(high_7),
        "budget": {
            "account_total_cash_yuan": 220000,
            "cash_safety_reserve_yuan": 225688,
            "live_grid_cash_yuan": 0,
            "other_reserved_cash_yuan": 0,
            "investable_cash_yuan": 0,
            "today_total_yuan": 0,
            "week_confirmed_yuan": 0,
            "month_confirmed_yuan": 0,
            "conditional_bond_to_equity_month_yuan": 30000,
            "approved_bond_to_equity_month_yuan": 0,
            "actual_bond_cash_arrived_yuan": 0,
            "paper_grid_cash_yuan": 0,
        },
        "portfolio_snapshot": {
            "cash": {
                "account_total_cash_cny": 220000,
                "cash_safety_reserve_cny": 225688,
                "live_grid_cash_cny": 0,
                "other_reserved_cash_cny": 0,
            }
        },
        "dqs": {
            "mode": "safe",
            "required_core_data": {"missing_count": 0, "missing_items": []},
            "enhancement_data": {"missing_count": 4, "missing_items": ["Put/Call Ratio", "市场宽度", "ETF资金流", "AAII情绪"]},
            "stale_metrics": [],
        },
        "risk": {
            "market_time_consistency": {"comparable": comparable},
            "components": [{"item": "趋势", "basis": "行情日期和交易口径一致。" if comparable else "行情时点不一致，暂不计算指数当日合计变化。"}],
        },
        "market_table": [],
        "grid": {"enabled": False, "paper_mode": True, "live_advice_enabled": False, "grid_budget": {"live_available_yuan": 0}},
        "ai": {"openai_status": "disabled", "enabled": False, "called": False, "call_failed": False, "fallback_occurred": False},
        "migration_plan": {"theoretical_transfer_yuan": 424725, "monthly_cap_yuan": 30000, "theoretical_full_months": 15},
        "today_trade": False,
    }


def test_bls_cpi_date_and_timezone() -> None:
    cpi = _event("CPI")
    assert cpi["reference_period"] == "2026-06"
    assert cpi["release_at"] == "2026-07-14T08:30:00-04:00"
    assert cpi["release_at_report_timezone"] == "2026-07-14T20:30:00+08:00"
    assert cpi["source_timezone"] == "America/New_York"


def test_bls_ppi_date_and_timezone() -> None:
    ppi = _event("PPI")
    assert ppi["reference_period"] == "2026-06"
    assert ppi["release_at"] == "2026-07-15T08:30:00-04:00"
    assert ppi["release_at_report_timezone"] == "2026-07-15T20:30:00+08:00"


def test_same_event_source_used_by_risk_and_grid() -> None:
    events = load_macro_events()
    assert _event_within_48h(events, date(2026, 7, 13)) == bool(
        get_upcoming_high_risk_events(date(2026, 7, 13), hours=48, events=events)
    )


def test_high_risk_event_not_reported_as_none() -> None:
    result = analyze_macro_calendar(today=date(2026, 7, 13))
    assert result["has_high_event_next_7_days"] is True
    assert "暂无已核验高等级" not in result["reminder"]


def test_required_and_enhancement_missing_data_are_separated() -> None:
    dqs = compute_dqs(_complete_live(), load_strategy(), {"calendar_confidence": "high"})
    assert dqs["required_core_missing_count"] == 0
    assert dqs["enhancement_missing_count"] == 4
    assert dqs["enhancement_missing_items"] == ["Put/Call Ratio", "市场宽度", "ETF资金流", "AAII情绪"]


def test_dqs_missing_count_matches_status_table() -> None:
    dqs = compute_dqs(_complete_live(), load_strategy(), {"calendar_confidence": "high"})
    assert dqs["enhancement_data"]["missing_count"] == len(dqs["enhancement_data"]["missing_items"])
    assert dqs["required_core_data"]["missing_count"] == len(dqs["required_core_data"]["missing_items"])


def test_mixed_market_dates_not_aggregated() -> None:
    live = {"items": {"VOO": _point(500, day="2026-07-10", session="previous_close"), "QQQ": _point(480, day="2026-07-13", session="intraday_delayed")}}
    result = aggregate_comparable_market_changes(live)
    assert result["combined_change_pct"] is None
    assert result["explanation"] == "行情时点不一致，暂不计算指数当日合计变化"


def test_fred_data_marked_official_lagged(monkeypatch) -> None:
    monkeypatch.setattr(fred_client, "_get_json", lambda *_: {"observations": [{"date": "2026-07-09", "value": "4.54"}, {"date": "2026-07-08", "value": "4.50"}]})
    point = fred_client.get_series_latest("DGS10")
    assert point["data_session"] == "official_lagged_macro"
    assert point["comparable_date"] == "2026-07-09"
    assert point["observed_at"].startswith("2026-07-09")


def test_zero_investable_cash_blocks_real_trade() -> None:
    decision = _consistent_decision()
    decision["budget"]["today_total_yuan"] = 1000
    result = build_consistency_checks(decision)
    assert result["status"] == "FAIL"
    assert any("可投资现金为0" in error for error in result["errors"])


def test_simulation_cash_not_counted_as_real_cash() -> None:
    decision = _consistent_decision()
    decision["budget"]["paper_grid_cash_yuan"] = 100000
    result = build_consistency_checks(decision)
    assert result["status"] == "PASS"
    assert decision["budget"]["investable_cash_yuan"] == 0


def test_disabled_openai_is_not_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_ENABLED", "false")
    advice = generate_openai_advice({})
    assert advice["openai_status"] == "disabled"
    assert advice["called"] is False
    assert advice["call_failed"] is False
    assert advice["fallback_occurred"] is False


def test_bond_migration_duration_is_15_months() -> None:
    allocation = [{"category": "债券", "current_amount_yuan": 1130000, "target_amount_yuan": 705275}]
    budget = {"conditional_bond_to_equity_month_yuan": 30000}
    result = build_migration_plan(allocation, budget)
    assert result["theoretical_transfer_yuan"] == 424725
    assert result["theoretical_full_months"] == 15
    assert result["twelve_month_transfer_yuan"] == 360000
    assert result["remaining_after_12_months_yuan"] == 64725


def test_consistency_validator_returns_warn_on_timestamp_conflict() -> None:
    assert build_consistency_checks(_consistent_decision(comparable=False))["status"] == "WARN"


def test_consistency_validator_passes_after_all_conflicts_fixed() -> None:
    assert build_consistency_checks(_consistent_decision(comparable=True))["status"] == "PASS"
