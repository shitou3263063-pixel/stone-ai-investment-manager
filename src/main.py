from __future__ import annotations

from datetime import date
from pathlib import Path
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
from src.notifier.push_notifier import send_push_summary
from src.notifier.wecom_app_notifier import send_wecom_app_summary
from src.notifier.wechat_work_notifier import send_wechat_work_summary
from src.risk.vix_risk import analyze_vix_risk
from src.strategy.dca_engine import build_dca_plan
from src.strategy.rebalance_engine import build_rebalance_plan
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root
from utils.market_data_provider import fetch_yfinance_market_data


VERSION_NAME = "Stone AI Investment Manager Pro V11"


def _orders_text(orders: list[dict[str, Any]]) -> str:
    if not orders:
        return "无"
    return "\n".join(
        f"- {item['name']}：{item['amount_wan']:.2f}万元，置信度{item['confidence']}%。{item['reason']}"
        for item in orders
    )


def _wait_text(wait_orders: list[dict[str, Any]]) -> str:
    if not wait_orders:
        return "无"
    return "\n".join(
        f"- {item['name']}：{item['action']}，置信度{item['confidence']}%。{item['reason']}"
        for item in wait_orders
    )


def _risk_notes_text(notes: list[str]) -> str:
    if not notes:
        return "- 暂无新增风险。"
    return "\n".join(f"- {note}" for note in notes)


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


def _write_rebalance_advice(
    path: Path,
    portfolio_result: dict[str, Any],
    risk_result: dict[str, Any],
    decision_result: dict[str, Any],
    rebalance_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
) -> None:
    lines = [
        f"# {VERSION_NAME} - 调仓建议",
        "",
        f"- 总资产：{portfolio_result['total_assets_wan']:.2f} 万元",
        f"- 操作等级：{decision_result['operation_level']}",
        f"- 今日是否调仓：{'是' if decision_result['today_rebalance'] else '否'}",
        f"- 整体置信度：{decision_result['confidence']}%",
        "",
        "## 规则触发",
        "",
    ]
    for item in risk_result["triggered_rules"]:
        lines.append(
            f"- {item['category']}：偏离{item['deviation_ratio'] * 100:.2f}%，"
            f"偏离金额{item['deviation_amount_wan']:.2f}万元。"
        )

    lines.extend(
        [
            "",
            "## 建议买入",
            "",
            _orders_text(decision_result["buy_orders"]),
            "",
            "## 建议卖出",
            "",
            _orders_text(decision_result["sell_orders"]),
            "",
            "## 建议等待",
            "",
            _wait_text(decision_result["wait_orders"]),
            "",
            "## 今日调仓建议",
            "",
            (
                rebalance_result.get("today_suggestion", "暂无")
                if allocation_rebalance_result.get("need_rebalance")
                else f"本轮再平衡模块未提示必须调仓；旧规则观察清单：{rebalance_result.get('today_suggestion', '暂无')}"
            ),
            "",
            "## 配置再平衡",
            "",
            allocation_rebalance_result.get("summary", "暂无"),
            "",
            "\n".join(f"- {item}" for item in allocation_rebalance_result.get("directions", [])),
            "",
            "## 辅助声明",
            "",
            allocation_rebalance_result.get(
                "disclaimer",
                rebalance_result.get("disclaimer", "仅供投资辅助，不构成投资建议。"),
            ),
            "",
            "## 最大风险",
            "",
            decision_result["max_risk"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_dca_reminder(path: Path, dca_result: dict[str, Any], decision_result: dict[str, Any]) -> None:
    lines = [
        f"# {VERSION_NAME} - 定投提醒",
        "",
        f"- 本月定投预算：{float(dca_result.get('monthly_budget', 0.0)):.2f} 元",
        f"- 今日是否继续定投：{'是' if dca_result.get('today_continue') else '否'}",
        f"- 今日建议定投合计：{float(dca_result.get('total_suggested_amount', 0.0)):.2f} 元",
        f"- 操作等级：{decision_result['operation_level']}",
        "",
        "## 本月计划",
        "",
        *[
            f"- {item['name']}（{item['symbol']}）：基础{item['base_amount']:.2f}元，"
            f"建议{item['suggested_amount']:.2f}元，动作：{item['action']}。"
            for item in dca_result.get("targets", [])
        ],
        "",
        "## V11 提醒",
        "",
        "- 不接入真实交易，不自动买卖。",
        f"- {dca_result.get('summary', '当前市场风险偏高时，大额补仓分批执行。')}",
        f"- {dca_result.get('disclaimer', '仅供投资辅助，不构成投资建议。')}",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_risk_alert(path: Path, market_result: dict[str, Any], decision_result: dict[str, Any]) -> None:
    lines = [
        f"# {VERSION_NAME} - 风险预警",
        "",
        f"- 市场风险评分：{market_result['market_risk_score']} / 100",
        f"- 进攻指数：{market_result['offense_index']} / 100",
        f"- 防守指数：{market_result['defense_index']} / 100",
        f"- 操作等级：{decision_result['operation_level']}",
        "",
        "## 风险说明",
        "",
        _risk_notes_text(decision_result["risk_notes"]),
        "",
        "## 最大风险",
        "",
        decision_result["max_risk"],
        "",
        "## 一句话结论",
        "",
        decision_result["one_sentence_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_today_action(
    path: Path,
    decision_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
) -> None:
    trade_text = "是，需人工确认" if decision_result.get("today_rebalance") else "否，系统只提醒不下单"
    dca_text = "是，按定投计划执行" if dca_result.get("today_continue") else "否，今日不继续定投"
    rebalance_text = "是，需人工确认" if allocation_rebalance_result.get("need_rebalance") else "否，暂不需要再平衡"
    lines = [
        f"今日是否交易：{trade_text}",
        f"今日是否定投：{dca_text}",
        f"今日是否再平衡：{rebalance_text}",
        f"今日最大风险：{decision_result.get('max_risk', '暂无')}",
        f"一句话结论：{decision_result.get('one_sentence_conclusion', '暂无')}（仅供投资辅助，最终由你自己执行）",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


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

    daily_report = report_agent.generate_daily_report(today)
    weekly_report = report_agent.generate_weekly_report(today)
    monthly_rebalance = report_agent.generate_monthly_rebalance(today)

    (reports_dir / "daily_report.md").write_text(daily_report, encoding="utf-8")
    (reports_dir / "weekly_report.md").write_text(weekly_report, encoding="utf-8")
    (reports_dir / "monthly_rebalance.md").write_text(monthly_rebalance, encoding="utf-8")
    _write_rebalance_advice(
        reports_dir / "rebalance_advice.md",
        portfolio_result,
        risk_result,
        decision_result,
        rebalance_result,
        allocation_rebalance_result,
    )
    _write_dca_reminder(reports_dir / "dca_reminder.md", dca_result, decision_result)
    _write_risk_alert(reports_dir / "risk_alert.md", market_result, decision_result)
    _write_today_action(
        reports_dir / "today_action.md",
        decision_result,
        dca_result,
        allocation_rebalance_result,
    )
    email_result = send_daily_reports(reports_dir=reports_dir, subject_date=today)
    wecom_app_result = send_wecom_app_summary(reports_dir=reports_dir)
    wechat_work_result = send_wechat_work_summary(reports_dir=reports_dir)
    push_result = send_push_summary(reports_dir=reports_dir)

    return "\n".join(
        [
            f"{VERSION_NAME} 运行完成",
            f"总资产：{portfolio_result['total_assets_wan']:.2f} 万元",
            f"操作等级：{decision_result['operation_level']}",
            f"今日是否调仓：{'是' if decision_result['today_rebalance'] else '否'}",
            f"邮件通知：{email_result['message']}",
            f"企业微信应用推送：{wecom_app_result['message']}",
            f"企业微信推送：{wechat_work_result['message']}",
            f"远程推送：{push_result['message']}",
            "已生成：",
            "- reports/today_action.md",
            "- reports/daily_report.md",
            "- reports/weekly_report.md",
            "- reports/monthly_rebalance.md",
            "- reports/rebalance_advice.md",
            "- reports/dca_reminder.md",
            "- reports/risk_alert.md",
        ]
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(run())
