from __future__ import annotations

from typing import Any


class DecisionAgent:
    """Decision Agent：综合市场、仓位、规则和长期风格，给出最终建议。"""

    def __init__(
        self,
        config: dict[str, Any],
        market_result: dict[str, Any],
        portfolio_result: dict[str, Any],
        risk_result: dict[str, Any],
    ):
        self.config = config
        self.market = market_result
        self.portfolio = portfolio_result
        self.risk = risk_result
        self.expected_returns = config.get("expected_annual_returns", {})

    def decide(self) -> dict[str, Any]:
        categories = {item["category"]: item for item in self.portfolio["categories"]}
        holdings = {item["name"]: item for item in self.portfolio["holdings"]}
        special = self.risk["special_rules"]

        if special["cash_below_5"]:
            return self._cash_protection_decision(holdings)
        if self.portfolio.get("has_unvalued_assets"):
            return self._data_incomplete_decision(holdings)

        max_deviation = max(abs(item["deviation_ratio"]) for item in categories.values())
        operation_level = self._operation_level(max_deviation)
        buy_ratio = self._buy_ratio_by_market()

        sell_orders, wait_orders, exception_notes = self._build_sell_and_wait_orders(categories)
        available_amount = sum(item["amount_wan"] for item in sell_orders)
        cash_to_use = self._cash_to_use(categories)
        available_amount += cash_to_use

        buy_orders = self._build_buy_orders(categories, available_amount * buy_ratio)
        hold_list = self._build_hold_list(holdings, buy_orders, sell_orders)
        wait_orders.extend(self._build_market_wait_orders())

        today_rebalance = operation_level.startswith("A")
        expected_return = self._expected_incremental_return(buy_orders, sell_orders, cash_to_use)

        return {
            "operation_level": operation_level,
            "today_rebalance": today_rebalance,
            "need_action": operation_level.startswith(("A", "B")),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "hold_list": hold_list,
            "wait_orders": wait_orders,
            "cash_to_use_wan": round(cash_to_use, 2),
            "confidence": self._overall_confidence(operation_level),
            "why": self._build_why(),
            "exception_notes": exception_notes,
            "risk_notes": self._build_risk_notes(buy_orders, sell_orders, cash_to_use),
            "expected_incremental_return_wan": expected_return,
            "max_risk": self._max_risk_text(buy_orders, sell_orders),
            "one_sentence_conclusion": self._one_sentence_conclusion(operation_level, exception_notes),
        }

    def _cash_protection_decision(self, holdings: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "operation_level": "D级：不要操作",
            "today_rebalance": False,
            "need_action": True,
            "buy_orders": [],
            "sell_orders": [],
            "hold_list": list(holdings.keys()),
            "wait_orders": [{"name": "所有加仓", "action": "等待", "confidence": 90, "reason": "现金低于5%，优先恢复现金。"}],
            "cash_to_use_wan": 0.0,
            "confidence": 90,
            "why": ["现金低于5%，触发暂停加仓规则。"],
            "exception_notes": [],
            "risk_notes": ["现金缓冲不足，继续加仓会削弱抗波动能力。"],
            "expected_incremental_return_wan": 0.0,
            "max_risk": "最大风险是流动性不足。",
            "one_sentence_conclusion": "现金不足时先保流动性，今天不要加仓风险资产。",
        }

    def _data_incomplete_decision(self, holdings: dict[str, dict[str, Any]]) -> dict[str, Any]:
        unvalued = "、".join(self.portfolio.get("unvalued_assets", [])) or "存在未估值资产"
        return {
            "operation_level": "C级：继续观察",
            "today_rebalance": False,
            "need_action": False,
            "buy_orders": [],
            "sell_orders": [],
            "hold_list": list(holdings.keys()),
            "wait_orders": [
                {
                    "name": "比例驱动调仓",
                    "action": "等待",
                    "confidence": 90,
                    "reason": f"{unvalued}，当前总资产和黄金占比不完整，先等金价估值成功。",
                }
            ],
            "cash_to_use_wan": 0.0,
            "confidence": 90,
            "why": [f"{unvalued}，暂停根据当前占比做调仓决策。"],
            "exception_notes": [f"数据例外：{unvalued}；是否立即执行调仓：否。"],
            "risk_notes": ["未估值资产会扭曲总资产、黄金占比和其他资产占比。"],
            "expected_incremental_return_wan": 0.0,
            "max_risk": "最大风险是金条未估值导致资产占比失真，进而误判再平衡方向。",
            "one_sentence_conclusion": "金条暂未估值时不做比例调仓，先继续定投并等待金价估值恢复。",
        }

    def _operation_level(self, max_deviation: float) -> str:
        if self.market["high_market_risk"]:
            if max_deviation > 0.08:
                return "B级：建议本周执行"
            return "C级：继续观察"
        if max_deviation > 0.12 and self.market["suitable_to_add"]:
            return "A级：建议立即执行"
        if max_deviation > 0.08:
            return "B级：建议本周执行"
        if max_deviation > 0.05:
            return "C级：观察，优先用新增资金修正"
        return "C级：继续观察"

    def _buy_ratio_by_market(self) -> float:
        if self.market["high_market_risk"]:
            return 0.50
        if self.market["market_risk_score"] >= 55:
            return 0.70
        return 1.00

    def _build_sell_and_wait_orders(
        self,
        categories: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        sell_orders: list[dict[str, Any]] = []
        wait_orders: list[dict[str, Any]] = []
        exception_notes: list[str] = []
        category_rules = {item["category"]: item for item in self.risk["category_rules"]}

        bond_rule = category_rules.get("债券")
        if bond_rule and bond_rule["deviation_ratio"] > 0.03:
            amount = bond_rule["max_single_sell_wan"]
            if self.market["defense_index"] >= 65:
                amount *= 0.50
                confidence = 65
                reason = "债券明显超配，但当前防守指数较高，先减一半，保留组合防守能力。"
            else:
                confidence = 75
                reason = "债券明显超配，且市场防守需求不强，可按规则分批减仓。"
            sell_orders.append(
                {
                    "name": "债券",
                    "category": "债券",
                    "amount_wan": round(amount, 2),
                    "confidence": confidence,
                    "reason": reason,
                }
            )

        gold_rule = category_rules.get("黄金")
        if gold_rule and gold_rule["deviation_ratio"] > 0.03:
            if self.market["gold_trend_strong"] or self.market["gold_defense_supported"]:
                wait_orders.append(
                    {
                        "name": "黄金",
                        "action": "等待确认信号",
                        "confidence": 75,
                        "reason": "黄金仓位偏高，但当前仍有趋势和避险支撑，不适合一次性减仓。",
                    }
                )
                exception_notes.append(
                    "黄金规则触发：是；是否立即执行：否；原因：当前市场环境支持黄金继续持有，等待趋势转弱后再分批减仓。"
                )
            else:
                sell_orders.append(
                    {
                        "name": "黄金",
                        "category": "黄金",
                        "amount_wan": gold_rule["max_single_sell_wan"],
                        "confidence": 70,
                        "reason": "黄金超配且趋势支撑不足，可分批减仓。",
                    }
                )

        return sell_orders, wait_orders, exception_notes

    def _cash_to_use(self, categories: dict[str, dict[str, Any]]) -> float:
        cash = categories["现金"]
        if cash["deviation_ratio"] <= 0.03:
            return 0.0
        if self.market["high_market_risk"]:
            return max(0.0, cash["deviation_amount_wan"] * 0.50)
        return max(0.0, cash["deviation_amount_wan"])

    def _build_buy_orders(
        self,
        categories: dict[str, dict[str, Any]],
        available_amount: float,
    ) -> list[dict[str, Any]]:
        buy_orders: list[dict[str, Any]] = []
        remaining = max(0.0, available_amount)

        a_need = max(0.0, -categories["A股"]["deviation_amount_wan"])
        if categories["A股"]["deviation_ratio"] < -0.03 and remaining > 0:
            amount = min(a_need, remaining)
            buy_orders.append(
                {
                    "name": "沪深300ETF",
                    "category": "A股",
                    "amount_wan": round(amount, 2),
                    "confidence": 70,
                    "reason": "A股低配超过3%，估值偏低，适合用宽基ETF小步补足。",
                }
            )
            remaining -= amount

        us_need = max(0.0, -categories["美股"]["deviation_amount_wan"])
        if categories["美股"]["deviation_ratio"] < -0.03 and remaining > 0:
            amount = min(us_need, remaining)
            voo_amount = amount * 8 / 14
            qqq_amount = amount * 6 / 14
            buy_orders.extend(
                [
                    {
                        "name": "VOO",
                        "category": "美股",
                        "amount_wan": round(voo_amount, 2),
                        "confidence": 65,
                        "reason": "美股严重低配，但市场风险偏高，先用宽基ETF分批补。",
                    },
                    {
                        "name": "QQQ",
                        "category": "美股",
                        "amount_wan": round(qqq_amount, 2),
                        "confidence": 60,
                        "reason": "纳指方向可补，但科技股波动较高，金额低于VOO。",
                    },
                ]
            )

        return [item for item in buy_orders if item["amount_wan"] > 0]

    def _build_market_wait_orders(self) -> list[dict[str, Any]]:
        waits = []
        if self.market["high_market_risk"]:
            waits.append(
                {
                    "name": "进攻型资产大幅加仓",
                    "action": "等待",
                    "confidence": 80,
                    "reason": "市场风险评分偏高，长期稳健风格不适合一次性大幅切换仓位。",
                }
            )
        return waits

    def _build_hold_list(
        self,
        holdings: dict[str, dict[str, Any]],
        buy_orders: list[dict[str, Any]],
        sell_orders: list[dict[str, Any]],
    ) -> list[str]:
        buy_names = {item["name"] for item in buy_orders}
        sell_categories = {item["category"] for item in sell_orders}
        hold_list = []
        for name, item in holdings.items():
            if name in buy_names:
                continue
            if item["category"] in sell_categories and item["category"] in {"黄金", "债券"}:
                hold_list.append(f"剩余{name}")
            else:
                hold_list.append(name)
        return hold_list

    def _build_why(self) -> list[str]:
        notes = list(self.risk["risk_notes"])
        notes.append("投资风格为5年以上长期投资、低频交易、优先控制回撤，因此调仓采用分批和例外机制。")
        return notes

    def _build_risk_notes(
        self,
        buy_orders: list[dict[str, Any]],
        sell_orders: list[dict[str, Any]],
        cash_to_use: float,
    ) -> list[str]:
        buy_total = sum(item["amount_wan"] for item in buy_orders)
        sell_total = sum(item["amount_wan"] for item in sell_orders)
        return [
            f"本次建议买入权益资产约{buy_total:.2f}万元，若短期下跌10%，账面回撤约{buy_total * 0.10:.2f}万元。",
            f"本次建议卖出防守资产约{sell_total:.2f}万元，若风险事件继续发酵，组合防守能力会略有下降。",
            f"动用现金约{cash_to_use:.2f}万元，仍需保留5%现金底线。",
        ]

    def _expected_incremental_return(
        self,
        buy_orders: list[dict[str, Any]],
        sell_orders: list[dict[str, Any]],
        cash_to_use: float,
    ) -> float:
        new_return = sum(
            item["amount_wan"] * float(self.expected_returns.get(item["category"], 0.0))
            for item in buy_orders
        )
        old_return = sum(
            item["amount_wan"] * float(self.expected_returns.get(item["category"], 0.0))
            for item in sell_orders
        )
        old_return += cash_to_use * float(self.expected_returns.get("现金", 0.0))
        return round(new_return - old_return, 2)

    def _max_risk_text(self, buy_orders: list[dict[str, Any]], sell_orders: list[dict[str, Any]]) -> str:
        buy_total = sum(item["amount_wan"] for item in buy_orders)
        sell_total = sum(item["amount_wan"] for item in sell_orders)
        return (
            f"最大风险是买入的权益资产短期回撤和卖出{sell_total:.2f}万元防守资产后的对冲能力下降；"
            f"若权益资产短期下跌20%，新增仓位回撤约{buy_total * 0.20:.2f}万元。"
        )

    def _overall_confidence(self, operation_level: str) -> int:
        if operation_level.startswith("B"):
            return 72
        if operation_level.startswith("A"):
            return 78
        if operation_level.startswith("D"):
            return 90
        return 65

    def _one_sentence_conclusion(self, operation_level: str, exception_notes: list[str]) -> str:
        if exception_notes:
            return "规则已经触发，但当前市场更适合分批调仓，黄金先等待趋势转弱再减。"
        if operation_level.startswith("A"):
            return "市场和配置同时支持行动，今天可以执行再平衡。"
        if operation_level.startswith("B"):
            return "配置偏离需要处理，但应按长期稳健风格在本周分批执行。"
        if operation_level.startswith("D"):
            return "当前最重要的是保护现金和控制回撤。"
        return "当前没有足够强的机会或风险信号，继续观察。"
