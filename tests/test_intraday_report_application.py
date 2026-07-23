from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.reports.bundle_report import render_daily_report
from tests.test_final_decision_bundle import _fixture_bundle


def test_regular_report_has_intraday_summary_without_mutating_bundle() -> None:
    bundle = _fixture_bundle()
    before = deepcopy(bundle)
    summary = {
        "status": "DATA_UNAVAILABLE",
        "generated_at": "2026-07-22T18:30:00+08:00",
        "timezone": "Asia/Shanghai",
        "futu_connection_status": "UNAVAILABLE",
        "monitored_symbol_count": 0,
        "configured_symbol_count": 8,
        "latest_round_id": "-",
        "round_anomaly_count": 0,
        "items": [],
        "notice": "盘中数据不足，本节仅作观察，不参与主动交易判断。",
    }
    report = render_daily_report(bundle, intraday_summary=summary)
    assert "## 盘中监控摘要" in report
    assert bundle == before
    assert bundle["bundle_hash"] == before["bundle_hash"]


def test_windows_launchers_are_space_safe_and_read_only() -> None:
    root = Path(__file__).parents[1]
    launchers = [
        root / "scripts" / "windows" / "start_intraday_monitor.bat",
        root / "scripts" / "windows" / "run_intraday_once.bat",
        root / "scripts" / "windows" / "start_intraday_monitor_dry_run.bat",
    ]
    for path in launchers:
        source = path.read_text(encoding="utf-8")
        assert 'pushd "%PROJECT_ROOT%"' in source
        assert '"%PYTHON_EXE%"' in source
        assert "127.0.0.1" in source and "11111" in source
        assert "OpenD" in source
        assert "taskkill" not in source.lower()
    assert "--dry-run" in launchers[-1].read_text(encoding="utf-8")


def test_monitoring_source_has_no_trade_context() -> None:
    root = Path(__file__).parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "monitoring").rglob("*.py")
    )
    forbidden = (
        "OpenSecTradeContext",
        "unlock_trade",
        "place_order",
        "modify_order",
        "cancel_order",
    )
    assert not any(token in source for token in forbidden)
