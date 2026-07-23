from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from src.monitoring.adapters.futu_quote_adapter import FutuQuoteAdapter
from src.monitoring.market_clock import MarketClock

from .engine import LongTermGridEngine
from .models import GridDecision, MarketInputs
from .notifier import GridAlertNotifier
from .risk_inputs import fetch_usd_cny
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
        common = self._bundle_inputs(now=now)
        vix, vix_time, vix_error, vix_metadata = self._vix(now=now)
        fx, fx_metadata, fx_errors = self._fx_quote(now=now)
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
            anomalies.extend(str(value) for value in common.get("errors", ()))
            if vix_error:
                anomalies.append(vix_error)
            anomalies.extend(str(value) for value in fx_errors)
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
                usd_cny=fx,
                data_anomalies=tuple(anomalies),
                consecutive_days_above_ma20=streak,
                input_metadata={
                    **(common.get("metadata") or {}),
                    "vix": vix_metadata,
                    "usd_cny": fx_metadata,
                },
            )
        return output

    def close(self) -> None:
        self.futu.close()

    def _bundle_inputs(self, *, now: datetime) -> dict[str, Any]:
        settings = self.config.get("runtime_inputs") or {}
        bundle, source = _load_latest_formal_run(self.root, settings, now=now)
        if bundle is None or source is None:
            source_metadata = (
                _formal_input_metadata(source, now=now)
                if source is not None
                else _unavailable_input("FORMAL_RUN_NOT_FOUND")
            )
            reason = "DQS_INPUT_CONFLICT" if source and source.get("conflict") else "DQS_UNAVAILABLE"
            risk_reason = "RISK_SCORE_INPUT_CONFLICT" if source and source.get("conflict") else "RISK_SCORE_UNAVAILABLE"
            return {
                "dqs": None,
                "risk_score": None,
                "errors": (reason, risk_reason),
                "metadata": {
                    "dqs": {**source_metadata, "validity": source_metadata.get("validity", "MISSING"), "reason": reason},
                    "risk_score": {**source_metadata, "validity": source_metadata.get("validity", "MISSING"), "reason": risk_reason},
                },
            }
        dqs_name = str(settings.get("dqs_name", "grid_dqs"))
        dqs_record = (bundle.get("dqs_results", {}).get(dqs_name) or {})
        if not dqs_record:
            dqs_record = (bundle.get("dqs", {}).get(dqs_name) or {})
        if not dqs_record and dqs_name == "grid_dqs":
            dqs_record = {"total": (bundle.get("dqs", {}).get("grid_dqs"))}
        dqs = _float(dqs_record.get("total"))
        risk = _float((bundle.get("risk_snapshot") or {}).get("score"))
        errors: list[str] = []
        if source.get("conflict"):
            errors.extend(("DQS_INPUT_CONFLICT", "RISK_SCORE_INPUT_CONFLICT"))
        if dqs is None:
            errors.append("DQS_UNAVAILABLE")
        if risk is None:
            errors.append("RISK_SCORE_UNAVAILABLE")
        source_metadata = _formal_input_metadata(source, now=now)
        metadata = {
            "dqs": {**source_metadata, "value": dqs, "validity": "VALID" if dqs is not None else "MISSING"},
            "risk_score": {**source_metadata, "value": risk, "validity": "VALID" if risk is not None else "MISSING"},
        }
        return {"dqs": dqs, "risk_score": risk, "errors": tuple(errors), "metadata": metadata}

    def _fx_quote(self, *, now: datetime) -> tuple[float | None, dict[str, Any], tuple[str, ...]]:
        settings = self.config.get("runtime_inputs") or {}
        return fetch_usd_cny(now=now, settings=settings)

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

    def _vix(self, *, now: datetime) -> tuple[float | None, datetime | None, str, dict[str, Any]]:
        settings = self.config.get("runtime_inputs") or {}
        try:
            from src.data_sources.data_router import _official_vix_quote

            official = _official_vix_quote()
            value = _float(official.get("close", official.get("value")))
            timestamp = _parse_optional_time(
                official.get("published_at")
                or official.get("quote_timestamp")
                or official.get("observed_at")
            )
            if timestamp is None and official.get("published_at"):
                timestamp = _parse_optional_time(
                    f"{official['published_at']}+00:00"
                )
            if value is not None and timestamp is not None:
                age = max(0.0, (_utc(now) - _utc(timestamp)).total_seconds())
                max_age = float((self.config.get("risk_gates") or {}).get("vix_max_age_seconds", 300))
                validity = "VALID" if age <= max_age else "STALE"
                metadata = {
                    "value": value,
                    "source": str(official.get("source") or "cboe_official"),
                    "as_of": timestamp.isoformat(),
                    "age_minutes": round(age / 60.0, 3),
                    "validity": validity,
                }
                if validity == "VALID":
                    return value, timestamp, "", metadata
                return value, timestamp, "VIX_STALE", metadata
        except Exception:
            pass
        try:
            import yfinance as yf

            history = yf.Ticker(str(settings.get("vix_symbol", "^VIX"))).history(
                period="1d",
                interval="1m",
                auto_adjust=False,
            )
            if history.empty:
                return None, None, "VIX_DATA_UNAVAILABLE", _unavailable_input("VIX_DATA_UNAVAILABLE")
            last = history.iloc[-1]
            timestamp = history.index[-1].to_pydatetime()
            if timestamp.tzinfo is None:
                return None, None, "VIX_TIMESTAMP_MISSING", _unavailable_input("VIX_TIMESTAMP_MISSING")
            value = float(last["Close"])
            age = max(0.0, (_utc(now) - _utc(timestamp)).total_seconds())
            max_age = float((self.config.get("risk_gates") or {}).get("vix_max_age_seconds", 300))
            validity = "VALID" if age <= max_age else "STALE"
            metadata = {
                "value": value,
                "source": "yfinance",
                "as_of": timestamp.isoformat(),
                "age_minutes": round(age / 60.0, 3),
                "validity": validity,
            }
            return value, timestamp, "" if validity == "VALID" else "VIX_STALE", metadata
        except Exception as exc:  # noqa: BLE001
            return None, None, f"VIX_FAILURE:{type(exc).__name__}", _unavailable_input(type(exc).__name__)


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
        persisted_inputs: dict[str, Any] | None = None
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
            if persisted_inputs is None:
                persisted_inputs = inputs.input_metadata
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
        if persisted_inputs:
            self.state_store.record_runtime_inputs(persisted_inputs, observed_at=current)
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
        print(
            f"{item.symbol} quote_time={item.quote_time.isoformat() if item.quote_time else '-'} "
            f"quote_delay_seconds={_fmt(item.quote_delay_seconds, 1)}"
        )
        input_metadata = item.metadata.get("input_metadata") or {}
        for name in ("dqs", "risk_score", "usd_cny", "vix"):
            detail = input_metadata.get(name) or {}
            print(
                f"{item.symbol} {name}={detail.get('value', getattr(item, name, None))} "
                f"source={detail.get('source') or '-'} as_of={detail.get('as_of') or '-'} "
                f"age_minutes={detail.get('age_minutes') if detail.get('age_minutes') is not None else '-'} "
                f"validity={detail.get('validity') or '-'}"
            )
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


def _unavailable_input(reason: str) -> dict[str, Any]:
    return {
        "value": None,
        "source": None,
        "as_of": None,
        "report_date": None,
        "run_time": None,
        "timezone": None,
        "age_minutes": None,
        "validity": "MISSING",
        "reason": reason,
        "unavailable_reason": reason,
    }


def _formal_input_metadata(candidate: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    as_of = candidate.get("as_of")
    age_minutes = None
    if isinstance(as_of, datetime):
        age_minutes = round(max(0.0, (_utc(now) - _utc(as_of)).total_seconds()) / 60.0, 3)
        as_of_text = as_of.isoformat()
    else:
        as_of_text = as_of
    return {
        "value": None,
        "source": candidate.get("source"),
        "as_of": as_of_text,
        "report_date": candidate.get("report_date"),
        "run_time": candidate.get("run_time") or as_of_text,
        "timezone": candidate.get("timezone"),
        "age_minutes": age_minutes,
        "validity": candidate.get("validity", "MISSING"),
        "unavailable_reason": candidate.get("unavailable_reason") or candidate.get("reason"),
    }


def _load_latest_formal_run(
    root: Path,
    settings: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load the newest same-day formal run without treating missing data as zero."""
    configured = settings.get("formal_input_paths") or [
        "reports/run_status.json",
        "reports/final_decision_bundle.json",
        "reports/decision.json",
    ]
    paths: list[Path] = []
    for value in configured:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        paths.append(path)
    paths.extend(sorted(root.glob("reports/run_status*.json")))
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in paths:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        candidate = _formal_candidate(path, payload, now=now, settings=settings)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None, None
    valid = [
        item
        for item in candidates
        if item["validity"] == "VALID"
        and _extract_dqs(item["payload"], settings) is not None
        and _extract_risk(item["payload"]) is not None
    ]
    pool = valid or candidates
    pool.sort(key=lambda item: item.get("as_of") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    selected = pool[0]
    # Same-day formal files must agree; disagreement is a hard input conflict.
    same_day = [
        item for item in valid
        if item.get("report_date") == selected.get("report_date")
    ]
    if len(same_day) > 1:
        values = {
            (
                _extract_dqs(item["payload"], settings),
                _extract_risk(item["payload"]),
            )
            for item in same_day
        }
        if len(values) > 1:
            selected = {**selected, "conflict": True}
    if selected["validity"] != "VALID":
        return None, selected
    return selected["payload"], selected


def _formal_candidate(
    path: Path,
    payload: Mapping[str, Any],
    *,
    now: datetime,
    settings: Mapping[str, Any],
) -> dict[str, Any] | None:
    metadata = payload.get("report_metadata") or {}
    timestamp_value = (
        metadata.get("report_generated_at")
        or metadata.get("decision_cutoff_at")
        or payload.get("data_cutoff_at")
        or payload.get("data_cutoff_time")
        or payload.get("generated_at")
        or payload.get("run_time")
    )
    timezone_name = (
        metadata.get("timezone")
        or payload.get("timezone")
        or settings.get("formal_timezone")
        or "Asia/Shanghai"
    )
    as_of = _parse_optional_time(timestamp_value)
    if as_of is None and timestamp_value not in {None, ""}:
        try:
            naive = datetime.fromisoformat(str(timestamp_value).replace("Z", ""))
            as_of = naive.replace(tzinfo=ZoneInfo(str(timezone_name)))
        except (TypeError, ValueError, KeyError):
            as_of = None
    report_date = (
        metadata.get("report_business_date")
        or payload.get("report_date")
        or (as_of.astimezone(ZoneInfo(str(timezone_name))).date().isoformat() if as_of else None)
    )
    validity = "VALID"
    if as_of is None or report_date is None:
        validity = "MISSING"
    else:
        age_minutes = (_utc(now) - _utc(as_of)).total_seconds() / 60.0
        max_age = float(settings.get("formal_max_age_minutes", 24 * 60))
        local_today = _utc(now).astimezone(ZoneInfo(str(timezone_name))).date().isoformat()
        if age_minutes < -5 or age_minutes > max_age or str(report_date) != local_today:
            validity = "STALE"
    validation = payload.get("validation") or {}
    if validation and validation.get("ok") is False:
        validity = "INVALID"
    return {
        "source": str(path),
        "payload": dict(payload),
        "as_of": as_of,
        "report_date": str(report_date) if report_date is not None else None,
        "run_time": timestamp_value,
        "timezone": str(timezone_name),
        "validity": validity,
    }


def _extract_dqs(payload: Mapping[str, Any], settings: Mapping[str, Any]) -> float | None:
    name = str(settings.get("dqs_name", "grid_dqs"))
    record = (payload.get("dqs_results", {}).get(name) or {})
    if not record:
        record = (payload.get("dqs", {}).get(name) or {})
    if not record and name == "grid_dqs":
        record = {"total": (payload.get("dqs", {}).get("grid_dqs"))}
    return _float(record.get("total"))


def _extract_risk(payload: Mapping[str, Any]) -> float | None:
    return _float((payload.get("risk_snapshot") or {}).get("score"))


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
