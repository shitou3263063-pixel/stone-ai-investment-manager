from __future__ import annotations

import pytest

from src.analysis.comparability_engine import build_comparability_snapshot
from src.data_sources.data_router import _apply_macro_freshness
from src.data_sources.source_registry import DataSourceRegistry
from src.decision.issue_registry import build_issue_registry
from src.decision.permission_engine import CANONICAL_PERMISSIONS, build_scenario_decisions
from src.decision.v12_1_decision import build_trade_reconciliation_summary
from src.domain.dqs_result import DQS_BINDINGS, build_dqs_results
from src.valuation.valuation_engine import apply_live_valuation
from utils.data_loader import load_config, project_root


def _base_snapshot() -> dict:
    return {
        "holdings": [
            {"asset_id": "us_voo", "canonical_id": "VOO", "security_code": "VOO", "security_name": "VOO", "asset_class": "美股", "currency": "CNY", "quantity": 28, "market_value_cny": 130000, "valuation_status": "confirmed_market_value"},
            {"asset_id": "voo_cost", "reference_symbol": "VOO", "security_name": "VOO cost", "asset_class": "美股", "actual_quantity": 2.166, "market_value_cny": 9000, "additional_cost_cny": 9000, "valuation_method": "confirmed_cost_pending_valuation", "valuation_status": "trade_reconciled_valuation_fx_pending"},
            {"asset_id": "cash", "security_code": "CASH_CNY", "security_name": "Cash", "asset_class": "现金", "currency": "CNY", "market_value_cny": 241000, "valuation_status": "confirmed_market_value"},
        ],
        "asset_class_values": {"美股": 130000, "现金": 241000},
        "cash": {"account_total_cash_cny": 241000, "cash_safety_reserve_cny": 220000},
        "confirmed_transactions": [{
            "id": "trade", "status": "executed", "symbol": "VOO", "action": "BUY",
            "trade_date": "2026-07-15", "trade_datetime": "2026-07-15T10:24:00-04:00",
            "quantity": 2.166, "fee": 0, "trade_currency": "USD", "funding_currency": "USD",
            "trade_amount_usd": 1499.955, "fx_status": "NOT_APPLICABLE_USD_CASH",
        }],
    }


def _market(*, fx: bool = True, stage: str = "PREVIOUS_OFFICIAL_CLOSE") -> dict:
    items = {"VOO": {"status": "ok", "close": 683.17, "data_stage": stage, "is_finalized": True, "market_date": "2026-07-17", "observed_at": "2026-07-17T16:00:00-04:00", "source": "yfinance"}}
    if fx:
        items["USD/CNY"] = {"status": "ok", "close": 7.18, "data_stage": "PREVIOUS_OFFICIAL_CLOSE", "market_date": "2026-07-17", "observed_at": "2026-07-17T16:00:00-04:00", "source": "yfinance"}
    return {"items": items}


def test_trade_merges_once_with_independent_valuation_fx() -> None:
    result = apply_live_valuation(_base_snapshot(), _market(), valuation_as_of="2026-07-19T08:00:00+08:00")
    voo = next(row for row in result["positions"] if row["security_id"] == "VOO")
    assert voo["quantity"] == pytest.approx(30.166)
    assert voo["market_value_cny"] == pytest.approx(30.166 * 683.17 * 7.18, abs=0.01)
    assert result["pending_valuation_total"] == 0
    assert result["cost_records"][0]["included_in_market_value"] is False


def test_reconciliation_reads_merged_position_without_recalculation() -> None:
    snapshot = apply_live_valuation(_base_snapshot(), _market(), valuation_as_of="2026-07-19T08:00:00+08:00")
    result = build_trade_reconciliation_summary(snapshot, _market())
    assert result["transactions"][0]["position_total_quantity"] == pytest.approx(30.166)
    assert result["total_valued_assets"] == snapshot["total_valued_assets"]


def test_cost_stays_pending_without_independent_fx() -> None:
    result = apply_live_valuation(_base_snapshot(), _market(fx=False), valuation_as_of="2026-07-19T08:00:00+08:00")
    assert result["pending_valuation_total"] == 9000
    assert result["pending_valuation_assets"][0]["valuation_status"] == "PENDING_VALUATION"


def test_intraday_price_produces_realtime_valuation() -> None:
    result = apply_live_valuation(_base_snapshot(), _market(stage="INTRADAY"), valuation_as_of="2026-07-19T08:00:00+08:00")
    voo = next(row for row in result["positions"] if row["security_id"] == "VOO")
    assert result["pending_valuation_total"] == 0
    assert voo["valuation_status"] == "VALUED_REALTIME"


def test_same_currency_holding_has_not_applicable_fx() -> None:
    result = apply_live_valuation(_base_snapshot(), {}, valuation_as_of="2026-07-19T08:00:00+08:00")
    cash = next(row for row in result["positions"] if row["security_id"] == "CASH_CNY")
    assert cash["fx_rate"] == 1
    assert cash["fx_status"] == "NOT_APPLICABLE_SAME_CURRENCY"


def test_pending_cost_never_enters_precise_total() -> None:
    result = apply_live_valuation(_base_snapshot(), {}, valuation_as_of="2026-07-19T08:00:00+08:00")
    assert result["total_asset_including_cost_records"] - result["total_valued_assets"] == 9000
    assert sum(result["asset_class_values"].values()) == result["total_valued_assets"]


@pytest.mark.parametrize(("metric", "frequency", "observed", "expected"), [
    ("VOO", "daily_market", "2026-07-17", "COMPARABLE"),
    ("DGS10", "daily_official", "2026-07-16", "COMPARABLE"),
    ("CPIAUCSL", "monthly", "2026-06-15", "COMPARABLE"),
    ("GDP", "quarterly", "2026-04-30", "COMPARABLE"),
    ("CPIAUCSL", "monthly", "2026-04-01", "DATA_NOT_COMPARABLE"),
    ("GDP", "quarterly", "2025-12-01", "DATA_NOT_COMPARABLE"),
])
def test_frequency_aware_comparability(metric: str, frequency: str, observed: str, expected: str) -> None:
    result = build_comparability_snapshot([{"name": metric, "success": True, "status": "ok", "frequency": frequency, "comparable_date": observed, "source": "test"}], decision_as_of="2026-07-19T08:00:00+08:00", blocking_dimensions=[])
    assert result["observations"][0]["comparability_status"] == expected


@pytest.mark.parametrize(("series", "observed", "retrieved", "status"), [
    ("DGS10", "2026-07-16", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
    ("DGS2", "2026-07-15", "2026-07-19T08:00:00-04:00", "DATA_INSUFFICIENT"),
    ("CPIAUCSL", "2026-06-01", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
    ("UNRATE", "2026-06-05", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
    ("GDP", "2026-03-31", "2026-07-19T08:00:00-04:00", "VALID_LAGGED_BY_DESIGN"),
])
def test_macro_freshness_by_release_frequency(series: str, observed: str, retrieved: str, status: str) -> None:
    assert _apply_macro_freshness(series, {"status": "ok", "value": 1, "market_date": observed, "retrieved_at": retrieved})["data_status"] == status


def _permissions(today_trade: bool) -> dict:
    results = build_dqs_results({name: [{"item": name, "score": 100}] for name in set(DQS_BINDINGS.values())})
    return build_scenario_decisions(
        dqs_results=results,
        dqs_thresholds={"scheduled_dca": 65, "scheduled_dca_normal": 75, "opportunity_add": 85, "strategic_rebalance": 75, "grid": 85, "risk_monitoring": 1, "transaction_reconciliation": 100},
        budget={"is_dca_day": True, "confirmed_cash_available_yuan": 21000}, risk={"score": 20},
        event_assessment={"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
        comparability={"core_decision_comparability": "COMPARABLE", "cross_asset_comparability": "COMPARABLE", "grid_snapshot_comparability": "COMPARABLE"},
        today_trade=today_trade,
    )


def test_no_today_trade_has_no_fake_selected_scenario() -> None:
    result = _permissions(False)
    assert result["selected_scenario"] is None
    assert result["today_trade_permission"] == "DENY"


def test_permission_enum_is_canonical() -> None:
    result = _permissions(True)
    assert {row["final_permission"] for row in result["scenarios"]} <= CANONICAL_PERMISSIONS
    assert result["automatic_trading_enabled"] is False


@pytest.mark.parametrize(("scenario", "dqs_name"), list(DQS_BINDINGS.items()))
def test_scenario_dqs_binding_configuration(scenario: str, dqs_name: str) -> None:
    assert DQS_BINDINGS[scenario] == dqs_name


def test_issue_registry_counts_pending_valuation_once() -> None:
    result = build_issue_registry({"portfolio_snapshot": {"pending_valuation_assets": [{"security_code": "VOO", "pending_reason": "MISSING_FX"}]}, "data_quality_snapshot": {}, "comparability": {}, "consistency": {"errors": [], "warnings": []}})
    assert result["warning_count"] == 0 and result["blocking_count"] == 1
    assert not ({row["issue_id"] for row in result["warnings"]} & {row["issue_id"] for row in result["blocking"]})


def test_simulation_cash_is_excluded() -> None:
    snapshot = {"holdings": [{"asset_id": "cash", "security_code": "CASH_CNY", "asset_class": "现金", "currency": "CNY", "market_value_cny": 241000}, {"asset_id": "sim", "reference_symbol": "SIM", "asset_class": "模拟现金", "market_value_cny": 200000, "is_cost_record": True}], "asset_class_values": {"现金": 241000, "模拟现金": 0}}
    result = apply_live_valuation(snapshot, {}, valuation_as_of="2026-07-19T08:00:00+08:00")
    assert result["asset_class_values"]["现金"] == 241000
    assert result["asset_class_values"]["模拟现金"] == 0


def test_source_registry_supplies_metadata_defaults() -> None:
    definition = DataSourceRegistry.load().source("yfinance").to_dict()
    assert {"provider_name", "source_tier", "supported_markets", "freshness_policy", "fallback_order", "enabled", "health_status"} <= definition.keys()


def test_version_entrypoint_and_cash_policy_remain_frozen() -> None:
    root = project_root()
    assert "Stone AI Investment Manager Pro V12.7.1 Final Freeze" in (root / "src/decision/v12_1_decision.py").read_text(encoding="utf-8")
    assert "python main.py" in (root / ".github/workflows/daily.yml").read_text(encoding="utf-8")
    assert "from src.pipeline.unified_pipeline import main" in (root / "main.py").read_text(encoding="utf-8")
    strategy = load_config(root / "config/strategy.yaml")
    assert strategy["patch_level"] == "root_fix_1"
    master = load_config(root / "data/portfolio_master.yaml")
    assert float(master["cash_policy"]["safety_reserve_cny"]) == 220000
