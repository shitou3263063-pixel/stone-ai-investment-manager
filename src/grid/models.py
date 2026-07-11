from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


GRID_VERSION = "Stone AI Investment Manager Pro V12.5 Stable"
GRID_STATES = {
    "IDLE",
    "WAIT_BUY",
    "BUY_SIGNAL",
    "BUY_APPROVED",
    "BUY_REJECTED",
    "HOLDING_GRID_POSITION",
    "WAIT_SELL",
    "SELL_SIGNAL",
    "SELL_APPROVED",
    "SELL_REJECTED",
    "PAUSED",
    "SAFE_MODE",
}


@dataclass
class GridSignal:
    symbol: str
    action: str
    raw_signal: str
    price: float | None
    trigger_price: float | None
    amount_yuan: float
    quantity: float
    layer: int
    reason: str
    expected_profit_pct: float = 0.0
    valid_until: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskReview:
    symbol: str
    approved: bool
    rejected: bool
    paper_only: bool
    reasons: list[str]
    final_advice: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GridSymbolState:
    symbol: str
    state: str = "IDLE"
    anchor_price: float | None = None
    buy_spacing_pct: float = 0.0
    sell_spacing_pct: float = 0.0
    next_buy_price: float | None = None
    next_sell_price: float | None = None
    core_quantity: float = 0.0
    grid_quantity: float = 0.0
    grid_cost_yuan: float = 0.0
    available_grid_cash_yuan: float = 0.0
    month_used_yuan: float = 0.0
    month_trade_count: int = 0
    day_trade_count: int = 0
    consecutive_buys: int = 0
    last_trade_time: str = ""
    last_trade_price: float | None = None
    realized_profit_yuan: float = 0.0
    unrealized_profit_yuan: float = 0.0
    market_regime: str = "unknown"
    last_signal_key: str = ""
    processed_trade_ids: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["state"] not in GRID_STATES:
            data["state"] = "SAFE_MODE"
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GridSymbolState":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        values = {key: value for key, value in (payload or {}).items() if key in known}
        return cls(**values)
