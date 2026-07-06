from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TROY_OUNCE_GRAMS = 31.1034768


def _write_log(message: str) -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = log_dir / "gold_price.log"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    log_path.write_text(existing + f"[{timestamp}] {message}\n", encoding="utf-8")


def _latest_close(ticker: str) -> float:
    import yfinance as yf  # type: ignore

    history = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
    if history.empty:
        raise ValueError(f"{ticker} 返回数据为空")

    closes = history["Close"].dropna()
    if len(closes) == 0:
        raise ValueError(f"{ticker} Close 数据为空")
    return float(closes.iloc[-1])


def fetch_gold_price_cny_per_gram() -> dict[str, Any]:
    """估算人民币/克黄金价格；失败时返回 ok=False，不抛异常。"""
    try:
        gold_usd_per_oz = _latest_close("GC=F")
        usd_cny = _latest_close("USDCNY=X")
        price_cny_per_gram = gold_usd_per_oz * usd_cny / TROY_OUNCE_GRAMS
        result = {
            "ok": True,
            "price_cny_per_gram": round(price_cny_per_gram, 2),
            "gold_usd_per_oz": round(gold_usd_per_oz, 2),
            "usd_cny": round(usd_cny, 4),
            "source": "yfinance: GC=F + USDCNY=X",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "error": "",
        }
        _write_log(
            "黄金价格获取成功："
            f"{result['price_cny_per_gram']} 元/克，"
            f"GC=F {result['gold_usd_per_oz']}，USDCNY {result['usd_cny']}"
        )
        return result
    except Exception as exc:  # noqa: BLE001 - 金价失败不能影响日报
        message = f"黄金价格获取失败：{exc}"
        _write_log(message)
        return {
            "ok": False,
            "price_cny_per_gram": None,
            "gold_usd_per_oz": None,
            "usd_cny": None,
            "source": "unavailable",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "error": str(exc),
        }


def estimate_gold_bar_value(quantity_grams: float) -> dict[str, Any]:
    """按实时黄金价格估算金条市值；失败时返回未估值说明。"""
    price_result = fetch_gold_price_cny_per_gram()
    if not price_result["ok"] or not price_result.get("price_cny_per_gram"):
        return {
            "ok": False,
            "amount_wan": 0.0,
            "quantity_grams": quantity_grams,
            "price_cny_per_gram": None,
            "source": price_result.get("source", "unavailable"),
            "fetched_at": price_result.get("fetched_at"),
            "error": price_result.get("error", ""),
        }

    price = float(price_result["price_cny_per_gram"])
    amount_wan = quantity_grams * price / 10000
    return {
        "ok": True,
        "amount_wan": round(amount_wan, 4),
        "quantity_grams": quantity_grams,
        "price_cny_per_gram": price,
        "source": price_result["source"],
        "fetched_at": price_result["fetched_at"],
        "error": "",
    }
