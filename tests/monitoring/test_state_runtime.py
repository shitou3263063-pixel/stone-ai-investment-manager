from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from src.monitoring.alert_rules import AlertRuleEngine
from src.monitoring.intraday_monitor import IntradayMonitor
from src.monitoring.market_clock import MarketClock
from src.monitoring.models import ChangeStatus, DataStatus, MonitorSnapshot
from src.monitoring.state_store import MonitoringStateStore


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    price: float = 100.0,
    status: DataStatus = DataStatus.VALID,
    timestamp: datetime = NOW,
) -> MonitorSnapshot:
    return MonitorSnapshot(
        symbol="VOO", asset_name="VOO", market="US", price=price,
        previous_close=99.0, change=price - 99.0,
        change_percent=(price / 99.0 - 1) * 100, timestamp=timestamp,
        source="finnhub", data_status=status, confidence=0.9,
        is_stale=status is DataStatus.STALE,
    )


def _config(tmp_path: Path) -> dict:
    return {
        "symbols": [{"symbol": "VOO", "route_symbol": "VOO", "asset_name": "VOO", "market": "US"}],
        "market_clock": {"holidays": {"US": [], "CN": [], "HK": []}},
        "freshness": {"intraday_max_age_minutes": 15, "future_tolerance_minutes": 2, "closed_market_max_age_hours": 120},
        "source_routing": {"allow_delayed_quotes": True},
        "change_calculation": {"five_minute_tolerance_seconds": 120, "fifteen_minute_tolerance_seconds": 180},
        "rules": {
            "price_5m": {"threshold_percent": 1.5, "severity": "WATCH"},
            "price_15m": {"threshold_percent": 2.5, "severity": "WARNING"},
            "daily_change": {"threshold_percent": 4.0, "severity": "CRITICAL"},
            "data_stale": {"severity": "WARNING"},
            "source_conflict": {"severity": "CRITICAL"},
            "all_sources_failed": {"severity": "CRITICAL"},
        },
        "cooldowns_minutes": {"WATCH": 60, "WARNING": 30, "CRITICAL": 10},
        "runtime": {"max_workers": 1},
        "retention": {"snapshot_retention_days": 7, "alert_retention_days": 30, "source_health_retention_days": 30},
        "storage": {"sqlite_path": str(tmp_path / "monitor.sqlite3")},
        "logging": {"structured_log_path": str(tmp_path / "monitor.jsonl")},
        "intervals": {"trading_interval_seconds": 60, "closed_interval_seconds": 900, "weekend_interval_seconds": 3600},
    }


def test_five_minute_change_uses_trusted_snapshot(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.save_snapshot(_snapshot(price=100, timestamp=NOW - timedelta(minutes=5)), captured_at=NOW - timedelta(minutes=5))
    result = store.calculate_change(_snapshot(price=102), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120)
    assert result.status is ChangeStatus.OK
    assert round(result.change_percent or 0, 2) == 2.0
    assert result.reference_captured_at == NOW - timedelta(minutes=5)
    assert result.current_captured_at == NOW


def test_fifteen_minute_change_uses_trusted_snapshot(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.save_snapshot(_snapshot(price=100, timestamp=NOW - timedelta(minutes=15)), captured_at=NOW - timedelta(minutes=15))
    result = store.calculate_change(_snapshot(price=97), captured_at=NOW, lookback_minutes=15, tolerance_seconds=180)
    assert result.status is ChangeStatus.OK
    assert round(result.change_percent or 0, 2) == -3.0


def test_insufficient_history_is_explicit(tmp_path: Path) -> None:
    result = MonitoringStateStore(tmp_path / "state.sqlite3").calculate_change(
        _snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120
    )
    assert result.status is ChangeStatus.INSUFFICIENT_HISTORY


def test_no_matching_snapshot_is_explicit(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.save_snapshot(_snapshot(timestamp=NOW - timedelta(minutes=30)), captured_at=NOW - timedelta(minutes=30))
    result = store.calculate_change(_snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120)
    assert result.status is ChangeStatus.NO_MATCHING_SNAPSHOT


def test_stale_snapshot_is_excluded_from_change(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.save_snapshot(_snapshot(status=DataStatus.STALE), captured_at=NOW - timedelta(minutes=5))
    result = store.calculate_change(_snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120)
    assert result.status is ChangeStatus.INSUFFICIENT_HISTORY


def test_conflict_snapshot_is_excluded_from_change(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.save_snapshot(_snapshot(status=DataStatus.CONFLICT), captured_at=NOW - timedelta(minutes=5))
    result = store.calculate_change(_snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120)
    assert result.status is ChangeStatus.INSUFFICIENT_HISTORY


def test_change_never_crosses_market_session_date(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    previous_day = NOW - timedelta(days=1, minutes=5)
    store.save_snapshot(_snapshot(timestamp=previous_day), captured_at=previous_day)
    result = store.calculate_change(_snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=90000)
    assert result.status is ChangeStatus.INSUFFICIENT_HISTORY


def test_delayed_snapshot_can_be_configured_as_trusted(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.save_snapshot(_snapshot(status=DataStatus.DELAYED_VALID), captured_at=NOW - timedelta(minutes=5))
    allowed = store.calculate_change(_snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120, allow_delayed=True)
    blocked = store.calculate_change(_snapshot(), captured_at=NOW, lookback_minutes=5, tolerance_seconds=120, allow_delayed=False)
    assert allowed.status is ChangeStatus.OK
    assert blocked.status is ChangeStatus.INSUFFICIENT_HISTORY


def test_sustained_anomaly_updates_state_without_repeat(tmp_path: Path) -> None:
    config = _config(tmp_path)
    stale = {
        "close": 100.0, "previous_close": 99.0, "status": "ok", "source": "cache:yfinance",
        "cache_used": True, "quote_timestamp": NOW.isoformat(),
    }
    monitor = IntradayMonitor(config, quote_fetcher=lambda _: stale, root=tmp_path)
    first = monitor.run_once(now=NOW, print_table=False)
    second = monitor.run_once(now=NOW + timedelta(minutes=1), print_table=False)
    assert len(first.alerts) == 1
    assert len(second.alerts) == 0
    assert len(second.suppressed_alerts) == 1
    assert monitor.state_store.table_count("alert_history") == 2


def test_anomaly_recovery_generates_recovered_event(tmp_path: Path) -> None:
    config = _config(tmp_path)
    rows = iter([
        {"close": 100.0, "previous_close": 99.0, "status": "ok", "source": "cache:yfinance", "cache_used": True, "quote_timestamp": NOW.isoformat()},
        {"close": 100.0, "previous_close": 99.0, "status": "ok", "source": "finnhub", "quote_timestamp": (NOW + timedelta(minutes=1)).isoformat(), "monitor_quote_status": "REALTIME_VALID"},
    ])
    monitor = IntradayMonitor(config, quote_fetcher=lambda _: next(rows), root=tmp_path)
    monitor.run_once(now=NOW, print_table=False)
    recovered = monitor.run_once(now=NOW + timedelta(minutes=1), print_table=False)
    assert any(event.event_type == "ALERT_RECOVERED" for event in recovered.recovery_events)


def test_source_recovery_generates_health_event(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    store.record_source_failure(
        "finnhub", now=NOW, error_type="UNAVAILABLE", error_message="timeout", latency_ms=10,
        retry_initial_seconds=1, retry_max_seconds=10, failure_threshold=2,
    )
    event = store.record_source_success("finnhub", now=NOW + timedelta(seconds=2), latency_ms=5)
    assert event is not None
    assert event.event_type == "SOURCE_RECOVERED"
    assert store.source_health("finnhub")["consecutive_failures"] == 0


def test_schema_migration_preserves_mvp_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    payload = json.dumps(_snapshot().to_dict())
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE monitor_snapshots(id INTEGER PRIMARY KEY, symbol TEXT, captured_at TEXT, payload_json TEXT);
            CREATE TABLE alert_state(
                fingerprint TEXT PRIMARY KEY, symbol TEXT, rule_id TEXT, direction TEXT,
                severity TEXT, last_alert_at TEXT, cooldown_until TEXT
            );
            """
        )
        connection.execute("INSERT INTO monitor_snapshots VALUES (1, ?, ?, ?)", ("VOO", NOW.isoformat(), payload))
    store = MonitoringStateStore(path)
    assert store.table_count("monitor_snapshots") == 1
    assert store.table_count("snapshots") == 1


def test_cleanup_preserves_active_cooldown_state(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3", snapshot_retention_days=1, alert_retention_days=1)
    alert = AlertRuleEngine(_config(tmp_path)["rules"]).evaluate(
        _snapshot(status=DataStatus.STALE), MarketClock().status("US", NOW), observed_at=NOW
    )[0]
    store.record_alert(alert, now=NOW, cooldown_minutes={"WATCH": 60, "WARNING": 30, "CRITICAL": 10})
    store.cleanup(now=NOW + timedelta(days=10))
    assert store.alert_state(alert.fingerprint) is not None


def test_monitor_does_not_change_protected_files_or_daily_cache(tmp_path: Path) -> None:
    protected = [
        tmp_path / "data" / "portfolio_master.yaml",
        tmp_path / "data" / "execution_state.json",
        tmp_path / "data" / "cache" / "quote.json",
        tmp_path / "data" / "grid" / "grid_state.json",
        tmp_path / "reports" / "daily_report.md",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    raw = {"close": 100.0, "previous_close": 99.0, "status": "ok", "source": "finnhub", "quote_timestamp": NOW.isoformat(), "monitor_quote_status": "REALTIME_VALID"}
    IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: raw, root=tmp_path).run_once(now=NOW, print_table=False)
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    assert after == before
