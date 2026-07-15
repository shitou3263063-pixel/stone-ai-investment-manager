from __future__ import annotations

from src.decision.v12_1_decision import build_v12_1_decision
from src.macro.macro_calendar import analyze_macro_calendar
from src.portfolio_snapshot import build_portfolio_snapshot


def _quote(value: float) -> dict:
    return {
        "close": value,
        "previous_close": value * 0.99,
        "change_pct": 0.5,
        "status": "ok",
        "source": "yfinance",
        "published_at": "2026-07-10T00:00:00",
        "fetched_at": "2026-07-12T00:00:00",
        "freshness_status": "fresh",
        "data_session": "previous_close",
        "comparable_date": "2026-07-10",
        "market_timezone": "America/New_York",
    }


def _live_market() -> dict:
    return {
        "items": {
            "VOO": _quote(690),
            "QQQ": _quote(720),
            "NVDA": _quote(160),
            "GOOG": _quote(260),
            "BABA": _quote(110),
            "IBKR": _quote(60),
            "XLF": _quote(50),
            "TLT": _quote(84),
            "GLD": _quote(377),
            "^VIX": _quote(15),
            "03033.HK": _quote(10),
            "002558.SZ": _quote(10),
            "513060.SS": _quote(1),
            "513090.SS": _quote(1),
            "510300.SS": _quote(4.8),
            "DX-Y.NYB": _quote(101),
        },
        "macro": {"items": {}},
        "fetched_at": "2026-07-12T00:00:00",
    }


def _portfolio_result(snapshot: dict) -> dict:
    category_amounts = {key: value / 10000 for key, value in snapshot["asset_class_totals"].items()}
    return {
        "total_assets_wan": snapshot["total_assets"] / 10000,
        "category_amounts": category_amounts,
        "holdings": [
            {
                "category": row["asset_class"],
                "name": row["security_name"],
                "amount_wan": row["market_value_cny"] / 10000,
            }
            for row in snapshot["holdings"]
        ],
    }


def _decision() -> dict:
    snapshot = build_portfolio_snapshot()
    return build_v12_1_decision(
        portfolio_result=_portfolio_result(snapshot),
        live_market_result=_live_market(),
        macro_result=analyze_macro_calendar(today=__import__("datetime").date.today()),
        ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则增强模式"},
    )


def test_portfolio_snapshot_totals_are_reconciled() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["total_assets"] == 2821100
    assert sum(row["market_value_cny"] for row in snapshot["holdings"]) == 2821100
    assert snapshot["asset_class_totals"] == snapshot["holding_class_totals"]


def test_cash_buckets_are_explicit_and_match_user_confirmed_fixed_reserve() -> None:
    cash = build_portfolio_snapshot()["cash"]
    assert cash["account_total_cash_cny"] == 241000
    assert cash["cash_safety_reserve_cny"] == 220000
    assert cash["cash_safety_reserve_mode"] == "fixed_user_confirmed"
    assert cash["investable_cash_cny"] == 21000
    assert cash["bond_to_equity_investable_cash_cny"] == 21000
    assert cash["paper_grid_cash_cny"] == 0


def test_gold_total_equals_gold_details() -> None:
    gold = build_portfolio_snapshot()["gold"]
    assert gold["class_total_cny"] == 547000
    assert gold["detail_total_cny"] == 547000
    assert gold["reconciled"] is True


def test_opportunity_score_uses_real_holdings_not_proxy_tickers() -> None:
    decision = _decision()
    opportunity = {row["name"]: row for row in decision["opportunity"]}
    assert opportunity["南方东英恒生科技指数ETF"]["current_holding_yuan"] == 140400
    assert opportunity["沪深300ETF"]["current_holding_yuan"] == 206000
    assert opportunity["黄金"]["current_holding_yuan"] == 547000
    assert opportunity["现金"]["current_holding_yuan"] == 241000


def test_overweight_gold_and_bonds_do_not_generate_add_advice() -> None:
    decision = _decision()
    opportunity = {row["name"]: row for row in decision["opportunity"]}
    assert "新增" in opportunity["黄金"]["advice"]
    assert "新增" in opportunity["TLT"]["advice"]
    assert opportunity["黄金"]["advice"] == "暂停新增"


def test_us_stock_underweight_does_not_auto_add_single_stocks() -> None:
    decision = _decision()
    opportunity = {row["name"]: row for row in decision["opportunity"]}
    for name in ["NVDA", "GOOG", "BABA", "IBKR"]:
        assert opportunity[name]["advice"] in {"继续持有", "观察"}


def test_dqs_safe_mode_downgrades_actionable_opportunities() -> None:
    decision = _decision()
    assert decision["dqs"]["mode"] == "safe"
    opportunity = {row["name"]: row for row in decision["opportunity"]}
    assert opportunity["VOO"]["advice"] == "等待条件，今日不交易"
    assert decision["today_trade"] is False
    assert decision["budget"]["today_total_yuan"] == 0


def test_consistency_validation_is_real_pass() -> None:
    decision = _decision()
    assert decision["consistency"]["status"] == "WARN"
    assert decision["consistency"]["errors"] == []


def test_conditional_bond_plan_is_not_cash() -> None:
    decision = _decision()
    budget = decision["budget"]
    assert budget["conditional_bond_to_equity_month_yuan"] == 30000
    assert budget["approved_bond_to_equity_month_yuan"] == 30000
    assert budget["actual_bond_cash_arrived_yuan"] == 30000
    assert budget["bond_to_equity_executed_this_month_yuan"] == 9000
    assert budget["bond_to_equity_remaining_this_month_yuan"] == 21000
    assert budget["investable_cash_yuan"] == 21000


def test_voo_trade_keeps_unknown_execution_fields_and_does_not_fake_market_value() -> None:
    snapshot = build_portfolio_snapshot()
    original = next(row for row in snapshot["holdings"] if row["asset_id"] == "us_voo")
    pending = next(row for row in snapshot["holdings"] if row["asset_id"] == "us_voo_20260715_pending_valuation")
    trade = snapshot["confirmed_transactions"][0]
    assert original["market_value_cny"] == 130000
    assert original["quantity"] == 28
    assert pending["market_value_cny"] == 9000
    assert pending["valuation_status"] == "pending_actual_quantity_fx_fee"
    assert trade["quantity"] is None
    assert trade["actual_fx_rate_cny_per_usd"] is None
    assert trade["fee"] is None
    assert trade["real_trade"] is True
    assert trade["simulation_trade"] is False
