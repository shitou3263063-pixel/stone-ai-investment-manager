from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from utils.data_loader import project_root


FIELDNAMES = [
    "date",
    "total_assets",
    "risk_score",
    "stone_score",
    "stock_ratio",
    "bond_ratio",
    "gold_ratio",
    "cash_ratio",
    "vix",
    "main_advice",
    "user_action",
    "result_note",
]


def _category_ratio(portfolio_result: dict[str, Any], category: str) -> float:
    for item in portfolio_result.get("categories", []):
        if item.get("category") == category:
            return float(item.get("current_ratio", 0.0) or 0.0)
    return 0.0


def _fmt_number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def _main_advice(
    decision_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
) -> str:
    parts = [
        decision_result.get("one_sentence_conclusion", ""),
        dca_result.get("summary", ""),
        allocation_rebalance_result.get("summary", ""),
    ]
    return "；".join(part for part in parts if part)


def build_log_row(
    report_date: date,
    portfolio_result: dict[str, Any],
    market_result: dict[str, Any],
    vix_result: dict[str, Any],
    decision_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
) -> dict[str, str]:
    """把当天运行结果整理成一行 CSV。比例字段按百分比保存。"""
    stock_ratio = (
        _category_ratio(portfolio_result, "美股")
        + _category_ratio(portfolio_result, "港股")
        + _category_ratio(portfolio_result, "A股")
    )
    return {
        "date": report_date.isoformat(),
        "total_assets": _fmt_number(portfolio_result.get("total_assets_wan")),
        "risk_score": _fmt_number(market_result.get("market_risk_score"), 0),
        "stone_score": _fmt_number(market_result.get("market_score"), 0),
        "stock_ratio": _fmt_number(stock_ratio * 100),
        "bond_ratio": _fmt_number(_category_ratio(portfolio_result, "债券") * 100),
        "gold_ratio": _fmt_number(_category_ratio(portfolio_result, "黄金") * 100),
        "cash_ratio": _fmt_number(_category_ratio(portfolio_result, "现金") * 100),
        "vix": _fmt_number(vix_result.get("vix")),
        "main_advice": _main_advice(decision_result, dca_result, allocation_rebalance_result),
        "user_action": "",
        "result_note": "",
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = []
        for row in csv.DictReader(file):
            rows.append({key: row.get(key, "") for key in FIELDNAMES})
        return rows


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def upsert_investment_log(row: dict[str, str], path: Path | None = None) -> Path:
    """写入或更新当天日志；保留用户手动填写的执行和结果备注。"""
    log_path = path or project_root() / "data" / "investment_log.csv"
    rows = _read_rows(log_path)
    updated = False

    for index, existing in enumerate(rows):
        if existing.get("date") == row["date"]:
            merged = {**existing, **row}
            merged["user_action"] = existing.get("user_action", "") or row.get("user_action", "")
            merged["result_note"] = existing.get("result_note", "") or row.get("result_note", "")
            rows[index] = merged
            updated = True
            break

    if not updated:
        rows.append(row)

    rows.sort(key=lambda item: item.get("date", ""))
    _write_rows(log_path, rows)
    return log_path

