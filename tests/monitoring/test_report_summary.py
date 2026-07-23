from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src.monitoring.models import DataStatus, MonitorSnapshot
from src.monitoring.report_summary import (
    load_intraday_report_summary,
    render_intraday_report_summary,
)
from src.monitoring.state_store import MonitoringStateStore


NOW = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)


def _config(root: Path) -> Path:
    path = root / "config" / "intraday_monitor.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "symbols": [
                    {"symbol": "NVDA", "asset_name": "NVIDIA", "market": "US"},
                ],
                "application": {
                    "symbols": ["NVDA"],
                    "sqlite_path": "data/monitoring/intraday_monitor.sqlite3",
                    "report_snapshot_max_age_minutes": 30,
                },
                "change_calculation": {
                    "five_minute_tolerance_seconds": 120,
                    "fifteen_minute_tolerance_seconds": 180,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _snapshot(price: float, timestamp: datetime, *, stale: bool = False) -> MonitorSnapshot:
    return MonitorSnapshot(
        symbol="NVDA",
        asset_name="NVIDIA",
        market="US",
        price=price,
        previous_close=100.0,
        change=price - 100.0,
        change_percent=price - 100.0,
        timestamp=timestamp,
        source="futu",
        data_status=DataStatus.STALE if stale else DataStatus.VALID,
        confidence=0.95,
        is_stale=stale,
    )


def test_missing_sqlite_returns_safe_report_section(tmp_path: Path) -> None:
    summary = load_intraday_report_summary(
        tmp_path, config_path=_config(tmp_path), now=NOW
    )
    report = render_intraday_report_summary(summary)
    assert summary["status"] == "DATA_UNAVAILABLE"
    assert "## 盘中监控摘要" in report
    assert "不参与主动交易判断" in report


def test_summary_reads_latest_futu_snapshot_and_real_changes(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    store = MonitoringStateStore(tmp_path / "data" / "monitoring" / "intraday_monitor.sqlite3")
    store.save_snapshot(_snapshot(100.0, NOW - timedelta(minutes=15)), captured_at=NOW - timedelta(minutes=15))
    store.save_snapshot(_snapshot(101.0, NOW - timedelta(minutes=5)), captured_at=NOW - timedelta(minutes=5))
    store.save_snapshot(_snapshot(103.0, NOW), captured_at=NOW)
    store.record_source_success("futu", now=NOW, latency_ms=10)
    store.record_monitor_run(
        {
            "round_id": "round-3",
            "started_at": NOW.isoformat(),
            "ended_at": NOW.isoformat(),
            "duration_ms": 10,
            "success_count": 1,
            "stale_count": 0,
            "conflict_count": 0,
            "error_count": 0,
            "new_alert_count": 0,
            "suppressed_alert_count": 0,
        }
    )
    summary = load_intraday_report_summary(tmp_path, config_path=config_path, now=NOW)
    item = summary["items"][0]
    assert summary["status"] == "VALID"
    assert summary["futu_connection_status"] == "HEALTHY"
    assert summary["latest_round_id"] == "round-3"
    assert round(item["change_5m"], 4) == round((103 / 101 - 1) * 100, 4)
    assert round(item["change_15m"], 4) == 3.0
    assert item["source"] == "futu"


def test_stale_snapshot_never_appears_realtime(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    store = MonitoringStateStore(tmp_path / "data" / "monitoring" / "intraday_monitor.sqlite3")
    store.save_snapshot(
        _snapshot(103.0, NOW - timedelta(hours=2), stale=True),
        captured_at=NOW - timedelta(hours=2),
    )
    summary = load_intraday_report_summary(tmp_path, config_path=config_path, now=NOW)
    assert summary["status"] == "STALE"
    assert summary["items"][0]["validity_status"] == "STALE"
    assert summary["items"][0]["change_5m"] is None
