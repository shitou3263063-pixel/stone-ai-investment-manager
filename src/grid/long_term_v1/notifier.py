from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Mapping

from src.notifier.email_notifier import send_grid_alert_email

from .models import GridDecision, GridStatus
from .state_store import LongTermGridStateStore


class GridAlertNotifier:
    def __init__(
        self,
        config: Mapping[str, Any],
        state_store: LongTermGridStateStore,
        *,
        sender=send_grid_alert_email,
    ) -> None:
        settings = config.get("email") or {}
        self.enabled = bool(settings.get("enabled", config.get("email_alerts_enabled", False)))
        self.dry_run = bool(settings.get("dry_run", config.get("email_dry_run", True)))
        self.cooldown_minutes = int(settings.get("cooldown_minutes", 60))
        self.state_store = state_store
        self.sender = sender

    def force_dry_run(self) -> None:
        self.enabled = True
        self.dry_run = True

    def process(self, decision: GridDecision, *, now: datetime) -> dict[str, Any]:
        event_type = _email_event_type(decision)
        if event_type is None or not self.enabled:
            return {"sent": False, "skipped": True, "message": "no grid email trigger"}
        fingerprint = sha256(
            f"{decision.symbol}|{event_type}".encode("utf-8")
        ).hexdigest()
        if not self.state_store.notification_allowed(fingerprint, now=now):
            return {"sent": False, "skipped": True, "message": "grid email cooldown active"}
        payload = {
            **decision.to_dict(),
            "event_type": event_type,
            "price": decision.current_price,
            "suggested_amount_cny": decision.adjusted_amount_cny,
            "automatic_trading": False,
            "simulation_only": True,
            "timezone": "America/New_York",
        }
        try:
            result = self.sender(payload, dry_run=self.dry_run)
        except Exception as exc:  # noqa: BLE001 - delivery never stops the engine
            result = {
                "sent": False,
                "skipped": False,
                "dry_run": self.dry_run,
                "message": "grid SMTP failure; strategy continued",
                "error_type": type(exc).__name__,
            }
        persisted = bool(result.get("sent") or result.get("dry_run"))
        self.state_store.record_notification(
            fingerprint,
            symbol=decision.symbol,
            event_type=event_type,
            now=now,
            cooldown_minutes=self.cooldown_minutes,
            sent=persisted,
        )
        return result


def _email_event_type(decision: GridDecision) -> str | None:
    if decision.status is GridStatus.GRID_BUY_CANDIDATE:
        return "GRID_BUY_CANDIDATE"
    if decision.status is GridStatus.GRID_TAKE_PROFIT_CANDIDATE:
        return "GRID_TAKE_PROFIT_CANDIDATE"
    if decision.status is GridStatus.GRID_BLOCKED:
        data_tokens = ("DATA", "QUOTE", "VIX_MISSING", "VIX_STALE", "MA20")
        return (
            "DATA_FAILURE"
            if any(
                any(token in reason for token in data_tokens)
                for reason in decision.blocked_reasons
            )
            else "GRID_BLOCKED"
        )
    return None
