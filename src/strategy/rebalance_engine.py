from __future__ import annotations

from pathlib import Path
from typing import Any

from src.strategy.dca_engine import load_strategy_settings


TARGET_CATEGORY_MAP = {
    "us_stock": "美股",
    "hk_stock": "港股",
    "cn_stock": "A股",
    "bond": "债券",
    "gold": "黄金",
    "cash": "现金",
}


def _to_ratio(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number / 100 if number > 1 else number


def _status_for_deviation(abs_deviation: float) -> str:
    if abs_deviation < 0.03:
        return "不调仓"
    if abs_deviation < 0.05:
        return "观察"
    return "提示再平衡"


def _direction(category: str, deviation: float, status: str) -> str:
    if status == "不调仓":
        return "接近目标，继续持有。"
    if deviation < 0:
        return f"{category}低配，优先用新增资金和定投慢慢补足。"
    return f"{category}超配，优先减少新增资金投入；不建议频繁卖出长期资产。"


def build_rebalance_plan(
    portfolio_result: dict[str, Any],
    settings_path: Path | None = None,
) -> dict[str, Any]:
    """根据当前资产占比和目标配置生成再平衡建议。"""
    settings = load_strategy_settings(settings_path)
    target_allocation = settings.get("target_allocation", {}) or {}
    total_assets = float(portfolio_result.get("total_assets_wan", 0.0) or 0.0)
    category_amounts = portfolio_result.get("category_amounts", {}) or {}

    if not target_allocation or total_assets <= 0:
        return {
            "need_rebalance": False,
            "items": [],
            "directions": ["目标配置或资产数据缺失，暂不输出再平衡建议。"],
            "summary": "再平衡模块数据不足。",
            "disclaimer": "仅供投资辅助，不构成投资建议；系统不会自动交易，也不承诺收益。",
        }

    items: list[dict[str, Any]] = []
    for key, target_value in target_allocation.items():
        category = TARGET_CATEGORY_MAP.get(str(key), str(key))
        amount = float(category_amounts.get(category, 0.0) or 0.0)
        current_ratio = amount / total_assets if total_assets else 0.0
        target_ratio = _to_ratio(target_value)
        deviation = current_ratio - target_ratio
        abs_deviation = abs(deviation)
        status = _status_for_deviation(abs_deviation)
        items.append(
            {
                "category": category,
                "amount_wan": round(amount, 2),
                "current_ratio": current_ratio,
                "target_ratio": target_ratio,
                "deviation_ratio": deviation,
                "deviation_amount_wan": round(total_assets * deviation, 2),
                "status": status,
                "direction": _direction(category, deviation, status),
            }
        )

    items.sort(key=lambda item: abs(item["deviation_ratio"]), reverse=True)
    need_rebalance = any(item["status"] == "提示再平衡" for item in items)
    watch_items = [item for item in items if item["status"] == "观察"]
    rebalance_items = [item for item in items if item["status"] == "提示再平衡"]

    directions = []
    for item in rebalance_items + watch_items:
        directions.append(
            f"{item['category']}：偏离{item['deviation_ratio'] * 100:.2f}%，{item['direction']}"
        )
    if not directions:
        directions.append("各资产类别偏离均小于3%，本期不需要再平衡。")

    if need_rebalance:
        summary = "存在偏离超过5%的资产类别，建议用新增资金优先再平衡，必要时再分批调整。"
    elif watch_items:
        summary = "存在3%-5%的配置偏离，先观察并用后续新增资金微调。"
    else:
        summary = "当前配置接近本轮目标，不建议为了微小偏离频繁交易。"

    return {
        "need_rebalance": need_rebalance,
        "items": items,
        "directions": directions,
        "summary": summary,
        "rule": "偏离<3%不调仓；3%-5%观察；>5%提示再平衡。",
        "priority": "优先用新增资金再平衡，不建议频繁卖出长期资产。",
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不会自动交易，也不承诺收益。",
    }

