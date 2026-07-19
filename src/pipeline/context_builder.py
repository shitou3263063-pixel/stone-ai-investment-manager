from __future__ import annotations

from datetime import date
from typing import Any

from src.ai.openai_advisor import apply_openai_review, build_cio_review_context, generate_openai_advice
from src.decision.issue_registry import refresh_issue_registry
from src.decision.v12_1_decision import build_consistency_checks, build_v12_1_decision, refresh_unified_decision_context
from src.domain.event_assessment import build_event_assessment
from src.macro.macro_calendar import analyze_macro_calendar
from src.portfolio_snapshot import build_portfolio_snapshot
from src.strategies.smart_grid_strategy import build_smart_grid_result
from src.valuation.valuation_engine import apply_live_valuation
from utils.logger import write_log


def build_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build one rule result; ScenarioDecision is finalized exactly once after grid analysis."""
    live_market = snapshot.get("market") or {}
    provided = ((snapshot.get("portfolio") or {}).get("snapshot") or {})
    portfolio_snapshot = provided if provided.get("valuation_engine") == "canonical_ledger_first" and provided.get("positions") is not None else build_portfolio_snapshot()
    portfolio_snapshot = apply_live_valuation(
        portfolio_snapshot, live_market,
        valuation_as_of=str(snapshot.get("decision_cutoff_time") or snapshot.get("built_at") or date.today().isoformat()),
    )
    macro = analyze_macro_calendar(macro_snapshot=live_market.get("macro", {}) or {})
    event_assessment = build_event_assessment(macro)
    decision = build_v12_1_decision(
        portfolio_result={}, live_market_result=live_market, macro_result=macro,
        ai_advice_result={"ai_status": "rule_only", "fallback_reason": "pre_ai_rule_pass"},
        portfolio_snapshot=portfolio_snapshot,
    )
    decision["event_assessment"] = event_assessment
    try:
        grid = build_smart_grid_result(decision=decision, live_market_result=live_market)
    except Exception as exc:  # noqa: BLE001
        grid = {"enabled": False, "error": str(exc), "summary": "grid isolated after failure", "real_trade": False}
        write_log(f"Grid isolated: {exc}", filename="stone_ai.log")
    decision["grid"] = grid
    refresh_unified_decision_context(decision, event_assessment)
    decision["consistency"] = build_consistency_checks(decision)
    refresh_issue_registry(decision)
    ai_review = apply_openai_review(decision, generate_openai_advice(build_cio_review_context(decision, live_market, macro)))
    decision["ai_review"] = ai_review
    return {
        "live_market_result": live_market, "macro_result": macro,
        "event_assessment": event_assessment, "ai_advice_result": ai_review,
        "decision": decision, "validation": decision.get("consistency", {}),
    }
