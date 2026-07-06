from __future__ import annotations

from typing import Any


CANONICAL_CATEGORIES = ["美股", "港股", "A股", "债券", "黄金", "现金"]

CATEGORY_ALIASES = {
    "中国债券": "债券",
    "债券": "债券",
    "黄金": "黄金",
    "现金": "现金",
    "美股": "美股",
    "港股": "港股",
    "A股": "A股",
}


class PortfolioAgent:
    """Portfolio Agent：计算资产占比和目标偏离。"""

    def __init__(self, config: dict[str, Any], portfolio: list[dict[str, Any]]):
        self.config = config
        self.portfolio = portfolio

    def analyze(self) -> dict[str, Any]:
        normalized_portfolio = self._normalize_portfolio()
        total_assets = sum(item["amount_wan"] for item in normalized_portfolio)
        category_amounts = self._sum_by_category(normalized_portfolio)
        target_allocation = self.config.get("target_allocation", {})
        recognized_categories = sorted(category_amounts.keys(), key=self._category_sort_key)

        holdings = []
        for item in normalized_portfolio:
            category_total = category_amounts[item["category"]]
            holdings.append(
                {
                    "category": item["category"],
                    "name": item["name"],
                    "amount_wan": item["amount_wan"],
                    "portfolio_ratio": item["amount_wan"] / total_assets if total_assets else 0.0,
                    "category_ratio": item["amount_wan"] / category_total if category_total else 0.0,
                    "quantity": item.get("quantity"),
                    "unit": item.get("unit", ""),
                    "note": item.get("note", ""),
                    "valuation_status": item.get("valuation_status", "manual"),
                    "valuation_note": item.get("valuation_note", ""),
                    "price_cny_per_gram": item.get("price_cny_per_gram"),
                    "recognized_category": item["category"],
                }
            )

        categories = []
        for category, target_ratio in target_allocation.items():
            amount = category_amounts.get(category, 0.0)
            current_ratio = amount / total_assets if total_assets else 0.0
            target_amount = total_assets * float(target_ratio)
            categories.append(
                {
                    "category": category,
                    "amount_wan": amount,
                    "current_ratio": current_ratio,
                    "target_ratio": float(target_ratio),
                    "deviation_ratio": current_ratio - float(target_ratio),
                    "deviation_amount_wan": amount - target_amount,
                }
            )

        categories.sort(key=lambda item: abs(item["deviation_ratio"]), reverse=True)

        return {
            "total_assets_wan": total_assets,
            "holdings": holdings,
            "categories": categories,
            "category_amounts": category_amounts,
            "recognized_categories": recognized_categories,
            "data_warnings": self._build_data_warnings(normalized_portfolio),
            "unvalued_assets": self._unvalued_assets(normalized_portfolio),
            "has_unvalued_assets": any(item.get("valuation_status") == "unvalued" for item in normalized_portfolio),
        }

    def _normalize_portfolio(self) -> list[dict[str, Any]]:
        normalized = []
        for item in self.portfolio:
            category = CATEGORY_ALIASES.get(item["category"], item["category"])
            normalized.append({**item, "category": category})
        return normalized

    def _sum_by_category(self, portfolio: list[dict[str, Any]]) -> dict[str, float]:
        category_amounts: dict[str, float] = {}
        for item in portfolio:
            category = item["category"]
            category_amounts[category] = category_amounts.get(category, 0.0) + item["amount_wan"]

        for category in CANONICAL_CATEGORIES:
            category_amounts.setdefault(category, 0.0)

        return category_amounts

    def _build_data_warnings(self, portfolio: list[dict[str, Any]]) -> list[str]:
        warnings = []
        unknown_categories = sorted(
            {
                item["category"]
                for item in portfolio
                if item["category"] not in CANONICAL_CATEGORIES
            }
        )
        if unknown_categories:
            warnings.append(f"发现未识别资产类别：{', '.join(unknown_categories)}。")
        for item in portfolio:
            valuation_note = str(item.get("valuation_note", "") or "").strip()
            if item.get("valuation_status") == "unvalued" and valuation_note:
                warnings.append(f"未估值资产：{valuation_note}；当前总资产与黄金占比暂未包含该资产。")
            elif item.get("valuation_status") == "estimated" and valuation_note:
                warnings.append(f"金条估值：{valuation_note}")
        return warnings

    def _unvalued_assets(self, portfolio: list[dict[str, Any]]) -> list[str]:
        assets = []
        for item in portfolio:
            if item.get("valuation_status") == "unvalued":
                assets.append(str(item.get("valuation_note") or item.get("name") or "未估值资产"))
        return assets

    def _category_sort_key(self, category: str) -> tuple[int, str]:
        if category in CANONICAL_CATEGORIES:
            return CANONICAL_CATEGORIES.index(category), category
        return len(CANONICAL_CATEGORIES), category
