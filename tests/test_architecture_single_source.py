from __future__ import annotations

import json
from pathlib import Path

from src.decision.v12_1_decision import (
    build_trade_permission_gates,
    compute_dqs,
    enrich_allocation,
    load_strategy,
    refresh_unified_decision_context,
)
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.report_center import _allocation_table, generate_daily_report
from tests.test_v12_1_stable import _live_market
from tests.test_final_decision_bundle import _fixture_bundle


FIXTURE = Path(__file__).parent / "fixtures" / "architecture_golden_report.json"


def _quality() -> dict:
    return compute_dqs(
        _live_market(),
        load_strategy(),
        {"calendar_confidence": "high", "released_events": []},
        build_portfolio_snapshot(),
    )


def _contexts(quality: dict | None = None) -> dict:
    quality = quality or _quality()
    return build_trade_permission_gates(
        quality,
        {"is_dca_day": True, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        {"score": 30, "market_risk": {"confidence": "high", "components": []}},
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
        {
            "core_decision_comparability": "COMPARABLE",
            "cross_asset_comparability": "COMPARABLE",
            "grid_snapshot_comparability": "COMPARABLE",
        },
    )


def _minimal_report_decision() -> dict:
    snapshot = build_portfolio_snapshot()
    quality = _quality()
    snapshot["allocation"] = enrich_allocation({}, load_strategy(), snapshot)
    snapshot["portfolio_repair_priority"] = []
    snapshot["holding_diagnostics"] = []
    snapshot["stress_scenarios"] = []
    contexts = _contexts(quality)
    return {
        "date": "2026-07-19",
        "report_business_date": "2026-07-19",
        "report_run_mode": "SCHEDULED",
        "report_run_mode_label": "自动定时运行",
        "generated_at": "2026-07-19T08:30:00+08:00",
        "report_generated_at": "2026-07-19T08:30:00+08:00",
        "decision_cutoff_at": "2026-07-19T08:30:00+08:00",
        "portfolio_snapshot": snapshot,
        "portfolio_value_yuan": snapshot["total_valued_assets"],
        "allocation": [{"category": "错误旧值", "current_amount_yuan": 999}],
        "data_quality_snapshot": quality,
        "dqs": quality,
        "risk_snapshot": {"score": 30, "level": "低风险", "market_risk": {"confidence": "high", "components": [], "market_risk_weights_sum": 100}},
        "risk": {"score": 30, "level": "低风险", "market_risk": {"confidence": "high", "components": [], "market_risk_weights_sum": 100}},
        "decision_context": contexts,
        "trade_permission_gates": contexts,
        "budget": {"investable_cash_yuan": 21000, "today_total_yuan": 0, "rows": []},
        "comparability": {
            "core_decision_comparability": "COMPARABLE",
            "cross_asset_comparability": "COMPARABLE",
            "grid_snapshot_comparability": "NOT_EVALUATED",
            "non_comparable_items_count": 0,
            "non_comparable_items": [],
        },
        "consistency": {"status": "WARN", "errors": [], "warnings": quality["warnings"], "checks": []},
        "confirmed_transactions": snapshot["confirmed_transactions"],
        "actual_trade_recorded": True,
        "actual_trade_date": "2026-07-15",
        "trade_reconciliation": {"status": "PASS", "missing_fields": []},
        "today_trade": False,
        "trade_type": "历史实盘交易（本报告仅归档）",
        "targets": "不适用",
        "funding_source": "今日不使用资金",
        "grid": {},
        "ai": {},
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不自动交易。",
    }


def test_01_each_dqs_total_equals_its_component_sum() -> None:
    quality = _quality()
    for name, components in quality["component_scores"].items():
        assert quality[name] == sum(int(row["score"]) for row in components)


def test_02_section_three_and_appendix_use_snapshot_allocation() -> None:
    bundle = _fixture_bundle()
    table = _allocation_table(bundle)
    assert any("美股" in row for row in table)
    assert all("错误旧值" not in row for row in table)


def test_03_exact_asset_classes_sum_to_total_valued_assets() -> None:
    snapshot = build_portfolio_snapshot()
    assert sum(snapshot["asset_class_values"].values()) == snapshot["total_valued_assets"]


def test_04_pending_9000_is_excluded_from_exact_weights() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["pending_valuation_total"] == 9000
    assert snapshot["asset_class_values"]["美股"] == 330000
    assert snapshot["asset_class_totals"]["美股"] == 330000
    assert abs(sum(snapshot["asset_class_weights"].values()) - 1.0) < 1e-12


def test_05_strategic_rebalance_uses_only_rebalance_dqs() -> None:
    context = _contexts()["contexts"]["strategic_rebalance"]
    assert context["used_dqs_name"] == "rebalance_dqs"
    assert context["used_dqs_value"] == _quality()["rebalance_dqs"]


def test_06_grid_trading_uses_only_grid_dqs() -> None:
    context = _contexts()["contexts"]["grid"]
    assert context["used_dqs_name"] == "grid_dqs"
    assert context["used_dqs_value"] == _quality()["grid_dqs"]


def test_07_opportunity_and_grid_dqs_are_not_mixed() -> None:
    contexts = _contexts()["contexts"]
    assert contexts["opportunity_add"]["used_dqs_name"] == "opportunity_dqs"
    assert contexts["grid"]["used_dqs_name"] == "grid_dqs"
    assert contexts["opportunity_add"]["used_dqs_value"] != contexts["grid"]["used_dqs_value"]


def test_08_data_insufficient_always_creates_a_warning() -> None:
    quality = _quality()
    assert any("DATA_INSUFFICIENT" in warning for warning in quality["warnings"])


def test_09_non_comparable_state_always_creates_a_warning() -> None:
    quality = _quality()
    decision = {
        "dqs": quality,
        "data_quality_snapshot": quality,
        "risk": {"score": 30, "market_risk": {"confidence": "high", "components": []}},
        "risk_snapshot": {"score": 30, "market_risk": {"confidence": "high", "components": []}},
        "budget": {"is_dca_day": False, "confirmed_cash_available_yuan": 21000, "live_grid_cash_yuan": 0},
        "market_table": [],
        "grid": {},
    }
    refresh_unified_decision_context(
        decision,
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
    )
    assert any("comparability=DATA_NOT_COMPARABLE" in warning for warning in quality["warnings"])


def test_10_simulation_cash_never_enters_real_cash() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["cash"]["paper_grid_cash_cny"] == 0
    assert snapshot["investable_cash"] == 241000 - 220000


def test_11_historical_trade_never_becomes_report_day_trade() -> None:
    bundle = _fixture_bundle()
    report = generate_daily_report(decision=bundle)
    assert bundle["report_metadata"]["report_business_date"] == "2026-07-19"
    assert bundle["report_metadata"]["actual_trade_date"] == "2026-07-15"
    assert "历史成交日期：2026-07-15" in report


def test_12_report_repeats_only_canonical_key_values() -> None:
    bundle = _fixture_bundle()
    report = generate_daily_report(decision=bundle)
    exact = bundle["portfolio_snapshot"]["precise_valued_assets"]
    non_exact = bundle["portfolio_snapshot"]["household_total_assets_estimated"]
    assert f"精确估值资产：{exact:,.2f} 元" in report
    assert f"包含待估值成本记录的非精确总额：{non_exact:,.2f} 元" in report
    assert bundle["bundle_hash"] in report


def test_13_golden_report_key_fields() -> None:
    snapshot = build_portfolio_snapshot()
    quality = _quality()
    actual = {
        "total_valued_assets": snapshot["total_valued_assets"],
        "pending_valuation_total": snapshot["pending_valuation_total"],
        "total_asset_including_cost_records": snapshot["total_asset_including_cost_records"],
        "asset_class_values": snapshot["asset_class_values"],
        **{name: quality[name] for name in ["core_dqs", "opportunity_dqs", "execution_dqs", "rebalance_dqs", "grid_dqs"]},
    }
    assert actual == json.loads(FIXTURE.read_text(encoding="utf-8"))
