from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Mapping

from .market_clock import MarketStatus
from .models import Alert, AlertSeverity, DataStatus, MonitorSnapshot


DATA_RULE_BY_STATUS = {
    DataStatus.STALE: "data_stale",
    DataStatus.CONFLICT: "source_conflict",
    DataStatus.MISSING: "all_sources_failed",
    DataStatus.ERROR: "all_sources_failed",
}


class AlertRuleEngine:
    def __init__(self, rules: Mapping[str, Mapping[str, Any]]) -> None:
        self.rules = {key: dict(value) for key, value in rules.items()}

    def evaluate(
        self,
        snapshot: MonitorSnapshot,
        market_status: MarketStatus,
        *,
        observed_at: datetime,
        reference_changes: Mapping[str, float | None] | None = None,
    ) -> list[Alert]:
        alerts: list[Alert] = []
        data_rule = DATA_RULE_BY_STATUS.get(snapshot.data_status)
        if data_rule:
            if bool(self.rules.get(data_rule, {}).get("enabled", True)):
                alerts.append(self._build(data_rule, snapshot, "DATA", observed_at, None))
            return alerts

        if not market_status.is_trading or snapshot.data_status is not DataStatus.VALID:
            return alerts

        changes = reference_changes or {}
        for rule_id, change_key in (("price_5m", "5m"), ("price_15m", "15m")):
            value = changes.get(change_key)
            if self._exceeds(rule_id, value):
                alerts.append(self._build(rule_id, snapshot, _direction(value), observed_at, value))
        if self._exceeds("daily_change", snapshot.change_percent):
            alerts.append(
                self._build(
                    "daily_change",
                    snapshot,
                    _direction(snapshot.change_percent),
                    observed_at,
                    snapshot.change_percent,
                )
            )
        return alerts

    def _exceeds(self, rule_id: str, value: float | None) -> bool:
        rule = self.rules.get(rule_id, {})
        if not bool(rule.get("enabled", True)) or value is None:
            return False
        return abs(float(value)) > float(rule.get("threshold_percent", float("inf")))

    def _build(
        self,
        rule_id: str,
        snapshot: MonitorSnapshot,
        direction: str,
        observed_at: datetime,
        value: float | None,
    ) -> Alert:
        rule = self.rules.get(rule_id, {})
        severity = AlertSeverity(str(rule.get("severity") or "WATCH").upper())
        fingerprint = alert_fingerprint(snapshot.symbol, rule_id, direction)
        if value is None:
            message = f"{snapshot.symbol} observed data condition: {rule_id}"
        else:
            message = f"{snapshot.symbol} observed {rule_id} {value:+.2f}%"
        return Alert(
            fingerprint=fingerprint,
            symbol=snapshot.symbol,
            rule_id=rule_id,
            direction=direction,
            severity=severity,
            message=message,
            observed_at=observed_at,
            value=value,
        )


def alert_fingerprint(symbol: str, rule_id: str, direction: str) -> str:
    raw = f"{symbol.upper()}|{rule_id}|{direction.upper()}"
    return sha256(raw.encode("utf-8")).hexdigest()


def price_change_percent(current: float | None, reference: float | None) -> float | None:
    if current is None or reference in {None, 0}:
        return None
    return (float(current) / float(reference) - 1.0) * 100.0


def _direction(value: float | None) -> str:
    if value is None or value == 0:
        return "FLAT"
    return "UP" if value > 0 else "DOWN"
