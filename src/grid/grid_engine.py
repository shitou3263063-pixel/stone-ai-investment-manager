from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backtest import run_backtest_suite
from .budget_manager import build_grid_budget, symbol_budget
from .models import GRID_VERSION, GridSignal, GridSymbolState
from .position_manager import load_portfolio_quantities, split_core_grid
from .regime_detector import detect_market_regime
from .signal_engine import build_signal, dynamic_grid_spacing, signal_key, update_next_prices
from .simulator import append_simulated_signal
from .validator import review_grid_signal
from utils.data_loader import load_config, project_root
from utils.logger import write_log
from src.data_sources.time_normalization import normalize_to_utc
from src.data_sources.normalized_market import market_quote_reference


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


def _session_phase(item: dict[str, Any]) -> str:
    return str(item.get("price_stage") or item.get("data_stage") or "UNKNOWN").upper()


def build_grid_decision_snapshot(
    live_market_result: dict[str, Any],
    *,
    symbols: tuple[str, ...] = ("VOO", "QQQ"),
    max_gap_minutes: int = 30,
    decision_cutoff_time: str | None = None,
    dqs_score: int | None = None,
    require_dqs: int | None = None,
) -> dict[str, Any]:
    """Build one comparable decision snapshot while retaining display quotes."""
    display_quotes = {symbol: _quote(live_market_result, symbol) for symbol in symbols}
    normalized: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    for symbol, item in display_quotes.items():
        source_timezone = item.get("market_timezone") or item.get("source_timezone") or item.get("timezone")
        raw_time = item.get("quote_timestamp") or item.get("observed_at_utc")
        try:
            observed_utc = normalize_to_utc(raw_time, source_timezone=str(source_timezone) if source_timezone else None)
        except (TypeError, ValueError, KeyError):
            observed_utc = None
        phase = _session_phase(item)
        data_stage = str(item.get("price_stage") or item.get("data_stage") or "UNKNOWN").upper()
        market_date = item.get("market_date")
        valid = (
            _price(item) is not None
            and str(item.get("status") or "").lower() in {"ok", "success"}
            and not bool(item.get("stale"))
            and observed_utc is not None
            and phase == "OFFICIAL_CLOSE"
            and data_stage == "OFFICIAL_CLOSE"
            and bool(item.get("is_finalized"))
            and bool(market_date)
        )
        if decision_cutoff_time and observed_utc:
            try:
                valid = valid and observed_utc <= datetime.fromisoformat(decision_cutoff_time).astimezone(timezone.utc)
            except ValueError:
                valid = False
        if dqs_score is not None and require_dqs is not None:
            valid = valid and dqs_score >= require_dqs
        if not valid:
            reasons.append(f"{symbol}缺少截至决策时间可用的正式收盘、带时区且新鲜的行情，或DQS未达网格门槛。")
        quote_ref = market_quote_reference(item, symbol)
        normalized[symbol] = {
            **quote_ref,
            "price": quote_ref["current_price"],
            "observed_at_utc": observed_utc.isoformat() if observed_utc else None,
            "source_timezone": source_timezone or "unknown",
            "market_date": str(market_date) if market_date else None,
            "market_phase": data_stage,
            "valid": valid,
        }

    valid_points = [normalized[symbol] for symbol in symbols if normalized[symbol]["valid"]]
    comparable = len(valid_points) == len(symbols)
    if comparable:
        dates = {point["market_date"] for point in valid_points}
        phases = {point["market_phase"] for point in valid_points}
        observed = [datetime.fromisoformat(point["observed_at_utc"]).astimezone(timezone.utc) for point in valid_points]
        gap_minutes = (max(observed) - min(observed)).total_seconds() / 60
        if len(dates) != 1:
            comparable = False
            reasons.append("VOO与QQQ不属于同一市场日期。")
        if len(phases) != 1:
            comparable = False
            reasons.append("VOO与QQQ不属于同一市场阶段。")
        if gap_minutes > max_gap_minutes:
            comparable = False
            reasons.append(f"VOO与QQQ行情时间差{gap_minutes:.1f}分钟，超过{max_gap_minutes}分钟上限。")
    else:
        gap_minutes = None

    return {
        "snapshot_comparable": comparable,
        "status": "COMPARABLE" if comparable else "DATA_NOT_COMPARABLE",
        "reason": "；".join(dict.fromkeys(reasons)) or "VOO与QQQ决策行情同日、同阶段且时间差在允许范围内。",
        "max_gap_minutes": max_gap_minutes,
        "actual_gap_minutes": round(gap_minutes, 1) if gap_minutes is not None else None,
        "display_quotes": display_quotes,
        "decision_quotes": normalized if comparable else {},
    }


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
    decision_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = GridSymbolState.from_dict(state_payload or {"symbol": symbol})
    item = _quote(live_market_result, symbol)
    price = _price(item)
    snapshot_comparable = True if decision_snapshot is None else bool(decision_snapshot.get("snapshot_comparable"))
    total_assets = float(
        ((decision.get("portfolio_snapshot", {}) or {}).get("total_valued_assets"))
        or decision.get("portfolio_value_yuan", 0)
        or 0
    )
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
    if snapshot_comparable:
        state.buy_spacing_pct = spacing["buy_spacing_pct"]
        state.sell_spacing_pct = spacing["sell_spacing_pct"]
        if price and not state.anchor_price:
            state.anchor_price = price
        update_next_prices(state)
        signal = build_signal(symbol=symbol, price=price, state=state, symbol_cfg=symbol_cfg, symbol_budget_yuan=symbol_budget_yuan, config=config, regime=regime)
    else:
        state.state = "SAFE_MODE"
        signal = GridSignal(
            symbol=symbol,
            action="NONE",
            raw_signal="DATA_NOT_COMPARABLE",
            price=price,
            trigger_price=None,
            amount_yuan=0,
            quantity=0,
            layer=0,
            reason=str((decision_snapshot or {}).get("reason") or "VOO与QQQ决策行情快照不可比，暂不计算精确触发价和距离。"),
        )
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
    if snapshot_comparable and config.get("smart_grid", {}).get("simulator", {}).get("enabled", True):
        state.last_signal_key = append_simulated_signal(sim_path, signal, review, state.last_signal_key)
    return {
        "symbol": symbol,
        "price": price,
        "data_time": item.get("observed_at_utc") or item.get("observed_at") or item.get("published_at") or item.get("fetched_at") or item.get("retrieved_at") or "暂无数据",
        "source": item.get("source", "unavailable") if price is not None else "unavailable",
        "state": state.to_dict(),
        "regime": regime,
        "spacing": spacing,
        "signal": signal.to_dict(),
        "review": review.to_dict(),
        "symbol_budget_yuan": symbol_budget_yuan,
        "snapshot_comparable": snapshot_comparable,
        "snapshot_status": "COMPARABLE" if snapshot_comparable else "DATA_NOT_COMPARABLE",
        "snapshot_reason": (decision_snapshot or {}).get("reason"),
        "historical_next_buy_price": state.next_buy_price,
        "historical_next_sell_price": state.next_sell_price,
        "distance_to_buy_pct": None if not snapshot_comparable or not price or not state.next_buy_price else round((price / state.next_buy_price - 1) * 100, 2),
        "distance_to_sell_pct": None if not snapshot_comparable or not price or not state.next_sell_price else round((state.next_sell_price / price - 1) * 100, 2),
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
    max_gap_minutes = int(smart.get("risk", {}).get("max_decision_snapshot_gap_minutes", 30) or 30)
    decision_snapshot = build_grid_decision_snapshot(
        live_market_result, max_gap_minutes=max_gap_minutes,
        decision_cutoff_time=(decision.get("data_time_summary", {}) or {}).get("decision_cutoff_time"),
        dqs_score=int((decision.get("data_quality_snapshot", decision.get("dqs", {})) or {}).get("grid_dqs", 0) or 0),
        require_dqs=int((smart.get("risk", {}) or {}).get("require_dqs", 85) or 85),
    )
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
                decision_snapshot=decision_snapshot,
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
        "decision_snapshot": decision_snapshot,
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
