from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import yaml

from src.data_sources.data_router import get_market_quote
from src.decision.v12_1_decision import build_v12_1_decision
from src.macro.macro_calendar import analyze_macro_calendar
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.report_center import generate_daily_report
from tests.test_v12_5_stable import _live_market, _portfolio_result


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _decision(*, weekend: bool = False) -> dict:
    snapshot = build_portfolio_snapshot()
    kwargs = {
        "portfolio_result": _portfolio_result(snapshot),
        "live_market_result": _live_market(),
        "macro_result": analyze_macro_calendar(today=date.today()),
        "ai_advice_result": {
            "ai_status": "rule_only",
            "actual_provider": "stone_rule_engine",
            "fallback_reason": "test",
            "summary": "Stone CIO规则增强分析",
        },
    }
    if not weekend:
        return build_v12_1_decision(**kwargs)

    class WeekendDate(date):
        @classmethod
        def today(cls) -> "WeekendDate":
            return cls(2026, 7, 12)

    with patch("src.decision.v12_1_decision.date", WeekendDate):
        return build_v12_1_decision(**kwargs)


def test_asset_total_consistency() -> None:
    snapshot = build_portfolio_snapshot()
    assert sum(snapshot["asset_class_totals"].values()) == snapshot["total_assets"]
    assert sum(row["market_value_cny"] for row in snapshot["holdings"]) == snapshot["total_assets"]


def test_target_weight_sum() -> None:
    strategy = yaml.safe_load((PROJECT_ROOT / "config" / "strategy.yaml").read_text(encoding="utf-8"))
    assert strategy["config_version"] == "V12.6.1_STABLE"
    assert abs(sum(strategy["target_allocation"].values()) - 1.0) < 1e-9


def test_cash_safety_line() -> None:
    cash = build_portfolio_snapshot()["cash"]
    expected = max(
        0,
        cash["account_total_cash_cny"]
        - cash["cash_safety_reserve_cny"]
        - cash["live_grid_cash_cny"]
        - cash["other_reserved_cash_cny"],
    )
    assert cash["investable_cash_cny"] == expected
    assert cash["investable_cash_cny"] >= 0


def test_pending_bond_cash_exclusion() -> None:
    budget = _decision()["budget"]
    assert budget["actual_bond_cash_arrived_yuan"] == 30000
    assert budget["approved_bond_to_equity_month_yuan"] == 30000
    assert budget["bond_to_equity_remaining_this_month_yuan"] == 21000
    assert budget["today_total_yuan"] == 0
    assert budget["investable_cash_yuan"] == 21000


def test_grid_simulation_cash_exclusion() -> None:
    grid = yaml.safe_load((PROJECT_ROOT / "config" / "smart_grid.yaml").read_text(encoding="utf-8"))["smart_grid"]
    budget = _decision()["budget"]
    assert grid["paper_mode"] is True
    assert grid["auto_trade"] is False
    assert budget["paper_grid_cash_yuan"] == 0
    assert budget["live_grid_cash_yuan"] == 0


def test_no_trade_amount_zero() -> None:
    decision = _decision()
    assert decision["today_trade"] is False
    assert decision["today_confirmed_trade_executed"] is False
    assert decision["today_amount_yuan"] == 0
    assert any(item["id"] == "USERCONF-20260715-VOO-001" for item in decision["confirmed_transactions"])
    assert decision["budget"]["today_total_yuan"] == 0


def test_dqs_execution_gate() -> None:
    decision = _decision()
    assert decision["dqs"]["score"] < 60
    assert decision["dqs"]["mode"] == "safe"
    assert decision["today_trade"] is False
    assert decision["budget"]["today_total_yuan"] == 0


def test_weekend_market_status() -> None:
    decision = _decision(weekend=True)
    assert "下一交易日" in decision["trading_day_status"]
    assert decision["today_trade"] is False


def test_single_production_entrypoint() -> None:
    production_main_files = [
        path
        for path in PROJECT_ROOT.rglob("main.py")
        if "archive" not in path.parts and ".venv" not in path.parts and "venv" not in path.parts
    ]
    assert production_main_files == [PROJECT_ROOT / "main.py"]
    assert "from src.app import main" in (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    assert "run: python main.py" in workflow
    assert "python src/main.py" not in workflow


def test_report_required_sections() -> None:
    report = generate_daily_report(decision=_decision())
    required = [
        "## 0. 报告状态",
        "## 1. Stone CIO 今日决策卡",
        "## 2. Stone CIO Commentary",
        "## 3. 今日资金计划",
        "## 4. 现金与预算口径",
        "## 5. 下一触发条件",
        "## 6. 资产配置与偏离",
        "## 7. 未来12个月债券迁移第一阶段路线图",
        "## 8. Opportunity Score",
        "## 9. 持仓健康检查",
        "## 10. 市场与宏观",
        "## 11. 市场风险评分",
        "## 12. DQS数据质量",
        "## 13. 未来7天事件",
        "## 14. 三种市场情景",
        "## 15. Stone Smart Grid",
        "## 16. OpenAI状态与回退说明",
        "## 17. 数据来源",
        "## 18. 一致性验证",
        "## 19. 免责声明",
    ]
    for heading in required:
        assert heading in report


def test_data_source_fallback() -> None:
    cached = {
        "close": 500.0,
        "previous_close": 495.0,
        "change_pct": 1.01,
        "source": "yfinance",
        "fetched_at": "2026-07-11T16:00:00",
    }
    with (
        patch("src.data_sources.data_router.alpha_vantage_client.get_quote", side_effect=RuntimeError("offline")),
        patch("src.data_sources.data_router.finnhub_client.get_quote", side_effect=RuntimeError("offline")),
        patch("src.data_sources.data_router.yfinance_client.get_quote", side_effect=RuntimeError("offline")),
        patch("src.data_sources.data_router.read_cache", return_value=cached),
    ):
        result = get_market_quote("VOO")
    assert result["status"] == "ok"
    assert result["source"] == "cache:yfinance"
    assert result["close"] == 500.0


def test_consistency_validation() -> None:
    decision = _decision()
    assert decision["consistency"]["status"] == "WARN"
    assert decision["consistency"]["errors"] == []
