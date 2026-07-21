from __future__ import annotations

from typing import Any

from src.grid import run_smart_grid


def build_smart_grid_result(*, decision: dict[str, Any], live_market_result: dict[str, Any]) -> dict[str, Any]:
    return run_smart_grid(decision=decision, live_market_result=live_market_result)
