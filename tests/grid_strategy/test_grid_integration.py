from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest
import yaml

from scripts.run_grid_strategy import parse_args
from src.grid.long_term_v1.engine import LongTermGridEngine
from src.grid.long_term_v1.models import GridStatus, MarketInputs
from src.grid.long_term_v1.notifier import GridAlertNotifier
from src.grid.long_term_v1.report_summary import (
    load_grid_strategy_summary,
    render_grid_strategy_summary,
)
from src.grid.long_term_v1.runtime import LongTermGridRuntime
from src.grid.long_term_v1.state_store import LongTermGridStateStore
from src.notifier import email_notifier
from src.reports.bundle_report import render_daily_report
from tests.test_final_decision_bundle import _fixture_bundle


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)


def _config(tmp_path: Path) -> dict:
    config = deepcopy(
        yaml.safe_load(
            (ROOT / "config" / "long_term_grid.yaml").read_text(encoding="utf-8")
        )
    )
    config["storage"] = {
        "sqlite_path": str(tmp_path / "grid.sqlite3"),
        "lock_path": str(tmp_path / "grid.lock"),
        "log_path": str(tmp_path / "grid.jsonl"),
    }
    return config


def _inputs(symbol: str = "VOO", price: float = 98) -> MarketInputs:
    return MarketInputs(
        symbol=symbol,
        price=price,
        source="futu",
        quote_time=NOW - timedelta(seconds=5),
        quote_status="REALTIME_VALID",
        quote_delay_seconds=5,
        previous_close=100,
        ma20=101,
        market_session="OPEN",
        dqs=90,
        risk_score=20,
        vix=18,
        vix_time=NOW - timedelta(seconds=5),
        usd_cny=7,
    )


def _decision(tmp_path: Path):
    config = _config(tmp_path)
    store = LongTermGridStateStore(config["storage"]["sqlite_path"])
    return LongTermGridEngine(config, store).evaluate(_inputs(), now=NOW), store, config


def test_grid_email_subject_body_and_dry_run_never_loads_smtp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision, _, _ = _decision(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("SMTP configuration must not be read during dry-run")

    monkeypatch.setattr(email_notifier, "_get_email_config", forbidden)
    result = email_notifier.send_grid_alert_email(
        {
            **decision.to_dict(),
            "event_type": "GRID_BUY_CANDIDATE",
            "price": decision.current_price,
            "suggested_amount_cny": decision.adjusted_amount_cny,
        },
        dry_run=True,
    )
    assert result["attempted"] is False
    assert "SIMULATION_ONLY" in result["subject"]
    for field in (
        "event_id=",
        "strategy=LONG_TERM_GRID_V1",
        "automatic_trading=false",
        "simulation_only=true",
        "timezone=",
    ):
        assert field in result["body"]


def test_notification_event_ids_unique_and_sixty_minute_cooldown(
    tmp_path: Path,
) -> None:
    first, store, config = _decision(tmp_path)
    second = LongTermGridEngine(config, store).evaluate(_inputs(), now=NOW)
    assert first.event_id != second.event_id
    sent: list[dict] = []

    def sender(payload: dict, *, dry_run: bool):
        sent.append(payload)
        return {"sent": False, "dry_run": True, "skipped": True}

    config["email"]["enabled"] = True
    notifier = GridAlertNotifier(config, store, sender=sender)
    assert notifier.process(first, now=NOW)["dry_run"] is True
    cooled = notifier.process(second, now=NOW + timedelta(minutes=59))
    assert cooled["skipped"] is True
    assert len(sent) == 1
    notifier.process(second, now=NOW + timedelta(minutes=61))
    assert len(sent) == 2


def test_smtp_failure_does_not_escape_notifier(tmp_path: Path) -> None:
    decision, store, config = _decision(tmp_path)
    config["email"]["enabled"] = True
    config["email"]["dry_run"] = False

    def failed(*_args, **_kwargs):
        raise OSError("network unavailable")

    result = GridAlertNotifier(config, store, sender=failed).process(
        decision, now=NOW
    )
    assert result["sent"] is False
    assert result["error_type"] == "OSError"


def test_report_summary_without_database_does_not_create_one(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "long_term_grid.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
    )
    summary = load_grid_strategy_summary(tmp_path, config_path=config_path)
    assert summary["notice"] == "网格数据不足"
    assert not Path(config["storage"]["sqlite_path"]).exists()


def test_report_summary_reads_simulation_ledger_read_only(tmp_path: Path) -> None:
    decision, store, config = _decision(tmp_path)
    store.record_evaluation(decision)
    before = Path(config["storage"]["sqlite_path"]).read_bytes()
    config_path = tmp_path / "long_term_grid.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
    )
    summary = load_grid_strategy_summary(tmp_path, config_path=config_path)
    assert summary["items"][0]["event_id"] == decision.event_id
    assert Path(config["storage"]["sqlite_path"]).read_bytes() == before
    report = render_grid_strategy_summary(summary)
    assert "## 网格策略观察" in report
    assert "候选仅为模拟观察，不能描述为已成交" in report


def test_runtime_inputs_are_persisted_and_exposed_in_summary(tmp_path: Path) -> None:
    _decision_value, store, config = _decision(tmp_path)
    store.record_runtime_inputs(
        {
            "dqs": {"value": 90, "source": "reports/run_status.json", "as_of": "2026-07-22T14:59:00+00:00", "age_minutes": 1, "validity": "VALID"},
            "risk_score": {"value": 40, "source": "reports/run_status.json", "as_of": "2026-07-22T14:59:00+00:00", "age_minutes": 1, "validity": "VALID"},
            "usd_cny": {"value": 7.2, "source": "data_router", "as_of": "2026-07-22T14:59:00+00:00", "age_minutes": 1, "validity": "VALID"},
        },
        observed_at=NOW,
    )
    config_path = tmp_path / "long_term_grid.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    summary = load_grid_strategy_summary(tmp_path, config_path=config_path)
    assert summary["runtime_inputs"]["usd_cny"]["value"] == 7.2
    assert "data_router" in render_grid_strategy_summary(summary)


def test_daily_report_grid_section_does_not_change_bundle_or_conclusion() -> None:
    bundle = _fixture_bundle()
    original = deepcopy(bundle)
    baseline = render_daily_report(bundle)
    report = render_daily_report(
        bundle,
        grid_strategy_summary={
            "items": [],
            "metrics": {},
            "simulation_only": True,
            "notice": "网格数据不足",
        },
    )
    assert bundle == original
    assert bundle["bundle_hash"] == original["bundle_hash"]
    assert "## 网格策略观察" in report
    assert "网格数据不足" in report
    assert baseline.split("## 今日总决策", 1)[1].split("## 今日场景决策", 1)[0] == (
        report.split("## 今日总决策", 1)[1].split("## 今日场景决策", 1)[0]
    )


class _Provider:
    def __init__(self, values: dict[str, MarketInputs], *, fail: bool = False) -> None:
        self.values = values
        self.fail = fail
        self.closed = False

    def collect(self, symbols: list[str], *, now: datetime):
        if self.fail:
            raise RuntimeError("round failure")
        return {symbol: self.values[symbol] for symbol in symbols}

    def close(self) -> None:
        self.closed = True


class _Logger:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    def write(self, event: str, payload: dict) -> None:
        self.rows.append((event, payload))

    def close(self) -> None:
        pass


def test_runtime_records_evaluation_but_never_auto_fills_candidate(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = LongTermGridStateStore(config["storage"]["sqlite_path"])
    provider = _Provider({"VOO": _inputs()})
    runtime = LongTermGridRuntime(
        config,
        root=ROOT,
        state_store=store,
        data_provider=provider,
        logger=_Logger(),
    )
    decisions = runtime.run_once(["VOO"], now=NOW, print_table=False)
    runtime.close()
    assert decisions[0].status is GridStatus.GRID_BUY_CANDIDATE
    assert store.table_count("evaluations") == 1
    assert store.table_count("grid_lots") == 0
    assert provider.closed is True


def test_watch_counts_failed_rounds_and_stops(tmp_path: Path) -> None:
    config = _config(tmp_path)
    logger = _Logger()
    runtime = LongTermGridRuntime(
        config,
        root=ROOT,
        data_provider=_Provider({}, fail=True),
        logger=logger,
    )
    rounds = runtime.watch(
        ["VOO"], interval_seconds=0.001, max_rounds=3
    )
    runtime.close()
    assert rounds == []
    assert sum(event == "grid_round_failed" for event, _ in logger.rows) == 3


def test_cli_modes_and_supported_symbols() -> None:
    assert parse_args(["--once"]).symbol_list == ["VOO", "QQQ"]
    assert parse_args(["--watch", "--symbols", "qqq", "--interval", "2"]).symbol_list == [
        "QQQ"
    ]
    assert parse_args(["--summary"]).summary is True
    with pytest.raises(SystemExit):
        parse_args(["--once", "--symbols", "NVDA"])


def test_grid_database_is_isolated_and_contains_no_real_account_tables(
    tmp_path: Path,
) -> None:
    _, store, config = _decision(tmp_path)
    with sqlite3.connect(config["storage"]["sqlite_path"]) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "portfolio" not in names
    assert "orders" not in names
    assert "accounts" not in names
    assert {"evaluations", "grid_lots", "ledger_events"} <= names
    assert store.table_count("grid_lots") == 0


def test_source_tree_has_no_trade_context_or_order_calls() -> None:
    paths = [
        *sorted((ROOT / "src" / "grid" / "long_term_v1").glob("*.py")),
        ROOT / "scripts" / "run_grid_strategy.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "TradeOpenContext",
        "OpenSecTradeContext",
        "place_order",
        "placeOrder",
        "cancel_order",
        "cancelOrder",
        "unlock_trade",
        "accinfo_query",
        "position_list_query",
        "order_list_query",
    ):
        assert forbidden not in text


def test_default_config_keeps_email_off_and_automatic_trading_off() -> None:
    config = yaml.safe_load(
        (ROOT / "config" / "long_term_grid.yaml").read_text(encoding="utf-8")
    )
    assert config["mode"] == "SIMULATION_ONLY"
    assert config["automatic_trading"] is False
    assert config["email"]["enabled"] is False
    assert config["email"]["dry_run"] is True
    assert config["transaction_costs"]["commission_bps"] > 0
    assert config["transaction_costs"]["slippage_bps"] > 0
