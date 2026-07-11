from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

from scripts.check_all_services import collect_service_health, format_service_health
from src.ai.openai_advisor import generate_openai_advice
from tests.test_v12_1_stable import _live_market, _portfolio
from src.decision.v12_1_decision import build_v12_1_decision
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
        self.assertIn("fallback_reason", result)
        self.assertNotIn("{'error'", result["summary"])
        self.assertNotIn("Error code: 429", result["summary"])

    def test_report_uses_v12_1_decision_object(self) -> None:
        decision = build_v12_1_decision(
            portfolio_result=_portfolio(),
            live_market_result=_live_market(),
            macro_result={"has_high_event_next_7_days": False, "upcoming_events": []},
            ai_advice_result={"ai_status": "rule_only", "fallback_reason": "test", "summary": "规则模式"},
        )
        today = generate_today_action(decision)
        daily = generate_daily_report(decision=decision)

        self.assertIn("今日建议金额", today)
        self.assertIn("## 6. Opportunity Score", daily)
        self.assertIn("## 10. DQS数据质量", daily)
        self.assertIn("## 12. 三种市场情景", daily)

    def test_service_health_report_has_no_secret_values(self) -> None:
        rows = collect_service_health()
        report = format_service_health(rows)
        self.assertIn("API Key 不会写入报告", report)
        self.assertNotIn("SMTP_PASSWORD=", report)


if __name__ == "__main__":
    unittest.main()
