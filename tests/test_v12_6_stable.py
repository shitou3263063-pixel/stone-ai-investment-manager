from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.ai.openai_advisor import apply_openai_review, generate_openai_advice
from src.analysis.scenario_analysis import calculate_portfolio_stress_scenarios
from src.decision.v12_1_decision import build_opportunity_scores, enrich_allocation, load_strategy
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.report_center import generate_daily_report
from tests.test_v12_1_stable import _live_market, _portfolio
from tests.test_v12_5_final_hardening import _ai_payload
from tests.test_v12_5_stable import _decision


def _install_fake_openai(monkeypatch, outcomes):
    calls = {"count": 0}

    class Responses:
        def create(self, **kwargs):
            calls["count"] += 1
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(output_text=outcome)

    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()

    monkeypatch.setitem(__import__("sys").modules, "openai", SimpleNamespace(OpenAI=Client))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr("src.ai.openai_advisor.time.sleep", lambda _: None)
    return calls


def _opportunities(live=None):
    strategy = load_strategy()
    allocation = enrich_allocation(_portfolio(), strategy)
    return build_opportunity_scores(allocation, live or _live_market(), strategy)


def test_openai_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-key")
    result = generate_openai_advice({}, env_path=tmp_path / "missing.env")
    assert result["called"] is False
    assert result["fallback_reason"] == "disabled"


def test_openai_quota_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_ENABLED", "true")
    calls = _install_fake_openai(monkeypatch, [RuntimeError("429 insufficient_quota")])
    result = generate_openai_advice({}, env_path=tmp_path / "missing.env")
    assert calls["count"] == 1
    assert result["fallback_reason"] == "insufficient_quota"
    assert result["error_category"] == "insufficient_quota"
    assert result["ai_status"] == "rule_only"


def test_openai_conflict_rule_engine_wins() -> None:
    advice = {
        **_ai_payload(cio_commentary="建议立即买入*ST闻泰10000元"),
        "ai_status": "available",
        "enabled": True,
        "called": True,
        "success": True,
        "model": "test",
    }
    reviewed = apply_openai_review(_decision(), advice)
    assert reviewed["ai_status"] == "rule_only"
    assert reviewed["conflict_with_rules"] is True
    assert "规则风控优先" in reviewed["review_summary"]


def test_opportunity_score_dispersion() -> None:
    scores = [row["score"] for row in _opportunities()]
    assert max(scores) - min(scores) >= 30
    assert sum(55 <= score <= 69 for score in scores) < len(scores) * 0.7


def test_individual_stock_not_boosted_by_asset_underweight() -> None:
    rows = {row["name"]: row for row in _opportunities()}
    for name in ["NVDA", "GOOG", "BABA", "IBKR", "巨人网络"]:
        assert rows[name]["advice"] in {"继续持有", "观察", "暂停新增", "风险复核或回避"}
        assert rows[name]["portfolio_constraint_adjustment"] < 0
    assert rows["XLF"]["score"] < rows["VOO"]["score"]


def test_overweight_gold_and_bond_penalty() -> None:
    rows = {row["name"]: row for row in _opportunities()}
    for name in ["黄金", "TLT", "中国债券", "10年地债"]:
        assert rows[name]["advice"] == "暂停新增"
        assert rows[name]["portfolio_constraint_adjustment"] < 0


def test_missing_market_data_reduces_confidence() -> None:
    good = {row["name"]: row for row in _opportunities(_live_market())}["VOO"]
    missing = {row["name"]: row for row in _opportunities(_live_market(missing=True))}["VOO"]
    assert missing["components"]["数据置信度"] < good["components"]["数据置信度"]
    assert missing["score"] < good["score"]
    assert missing["advice"] == "观察"


def test_st_stock_never_auto_add() -> None:
    st = {row["name"]: row for row in _opportunities()}["*ST闻泰"]
    assert st["advice"] == "风险复核或回避"
    assert st["score"] < 40


def test_unsettled_bond_cash_not_investable() -> None:
    cash = build_portfolio_snapshot()["cash"]
    assert cash["unsettled_conditional_cash_cny"] == 0
    assert cash["investable_cash_cny"] == 21000
    assert cash["bond_maturity_arrival_cny"] == 30000


def test_simulation_cash_not_real_cash() -> None:
    cash = build_portfolio_snapshot()["cash"]
    assert cash["paper_grid_cash_cny"] == 0
    assert cash["live_grid_cash_cny"] == 0


def test_scenario_analysis_calculation() -> None:
    strategy = load_strategy()
    allocation = enrich_allocation(_portfolio(), strategy)
    scenarios = calculate_portfolio_stress_scenarios(allocation, strategy["scenario_stress"])
    by_key = {row["key"]: row for row in scenarios}
    assert by_key["equity_bull"]["portfolio_change_yuan"] == 88620
    assert by_key["range_market"]["portfolio_change_yuan"] == 66255
    assert by_key["risk_shock"]["portfolio_change_yuan"] == -120130
    assert by_key["risk_shock"]["exceeds_tolerance_low"] is False


def test_report_internal_consistency() -> None:
    decision = _decision()
    report = generate_daily_report(decision=decision)
    assert decision["consistency"]["status"] == "PASS"
    assert "### 组合情景压力测试" in report
    assert "### 市场宽度、资金流与情绪数据状态" in report
    assert "模拟资金" not in decision.get("funding_source", "")
    assert json.dumps(decision, ensure_ascii=False)
