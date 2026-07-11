"""禁止生产运行：V12.5 Stable 之前的 src/main.py 入口，仅供历史追溯。"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.decision_agent import DecisionAgent
from agents.market_agent import MarketAgent
from agents.portfolio_agent import PortfolioAgent
from agents.risk_agent import RiskAgent
from scripts.build_daily_snapshot import write_snapshot
from scripts.check_all_services import write_service_health
from scripts.project_audit import write_project_audit
from src.ai.openai_advisor import build_ai_context, generate_openai_advice
from src.analysis.cross_asset_engine import analyze_cross_asset
from src.decision.unified_decision import build_unified_decision
from src.journal.investment_journal import build_log_row, upsert_investment_log
from src.journal.review_engine import build_history_review
from src.macro.macro_calendar import analyze_macro_calendar
from src.notifier.email_notifier import send_daily_reports
from src.reports.report_center import (
    generate_daily_report,
    generate_monthly_report,
    generate_today_action,
    generate_weekly_report,
)
from src.risk.vix_risk import analyze_vix_risk
from src.strategy.dca_engine import build_dca_plan
from src.strategy.execution_plan import build_execution_plan
from src.strategy.rebalance_engine import build_rebalance_plan
from src.system.health_check import format_health_report, run_health_check
from src.validators.decision_validator import (
    conservative_decision,
    validate_decision,
    write_validation_report,
)
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root
from utils.market_data_provider import fetch_yfinance_market_data


VERSION_NAME = "Stone AI Investment Manager Pro V12"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_data_quality_guardrails(
    decision_result: dict[str, Any],
    live_market_result: dict[str, Any],
) -> dict[str, Any]:
    """保留旧 Agent 的数据质量保护，只作为候选材料，不作为最终裁决。"""
    quality = live_market_result.get("data_quality", {}) or {}
    guarded = {**decision_result}
    notes = list(guarded.get("exception_notes", []))
    wait_orders = list(guarded.get("wait_orders", []))
    buy_orders = [dict(item) for item in guarded.get("buy_orders", [])]

    critical_missing = bool(quality.get("critical_missing"))
    only_yfinance = bool(quality.get("only_yfinance"))
    macro_available = bool(quality.get("macro_available"))
    market_available = bool(quality.get("market_available"))

    if critical_missing:
        if buy_orders:
            wait_orders.append(
                {
                    "name": "激进买入",
                    "action": "等待",
                    "confidence": 90,
                    "reason": "关键数据缺失，不做激进判断；先等待更完整的数据源。",
                }
            )
        buy_orders = []
        guarded["operation_level"] = "C级：数据缺失，禁止激进买入"
        guarded["need_action"] = False
        guarded["today_rebalance"] = False
        guarded["confidence"] = min(int(guarded.get("confidence", 60)), 55)
        notes.append("数据质量风控：关键数据缺失，不输出激进买入建议。")
        guarded["one_sentence_conclusion"] = (
            "关键数据缺失，今天只做持有、观察和基础定投评估，不做激进买入。"
        )
    elif only_yfinance:
        for order in buy_orders:
            order["confidence"] = min(int(order.get("confidence", 60)), 60)
            order["reason"] = f"{order.get('reason', '')} 数据源仅为 yfinance/缓存，置信度下调。".strip()
        guarded["confidence"] = min(int(guarded.get("confidence", 65)), 60)
        notes.append("数据质量风控：仅使用 yfinance/缓存，普通建议可保留，但降低置信度。")
    elif macro_available and market_available:
        notes.append("数据质量风控：宏观数据和市场数据同时可用，可保留较高置信度建议。")
    else:
        guarded["confidence"] = min(int(guarded.get("confidence", 65)), 65)
        notes.append("数据质量风控：数据不完整，建议保持中性置信度。")

    guarded["buy_orders"] = buy_orders
    guarded["wait_orders"] = wait_orders
    guarded["exception_notes"] = notes
    guarded["data_quality_guardrail"] = {
        "critical_missing": critical_missing,
        "only_yfinance": only_yfinance,
        "macro_available": macro_available,
        "market_available": market_available,
        "score": quality.get("score", 0),
    }
    return guarded


def _build_context(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    root = project_root()
    config = load_config(root / "data" / "config.yaml")
    portfolio = load_portfolio(root / "data" / "portfolio.csv")
    market_data = load_market_data(root / "data" / "market_data.csv")
    live_market_result = (snapshot or {}).get("market") or fetch_yfinance_market_data()
    macro_result = analyze_macro_calendar()
    vix_result = analyze_vix_risk(live_market_result, market_data)

    market_result = MarketAgent(config, market_data).analyze()
    portfolio_result = PortfolioAgent(config, portfolio).analyze()
    risk_result = RiskAgent(config, portfolio_result, market_result).analyze()

    candidate_decision_result = DecisionAgent(config, market_result, portfolio_result, risk_result).decide()
    candidate_decision_result = _apply_data_quality_guardrails(candidate_decision_result, live_market_result)

    dca_result = build_dca_plan(market_data, vix_result, macro_result)
    allocation_rebalance_result = build_rebalance_plan(portfolio_result)
    execution_plan_result = build_execution_plan(
        portfolio_result,
        market_result,
        live_market_result,
        vix_result,
        macro_result,
        dca_result,
        allocation_rebalance_result,
        config,
    )
    cross_asset_result = analyze_cross_asset(live_market_result, market_data, portfolio_result)
    ai_context = build_ai_context(
        portfolio_result,
        market_result,
        live_market_result,
        vix_result,
        macro_result,
        dca_result,
        allocation_rebalance_result,
        cross_asset_result,
    )
    ai_advice_result = generate_openai_advice(ai_context)

    decision = build_unified_decision(
        portfolio_result=portfolio_result,
        market_result=market_result,
        live_market_result=live_market_result,
        macro_result=macro_result,
        allocation_rebalance_result=allocation_rebalance_result,
        execution_plan_result=execution_plan_result,
        ai_advice_result=ai_advice_result,
    )
    initial_validation = validate_decision(decision)
    if not initial_validation.get("ok"):
        decision = conservative_decision(decision, initial_validation)
        final_validation = validate_decision(decision)
        validation = {
            **final_validation,
            "fallback_applied": True,
            "initial_errors": initial_validation.get("errors", []),
            "initial_warnings": initial_validation.get("warnings", []),
        }
    else:
        validation = {**initial_validation, "fallback_applied": False}

    log_decision = {
        **candidate_decision_result,
        "one_sentence_conclusion": decision.get("one_sentence", ""),
    }
    today = date.today()
    log_row = build_log_row(
        today,
        portfolio_result,
        market_result,
        vix_result,
        log_decision,
        dca_result,
        allocation_rebalance_result,
    )
    upsert_investment_log(log_row)
    history_review_result = build_history_review(today)

    return {
        "config": config,
        "market_result": market_result,
        "portfolio_result": portfolio_result,
        "risk_result": risk_result,
        "candidate_decision_result": candidate_decision_result,
        "live_market_result": live_market_result,
        "macro_result": macro_result,
        "vix_result": vix_result,
        "dca_result": dca_result,
        "allocation_rebalance_result": allocation_rebalance_result,
        "execution_plan_result": execution_plan_result,
        "cross_asset_result": cross_asset_result,
        "ai_advice_result": ai_advice_result,
        "decision": decision,
        "validation": validation,
        "history_review_result": history_review_result,
    }


def _system_check_report() -> str:
    return format_health_report(run_health_check(auto_fix=True))


def run(*, send_email: bool = True) -> str:
    root = project_root()
    reports_dir = root / "reports"
    reports_dir.mkdir(exist_ok=True)
    today = date.today()

    snapshot = write_snapshot()
    context = _build_context(snapshot)
    decision = context["decision"]
    validation = context["validation"]

    _write_json(reports_dir / "decision.json", decision)
    write_validation_report(reports_dir / "validation_report.md", validation, decision)
    write_project_audit(reports_dir / "project_audit.md")
    write_service_health(reports_dir / "service_health.md")

    (reports_dir / "today_action.md").write_text(generate_today_action(decision), encoding="utf-8")
    (reports_dir / "daily_report.md").write_text(
        generate_daily_report(
            decision=decision,
            portfolio_result=context["portfolio_result"],
            market_result=context["market_result"],
            live_market_result=context["live_market_result"],
            macro_result=context["macro_result"],
            allocation_rebalance_result=context["allocation_rebalance_result"],
            ai_advice_result=context["ai_advice_result"],
            validation=validation,
        ),
        encoding="utf-8",
    )
    (reports_dir / "weekly_report.md").write_text(generate_weekly_report(decision), encoding="utf-8")
    (reports_dir / "monthly_report.md").write_text(generate_monthly_report(decision), encoding="utf-8")
    (reports_dir / "system_check_report.md").write_text(_system_check_report(), encoding="utf-8")

    email_result = {"message": "本次运行未发送邮件"}
    if send_email:
        email_result = send_daily_reports(reports_dir=reports_dir, subject_date=today)

    return "\n".join(
        [
            f"{VERSION_NAME} 运行完成",
            f"总资产：{decision.get('portfolio_value_wan', 0):.2f} 万元",
            f"操作等级：{decision.get('action_level', 'C')}级",
            f"DQS：{decision.get('dqs', 0)}；金额模式：{decision.get('amount_label', '')}",
            f"今日是否调仓：{'是' if decision.get('rebalance_today') else '否'}",
            f"今日计划买入：{decision.get('today_buy_amount_yuan', 0):.0f} 元",
            f"本周计划买入：{decision.get('week_buy_amount_yuan', 0):.0f} 元",
            f"本月计划买入：{decision.get('month_buy_amount_yuan', 0):.0f} 元",
            f"债券转权益：本周 {decision.get('bond_weekly_transfer_wan', 0):.2f} 万元，本月 {decision.get('bond_monthly_transfer_wan', 0):.2f} 万元",
            f"一致性校验：{'通过' if validation.get('ok') else '已降级'}",
            f"邮件通知：{email_result['message']}",
            "已生成：",
            "- reports/today_action.md",
            "- reports/daily_report.md",
            "- reports/weekly_report.md",
            "- reports/monthly_report.md",
            "- reports/system_check_report.md",
            "- reports/service_health.md",
            "- reports/validation_report.md",
            "- reports/project_audit.md",
            "- reports/decision.json",
            "声明：系统不自动交易，不接券商下单权限；仅供投资辅助，不构成投资建议，不承诺收益。",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    health = run_health_check(auto_fix=True)
    print(format_health_report(health))
    print("")
    if not health.get("can_run", False):
        print("发现 ERROR 项，主程序未运行。请按系统检查报告修复后重试。")
        return 1

    print("开始生成投资日报...")
    print(run(send_email=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
