from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.data_sources import fred_client
from src.decision.v12_1_decision import (
    build_opportunity_scores,
    build_portfolio_repair_priority,
    build_trade_reconciliation_summary,
    build_trade_permission_gates,
    compute_dqs,
    describe_max_opportunity,
    enrich_allocation,
    load_strategy,
)
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.report_center import generate_daily_report
from tests.test_final_decision_bundle import _fixture_bundle
from tests.test_v12_1_stable import _live_market, _portfolio
from tests.test_v12_5_freeze import _decision


def _all_use_cases(score: int = 100) -> dict:
    return {
        "use_cases": {
            "scheduled_dca": {"score": score, "threshold": 65},
            "opportunity_add": {"score": score, "threshold": 85},
            "strategic_rebalance": {"score": score, "threshold": 75},
            "grid": {"score": score, "threshold": 85},
            "risk_monitoring": {"score": score, "threshold": 1},
            "transaction_reconciliation": {"score": score, "threshold": 100},
        }
    }


def test_01_opportunity_add_dqs_51_is_denied() -> None:
    gates = build_trade_permission_gates(
        _all_use_cases(51),
        {"is_dca_day": True, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        {"score": 40, "market_risk": {"confidence": "high", "components": []}},
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
    )
    opportunity = next(row for row in gates["scenarios"] if row["scenario_name"] == "Opportunity Add")
    assert opportunity["dqs_gate_passed"] is False
    assert opportunity["final_permission"] == "DENY"
    assert any("opportunity_dqs=51" in reason and "85" in reason for reason in opportunity["exact_denial_reasons"])


def test_02_scheduled_dca_has_independent_dqs_and_enhancement_missing_does_not_deny() -> None:
    live = _live_market()
    live["items"]["VOO"].update({"price_stage": "OFFICIAL_CLOSE", "is_finalized": True})
    live["market_context_status"] = {"indicators": []}
    dqs = compute_dqs(live, load_strategy(), {"calendar_confidence": "high"})
    scheduled = dqs["use_cases"]["scheduled_dca"]
    opportunity = dqs["use_cases"]["opportunity_add"]
    assert dqs["enhancement_missing_count"] > 0
    assert scheduled["enhancement_data_required"] is False
    assert scheduled["dqs_gate_passed"] is True
    assert scheduled["allowed"] is True
    assert opportunity["enhancement_data_required"] is True


def test_03_non_scheduled_day_denies_scheduled_dca_even_when_dqs_passes() -> None:
    gates = build_trade_permission_gates(
        _all_use_cases(),
        {"is_dca_day": False, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        {"score": 40, "market_risk": {"confidence": "high", "components": []}},
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
    )
    scheduled = gates["scenarios"][0]
    assert scheduled["dqs_gate_passed"] is True
    assert scheduled["schedule_gate_passed"] is False
    assert scheduled["final_permission"] == "DENY"
    assert any("计划执行窗口" in reason for reason in scheduled["exact_denial_reasons"])


def test_04_risk_and_event_gates_are_separate() -> None:
    risk = {"score": 53, "market_risk": {"confidence": "high", "components": [{"item": "利率", "score": 13}]}}
    gates = build_trade_permission_gates(
        _all_use_cases(),
        {"is_dca_day": True, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        risk,
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
    )
    opportunity = next(row for row in gates["scenarios"] if row["scenario_name"] == "Opportunity Add")
    assert opportunity["risk_gate_passed"] is False
    assert opportunity["event_gate_passed"] is True
    assert opportunity["final_permission"] == "DENY"
    assert opportunity["rejection_reasons"]


def test_05_strategic_rebalance_is_evaluation_only_and_grid_is_simulation_only() -> None:
    gates = build_trade_permission_gates(
        _all_use_cases(),
        {"is_dca_day": True, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        {"score": 30, "market_risk": {"confidence": "high", "components": []}},
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
    )
    strategic = next(row for row in gates["scenarios"] if row["scenario_name"] == "Strategic Rebalance")
    grid = next(row for row in gates["scenarios"] if row["scenario_name"] == "Grid Trading")
    assert strategic["final_permission"] == "ALLOW_EVALUATION_ONLY"
    assert grid["final_permission"] == "ALLOW_SIMULATION_ONLY"
    assert "实盘网格现金为0" in grid["live_rejection_reasons"]
    assert any("SIMULATION_ONLY" in reason for reason in grid["warning_reasons"])


def test_06_market_attractiveness_does_not_change_with_portfolio_deviation() -> None:
    live = _live_market()
    strategy = load_strategy()
    allocation = enrich_allocation(_portfolio(), strategy)
    changed = [dict(row) for row in allocation]
    us = next(row for row in changed if row["category"] == "美股")
    us["deviation_ratio"] = 0.20
    first = next(row for row in build_opportunity_scores(allocation, live, strategy) if row["name"] == "VOO")
    second = next(row for row in build_opportunity_scores(changed, live, strategy) if row["name"] == "VOO")
    assert first["market_attractiveness_score"] == second["market_attractiveness_score"]


def test_07_portfolio_repair_priority_puts_broad_us_equity_first() -> None:
    strategy = load_strategy()
    allocation = enrich_allocation(_portfolio(), strategy)
    opportunity = build_opportunity_scores(allocation, _live_market(), strategy)
    repair = build_portfolio_repair_priority(allocation, opportunity)
    assert repair[0]["category"] == "美股"
    assert repair[0]["preferred_broad_market_instrument"] == "VOO"
    assert repair[0]["repair_direction"] == "ADD_WITH_NEW_MONEY"


def test_08_no_cross_asset_unified_winner_wording() -> None:
    text = describe_max_opportunity(
        [
            {"name": "沪深300ETF", "category": "A股", "advice": "观察", "score": 90},
            {"name": "VOO", "category": "美股", "advice": "观察", "score": 80},
        ],
        {"mode_label": "方向性建议"},
        False,
    )
    assert "跨资产不生成统一第一名" in text
    assert "美股宽基仍是长期第一优先方向" in text


def test_09_unconfirmed_holding_is_excluded_from_formal_assets() -> None:
    fake_master = {
        "source": "user_confirmed",
        "as_of": "2026-07-16",
        "asset_class_labels": {"us_stock": "美股"},
        "totals": {"us_stock": 1000, "total_assets": 1000},
        "cash_policy": {},
        "confirmed_holding_whitelist": [],
        "holdings": [{
            "asset_id": "test_pollution", "asset_class": "us_stock", "allocation_bucket": "us_equity",
            "security_name": "TEST", "security_code": "TEST", "market_value_cny": 1000,
            "data_source": "example_fixture", "valuation_time": "2026-07-16",
        }],
    }
    with patch("src.portfolio_snapshot._load_yaml", side_effect=[fake_master, {}]):
        snapshot = build_portfolio_snapshot()
    assert snapshot["holdings"] == []
    assert snapshot["total_assets"] == 0
    assert snapshot["unconfirmed_holdings"][0]["validation_status"] == "UNCONFIRMED_HOLDING"


def test_10_confirmed_st_holding_has_provenance_and_permanent_no_auto_add() -> None:
    snapshot = build_portfolio_snapshot()
    st = next(row for row in snapshot["holdings"] if row["security_name"] == "*ST闻泰")
    assert st["user_confirmed"] is True
    assert st["holding_source"] == "user_confirmed_category_reconciled"
    assert Path(st["holding_source_file"]).parts[-2:] == ("data", "portfolio_master.yaml")
    decision = _decision()
    opportunity = next(row for row in decision["opportunity"] if row["name"] == "*ST闻泰")
    assert opportunity["today_trade_permission"] is False
    assert opportunity["final_action"] == "风险复核或回避"


def test_11_report_body_and_appendix_share_one_bundle() -> None:
    bundle = _fixture_bundle()
    report = generate_daily_report(decision=bundle)
    assert bundle["render_contract"]["main_bundle_hash"] == bundle["render_contract"]["appendix_bundle_hash"]
    assert report.count(bundle["bundle_hash"]) >= 3


def test_12_report_wording_is_unambiguous() -> None:
    report = generate_daily_report(decision=_fixture_bundle())
    assert "待估值成本记录" in report
    assert "不进入精确市值和配置占比" in report
    assert "SIMULATION_ONLY" in report


def test_13_main_py_remains_only_production_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    production_main = [
        path for path in root.rglob("main.py")
        if "archive" not in path.parts and ".venv" not in path.parts and "venv" not in path.parts
    ]
    assert production_main == [root / "main.py"]
    assert "from src.pipeline.unified_pipeline import main" in (root / "main.py").read_text(encoding="utf-8")


def test_14_existing_cash_trade_and_grid_isolation_contract() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["cash"]["account_total_cash_cny"] == 241000
    assert snapshot["cash"]["cash_safety_reserve_cny"] == 220000
    assert snapshot["cash"]["investable_cash_cny"] == 21000
    assert snapshot["cash"]["paper_grid_cash_cny"] == 0
    trade = snapshot["confirmed_transactions"][0]
    assert trade["invested_amount_cny"] == 9000
    assert trade["reconciliation_status"] == "RECONCILED"
    assert trade["trade_datetime"] == "2026-07-15T10:24:00-04:00"
    assert trade["quantity"] == pytest.approx(2.166)
    assert trade["execution_price"] == pytest.approx(692.5)
    assert trade["fee"] == 0
    assert trade["actual_fx_rate_cny_per_usd"] is None
    assert trade["valuation_fx_rate_cny_per_usd"] is None
    assert trade["trade_currency"] == "USD"
    assert trade["funding_currency"] == "USD"
    assert trade["trade_amount_usd"] == pytest.approx(1499.955)
    assert trade["fx_status"] == "NOT_APPLICABLE_USD_CASH"
    assert trade["missing_reconciliation_fields"] == []


def test_15_provisional_cost_is_not_exact_rebalance_value() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["total_assets"] == 2812100
    assert snapshot["total_asset_including_cost_records"] == 2821100
    assert snapshot["provisional_value_cny"] == 9000
    assert snapshot["asset_class_totals"]["美股"] == 330000
    assert snapshot["decision_asset_class_totals"]["美股"] == 330000


def test_16_market_risk_weights_are_exactly_100() -> None:
    strategy = load_strategy()
    assert sum(strategy["market_risk_weights"].values()) == 100


def test_17_usd_cash_trade_does_not_require_actual_fx() -> None:
    snapshot = build_portfolio_snapshot()
    summary = build_trade_reconciliation_summary(snapshot, {"items": {"VOO": {"current_price": 700}}})
    assert summary["status"] == "PASS"
    assert summary["missing_fields"] == []
    assert summary["transaction_reconciliation_quality"] == 100
    trade_row = summary["transactions"][0]
    assert trade_row["actual_fx_rate_cny_per_usd"] is None
    assert trade_row["fx_status"] == "NOT_APPLICABLE_USD_CASH"
    assert summary["auto_recalculated"] is True
    assert trade_row["position_total_quantity"] == pytest.approx(30.166)
def test_18_cpi_and_gdp_use_release_frequency_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    business_today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    observed_dates = {
        "CPIAUCSL": business_today - timedelta(days=50),
        "GDP": business_today - timedelta(days=130),
    }

    def fake_get(_: str, params: dict[str, str]) -> dict:
        observed = observed_dates[params["series_id"]].isoformat()
        return {"observations": [{"date": observed, "value": "100"}]}

    monkeypatch.setattr(fred_client, "_get_json", fake_get)
    cpi = fred_client.get_series_latest("CPIAUCSL")
    gdp = fred_client.get_series_latest("GDP")
    assert cpi["stale"] is False
    assert gdp["stale"] is False
    assert cpi["data_status"] == "VALID_LAGGED_BY_DESIGN"
    assert gdp["data_status"] == "VALID_LAGGED_BY_DESIGN"

    observed_dates["CPIAUCSL"] = business_today - timedelta(days=51)
    observed_dates["GDP"] = business_today - timedelta(days=131)
    assert fred_client.get_series_latest("CPIAUCSL")["data_status"] == "DATA_INSUFFICIENT"
    assert fred_client.get_series_latest("GDP")["data_status"] == "DATA_INSUFFICIENT"


def test_19_three_dqs_are_independent_and_enhancement_only_limits_opportunity() -> None:
    live = _live_market()
    live["items"]["VOO"].update({"price_stage": "OFFICIAL_CLOSE", "is_finalized": True})
    live["market_context_status"] = {"indicators": []}
    dqs = compute_dqs(live, load_strategy(), {"calendar_confidence": "high"})

    assert dqs["core_dqs"] == dqs["use_cases"]["scheduled_dca"]["score"]
    assert dqs["opportunity_dqs"] == dqs["use_cases"]["opportunity_add"]["score"]
    assert dqs["execution_dqs"] == dqs["use_cases"]["transaction_reconciliation"]["score"]
    assert dqs["use_cases"]["scheduled_dca"]["allowed"] is True
    assert dqs["use_cases"]["opportunity_add"]["allowed"] is False
    opportunity_issues = dqs["data_issues_by_scope"]["opportunity_add"]
    assert {row["item"] for row in opportunity_issues} >= {"市场宽度", "ETF资金流"}
    assert all(row["data_status"] == "NOT_CONNECTED" for row in opportunity_issues)
    assert dqs["data_issues_by_scope"]["scheduled_dca"] == []


def test_20_daily_report_explains_dqs_and_event_scope() -> None:
    bundle = _fixture_bundle()
    report = generate_daily_report(decision=bundle)
    for name in ["core_dqs", "opportunity_dqs", "execution_dqs", "rebalance_dqs", "grid_dqs"]:
        assert name in report
    assert bundle["event_assessment"]["status"] in report
