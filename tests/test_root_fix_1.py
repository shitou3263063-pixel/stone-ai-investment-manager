from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.analysis.comparability_engine import build_comparability_snapshot
from src.data_sources.data_router import _apply_macro_freshness
from src.data_sources.source_registry import DataSourceRegistry
from src.decision.issue_registry import build_issue_registry
from src.decision.permission_engine import CANONICAL_PERMISSIONS, finalize_permission_context
from src.decision.v12_1_decision import build_trade_reconciliation_summary
from src.portfolio_snapshot import _canonical_portfolio_values
from src.valuation.valuation_engine import apply_live_valuation
from utils.data_loader import load_config, project_root


def _base_snapshot() -> dict:
    return {
        "holdings": [
            {
                "asset_id": "us_voo", "canonical_id": "VOO", "security_code": "VOO",
                "security_name": "VOO", "asset_class": "美股", "currency": "CNY",
                "quantity": 28, "market_value_cny": 130000, "market_value_original": 130000,
                "market_value_original_currency": "CNY", "valuation_status": "confirmed_market_value",
                "holding_source": "user_confirmed", "confidence": "high",
            },
            {
                "asset_id": "voo_cost", "security_code": "", "reference_symbol": "VOO",
                "security_name": "VOO cost", "asset_class": "美股", "currency": "USD",
                "actual_quantity": 2.166, "market_value_cny": 9000, "additional_cost_cny": 9000,
                "valuation_status": "trade_reconciled_valuation_fx_pending", "confidence": "high",
            },
            {
                "asset_id": "cash", "security_code": "CASH", "security_name": "Cash",
                "asset_class": "现金", "currency": "CNY", "market_value_cny": 241000,
                "market_value_original": 241000, "market_value_original_currency": "CNY",
                "valuation_status": "confirmed_market_value", "confidence": "high",
            },
        ],
        "asset_class_values": {"美股": 130000, "现金": 241000},
        "cash": {"account_total_cash_cny": 241000, "cash_safety_reserve_cny": 220000},
    }


def _market(*, fx: bool = True, stage: str = "PREVIOUS_OFFICIAL_CLOSE") -> dict:
    items = {
        "VOO": {
            "status": "ok", "close": 683.17, "data_stage": stage,
            "market_date": "2026-07-17", "observed_at": "2026-07-17T16:00:00-04:00",
            "source": "yfinance", "source_tier": 3,
        }
    }
    if fx:
        items["USD/CNY"] = {
            "status": "ok", "close": 7.18, "data_stage": "PREVIOUS_OFFICIAL_CLOSE",
            "market_date": "2026-07-17", "observed_at": "2026-07-17T16:00:00-04:00",
            "source": "yfinance",
        }
    return {"items": items}


def test_voo_trade_merges_once_with_independent_valuation_fx() -> None:
    result = apply_live_valuation(_base_snapshot(), _market(), valuation_as_of="2026-07-19T08:00:00+08:00")
    voo = next(row for row in result["holdings"] if row.get("security_code") == "VOO")
    assert voo["quantity"] == pytest.approx(30.166)
    assert voo["market_value_cny"] == pytest.approx(30.166 * 683.17 * 7.18, abs=0.01)
    assert result["pending_valuation_total"] == 0
    assert result["voo_trade_merged_once"] is True
    assert result["cost_records"][0]["included_in_precise_market_value"] is False


def test_reconciliation_does_not_add_trade_quantity_after_valuation_merge() -> None:
    snapshot = apply_live_valuation(_base_snapshot(), _market(), valuation_as_of="2026-07-19T08:00:00+08:00")
    snapshot["confirmed_transactions"] = [{
        "id": "trade", "symbol": "VOO", "trade_date": "2026-07-15",
        "trade_datetime": "2026-07-15T10:24:00-04:00", "quantity": 2.166,
        "fee": 0, "trade_currency": "USD", "funding_currency": "USD",
        "trade_amount_usd": 1499.955, "fx_status": "NOT_APPLICABLE_USD_CASH",
    }]
    result = build_trade_reconciliation_summary(snapshot, _market())
    assert result["voo_total_quantity"] == pytest.approx(30.166)
    assert result["recalculated_total_assets_cny"] == snapshot["total_valued_assets"]


def test_voo_cost_stays_pending_without_independent_fx() -> None:
    result = apply_live_valuation(_base_snapshot(), _market(fx=False), valuation_as_of="2026-07-19T08:00:00+08:00")
    assert result["pending_valuation_total"] == 9000
    assert result["voo_trade_merged_once"] is False
    assert result["pending_valuation_assets"][0]["pending_reason"] == "MISSING_INDEPENDENT_VALUATION_FX"


def test_intraday_price_cannot_resolve_official_portfolio_valuation() -> None:
    result = apply_live_valuation(_base_snapshot(), _market(stage="INTRADAY"), valuation_as_of="2026-07-19T08:00:00+08:00")
    assert result["pending_valuation_total"] == 9000


def test_same_currency_holding_has_not_applicable_fx() -> None:
    result = apply_live_valuation(_base_snapshot(), {}, valuation_as_of="2026-07-19T08:00:00+08:00")
    cash = next(row for row in result["holdings"] if row.get("security_code") == "CASH")
    assert cash["fx_rate"] == 1
    assert cash["fx_status"] == "NOT_APPLICABLE_SAME_CURRENCY"


def test_pending_cost_never_enters_precise_total() -> None:
    result = apply_live_valuation(_base_snapshot(), {}, valuation_as_of="2026-07-19T08:00:00+08:00")
    assert result["total_asset_including_cost_records"] - result["total_valued_assets"] == 9000
    assert sum(result["asset_class_values"].values()) == result["total_valued_assets"]


@pytest.mark.parametrize(
    ("metric", "frequency", "observed", "expected"),
    [
        ("VOO", "daily_market", "2026-07-17", "COMPARABLE"),
        ("^VIX", "daily_market", "2026-07-17", "COMPARABLE"),
        ("DGS10", "daily_official", "2026-07-16", "COMPARABLE"),
        ("CPIAUCSL", "monthly", "2026-06-15", "COMPARABLE"),
        ("UNRATE", "monthly", "2026-06-05", "COMPARABLE"),
        ("GDP", "quarterly", "2026-04-30", "COMPARABLE"),
        ("CPIAUCSL", "monthly", "2026-04-01", "DATA_NOT_COMPARABLE"),
        ("GDP", "quarterly", "2025-12-01", "DATA_NOT_COMPARABLE"),
    ],
)
def test_frequency_aware_comparability(metric: str, frequency: str, observed: str, expected: str) -> None:
    snapshot = build_comparability_snapshot(
        [{"name": metric, "success": True, "status": "ok", "frequency": frequency, "comparable_date": observed, "source": "test"}],
        decision_as_of="2026-07-19T08:00:00+08:00",
        blocking_dimensions=[],
    )
    assert snapshot["observations"][0]["comparability_status"] == expected


def test_mixed_valid_observation_dates_are_comparable() -> None:
    rows = [
        {"name": "VOO", "success": True, "status": "ok", "comparable_date": "2026-07-17"},
        {"name": "DGS10", "success": True, "status": "ok", "frequency": "daily_official", "comparable_date": "2026-07-16"},
        {"name": "CPIAUCSL", "success": True, "status": "ok", "frequency": "monthly", "comparable_date": "2026-06-15"},
    ]
    result = build_comparability_snapshot(rows, decision_as_of="2026-07-19", blocking_dimensions=["VOO", "DGS10"])
    assert result["final_status"] == "COMPARABLE"
    assert result["coverage_pct"] == 100


@pytest.mark.parametrize(
    ("series", "observed", "retrieved", "status"),
    [
        ("DGS10", "2026-07-16", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
        ("DGS2", "2026-07-15", "2026-07-19T08:00:00-04:00", "DATA_INSUFFICIENT"),
        ("CPIAUCSL", "2026-06-01", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
        ("UNRATE", "2026-06-05", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
        ("GDP", "2026-03-31", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
    ],
)
def test_macro_freshness_by_release_frequency(series: str, observed: str, retrieved: str, status: str) -> None:
    result = _apply_macro_freshness(series, {"status": "ok", "value": 1, "market_date": observed, "retrieved_at": retrieved})
    assert result["data_status"] == status


def _raw_permissions() -> dict:
    return {
        "scenarios": [
            {"scenario_key": "scheduled_dca", "scenario_name": "Scheduled DCA", "final_permission": "ALLOW_TRADE_SUBJECT_TO_MANUAL_CONFIRMATION"},
            {"scenario_key": "strategic_rebalance", "scenario_name": "Strategic Rebalance", "final_permission": "ALLOW_EVALUATION_ONLY"},
            {"scenario_key": "grid", "scenario_name": "Grid", "final_permission": "DENY"},
            {"scenario_key": "risk_monitoring", "scenario_name": "Risk", "final_permission": "ALLOW_MONITORING"},
            {"scenario_key": "transaction_reconciliation", "scenario_name": "Reconcile", "final_permission": "ALLOW_RECONCILIATION"},
        ]
    }


def test_no_today_trade_has_no_fake_selected_scenario() -> None:
    result = finalize_permission_context(_raw_permissions(), today_trade=False)
    assert result["selected_scenario"] is None
    assert result["today_trade_permission"] == "DENY"


def test_permission_enum_is_normalized() -> None:
    result = finalize_permission_context(_raw_permissions(), today_trade=True)
    assert {row["final_permission"] for row in result["scenarios"]} <= CANONICAL_PERMISSIONS
    assert result["automatic_trading_enabled"] is False


@pytest.mark.parametrize(
    ("scenario", "dqs_name"),
    [
        ("scheduled_dca", "core_dqs"),
        ("opportunity_add", "opportunity_dqs"),
        ("strategic_rebalance", "rebalance_dqs"),
        ("grid", "grid_dqs"),
        ("transaction_reconciliation", "execution_dqs"),
    ],
)
def test_scenario_dqs_binding_configuration(scenario: str, dqs_name: str) -> None:
    source = (project_root() / "src" / "decision" / "v12_1_decision.py").read_text(encoding="utf-8")
    assert f'"{scenario}": "{dqs_name}"' in source


def test_issue_registry_counts_pending_valuation_once() -> None:
    decision = {
        "portfolio_snapshot": {"pending_valuation_assets": [{"security_code": "VOO", "pending_reason": "MISSING_FX"}]},
        "data_quality_snapshot": {}, "comparability": {}, "consistency": {"errors": [], "warnings": []},
    }
    result = build_issue_registry(decision)
    assert result["warning_count"] == 1
    assert result["blocking_count"] == 1


def test_issue_registry_separates_errors_and_warnings() -> None:
    decision = {
        "portfolio_snapshot": {}, "data_quality_snapshot": {}, "comparability": {},
        "consistency": {"errors": ["broken invariant"], "warnings": ["limited data"]},
    }
    result = build_issue_registry(decision)
    assert result["error_count"] == 1
    assert result["warning_count"] == 1


def test_simulation_cash_is_not_part_of_canonical_portfolio_values() -> None:
    rows = [
        {"asset_class": "现金", "market_value_cny": 241000, "valuation_status": "confirmed_market_value"},
        {"asset_class": "模拟现金", "market_value_cny": 200000, "valuation_status": "confirmed_market_value", "is_cost_record": True},
    ]
    result = _canonical_portfolio_values(rows, ["现金", "模拟现金"])
    assert result["asset_class_values"]["现金"] == 241000
    assert result["asset_class_values"]["模拟现金"] == 0


def test_source_registry_supplies_complete_metadata_defaults() -> None:
    registry = DataSourceRegistry.load()
    definition = registry.source("yfinance").to_dict()
    required = {
        "provider_name", "source_tier", "supported_markets", "supported_fields",
        "expected_frequency", "timeout", "retry_policy", "cache_policy",
        "freshness_policy", "authentication_required", "license_or_usage_note",
        "fallback_order", "enabled", "health_status",
    }
    assert required <= definition.keys()


def test_version_and_entrypoint_remain_frozen() -> None:
    decision_source = (project_root() / "src" / "decision" / "v12_1_decision.py").read_text(encoding="utf-8")
    workflow = (project_root() / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    strategy = load_config(project_root() / "config" / "strategy.yaml")
    assert "Stone AI Investment Manager Pro V12.7.1 Final Freeze" in decision_source
    assert "python main.py" in workflow
    assert strategy["patch_level"] == "root_fix_1"


def test_target_allocation_and_cash_reserve_are_unchanged() -> None:
    strategy = load_config(project_root() / "config" / "strategy.yaml")
    assert sum(float(value) for value in strategy["target_allocation"].values()) == pytest.approx(1)
    snapshot = load_config(project_root() / "data" / "portfolio_master.yaml")
    assert float(snapshot["cash_policy"]["safety_reserve_cny"]) == 220000
