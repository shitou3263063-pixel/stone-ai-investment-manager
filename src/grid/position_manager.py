from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from utils.data_loader import project_root


def split_core_grid(total_quantity: float, core_pct: float) -> dict[str, float]:
    core_quantity = max(0.0, total_quantity * core_pct / 100)
    grid_quantity = max(0.0, total_quantity - core_quantity)
    return {"core_quantity": round(core_quantity, 6), "grid_quantity": round(grid_quantity, 6)}


def load_portfolio_quantities(path: Path | None = None) -> dict[str, float]:
    target = path or project_root() / "data" / "portfolio.csv"
    if not target.exists():
        return {}
    quantities: dict[str, float] = {}
    with target.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            symbol = (row.get("symbol") or row.get("ticker") or row.get("代码") or row.get("name") or "").strip()
            quantity_raw = row.get("quantity") or row.get("持仓") or row.get("份额") or "0"
            try:
                quantity = float(quantity_raw)
            except (TypeError, ValueError):
                quantity = 0.0
            if symbol:
                quantities[symbol.upper()] = quantity
    return quantities


def estimate_tech_exposure_yuan(portfolio_result: dict[str, Any]) -> float:
    total = float(portfolio_result.get("total_assets_wan", 0) or 0) * 10000
    if total <= 0:
        return 0.0
    detail_rows = portfolio_result.get("holding_rows") or portfolio_result.get("holdings") or []
    exposure = 0.0
    if detail_rows:
        for row in detail_rows:
            text = " ".join(str(value) for value in row.values()).upper()
            if any(token in text for token in ["NVDA", "GOOG", "QQQ", "纳斯达克", "科技"]):
                try:
                    exposure += float(row.get("amount_yuan") or row.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
    if exposure <= 0:
        amounts = portfolio_result.get("category_amounts", {}) or {}
        exposure = float(amounts.get("美股", 0) or 0) * 10000 * 0.45
    return exposure
