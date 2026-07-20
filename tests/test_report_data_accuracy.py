from unittest.mock import patch

from src.decision.v12_1_decision import build_report_metadata
from src.domain.event_assessment import build_event_assessment
from src.macro.macro_calendar import analyze_macro_calendar
from src.reports.bundle_report import render_daily_report
from src.valuation.valuation_engine import apply_live_valuation


def test_us_report_uses_new_york_business_date() -> None:
    with patch.dict("os.environ", {"REPORT_INSTANCE_ID": "US_PREOPEN"}, clear=False):
        metadata = build_report_metadata(
            generated_at="2026-07-21T00:31:43+08:00",
            decision_cutoff_at="2026-07-21T00:31:43+08:00",
            transactions=[],
        )
    assert metadata["report_business_date"] == "2026-07-20"
    assert metadata["report_timezone"] == "America/New_York"


def test_cn_report_uses_shanghai_business_date() -> None:
    with patch.dict("os.environ", {"REPORT_INSTANCE_ID": "CN_PREOPEN"}, clear=False):
        metadata = build_report_metadata(
            generated_at="2026-07-21T00:31:43+08:00",
            decision_cutoff_at="2026-07-21T00:31:43+08:00",
            transactions=[],
        )
    assert metadata["report_business_date"] == "2026-07-21"
    assert metadata["report_timezone"] == "Asia/Shanghai"


def test_unverified_event_coverage_cannot_pass_gate() -> None:
    assessment = build_event_assessment(
        {
            "event_calendar_data_status": "VALID",
            "events": [],
            "calendar_missing_items": [],
            "verified_event_coverage": False,
            "has_high_event_next_7_days": False,
        }
    )
    assert assessment["status"] == "DATA_INSUFFICIENT"
    assert assessment["event_gate_passed"] is False


def test_verified_calendar_exposes_success_timestamp() -> None:
    macro = analyze_macro_calendar()
    assert macro["verified_event_coverage"] is True
    assert macro["last_success_at"]


def test_stale_user_confirmed_value_is_not_precise() -> None:
    base = {
        "holdings": [
            {
                "security_code": "VOO",
                "asset_class": "美股",
                "currency": "USD",
                "quantity": 10,
                "market_value_cny": 33000,
                "valuation_time": "2026-07-19",
                "source": "user_confirmed",
                "strategy_bucket": "core_etf",
            }
        ],
        "asset_class_values": {"美股": 33000},
        "confirmed_transactions": [],
        "investable_cash": 0,
        "safety_cash": 0,
    }
    result = apply_live_valuation(base, {}, valuation_as_of="2026-07-20T20:00:00+08:00")
    position = result["positions"][0]
    assert position["valuation_status"] == "STALE_USER_CONFIRMED_VALUE"
    assert position["precise_valuation"] is False
    assert result["precise_valued_assets"] == 0
    assert result["stale_valued_assets"] == 33000
