from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from src.grid.long_term_v1.engine import LongTermGridEngine
from src.grid.long_term_v1.models import GridStatus, MarketInputs
from src.grid.long_term_v1.state_store import LongTermGridStateStore


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)


def _config() -> dict:
    return deepcopy(
        yaml.safe_load(
            (ROOT / "config" / "long_term_grid.yaml").read_text(encoding="utf-8")
        )
    )


def _engine(tmp_path: Path, config: dict | None = None):
    store = LongTermGridStateStore(tmp_path / f"{uuid4().hex}.sqlite3")
    return LongTermGridEngine(config or _config(), store), store


def _inputs(
    symbol: str,
    price: float,
    *,
    now: datetime = NOW,
    previous_close: float = 100,
    ma20: float = 101,
    dqs: float | None = 90,
    risk_score: float | None = 20,
    quote_status: str = "REALTIME_VALID",
    delay: float | None = 10,
    market_session: str = "OPEN",
    vix: float | None = 18,
    vix_age: float = 10,
    anomalies: tuple[str, ...] = (),
    usd_cny: float | None = 7,
    streak: int = 0,
) -> MarketInputs:
    return MarketInputs(
        symbol=symbol,
        price=price,
        source="futu",
        quote_time=now - timedelta(seconds=delay or 0),
        quote_status=quote_status,
        quote_delay_seconds=delay,
        previous_close=previous_close,
        ma20=ma20,
        market_session=market_session,
        dqs=dqs,
        risk_score=risk_score,
        vix=vix,
        vix_time=now - timedelta(seconds=vix_age),
        usd_cny=usd_cny,
        data_anomalies=anomalies,
        consecutive_days_above_ma20=streak,
    )


@pytest.mark.parametrize(
    ("symbol", "level", "drop_pct", "budget_pct"),
    [
        ("VOO", 1, 2, 10),
        ("VOO", 2, 4, 15),
        ("VOO", 3, 6, 15),
        ("VOO", 4, 9, 20),
        ("VOO", 5, 12, 20),
        ("VOO", 6, 16, 20),
        ("QQQ", 1, 3, 10),
        ("QQQ", 2, 6, 15),
        ("QQQ", 3, 9, 15),
        ("QQQ", 4, 13, 20),
        ("QQQ", 5, 18, 20),
        ("QQQ", 6, 24, 20),
    ],
)
def test_all_configured_buy_levels(
    tmp_path: Path, symbol: str, level: int, drop_pct: float, budget_pct: float
) -> None:
    engine, _ = _engine(tmp_path)
    decision = engine.evaluate(
        _inputs(symbol, 100 * (1 - drop_pct / 100)), now=NOW
    )
    assert decision.status is GridStatus.GRID_BUY_CANDIDATE
    assert decision.grid_level == level
    expected_budget = 42000 if symbol == "VOO" else 18000
    assert decision.standard_amount_cny == pytest.approx(
        expected_budget * budget_pct / 100
    )
    assert decision.adjusted_amount_cny == decision.standard_amount_cny
    assert decision.flags == ("SIMULATION_ONLY", "NO_AUTOMATIC_TRADING")
    assert decision.position_scope == "GRID_POSITION"


def test_two_stage_take_profit_uses_only_grid_lot(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    buy = engine.evaluate(_inputs("VOO", 98), now=NOW)
    store.record_simulated_entry(buy)

    first = engine.evaluate(_inputs("VOO", buy.take_profit_1 + 0.01), now=NOW)
    assert first.status is GridStatus.GRID_TAKE_PROFIT_CANDIDATE
    assert first.metadata["take_profit_stage"] == 1
    assert first.metadata["sell_position_scope"] == "GRID_POSITION"
    first_fill = store.record_take_profit(
        buy.event_id,
        stage=1,
        price=float(first.current_price),
        event_time=NOW,
        fees=1,
        slippage=1,
    )
    assert first_fill["remaining_quantity"] > 0

    second = engine.evaluate(_inputs("VOO", buy.take_profit_2 + 0.01), now=NOW)
    assert second.status is GridStatus.GRID_TAKE_PROFIT_CANDIDATE
    assert second.metadata["take_profit_stage"] == 2
    second_fill = store.record_take_profit(
        buy.event_id,
        stage=2,
        price=float(second.current_price),
        event_time=NOW + timedelta(minutes=1),
        fees=1,
        slippage=1,
    )
    assert second_fill["remaining_quantity"] == 0
    assert second_fill["status"] == "COMPLETED"


def test_core_or_dca_position_cannot_enter_grid_ledger(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    decision = engine.evaluate(_inputs("VOO", 98), now=NOW)
    with pytest.raises(ValueError, match="GRID_POSITION"):
        store.record_simulated_entry(
            replace(decision, position_scope="CORE_POSITION")
        )
    with pytest.raises(ValueError, match="GRID_POSITION"):
        store.record_simulated_entry(
            replace(decision, event_id=uuid4().hex, position_scope="DCA_POSITION")
        )


def test_same_level_cannot_repeat_before_profit_cycle_finishes(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    first = engine.evaluate(_inputs("VOO", 98), now=NOW)
    store.record_simulated_entry(first)
    second = engine.evaluate(_inputs("VOO", 98), now=NOW + timedelta(minutes=1))
    assert second.status is GridStatus.NO_ACTION
    assert second.grid_level is None


def test_daily_twenty_percent_limit(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    level_one = engine.evaluate(_inputs("VOO", 98), now=NOW)
    store.record_simulated_entry(level_one)
    later = NOW + timedelta(hours=1)
    level_four = engine.evaluate(_inputs("VOO", 91, now=later), now=later)
    assert level_four.status is GridStatus.GRID_BLOCKED
    assert "DAILY_SYMBOL_BUDGET_LIMIT_20_PERCENT" in level_four.blocked_reasons


def test_rolling_three_day_thirty_five_percent_limit(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    first = engine.evaluate(
        _inputs("VOO", 98, now=NOW - timedelta(days=2)),
        now=NOW - timedelta(days=2),
    )
    store.record_simulated_entry(first)
    second = engine.evaluate(
        _inputs("VOO", 96, now=NOW - timedelta(days=1)),
        now=NOW - timedelta(days=1),
    )
    store.record_simulated_entry(second)
    third = engine.evaluate(_inputs("VOO", 94), now=NOW)
    assert third.status is GridStatus.GRID_BLOCKED
    assert "ROLLING_THREE_DAY_LIMIT_35_PERCENT" in third.blocked_reasons


def test_rolling_limit_uses_three_weekdays_across_weekend(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    thursday = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
    first = engine.evaluate(_inputs("VOO", 98, now=thursday), now=thursday)
    store.record_simulated_entry(first)
    friday = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)
    second = engine.evaluate(_inputs("VOO", 96, now=friday), now=friday)
    store.record_simulated_entry(second)
    monday = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)
    third = engine.evaluate(_inputs("VOO", 88, now=monday), now=monday)
    assert third.status is GridStatus.GRID_BLOCKED
    assert "ROLLING_THREE_DAY_LIMIT_35_PERCENT" in third.blocked_reasons


def test_total_use_and_cash_reserve_are_enforced(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    for level, price, when in (
        (1, 98, NOW - timedelta(days=20)),
        (2, 96, NOW - timedelta(days=16)),
        (3, 94, NOW - timedelta(days=12)),
        (4, 91, NOW - timedelta(days=8)),
        (5, 88, NOW - timedelta(days=4)),
    ):
        decision = engine.evaluate(
            _inputs("VOO", price, now=when), now=when
        )
        assert decision.grid_level == level
        store.record_simulated_entry(decision)
    qqq = engine.evaluate(_inputs("QQQ", 94), now=NOW)
    assert qqq.status is GridStatus.GRID_BLOCKED
    assert "TOTAL_GRID_USAGE_LIMIT_60_PERCENT" in qqq.blocked_reasons
    assert "MINIMUM_GRID_CASH_RESERVE_40_PERCENT" in qqq.blocked_reasons


@pytest.mark.parametrize(
    ("vix", "price", "expected_status", "multiplier"),
    [
        (14, 98, GridStatus.GRID_BUY_CANDIDATE, 0.7),
        (18, 98, GridStatus.GRID_BUY_CANDIDATE, 1.0),
        (25, 98, GridStatus.GRID_BUY_CANDIDATE, 0.5),
        (35, 98, GridStatus.GRID_BLOCKED, 0.0),
        (35, 94, GridStatus.ALLOW_EVALUATION_ONLY, 1.0),
        (40, 94, GridStatus.GRID_BLOCKED, None),
    ],
)
def test_vix_adjustment_bands(
    tmp_path: Path,
    vix: float,
    price: float,
    expected_status: GridStatus,
    multiplier: float | None,
) -> None:
    engine, _ = _engine(tmp_path)
    decision = engine.evaluate(_inputs("VOO", price, vix=vix), now=NOW)
    assert decision.status is expected_status
    if multiplier is not None:
        assert decision.metadata.get("vix_multiplier") == multiplier


@pytest.mark.parametrize(
    ("overrides", "reason_fragment"),
    [
        ({"dqs": 84}, "DQS_BELOW_85"),
        ({"risk_score": 51}, "RISK_SCORE_ABOVE_50"),
        ({"quote_status": "STALE"}, "QUOTE_STATUS_STALE"),
        ({"delay": 301}, "QUOTE_DELAY_EXCEEDS_90_SECONDS"),
        ({"market_session": "CLOSED"}, "US_MARKET_NOT_IN_REGULAR_SESSION"),
        ({"vix": None}, "VIX_MISSING"),
        ({"vix_age": 301}, "VIX_STALE"),
        ({"anomalies": ("SECURITY_STATUS_SUSPENDED",)}, "SECURITY_STATUS_SUSPENDED"),
    ],
)
def test_risk_gates_collect_blocking_reasons(
    tmp_path: Path, overrides: dict, reason_fragment: str
) -> None:
    engine, _ = _engine(tmp_path)
    decision = engine.evaluate(_inputs("VOO", 98, **overrides), now=NOW)
    assert decision.status is GridStatus.GRID_BLOCKED
    assert any(reason_fragment in reason for reason in decision.blocked_reasons)


def test_all_risk_failures_are_reported_together(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    decision = engine.evaluate(
        _inputs(
            "VOO",
            98,
            dqs=20,
            risk_score=90,
            quote_status="STALE",
            delay=500,
            market_session="CLOSED",
            vix=45,
            anomalies=("QUOTE_CONFLICT",),
        ),
        now=NOW,
    )
    assert len(decision.blocked_reasons) >= 7
    assert "QUOTE_CONFLICT" in decision.blocked_reasons


def test_integer_share_and_insufficient_cash(tmp_path: Path) -> None:
    config = _config()
    config["symbols"]["VOO"]["budget_cny"] = 100
    config["budget"]["total_cny"] = 100
    engine, _ = _engine(tmp_path, config)
    decision = engine.evaluate(_inputs("VOO", 98), now=NOW)
    assert decision.estimated_quantity == 0
    assert decision.status is GridStatus.GRID_BLOCKED
    assert "INSUFFICIENT_CASH_FOR_ONE_WHOLE_SHARE" in decision.blocked_reasons


def test_commission_and_slippage_are_nonzero_and_configured(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    decision = engine.evaluate(_inputs("VOO", 98), now=NOW)
    assert decision.estimated_fees_cny > 0
    assert decision.estimated_slippage_cny > 0
    assert decision.estimated_quantity == int(decision.estimated_quantity)


def test_reference_center_is_persistent_and_requires_manual_confirmation(
    tmp_path: Path,
) -> None:
    engine, store = _engine(tmp_path)
    first = engine.evaluate(_inputs("VOO", 100), now=NOW)
    assert first.reference_center == 100
    raised = engine.evaluate(
        _inputs(
            "VOO",
            110,
            previous_close=110,
            ma20=108,
            streak=5,
            now=NOW + timedelta(days=6),
        ),
        now=NOW + timedelta(days=6),
    )
    assert raised.reference_center == 100
    candidate_id = raised.metadata["reference_center_candidate_id"]
    assert candidate_id is not None
    assert store.active_center("VOO")["center"] == 100
    store.confirm_center(candidate_id, now=NOW + timedelta(days=6, minutes=1))
    assert store.active_center("VOO")["center"] == 108
    lowered = engine.evaluate(
        _inputs(
            "VOO",
            80,
            previous_close=80,
            ma20=85,
            now=NOW + timedelta(days=40),
        ),
        now=NOW + timedelta(days=40),
    )
    assert lowered.reference_center == 108


def test_reference_center_has_one_upward_adjustment_candidate_per_month(
    tmp_path: Path,
) -> None:
    engine, store = _engine(tmp_path)
    first = engine.evaluate(_inputs("VOO", 100), now=NOW)
    assert first.reference_center == 100
    first_candidate = engine.evaluate(
        _inputs("VOO", 110, previous_close=110, ma20=108),
        now=NOW + timedelta(days=1),
    )
    assert first_candidate.metadata["reference_center_candidate_id"] is not None
    second_candidate = engine.evaluate(
        _inputs("VOO", 115, previous_close=115, ma20=112),
        now=NOW + timedelta(days=2),
    )
    assert second_candidate.metadata["reference_center_candidate_id"] is None
    assert len(
        [row for row in store.center_history("VOO") if row["status"] == "CANDIDATE"]
    ) == 1


def test_sqlite_state_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "persistent.sqlite3"
    first_store = LongTermGridStateStore(path)
    first_engine = LongTermGridEngine(_config(), first_store)
    decision = first_engine.evaluate(_inputs("VOO", 98), now=NOW)
    first_store.record_evaluation(decision)
    first_store.record_simulated_entry(decision)
    first_store.close()

    second_store = LongTermGridStateStore(path)
    assert second_store.active_lot("VOO", 1)["event_id"] == decision.event_id
    assert second_store.latest_evaluations()[0]["event_id"] == decision.event_id
    assert second_store.table_count("ledger_events") == 1


def test_buy_hold_comparison_metrics(tmp_path: Path) -> None:
    engine, store = _engine(tmp_path)
    decision = engine.evaluate(_inputs("VOO", 98), now=NOW)
    store.record_simulated_entry(decision)
    store.ensure_benchmark("VOO", price=98, budget_cny=42000, now=NOW)
    store.ensure_benchmark("QQQ", price=100, budget_cny=18000, now=NOW)
    metrics = store.performance_summary(
        current_prices={"VOO": 102, "QQQ": 110},
        total_budget_cny=60000,
        pause_config=_config()["evaluation"],
    )
    assert metrics["buy_hold_return"] > 0
    assert "strategy_return" in metrics
    assert metrics["excess_return_vs_buy_hold"] == pytest.approx(
        metrics["strategy_return"] - metrics["buy_hold_return"], abs=1e-6
    )
