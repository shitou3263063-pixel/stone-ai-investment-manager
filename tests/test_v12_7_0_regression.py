from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.analysis.scenario_analysis import calculate_portfolio_stress_scenarios
from src.data_sources.data_router import merge_newer_validated_cn_hk_quotes
from src.data_sources.normalized_market import (
    PRICE_STAGES,
    market_quote_reference,
    normalize_market_quote,
)
from src.decision.v12_1_decision import (
    apply_dqs_to_opportunity,
    build_consistency_checks,
    build_opportunity_scores,
    compute_risk_score,
    enrich_allocation,
    load_strategy,
    scheduled_dca_event_window_policy,
)
from src.grid.grid_engine import build_grid_decision_snapshot
from src.macro.macro_calendar import classify_event_status
from src.portfolio_snapshot import build_portfolio_snapshot
from utils.data_loader import project_root


US_CUTOFF_INTRADAY = datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
US_CUTOFF_AFTER_CLOSE = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)


def _quote(*, cutoff: datetime, finalized: bool = False, source: str = "finnhub") -> dict:
    return normalize_market_quote(
        {
            "symbol": "VOO",
            "formal_name": "Vanguard S&P 500 ETF",
            "market": "US",
            "exchange": "NYSE Arca",
            "currency": "USD",
            "market_timezone": "America/New_York",
            "market_date": "2026-07-15",
            "close": 692.5,
            "previous_close": 690.0,
            "quote_timestamp": "2026-07-15T14:59:00-04:00",
            "retrieved_at": "2026-07-15T19:00:00+00:00",
            "source": source,
            "source_level": 2,
            "status": "ok",
            "daily_bar_finalized": finalized,
        },
        decision_cutoff=cutoff,
    )


def _allocation() -> list[dict]:
    snapshot = build_portfolio_snapshot()
    return enrich_allocation(
        {
            "total_assets_wan": snapshot["total_assets"] / 10000,
            "category_amounts": {
                key: value / 10000 for key, value in snapshot["asset_class_totals"].items()
            },
        },
        load_strategy(),
    )


def test_01_us_intraday_daily_bar_is_intraday() -> None:
    quote = _quote(cutoff=US_CUTOFF_INTRADAY, finalized=False, source="yfinance")
    assert quote["price_stage"] == "INTRADAY"
    assert quote["price_stage"] in PRICE_STAGES


def test_02_after_close_unfinalized_is_not_official() -> None:
    quote = _quote(cutoff=US_CUTOFF_AFTER_CLOSE, finalized=False)
    assert quote["price_stage"] == "AFTER_HOURS_UNFINALIZED"
    assert quote["is_finalized"] is False


def test_03_only_finalized_bar_is_official_close() -> None:
    quote = _quote(cutoff=US_CUTOFF_AFTER_CLOSE, finalized=True)
    assert quote["price_stage"] == "OFFICIAL_CLOSE"
    assert quote["is_finalized"] is True


def test_04_yfinance_midnight_index_is_market_date_only() -> None:
    quote = normalize_market_quote(
        {
            "symbol": "VOO", "market": "US", "market_timezone": "America/New_York",
            "source": "yfinance", "status": "ok", "data_frequency": "daily",
            "daily_index_market_date": "2026-07-15",
            "daily_index_timestamp": "2026-07-15T00:00:00-04:00",
            "published_at": "2026-07-15T00:00:00-04:00",
            "retrieved_at": "2026-07-15T18:00:00+00:00", "close": 692.5,
        },
        decision_cutoff=US_CUTOFF_INTRADAY,
    )
    assert quote["market_date"] == "2026-07-15"
    assert quote["quote_timestamp"] == "2026-07-15T18:00:00+00:00"
    assert any("午夜索引仅用于market_date" in note for note in quote["validation_notes"])


def test_05_data_age_uses_real_quote_timestamp() -> None:
    quote = _quote(cutoff=US_CUTOFF_INTRADAY)
    assert quote["data_age_hours"] == pytest.approx(1 / 60, abs=0.001)


def test_06_missing_reliable_time_has_none_age() -> None:
    quote = normalize_market_quote(
        {"symbol": "VOO", "market": "US", "status": "ok", "source": "unknown", "close": 1},
        decision_cutoff=US_CUTOFF_INTRADAY,
    )
    assert quote["quote_timestamp"] is None
    assert quote["data_age_hours"] is None


def test_07_target_weights_equal_one() -> None:
    assert sum(row["target_ratio"] for row in _allocation()) == pytest.approx(1.0)


def test_08_target_amounts_equal_total_assets() -> None:
    snapshot = build_portfolio_snapshot()
    assert sum(row["target_amount_yuan"] for row in _allocation()) == snapshot["total_assets"]


def test_09_cash_target_and_fixed_floor_are_separate() -> None:
    cash = next(row for row in _allocation() if row["category"] == "现金")
    assert cash["target_ratio"] == pytest.approx(0.08)
    assert cash["cash_safety_floor_yuan"] == 220000
    assert cash["target_amount_yuan"] != cash["cash_safety_floor_yuan"]


def test_10_cash_241k_minus_floor_is_21k() -> None:
    cash = build_portfolio_snapshot()["cash"]
    assert cash["account_total_cash_cny"] == 241000
    assert cash["cash_safety_reserve_cny"] == 220000
    assert cash["investable_cash_cny"] == 21000


def test_11_tlt_is_bond_not_us_equity() -> None:
    tlt = next(row for row in build_portfolio_snapshot()["holdings"] if row["security_code"] == "TLT")
    assert tlt["asset_class"] == "债券"
    assert tlt["allocation_bucket"] == "bonds"


def test_12_voo_trade_amount_counted_once() -> None:
    snapshot = build_portfolio_snapshot()
    trade = next(row for row in snapshot["confirmed_transactions"] if row["symbol"] == "VOO")
    assert trade["invested_amount_cny"] == 9000
    assert trade["order_type"] == "base_dca"
    assert trade["asset_migration_attribute"] == "bond_to_equity"
    assert sum(row["invested_amount_cny"] for row in snapshot["confirmed_transactions"] if row["id"] == trade["id"]) == 9000


def test_13_pending_valuation_does_not_increase_voo_value() -> None:
    holdings = build_portfolio_snapshot()["holdings"]
    original = next(row for row in holdings if row["security_code"] == "VOO")
    pending = next(row for row in holdings if row["security_code"] == "VOO_PENDING_20260715")
    assert original["market_value_cny"] == 130000
    assert pending["valuation_status"] == "trade_reconciled_valuation_fx_pending"
    assert pending["actual_quantity"] == pytest.approx(2.166)
    assert pending["actual_fx_rate"] is None
    assert pending["fee"] == 0


def test_14_released_event_has_value_or_missing_marker() -> None:
    event = {
        "release_at_utc": "2026-07-15T12:30:00+00:00",
        "actual_value": None,
        "release_result_confirmed": False,
    }
    assert classify_event_status(event, datetime(2026, 7, 15, 13, tzinfo=timezone.utc)) == "RELEASED_DATA_MISSING"


def test_15_zero_qqq_holding_action_not_applicable() -> None:
    rows = apply_dqs_to_opportunity(
        [{"name": "QQQ", "current_holding_yuan": 0, "asset_type": "growth_etf", "advice": "小额分批", "final_action": "小额分批"}],
        {"score": 60, "use_cases": {"opportunity_add": {"allowed": False, "score": 60}}},
    )
    assert rows[0]["current_holding_action"] == "不适用"


def test_16_no_trade_permission_has_no_buy_action() -> None:
    row = apply_dqs_to_opportunity(
        [{"name": "QQQ", "current_holding_yuan": 0, "asset_type": "growth_etf", "advice": "小额分批", "final_action": "建议加仓"}],
        {"score": 60, "use_cases": {"opportunity_add": {"allowed": False, "score": 60}}},
    )[0]
    assert row["today_trade_permission"] is False
    assert not any(word in row["final_action"] for word in ("买入", "加仓", "小额分批"))


def test_17_daily_and_scheduled_review_are_separate() -> None:
    assert scheduled_dca_event_window_policy(already_executed=False, in_event_window=True) == "PAUSE_AND_REVIEW_BEFORE_EXECUTION"
    assert scheduled_dca_event_window_policy(already_executed=True, in_event_window=True) == "PRE_AUTHORIZED_EXECUTED_NO_RETROACTIVE_RECLASSIFICATION"


def test_18_simulated_grid_cash_excluded_from_real_assets() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["cash"]["paper_grid_cash_cny"] == 0
    assert snapshot["total_assets"] == sum(snapshot["asset_class_totals"].values())


def test_19_newer_valid_cn_hk_quote_beats_old_quote() -> None:
    old = normalize_market_quote(
        {
            "symbol": "510300.SS", "market": "CN", "exchange": "SSE", "currency": "CNY",
            "market_timezone": "Asia/Shanghai", "market_date": "2026-07-14", "close": 4.1,
            "status": "ok", "source": "yfinance", "source_level": 3,
            "retrieved_at": "2026-07-15T08:00:00+00:00", "data_session": "previous_close",
        }, decision_cutoff=datetime(2026, 7, 15, 8, 1, tzinfo=timezone.utc),
    )
    p1a = {"akshare": {"market_references": {"510300.SS": {
        "status": "ok", "scoring_eligible": True, "freshness": "fresh", "market_date": "2026-07-15",
        "metrics": {"close": 4.2}, "currency": "CNY", "source_level": 3,
        "underlying_provider": "eastmoney", "fetched_at": "2026-07-15T15:05:00+08:00",
    }}}}
    merged = merge_newer_validated_cn_hk_quotes(
        {"510300.SS": old}, p1a,
        decision_cutoff=datetime(2026, 7, 15, 7, 10, tzinfo=timezone.utc),
    )
    assert merged["510300.SS"]["current_price"] == 4.2
    assert merged["510300.SS"]["market_date"] == "2026-07-15"
    assert merged["510300.SS"]["promoted_from_p1a"] is True


def test_20_risk_opportunity_grid_share_quote_contract() -> None:
    voo = _quote(cutoff=US_CUTOFF_AFTER_CLOSE, finalized=True)
    qqq = {**voo, "symbol": "QQQ", "formal_name": "Invesco QQQ", "current_price": 610.0, "close": 610.0}
    live = {"items": {"VOO": voo, "QQQ": qqq}, "macro": {}}
    dqs = {"score": 90, "market_coverage": 1.0, "mode_label": "精确", "components": [], "stale_metrics": [], "missing_metrics": [], "transaction_reconciliation": []}
    risk = compute_risk_score(live, {}, dqs, load_strategy())
    opp = next(row for row in build_opportunity_scores(_allocation(), live, load_strategy()) if row["symbol"] == "VOO")
    grid = build_grid_decision_snapshot(live)
    expected = market_quote_reference(voo, "VOO")
    assert risk["market_quote_contract"]["VOO"] == expected
    assert opp["market_quote_ref"] == expected
    assert {key: grid["decision_quotes"]["VOO"][key] for key in expected} == expected


def test_21_blocking_error_forces_fail() -> None:
    result = build_consistency_checks({
        "generated_at": "2026-07-15T12:00:00+08:00",
        "data_cutoff": "2026-07-15T12:00:00+08:00",
        "normalized_market_quotes": {"VOO": {"price_stage": "MADE_UP_STAGE"}},
        "budget": {}, "dqs": {}, "portfolio_snapshot": {}, "events": [], "opportunity": [],
    })
    assert result["status"] == "FAIL"
    assert any("未知行情阶段" in error for error in result["errors"])


def test_22_single_main_entrypoint_and_historical_suite_preserved() -> None:
    root = project_root()
    main_text = (root / "main.py").read_text(encoding="utf-8")
    assert "from src.app import main" in main_text
    assert "raise SystemExit(main())" in main_text
    assert len(list((root / "tests").glob("test_*.py"))) >= 20


def test_23_extreme_stress_is_static_and_can_exceed_25pct() -> None:
    allocation = _allocation()
    results = calculate_portfolio_stress_scenarios(allocation, load_strategy()["scenario_stress"])
    crisis = next(row for row in results if row["key"] == "global_liquidity_crisis")
    assert crisis["portfolio_return"] < -0.25
    assert crisis["long_term_allocation_review_required"] is True
    assert "不直接形成自动交易指令" in crisis["note"]


def test_24_scenario_uses_remaining_cash_not_full_month_cap() -> None:
    from src.decision.v12_1_decision import build_scenarios

    scenarios = build_scenarios(
        {
            "conditional_bond_to_equity_month_yuan": 30000,
            "bond_to_equity_remaining_real_cash_yuan": 21000,
            "investable_cash_yuan": 21000,
            "month_confirmed_yuan": 9000,
        },
        [],
        load_strategy(),
    )
    assert "剩余专项现金21000元" in scenarios[0]["amount"]
    assert "条件性上限30000元" not in scenarios[0]["amount"]
