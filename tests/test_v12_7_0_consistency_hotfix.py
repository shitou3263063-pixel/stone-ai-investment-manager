from __future__ import annotations

from src.decision.v12_1_decision import (
    build_report_metadata,
    build_trade_permission_gates,
    build_trade_reconciliation_summary,
    compute_risk_score,
    load_strategy,
    update_comparability_summary,
)


def _trade(**overrides):
    row = {
        "id": "USERCONF-20260715-VOO-001",
        "symbol": "VOO",
        "trade_date": "2026-07-15",
        "trade_datetime": None,
        "quantity": None,
        "execution_price_usd": 700,
        "trade_currency": "USD",
        "funding_currency": "CNY",
        "trade_amount_usd": 1400,
        "actual_fx_rate_cny_per_usd": None,
        "valuation_fx_rate_cny_per_usd": 7.2,
        "fx_status": "ACTUAL_FX_REQUIRED",
        "fee": None,
    }
    row.update(overrides)
    return row


def _snapshot(trade):
    return {
        "confirmed_transactions": [trade],
        "asset_class_totals": {
            "美股": 339000,
            "港股": 272600,
            "A股": 266500,
            "债券": 1155000,
            "黄金": 547000,
            "现金": 241000,
        },
        "holdings": [
            {"security_code": "VOO", "quantity": 28, "market_value_cny": 130000},
            {"security_code": "VOO_PENDING_20260715", "reference_symbol": "VOO", "market_value_cny": 9000},
        ],
    }


def test_manual_reconciliation_uses_run_date_not_trade_date() -> None:
    metadata = build_report_metadata(
        generated_at="2026-07-16T21:06:00+08:00",
        decision_cutoff_at="2026-07-16T21:05:00+08:00",
        transactions=[_trade()],
        run_label="手动运行",
        explicit_run_mode="MANUAL_RECONCILIATION",
    )
    assert metadata["report_business_date"] == "2026-07-16"
    assert metadata["report_generated_at"] == "2026-07-16T21:06:00+08:00"
    assert metadata["decision_cutoff_at"] == "2026-07-16T21:05:00+08:00"
    assert metadata["actual_trade_date"] == "2026-07-15"
    assert metadata["report_run_mode"] == "MANUAL_RECONCILIATION"


def test_dqs_gate_is_separate_from_final_trade_permission() -> None:
    dqs = {"use_cases": {"scheduled_dca": {"score": 75, "threshold": 65, "normal_execution_dqs": 75}}}
    gates = build_trade_permission_gates(
        dqs,
        {"is_dca_day": False, "confirmed_cash_available_yuan": 21000},
        {"score": 45},
        {"status": "VALID_NO_HIGH_IMPACT_EVENT", "event_gate_passed": True, "reasons": []},
    )
    scheduled = gates["contexts"]["scheduled_dca"]
    assert scheduled["dqs_gate_passed"] is True
    assert scheduled["schedule_gate_passed"] is False
    assert scheduled["cash_gate_passed"] is True
    assert scheduled["risk_gate_passed"] is True
    assert gates["final_trade_permission"] is False
    assert scheduled["rejection_reasons"]


def test_market_risk_weights_sum_to_100_and_missing_breadth_is_neutral() -> None:
    risk = compute_risk_score(
        {"items": {}, "macro": {"items": {}}, "market_context_status": {"indicators": []}},
        {},
        {
            "score": 75,
            "market_coverage": 0.0,
            "mode_label": "只允许金额区间和分批计划",
            "components": [],
            "stale_metrics": [],
            "missing_metrics": [],
            "transaction_reconciliation": [],
        },
        load_strategy(),
    )
    components = risk["market_risk"]["components"]
    assert sum(row["weight"] for row in components) == 100
    assert risk["market_risk_weights_sum"] == 100
    breadth = next(row for row in components if row["item"] == "市场宽度与资金流")
    assert breadth["weight"] == 15
    assert breadth["score"] == 8
    assert breadth["confidence"] == "low"
    assert breadth["data_status"] == "MISSING_NEUTRAL"


def test_three_comparability_scopes_share_one_non_comparable_count() -> None:
    decision = {
        "risk": {"market_time_consistency": {"comparable": False, "symbols": ["VOO", "QQQ"]}},
        "market_table": [
            {"name": "VOO", "success": True, "comparable_date": "2026-07-15"},
            {"name": "DGS10", "success": True, "comparable_date": "2026-07-14"},
        ],
        "dqs": {},
        "grid": {
            "decision_snapshot": {"snapshot_comparable": False},
            "symbols": {"VOO": {"signal": {"raw_signal": "DATA_NOT_COMPARABLE"}}},
        },
    }
    update_comparability_summary(decision)
    summary = decision["comparability"]
    assert summary["core_decision_comparability"] == "DATA_NOT_COMPARABLE"
    assert summary["cross_asset_comparability"] == "DATA_NOT_COMPARABLE"
    assert summary["grid_snapshot_comparability"] == "DATA_NOT_COMPARABLE"
    assert summary["non_comparable_items_count"] == len(summary["non_comparable_items"])
    assert decision["dqs"]["non_comparable_items_count"] == summary["non_comparable_items_count"]
    assert any(item.startswith("grid_snapshot:") for item in summary["non_comparable_items"])


def test_missing_trade_fields_remain_warn_without_estimation() -> None:
    summary = build_trade_reconciliation_summary(_snapshot(_trade()), {"items": {"VOO": {"current_price": 700}}})
    assert summary["status"] == "WARN"
    assert set(summary["missing_fields"]) == {"trade_datetime", "quantity", "actual_fx_rate_cny_per_usd", "fee"}
    assert summary["auto_recalculated"] is False
    assert summary["transactions"][0]["position_total_quantity"] is None


def test_completed_trade_fields_are_reconciled_without_report_recalculation() -> None:
    trade = _trade(trade_datetime="2026-07-15T10:30:00-04:00", quantity=2, actual_fx_rate_cny_per_usd=7.2, fee=5)
    summary = build_trade_reconciliation_summary(_snapshot(trade), {"items": {"VOO": {"current_price": 700}}})
    assert summary["status"] == "PASS"
    assert summary["transaction_reconciliation_quality"] == 100
    assert summary["auto_recalculated"] is False
    assert summary["transactions"][0]["actual_fx_rate_cny_per_usd"] == 7.2
