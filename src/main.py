from __future__ import annotations

from datetime import date
import sys
from typing import Any

from agents.decision_agent import DecisionAgent
from agents.market_agent import MarketAgent
from agents.portfolio_agent import PortfolioAgent
from agents.rebalance_advisor import RebalanceAdvisor
from agents.report_agent import ReportAgent
from agents.risk_agent import RiskAgent
from src.analysis.cross_asset_engine import analyze_cross_asset
from src.ai.openai_advisor import build_ai_context, generate_openai_advice
from src.journal.investment_journal import build_log_row, upsert_investment_log
from src.journal.review_engine import build_history_review
from src.macro.macro_calendar import analyze_macro_calendar
from src.notifier.email_notifier import send_daily_reports
from src.risk.vix_risk import analyze_vix_risk
from src.strategy.dca_engine import build_dca_plan
from src.strategy.execution_plan import build_execution_plan
from src.strategy.rebalance_engine import build_rebalance_plan
from src.system.health_check import format_health_report, run_health_check
from scripts.build_daily_snapshot import write_snapshot
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root
from utils.market_data_provider import fetch_yfinance_market_data


VERSION_NAME = "Stone AI Investment Manager Pro V12"


def _apply_data_quality_guardrails(
    decision_result: dict[str, Any],
    live_market_result: dict[str, Any],
) -> dict[str, Any]:
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
                    "reason": "关键数据缺失，数据缺失，不做激进判断；先等待更完整的数据源。",
                }
            )
        buy_orders = []
        guarded["operation_level"] = "C级：数据缺失，禁止激进买入"
        guarded["need_action"] = False
        guarded["today_rebalance"] = False
        guarded["confidence"] = min(int(guarded.get("confidence", 60)), 55)
        notes.append("数据质量风控：关键数据缺失，不输出激进买入建议。")
        guarded["one_sentence_conclusion"] = (
            "关键数据缺失，今天只做持有、观察和基础定投，不做激进买入。"
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


def _build_context(snapshot: dict[str, Any] | None = None) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
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
    decision_result = DecisionAgent(config, market_result, portfolio_result, risk_result).decide()
    decision_result = _apply_data_quality_guardrails(decision_result, live_market_result)
    rebalance_result = RebalanceAdvisor(portfolio_result, decision_result).analyze()
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
    today = date.today()
    log_row = build_log_row(
        today,
        portfolio_result,
        market_result,
        vix_result,
        decision_result,
        dca_result,
        allocation_rebalance_result,
    )
    upsert_investment_log(log_row)
    history_review_result = build_history_review(today)
    return (
        config,
        market_result,
        portfolio_result,
        risk_result,
        decision_result,
        live_market_result,
        rebalance_result,
        macro_result,
        vix_result,
        dca_result,
        allocation_rebalance_result,
        execution_plan_result,
        cross_asset_result,
        ai_advice_result,
        history_review_result,
    )


def _write_today_action(
    decision_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
    execution_plan_result: dict[str, Any],
) -> str:
    today_buy_wan = float(execution_plan_result.get("today_buy_wan", 0.0) or 0.0)
    trade_text = "是，需人工确认" if decision_result.get("today_rebalance") or today_buy_wan > 0 else "否，系统只提醒不下单"
    dca_text = "是，按定投计划执行" if dca_result.get("today_continue") else "否，今日不继续定投"
    rebalance_text = "是，需人工确认" if allocation_rebalance_result.get("need_rebalance") else "否，暂不需要再平衡"
    today_orders = execution_plan_result.get("today_orders", [])
    today_buy_text = (
        "；".join(f"{item['name']} {item['amount_yuan']}元" for item in today_orders)
        if today_orders
        else "今日不建议买入"
    )
    if execution_plan_result.get("amount_mode") == "cap" and today_orders:
        today_buy_text = "；".join(f"{item['name']} 不超过{item['amount_yuan']}元" for item in today_orders)
    if execution_plan_result.get("amount_mode") == "blocked":
        today_buy_text = f"DQS门槛未通过：{execution_plan_result.get('data_policy', '不输出交易建议')}"
    pause_text = "、".join(execution_plan_result.get("pause_list", [])) or "无"
    amount_label = "今日买多少"
    if execution_plan_result.get("amount_mode") == "cap":
        amount_label = "今日买入上限"
    elif execution_plan_result.get("amount_mode") == "blocked":
        amount_label = "今日买多少"
    return "\n".join(
        [
            f"今日是否交易：{trade_text}",
            f"今日是否定投：{dca_text}",
            f"今日是否再平衡：{rebalance_text}",
            f"{amount_label}：{today_buy_wan * 10000:.0f}元",
            f"今日买什么：{today_buy_text}",
            f"本周计划买入：{execution_plan_result.get('week_buy_wan', 0.0) * 10000:.0f}元",
            f"本月计划买入：{execution_plan_result.get('month_buy_wan', 0.0) * 10000:.0f}元",
            f"债券转权益：本周转出{execution_plan_result.get('bond_to_equity_path', {}).get('this_week_transfer_wan', 0.0):.2f}万元，本月转出{execution_plan_result.get('bond_to_equity_path', {}).get('this_month_transfer_wan', 0.0):.2f}万元",
            f"暂停加仓：{pause_text}",
            f"今日最大风险：{decision_result.get('max_risk', '暂无')}",
            f"一句话结论：{decision_result.get('one_sentence_conclusion', '暂无')}（仅供投资辅助，不构成投资建议；最终由你自己执行）",
        ]
    )


def _system_check_report() -> str:
    return format_health_report(run_health_check(auto_fix=True))


def run() -> str:
    root = project_root()
    reports_dir = root / "reports"
    reports_dir.mkdir(exist_ok=True)
    today = date.today()
    snapshot = write_snapshot()

    (
        config,
        market_result,
        portfolio_result,
        risk_result,
        decision_result,
        live_market_result,
        rebalance_result,
        macro_result,
        vix_result,
        dca_result,
        allocation_rebalance_result,
        execution_plan_result,
        cross_asset_result,
        ai_advice_result,
        history_review_result,
    ) = _build_context(snapshot)
    report_agent = ReportAgent(
        market_result,
        portfolio_result,
        risk_result,
        decision_result,
        config,
        live_market_result,
        rebalance_result,
        macro_result,
        vix_result,
        dca_result,
        allocation_rebalance_result,
        execution_plan_result,
        cross_asset_result,
        ai_advice_result,
        history_review_result,
    )

    (reports_dir / "today_action.md").write_text(
        _write_today_action(decision_result, dca_result, allocation_rebalance_result, execution_plan_result),
        encoding="utf-8",
    )
    (reports_dir / "daily_report.md").write_text(
        report_agent.generate_daily_report(today),
        encoding="utf-8",
    )
    (reports_dir / "weekly_report.md").write_text(
        report_agent.generate_weekly_report(today),
        encoding="utf-8",
    )
    (reports_dir / "system_check_report.md").write_text(_system_check_report(), encoding="utf-8")

    email_result = send_daily_reports(reports_dir=reports_dir, subject_date=today)

    return "\n".join(
        [
            f"{VERSION_NAME} 运行完成",
            f"总资产：{portfolio_result['total_assets_wan']:.2f} 万元",
            f"操作等级：{decision_result['operation_level']}",
            f"今日是否调仓：{'是' if decision_result['today_rebalance'] else '否'}",
            f"今日计划买入：{execution_plan_result.get('today_buy_wan', 0.0) * 10000:.0f} 元",
            f"本周计划买入：{execution_plan_result.get('week_buy_wan', 0.0) * 10000:.0f} 元",
            f"本月计划买入：{execution_plan_result.get('month_buy_wan', 0.0) * 10000:.0f} 元",
            f"债券转权益：本周 {execution_plan_result.get('bond_to_equity_path', {}).get('this_week_transfer_wan', 0.0):.2f} 万元，本月 {execution_plan_result.get('bond_to_equity_path', {}).get('this_month_transfer_wan', 0.0):.2f} 万元",
            f"邮件通知：{email_result['message']}",
            "已生成：",
            "- reports/today_action.md",
            "- reports/daily_report.md",
            "- reports/weekly_report.md",
            "- reports/system_check_report.md",
            "声明：系统不自动交易，不接券商下单权限；仅供投资辅助，不构成投资建议，不承诺收益。",
        ]
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(run())
