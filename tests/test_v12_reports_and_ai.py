from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from scripts.check_all_services import collect_service_health, format_service_health
from src.ai.openai_advisor import generate_openai_advice
from src.reports.report_center import generate_daily_report, generate_today_action


class ReportsAndAiTest(unittest.TestCase):
    def test_openai_missing_key_falls_back_without_raw_error(self) -> None:
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = ""
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = generate_openai_advice({}, env_path=Path(tmp) / ".env")
        finally:
            if old is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old

        self.assertEqual(result["ai_status"], "rule_only")
        self.assertNotIn("{'error'", result["summary"])
        self.assertNotIn("Error code: 429", result["summary"])

    def test_report_uses_unified_decision_amounts(self) -> None:
        decision = {
            "portfolio_value_wan": 282.11,
            "action_level": "B",
            "dqs": 84,
            "amount_mode": "upper_limit",
            "amount_label": "金额上限",
            "base_dca": True,
            "base_dca_status": "allowed",
            "tactical_add": False,
            "rebalance_today": False,
            "rebalance_required": True,
            "reduce_positions": False,
            "today_buy_amount_yuan": 4000,
            "week_buy_amount_yuan": 9000,
            "month_buy_amount_yuan": 25000,
            "conditional_month_buy_upper_yuan": 30000,
            "bond_weekly_transfer_wan": 3.0,
            "bond_monthly_transfer_wan": 3.4,
            "cash_current_wan": 22.0,
            "cash_floor_wan": 22.57,
            "cash_available_wan": 0.0,
            "priority_assets": ["VOO/QQQ", "沪深300ETF"],
            "paused_assets": ["债券", "黄金", "TLT"],
            "warnings": ["现金接近安全线"],
            "reasons": ["债券超配"],
            "one_sentence": "按上限分批，不追涨。",
            "source_coverage": {"data_source_coverage": 0.9, "dual_source_coverage": 0.7, "tier1_coverage": 0.8},
            "risk_score": 55,
            "macro_event_high_next_7_days": False,
            "ai_status": "rule_only",
            "llm_provider": "rule-only",
        }
        today = generate_today_action(decision)
        daily = generate_daily_report(
            decision=decision,
            portfolio_result={"categories": []},
            market_result={"summary": "test"},
            live_market_result={"data_quality": {"score": 84}},
            macro_result={"upcoming_events": []},
            allocation_rebalance_result={"need_rebalance": True},
            ai_advice_result={"summary": "规则模式"},
            validation={"ok": True, "fallback_applied": False, "warnings": [], "errors": []},
        )

        self.assertIn("今日买多少：不超过4,000元", today)
        self.assertIn("本周买多少：不超过9,000元", daily)
        self.assertIn("reports/decision.json", Path("README.md").read_text(encoding="utf-8"))

    def test_service_health_report_has_no_secret_values(self) -> None:
        rows = collect_service_health()
        report = format_service_health(rows)
        self.assertIn("API Key 不会写入报告", report)
        self.assertNotIn("SMTP_PASSWORD=", report)


if __name__ == "__main__":
    unittest.main()
