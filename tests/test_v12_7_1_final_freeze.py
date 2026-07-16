from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.decision.v12_1_decision import (
    build_opportunity_scores,
    build_portfolio_repair_priority,
    build_trade_permission_gates,
    compute_dqs,
    describe_max_opportunity,
    enrich_allocation,
    load_strategy,
)
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.report_center import generate_daily_report
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
        {"has_high_event_next_7_days": False},
    )
    opportunity = next(row for row in gates["scenarios"] if row["scenario_name"] == "Opportunity Add")
    assert opportunity["dqs_gate_passed"] is False
    assert opportunity["final_permission"] == "DENY"
    assert any("DQS 51低于门槛85" in reason for reason in opportunity["exact_denial_reasons"])


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
        {"has_high_event_next_7_days": False},
    )
    scheduled = gates["scenarios"][0]
    assert scheduled["dqs_gate_passed"] is True
    assert scheduled["schedule_gate_passed"] is False
    assert scheduled["final_permission"] == "DENY"
    assert "当前不是计划定投日" in scheduled["exact_denial_reasons"][0]


def test_04_risk_and_event_gates_are_separate_with_exact_factors() -> None:
    risk = {
        "score": 53,
        "market_risk": {
            "confidence": "high",
            "components": [
                {"item": "利率", "score": 13},
                {"item": "市场宽度与资金流", "score": 8},
            ],
        },
    }
    gates = build_trade_permission_gates(
        _all_use_cases(),
        {"is_dca_day": True, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        risk,
        {"has_high_event_next_7_days": False},
    )
    opportunity = next(row for row in gates["scenarios"] if row["scenario_name"] == "Opportunity Add")
    assert opportunity["risk_gate_passed"] is False
    assert opportunity["event_gate_passed"] is True
    assert opportunity["risk_threshold"] == 50
    assert opportunity["current_risk_score"] == 53
    assert opportunity["event_blocking_factors"] == []
    assert opportunity["risk_top_contributors"] == ["利率 13分", "市场宽度与资金流 8分"]


def test_05_strategic_rebalance_is_evaluation_only_and_grid_is_simulation_denied() -> None:
    gates = build_trade_permission_gates(
        _all_use_cases(),
        {"is_dca_day": True, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        {"score": 30, "market_risk": {"confidence": "high", "components": []}},
        {"has_high_event_next_7_days": False},
    )
    strategic = next(row for row in gates["scenarios"] if row["scenario_name"] == "Strategic Rebalance")
    grid = next(row for row in gates["scenarios"] if row["scenario_name"] == "Grid Trading")
    assert strategic["final_permission"] == "ALLOW_EVALUATION_ONLY"
    assert grid["final_permission"] == "DENY"
    assert any("SIMULATION_ONLY" in reason for reason in grid["exact_denial_reasons"])


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
    assert st["holding_source_file"].endswith("data\\portfolio_master.yaml")
    decision = _decision()
    opportunity = next(row for row in decision["opportunity"] if row["name"] == "*ST闻泰")
    assert opportunity["today_trade_permission"] is False
    assert opportunity["final_action"] == "风险复核或回避"


def test_11_report_body_is_sections_zero_to_five_and_details_are_appendix() -> None:
    report = generate_daily_report(decision=_decision())
    body, appendix = report.split("# 附录", 1)
    for number in range(6):
        assert f"## {number}." in body
    assert "## 6." not in body
    assert "Market Attractiveness Score" in appendix
    assert "Portfolio Repair Priority" in appendix
    assert report.count("## 4. 下一触发条件") == 1


def test_12_report_wording_and_stress_headers_are_unambiguous() -> None:
    report = generate_daily_report(decision=_decision())
    assert "行情字段覆盖率" in report
    assert "A股研究数据完整度 / 港股研究数据完整度" in report
    assert "组合收益/损失率" in report
    assert "组合收益/损失金额" in report
    assert "最大正贡献/负贡献资产" in report
    assert "组合损失比例" not in report


def test_13_main_py_remains_only_production_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    production_main = [
        path for path in root.rglob("main.py")
        if "archive" not in path.parts and ".venv" not in path.parts and "venv" not in path.parts
    ]
    assert production_main == [root / "main.py"]
    assert "from src.app import main" in (root / "main.py").read_text(encoding="utf-8")


def test_14_existing_cash_trade_and_grid_isolation_contract() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["cash"]["account_total_cash_cny"] == 241000
    assert snapshot["cash"]["cash_safety_reserve_cny"] == 220000
    assert snapshot["cash"]["investable_cash_cny"] == 21000
    assert snapshot["cash"]["paper_grid_cash_cny"] == 0
    trade = snapshot["confirmed_transactions"][0]
    assert trade["invested_amount_cny"] == 9000
    assert trade["reconciliation_status"] == "WARN"
    assert trade["quantity"] is None


def test_15_provisional_cost_is_not_exact_rebalance_value() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["total_assets"] == 2821100
    assert snapshot["decision_total_assets"] == 2812100
    assert snapshot["provisional_value_cny"] == 9000
    assert snapshot["asset_class_totals"]["美股"] == 339000
    assert snapshot["decision_asset_class_totals"]["美股"] == 330000


def test_16_market_risk_weights_are_exactly_100() -> None:
    strategy = load_strategy()
    assert sum(strategy["market_risk_weights"].values()) == 100

