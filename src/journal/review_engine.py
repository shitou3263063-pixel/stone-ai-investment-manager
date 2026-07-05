from __future__ import annotations

import csv
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from utils.data_loader import project_root


CONSERVATIVE_KEYWORDS = ["不追涨", "观察", "等待", "暂停", "保留", "降低", "控制", "小额", "现金", "防守", "不建议", "暂不"]
AGGRESSIVE_KEYWORDS = ["加仓", "买入", "补足", "定投", "进攻", "提高", "参与"]


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def read_investment_log(path: Path | None = None) -> list[dict[str, Any]]:
    log_path = path or project_root() / "data" / "investment_log.csv"
    if not log_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            row_date = _parse_date(row.get("date", ""))
            if not row_date:
                continue
            parsed = dict(row)
            parsed["_date"] = row_date
            for key in [
                "total_assets",
                "risk_score",
                "stone_score",
                "stock_ratio",
                "bond_ratio",
                "gold_ratio",
                "cash_ratio",
                "vix",
            ]:
                parsed[key] = _to_float(parsed.get(key))
            rows.append(parsed)

    rows.sort(key=lambda item: item["_date"])
    return rows


def _window_rows(rows: list[dict[str, Any]], as_of: date, days: int) -> list[dict[str, Any]]:
    start = as_of - timedelta(days=days - 1)
    return [row for row in rows if start <= row["_date"] <= as_of]


def _change(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if len(values) < 2:
        return None
    return float(values[-1]) - float(values[0])


def _change_text(rows: list[dict[str, Any]], key: str, label: str, unit: str = "") -> str:
    delta = _change(rows, key)
    if delta is None:
        return f"{label}数据不足，至少需要2条记录。"
    sign = "+" if delta >= 0 else ""
    return f"{label}{sign}{delta:.2f}{unit}"


def _advice_bias(rows: list[dict[str, Any]]) -> dict[str, Any]:
    conservative = 0
    aggressive = 0
    for row in rows:
        advice = str(row.get("main_advice", ""))
        conservative += sum(1 for word in CONSERVATIVE_KEYWORDS if word in advice)
        aggressive += sum(1 for word in AGGRESSIVE_KEYWORDS if word in advice)

    if conservative > aggressive:
        bias = "偏保守"
    elif aggressive > conservative:
        bias = "偏进攻"
    else:
        bias = "中性"
    return {"bias": bias, "conservative_count": conservative, "aggressive_count": aggressive}


def _recent_advices(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    advices = []
    for row in rows[-limit:]:
        advice = str(row.get("main_advice", "")).strip() or "暂无建议"
        advices.append(f"{row['_date'].isoformat()}：{advice}")
    return advices


def _allocation_change(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {
        "stock": _change_text(rows, "stock_ratio", "股票占比变化", "个百分点"),
        "bond": _change_text(rows, "bond_ratio", "债券占比变化", "个百分点"),
        "gold": _change_text(rows, "gold_ratio", "黄金占比变化", "个百分点"),
        "cash": _change_text(rows, "cash_ratio", "现金占比变化", "个百分点"),
    }


def _strategy_adjustment(rows_30: list[dict[str, Any]]) -> str:
    if len(rows_30) < 5:
        return "历史数据仍较少，不建议根据短期结果频繁改策略；继续积累日志。"

    bias = _advice_bias(rows_30)["bias"]
    risk_delta = _change(rows_30, "risk_score")
    if risk_delta is not None and risk_delta > 10:
        return "风险评分明显上升，应降低追涨冲动，优先控制回撤和现金缓冲。"
    if bias == "偏进攻":
        return "最近建议偏进攻，后续应检查是否仍符合长期、低频、控制回撤的风格。"
    if bias == "偏保守":
        return "最近建议偏保守，若市场风险下降，可用定投和新增资金逐步补低配资产。"
    return "当前建议风格较均衡，暂不需要调整长期策略。"


def build_history_review(as_of: date | None = None, path: Path | None = None) -> dict[str, Any]:
    current_date = as_of or date.today()
    rows = read_investment_log(path)
    rows_7 = _window_rows(rows, current_date, 7)
    rows_30 = _window_rows(rows, current_date, 30)

    if not rows:
        return {
            "has_history": False,
            "daily": {
                "risk_7": "暂无历史日志，今天开始记录。",
                "stone_30": "暂无历史日志，今天开始记录。",
                "allocation_7": {},
                "recent_advices": [],
                "advice_bias_30": {"bias": "暂无"},
                "strategy_adjustment": "先积累历史数据，不根据短期结果频繁改策略。",
            },
            "weekly": {
                "asset_change": "暂无历史日志。",
                "risk_change": "暂无历史日志。",
                "main_advices": [],
                "next_week_focus": ["继续积累投资日志。"],
            },
        }

    daily = {
        "risk_7": _change_text(rows_7, "risk_score", "最近7天风险评分变化", "分"),
        "stone_30": _change_text(rows_30, "stone_score", "最近30天 Stone Score 变化", "分"),
        "allocation_7": _allocation_change(rows_7),
        "recent_advices": _recent_advices(rows, limit=5),
        "advice_bias_30": _advice_bias(rows_30),
        "strategy_adjustment": _strategy_adjustment(rows_30),
        "log_count": len(rows),
    }

    weekly_allocation = _allocation_change(rows_7)
    weekly = {
        "asset_change": "；".join(weekly_allocation.values()),
        "risk_change": _change_text(rows_7, "risk_score", "本周风险评分变化", "分"),
        "main_advices": _recent_advices(rows_7, limit=7),
        "next_week_focus": [
            "继续检查风险评分是否连续上升。",
            "观察低配资产是否适合用新增资金慢慢补足。",
            "记录 user_action 和 result_note，便于后续复盘建议质量。",
        ],
    }

    return {
        "has_history": True,
        "daily": daily,
        "weekly": weekly,
        "disclaimer": "历史复盘仅供投资辅助，不构成投资建议；不自动交易，不承诺收益，不根据短期结果频繁改策略。",
    }

