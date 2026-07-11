from __future__ import annotations

from typing import Any


def build_grid_budget(decision: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    smart = config.get("smart_grid", {})
    portfolio = smart.get("portfolio", {})
    total_assets = float(decision.get("portfolio_value_yuan", 0) or 0)
    confirmed_cash = float(decision.get("budget", {}).get("confirmed_cash_available_yuan", 0) or 0)
    total_limit_pct = float(portfolio.get("total_grid_capital_pct", 4) or 4)
    max_total_pct = float(portfolio.get("max_total_grid_capital_pct", 8) or 8)
    reserve_pct = float(portfolio.get("cash_reserve_pct", 25) or 25)
    configured_budget = total_assets * min(total_limit_pct, max_total_pct) / 100
    reserved = configured_budget * reserve_pct / 100
    paper_mode = bool(smart.get("paper_mode", True))
    live_enabled = bool(smart.get("live_advice_enabled", False)) and not paper_mode
    live_budget = max(0.0, min(configured_budget - reserved, confirmed_cash)) if live_enabled else 0.0
    return {
        "paper_mode": paper_mode,
        "live_advice_enabled": live_enabled,
        "configured_total_yuan": round(configured_budget),
        "reserved_grid_cash_yuan": round(reserved),
        "simulated_available_yuan": round(max(0.0, configured_budget - reserved)),
        "live_available_yuan": round(live_budget),
        "confirmed_cash_available_yuan": round(confirmed_cash),
        "overlap_guard": "网格资金独立核算；模拟模式不占用基础定投、机会加仓或真实现金预算。",
    }


def symbol_budget(symbol: str, total_assets_yuan: float, grid_budget: dict[str, Any], symbol_cfg: dict[str, Any]) -> float:
    max_pct = float(symbol_cfg.get("max_capital_pct", 5) or 5)
    cap = total_assets_yuan * max_pct / 100
    available = grid_budget["simulated_available_yuan"] if grid_budget["paper_mode"] else grid_budget["live_available_yuan"]
    return round(max(0.0, min(cap, available / 2)))
