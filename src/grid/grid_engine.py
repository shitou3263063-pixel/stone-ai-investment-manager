from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .backtest import run_backtest_suite
from .budget_manager import build_grid_budget, symbol_budget
from .models import GRID_VERSION, GridSymbolState
from .position_manager import load_portfolio_quantities, split_core_grid
from .regime_detector import detect_market_regime
from .signal_engine import build_signal, dynamic_grid_spacing, signal_key, update_next_prices
from .simulator import append_simulated_signal
from .validator import review_grid_signal
from utils.data_loader import load_config, project_root
from utils.logger import write_log


MANUAL_FIELDS = ["trade_id", "date", "symbol", "action", "quantity", "price", "fees", "status", "note"]


def load_smart_grid_config() -> dict[str, Any]:
    path = project_root() / "config" / "smart_grid.yaml"
    if not path.exists():
        return {"smart_grid": {"enabled": False, "auto_trade": False, "paper_mode": True, "live_advice_enabled": False}}
    return load_config(path)


def _grid_dir() -> Path:
    path = project_root() / "data" / "grid"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_manual_trade_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8", newline="") as file:
            csv.DictWriter(file, fieldnames=MANUAL_FIELDS).writeheader()


def load_grid_state(path: Path | None = None) -> dict[str, Any]:
    target = path or _grid_dir() / "grid_state.json"
    if not target.exists():
        return {"version": GRID_VERSION, "symbols": {}, "updated_at": datetime.now().isoformat(timespec="seconds")}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        write_log(f"网格状态读取失败，进入安全模式：{exc}", filename="stone_ai.log")
        return {"version": GRID_VERSION, "symbols": {}, "updated_at": datetime.now().isoformat(timespec="seconds"), "load_error": str(exc)}


def save_grid_state(state: dict[str, Any], path: Path | None = None) -> None:
    target = path or _grid_dir() / "grid_state.json"
    state["version"] = GRID_VERSION
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _apply_confirmed_manual_trades(state: dict[str, Any], manual_path: Path) -> list[str]:
    ensure_manual_trade_file(manual_path)
    applied: list[str] = []
    with manual_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if str(row.get("status", "")).lower() != "confirmed":
                continue
            trade_id = row.get("trade_id") or ""
            symbol = (row.get("symbol") or "").upper()
            if not trade_id or not symbol:
                continue
            symbol_state = GridSymbolState.from_dict((state.get("symbols", {}) or {}).get(symbol, {"symbol": symbol}))
            if trade_id in symbol_state.processed_trade_ids:
                continue
            try:
                qty = float(row.get("quantity") or 0)
                price = float(row.get("price") or 0)
                fees = float(row.get("fees") or 0)
            except (TypeError, ValueError):
                continue
            if row.get("action", "").upper() == "BUY":
                symbol_state.grid_quantity += qty
                symbol_state.grid_cost_yuan += qty * price + fees
                symbol_state.available_grid_cash_yuan = max(0.0, symbol_state.available_grid_cash_yuan - qty * price - fees)
                symbol_state.consecutive_buys += 1
            elif row.get("action", "").upper() == "SELL" and qty <= symbol_state.grid_quantity:
                avg_cost = symbol_state.grid_cost_yuan / symbol_state.grid_quantity if symbol_state.grid_quantity else 0
                symbol_state.grid_quantity -= qty
                symbol_state.grid_cost_yuan = max(0.0, symbol_state.grid_cost_yuan - avg_cost * qty)
                symbol_state.available_grid_cash_yuan += qty * price - fees
                symbol_state.realized_profit_yuan += (price - avg_cost) * qty - fees
                symbol_state.consecutive_buys = 0
            else:
                continue
            symbol_state.anchor_price = price
            symbol_state.last_trade_price = price
            symbol_state.last_trade_time = row.get("date") or datetime.now().isoformat(timespec="seconds")
            symbol_state.processed_trade_ids.append(trade_id)
            applied.append(trade_id)
            state.setdefault("symbols", {})[symbol] = symbol_state.to_dict()
    return applied


def _quote(live_market_result: dict[str, Any], symbol: str) -> dict[str, Any]:
    return (live_market_result.get("items", {}) or {}).get(symbol, {}) or {}


def _price(item: dict[str, Any]) -> float | None:
    try:
        value = item.get("close")
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol_history_from_cache(symbol: str) -> list[float]:
    cache = project_root() / "data" / "cache" / f"grid_history_{symbol}.json"
    if not cache.exists():
        return []
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        return [float(row["close"]) for row in payload.get("rows", []) if row.get("close") is not None]
    except Exception:  # noqa: BLE001
        return []


def evaluate_symbol(
    *,
    symbol: str,
    symbol_cfg: dict[str, Any],
    state_payload: dict[str, Any],
    decision: dict[str, Any],
    portfolio_result: dict[str, Any],
    live_market_result: dict[str, Any],
    config: dict[str, Any],
    grid_budget: dict[str, Any],
    quantities: dict[str, float],
) -> dict[str, Any]:
    state = GridSymbolState.from_dict(state_payload or {"symbol": symbol})
    item = _quote(live_market_result, symbol)
    price = _price(item)
    total_assets = float(decision.get("portfolio_value_yuan", 0) or 0)
    symbol_budget_yuan = symbol_budget(symbol, total_assets, grid_budget, symbol_cfg)
    total_quantity = float(quantities.get(symbol, 0) or 0)
    split = split_core_grid(total_quantity, float(symbol_cfg.get("core_position_pct", 70) or 70))
    if state.core_quantity == 0 and state.grid_quantity == 0:
        state.core_quantity = split["core_quantity"]
        state.grid_quantity = split["grid_quantity"]
        state.available_grid_cash_yuan = symbol_budget_yuan
    history = _symbol_history_from_cache(symbol)
    vix = _price(_quote(live_market_result, "^VIX"))
    regime = detect_market_regime(symbol, item, history, vix)
    spacing = dynamic_grid_spacing(symbol_cfg, regime)
    state.buy_spacing_pct = spacing["buy_spacing_pct"]
    state.sell_spacing_pct = spacing["sell_spacing_pct"]
    if price and not state.anchor_price:
        state.anchor_price = price
    update_next_prices(state)
    signal = build_signal(symbol=symbol, price=price, state=state, symbol_cfg=symbol_cfg, symbol_budget_yuan=symbol_budget_yuan, config=config, regime=regime)
    review = review_grid_signal(
        signal=signal,
        state=state,
        decision=decision,
        portfolio_result=portfolio_result,
        grid_budget=grid_budget,
        config=config,
        symbol_cfg=symbol_cfg,
        price_available=price is not None,
    )
    if review.approved:
        state.state = "BUY_APPROVED" if signal.action == "BUY" else "SELL_APPROVED"
    elif signal.action in {"BUY", "SELL"}:
        state.state = "BUY_REJECTED" if signal.action == "BUY" else "SELL_REJECTED"
    state.market_regime = regime.get("regime", "unknown")
    state.updated_at = datetime.now().isoformat(timespec="seconds")
    sim_path = _grid_dir() / "simulation_trades.csv"
    if config.get("smart_grid", {}).get("simulator", {}).get("enabled", True):
        state.last_signal_key = append_simulated_signal(sim_path, signal, review, state.last_signal_key)
    return {
        "symbol": symbol,
        "price": price,
        "data_time": item.get("published_at") or item.get("fetched_at") or item.get("retrieved_at") or "暂无数据",
        "source": item.get("source", "unavailable") if price is not None else "unavailable",
        "state": state.to_dict(),
        "regime": regime,
        "spacing": spacing,
        "signal": signal.to_dict(),
        "review": review.to_dict(),
        "symbol_budget_yuan": symbol_budget_yuan,
        "distance_to_buy_pct": None if not price or not state.next_buy_price else round((price / state.next_buy_price - 1) * 100, 2),
        "distance_to_sell_pct": None if not price or not state.next_sell_price else round((state.next_sell_price / price - 1) * 100, 2),
    }


def run_smart_grid(*, decision: dict[str, Any], live_market_result: dict[str, Any], portfolio_result: dict[str, Any]) -> dict[str, Any]:
    config = load_smart_grid_config()
    smart = config.get("smart_grid", {})
    if not smart.get("enabled", False):
        return {"enabled": False, "version": GRID_VERSION, "summary": "智能网格模块已关闭。"}
    if smart.get("auto_trade") is not False:
        smart["auto_trade"] = False
    state = load_grid_state()
    applied_manual_trades = _apply_confirmed_manual_trades(state, _grid_dir() / "manual_trades.csv")
    grid_budget = build_grid_budget(decision, config)
    quantities = load_portfolio_quantities()
    symbols = {}
    for symbol, symbol_cfg in smart.get("symbols", {}).items():
        if not symbol_cfg.get("enabled", False):
            continue
        try:
            result = evaluate_symbol(
                symbol=symbol,
                symbol_cfg=symbol_cfg,
                state_payload=(state.get("symbols", {}) or {}).get(symbol, {"symbol": symbol}),
                decision=decision,
                portfolio_result=portfolio_result,
                live_market_result=live_market_result,
                config=config,
                grid_budget=grid_budget,
                quantities=quantities,
            )
            symbols[symbol] = result
            state.setdefault("symbols", {})[symbol] = result["state"]
        except Exception as exc:  # noqa: BLE001
            write_log(f"智能网格标的评估失败 {symbol}: {exc}", filename="stone_ai.log")
            symbols[symbol] = {"symbol": symbol, "error": str(exc), "signal": {"action": "NONE"}, "review": {"approved": False, "reasons": ["模块异常，已隔离。"]}}
    save_grid_state(state)
    backtest = run_backtest_suite(config) if smart.get("backtest", {}).get("run_on_daily", True) else {"enabled": False, "summary": "每日回测关闭。", "results": []}
    actionable = [item for item in symbols.values() if item.get("signal", {}).get("action") in {"BUY", "SELL"}]
    approved = [item for item in actionable if item.get("review", {}).get("approved")]
    summary = "模拟模式：仅监控和记录候选信号，不生成真实执行建议。" if smart.get("paper_mode", True) else "实盘建议模式：仍需人工确认，不自动下单。"
    result = {
        "enabled": True,
        "version": GRID_VERSION,
        "paper_mode": bool(smart.get("paper_mode", True)),
        "live_advice_enabled": bool(smart.get("live_advice_enabled", False)),
        "auto_trade": False,
        "summary": summary,
        "grid_budget": grid_budget,
        "symbols": symbols,
        "candidate_count": len(actionable),
        "approved_count": len(approved),
        "today_total_advice_yuan": round(sum(item.get("signal", {}).get("amount_yuan", 0) for item in approved)),
        "applied_manual_trades": applied_manual_trades,
        "backtest": backtest,
        "state_path": str(_grid_dir() / "grid_state.json"),
        "manual_trade_path": str(_grid_dir() / "manual_trades.csv"),
        "simulation_trade_path": str(_grid_dir() / "simulation_trades.csv"),
    }
    write_log(f"智能网格完成：signals={len(actionable)} approved={len(approved)} paper={result['paper_mode']}", filename="stone_ai.log")
    return result
