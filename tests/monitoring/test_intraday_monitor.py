from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data_sources import data_router
from src.monitoring.alert_rules import AlertRuleEngine
from src.monitoring.intraday_monitor import IntradayMonitor
from src.monitoring.market_clock import MarketClock, MarketPhase, StaticHolidayCalendar
from src.monitoring.models import AlertSeverity, DataStatus, MonitorSnapshot
from src.monitoring.state_store import MonitoringStateStore


RULES = {
    "price_5m": {"enabled": True, "threshold_percent": 1.5, "severity": "WATCH"},
    "price_15m": {"enabled": True, "threshold_percent": 2.5, "severity": "WARNING"},
    "daily_change": {"enabled": True, "threshold_percent": 4.0, "severity": "CRITICAL"},
    "data_stale": {"enabled": True, "severity": "WARNING"},
    "source_conflict": {"enabled": True, "severity": "CRITICAL"},
    "all_sources_failed": {"enabled": True, "severity": "CRITICAL"},
}
COOLDOWNS = {"WATCH": 60, "WARNING": 30, "CRITICAL": 10}
US_OPEN = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    symbol: str = "VOO",
    price: float | None = 101.0,
    previous_close: float | None = 100.0,
    status: DataStatus = DataStatus.VALID,
    now: datetime = US_OPEN,
) -> MonitorSnapshot:
    change = None if price is None or previous_close is None else price - previous_close
    change_pct = None if price is None or previous_close in {None, 0} else (price / previous_close - 1) * 100
    return MonitorSnapshot(
        symbol=symbol,
        asset_name=symbol,
        market="US",
        price=price,
        previous_close=previous_close,
        change=change,
        change_percent=change_pct,
        timestamp=now,
        source="finnhub",
        data_status=status,
        confidence=0.95 if status is DataStatus.VALID else 0.2,
        is_stale=status is DataStatus.STALE,
    )


def _open_status():
    return MarketClock().status("US", US_OPEN)


def _monitor_config(tmp_path: Path, symbols: list[dict[str, str]]) -> dict:
    return {
        "symbols": symbols,
        "market_clock": {"holidays": {"CN": [], "HK": [], "US": []}},
        "freshness": {
            "intraday_max_age_minutes": 15,
            "future_tolerance_minutes": 2,
            "closed_market_max_age_hours": 120,
        },
        "rules": RULES,
        "cooldowns_minutes": COOLDOWNS,
        "runtime": {"max_workers": 2, "snapshot_retention_hours": 24, "reference_tolerance_minutes": 2},
        "storage": {"sqlite_path": str(tmp_path / "monitor.sqlite3")},
        "logging": {"structured_log_path": str(tmp_path / "monitor.jsonl")},
    }


def _valid_raw(now: datetime = US_OPEN, *, price: float = 101.0, previous: float = 100.0) -> dict:
    return {
        "current_price": price,
        "previous_official_close": previous,
        "quote_timestamp": now.isoformat(),
        "status": "ok",
        "source": "finnhub",
        "data_frequency": "quote",
        "cross_validation_status": "LATEST_DATE_SINGLE_SOURCE",
        "provider_errors": [],
    }


def test_normal_quote_does_not_trigger_alert() -> None:
    alerts = AlertRuleEngine(RULES).evaluate(
        _snapshot(),
        _open_status(),
        observed_at=US_OPEN,
        reference_changes={"5m": 1.0, "15m": -2.0},
    )
    assert alerts == []


def test_thresholds_trigger_correct_rules_and_severity() -> None:
    alerts = AlertRuleEngine(RULES).evaluate(
        _snapshot(price=105.0),
        _open_status(),
        observed_at=US_OPEN,
        reference_changes={"5m": 1.51, "15m": -2.51},
    )
    assert {(alert.rule_id, alert.direction, alert.severity) for alert in alerts} == {
        ("price_5m", "UP", AlertSeverity.WATCH),
        ("price_15m", "DOWN", AlertSeverity.WARNING),
        ("daily_change", "UP", AlertSeverity.CRITICAL),
    }


def test_stale_data_never_triggers_price_alert() -> None:
    alerts = AlertRuleEngine(RULES).evaluate(
        _snapshot(price=120.0, status=DataStatus.STALE),
        _open_status(),
        observed_at=US_OPEN,
        reference_changes={"5m": 20.0, "15m": 20.0},
    )
    assert [alert.rule_id for alert in alerts] == ["data_stale"]


def test_conflict_emits_data_quality_alert() -> None:
    alerts = AlertRuleEngine(RULES).evaluate(
        _snapshot(status=DataStatus.CONFLICT),
        _open_status(),
        observed_at=US_OPEN,
    )
    assert len(alerts) == 1
    assert alerts[0].rule_id == "source_conflict"
    assert alerts[0].severity is AlertSeverity.CRITICAL


def test_cooldown_suppresses_duplicate_alert(tmp_path: Path) -> None:
    store = MonitoringStateStore(tmp_path / "state.sqlite3")
    alert = AlertRuleEngine(RULES).evaluate(
        _snapshot(status=DataStatus.STALE), _open_status(), observed_at=US_OPEN
    )[0]
    assert store.should_emit(alert, now=US_OPEN)
    store.record_alert(alert, now=US_OPEN, cooldown_minutes=COOLDOWNS)
    assert not store.should_emit(alert, now=US_OPEN + timedelta(minutes=29))
    assert store.should_emit(alert, now=US_OPEN + timedelta(minutes=30))


def test_restart_preserves_alert_state(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    first = MonitoringStateStore(path)
    alert = AlertRuleEngine(RULES).evaluate(
        _snapshot(status=DataStatus.CONFLICT), _open_status(), observed_at=US_OPEN
    )[0]
    first.record_alert(alert, now=US_OPEN, cooldown_minutes=COOLDOWNS)
    restarted = MonitoringStateStore(path)
    assert not restarted.should_emit(alert, now=US_OPEN + timedelta(minutes=9))
    assert restarted.alert_state(alert.fingerprint)["last_alert_at"] == US_OPEN.isoformat()


def test_cn_lunch_break() -> None:
    status = MarketClock().status("CN", datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc))
    assert status.phase is MarketPhase.LUNCH_BREAK
    assert status.is_trading is False


def test_hk_lunch_break() -> None:
    status = MarketClock().status("HK", datetime(2026, 7, 20, 4, 30, tzinfo=timezone.utc))
    assert status.phase is MarketPhase.LUNCH_BREAK
    assert status.is_trading is False


def test_us_daylight_saving_open_time() -> None:
    clock = MarketClock()
    winter = clock.status("US", datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc))
    summer = clock.status("US", datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc))
    assert winter.phase is MarketPhase.OPEN
    assert summer.phase is MarketPhase.OPEN
    assert winter.local_time.hour == summer.local_time.hour == 9


def test_static_holiday_interface_closes_market() -> None:
    holiday = US_OPEN.date()
    clock = MarketClock(StaticHolidayCalendar({"US": {holiday}}))
    status = clock.status("US", US_OPEN)
    assert status.phase is MarketPhase.HOLIDAY
    assert status.is_trading is False


def test_non_trading_session_disables_price_rules() -> None:
    closed_at = datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc)
    closed = MarketClock().status("US", closed_at)
    alerts = AlertRuleEngine(RULES).evaluate(
        _snapshot(price=120.0, now=closed_at),
        closed,
        observed_at=closed_at,
        reference_changes={"5m": 20.0, "15m": 20.0},
    )
    assert closed.is_trading is False
    assert alerts == []


def test_sqlite_is_isolated_from_existing_state_files(tmp_path: Path) -> None:
    portfolio = tmp_path / "data" / "portfolio_master.yaml"
    execution = tmp_path / "data" / "execution_state.json"
    grid = tmp_path / "data" / "grid" / "grid_state.json"
    for path, value in ((portfolio, "portfolio"), (execution, "execution"), (grid, "grid")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    before = {path: path.read_bytes() for path in (portfolio, execution, grid)}
    store = MonitoringStateStore(tmp_path / "data" / "monitoring" / "monitor.sqlite3")
    store.save_snapshot(_snapshot(), captured_at=US_OPEN)
    assert {path: path.read_bytes() for path in before} == before
    assert store.path.parent.name == "monitoring"


def test_one_symbol_failure_does_not_affect_other_symbols(tmp_path: Path) -> None:
    symbols = [
        {"symbol": "GOOD", "route_symbol": "GOOD", "asset_name": "Good", "market": "US"},
        {"symbol": "BAD", "route_symbol": "BAD", "asset_name": "Bad", "market": "US"},
    ]

    def fetch(symbol: str) -> dict:
        if symbol == "BAD":
            raise RuntimeError("provider down")
        return _valid_raw()

    result = IntradayMonitor(_monitor_config(tmp_path, symbols), quote_fetcher=fetch, root=tmp_path).run_once(
        now=US_OPEN,
        print_table=False,
    )
    by_symbol = {snapshot.symbol: snapshot for snapshot in result.snapshots}
    assert by_symbol["GOOD"].data_status is DataStatus.VALID
    assert by_symbol["BAD"].data_status is DataStatus.ERROR
    assert any(alert.symbol == "BAD" and alert.rule_id == "all_sources_failed" for alert in result.alerts)


def test_legacy_cache_quote_is_stale_and_cannot_trigger_price_rule(tmp_path: Path) -> None:
    symbols = [{"symbol": "VOO", "route_symbol": "VOO", "asset_name": "VOO", "market": "US"}]
    stale_cache = {
        **_valid_raw(price=120.0),
        "source": "cache:yfinance",
        "cache_used": True,
        "cache_age_days": 7,
    }
    result = IntradayMonitor(
        _monitor_config(tmp_path, symbols),
        quote_fetcher=lambda _: stale_cache,
        root=tmp_path,
    ).run_once(now=US_OPEN, print_table=False)
    assert result.snapshots[0].data_status is DataStatus.STALE
    assert [alert.rule_id for alert in result.alerts] == ["data_stale"]


def test_router_cache_can_be_disabled_for_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_router.SOURCE_REGISTRY, "provider_order", lambda *args, **kwargs: ["yfinance", "local_cache"])
    monkeypatch.setattr(data_router.yfinance_client, "get_quote", lambda _: _valid_raw())
    monkeypatch.setattr(data_router, "write_cache", lambda *args, **kwargs: pytest.fail("cache write attempted"))
    monkeypatch.setattr(data_router, "read_cache", lambda *args, **kwargs: pytest.fail("cache read attempted"))
    item = data_router.get_market_quote(
        "VOO",
        allow_cache=False,
        write_through_cache=False,
        log_events=False,
    )
    assert item["status"] == "ok"
