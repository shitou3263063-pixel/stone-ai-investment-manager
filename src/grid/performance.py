from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev


def equity_curve_metrics(values: list[float]) -> dict[str, float]:
    if len(values) < 2 or values[0] <= 0:
        return {"total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0, "volatility": 0.0, "sharpe": 0.0, "calmar": 0.0}
    returns = [current / previous - 1 for previous, current in zip(values[:-1], values[1:]) if previous]
    total_return = values[-1] / values[0] - 1
    years = max(len(values) / 252, 1 / 252)
    annual_return = (1 + total_return) ** (1 / years) - 1
    volatility = pstdev(returns) * sqrt(252) if len(returns) > 2 else 0.0
    sharpe = annual_return / volatility if volatility else 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = min(max_dd, value / peak - 1 if peak else 0)
    calmar = annual_return / abs(max_dd) if max_dd else 0.0
    return {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "max_drawdown": round(max_dd, 4),
        "volatility": round(volatility, 4),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
    }


def trade_metrics(profits: list[float]) -> dict[str, float]:
    if not profits:
        return {"win_rate": 0.0, "profit_factor": 0.0, "avg_trade_profit": 0.0, "max_losing_streak": 0}
    wins = [profit for profit in profits if profit > 0]
    losses = [profit for profit in profits if profit < 0]
    losing_streak = 0
    max_losing_streak = 0
    for profit in profits:
        if profit < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return {
        "win_rate": round(len(wins) / len(profits), 4),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else 0.0,
        "avg_trade_profit": round(mean(profits), 4),
        "max_losing_streak": max_losing_streak,
    }
