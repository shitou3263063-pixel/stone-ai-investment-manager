from __future__ import annotations

from typing import Any


class RiskAgent:
    """Risk Agent：只检查规则触发，不直接决定买卖。"""

    def __init__(
        self,
        config: dict[str, Any],
        portfolio_result: dict[str, Any],
        market_result: dict[str, Any],
    ):
        self.config = config
        self.portfolio_result = portfolio_result
        self.market_result = market_result
        self.rules = config.get("rules", {})

    def analyze(self) -> dict[str, Any]:
        categories = {item["category"]: item for item in self.portfolio_result["categories"]}
        holdings = {item["name"]: item for item in self.portfolio_result["holdings"]}
        threshold = float(self.rules.get("deviation_threshold", 0.03))

        category_rules = []
        for item in self.portfolio_result["categories"]:
            deviation_ratio = item["deviation_ratio"]
            triggered = abs(deviation_ratio) > threshold
            direction = "超配" if deviation_ratio > 0 else "低配"
            max_single_sell = 0.0
            if deviation_ratio > threshold:
                max_single_sell = item["amount_wan"] * float(self.rules.get("max_sell_ratio_per_category", 0.20))

            category_rules.append(
                {
                    "category": item["category"],
                    "triggered": triggered,
                    "direction": direction,
                    "current_ratio": item["current_ratio"],
                    "target_ratio": item["target_ratio"],
                    "deviation_ratio": deviation_ratio,
                    "deviation_amount_wan": item["deviation_amount_wan"],
                    "max_single_sell_wan": round(max_single_sell, 2),
                    "rule": "偏离目标超过5%" if triggered else "未触发偏离规则",
                }
            )

        gold_ratio = categories["黄金"]["current_ratio"]
        cash_ratio = categories["现金"]["current_ratio"]
        hk_ratio = categories["港股"]["current_ratio"]
        nvidia_ratio = holdings.get("英伟达", {}).get("portfolio_ratio", 0.0)

        special_rules = {
            "gold_over_15": gold_ratio > float(self.rules.get("gold_reduce_only_threshold", 0.15)),
            "cash_below_5": cash_ratio < float(self.rules.get("cash_minimum_ratio", 0.05)),
            "hong_kong_large_buy_allowed": hk_ratio < float(self.rules.get("hong_kong_buy_threshold", 0.06)),
            "nvidia_stop_buy": nvidia_ratio > float(self.rules.get("nvidia_stop_buy_threshold", 0.05)),
            "high_market_risk": self.market_result.get("high_market_risk", False),
            "nasdaq_large_drawdown": self.market_result.get("nasdaq_large_drawdown", False),
        }

        triggered_rules = [item for item in category_rules if item["triggered"]]
        notes = self._build_notes(category_rules, special_rules, nvidia_ratio)

        return {
            "need_attention": bool(triggered_rules or any(special_rules.values())),
            "category_rules": category_rules,
            "triggered_rules": triggered_rules,
            "special_rules": special_rules,
            "risk_notes": notes,
        }

    def _build_notes(
        self,
        category_rules: list[dict[str, Any]],
        special_rules: dict[str, bool],
        nvidia_ratio: float,
    ) -> list[str]:
        notes: list[str] = []
        for item in category_rules:
            if item["triggered"]:
                notes.append(
                    f"{item['category']}{item['direction']}{abs(item['deviation_ratio']) * 100:.2f}%，"
                    f"偏离金额{abs(item['deviation_amount_wan']):.2f}万元。"
                )

        if special_rules["gold_over_15"]:
            notes.append("黄金占比超过15%，触发黄金仓位警戒，但是否减仓需由 Decision Agent 结合趋势和避险环境判断。")
        if special_rules["cash_below_5"]:
            notes.append("现金低于5%，触发暂停加仓规则。")
        if special_rules["nvidia_stop_buy"]:
            notes.append("英伟达占总资产超过5%，触发停止继续加仓规则。")
        else:
            notes.append(f"英伟达占总资产{nvidia_ratio * 100:.2f}%，未触发5%停止加仓规则。")
        if special_rules["high_market_risk"]:
            notes.append("市场风险较高，进攻型资产加仓比例需要降低。")
        if special_rules["nasdaq_large_drawdown"]:
            notes.append("纳斯达克出现较大回撤，需要评估是否提高美股ETF定投比例。")

        return notes
