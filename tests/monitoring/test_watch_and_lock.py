from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest
import yaml

from scripts import run_intraday_monitor as cli
from src.monitoring.intraday_monitor import IntradayMonitor
from src.monitoring.process_lock import LOCK_HELD_EXIT_CODE, LockHeldError, MonitorProcessLock


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)


def _config(tmp_path: Path) -> dict:
    return {
        "symbols": [{"symbol": "VOO", "route_symbol": "VOO", "asset_name": "VOO", "market": "US"}],
        "market_clock": {"holidays": {"US": [], "CN": [], "HK": []}},
        "freshness": {"intraday_max_age_minutes": 15, "future_tolerance_minutes": 2, "closed_market_max_age_hours": 120},
        "source_routing": {"source_priority": {"US": ["finnhub"]}, "source_conflict_threshold": 1.0, "allow_delayed_quotes": True},
        "retry": {"retry_initial_seconds": 1, "retry_max_seconds": 10, "failure_threshold": 2},
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
        "process_lock": {"lock_file_path": str(tmp_path / "monitor.lock"), "stale_lock_timeout_seconds": 1},
    }


def _raw() -> dict:
    return {
        "close": 100.0, "previous_close": 99.0, "status": "ok", "source": "finnhub",
        "quote_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "monitor_quote_status": "REALTIME_VALID",
    }


def test_watch_executes_three_rounds(tmp_path: Path) -> None:
    monitor = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: _raw(), root=tmp_path)
    results = monitor.watch(max_rounds=3, interval_override=0.01, print_table=False)
    assert len(results) == 3
    assert monitor.state_store.table_count("monitor_runs") == 3
    assert monitor.state_store.table_count("snapshots") == 3


def test_interval_override_replaces_configuration(tmp_path: Path) -> None:
    monitor = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: _raw(), root=tmp_path)
    waits: list[float] = []

    def wait(value: float) -> bool:
        waits.append(value)
        return False

    monitor.watch(max_rounds=3, interval_override=2, print_table=False, wait_function=wait)
    assert waits == [2.0, 2.0]


def test_failed_round_does_not_stop_watch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: _raw(), root=tmp_path)
    original = monitor.run_once
    calls = 0

    def flaky(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("round failed")
        return original(**kwargs)

    monkeypatch.setattr(monitor, "run_once", flaky)
    results = monitor.watch(max_rounds=3, interval_override=0.01, print_table=False)
    assert calls == 3
    assert len(results) == 2


def test_normal_exit_releases_process_lock(tmp_path: Path) -> None:
    path = tmp_path / "monitor.lock"
    lock = MonitorProcessLock(path)
    lock.acquire()
    assert path.exists()
    lock.release()
    assert not path.exists()


def test_second_process_is_blocked_by_active_lock(tmp_path: Path) -> None:
    path = tmp_path / "monitor.lock"
    first = MonitorProcessLock(path)
    first.acquire()
    try:
        with pytest.raises(LockHeldError):
            MonitorProcessLock(path).acquire()
        assert path.exists()
    finally:
        first.release()


def test_stale_lock_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "monitor.lock"
    path.write_text(json.dumps({"pid": 99999999, "started_at": "2000-01-01T00:00:00+00:00", "instance_id": "old"}), encoding="utf-8")
    lock = MonitorProcessLock(path)
    lock.acquire()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["instance_id"] == lock.instance_id
    finally:
        lock.release()


def test_active_lock_is_never_deleted_as_stale(tmp_path: Path) -> None:
    path = tmp_path / "monitor.lock"
    path.write_text(json.dumps({"pid": os.getpid(), "started_at": "2000-01-01T00:00:00+00:00", "instance_id": "active"}), encoding="utf-8")
    with pytest.raises(LockHeldError):
        MonitorProcessLock(path, stale_timeout_seconds=0).acquire()
    assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "active"


def test_cli_ctrl_c_exits_cleanly_and_releases_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(IntradayMonitor, "watch", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    exit_code = cli.main(["--watch", "--config", str(config_path)])
    assert exit_code == 0
    assert "stopped gracefully" in capsys.readouterr().out
    assert not (tmp_path / "monitor.lock").exists()


def test_cli_returns_recognizable_code_when_locked(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    lock = MonitorProcessLock(tmp_path / "monitor.lock")
    lock.acquire()
    try:
        assert cli.main(["--once", "--config", str(config_path)]) == LOCK_HELD_EXIT_CODE
    finally:
        lock.release()


def test_no_alert_output_keeps_sqlite_state(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stale = {
        "close": 100.0, "previous_close": 99.0, "status": "ok", "source": "cache:yfinance",
        "cache_used": True, "quote_timestamp": NOW.isoformat(),
    }
    monitor = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: stale, root=tmp_path)
    monitor.run_once(now=NOW, print_table=True, show_alerts=False)
    output = capsys.readouterr().out
    assert "[WARNING]" not in output
    assert monitor.state_store.table_count("alert_state") == 1


def test_jsonl_contains_round_structure(tmp_path: Path) -> None:
    monitor = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: _raw(), root=tmp_path)
    monitor.run_once(print_table=False)
    records = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text(encoding="utf-8").splitlines()]
    completed = next(row for row in records if row["event"] == "round_completed")
    assert {
        "round_id", "started_at", "ended_at", "duration_ms", "success_count",
        "stale_count", "conflict_count", "error_count", "new_alert_count",
        "suppressed_alert_count",
    } <= completed.keys()
