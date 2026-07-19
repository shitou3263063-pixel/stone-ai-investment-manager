from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.analysis.scenario_analysis import calculate_portfolio_stress_scenarios
from src.data_sources.decision_time import filter_market_for_cutoff, item_time_metadata
from src.decision.v12_1_decision import (
    build_budget_plan,
    build_migration_plan,
    build_opportunity_groups,
    build_opportunity_scores,
    build_stress_exposures,
    compute_dqs,
    compute_risk_score,
    enrich_allocation,
    load_strategy,
    scheduled_dca_event_window_policy,
)
from src.grid.grid_engine import build_grid_decision_snapshot
from src.macro.macro_calendar import classify_event_status, get_upcoming_high_risk_events
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.grid_report import generate_grid_daily_section


def _point(value: float, *, session: str = "official_close", observed: str = "2026-07-15T20:00:00+00:00") -> dict:
    return {"close": value, "value": value, "status": "ok", "source": "fred", "observed_at_utc": observed,
            "source_timezone": "UTC", "data_session": session, "market_date": "2026-07-15", "freshness_status": "fresh"}


def _live(*, stale: bool = False) -> dict:
    items = {symbol: _point(100 + index) for index, symbol in enumerate(["VOO", "QQQ", "TLT", "GLD", "^VIX", "DX-Y.NYB", "03033.HK", "510300.SS", "002558.SZ", "513060.SS", "513090.SS"])}
    if stale:
        items["VOO"]["freshness_status"] = "stale"
    return {"items": items, "macro": {"items": {name: _point(4.0) for name in ["DGS10", "CPIAUCSL", "UNRATE", "GDP"]}}}


def _budget_plan() -> dict:
    snapshot, strategy, live = build_portfolio_snapshot(), load_strategy(), _live()
    allocation = enrich_allocation(
        {
            "total_assets_wan": snapshot["total_assets"] / 10000,
            "category_amounts": {key: value / 10000 for key, value in snapshot["asset_class_totals"].items()},
        },
        strategy,
    )
    dqs = compute_dqs(live, strategy)
    risk = compute_risk_score(live, {"has_high_event_next_7_days": False}, dqs, strategy)
    opportunity = build_opportunity_scores(allocation, live, strategy)
    return build_budget_plan(allocation, dqs, risk, {"has_high_event_next_7_days": False}, opportunity, strategy)


def test_01_tlt_is_bond_not_us_equity() -> None:
    tlt = next(row for row in build_portfolio_snapshot()["holdings"] if row["security_code"] == "TLT")
    assert tlt["asset_class"] == "债券"


def test_02_tlt_listing_and_allocation_are_distinct() -> None:
    tlt = next(row for row in build_portfolio_snapshot()["holdings"] if row["security_code"] == "TLT")
    assert (tlt["listing_market"], tlt["allocation_bucket"]) == ("US", "bonds")


def test_03_china_bond_children_are_counted_once() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["holding_class_totals"]["债券"] == snapshot["asset_class_totals"]["债券"]


def test_04_allocation_equals_total_assets() -> None:
    snapshot, strategy = build_portfolio_snapshot(), load_strategy()
    portfolio = {"total_assets_wan": snapshot["total_assets"] / 10000, "category_amounts": {key: value / 10000 for key, value in snapshot["asset_class_totals"].items()}}
    assert sum(row["current_amount_yuan"] for row in enrich_allocation(portfolio, strategy)) == snapshot["total_assets"]


def test_05_tlt_reclassification_changes_bond_deviation() -> None:
    snapshot, strategy = build_portfolio_snapshot(), load_strategy()
    portfolio = {"total_assets_wan": snapshot["total_assets"] / 10000, "category_amounts": {key: value / 10000 for key, value in snapshot["asset_class_totals"].items()}}
    bonds = next(row for row in enrich_allocation(portfolio, strategy) if row["category"] == "债券")
    assert bonds["current_amount_yuan"] == 1_155_000 and bonds["deviation_amount_yuan"] > 0


def test_06_migration_uses_corrected_bond_total() -> None:
    plan = build_migration_plan([{"category": "债券", "current_amount_yuan": 1_155_000, "target_amount_yuan": 705_275}], {"conditional_bond_to_equity_month_yuan": 30_000})
    assert plan["theoretical_transfer_yuan"] == 449_725 and plan["theoretical_full_months"] == 15


def test_07_post_cutoff_quote_is_excluded() -> None:
    cutoff = datetime(2026, 7, 15, 20, tzinfo=ZoneInfo("UTC"))
    filtered = filter_market_for_cutoff({"items": {"VOO": _point(100, observed="2026-07-15T20:01:00+00:00")}, "macro": {"items": {}}}, cutoff)
    assert "VOO" not in filtered["items"] and filtered["decision_timing"]["post_cutoff_data"]


def test_08_intraday_is_not_official_close() -> None:
    assert item_time_metadata(_point(100, session="intraday_delayed"))["data_stage"] == "INTRADAY"


def test_09_cutoff_has_real_timezone_timestamp() -> None:
    filtered = filter_market_for_cutoff({"items": {}, "macro": {"items": {}}}, datetime.now(tz=ZoneInfo("Asia/Shanghai")))
    assert "T" in filtered["decision_timing"]["report_generation_time"] and "+08:00" in filtered["decision_timing"]["report_generation_time"]


def test_10_released_event_is_not_upcoming() -> None:
    event = {"release_at_utc": "2026-07-15T12:30:00+00:00"}
    assert classify_event_status(event, datetime(2026, 7, 15, 13, tzinfo=ZoneInfo("UTC"))) == "RELEASED_FETCH_FAILED"


def test_11_next_event_selector_ignores_released_event() -> None:
    events = [{"risk_level": "high", "verification_status": "verified", "release_at_utc": "2026-07-15T12:30:00+00:00"}]
    assert not get_upcoming_high_risk_events(datetime(2026, 7, 15, 13, tzinfo=ZoneInfo("UTC")), hours=48, events=events)


def test_12_risk_is_split_into_four_dimensions() -> None:
    risk = compute_risk_score(_live(), {"has_high_event_next_7_days": False}, compute_dqs(_live(), load_strategy()), load_strategy())
    assert {"market_risk", "portfolio_risk", "data_confidence", "execution_risk"} <= set(risk)


def test_13_stale_quote_reduces_timeliness() -> None:
    strategy = load_strategy()
    fresh, stale = compute_dqs(_live(), strategy), compute_dqs(_live(stale=True), strategy)
    assert next(row for row in stale["components"] if row["item"] == "timeliness")["score"] < next(row for row in fresh["components"] if row["item"] == "timeliness")["score"]


def test_14_usd_cash_trade_is_reconciled_without_actual_fx() -> None:
    dqs = compute_dqs(_live(), load_strategy())
    assert dqs["transaction_reconciliation"][0]["status"] == "RECONCILED"


def test_15_pending_trade_blocks_valuation_readiness() -> None:
    dqs = compute_dqs(_live(), load_strategy())
    assert not dqs["valuation_readiness"]["ready"]


def test_16_opportunity_is_grouped() -> None:
    snapshot, strategy = build_portfolio_snapshot(), load_strategy()
    allocation = enrich_allocation({"total_assets_wan": snapshot["total_assets"] / 10000, "category_amounts": {k: v / 10000 for k, v in snapshot["asset_class_totals"].items()}}, strategy)
    groups = build_opportunity_groups(allocation, build_opportunity_scores(allocation, _live(), strategy))
    assert set(groups) == {"strategic_allocation", "core_etf", "satellite_holding"}


def test_17_cash_is_not_in_satellite_cross_section() -> None:
    snapshot, strategy = build_portfolio_snapshot(), load_strategy()
    allocation = enrich_allocation({"total_assets_wan": snapshot["total_assets"] / 10000, "category_amounts": {k: v / 10000 for k, v in snapshot["asset_class_totals"].items()}}, strategy)
    groups = build_opportunity_groups(allocation, build_opportunity_scores(allocation, _live(), strategy))
    assert all(row["name"] != "现金" for row in groups["satellite_holding"])


def test_18_equity_bond_drawdown_is_static_stress_test() -> None:
    rows = calculate_portfolio_stress_scenarios([{"category": "美股", "current_amount_yuan": 100}, {"category": "普通债券", "current_amount_yuan": 100}], {"equity_bond_drawdown": {"美股": -0.3, "普通债券": -0.05}})
    scenario = next(row for row in rows if row["key"] == "equity_bond_drawdown")
    assert scenario["portfolio_change_yuan"] == -35 and scenario["note"].startswith("静态")


def test_19_grid_report_declares_simulation_only() -> None:
    assert "SIMULATION_ONLY = true" in generate_grid_daily_section({"enabled": True, "symbols": {}, "grid_budget": {}})


def test_20_intraday_quote_cannot_form_grid_anchor() -> None:
    snapshot = build_grid_decision_snapshot({"items": {"VOO": _point(100, session="intraday_delayed"), "QQQ": _point(100, session="intraday_delayed")}})
    assert not snapshot["snapshot_comparable"]


def test_21_confirmed_trade_is_not_system_recommendation() -> None:
    assert compute_dqs(_live(), load_strategy())["transaction_reconciliation"][0]["status"] == "RECONCILED"


def test_22_conditional_budget_is_not_real_cash() -> None:
    snapshot = build_portfolio_snapshot()
    assert snapshot["cash"]["unsettled_conditional_cash_cny"] == 0


def test_23_fixed_cash_reserve_is_not_occupied() -> None:
    cash = build_portfolio_snapshot()["cash"]
    assert cash["cash_safety_reserve_cny"] == 220_000 and cash["investable_cash_cny"] == 21_000


def test_24_paper_grid_cash_is_excluded_from_real_cash() -> None:
    cash = build_portfolio_snapshot()["cash"]
    assert cash["paper_grid_cash_cny"] == 0 and cash["live_grid_cash_cny"] == 0


def test_25_security_master_does_not_infer_equity_from_us_listing() -> None:
    tlt = next(row for row in build_portfolio_snapshot()["holdings"] if row["security_code"] == "TLT")
    assert tlt["instrument_asset_class"] == "fixed_income_etf" and tlt["listing_market"] == "US"


def test_26_wednesday_plan_is_scheduled_base_dca() -> None:
    trade = build_portfolio_snapshot()["confirmed_transactions"][0]
    assert trade["trade_date"] == "2026-07-15"
    assert trade["order_type"] == "base_dca"
    assert trade["trade_origin"] == "SCHEDULED_BASE_DCA"
    assert trade["execution_status"] == "USER_CONFIRMED_EXECUTED"
    assert trade["system_pre_authorized"] is True


def test_27_scheduled_dca_is_not_discretionary_or_event_chasing() -> None:
    trade = build_portfolio_snapshot()["confirmed_transactions"][0]
    assert trade["opportunity_add"] is False
    assert trade["discretionary_trade"] is False
    assert trade["event_chasing"] is False
    assert trade["simulation_trade"] is False


def test_28_base_dca_and_bond_funding_count_trade_once() -> None:
    budget = _budget_plan()
    base = next(row for row in budget["rows"] if row["budget_id"] == "BUDGET_BASE_DCA")
    migration = next(row for row in budget["rows"] if row["budget_id"] == "ACTUAL_BOND_TO_EQUITY_20260715")
    counted_total = sum(row["amount_yuan"] for row in budget["rows"] if row.get("counts_toward_actual_trade_total"))
    assert base["amount_yuan"] == 9000 and base["counts_toward_actual_trade_total"] is True
    assert migration["amount_yuan"] == 0 and migration["attributed_amount_yuan"] == 9000
    assert migration["counts_toward_actual_trade_total"] is False
    assert counted_total == 9000


def test_29_dca_budget_migration_cap_and_cash_change_reconcile() -> None:
    budget = _budget_plan()
    conditional = next(row for row in budget["rows"] if row["budget_id"] == "BUDGET_CONDITIONAL_BOND_TO_EQUITY")
    opportunity = next(row for row in budget["rows"] if row["budget_id"] == "BUDGET_OPPORTUNITY_ADD")
    assert budget["actual_bond_cash_arrived_yuan"] == 30000
    assert budget["base_dca_executed_yuan"] == 9000
    assert budget["bond_migration_attributed_yuan"] == 9000
    assert budget["bond_to_equity_remaining_this_month_yuan"] == 21000
    assert budget["account_total_cash_yuan"] == 241000
    assert budget["cash_safety_reserve_yuan"] == 220000
    assert budget["investable_cash_yuan"] == 21000
    assert conditional["amount_yuan"] == 21000 and conditional["execute"] is False
    assert opportunity["amount_yuan"] == 0 and opportunity["execute"] is False


def test_30_preapproved_dca_event_window_rule_is_explicit_and_not_retroactive() -> None:
    assert scheduled_dca_event_window_policy(already_executed=False, in_event_window=True) == "PAUSE_AND_REVIEW_BEFORE_EXECUTION"
    assert scheduled_dca_event_window_policy(already_executed=False, in_event_window=False) == "ELIGIBLE_SUBJECT_TO_STANDARD_GATES"
    assert scheduled_dca_event_window_policy(already_executed=True, in_event_window=True) == "PRE_AUTHORIZED_EXECUTED_NO_RETROACTIVE_RECLASSIFICATION"
