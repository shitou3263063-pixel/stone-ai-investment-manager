from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

from agents.decision_agent import DecisionAgent
from agents.market_agent import MarketAgent
from agents.portfolio_agent import PortfolioAgent
from agents.report_agent import ReportAgent
from agents.risk_agent import RiskAgent
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root
from utils.email_sender import send_email_report


def _category_map(portfolio_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["category"]: item for item in portfolio_result["categories"]}


def _build_monitor_state(
    market_result: dict[str, Any],
    portfolio_result: dict[str, Any],
    risk_result: dict[str, Any],
    decision_result: dict[str, Any],
) -> dict[str, Any]:
    """生成用于去重的状态。状态不变时不重复提醒。"""

    categories = _category_map(portfolio_result)
    return {
        "market_score": market_result["market_score"],
        "market_risk_score": market_result["market_risk_score"],
        "offense_index": market_result["offense_index"],
        "defense_index": market_result["defense_index"],
        "risk_level": market_result["risk_level"],
        "operation_level": decision_result["operation_level"],
        "buy_orders": decision_result["buy_orders"],
        "sell_orders": decision_result["sell_orders"],
        "cash_to_use_wan": decision_result["cash_to_use_wan"],
        "category_deviation": {
            name: round(item["deviation_ratio"], 4)
            for name, item in sorted(categories.items())
        },
    }


def _has_emergency(
    market_result: dict[str, Any],
    portfolio_result: dict[str, Any],
    risk_result: dict[str, Any],
    decision_result: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    categories = _category_map(portfolio_result)
    rules = config.get("rules", {})
    gold_ratio = categories["黄金"]["current_ratio"]
    cash_ratio = categories["现金"]["current_ratio"]

    return any(
        [
            decision_result["operation_level"].startswith(("A", "B", "D")),
            market_result.get("high_market_risk", False),
            market_result.get("nasdaq_large_drawdown", False),
            gold_ratio > float(rules.get("gold_reduce_only_threshold", 0.15)),
            cash_ratio < float(rules.get("cash_minimum_ratio", 0.05)),
        ]
    )


def run_monitor(send_email: bool = True) -> str:
    """高频运行：有新风险才生成提醒并发送邮件。"""

    root = project_root()
    config = load_config(root / "data" / "config.yaml")
    portfolio = load_portfolio(root / "data" / "portfolio.csv")
    market_data = load_market_data(root / "data" / "market_data.csv")

    market_result = MarketAgent(config, market_data).analyze()
    portfolio_result = PortfolioAgent(config, portfolio).analyze()
    risk_result = RiskAgent(config, portfolio_result, market_result).analyze()
    decision_result = DecisionAgent(config, market_result, portfolio_result, risk_result).decide()

    state_path = root / "reports" / "last_emergency_state.json"
    current_state = _build_monitor_state(market_result, portfolio_result, risk_result, decision_result)
    previous_state = None
    if state_path.exists():
        previous_state = json.loads(state_path.read_text(encoding="utf-8"))

    if not _has_emergency(market_result, portfolio_result, risk_result, decision_result, config):
        if send_email:
            state_path.write_text(json.dumps(current_state, ensure_ascii=False, indent=2), encoding="utf-8")
        return "本次未触发紧急提醒。"

    if previous_state == current_state:
        return "本次风险状态没有变化，不重复发送紧急提醒。"

    event = f"投资组合触发{decision_result['operation_level']}风险监控"
    report_agent = ReportAgent(market_result, portfolio_result, risk_result, decision_result, config)
    content = report_agent.generate_emergency_alert(
        event=event,
        report_date=date.today(),
    )
    alert_path = root / "reports" / "emergency_alert.md"
    report_agent.save(content, alert_path)

    email_result = False
    if send_email:
        email_result = send_email_report(config, alert_path)
        state_path.write_text(json.dumps(current_state, ensure_ascii=False, indent=2), encoding="utf-8")

    return f"{content}\n邮件发送：{'成功' if email_result else '未发送或失败'}"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    no_email = "--no-email" in sys.argv
    print(run_monitor(send_email=not no_email))
