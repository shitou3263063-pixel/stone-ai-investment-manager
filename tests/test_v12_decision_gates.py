from __future__ import annotations

import unittest

from src.decision.unified_decision import build_unified_decision
from src.validators.decision_validator import conservative_decision, validate_decision


def _portfolio(cash_wan: float = 22.0) -> dict:
    return {
        "total_assets_wan": 282.11,
        "category_amounts": {
            "美股": 38.5,
            "港股": 27.26,
            "A股": 26.65,
            "债券": 113.0,
            "黄金": 54.7,
            "现金": cash_wan,
        },
        "categories": [
            {"category": "美股", "current_ratio": 0.136, "target_ratio": 0.30, "deviation_ratio": -0.164, "status": "低配"},
            {"category": "港股", "current_ratio": 0.097, "target_ratio": 0.12, "deviation_ratio": -0.023, "status": "略低"},
            {"category": "A股", "current_ratio": 0.094, "target_ratio": 0.10, "deviation_ratio": -0.006, "status": "接近"},
            {"category": "债券", "current_ratio": 0.401, "target_ratio": 0.25, "deviation_ratio": 0.151, "status": "超配"},
            {"category": "黄金", "current_ratio": 0.194, "target_ratio": 0.15, "deviation_ratio": 0.044, "status": "超配"},
            {"category": "现金", "current_ratio": cash_wan / 282.11, "target_ratio": 0.08, "deviation_ratio": cash_wan / 282.11 - 0.08, "status": "正常"},
        ],
    }


def _base_kwargs(score: int = 90, risk: int = 55, cash_wan: float = 22.0) -> dict:
    return {
        "portfolio_result": _portfolio(cash_wan),
        "market_result": {"market_risk_score": risk, "summary": "test"},
        "live_market_result": {
            "data_quality": {
                "score": score,
                "blocking_errors": [],
                "source_audit": {
                    "data_source_coverage": 0.9,
                    "dual_source_coverage": 0.8,
                    "tier1_coverage": 0.8,
                },
            },
        },
        "macro_result": {"has_high_event_next_7_days": False, "upcoming_events": []},
        "allocation_rebalance_result": {"need_rebalance": True},
        "execution_plan_result": {
            "today_buy_wan": 0.4,
            "week_buy_wan": 1.0,
            "month_buy_wan": 2.5,
            "pause_list": ["债券", "黄金", "TLT"],
        },
        "ai_advice_result": {"ai_status": "available", "actual_provider": "openai"},
    }


class DecisionGateTest(unittest.TestCase):
    def test_dqs_70_79_outputs_no_amounts(self) -> None:
        decision = build_unified_decision(**_base_kwargs(score=75))
        self.assertEqual(decision["amount_mode"], "direction_only")
        self.assertEqual(decision["today_buy_amount_yuan"], 0)
        self.assertEqual(decision["week_buy_amount_yuan"], 0)
        self.assertEqual(decision["month_buy_amount_yuan"], 0)
        self.assertTrue(validate_decision(decision)["ok"])

    def test_dqs_80_89_uses_upper_limit_not_precise(self) -> None:
        decision = build_unified_decision(**_base_kwargs(score=84))
        self.assertEqual(decision["amount_mode"], "upper_limit")
        self.assertFalse(decision["precise_amount_allowed"])
        self.assertTrue(validate_decision(decision)["ok"])

    def test_cash_floor_blocks_today_buy(self) -> None:
        decision = build_unified_decision(**_base_kwargs(score=90, cash_wan=12.0))
        self.assertEqual(decision["cash_available_wan"], 0)
        self.assertEqual(decision["today_buy_amount_yuan"], 0)
        self.assertTrue(validate_decision(decision)["ok"])

    def test_high_risk_blocks_tactical_add(self) -> None:
        decision = build_unified_decision(**_base_kwargs(score=90, risk=80))
        self.assertFalse(decision["tactical_add"])
        self.assertTrue(validate_decision(decision)["ok"])

    def test_invalid_decision_is_downgraded(self) -> None:
        decision = build_unified_decision(**_base_kwargs(score=75))
        decision["today_buy_amount_yuan"] = 1000
        validation = validate_decision(decision)
        self.assertFalse(validation["ok"])
        downgraded = conservative_decision(decision, validation)
        self.assertEqual(downgraded["action_level"], "C")
        self.assertEqual(downgraded["today_buy_amount_yuan"], 0)


if __name__ == "__main__":
    unittest.main()
