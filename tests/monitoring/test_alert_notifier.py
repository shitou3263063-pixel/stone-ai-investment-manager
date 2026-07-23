from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.monitoring.alert_notifier import IntradayAlertNotifier
from src.monitoring.models import ChangeResult, ChangeStatus, DataStatus, MonitorSnapshot
from src.monitoring.state_store import MonitoringStateStore
from src.notifier import email_notifier


NOW = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    status: DataStatus = DataStatus.VALID,
    source: str = "futu",
    price: float = 105.0,
    day_change: float = 5.0,
    timestamp: datetime | None = NOW,
) -> MonitorSnapshot:
    return MonitorSnapshot(
        symbol="NVDA",
        asset_name="NVIDIA",
        market="US",
        price=price,
        previous_close=100.0,
        change=price - 100.0,
        change_percent=day_change,
        timestamp=timestamp,
        source=source,
        data_status=status,
        confidence=0.95,
        is_stale=status is DataStatus.STALE,
    )


def _change(minutes: int, value: float | None) -> ChangeResult:
    return ChangeResult(
        window_minutes=minutes,
        status=ChangeStatus.OK if value is not None else ChangeStatus.INSUFFICIENT_HISTORY,
        change_percent=value,
        reference_price=100.0 if value is not None else None,
        current_price=105.0,
        reference_captured_at=NOW - timedelta(minutes=minutes) if value is not None else None,
        current_captured_at=NOW,
    )


def _result(snapshot: MonitorSnapshot, *, five: float | None = None, fifteen: float | None = None):
    return SimpleNamespace(
        snapshots=(snapshot,),
        change_results={"NVDA": {"5m": _change(5, five), "15m": _change(15, fifteen)}},
    )


def _config() -> dict:
    return {
        "application": {
            "enabled": True,
            "email_alerts_enabled": True,
            "email_dry_run": True,
            "alert_cooldown_minutes": 60,
            "consecutive_failure_threshold": 3,
            "max_quote_delay_seconds": 300,
            "move_5m_threshold_pct": 2.0,
            "move_15m_threshold_pct": 3.0,
            "day_move_threshold_pct": 5.0,
        }
    }


def test_price_thresholds_are_configuration_driven(tmp_path: Path) -> None:
    sent: list[dict] = []
    notifier = IntradayAlertNotifier(
        _config(),
        MonitoringStateStore(tmp_path / "monitor.sqlite3"),
        sender=lambda payload, **_: sent.append(payload) or {"dry_run": True},
    )
    outcome = notifier.process(_result(_snapshot(), five=2.1, fifteen=-3.1), now=NOW)
    assert outcome["triggered"] == 3
    assert {item["rule_id"] for item in sent} == {"price_5m", "price_15m", "daily_change"}


def test_failure_alert_waits_for_three_consecutive_rounds(tmp_path: Path) -> None:
    sent: list[dict] = []
    store = MonitoringStateStore(tmp_path / "monitor.sqlite3")
    notifier = IntradayAlertNotifier(
        _config(),
        store,
        sender=lambda payload, **_: sent.append(payload) or {"dry_run": True},
    )
    failed = _result(
        _snapshot(status=DataStatus.ERROR, source="unavailable", timestamp=None, day_change=0)
    )
    notifier.process(failed, now=NOW)
    notifier.process(failed, now=NOW + timedelta(minutes=1))
    assert sent == []
    notifier.process(failed, now=NOW + timedelta(minutes=2))
    assert {"futu_symbol_failure", "futu_all_invalid"} <= {
        item["rule_id"] for item in sent
    }


def test_persistent_cooldown_survives_notifier_restart(tmp_path: Path) -> None:
    sent: list[dict] = []
    path = tmp_path / "monitor.sqlite3"
    first = IntradayAlertNotifier(
        _config(),
        MonitoringStateStore(path),
        sender=lambda payload, **_: sent.append(payload) or {"dry_run": True},
    )
    first.process(_result(_snapshot(day_change=5.5)), now=NOW)
    second = IntradayAlertNotifier(
        _config(),
        MonitoringStateStore(path),
        sender=lambda payload, **_: sent.append(payload) or {"dry_run": True},
    )
    outcome = second.process(_result(_snapshot(day_change=5.5)), now=NOW + timedelta(minutes=30))
    assert len([item for item in sent if item["rule_id"] == "daily_change"]) == 1
    assert outcome["suppressed"] >= 1


def test_recovery_is_sent_once(tmp_path: Path) -> None:
    sent: list[dict] = []
    notifier = IntradayAlertNotifier(
        _config(),
        MonitoringStateStore(tmp_path / "monitor.sqlite3"),
        sender=lambda payload, **_: sent.append(payload) or {"dry_run": True},
    )
    notifier.process(_result(_snapshot(day_change=5.5)), now=NOW)
    notifier.process(_result(_snapshot(day_change=0.5)), now=NOW + timedelta(minutes=1))
    notifier.process(_result(_snapshot(day_change=0.5)), now=NOW + timedelta(minutes=2))
    assert len([item for item in sent if item["rule_id"] == "daily_change_recovered"]) == 1


def test_email_dry_run_never_opens_smtp(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        email_notifier,
        "_send_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SMTP must not run")),
    )
    result = email_notifier.send_intraday_alert_email(
        {"symbol": "NVDA", "rule_id": "price_5m", "value": -2.1},
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert "EMAIL DRY-RUN" in capsys.readouterr().out


def test_smtp_failure_is_returned_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(email_notifier, "_get_email_config", lambda *_: {"EMAIL_TO": "x@example.test"})
    monkeypatch.setattr(
        email_notifier,
        "_send_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("network unavailable")),
    )
    result = email_notifier.send_intraday_alert_email(
        {"symbol": "NVDA", "rule_id": "daily_change"},
        dry_run=False,
    )
    assert result["sent"] is False
    assert result["attempted"] is True
