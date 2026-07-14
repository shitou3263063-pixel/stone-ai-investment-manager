from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.data_sources.data_router import _normalize_point
from src.data_sources.time_normalization import TIMEZONE_UNKNOWN, calculate_age_hours, normalize_to_utc
from src.grid.budget_manager import build_grid_budget
from src.grid.grid_engine import build_grid_decision_snapshot, evaluate_symbol, load_smart_grid_config
from src.grid.validator import _event_within_48h
from src.macro.macro_calendar import (
    analyze_macro_calendar,
    classify_event_status,
    get_upcoming_high_risk_events,
    load_macro_events,
)
from src.reports.grid_report import generate_grid_daily_section


def _event(name: str) -> dict:
    return next(item for item in load_macro_events() if item["event_name"] == name)


def _quote(*, price: float, observed: str, market_date: str, session: str) -> dict:
    return {
        "close": price,
        "status": "ok",
        "source": "alpha_vantage",
        "source_timezone": "America/New_York",
        "observed_at": observed,
        "observed_at_utc": normalize_to_utc(observed, source_timezone="America/New_York").isoformat(),
        "market_date": market_date,
        "comparable_date": market_date,
        "data_session": session,
        "stale": False,
    }


def _decision() -> dict:
    return {
        "date": "2026-07-14",
        "generated_at": "2026-07-14T14:51:00-04:00",
        "portfolio_value_yuan": 2_821_100,
        "dqs": {"score": 90, "mode": "exact", "mode_label": "允许具体金额"},
        "budget": {"confirmed_cash_available_yuan": 0},
        "allocation": [{"category": "美股", "status": "严重低配"}],
        "events": load_macro_events(),
    }


def test_released_event_is_not_upcoming_or_grid_gate() -> None:
    report_time = datetime(2026, 7, 14, 14, 51, tzinfo=ZoneInfo("America/New_York"))
    cpi = _event("CPI")
    assert classify_event_status(cpi, report_time) == "RELEASED"
    selected = get_upcoming_high_risk_events(report_time, hours=48, events=[cpi])
    assert selected == []
    assert _event_within_48h([cpi], report_time) is False


def test_future_event_is_upcoming_and_enters_48_hour_window() -> None:
    report_time = datetime(2026, 7, 14, 14, 51, tzinfo=ZoneInfo("America/New_York"))
    ppi = _event("PPI")
    assert classify_event_status(ppi, report_time) == "UPCOMING"
    assert get_upcoming_high_risk_events(report_time, hours=48, events=[ppi]) == [{**ppi, "status": "UPCOMING"}]


def test_event_at_report_time_is_released() -> None:
    event = _event("PPI")
    report_time = datetime(2026, 7, 15, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    assert classify_event_status(event, report_time) == "RELEASED"


def test_cross_timezone_event_times_are_equal() -> None:
    shanghai = normalize_to_utc("2026-07-15T20:30:00+08:00")
    new_york = normalize_to_utc("2026-07-15T08:30:00-04:00")
    utc = normalize_to_utc("2026-07-15T12:30:00+00:00")
    assert shanghai == new_york == utc


def test_naive_datetime_requires_declared_source_timezone() -> None:
    with pytest.raises(ValueError, match=TIMEZONE_UNKNOWN):
        normalize_to_utc(datetime(2026, 7, 14, 18, 48, 40))
    with pytest.raises(ValueError, match=TIMEZONE_UNKNOWN):
        calculate_age_hours(datetime(2026, 7, 14, 18, 48), datetime(2026, 7, 14, 18, 51))


def test_cboe_protocol_timestamp_does_not_gain_eight_hours() -> None:
    point = _normalize_point(
        {
            "symbol": "^VIX",
            "close": 18.0,
            "status": "ok",
            "source": "cboe_official",
            "published_at": "2026-07-14 18:48:40",
            "fetched_at": "2026-07-15T02:51:15+08:00",
            "data_session": "intraday_delayed",
            "market_date": "2026-07-14",
        }
    )
    assert point["source_timezone"] == "UTC"
    assert point["observed_at_utc"] == "2026-07-14T18:48:40+00:00"
    assert point["age_hours"] == 0.0


def test_mixed_market_phases_block_grid_signal_and_amount() -> None:
    live = {
        "items": {
            "VOO": _quote(price=500, observed="2026-07-13T16:00:00-04:00", market_date="2026-07-13", session="previous_close"),
            "QQQ": _quote(price=480, observed="2026-07-14T14:45:00-04:00", market_date="2026-07-14", session="intraday_delayed"),
            "^VIX": {"close": 18, "status": "ok"},
        }
    }
    snapshot = build_grid_decision_snapshot(live, max_gap_minutes=30)
    assert snapshot["snapshot_comparable"] is False
    config = load_smart_grid_config()
    decision = _decision()
    result = evaluate_symbol(
        symbol="VOO",
        symbol_cfg=config["smart_grid"]["symbols"]["VOO"],
        state_payload={"symbol": "VOO", "anchor_price": 500, "next_buy_price": 485, "next_sell_price": 515},
        decision=decision,
        portfolio_result={"total_assets_wan": 282.11, "category_amounts": {"美股": 38.5}},
        live_market_result=live,
        config=config,
        grid_budget=build_grid_budget(decision, config),
        quantities={"VOO": 28},
        decision_snapshot=snapshot,
    )
    assert result["signal"]["raw_signal"] == "DATA_NOT_COMPARABLE"
    assert result["signal"]["amount_yuan"] == 0
    report = generate_grid_daily_section(
        {
            "enabled": True,
            "paper_mode": True,
            "summary": "模拟",
            "decision_snapshot": snapshot,
            "grid_budget": build_grid_budget(decision, config),
            "symbols": {"VOO": result},
            "approved_count": 0,
            "candidate_count": 0,
            "today_total_advice_yuan": 0,
            "applied_manual_trades": [],
        }
    )
    assert "暂不计算（历史参数仅供参考）" in report
    assert "DATA_NOT_COMPARABLE" in report


def test_same_market_snapshot_is_comparable() -> None:
    live = {
        "items": {
            "VOO": _quote(price=500, observed="2026-07-14T16:00:00-04:00", market_date="2026-07-14", session="official_close"),
            "QQQ": _quote(price=480, observed="2026-07-14T16:05:00-04:00", market_date="2026-07-14", session="official_close"),
        }
    }
    snapshot = build_grid_decision_snapshot(live, max_gap_minutes=30)
    assert snapshot["snapshot_comparable"] is True
    assert snapshot["actual_gap_minutes"] == 5.0


def test_next_review_is_fifteen_minutes_after_next_upcoming_event() -> None:
    report_time = datetime(2026, 7, 14, 14, 51, tzinfo=ZoneInfo("America/New_York"))
    result = analyze_macro_calendar(as_of=report_time)
    assert next(item for item in result["events"] if item["event_name"] == "CPI")["status"] == "RELEASED"
    assert next(item for item in result["events"] if item["event_name"] == "PPI")["status"] == "UPCOMING"
    assert result["next_review_date"] == "2026-07-15T20:45:00+08:00"
    assert "PPI公布后15分钟" in result["next_review_reason"]
