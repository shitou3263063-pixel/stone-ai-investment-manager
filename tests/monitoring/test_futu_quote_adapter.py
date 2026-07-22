from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.monitoring.adapters.futu_quote_adapter import (
    FutuQuoteAdapter,
    FutuQuoteError,
    map_futu_symbol,
)
from src.monitoring.models import SourceQuoteStatus
from src.monitoring.quote_router import MonitoringQuoteRouter
from src.monitoring.state_store import MonitoringStateStore


class _SubType:
    QUOTE = "QUOTE"


SDK = SimpleNamespace(RET_OK=0, SubType=_SubType)


class FakeQuoteContext:
    def __init__(self, rows: dict[str, dict], *, failures: set[str] | None = None) -> None:
        self.rows = rows
        self.failures = failures or set()
        self.subscribed: set[str] = set()
        self.closed = False
        self.unsubscribed = False

    def get_market_snapshot(self, codes: list[str]):
        code = codes[0]
        if code in self.failures:
            return -1, "network unavailable"
        return 0, [self.rows[code]]

    def get_market_state(self, codes: list[str]):
        code = codes[0]
        return 0, [{"code": code, "market_state": self.rows[code]["market_state"]}]

    def subscribe(self, codes, subtypes, **kwargs):
        self.subscribed.update(codes)
        return 0, "ok"

    def get_stock_quote(self, codes: list[str]):
        return 0, [self.rows[codes[0]]]

    def unsubscribe(self, codes, subtypes):
        self.unsubscribed = True
        self.subscribed.difference_update(codes)
        return 0, "ok"

    def close(self):
        self.closed = True


def _row(code: str, *, age_seconds: int = 5, state: str = "AFTERNOON") -> dict:
    zone = ZoneInfo("Asia/Hong_Kong" if code.startswith("HK.") else "America/New_York")
    observed = datetime.now(zone) - timedelta(seconds=age_seconds)
    return {
        "code": code,
        "last_price": 101.25,
        "prev_close_price": 100.0,
        "update_time": observed.strftime("%Y-%m-%d %H:%M:%S"),
        "market_state": state,
        "sec_status": "NORMAL",
    }


def _adapter(context: FakeQuoteContext, **kwargs) -> FutuQuoteAdapter:
    return FutuQuoteAdapter(
        context_factory=lambda **_: context,
        sdk=SDK,
        **kwargs,
    )


def test_opend_not_running_is_unavailable() -> None:
    adapter = FutuQuoteAdapter(
        context_factory=lambda **_: (_ for _ in ()).throw(ConnectionRefusedError("refused")),
        sdk=SDK,
    )
    with pytest.raises(FutuQuoteError, match="UNAVAILABLE"):
        adapter.get_quote("VOO")


def test_unreachable_port_fails_safely() -> None:
    adapter = FutuQuoteAdapter(
        host="127.0.0.1",
        port=1,
        context_factory=lambda **_: (_ for _ in ()).throw(OSError("connection failed")),
        sdk=SDK,
    )
    with pytest.raises(FutuQuoteError, match="UNAVAILABLE"):
        adapter.get_quote("NVDA")


def test_realtime_quote_conversion() -> None:
    context = FakeQuoteContext({"US.VOO": _row("US.VOO")})
    quote = _adapter(context).get_quote("VOO")
    assert quote["provider_symbol"] == "US.VOO"
    assert quote["current_price"] == 101.25
    assert quote["previous_close"] == 100.0
    assert quote["monitor_quote_status"] == SourceQuoteStatus.REALTIME_VALID.value
    assert datetime.fromisoformat(quote["quote_timestamp"]).tzinfo is not None


def test_delayed_quote_conversion() -> None:
    context = FakeQuoteContext({"US.NVDA": _row("US.NVDA", age_seconds=300)})
    quote = _adapter(context, realtime_max_age_seconds=90).get_quote("NVDA")
    assert quote["monitor_quote_status"] == SourceQuoteStatus.DELAYED_VALID.value
    assert quote["is_realtime"] is False


def test_closed_snapshot_is_not_realtime() -> None:
    context = FakeQuoteContext({"HK.03033": _row("HK.03033", age_seconds=3600, state="CLOSED")})
    quote = _adapter(context).get_quote("03033.HK")
    assert quote["monitor_quote_status"] == SourceQuoteStatus.CLOSED_VALID.value
    assert quote["quote_permission"] == "CLOSED_SNAPSHOT"
    assert quote["is_realtime"] is False


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [("VOO", "US.VOO"), ("NVDA", "US.NVDA"), ("03033.HK", "HK.03033")],
)
def test_symbol_mapping(symbol: str, expected: str) -> None:
    assert map_futu_symbol(symbol) == expected


def test_close_releases_quote_context() -> None:
    context = FakeQuoteContext({"US.VOO": _row("US.VOO")})
    adapter = _adapter(context)
    adapter.get_quote("VOO")
    adapter.close()
    assert context.unsubscribed is True
    assert context.closed is True


def test_one_symbol_failure_does_not_poison_next_symbol() -> None:
    rows = {"US.VOO": _row("US.VOO"), "US.NVDA": _row("US.NVDA")}
    context = FakeQuoteContext(rows, failures={"US.VOO"})
    adapter = _adapter(context)
    with pytest.raises(FutuQuoteError):
        adapter.get_quote("VOO")
    assert adapter.get_quote("NVDA")["current_price"] == 101.25


def test_futu_failure_falls_back_to_daily_reference(tmp_path: Path) -> None:
    config = {
        "source_routing": {
            "source_priority": {"US": ["futu", "yfinance"]},
            "source_conflict_threshold": 1.0,
            "allow_delayed_quotes": True,
        },
        "retry": {"retry_initial_seconds": 1, "retry_max_seconds": 10, "failure_threshold": 2},
    }
    providers = {
        "futu": lambda _: (_ for _ in ()).throw(FutuQuoteError("UNAVAILABLE", "OpenD unavailable")),
        "yfinance": lambda _: {
            "status": "ok",
            "close": 100.0,
            "previous_close": 99.0,
            "data_frequency": "daily",
            "quote_timestamp": None,
        },
    }
    router = MonitoringQuoteRouter(
        config, MonitoringStateStore(tmp_path / "state.sqlite3"), providers=providers
    )
    result = router.fetch(
        {"symbol": "VOO", "route_symbol": "VOO", "market": "US"},
        now=datetime.now(ZoneInfo("UTC")),
    )
    assert result["source"] == "yfinance"
    assert result["monitor_quote_status"] == SourceQuoteStatus.DAILY_ONLY.value


def test_adapter_has_no_forbidden_interfaces() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "monitoring"
        / "adapters"
        / "futu_quote_adapter.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "OpenSecTradeContext",
        "unlock_trade",
        "place_order",
        "cancel_order",
        "get_acc_list",
        "get_position_list",
        "get_order_list",
    )
    assert not any(name in source for name in forbidden)
