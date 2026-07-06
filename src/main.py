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
from src.strategy.rebalance_engine import build_rebalance_plan
from src.system.health_check import format_health_report, run_health_check
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root
from utils.market_data_provider import fetch_yfinance_market_data


VERSION_NAME = "Stone AI Investment Manager Pro V12"


def _build_context() -> tuple[
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
    live_market_result = fetch_yfinance_market_data()
    macro_result = analyze_macro_calendar()
    vix_result = analyze_vix_risk(live_market_result, market_data)

    market_result = MarketAgent(config, market_data).analyze()
    portfolio_result = PortfolioAgent(config, portfolio).analyze()
    risk_result = RiskAgent(config, portfolio_result, market_result).analyze()
    decision_result = DecisionAgent(config, market_result, portfolio_result, risk_result).decide()
    rebalance_result = RebalanceAdvisor(portfolio_result, decision_result).analyze()
    dca_result = build_dca_plan(market_data, vix_result, macro_result)
    allocation_rebalance_result = build_rebalance_plan(portfolio_result)
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
        cross_asset_result,
        ai_advice_result,
        history_review_result,
    )


def _write_today_action(
    decision_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
) -> str:
    trade_text = "是，需人工确认" if decision_result.get("today_rebalance") else "否，系统只提醒不下单"
    dca_text = "是，按定投计划执行" if dca_result.get("today_continue") else "否，今日不继续定投"
    rebalance_text = "是，需人工确认" if allocation_rebalance_result.get("need_rebalance") else "否，暂不需要再平衡"
    return "\n".join(
        [
            f"今日是否交易：{trade_text}",
            f"今日是否定投：{dca_text}",
            f"今日是否再平衡：{rebalance_text}",
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
        cross_asset_result,
        ai_advice_result,
        history_review_result,
    ) = _build_context()
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
        cross_asset_result,
        ai_advice_result,
        history_review_result,
    )

    (reports_dir / "today_action.md").write_text(
        _write_today_action(decision_result, dca_result, allocation_rebalance_result),
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
