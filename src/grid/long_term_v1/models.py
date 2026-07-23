from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


STRATEGY_ID = "LONG_TERM_GRID_V1"
MODE = "SIMULATION_ONLY"


class GridStatus(str, Enum):
    NO_ACTION = "NO_ACTION"
    GRID_BUY_CANDIDATE = "GRID_BUY_CANDIDATE"
    GRID_TAKE_PROFIT_CANDIDATE = "GRID_TAKE_PROFIT_CANDIDATE"
    GRID_BLOCKED = "GRID_BLOCKED"
    ALLOW_EVALUATION_ONLY = "ALLOW_EVALUATION_ONLY"


class PositionType(str, Enum):
    CORE_POSITION = "CORE_POSITION"
    DCA_POSITION = "DCA_POSITION"
    GRID_POSITION = "GRID_POSITION"


@dataclass(frozen=True)
class MarketInputs:
    symbol: str
    price: float | None
    source: str
    quote_time: datetime | None
    quote_status: str
    quote_delay_seconds: float | None
    previous_close: float | None
    ma20: float | None
    market_session: str
    dqs: float | None
    risk_score: float | None
    vix: float | None
    vix_time: datetime | None
    usd_cny: float | None
    data_anomalies: tuple[str, ...] = ()
    consecutive_days_above_ma20: int = 0
    input_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quote_time is not None and self.quote_time.tzinfo is None:
            raise ValueError("quote_time must include timezone")
        if self.vix_time is not None and self.vix_time.tzinfo is None:
            raise ValueError("vix_time must include timezone")


@dataclass(frozen=True)
class GridDecision:
    event_id: str
    strategy_id: str
    symbol: str
    status: GridStatus
    generated_at: datetime
    current_price: float | None
    source: str
    quote_time: datetime | None
    quote_delay_seconds: float | None
    quote_status: str
    market_session: str
    reference_center: float | None
    center_deviation_pct: float | None
    grid_level: int | None
    standard_amount_cny: float
    adjusted_amount_cny: float
    amount_usd: float
    estimated_quantity: int
    remaining_grid_budget_cny: float
    used_budget_pct: float
    take_profit_1: float | None
    take_profit_2: float | None
    dqs: float | None
    risk_score: float | None
    vix: float | None
    blocked_reasons: tuple[str, ...] = ()
    position_scope: str = PositionType.GRID_POSITION.value
    flags: tuple[str, ...] = (MODE, "NO_AUTOMATIC_TRADING")
    estimated_fees_cny: float = 0.0
    estimated_slippage_cny: float = 0.0
    decision_inputs_hash: str = ""
    lot_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["generated_at"] = self.generated_at.isoformat()
        payload["quote_time"] = self.quote_time.isoformat() if self.quote_time else None
        return payload
