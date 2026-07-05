from __future__ import annotations

from typing import Any

from utils.logger import write_log


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_vix_from_live_market(live_market_result: dict[str, Any] | None) -> float | None:
    if not live_market_result:
        return None
    vix_item = live_market_result.get("items", {}).get("^VIX", {})
    if vix_item.get("status") != "ok":
        return None
    return _to_float(vix_item.get("close"))


def _extract_vix_from_manual_market(market_data: dict[str, dict[str, Any]] | None) -> float | None:
    if not market_data:
        return None
    item = market_data.get("VIX指数", {})
    return _to_float(item.get("value"))


def analyze_vix_risk(
    live_market_result: dict[str, Any] | None = None,
    market_data: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """根据 VIX 阈值判断市场情绪，数据缺失时不让主程序崩溃。"""
    vix = _extract_vix_from_live_market(live_market_result)
    source = "yfinance"
    if vix is None:
        vix = _extract_vix_from_manual_market(market_data)
        source = "market_data.csv"

    if vix is None:
        message = "VIX 数据不可用，使用中性风险提示继续生成日报"
        write_log(message, filename="vix_risk.log")
        return {
            "vix": None,
            "source": "unavailable",
            "risk_level": "未知",
            "explanation": "VIX 当前水平暂不可用，按中性偏谨慎处理。",
            "suitable_to_add": False,
            "pause_chasing": True,
            "message": message,
        }

    if vix < 15:
        risk_level = "乐观"
        explanation = "VIX < 15，市场情绪偏乐观，但仍不建议追涨。"
        suitable_to_add = True
        pause_chasing = False
    elif vix < 20:
        risk_level = "正常"
        explanation = "15 <= VIX < 20，市场处于正常波动区间，可按计划定投。"
        suitable_to_add = True
        pause_chasing = False
    elif vix < 30:
        risk_level = "风险升高"
        explanation = "20 <= VIX < 30，风险升高，新增仓位需要分批，避免一次性重仓。"
        suitable_to_add = False
        pause_chasing = True
    else:
        risk_level = "高风险"
        explanation = "VIX >= 30，市场高风险，暂停追涨，优先控制回撤和现金安全垫。"
        suitable_to_add = False
        pause_chasing = True

    return {
        "vix": round(vix, 2),
        "source": source,
        "risk_level": risk_level,
        "explanation": explanation,
        "suitable_to_add": suitable_to_add,
        "pause_chasing": pause_chasing,
        "message": "VIX 风险分析完成",
    }

