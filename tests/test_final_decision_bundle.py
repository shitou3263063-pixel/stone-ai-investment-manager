from __future__ import annotations

from copy import deepcopy

from src.decision.permission_engine import build_scenario_decisions
from src.domain.dqs_result import build_dqs_results
from src.domain.event_assessment import build_event_assessment
from src.domain.final_decision_bundle import build_final_decision_bundle, validate_final_decision_bundle
from src.domain.market_snapshot import build_market_snapshot
from src.grid.grid_engine import build_grid_decision_snapshot
from src.reports.bundle_report import render_daily_report
from src.valuation.valuation_engine import apply_live_valuation


def _fixture_bundle() -> dict:
    live = {
        "items": {
            "VOO": {"status": "ok", "close": 683.17, "currency": "unknown", "data_stage": "PREVIOUS_OFFICIAL_CLOSE", "is_finalized": False, "market_date": "2026-07-17", "quote_timestamp": "2026-07-17T20:00:00+00:00"},
            "QQQ": {"status": "ok", "close": 610.0, "currency": "USD", "data_stage": "PREVIOUS_OFFICIAL_CLOSE", "is_finalized": True, "market_date": "2026-07-17"},
            "USD/CNY": {"status": "ok", "close": 7.18, "data_stage": "PREVIOUS_OFFICIAL_CLOSE", "market_date": "2026-07-17"},
        },
        "macro": {"CPIAUCSL": {"status": "VALID_LAGGED_BY_DESIGN", "value": 1, "data_stage": "VALID_LAGGED_BY_DESIGN", "market_date": "2026-06-01"}},
    }
    raw = {
        "holdings": [
            {"asset_id": "voo", "canonical_id": "VOO", "security_code": "VOO", "asset_class": "美股", "currency": "CNY", "quantity": 28, "market_value_cny": 130000, "last_confirmed_at": "2026-07-11"},
            {"asset_id": "voo_cost", "reference_symbol": "VOO", "asset_class": "美股", "market_value_cny": 9000, "additional_cost_cny": 9000, "valuation_method": "confirmed_cost_pending_valuation"},
            {"asset_id": "cash", "security_code": "CASH_CNY", "asset_class": "现金", "currency": "CNY", "market_value_cny": 241000},
        ],
        "asset_class_values": {"美股": 130000, "现金": 241000},
        "cash": {"account_total_cash_cny": 241000, "cash_safety_reserve_cny": 220000, "investable_cash_cny": 21000},
        "safety_cash": 220000, "investable_cash": 21000,
        "confirmed_transactions": [{"id": "VOO-1", "status": "executed", "symbol": "VOO", "action": "BUY", "trade_date": "2026-07-15", "trade_datetime": "2026-07-15T10:24:00-04:00", "quantity": 2.166, "fee": 0, "trade_currency": "USD", "funding_currency": "USD", "trade_amount_usd": 1499.955, "fx_status": "NOT_APPLICABLE_USD_CASH"}],
    }
    portfolio = apply_live_valuation(raw, live, valuation_as_of="2026-07-19T08:00:00+08:00")
    dqs = build_dqs_results({
        "core_dqs": [{"item": "core", "score": 75}],
        "opportunity_dqs": [{"item": "market", "score": 60}, {"item": "optional data", "score": 10}],
        "execution_dqs": [{"item": "ledger", "score": 100}],
        "rebalance_dqs": [{"item": "portfolio", "score": 80}],
        "grid_dqs": [{"item": "VOO close", "score": 50}, {"item": "QQQ close", "score": 50}],
    })
    event = build_event_assessment({"event_calendar_data_status": "UNAVAILABLE"})
    scenarios = build_scenario_decisions(
        dqs_results=dqs,
        dqs_thresholds={"scheduled_dca": 65, "scheduled_dca_normal": 75, "opportunity_add": 85, "strategic_rebalance": 75, "grid": 85, "risk_monitoring": 1, "transaction_reconciliation": 100},
        budget={"is_dca_day": False, "confirmed_cash_available_yuan": 21000}, risk={"score": 20},
        event_assessment=event,
        comparability={"core_decision_comparability": "COMPARABLE", "cross_asset_comparability": "DATA_NOT_COMPARABLE", "grid_snapshot_comparability": "COMPARABLE"},
        today_trade=False,
    )
    market = build_market_snapshot(live, decision_cutoff_at="2026-07-19T08:00:00+08:00")
    decision = {
        "allocation": [], "risk": {"score": 20},
        "comparability": {"core_decision_comparability": "COMPARABLE", "cross_asset_comparability": "DATA_NOT_COMPARABLE", "grid_snapshot_comparability": "COMPARABLE"},
        "report_metadata": {"report_business_date": "2026-07-19", "decision_cutoff_at": "2026-07-19T08:00:00+08:00", "actual_trade_date": "2026-07-15", "report_run_mode": "RERUN"},
    }
    return build_final_decision_bundle(
        product_version="Stone AI Investment Manager Pro V12.7.1 Final Freeze", patch_level="root_fix_1",
        market_snapshot=market, portfolio_snapshot=portfolio, dqs_results=dqs,
        event_assessment=event, scenario_context=scenarios, decision=decision,
        issue_registry={"issues": [], "warnings": []}, consistency={"warnings": []},
        grid={"mode": "SIMULATION_ONLY", "real_trade": False},
    )


def test_dqs_total_equals_breakdown_sum() -> None:
    bundle = _fixture_bundle()
    assert all(result["total"] == sum(row["score"] for row in result["breakdown"]) for result in bundle["dqs_results"].values())


def test_report_uses_single_dqs_result() -> None:
    bundle = _fixture_bundle()
    report = render_daily_report(bundle)
    assert f"core_dqs: **{bundle['dqs_results']['core_dqs']['total']}**" in report


def test_positions_unique_by_security_id() -> None:
    positions = _fixture_bundle()["portfolio_snapshot"]["positions"]
    assert len(positions) == len({row["security_id"] for row in positions})


def test_trade_cost_not_rendered_as_position() -> None:
    bundle = _fixture_bundle()
    assert all(not row.get("is_cost_record") for row in bundle["portfolio_snapshot"]["positions"])
    assert "voo_cost" not in render_daily_report(bundle)


def test_voo_trade_merged_into_single_position() -> None:
    rows = [row for row in _fixture_bundle()["portfolio_snapshot"]["positions"] if row["security_id"] == "VOO"]
    assert len(rows) == 1 and rows[0]["quantity"] == 30.166


def test_event_insufficient_cannot_silently_pass() -> None:
    bundle = _fixture_bundle()
    assert bundle["event_assessment"]["status"] == "DATA_INSUFFICIENT"
    assert bundle["event_assessment"]["event_gate_passed"] is False


def test_released_event_without_actual_data_does_not_pollute_future_gate() -> None:
    assessment = build_event_assessment({
        "event_calendar_data_status": "VALID",
        "as_of": "2026-07-19T00:00:00+08:00",
        "events": [{"status": "RELEASED_FETCH_FAILED", "release_at": "2026-07-15T08:30:00-04:00"}],
    })
    assert assessment["status"] == "VALID_NO_HIGH_IMPACT_EVENT"
    assert assessment["event_gate_passed"] is True
    assert assessment["released_data_issues"]


def test_deny_requires_rejection_reason() -> None:
    for row in _fixture_bundle()["scenario_decisions"]:
        if row["final_permission"] == "DENY":
            assert row["rejection_reasons"]


def test_all_permission_tables_use_same_scenario_decision() -> None:
    bundle = _fixture_bundle()
    report = render_daily_report(bundle)
    for row in bundle["scenario_decisions"]:
        assert row["final_permission"] in report


def test_weekend_close_is_not_stale_or_non_comparable() -> None:
    bundle = _fixture_bundle()
    voo = bundle["market_snapshot"]["market"]["VOO"]
    assert voo["market_state"] == "MARKET_CLOSED"
    assert voo["freshness_state"] == "VALID"
    assert voo["comparability_state"] == "NOT_EVALUATED"
    grid_snapshot = build_grid_decision_snapshot({
        "items": {
            symbol: {
                "status": "ok", "close": price, "stale": False,
                "data_stage": "PREVIOUS_OFFICIAL_CLOSE", "price_stage": "PREVIOUS_OFFICIAL_CLOSE",
                "market_date": "2026-07-17", "quote_timestamp": "2026-07-17T20:00:00+00:00",
                "market_timezone": "America/New_York", "is_finalized": False,
            }
            for symbol, price in {"VOO": 683.17, "QQQ": 695.33}.items()
        }
    })
    assert grid_snapshot["snapshot_comparable"] is True


def test_unknown_provider_currency_falls_back_to_security_master() -> None:
    portfolio = _fixture_bundle()["portfolio_snapshot"]
    voo = next(row for row in portfolio["positions"] if row["security_id"] == "VOO")
    assert voo["price_currency"] == "USD"
    assert voo["market_value_cny"] > 130000
    assert portfolio["pending_valuation_total"] == 0


def test_simulation_assets_excluded_from_real_portfolio() -> None:
    bundle = _fixture_bundle()
    assert bundle["portfolio_snapshot"]["simulation_assets_cny"] == 0
    assert bundle["grid_simulation"]["mode"] == "SIMULATION_ONLY"


def test_asset_totals_reconcile() -> None:
    portfolio = _fixture_bundle()["portfolio_snapshot"]
    assert sum(portfolio["asset_class_values"].values()) == portfolio["total_valued_assets"]


def test_warning_counts_have_distinct_semantics() -> None:
    issues = _fixture_bundle()["issues"]
    assert {"warning_count", "blocking_count", "consistency_warning_count"} <= issues.keys()


def test_same_input_produces_same_bundle_hash() -> None:
    assert _fixture_bundle()["bundle_hash"] == _fixture_bundle()["bundle_hash"]


def test_main_report_and_appendix_share_bundle_hash() -> None:
    bundle = _fixture_bundle()
    assert bundle["render_contract"]["main_bundle_hash"] == bundle["render_contract"]["appendix_bundle_hash"] == bundle["bundle_hash"]


def test_end_to_end_fixed_sample_and_invariants() -> None:
    first = _fixture_bundle()
    second = _fixture_bundle()
    assert validate_final_decision_bundle(first)["status"] == "PASS"
    assert first["bundle_hash"] == second["bundle_hash"]
    assert render_daily_report(first) == render_daily_report(second)
