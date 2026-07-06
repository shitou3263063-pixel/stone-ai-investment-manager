from __future__ import annotations

from typing import Any


class RebalanceAdvisor:
    """根据当前资产比例生成今日调仓建议，不执行交易。"""

    def __init__(self, portfolio_result: dict[str, Any], decision_result: dict[str, Any]):
        self.portfolio = portfolio_result
        self.decision = decision_result

    def analyze(self) -> dict[str, Any]:
        categories = {item["category"]: item for item in self.portfolio["categories"]}
        stock_ratio = (
            categories["美股"]["current_ratio"]
            + categories["港股"]["current_ratio"]
            + categories["A股"]["current_ratio"]
        )
        cash_ratio = categories["现金"]["current_ratio"]
        gold_ratio = categories["黄金"]["current_ratio"]
        bond_ratio = categories["债券"]["current_ratio"]

        warnings: list[str] = []
        suggestions: list[str] = []
        if self.portfolio.get("has_unvalued_assets"):
            unvalued_text = "、".join(self.portfolio.get("unvalued_assets", []))
            warnings.append(f"存在未估值资产：{unvalued_text}；当前总资产和资产占比不完整，今天不做比例调仓。")

        if stock_ratio > 0.70:
            warnings.append("股票类资产占比过高，组合波动可能明显上升，建议降低进攻风险。")
        else:
            suggestions.append(f"股票类资产占比{stock_ratio * 100:.2f}%，未达到过高区间。")

        if cash_ratio < 0.05:
            warnings.append("现金低于5%，应暂停加仓，优先恢复现金。")
        else:
            suggestions.append(f"现金占比{cash_ratio * 100:.2f}%，流动性暂时高于5%底线。")

        if gold_ratio > 0.15:
            warnings.append("黄金占比超过15%，不要继续追高；是否减仓需结合趋势和避险环境分批判断。")

        if bond_ratio > 0.28:
            suggestions.append("债券占比较高，组合稳定性较好，但也会压低长期增长弹性。")

        today_suggestion = self._build_today_suggestion()

        return {
            "stock_ratio": stock_ratio,
            "cash_ratio": cash_ratio,
            "gold_ratio": gold_ratio,
            "bond_ratio": bond_ratio,
            "warnings": warnings,
            "suggestions": suggestions,
            "today_suggestion": today_suggestion,
            "disclaimer": "仅供投资辅助，不构成投资建议；系统不会自动交易，也不保证收益。",
        }

    def _build_today_suggestion(self) -> str:
        buy_orders = self.decision.get("buy_orders", [])
        sell_orders = self.decision.get("sell_orders", [])
        wait_orders = self.decision.get("wait_orders", [])

        parts = []
        if buy_orders:
            buy_text = "、".join(f"{item['name']} {item['amount_wan']:.2f}万元" for item in buy_orders)
            parts.append(f"建议买入观察清单：{buy_text}")
        if sell_orders:
            sell_text = "、".join(f"{item['name']} {item['amount_wan']:.2f}万元" for item in sell_orders)
            parts.append(f"建议卖出观察清单：{sell_text}")
        if wait_orders:
            wait_text = "、".join(f"{item['name']}" for item in wait_orders)
            parts.append(f"建议等待：{wait_text}")

        if not parts:
            return "今日不需要调仓，继续观察。"
        return "；".join(parts) + "。所有操作均需人工确认。"
