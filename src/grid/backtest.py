from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .performance import equity_curve_metrics, trade_metrics
from .signal_engine import dynamic_grid_spacing
from utils.data_loader import project_root
from utils.logger import write_log


def _download_yahoo_history(symbol: str, years: int) -> list[dict[str, Any]]:
    end = int(time.time())
    start = int((datetime.now() - timedelta(days=years * 365)).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={start}&period2={end}&interval=1d&events=history&includeAdjustedClose=true"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=25) as response:  # noqa: S310 - public Yahoo chart endpoint
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("adjclose") or result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("adjclose") or quote.get("close") or []
    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        rows.append({"date": datetime.fromtimestamp(ts).date().isoformat(), "close": float(close)})
    return rows


def load_history(symbol: str, years: int, cache_days: int = 7) -> dict[str, Any]:
    cache_path = project_root() / "data" / "cache" / f"grid_history_{symbol}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(cached.get("fetched_at"))
            if datetime.now() - fetched <= timedelta(days=cache_days):
                return {**cached, "cache_used": True}
        except Exception:  # noqa: BLE001
            pass
    try:
        rows = _download_yahoo_history(symbol, years)
        payload = {"symbol": symbol, "source": "yahoo_chart", "fetched_at": datetime.now().isoformat(timespec="seconds"), "rows": rows}
        if rows:
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**payload, "cache_used": False}
    except Exception as exc:  # noqa: BLE001
        write_log(f"网格回测历史数据获取失败 {symbol}: {exc}", filename="stone_ai.log")
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                return {**cached, "cache_used": True, "warning": f"实时获取失败，使用缓存：{exc}"}
            except Exception:  # noqa: BLE001
                pass
        return {"symbol": symbol, "source": "unavailable", "fetched_at": datetime.now().isoformat(timespec="seconds"), "rows": [], "error": str(exc), "cache_used": False}


def _simulate_buy_hold(prices: list[float], initial: float) -> dict[str, Any]:
    shares = initial / prices[0]
    curve = [shares * price for price in prices]
    return {"name": "买入并持有", "curve": curve, "trades": 1, "realized": 0.0, "profits": []}


def _simulate_grid(prices: list[float], initial: float, symbol_cfg: dict[str, Any], dynamic: bool, core: bool) -> dict[str, Any]:
    core_ratio = float(symbol_cfg.get("core_position_pct", 70) or 70) / 100 if core else 0.0
    core_cash = initial * core_ratio
    grid_cash = initial - core_cash
    core_shares = core_cash / prices[0] if core_cash else 0.0
    grid_shares = 0.0
    anchor = prices[0]
    realized = 0.0
    profits: list[float] = []
    trades = 0
    max_consecutive_buys = 0
    consecutive_buys = 0
    curve = []
    for index, price in enumerate(prices):
        history = prices[: index + 1]
        regime = {"regime": "range", "volatility_state": "normal"}
        if dynamic and len(history) >= 20:
            from .regime_detector import detect_market_regime

            regime = detect_market_regime("BT", {"close": price, "change_pct": 0}, history, None)
        spacing = dynamic_grid_spacing(symbol_cfg, regime)
        buy_price = anchor * (1 - spacing["buy_spacing_pct"])
        sell_price = anchor * (1 + spacing["sell_spacing_pct"])
        buy_amount = min(grid_cash, initial * 0.04)
        if price <= buy_price and buy_amount > 0:
            grid_cash -= buy_amount
            grid_shares += buy_amount / price
            anchor = price
            trades += 1
            consecutive_buys += 1
            max_consecutive_buys = max(max_consecutive_buys, consecutive_buys)
        elif price >= sell_price and grid_shares > 0:
            sell_qty = grid_shares * 0.25
            proceeds = sell_qty * price
            cost_basis = sell_qty * anchor
            profit = proceeds - cost_basis
            grid_cash += proceeds
            grid_shares -= sell_qty
            realized += profit
            profits.append(profit)
            anchor = price
            trades += 1
            consecutive_buys = 0
        curve.append(grid_cash + grid_shares * price + core_shares * price)
    return {
        "name": ("核心仓+动态网格" if core and dynamic else "动态网格" if dynamic else "固定网格"),
        "curve": curve,
        "trades": trades,
        "realized": realized,
        "profits": profits,
        "max_consecutive_buys": max_consecutive_buys,
        "capital_utilization": round(1 - min(curve) / max(curve), 4) if curve else 0,
    }


def run_symbol_backtest(symbol: str, symbol_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    backtest_cfg = config.get("smart_grid", {}).get("backtest", {})
    history = load_history(symbol, int(backtest_cfg.get("history_years", 18) or 18), int(backtest_cfg.get("cache_days", 7) or 7))
    rows = history.get("rows", [])
    if len(rows) < 252:
        return {
            "symbol": symbol,
            "status": "insufficient_data",
            "source": history.get("source", "unavailable"),
            "coverage": f"{len(rows)}个交易日",
            "error": history.get("error") or history.get("warning") or "历史数据不足，不能计算可靠回测指标。",
            "strategies": [],
            "sensitivity": [],
        }
    prices = [float(row["close"]) for row in rows]
    initial = float(backtest_cfg.get("initial_capital_yuan", 100000) or 100000)
    strategies = [
        _simulate_buy_hold(prices, initial),
        _simulate_grid(prices, initial, symbol_cfg, dynamic=False, core=False),
        _simulate_grid(prices, initial, symbol_cfg, dynamic=True, core=False),
        _simulate_grid(prices, initial, symbol_cfg, dynamic=True, core=True),
    ]
    output = []
    buy_hold_return = equity_curve_metrics(strategies[0]["curve"])["total_return"]
    for strategy in strategies:
        metrics = equity_curve_metrics(strategy["curve"])
        trades = trade_metrics(strategy.get("profits", []))
        output.append(
            {
                "name": strategy["name"],
                **metrics,
                **trades,
                "trade_count": strategy.get("trades", 0),
                "realized_grid_profit_yuan": round(strategy.get("realized", 0), 2),
                "unrealized_yuan": round(strategy["curve"][-1] - initial - strategy.get("realized", 0), 2),
                "capital_utilization": strategy.get("capital_utilization", 0),
                "max_consecutive_buys": strategy.get("max_consecutive_buys", 0),
                "excess_vs_buy_hold": round(metrics["total_return"] - buy_hold_return, 4),
                "drawdown_improvement_vs_buy_hold": round(metrics["max_drawdown"] - equity_curve_metrics(strategies[0]["curve"])["max_drawdown"], 4),
            }
        )
    sensitivity = []
    base_min = float(symbol_cfg.get("normal_grid_min_pct", 3) or 3)
    for width in [base_min - 0.5, base_min, base_min + 0.5]:
        test_cfg = {**symbol_cfg, "normal_grid_min_pct": width, "normal_grid_max_pct": width + 1}
        sim = _simulate_grid(prices, initial, test_cfg, dynamic=True, core=True)
        sensitivity.append({"grid_width_pct": width, **equity_curve_metrics(sim["curve"])})
    return {
        "symbol": symbol,
        "status": "ok",
        "source": history.get("source"),
        "coverage": f"{rows[0]['date']} 至 {rows[-1]['date']}，{len(rows)}个交易日",
        "cache_used": history.get("cache_used", False),
        "strategies": output,
        "sensitivity": sensitivity,
    }


def run_backtest_suite(config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("smart_grid", {}).get("backtest", {}).get("enabled", True):
        return {"enabled": False, "results": [], "summary": "回测已关闭。"}
    symbols_cfg = config.get("smart_grid", {}).get("symbols", {})
    results = []
    for symbol in ["VOO", "QQQ"]:
        if symbols_cfg.get(symbol, {}).get("enabled", False):
            results.append(run_symbol_backtest(symbol, symbols_cfg[symbol], config))
    ok_count = sum(1 for item in results if item.get("status") == "ok")
    return {
        "enabled": True,
        "results": results,
        "summary": f"{ok_count}/{len(results)}个标的完成历史回测；未完成项会列出真实失败原因，不伪造收益。",
    }
