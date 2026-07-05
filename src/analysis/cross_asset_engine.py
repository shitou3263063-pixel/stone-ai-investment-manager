from __future__ import annotations

from typing import Any


POSITIVE_WORDS = {"偏强", "强", "上涨", "走强", "回升"}
NEGATIVE_WORDS = {"偏弱", "弱", "下跌", "走弱", "回落"}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _live_change(live_market_result: dict[str, Any], tickers: list[str]) -> float | None:
    items = live_market_result.get("items", {}) if live_market_result else {}
    changes = []
    for ticker in tickers:
        item = items.get(ticker, {})
        if item.get("status") == "ok":
            change = _to_float(item.get("change_pct"))
            if change is not None:
                changes.append(change)
    if not changes:
        return None
    return sum(changes) / len(changes)


def _manual_change(market_data: dict[str, dict[str, Any]], indicator: str) -> float | None:
    item = market_data.get(indicator, {})
    for key in ["change", "value"]:
        number = _to_float(item.get(key))
        if number is not None:
            return number
    return None


def _manual_trend(market_data: dict[str, dict[str, Any]], indicator: str) -> str:
    item = market_data.get(indicator, {})
    return str(item.get("trend") or item.get("value") or "未知")


def _direction_from_change(change: float | None, trend: str = "") -> str:
    if change is not None:
        if change > 0.2:
            return "up"
        if change < -0.2:
            return "down"
        if any(word in trend for word in POSITIVE_WORDS):
            return "up"
        if any(word in trend for word in NEGATIVE_WORDS):
            return "down"
        return "flat"
    if any(word in trend for word in POSITIVE_WORDS):
        return "up"
    if any(word in trend for word in NEGATIVE_WORDS):
        return "down"
    return "flat"


def _ratio(portfolio_result: dict[str, Any], category: str) -> float:
    for item in portfolio_result.get("categories", []):
        if item.get("category") == category:
            return float(item.get("current_ratio", 0.0) or 0.0)
    return 0.0


def _metric(
    live_market_result: dict[str, Any],
    market_data: dict[str, dict[str, Any]],
    tickers: list[str],
    indicator: str,
    invert_manual_direction: bool = False,
) -> dict[str, Any]:
    change = _live_change(live_market_result, tickers)
    source = "yfinance"
    live_available = change is not None
    if change is None:
        change = _manual_change(market_data, indicator)
        source = "market_data.csv"

    trend = _manual_trend(market_data, indicator)
    direction = _direction_from_change(change, trend)
    if invert_manual_direction and not live_available:
        if direction == "up":
            direction = "down"
        elif direction == "down":
            direction = "up"

    return {
        "change": change,
        "trend": trend,
        "direction": direction,
        "source": source,
    }


def analyze_cross_asset(
    live_market_result: dict[str, Any],
    market_data: dict[str, dict[str, Any]],
    portfolio_result: dict[str, Any],
) -> dict[str, Any]:
    """分析跨资产联动，输出可直接写入日报的组合相关判断。"""
    us_stock = _metric(live_market_result, market_data, ["VOO", "QQQ", "^GSPC", "^IXIC"], "标普500涨跌幅")
    hk_stock = _metric(live_market_result, market_data, ["3067.HK", "3033.HK", "2800.HK"], "恒生科技涨跌幅")
    cn_stock = _metric(live_market_result, market_data, ["510300.SS"], "沪深300涨跌幅")
    gold = _metric(live_market_result, market_data, ["GLD"], "黄金涨跌幅")
    bond = _metric(
        live_market_result,
        market_data,
        ["TLT", "IEF"],
        "美国10年国债收益率",
        invert_manual_direction=True,
    )
    dollar = _metric(live_market_result, market_data, ["UUP", "DX-Y.NYB"], "美元指数变化")
    vix = _metric(live_market_result, market_data, ["^VIX"], "VIX指数")

    gold_ratio = _ratio(portfolio_result, "黄金")
    us_ratio = _ratio(portfolio_result, "美股")
    hk_ratio = _ratio(portfolio_result, "港股")
    cn_ratio = _ratio(portfolio_result, "A股")
    bond_ratio = _ratio(portfolio_result, "债券")

    signals: list[str] = []

    if dollar["direction"] == "up" and gold["direction"] == "down":
        signals.append("美元指数走强且黄金回落，黄金短期可能承压；你的黄金仓位偏高，因此不建议继续追高黄金。")
    elif dollar["direction"] == "down" and gold["direction"] in {"up", "flat"}:
        signals.append("美元指数偏弱，对黄金和非美资产相对友好；但你的黄金仓位已经偏高，黄金更适合继续观察而不是追加买入。")
    else:
        signals.append("美元与黄金暂未形成强烈背离信号，黄金操作应继续看仓位和避险需求，不做追涨。")

    if bond["direction"] == "down":
        signals.append("TLT/IEF 下跌说明长债承压，若利率继续上行，科技股估值可能承压，因此不建议重仓追涨纳斯达克。")
    elif "回落" in bond["trend"] or bond["direction"] == "up":
        signals.append("长债未显示明显承压，利率压力暂时可控；但科技资产仍应分批配置，避免单日重仓追涨。")
    else:
        signals.append("美债信号中性，暂时不能确认利率方向，成长股加仓仍以定投和分批为主。")

    if vix["direction"] == "up":
        signals.append("VIX 上升代表风险偏好下降，权益资产新增仓位应放慢，优先保留现金缓冲。")
    else:
        signals.append("VIX 未显示明显上升，市场恐慌不突出；但当前组合仍不适合一次性大幅切换仓位。")

    if us_stock["direction"] == "up" and hk_stock["direction"] != "up":
        signals.append("美股上涨但港股没有同步走强，说明港股相对仍偏弱；你的港股不低配，因此不建议主动大幅加仓港股。")
    elif us_stock["direction"] == "up" and hk_stock["direction"] == "up":
        signals.append("美股和港股同步走强，风险偏好改善；但港股仍以观察和定投为主，不建议一次性追高。")
    else:
        signals.append("美股与港股强弱差异暂不鲜明，权益资产配置应继续围绕宽基 ETF 和分批定投。")

    if gold["direction"] == "up" and us_stock["direction"] == "down":
        signals.append("黄金上涨且美股下跌，说明避险情绪升温；你的黄金仓位较高，当前更适合持有对冲，不适合再加仓。")

    if dollar["direction"] == "down" and (hk_stock["direction"] == "up" or cn_stock["direction"] == "up"):
        signals.append("美元下跌且港股/A股走强，说明外资风险偏好可能改善；A股和港股可通过定投参与，不建议一次性重仓。")

    gold_judgement = (
        f"黄金当前判断：黄金仓位约{gold_ratio * 100:.2f}%。"
        "若美元走强或黄金转弱，黄金短期可能承压；由于仓位偏高，不建议继续追高，等待趋势转弱后再考虑分批减仓。"
        if gold_ratio >= 0.15
        else f"黄金当前判断：黄金仓位约{gold_ratio * 100:.2f}%，更多作为组合对冲资产，暂不需要主动追涨。"
    )

    bond_judgement = (
        f"债券当前判断：债券仓位约{bond_ratio * 100:.2f}%。"
        "如果 TLT/IEF 继续下跌，说明长债承压，科技股估值也可能受压；当前债券仓位提供稳定性，但不宜忽视利率风险。"
    )

    dollar_judgement = (
        "美元当前判断：美元偏弱时通常利好黄金、港股和A股风险偏好；美元走强时黄金和非美资产可能承压。"
        "当前不根据美元单一信号做交易，只用于调整加仓节奏。"
    )

    us_hk_relative = (
        f"美股与港股强弱对比：美股仓位约{us_ratio * 100:.2f}%，港股仓位约{hk_ratio * 100:.2f}%。"
        "若美股强而港股弱，说明资金仍偏向美国核心资产，港股继续以观察和小额定投为主。"
    )

    portfolio_impact = (
        f"对当前组合的影响：组合中美股约{us_ratio * 100:.2f}%、港股约{hk_ratio * 100:.2f}%、"
        f"A股约{cn_ratio * 100:.2f}%、黄金约{gold_ratio * 100:.2f}%、债券约{bond_ratio * 100:.2f}%。"
        "跨资产信号支持继续控制追涨冲动：黄金不追高，纳斯达克不重仓追涨，A股和美股优先通过定投慢慢补。"
    )

    return {
        "signals": signals,
        "gold_judgement": gold_judgement,
        "bond_judgement": bond_judgement,
        "dollar_judgement": dollar_judgement,
        "us_hk_relative": us_hk_relative,
        "portfolio_impact": portfolio_impact,
        "metrics": {
            "us_stock": us_stock,
            "hk_stock": hk_stock,
            "cn_stock": cn_stock,
            "gold": gold,
            "bond": bond,
            "dollar": dollar,
            "vix": vix,
        },
        "disclaimer": "仅供投资辅助，不构成投资建议；系统不会自动交易，也不承诺收益，不预测具体涨跌点位。",
    }
