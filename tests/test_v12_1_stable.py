from __future__ import annotations

import json
import unittest

from src.decision.v12_1_decision import (
    build_budget_plan,
    build_migration_plan,
    build_opportunity_scores,
    build_v12_1_decision,
    compute_dqs,
    compute_risk_score,
    enrich_allocation,
    load_strategy,
)
from src.reports.report_center import generate_daily_report


def _portfolio() -> dict:
    return {
        "total_assets_wan": 282.11,
        "category_amounts": {
            "美股": 38.5,
            "港股": 27.26,
            "A股": 26.65,
            "债券": 113.0,
            "黄金": 54.7,
            "现金": 22.0,
        },
        "categories": [],
    }


def _quote(value: float, source: str = "alpha_vantage", second: str | None = "finnhub") -> dict:
    candidates = [
        {
            "close": value,
            "previous_close": value * 0.99,
            "change_pct": 1.0,
            "status": "ok",
            "source": source,
            "published_at": "2026-07-11T00:00:00",
            "retrieved_at": "2026-07-11T00:05:00",
            "fetched_at": "2026-07-11T00:05:00",
            "freshness_status": "fresh",
        }
    ]
    if second:
        candidates.append({**candidates[0], "close": value * 1.001, "source": second})
    return {**candidates[0], "candidates": candidates}


def _live_market(dual: bool = True, missing: bool = False) -> dict:
    second = "finnhub" if dual else None
    failed = {"status": "failed", "source": "unavailable", "close": None, "error": "请求失败"}
    items = {
        "VOO": failed if missing else _quote(500, "alpha_vantage", second),
        "QQQ": _quote(480, "alpha_vantage", second),
        "TLT": _quote(90, "alpha_vantage", second),
        "GLD": _quote(220, "alpha_vantage", second),
        "^VIX": _quote(18, "cboe_official", "yfinance" if dual else None),
        "3067.HK": _quote(8, "finnhub", "yfinance" if dual else None),
        "510300.SS": _quote(4, "finnhub", "yfinance" if dual else None),
        "DX-Y.NYB": _quote(104, "yfinance", None),
        "^GSPC": _quote(5600, "yfinance", None),
        "^IXIC": _quote(18000, "yfinance", None),
    }
    macro_items = {
        "DGS10": {"value": 4.2, "status": "ok", "source": "fred", "date": "2026-07-11", "fetched_at": "2026-07-11T00:05:00"},
        "CPIAUCSL": {"value": 320, "status": "ok", "source": "fred", "date": "2026-07-11", "fetched_at": "2026-07-11T00:05:00"},
        "UNRATE": {"value": 4.1, "status": "ok", "source": "fred", "date": "2026-07-11", "fetched_at": "2026-07-11T00:05:00"},
        "GDP": {"value": 29000, "status": "ok", "source": "fred", "date": "2026-07-11", "fetched_at": "2026-07-11T00:05:00"},
    }
    return {"items": items, "macro": {"items": macro_items}, "fetched_at": "2026-07-11T00:05:00"}


class V121StableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = load_strategy()
        self.allocation = enrich_allocation(_portfolio(), self.strategy)
        self.live = _live_market()
        self.dqs = compute_dqs(self.live, self.strategy)
        self.risk = compute_risk_score(self.live, {"has_high_event_next_7_days": False}, self.dqs, self.strategy)
        self.opportunity = build_opportunity_scores(self.allocation, self.live, self.strategy)

    def test_asset_total_calculation(self) -> None:
        self.assertEqual(sum(row["current_amount_yuan"] for row in self.allocation), 2821100)

    def test_asset_ratio_sum_is_about_100_percent(self) -> None:
        self.assertAlmostEqual(sum(row["current_ratio"] for row in self.allocation), 1.0, places=4)

    def test_target_deviation_calculation(self) -> None:
        us = next(row for row in self.allocation if row["category"] == "美股")
        self.assertLess(us["deviation_ratio"], -0.05)
        self.assertEqual(us["status"], "严重低配")

    def test_status_column_never_empty(self) -> None:
        self.assertTrue(all(row["status"] for row in self.allocation))

    def test_cash_floor_blocks_cash_buy(self) -> None:
        budget = build_budget_plan(self.allocation, self.dqs, self.risk, {"has_high_event_next_7_days": False}, self.opportunity, self.strategy)
        self.assertEqual(budget["confirmed_cash_available_yuan"], 0)
        self.assertEqual(budget["today_total_yuan"], 0)

    def test_today_week_month_budget_consistency(self) -> None:
        budget = build_budget_plan(self.allocation, self.dqs, self.risk, {"has_high_event_next_7_days": False}, self.opportunity, self.strategy)
        self.assertLessEqual(budget["today_total_yuan"], budget["week_confirmed_yuan"])
        self.assertLessEqual(budget["week_confirmed_yuan"], budget["month_confirmed_yuan"])

    def test_bond_to_equity_budget_is_conditional(self) -> None:
        budget = build_budget_plan(self.allocation, self.dqs, self.risk, {"has_high_event_next_7_days": False}, self.opportunity, self.strategy)
        self.assertGreater(budget["conditional_bond_to_equity_month_yuan"], 0)
        self.assertEqual(budget["funding_note"].startswith("未到账债券"), True)

    def test_dca_date_rule_present(self) -> None:
        budget = build_budget_plan(self.allocation, self.dqs, self.risk, {"has_high_event_next_7_days": False}, self.opportunity, self.strategy)
        self.assertIn("next_dca_date", budget)
        self.assertIsInstance(budget["is_dca_day"], bool)

    def test_opportunity_scores_exist(self) -> None:
        names = {row["name"] for row in self.opportunity}
        self.assertIn("VOO", names)
        self.assertIn("黄金", names)

    def test_dqs_components_sum_to_score_before_caps_or_higher(self) -> None:
        component_sum = sum(row["score"] for row in self.dqs["components"])
        self.assertGreaterEqual(component_sum, self.dqs["score"])

    def test_risk_components_sum_to_score(self) -> None:
        self.assertEqual(sum(row["score"] for row in self.risk["components"]), self.risk["score"])

    def test_dual_source_verification(self) -> None:
        self.assertGreater(self.dqs["dual_source_coverage"], 0)

    def test_data_missing_does_not_display_zero(self) -> None:
        dqs = compute_dqs(_live_market(missing=True), self.strategy)
        self.assertIn("VOO", dqs["missing_metrics"])

    def test_missing_dual_source_caps_dqs_mode(self) -> None:
        dqs = compute_dqs(_live_market(dual=False), self.strategy)
        self.assertLessEqual(dqs["score"], 74)
        self.assertIn(dqs["mode"], {"direction", "safe"})

    def test_migration_plan_has_12_months(self) -> None:
        budget = build_budget_plan(self.allocation, self.dqs, self.risk, {"has_high_event_next_7_days": False}, self.opportunity, self.strategy)
        plan = build_migration_plan(self.allocation, budget)
        self.assertEqual(len(plan["months"]), 12)
        self.assertGreater(plan["theoretical_transfer_yuan"], 0)

    def test_decision_contains_required_sections(self) -> None:
        decision = build_v12_1_decision(
            portfolio_result=_portfolio(),
            live_market_result=self.live,
            macro_result={"has_high_event_next_7_days": False, "upcoming_events": []},
            ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则模式"},
        )
        for key in ["budget", "opportunity", "risk", "dqs", "migration_plan", "holding_diagnostics", "scenarios"]:
            self.assertIn(key, decision)

    def test_no_trade_has_no_actionable_targets(self) -> None:
        decision = build_v12_1_decision(
            portfolio_result=_portfolio(),
            live_market_result=_live_market(dual=False),
            macro_result={"has_high_event_next_7_days": False, "upcoming_events": []},
            ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则模式"},
        )
        self.assertFalse(decision["today_trade"])
        self.assertEqual(decision["targets"], "不适用")

    def test_decision_can_be_serialized_to_json(self) -> None:
        decision = build_v12_1_decision(
            portfolio_result=_portfolio(),
            live_market_result=self.live,
            macro_result={"has_high_event_next_7_days": False, "upcoming_events": []},
            ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则模式"},
        )
        encoded = json.dumps(decision, ensure_ascii=False, default=str)
        self.assertIn("Stone AI Investment Manager Pro V12.6 Stable", encoded)

    def test_report_fields_complete(self) -> None:
        decision = build_v12_1_decision(
            portfolio_result=_portfolio(),
            live_market_result=self.live,
            macro_result={"has_high_event_next_7_days": False, "upcoming_events": []},
            ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则模式"},
        )
        report = generate_daily_report(decision=decision)
        self.assertIn("## 18. 一致性验证", report)
        self.assertNotIn("|  |", report)

    def test_suggestion_does_not_violate_dqs(self) -> None:
        decision = build_v12_1_decision(
            portfolio_result=_portfolio(),
            live_market_result=_live_market(dual=False),
            macro_result={"has_high_event_next_7_days": False, "upcoming_events": []},
            ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则模式"},
        )
        if decision["dqs"]["mode"] in {"direction", "safe"}:
            self.assertEqual(decision["budget"]["today_total_yuan"], 0)

    def test_report_amount_not_above_available_cash(self) -> None:
        budget = build_budget_plan(self.allocation, self.dqs, self.risk, {"has_high_event_next_7_days": False}, self.opportunity, self.strategy)
        self.assertLessEqual(budget["today_total_yuan"], budget["confirmed_cash_available_yuan"])

    def test_risk_score_level_is_present(self) -> None:
        self.assertIn(self.risk["level"], {"低风险", "中低风险", "中高风险", "高风险", "极高风险"})


if __name__ == "__main__":
    unittest.main()
