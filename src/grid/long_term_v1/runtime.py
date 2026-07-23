from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
import time
from typing import Any, Mapping

import yaml

from src.monitoring.adapters.futu_quote_adapter import FutuQuoteAdapter
from src.monitoring.market_clock import MarketClock

from .engine import LongTermGridEngine
from .models import GridDecision, MarketInputs
from .notifier import GridAlertNotifier
from .state_store import LongTermGridStateStore


class GridStructuredLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, payload: Mapping[str, Any]) -> None:
        row = {
            "event": event,
            "logged_at": datetime.now(tz=timezone.utc).isoformat(),
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            handle.flush()

    def close(self) -> None:
        """Writes are flushed immediately."""


class GridRuntimeDataProvider:
    """Read-only Futu quotes plus daily analytical inputs; no report pipeline calls."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        root: Path,
        futu_adapter: FutuQuoteAdapter | None = None,
    ) -> None:
        self.config = dict(config)
        self.root = root
        monitor_config = yaml.safe_load(
            (root / "config" / "intraday_monitor.yaml").read_text(encoding="utf-8")
        ) or {}
        self.futu = futu_adapter or FutuQuoteAdapter.from_config(monitor_config)
        self.clock = MarketClock()

    def collect(self, symbols: list[str], *, now: datetime) -> dict[str, MarketInputs]:
        common = self._bundle_inputs()
        vix, vix_time, vix_error = self._vix()
        output: dict[str, MarketInputs] = {}
        for symbol in symbols:
            anomalies: list[str] = []
            quote: dict[str, Any] = {}
            try:
                quote = self.futu.get_quote(symbol)
            except Exception as exc:  # noqa: BLE001 - symbols are isolated
                anomalies.append(f"FUTU_QUOTE_FAILURE:{type(exc).__name__}")
            ma20, streak, history_error = self._history_inputs(symbol)
            if history_error:
                anomalies.append(history_error)
            if vix_error:
                anomalies.append(vix_error)
            quote_time = _parse_optional_time(quote.get("quote_timestamp"))
            delay = (
                max(0.0, (_utc(now) - _utc(quote_time)).total_seconds())
                if quote_time else None
            )
            security_status = str(quote.get("security_status") or "UNKNOWN").upper()
            if security_status not in {"NORMAL", "UNKNOWN"}:
                anomalies.append(f"SECURITY_STATUS_{security_status}")
            output[symbol] = MarketInputs(
                symbol=symbol,
                price=_float(quote.get("current_price")),
                source=str(quote.get("source") or "unavailable"),
                quote_time=quote_time,
                quote_status=str(
                    quote.get("monitor_quote_status") or "UNAVAILABLE"
                ),
                quote_delay_seconds=delay,
                previous_close=_float(quote.get("previous_close")),
                ma20=ma20,
                market_session=self.clock.status("US", now).phase.value,
                dqs=common.get("dqs"),
                risk_score=common.get("risk_score"),
                vix=vix,
                vix_time=vix_time,
                usd_cny=common.get("usd_cny"),
                data_anomalies=tuple(anomalies),
                consecutive_days_above_ma20=streak,
            )
        return output

    def close(self) -> None:
        self.futu.close()

    def _bundle_inputs(self) -> dict[str, float | None]:
        settings = self.config.get("runtime_inputs") or {}
        path = Path(
            settings.get("final_bundle_path", "reports/final_decision_bundle.json")
        )
        if not path.is_absolute():
            path = self.root / path
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"dqs": None, "risk_score": None, "usd_cny": None}
        dqs_name = str(settings.get("dqs_name", "grid_dqs"))
        dqs = _float((bundle.get("dqs_results", {}).get(dqs_name) or {}).get("total"))
        risk = _float((bundle.get("risk_snapshot") or {}).get("score"))
        fx_symbol = str(settings.get("fx_symbol", "USD/CNY"))
        market = (bundle.get("market_snapshot") or {}).get("market") or {}
        fx = market.get(fx_symbol) or {}
        usd_cny = _float(fx.get("current_price", fx.get("close", fx.get("value"))))
        return {"dqs": dqs, "risk_score": risk, "usd_cny": usd_cny}

    def _history_inputs(self, symbol: str) -> tuple[float | None, int, str]:
        settings = self.config.get("runtime_inputs") or {}
        window = int(settings.get("moving_average_days", 20))
        try:
            import yfinance as yf

            history = yf.Ticker(symbol).history(
                period=str(settings.get("history_period", "2mo")),
                interval="1d",
                auto_adjust=False,
            )
            closes = [float(value) for value in history["Close"].dropna().tolist()]
            if len(closes) < window:
                return None, 0, "MA20_INSUFFICIENT_HISTORY"
            ma20 = sum(closes[-window:]) / window
            streak = 0
            for index in range(len(closes) - 1, window - 2, -1):
                rolling = sum(closes[index - window + 1 : index + 1]) / window
                if closes[index] > rolling:
                    streak += 1
                else:
                    break
            return ma20, streak, ""
        except Exception as exc:  # noqa: BLE001
            return None, 0, f"MA20_FAILURE:{type(exc).__name__}"

    def _vix(self) -> tuple[float | None, datetime | None, str]:
        settings = self.config.get("runtime_inputs") or {}
        try:
            import yfinance as yf

            history = yf.Ticker(str(settings.get("vix_symbol", "^VIX"))).history(
                period="1d",
                interval="1m",
                auto_adjust=False,
            )
            if history.empty:
                return None, None, "VIX_DATA_UNAVAILABLE"
            last = history.iloc[-1]
            timestamp = history.index[-1].to_pydatetime()
            if timestamp.tzinfo is None:
                return None, None, "VIX_TIMESTAMP_MISSING"
            return float(last["Close"]), timestamp, ""
        except Exception as exc:  # noqa: BLE001
            return None, None, f"VIX_FAILURE:{type(exc).__name__}"


class LongTermGridRuntime:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        root: Path,
        state_store: LongTermGridStateStore | None = None,
        data_provider: GridRuntimeDataProvider | Any | None = None,
        logger: GridStructuredLogger | None = None,
        notifier: GridAlertNotifier | None = None,
    ) -> None:
        self.config = dict(config)
        self.root = root
        storage = self.config.get("storage") or {}
        db_path = _resolve(root, storage.get("sqlite_path", "data/grid_strategy/long_term_grid_v1.sqlite3"))
        log_path = _resolve(root, storage.get("log_path", "logs/grid_strategy/long_term_grid_v1.jsonl"))
        self.state_store = state_store or LongTermGridStateStore(db_path)
        self.data_provider = data_provider or GridRuntimeDataProvider(
            self.config, root=root
        )
        self.logger = logger or GridStructuredLogger(log_path)
        self.engine = LongTermGridEngine(self.config, self.state_store)
        self.notifier = notifier or GridAlertNotifier(self.config, self.state_store)
        self._closed = False

    def enable_email_dry_run(self) -> None:
        self.notifier.force_dry_run()

    def run_once(
        self,
        symbols: list[str],
        *,
        now: datetime | None = None,
        print_table: bool = True,
    ) -> list[GridDecision]:
        current = _utc(now or datetime.now(tz=timezone.utc))
        started = time.perf_counter()
        inputs_by_symbol = self.data_provider.collect(symbols, now=current)
        decisions: list[GridDecision] = []
        current_prices: dict[str, float] = {}
        for symbol in symbols:
            inputs = inputs_by_symbol.get(symbol)
            if inputs is None:
                self.logger.write(
                    "grid_symbol_failed",
                    {
                        "symbol": symbol,
                        "error_type": "MISSING_MARKET_INPUT",
                        "simulation_only": True,
                        "automatic_trading": False,
                    },
                )
                continue
            decision = self.engine.evaluate(inputs, now=current)
            self.state_store.record_evaluation(decision)
            notification = self.notifier.process(decision, now=current)
            decisions.append(decision)
            if inputs.price is not None:
                current_prices[symbol] = float(inputs.price)
                self.state_store.ensure_benchmark(
                    symbol,
                    price=float(inputs.price),
                    budget_cny=float(self.config["symbols"][symbol]["budget_cny"]),
                    now=current,
                )
            self.logger.write("grid_evaluation", decision.to_dict())
            self.logger.write(
                "grid_notification",
                {
                    "event_id": decision.event_id,
                    "symbol": decision.symbol,
                    **notification,
                },
            )
        metrics = self.state_store.performance_summary(
            current_prices=current_prices,
            total_budget_cny=float(self.config["budget"]["total_cny"]),
            pause_config=self.config.get("evaluation") or {},
        )
        strategy_value = float(self.config["budget"]["total_cny"]) + float(
            metrics["cumulative_net_profit_cny"]
        )
        benchmark_value = float(self.config["budget"]["total_cny"]) * (
            1.0 + float(metrics["buy_hold_return"])
        )
        self.state_store.record_equity(
            observed_at=current,
            strategy_value_cny=strategy_value,
            benchmark_value_cny=benchmark_value,
            capital_used_cny=self.state_store.used_cash(),
        )
        self.logger.write(
            "grid_round_completed",
            {
                "generated_at": current.isoformat(),
                "symbols": symbols,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "status_counts": _status_counts(decisions),
                "simulation_only": True,
                "automatic_trading": False,
            },
        )
        if print_table:
            print_grid_table(decisions)
        return decisions

    def watch(
        self,
        symbols: list[str],
        *,
        interval_seconds: float,
        stop_event: Event | None = None,
        max_rounds: int | None = None,
    ) -> list[list[GridDecision]]:
        stopper = stop_event or Event()
        rounds: list[list[GridDecision]] = []
        attempts = 0
        while not stopper.is_set():
            attempts += 1
            try:
                rounds.append(self.run_once(symbols))
            except Exception as exc:  # noqa: BLE001
                self.logger.write(
                    "grid_round_failed",
                    {
                        "error_type": type(exc).__name__,
                        "error_summary": str(exc)[:240],
                    },
                )
            if max_rounds is not None and attempts >= max_rounds:
                break
            if stopper.wait(max(0.1, float(interval_seconds))):
                break
        return rounds

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.data_provider.close()
        self.logger.write(
            "grid_runtime_closed",
            {
                "closed_at": datetime.now(tz=timezone.utc).isoformat(),
                "simulation_only": True,
                "automatic_trading": False,
            },
        )
        self.state_store.close()
        self.logger.close()


def load_grid_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError("long-term grid config must be a mapping")
    if str(config.get("mode")) != "SIMULATION_ONLY":
        raise ValueError("long-term grid V1 must remain SIMULATION_ONLY")
    if bool(config.get("automatic_trading")):
        raise ValueError("automatic trading must remain disabled")
    return config


def print_grid_table(decisions: list[GridDecision]) -> None:
    headers = (
        "SYMBOL",
        "STATUS",
        "PRICE",
        "SOURCE",
        "DELAY",
        "CENTER",
        "LEVEL",
        "CNY",
        "SHARES",
        "DQS",
        "RISK",
        "VIX",
    )
    rows = [
        (
            item.symbol,
            item.status.value,
            _fmt(item.current_price, 2),
            item.source,
            _fmt(item.quote_delay_seconds, 0),
            _fmt(item.reference_center, 2),
            "-" if item.grid_level is None else str(item.grid_level),
            _fmt(item.adjusted_amount_cny, 0),
            str(item.estimated_quantity),
            _fmt(item.dqs, 0),
            _fmt(item.risk_score, 0),
            _fmt(item.vix, 2),
        )
        for item in decisions
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print("  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(row[index].ljust(widths[index]) for index in range(len(headers))))
    for item in decisions:
        if item.blocked_reasons:
            print(f"{item.symbol} blocked: {', '.join(item.blocked_reasons)}")
    print("SIMULATION_ONLY | NO_AUTOMATIC_TRADING | position_scope=GRID_POSITION")


def _status_counts(decisions: list[GridDecision]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.status.value] = counts.get(decision.status.value, 0) + 1
    return counts


def _parse_optional_time(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _float(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("grid runtime time must include timezone")
    return value.astimezone(timezone.utc)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _fmt(value: Any, digits: int) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"
