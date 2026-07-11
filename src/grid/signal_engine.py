from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .models import GridSignal, GridSymbolState


def _pct_mid(symbol_cfg: dict[str, Any], low_key: str, high_key: str) -> float:
    return (float(symbol_cfg.get(low_key, 0) or 0) + float(symbol_cfg.get(high_key, 0) or 0)) / 2 / 100


def dynamic_grid_spacing(symbol_cfg: dict[str, Any], regime: dict[str, Any]) -> dict[str, float]:
    state = regime.get("volatility_state", "normal")
    market_regime = regime.get("regime", "range")
    if state == "low":
        buy = _pct_mid(symbol_cfg, "low_vol_buy_min_pct", "low_vol_buy_max_pct")
        sell = _pct_mid(symbol_cfg, "low_vol_sell_min_pct", "low_vol_sell_max_pct")
    elif state == "high" or market_regime == "crisis":
        buy = _pct_mid(symbol_cfg, "high_vol_grid_min_pct", "high_vol_grid_max_pct")
        sell = _pct_mid(symbol_cfg, "high_vol_sell_min_pct", "high_vol_sell_max_pct")
    else:
        buy = _pct_mid(symbol_cfg, "normal_grid_min_pct", "normal_grid_max_pct")
        sell = _pct_mid(symbol_cfg, "normal_sell_min_pct", "normal_sell_max_pct")

    if market_regime == "uptrend":
        sell *= 1.35
        buy *= 0.95
    elif market_regime == "downtrend":
        buy *= 1.35
        sell *= 1.10
    elif market_regime == "crisis":
        buy *= 1.25
        sell *= 1.25
    return {"buy_spacing_pct": round(buy, 4), "sell_spacing_pct": round(sell, 4)}


def update_next_prices(state: GridSymbolState) -> None:
    if state.anchor_price:
        state.next_buy_price = round(state.anchor_price * (1 - state.buy_spacing_pct), 4)
        state.next_sell_price = round(state.anchor_price * (1 + state.sell_spacing_pct), 4)


def layer_amount(symbol_budget_yuan: float, layer: int, config: dict[str, Any]) -> float:
    layers = config.get("smart_grid", {}).get("buy_layers_pct", [15, 17.5, 20, 22.5, 25])
    index = max(0, min(layer - 1, len(layers) - 1))
    return round(symbol_budget_yuan * float(layers[index]) / 100)


def build_signal(
    *,
    symbol: str,
    price: float | None,
    state: GridSymbolState,
    symbol_cfg: dict[str, Any],
    symbol_budget_yuan: float,
    config: dict[str, Any],
    regime: dict[str, Any],
) -> GridSignal:
    if not price or price <= 0:
        state.state = "SAFE_MODE"
        return GridSignal(symbol, "NONE", "DATA_MISSING", price, None, 0, 0, 0, "缺少可靠价格，禁止生成网格交易。")

    if not state.anchor_price:
        state.anchor_price = price
    state.buy_spacing_pct = state.buy_spacing_pct or dynamic_grid_spacing(symbol_cfg, regime)["buy_spacing_pct"]
    state.sell_spacing_pct = state.sell_spacing_pct or dynamic_grid_spacing(symbol_cfg, regime)["sell_spacing_pct"]
    update_next_prices(state)

    max_consecutive = int(symbol_cfg.get("max_consecutive_buys") or config.get("smart_grid", {}).get("risk", {}).get("max_consecutive_buys", 3))
    if state.next_buy_price and price <= state.next_buy_price and state.consecutive_buys < max_consecutive:
        layer = min(state.consecutive_buys + 1, int(symbol_cfg.get("max_buy_levels", 5)))
        amount = min(state.available_grid_cash_yuan, layer_amount(symbol_budget_yuan, layer, config))
        quantity = 0 if price <= 0 else amount / price
        state.state = "BUY_SIGNAL"
        return GridSignal(
            symbol=symbol,
            action="BUY",
            raw_signal="BUY_SIGNAL",
            price=price,
            trigger_price=state.next_buy_price,
            amount_yuan=round(amount),
            quantity=round(quantity, 6),
            layer=layer,
            reason=f"价格低于下一买入价，触发第{layer}层网格买入候选。",
            valid_until=(date.today() + timedelta(days=1)).isoformat(),
        )

    min_grid_hold_pct = float(symbol_cfg.get("min_grid_hold_pct", 20) or 20) / 100
    min_grid_qty = state.grid_quantity * min_grid_hold_pct
    if state.next_sell_price and price >= state.next_sell_price and state.grid_quantity > max(0.000001, min_grid_qty):
        sell_qty = max(0.0, (state.grid_quantity - min_grid_qty) * 0.25)
        amount = sell_qty * price
        state.state = "SELL_SIGNAL"
        return GridSignal(
            symbol=symbol,
            action="SELL",
            raw_signal="SELL_SIGNAL",
            price=price,
            trigger_price=state.next_sell_price,
            amount_yuan=round(amount),
            quantity=round(sell_qty, 6),
            layer=0,
            reason="价格高于下一卖出价，触发网格仓卖出候选。",
            expected_profit_pct=round(state.sell_spacing_pct * 100, 2),
            valid_until=(date.today() + timedelta(days=1)).isoformat(),
        )

    state.state = "WAIT_BUY" if state.grid_quantity <= 0 else "HOLDING_GRID_POSITION"
    distance_buy = ""
    if state.next_buy_price:
        distance_buy = f"距离下一买入价约{(price / state.next_buy_price - 1) * 100:.2f}%"
    return GridSignal(symbol, "NONE", "NO_TRIGGER", price, None, 0, 0, 0, f"未触发网格条件。{distance_buy}")


def signal_key(signal: GridSignal) -> str:
    return f"{signal.symbol}:{signal.action}:{signal.trigger_price}:{signal.layer}:{signal.valid_until}"
