from __future__ import annotations

import json
from pathlib import Path

from src.decision.issue_registry import build_issue_registry
from src.decision.permission_engine import build_scenario_decisions
from src.decision.scenario_dependencies import SCENARIO_DEPENDENCIES
from src.decision.v12_1_decision import compute_dqs, compute_risk_score, load_strategy
from src.domain.dqs_result import build_dqs_results
from src.domain.event_assessment import build_event_assessment
from src.domain.final_decision_bundle import validate_final_decision_bundle
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.bundle_report import render_daily_report
from tests.test_final_decision_bundle import _fixture_bundle
from tests.test_v12_1_stable import _live_market


GOLDEN = Path(__file__).parent / "fixtures" / "architecture_golden_report.json"


def _dqs(
    *,
    core: int = 75,
    opportunity: int = 63,
    execution: int = 100,
    rebalance: int = 100,
    grid: int = 100,
) -> dict:
    return build_dqs_results(
        {
            "core_dqs": [{"item": "core", "score": core, "max": 100}],
            "opportunity_dqs": [{"item": "opportunity", "score": opportunity, "max": 100}],
            "execution_dqs": [{"item": "execution", "score": execution, "max": 100}],
            "rebalance_dqs": [{"item": "rebalance", "score": rebalance, "max": 100}],
            "grid_dqs": [{"item": "grid", "score": grid, "max": 100}],
        }
    )


def _event(status: str = "DATA_INSUFFICIENT") -> dict:
    if status == "DATA_INSUFFICIENT":
        return build_event_assessment({"event_calendar_data_status": "UNAVAILABLE"})
    if status == "VALID_EVENTS_FOUND":
        return build_event_assessment(
            {"event_calendar_data_status": "VALID", "has_high_event_next_7_days": True}
        )
    return build_event_assessment({"event_calendar_data_status": "VALID"})


def _decisions(
    *,
    event_status: str = "DATA_INSUFFICIENT",
    is_dca_day: bool = False,
    risk_score: int = 56,
    live_grid_cash: int = 0,
    dqs: dict | None = None,
    comparability: dict | None = None,
) -> dict:
    return build_scenario_decisions(
        dqs_results=dqs or _dqs(),
        dqs_thresholds={
            "scheduled_dca": 65,
            "scheduled_dca_normal": 75,
            "opportunity_add": 85,
            "strategic_rebalance": 75,
            "grid": 85,
            "risk_monitoring": 1,
            "transaction_reconciliation": 100,
        },
        budget={
            "is_dca_day": is_dca_day,
            "confirmed_cash_available_yuan": 21000,
            "live_grid_cash_yuan": live_grid_cash,
            "portfolio_data_available": True,
            "target_allocation_available": True,
            "execution_data_available": True,
        },
        risk={"score": risk_score},
        event_assessment=_event(event_status),
        comparability=comparability
        or {
            "core_decision_comparability": "COMPARABLE",
            "cross_asset_comparability": "COMPARABLE",
            "grid_snapshot_comparability": "COMPARABLE",
        },
        today_trade=False,
    )


def test_event_insufficient_denies_opportunity_add() -> None:
    row = _decisions()["contexts"]["opportunity_add"]
    assert row["final_permission"] == "DENY"
    assert any("事件数据硬阻断" in reason for reason in row["rejection_reasons"])


def test_event_insufficient_allows_rebalance_evaluation_only() -> None:
    row = _decisions()["contexts"]["strategic_rebalance"]
    assert row["final_permission"] == "ALLOW_EVALUATION_ONLY"
    assert not row["rejection_reasons"]


def test_event_insufficient_never_denies_risk_monitoring() -> None:
    row = _decisions()["contexts"]["risk_monitoring"]
    assert row["final_permission"] in {"ACTIVE", "PARTIAL_MONITORING", "WARN"}
    assert row["final_permission"] != "DENY"


def test_event_insufficient_does_not_block_execution_dqs_100_reconciliation() -> None:
    row = _decisions()["contexts"]["transaction_reconciliation"]
    assert row["dqs_score"] == 100
    assert row["final_permission"] in {"PASS", "ALLOW_RECONCILIATION"}
    assert all("事件" not in reason for reason in row["rejection_reasons"])


def test_scheduled_dca_outside_window_is_denied_for_schedule_only() -> None:
    row = _decisions(is_dca_day=False)["contexts"]["scheduled_dca"]
    assert row["final_permission"] == "DENY"
    assert "当前不在计划执行窗口" in row["rejection_reasons"]
    assert all("事件" not in reason for reason in row["rejection_reasons"])


def test_scheduled_dca_in_window_with_only_event_missing_is_not_denied() -> None:
    row = _decisions(is_dca_day=True, risk_score=30)["contexts"]["scheduled_dca"]
    assert row["final_permission"] == "ALLOW_REDUCED_EXECUTION"
    assert not row["rejection_reasons"]


def test_grid_live_cash_zero_does_not_collapse_simulation_permission() -> None:
    row = _decisions(live_grid_cash=0)["contexts"]["grid"]
    assert row["final_permission"] == "ALLOW_SIMULATION_ONLY"
    assert "实盘网格现金为0" in row["live_rejection_reasons"]


def test_opportunity_comparability_change_does_not_modify_other_scenarios() -> None:
    baseline = _decisions()["contexts"]
    changed = _decisions(
        comparability={
            "core_decision_comparability": "COMPARABLE",
            "cross_asset_comparability": "DATA_NOT_COMPARABLE",
            "grid_snapshot_comparability": "COMPARABLE",
        }
    )["contexts"]
    for scenario in set(baseline) - {"opportunity_add"}:
        assert baseline[scenario]["final_permission"] == changed[scenario]["final_permission"]


def test_warning_count_equals_warning_detail_count() -> None:
    contexts = _decisions()
    registry = build_issue_registry(
        {
            "decision_context": contexts,
            "data_quality_snapshot": {"data_issues_by_scope": {}},
            "portfolio_snapshot": {},
            "comparability": {},
            "consistency": {"errors": [], "warnings": []},
            "risk_snapshot": {"market_risk": {"confidence": "high"}},
        }
    )
    assert registry["warning_count"] == len(registry["warnings"])


def test_blocking_count_equals_blocking_detail_count() -> None:
    contexts = _decisions()
    registry = build_issue_registry(
        {
            "decision_context": contexts,
            "data_quality_snapshot": {"data_issues_by_scope": {}},
            "portfolio_snapshot": {},
            "comparability": {},
            "consistency": {"errors": [], "warnings": []},
            "risk_snapshot": {"market_risk": {"confidence": "high"}},
        }
    )
    assert registry["blocking_count"] == len(registry["blocking"])


def test_market_risk_score_equals_component_contributions() -> None:
    snapshot = build_portfolio_snapshot()
    strategy = load_strategy()
    live = _live_market()
    macro = {"calendar_confidence": "high", "released_events": []}
    quality = compute_dqs(live, strategy, macro, snapshot)
    risk = compute_risk_score(live, macro, quality, strategy, snapshot)
    market = risk["market_risk"]
    assert sum(int(row["score"]) for row in market["components"]) == market["score"]


def test_all_dqs_totals_equal_breakdown_sum() -> None:
    assert all(
        result["total"] == sum(int(row["score"]) for row in result["breakdown"])
        for result in _dqs().values()
    )


def test_asset_totals_are_unchanged_from_frozen_golden_fixture() -> None:
    snapshot = build_portfolio_snapshot()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert snapshot["total_valued_assets"] == golden["total_valued_assets"]
    assert snapshot["pending_valuation_total"] == golden["pending_valuation_total"]
    assert snapshot["asset_class_values"] == golden["asset_class_values"]
    assert snapshot["cash"]["account_total_cash_cny"] == 241000
    assert snapshot["safety_cash"] == 220000
    assert snapshot["investable_cash"] == 21000


def test_position_rows_ids_and_values_are_unchanged() -> None:
    positions = build_portfolio_snapshot()["positions"]
    expected = [
        ("VOO", 130000.0), ("NVDA", 68000.0), ("GOOG", 22000.0),
        ("TLT", 55000.0), ("IBKR", 40000.0), ("XLF", 42000.0),
        ("BABA", 28000.0), ("HK_03033", 140400.0), ("HK_513060", 99000.0),
        ("HK_513090", 33200.0), ("CN_510300", 206000.0),
        ("CN_002558", 41000.0), ("CN_ST_WENTAI", 19500.0),
        ("CN_BOND_CORE", 967800.0), ("CN_LOCAL_BOND_10Y", 132200.0),
        ("GOLD_BAR_565G", 512000.0), ("GOLD_518880", 35000.0),
        ("CASH_CNY", 241000.0),
    ]
    assert [(row["security_id"], row["market_value_cny"]) for row in positions] == expected


def test_main_report_and_appendix_keep_the_same_bundle_hash() -> None:
    bundle = _fixture_bundle()
    assert bundle["render_contract"]["main_bundle_hash"] == bundle["render_contract"]["appendix_bundle_hash"]


def test_event_gate_cannot_deny_all_six_scenarios() -> None:
    permissions = {row["final_permission"] for row in _decisions()["scenarios"]}
    assert permissions != {"DENY"}
    assert {"ALLOW_EVALUATION_ONLY", "ALLOW_SIMULATION_ONLY", "PARTIAL_MONITORING", "PASS"} <= permissions


def test_reconciliation_result_is_independent_of_event_gate() -> None:
    missing = _decisions(event_status="DATA_INSUFFICIENT")["contexts"]["transaction_reconciliation"]
    valid = _decisions(event_status="VALID_NO_HIGH_IMPACT_EVENT")["contexts"]["transaction_reconciliation"]
    assert missing["final_permission"] == valid["final_permission"] == "PASS"
    assert missing["rejection_reasons"] == valid["rejection_reasons"] == []


def test_risk_monitoring_is_not_hard_blocked_by_event_gate() -> None:
    row = _decisions(event_status="DATA_INSUFFICIENT")["contexts"]["risk_monitoring"]
    assert row["event_gate_applicable"] is False
    assert row["final_permission"] != "DENY"


def test_rebalance_event_missing_never_emits_live_execution_permission() -> None:
    row = _decisions(event_status="DATA_INSUFFICIENT")["contexts"]["strategic_rebalance"]
    assert row["final_permission"] == "ALLOW_EVALUATION_ONLY"
    assert row["manual_confirmation_required"] is True


def test_dependency_matrix_declares_all_six_scenarios() -> None:
    assert set(SCENARIO_DEPENDENCIES) == {
        "scheduled_dca", "opportunity_add", "strategic_rebalance", "grid",
        "risk_monitoring", "transaction_reconciliation",
    }
    assert all("event_data_mode" in row and "allowed_permissions" in row for row in SCENARIO_DEPENDENCIES.values())


def test_report_lists_every_warning_and_blocking_detail() -> None:
    bundle = _fixture_bundle()
    report = render_daily_report(bundle)
    assert f"警告总数：**{bundle['issues']['warning_count']}**" in report
    assert f"阻断总数：**{bundle['issues']['blocking_count']}**" in report
    assert validate_final_decision_bundle(bundle)["status"] == "PASS"
