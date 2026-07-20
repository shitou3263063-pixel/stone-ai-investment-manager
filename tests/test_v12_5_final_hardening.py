from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.openai_advisor import (
    AI_FIELDS,
    apply_openai_review,
    generate_openai_advice,
    validate_openai_advice,
)
from src.decision.v12_1_decision import apply_ai_explanation, build_consistency_checks
from src.macro.macro_calendar import analyze_macro_calendar
from src.data_sources.data_router import _normalize_point
from src.portfolio_snapshot import build_portfolio_snapshot
from src.reports.report_center import generate_daily_report
from tests.test_final_decision_bundle import _fixture_bundle
from tests.test_v12_5_stable import _decision


def _ai_payload(**overrides):
    payload = {
        "market_regime": "中性",
        "why_action_or_no_action": "今日不交易，当前没有真实可执行买入预算。",
        "key_risk_3_7_days": "数据质量与现金安全线约束",
        "portfolio_priority": "保留安全现金，等待债券资金真实到账",
        "best_opportunity": "VOO是长期配置优先方向，但当前不追涨",
        "required_trigger_conditions": ["DQS不低于85", "可投资现金大于0"],
        "cio_commentary": "规则结论为今日不交易。",
        "one_sentence_conclusion": "今日不交易，等待数据和资金条件。",
    }
    payload.update(overrides)
    return payload


def _install_fake_openai(monkeypatch, outcomes):
    class Responses:
        def create(self, **kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(output_text=outcome)

    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()

    monkeypatch.setitem(__import__("sys").modules, "openai", SimpleNamespace(OpenAI=Client))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OPENAI_ENABLED", "true")
    monkeypatch.setenv("MAX_LLM_RETRIES", "2")
    monkeypatch.setattr("src.ai.openai_advisor.time.sleep", lambda _: None)


def test_portfolio_snapshot_has_traceability_and_freshness_fields() -> None:
    snapshot = build_portfolio_snapshot()
    for field in ["snapshot_date", "source", "last_confirmed_at", "valuation_method", "holdings_stale"]:
        assert field in snapshot
    for holding in snapshot["holdings"]:
        for field in ["source", "last_confirmed_at", "currency", "valuation_method"]:
            assert holding[field]


def test_market_point_contract_never_turns_missing_into_zero() -> None:
    point = _normalize_point({"value": None, "status": "missing", "source": "unavailable", "fetched_at": "2026-07-12T08:00:00"})
    for field in ["value", "timestamp", "source", "source_level", "status", "stale", "fallback_used"]:
        assert field in point
    assert point["value"] is None
    assert point["status"] == "missing"


def test_openai_valid_structured_json(monkeypatch, tmp_path: Path) -> None:
    _install_fake_openai(monkeypatch, [json.dumps(_ai_payload(), ensure_ascii=False)])
    result = generate_openai_advice({}, env_path=tmp_path / "missing.env")
    assert result["ai_status"] == "available"
    assert all(field in result for field in AI_FIELDS)


@pytest.mark.parametrize(
    ("error", "reason"),
    [(RuntimeError("429 rate_limit"), "rate_limit"), (TimeoutError("request timeout"), "network_or_timeout")],
)
def test_openai_retryable_failures_fall_back(monkeypatch, tmp_path: Path, error: Exception, reason: str) -> None:
    _install_fake_openai(monkeypatch, [error, error, error])
    result = generate_openai_advice({}, env_path=tmp_path / "missing.env")
    assert result["ai_status"] == "rule_only"
    assert result["fallback_reason"] == reason
    assert result["retry_count"] == 2


@pytest.mark.parametrize("raw", ["not json", json.dumps({"market_regime": "中性"}, ensure_ascii=False)])
def test_openai_invalid_json_or_missing_fields_falls_back(monkeypatch, tmp_path: Path, raw: str) -> None:
    _install_fake_openai(monkeypatch, [raw])
    result = generate_openai_advice({}, env_path=tmp_path / "missing.env")
    assert result["ai_status"] == "rule_only"
    assert result["fallback_reason"] == "invalid_json_or_schema"


def test_openai_cash_and_dqs_violations_are_rejected() -> None:
    decision = _decision()
    advice = {**_ai_payload(cio_commentary="建议立即买入VOO 10000元"), "ai_status": "available", "model": "test"}
    valid, errors = validate_openai_advice(advice, decision)
    assert valid is False
    assert any("DQS" in error for error in errors)
    reviewed = apply_openai_review(decision, advice)
    assert reviewed["fallback_reason"] == "OPENAI_VALIDATION_REJECTED"


def test_openai_st_buy_is_rejected() -> None:
    advice = {**_ai_payload(cio_commentary="建议加仓*ST闻泰"), "ai_status": "available"}
    valid, errors = validate_openai_advice(advice, _decision())
    assert valid is False
    assert any("ST" in error for error in errors)


def test_rule_commentary_is_complete_when_openai_fails() -> None:
    decision = apply_ai_explanation(_decision(), {"ai_status": "rule_only", "fallback_reason": "rate_limit"})
    commentary = decision["ai"]
    assert commentary["mode"] in {"RULES_ONLY", "SAFE_MODE"}
    assert "可投资现金为21,000元" in commentary["best_action_today"]
    assert "剩余专项资金" in commentary["best_action_today"]
    assert commentary["required_trigger_conditions"]


def test_unconfirmed_event_is_not_presented_as_confirmed_high_event(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "macro_events:\n  - name: FOMC\n    date: 2026-08-05\n    level: high\n    confirmed: false\n",
        encoding="utf-8",
    )
    result = analyze_macro_calendar(today=__import__("datetime").date(2026, 8, 1), settings_path=settings)
    assert result["has_high_event_next_7_days"] is False
    assert result["has_unconfirmed_high_event_next_7_days"] is False
    configured = next(item for item in result["events"] if item["event_name"] == "FOMC")
    assert configured["verification_status"] == "unverified"
    assert configured["release_at_utc"] is None


def test_daily_report_uses_single_bundle_and_fixed_section_order() -> None:
    bundle = _fixture_bundle()
    report = generate_daily_report(decision=bundle)
    headings = ["## 今日场景决策", "## 数据质量评分", "## 统一真实资产快照", "## 事件与数据状态", "## 附录：统一快照引用"]
    assert [report.index(item) for item in headings] == sorted(report.index(item) for item in headings)
    assert report.count(bundle["bundle_hash"]) >= 3
def test_hard_validation_detects_live_grid_budget_in_simulation() -> None:
    decision = _decision()
    decision["grid"] = {
        "paper_mode": True,
        "live_advice_enabled": False,
        "grid_budget": {"live_available_yuan": 1},
    }
    result = build_consistency_checks(decision)
    assert result["status"] == "FAIL"
    assert any("模拟网格" in error for error in result["errors"])


def test_workflows_have_single_entry_and_independent_concurrency() -> None:
    cn = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")
    us = Path(".github/workflows/daily-us.yml").read_text(encoding="utf-8")

    assert cn.count("python main.py 2>&1 | tee logs/main.log") == 1
    assert us.count("python main.py 2>&1 | tee logs/main.log") == 1
    assert "group: stone-ai-cn-preopen-${{ github.ref }}" in cn
    assert "group: stone-ai-us-preopen-${{ github.ref }}" in us
    assert 'cron: "35 0 * * 1-5"' in cn
    assert 'cron: "40 12 * * 1-5"' in us
    assert 'cron: "40 13 * * 1-5"' in us
    assert "timezone:" not in cn
    assert "timezone:" not in us
