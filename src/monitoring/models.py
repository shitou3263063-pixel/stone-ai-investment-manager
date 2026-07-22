from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class DataStatus(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    ERROR = "ERROR"


class AlertSeverity(str, Enum):
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class MonitorSnapshot:
    symbol: str
    asset_name: str
    market: str
    price: float | None
    previous_close: float | None
    change: float | None
    change_percent: float | None
    timestamp: datetime | None
    source: str
    data_status: DataStatus
    confidence: float
    is_stale: bool

    def __post_init__(self) -> None:
        if self.timestamp is not None and self.timestamp.tzinfo is None:
            raise ValueError("monitor snapshot timestamp must include a timezone")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        payload["data_status"] = self.data_status.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MonitorSnapshot":
        raw_timestamp = payload.get("timestamp")
        timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00")) if raw_timestamp else None
        return cls(
            symbol=str(payload["symbol"]),
            asset_name=str(payload.get("asset_name") or payload["symbol"]),
            market=str(payload["market"]),
            price=_optional_float(payload.get("price")),
            previous_close=_optional_float(payload.get("previous_close")),
            change=_optional_float(payload.get("change")),
            change_percent=_optional_float(payload.get("change_percent")),
            timestamp=timestamp,
            source=str(payload.get("source") or "unavailable"),
            data_status=DataStatus(str(payload["data_status"])),
            confidence=float(payload.get("confidence") or 0.0),
            is_stale=bool(payload.get("is_stale")),
        )


@dataclass(frozen=True)
class Alert:
    fingerprint: str
    symbol: str
    rule_id: str
    direction: str
    severity: AlertSeverity
    message: str
    observed_at: datetime
    value: float | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("alert observed_at must include a timezone")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


def _optional_float(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None
