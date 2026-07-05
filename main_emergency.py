from __future__ import annotations

from datetime import date
import sys

from agents.decision_agent import DecisionAgent
from agents.market_agent import MarketAgent
from agents.portfolio_agent import PortfolioAgent
from agents.report_agent import ReportAgent
from agents.risk_agent import RiskAgent
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root


def run_emergency(event: str | None = None, why_important: str | None = None) -> str:
    """手动运行：生成紧急提醒。"""

    root = project_root()
    config = load_config(root / "data" / "config.yaml")
    portfolio = load_portfolio(root / "data" / "portfolio.csv")
    market_data = load_market_data(root / "data" / "market_data.csv")

    market_result = MarketAgent(config, market_data).analyze()
    portfolio_result = PortfolioAgent(config, portfolio).analyze()
    risk_result = RiskAgent(config, portfolio_result, market_result).analyze()
    decision_result = DecisionAgent(config, market_result, portfolio_result, risk_result).decide()

    default_event = "资产配置触发紧急再平衡检查"
    report_agent = ReportAgent(market_result, portfolio_result, risk_result, decision_result, config)
    content = report_agent.generate_emergency_alert(
        event=event or default_event,
        report_date=date.today(),
        why_important=why_important,
    )
    report_agent.save(content, root / "reports" / "emergency_alert.md")
    return content


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = sys.argv[1:]
    event_arg = args[0] if args else None
    why_arg = args[1] if len(args) > 1 else None
    print(run_emergency(event_arg, why_arg))
