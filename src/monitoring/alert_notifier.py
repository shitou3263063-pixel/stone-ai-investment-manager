from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.notifier.email_notifier import send_intraday_alert_email

from .models import DataStatus, MonitorSnapshot
from .state_store import MonitoringStateStore


MARKET_TIMEZONES = {
    "US": ZoneInfo("America/New_York"),
    "HK": ZoneInfo("Asia/Hong_Kong"),
    "CN": ZoneInfo("Asia/Shanghai"),
}
TRUSTED = {DataStatus.VALID, DataStatus.DELAYED_VALID}


class IntradayAlertNotifier:
    """Observation-only alert delivery layered over persisted monitoring state."""

    def __init__(
        self,
        config: Mapping[str, Any],
        state_store: MonitoringStateStore,
        *,
        sender=send_intraday_alert_email,
    ) -> None:
        self.config = dict(config.get("application") or {})
        self.state_store = state_store
        self.sender = sender
        self.enabled = bool(self.config.get("enabled", True))
        self.email_enabled = bool(self.config.get("email_alerts_enabled", False))
        self.dry_run = bool(self.config.get("email_dry_run", True))
        self.cooldown_minutes = int(self.config.get("alert_cooldown_minutes", 60))
        self.failure_threshold = int(self.config.get("consecutive_failure_threshold", 3))
        self.max_delay_seconds = float(self.config.get("max_quote_delay_seconds", 300))
        self.thresholds = {
            "price_5m": float(self.config.get("move_5m_threshold_pct", 2.0)),
            "price_15m": float(self.config.get("move_15m_threshold_pct", 3.0)),
            "daily_change": float(self.config.get("day_move_threshold_pct", 5.0)),
        }

    def force_dry_run(self) -> None:
        self.email_enabled = True
        self.dry_run = True

    def process(self, result: Any, *, now: datetime) -> dict[str, Any]:
        if not self.enabled:
            return {"triggered": 0, "suppressed": 0, "deliveries": []}
        current = _as_utc(now)
        candidates: list[dict[str, Any]] = []
        recoveries: list[dict[str, Any]] = []
        futu_valid_count = 0

        for snapshot in result.snapshots:
            changes = result.change_results.get(snapshot.symbol, {})
            five = getattr(changes.get("5m"), "change_percent", None)
            fifteen = getattr(changes.get("15m"), "change_percent", None)
            futu_valid = snapshot.source.lower() == "futu" and snapshot.data_status in TRUSTED
            futu_valid_count += int(futu_valid)
            failure_key = f"futu_symbol_failure:{snapshot.symbol}"
            failures = self.state_store.observe_condition(
                failure_key, active=not futu_valid, now=current
            )
            if not futu_valid and failures >= self.failure_threshold:
                candidates.append(
                    self._payload(snapshot, "futu_symbol_failure", current, five, fifteen)
                )
            elif futu_valid and self.state_store.recover_notification(
                snapshot.symbol, "futu_symbol_failure"
            ):
                recoveries.append(
                    self._recovery_payload(snapshot.symbol, snapshot.market, current, "futu_symbol_failure")
                )

            delay = _quote_delay(snapshot, current)
            if (
                snapshot.data_status is DataStatus.STALE
                and delay is not None
                and delay > self.max_delay_seconds
            ):
                candidates.append(
                    self._payload(snapshot, "quote_delay", current, five, fifteen, delay=delay)
                )
            elif snapshot.data_status in TRUSTED and self.state_store.recover_notification(
                snapshot.symbol, "quote_delay"
            ):
                recoveries.append(
                    self._recovery_payload(snapshot.symbol, snapshot.market, current, "quote_delay")
                )

            if snapshot.data_status in TRUSTED:
                for rule_id, value in (
                    ("price_5m", five),
                    ("price_15m", fifteen),
                    ("daily_change", snapshot.change_percent),
                ):
                    if value is not None and abs(float(value)) >= self.thresholds[rule_id]:
                        candidates.append(
                            self._payload(snapshot, rule_id, current, five, fifteen, value=float(value))
                        )
                    elif self.state_store.recover_notification(snapshot.symbol, rule_id):
                        recoveries.append(
                            self._recovery_payload(snapshot.symbol, snapshot.market, current, rule_id)
                        )

        all_invalid = bool(result.snapshots) and futu_valid_count == 0
        all_invalid_count = self.state_store.observe_condition(
            "futu_all_invalid", active=all_invalid, now=current
        )
        if all_invalid and all_invalid_count >= self.failure_threshold:
            candidates.append(self._global_payload("futu_all_invalid", current))
        elif not all_invalid and self.state_store.recover_notification("FUTU", "futu_all_invalid"):
            recoveries.append(self._recovery_payload("FUTU", "MULTI", current, "futu_all_invalid"))

        health = self.state_store.source_health("futu") or {}
        connection_failed = int(health.get("consecutive_failures") or 0) >= self.failure_threshold
        if connection_failed:
            candidates.append(self._global_payload("futu_connection_failure", current))
        elif str(health.get("status") or "").upper() == "HEALTHY" and self.state_store.recover_notification(
            "FUTU", "futu_connection_failure"
        ):
            recoveries.append(
                self._recovery_payload("FUTU", "MULTI", current, "futu_connection_failure")
            )

        triggered = 0
        suppressed = 0
        deliveries: list[dict[str, Any]] = []
        for payload in [*candidates, *recoveries]:
            fingerprint = str(payload["fingerprint"])
            if not self.state_store.notification_allowed(
                fingerprint, now=current, value=payload.get("value")
            ):
                suppressed += 1
                continue
            if not self.email_enabled:
                continue
            delivery = self.sender(payload, dry_run=self.dry_run)
            persisted_as_sent = bool(delivery.get("sent") or delivery.get("dry_run"))
            self.state_store.record_notification(
                fingerprint=fingerprint,
                subject=str(payload["symbol"]),
                rule_id=str(payload["rule_id"]),
                now=current,
                cooldown_minutes=self.cooldown_minutes,
                sent=persisted_as_sent,
                active=persisted_as_sent,
                value=payload.get("value"),
            )
            deliveries.append(delivery)
            triggered += 1
        return {"triggered": triggered, "suppressed": suppressed, "deliveries": deliveries}

    def _payload(
        self,
        snapshot: MonitorSnapshot,
        rule_id: str,
        now: datetime,
        five: float | None,
        fifteen: float | None,
        *,
        value: float | None = None,
        delay: float | None = None,
    ) -> dict[str, Any]:
        direction = "DATA" if value is None else ("UP" if value >= 0 else "DOWN")
        return {
            "fingerprint": _fingerprint(snapshot.symbol, rule_id, direction),
            "symbol": snapshot.symbol,
            "market": snapshot.market,
            "price": snapshot.price,
            "day_change_pct": snapshot.change_percent,
            "change_5m": five,
            "change_15m": fifteen,
            "quote_time": snapshot.timestamp.isoformat() if snapshot.timestamp else None,
            "delay_seconds": delay if delay is not None else _quote_delay(snapshot, now),
            "source": snapshot.source,
            "rule_id": rule_id,
            "value": value,
            "observed_at": now.isoformat(),
            "local_time": now.astimezone(MARKET_TIMEZONES.get(snapshot.market, timezone.utc)).isoformat(),
            "message": f"{snapshot.symbol} observed monitor condition: {rule_id}",
        }

    @staticmethod
    def _global_payload(rule_id: str, now: datetime) -> dict[str, Any]:
        return {
            "fingerprint": _fingerprint("FUTU", rule_id, "DATA"),
            "symbol": "FUTU",
            "market": "MULTI",
            "source": "futu",
            "rule_id": rule_id,
            "observed_at": now.isoformat(),
            "local_time": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
            "message": f"Futu monitor condition persisted: {rule_id}",
        }

    @staticmethod
    def _recovery_payload(subject: str, market: str, now: datetime, recovered_rule: str) -> dict[str, Any]:
        rule_id = f"{recovered_rule}_recovered"
        return {
            "fingerprint": _fingerprint(subject, rule_id, "RECOVERED"),
            "symbol": subject,
            "market": market,
            "source": "futu",
            "rule_id": rule_id,
            "observed_at": now.isoformat(),
            "local_time": now.astimezone(MARKET_TIMEZONES.get(market, timezone.utc)).isoformat(),
            "message": f"{subject} data source recovered from {recovered_rule}",
        }


def _fingerprint(subject: str, rule_id: str, direction: str) -> str:
    return sha256(f"EMAIL|{subject.upper()}|{rule_id}|{direction}".encode("utf-8")).hexdigest()


def _quote_delay(snapshot: MonitorSnapshot, now: datetime) -> float | None:
    if snapshot.timestamp is None:
        return None
    return max(0.0, (_as_utc(now) - _as_utc(snapshot.timestamp)).total_seconds())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("monitor notification time must include a timezone")
    return value.astimezone(timezone.utc)
