from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.monitoring.intraday_monitor import IntradayMonitor
from src.monitoring.models import DataStatus, SourceQuoteStatus
from src.monitoring.quote_router import MonitoringQuoteRouter, classify_failure, classify_quote
from src.monitoring.state_store import MonitoringStateStore


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
ASSET = {"symbol": "VOO", "route_symbol": "VOO", "asset_name": "VOO", "market": "US"}


def _config(tmp_path: Path, *, priority: list[str] | None = None) -> dict:
    return {
        "symbols": [ASSET],
        "market_clock": {"holidays": {"US": [], "HK": [], "CN": []}},
        "freshness": {"intraday_max_age_minutes": 15, "future_tolerance_minutes": 2, "closed_market_max_age_hours": 120},
        "source_routing": {
            "source_priority": {"US": priority or ["finnhub"]},
            "source_conflict_threshold": 1.0,
            "allow_delayed_quotes": True,
        },
        "retry": {"retry_initial_seconds": 1, "retry_max_seconds": 10, "failure_threshold": 2},
        "change_calculation": {"five_minute_tolerance_seconds": 120, "fifteen_minute_tolerance_seconds": 180},
        "rules": {
            "price_5m": {"threshold_percent": 1.5, "severity": "WATCH"},
            "price_15m": {"threshold_percent": 2.5, "severity": "WARNING"},
            "daily_change": {"threshold_percent": 4, "severity": "CRITICAL"},
            "data_stale": {"severity": "WARNING"},
            "source_conflict": {"severity": "CRITICAL"},
            "all_sources_failed": {"severity": "CRITICAL"},
        },
        "cooldowns_minutes": {"WATCH": 60, "WARNING": 30, "CRITICAL": 10},
        "runtime": {"max_workers": 2},
        "retention": {"snapshot_retention_days": 7, "alert_retention_days": 30, "source_health_retention_days": 30},
        "storage": {"sqlite_path": str(tmp_path / "monitor.sqlite3")},
        "logging": {"structured_log_path": str(tmp_path / "monitor.jsonl")},
        "intervals": {"trading_interval_seconds": 60, "closed_interval_seconds": 900, "weekend_interval_seconds": 3600},
    }


def _intraday(*, price: float = 100.0, realtime: bool = True) -> dict:
    return {
        "close": price,
        "previous_close": 99.0,
        "status": "ok",
        "quote_timestamp": NOW.isoformat(),
        "published_at": NOW.isoformat(),
        "data_frequency": "quote",
        "is_realtime": realtime,
    }


def test_explicit_realtime_timestamp_is_valid(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    router = MonitoringQuoteRouter(_config(tmp_path), store, providers={"finnhub": lambda _: _intraday()})
    raw = router.fetch(ASSET, now=NOW)
    assert raw["monitor_quote_status"] == SourceQuoteStatus.REALTIME_VALID.value
    monitor = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: raw, root=tmp_path)
    assert monitor.run_once(now=NOW, print_table=False).snapshots[0].data_status is DataStatus.VALID


def test_explicit_delayed_timestamp_is_delayed_valid(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    router = MonitoringQuoteRouter(_config(tmp_path), store, providers={"finnhub": lambda _: _intraday(realtime=False)})
    raw = router.fetch(ASSET, now=NOW)
    assert raw["monitor_quote_status"] == SourceQuoteStatus.DELAYED_VALID.value
    result = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: raw, root=tmp_path).run_once(now=NOW, print_table=False)
    assert result.snapshots[0].data_status is DataStatus.DELAYED_VALID


def test_daily_timestamp_cannot_be_intraday_valid(tmp_path: Path) -> None:
    daily = {
        "close": 100.0, "previous_close": 99.0, "status": "ok", "source": "yfinance",
        "market_date": NOW.date().isoformat(), "data_frequency": "daily", "quote_timestamp": None,
    }
    assert classify_quote("yfinance", daily, now=NOW) is SourceQuoteStatus.DAILY_ONLY
    result = IntradayMonitor(_config(tmp_path), quote_fetcher=lambda _: daily, root=tmp_path).run_once(now=NOW, print_table=False)
    assert result.snapshots[0].data_status is DataStatus.STALE


@pytest.mark.parametrize("message", ["API_KEY missing", "401 Unauthorized", "403 Forbidden"])
def test_auth_failure_classification(message: str) -> None:
    assert classify_failure(RuntimeError(message)) is SourceQuoteStatus.AUTH_ERROR


def test_rate_limit_classification() -> None:
    assert classify_failure(RuntimeError("429 too many requests")) is SourceQuoteStatus.RATE_LIMITED


def test_network_failure_enters_degraded_then_unavailable(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    first = store.record_source_failure(
        "finnhub", now=NOW, error_type="UNAVAILABLE", error_message="network timeout",
        latency_ms=10, retry_initial_seconds=1, retry_max_seconds=10, failure_threshold=2,
    )
    second = store.record_source_failure(
        "finnhub", now=NOW, error_type="UNAVAILABLE", error_message="network timeout",
        latency_ms=10, retry_initial_seconds=1, retry_max_seconds=10, failure_threshold=2,
    )
    assert first["status"] == "DEGRADED"
    assert second["status"] == "UNAVAILABLE"


def test_one_source_failure_does_not_block_fallback_source(tmp_path: Path) -> None:
    config = _config(tmp_path, priority=["finnhub", "yfinance"])
    providers = {
        "finnhub": lambda _: (_ for _ in ()).throw(RuntimeError("network timeout")),
        "yfinance": lambda _: {
            "close": 100.0, "previous_close": 99.0, "status": "ok",
            "data_frequency": "daily", "quote_timestamp": None,
        },
    }
    raw = MonitoringQuoteRouter(config, MonitoringStateStore(tmp_path / "state.sqlite3"), providers=providers).fetch(ASSET, now=NOW)
    assert raw["source"] == "yfinance"
    assert raw["monitor_quote_status"] == SourceQuoteStatus.DAILY_ONLY.value


def test_intraday_source_conflict_is_explicit(tmp_path: Path) -> None:
    config = _config(tmp_path, priority=["finnhub", "alpha_vantage"])
    providers = {
        "finnhub": lambda _: _intraday(price=100.0, realtime=True),
        "alpha_vantage": lambda _: _intraday(price=103.0, realtime=False),
    }
    raw = MonitoringQuoteRouter(config, MonitoringStateStore(tmp_path / "state.sqlite3"), providers=providers).fetch(ASSET, now=NOW)
    assert raw["cross_validation_status"] == "SOURCE_CONFLICT"
