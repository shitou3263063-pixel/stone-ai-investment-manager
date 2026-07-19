from __future__ import annotations

from typing import Any


def split_core_grid(total_quantity: float, core_pct: float) -> dict[str, float]:
    core_quantity = max(0.0, total_quantity * core_pct / 100)
    grid_quantity = max(0.0, total_quantity - core_quantity)
    return {"core_quantity": round(core_quantity, 6), "grid_quantity": round(grid_quantity, 6)}


def estimate_tech_exposure_yuan(portfolio_snapshot: dict[str, Any]) -> float:
    detail_rows = portfolio_snapshot.get("positions", []) or []
    exposure = 0.0
    for row in detail_rows:
        text = " ".join(str(value) for value in row.values()).upper()
        if any(token in text for token in ["NVDA", "GOOG", "QQQ", "纳斯达克", "科技"]):
            exposure += float(row.get("market_value_cny", 0) or 0)
    return exposure
