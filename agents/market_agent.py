from __future__ import annotations

from typing import Any


class MarketAgent:
    """Market Agent：把手动市场数据整理成 V10 市场评分。"""

    def __init__(self, config: dict[str, Any], market_data: dict[str, dict[str, Any]]):
        self.config = config
        self.market_data = market_data

    def analyze(self) -> dict[str, Any]:
        market_risk_score = 45
        offense_index = 50
        defense_index = 50

        for item in self.market_data.values():
            score_impact = int(item.get("score_impact", 0))
            offense_index += score_impact
            if item.get("macro_risk") in {"中高", "高"}:
                market_risk_score += 8
            if item.get("trend") in {"偏弱", "回落"}:
                market_risk_score += 3
            if item.get("defense_support") == "是":
                defense_index += 7
            if item.get("trend") in {"偏强", "强"}:
                offense_index += 4

        market_risk_score = max(0, min(100, market_risk_score))
        offense_index = max(0, min(100, offense_index))
        defense_index = max(0, min(100, defense_index))

        if market_risk_score < 35:
            risk_level = "低"
        elif market_risk_score < 55:
            risk_level = "中"
        elif market_risk_score < 75:
            risk_level = "中高"
        else:
            risk_level = "高"

        rules = self.config.get("rules", {})
        nasdaq_drawdown = self._get_number("纳斯达克涨跌幅", default=0.0)
        nasdaq_threshold = float(rules.get("nasdaq_drawdown_threshold", -0.10))
        suitable_to_add = offense_index >= 55 and market_risk_score < 65
        suitable_to_reduce = defense_index >= 60 or market_risk_score >= 65

        return {
            "market_score": max(0, min(100, 100 - market_risk_score + int((offense_index - 50) * 0.3))),
            "market_risk_score": market_risk_score,
            "offense_index": offense_index,
            "defense_index": defense_index,
            "risk_level": risk_level,
            "suitable_to_add": suitable_to_add,
            "suitable_to_reduce": suitable_to_reduce,
            "high_market_risk": market_risk_score >= 65,
            "nasdaq_large_drawdown": nasdaq_drawdown <= nasdaq_threshold,
            "gold_trend_strong": self._is_trend_strong("黄金涨跌幅"),
            "gold_defense_supported": self._defense_supported("黄金涨跌幅"),
            "summary": self._build_summary(),
            "impact_notes": self._build_impact_notes(),
            "portfolio_impacts": self._build_portfolio_impacts(),
            "raw_market_data": self.market_data,
        }

    def _get_number(self, indicator: str, default: float = 0.0) -> float:
        value = self.market_data.get(indicator, {}).get("value", default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _value(self, indicator: str) -> str:
        return str(self.market_data.get(indicator, {}).get("value", "未填写"))

    def _trend(self, indicator: str) -> str:
        return str(self.market_data.get(indicator, {}).get("trend", "未填写"))

    def _valuation(self, indicator: str) -> str:
        return str(self.market_data.get(indicator, {}).get("valuation", "未填写"))

    def _defense_supported(self, indicator: str) -> bool:
        return self.market_data.get(indicator, {}).get("defense_support") == "是"

    def _is_trend_strong(self, indicator: str) -> bool:
        return self._trend(indicator) in {"偏强", "强"}

    def _build_summary(self) -> str:
        """日报要求三句话以内，这里固定生成三句话。"""

        us_stock = self._value("标普500涨跌幅")
        a_share = self._value("沪深300涨跌幅")
        hk_stock = self._value("恒生科技涨跌幅")
        gold = self._value("黄金涨跌幅")
        us10y = self._value("美国10年国债收益率")
        dxy = self._value("美元指数变化")
        fed = self._value("美联储政策")
        vix = self._value("VIX指数")

        return (
            f"美股{us_stock}，A股{a_share}，港股{hk_stock}。"
            f"黄金{gold}，美国10年国债收益率为{us10y}，美元指数{dxy}。"
            f"美联储政策状态为{fed}，VIX指数{vix}，操作应兼顾再平衡和回撤控制。"
        )

    def _build_impact_notes(self) -> list[str]:
        notes: list[str] = []
        for key in [
            "标普500涨跌幅",
            "沪深300涨跌幅",
            "恒生科技涨跌幅",
            "黄金涨跌幅",
            "美元指数变化",
            "美国10年国债收益率",
            "美联储政策",
            "VIX指数",
            "重要事件备注",
        ]:
            note = self.market_data.get(key, {}).get("risk_note")
            if note:
                notes.append(f"{key}：{note}")
        return notes

    def _build_portfolio_impacts(self) -> dict[str, str]:
        return {
            "美股": self.market_data.get("标普500涨跌幅", {}).get("risk_note", "暂无美股影响说明"),
            "港股": self.market_data.get("恒生科技涨跌幅", {}).get("risk_note", "暂无港股影响说明"),
            "A股": self.market_data.get("沪深300涨跌幅", {}).get("risk_note", "暂无A股影响说明"),
            "黄金": self.market_data.get("黄金涨跌幅", {}).get("risk_note", "暂无黄金影响说明"),
            "债券": self.market_data.get("美国10年国债收益率", {}).get("risk_note", "暂无债券影响说明"),
            "现金": self.market_data.get("重要事件备注", {}).get("risk_note", "现金用于应对不确定性和再平衡"),
        }
