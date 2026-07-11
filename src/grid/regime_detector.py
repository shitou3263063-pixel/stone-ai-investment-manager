from __future__ import annotations

from statistics import pstdev
from typing import Any


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def historical_volatility(values: list[float], window: int = 20) -> float:
    if len(values) <= window:
        return 0.0
    returns = []
    for previous, current in zip(values[-window - 1 : -1], values[-window:]):
        if previous:
            returns.append(current / previous - 1)
    return pstdev(returns) * (252**0.5) if len(returns) > 2 else 0.0


def max_drawdown(values: list[float], window: int) -> float:
    sample = values[-window:] if len(values) >= window else values[:]
    peak = 0.0
    worst = 0.0
    for value in sample:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
    return worst


def detect_market_regime(symbol: str, market_item: dict[str, Any], history: list[float] | None = None, vix: float | None = None) -> dict[str, Any]:
    history = history or []
    price = _float(market_item.get("close"), 0.0)
    change_pct = _float(market_item.get("change_pct"), 0.0)
    vix_value = _float(vix, -1.0)
    ma20 = moving_average(history, 20)
    ma50 = moving_average(history, 50)
    ma200 = moving_average(history, 200)
    vol20 = historical_volatility(history, 20)
    dd20 = max_drawdown(history, 20)
    dd60 = max_drawdown(history, 60)

    reasons: list[str] = []
    if not price:
        return {
            "regime": "data_missing",
            "volatility_state": "unknown",
            "trend_strength": "unknown",
            "ma20": ma20,
            "ma50": ma50,
            "ma200": ma200,
            "volatility_20d": vol20,
            "drawdown_20d": dd20,
            "drawdown_60d": dd60,
            "reason": "缺少当前价格，不能识别市场状态。",
        }

    high_vol = (vix_value >= 30) or (vol20 >= 0.30) or (abs(change_pct) >= 4)
    if high_vol:
        reasons.append("VIX、历史波动或单日波动显示高波动。")
        regime = "crisis"
    elif ma50 and ma200 and price > ma200 and ma50 > ma200:
        regime = "uptrend"
        reasons.append("价格位于200日均线上方且50日均线高于200日均线。")
    elif ma50 and ma200 and (price < ma200 or ma50 < ma200):
        regime = "downtrend"
        reasons.append("价格跌破200日均线或50日均线弱于200日均线。")
    elif dd20 <= -0.08 or dd60 <= -0.12:
        regime = "downtrend"
        reasons.append("近20/60日回撤较深。")
    else:
        regime = "range"
        reasons.append("趋势证据不足，按震荡状态处理。")

    if vix_value >= 25 or vol20 >= 0.25:
        volatility_state = "high"
    elif 0 < vix_value < 15 and vol20 < 0.15:
        volatility_state = "low"
    else:
        volatility_state = "normal"

    return {
        "symbol": symbol,
        "regime": regime,
        "volatility_state": volatility_state,
        "trend_strength": "strong" if regime == "uptrend" else "weak" if regime == "downtrend" else "neutral",
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "volatility_20d": vol20,
        "drawdown_20d": dd20,
        "drawdown_60d": dd60,
        "reason": "；".join(reasons),
    }
