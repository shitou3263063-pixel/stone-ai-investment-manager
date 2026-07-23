from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

import yaml

from src.data_sources.data_router import get_market_quote
from utils.data_loader import project_root

from .alert_rules import AlertRuleEngine
from .alert_notifier import IntradayAlertNotifier
from .market_clock import MarketClock, MarketPhase, MarketStatus, StaticHolidayCalendar
from .models import (
    Alert,
    ChangeResult,
    DataStatus,
    MonitorSnapshot,
    RecoveryEvent,
    SourceQuoteStatus,
)
from .quote_router import MonitoringQuoteRouter
from .state_store import MonitoringStateStore


QuoteFetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class MonitorRunResult:
    snapshots: tuple[MonitorSnapshot, ...]
    alerts: tuple[Alert, ...]
    suppressed_alerts: tuple[Alert, ...]
    recovery_events: tuple[RecoveryEvent, ...]
    market_statuses: Mapping[str, MarketStatus]
    change_results: Mapping[str, Mapping[str, ChangeResult]]
    metrics: Mapping[str, Any]


class StructuredMonitorLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, payload: Mapping[str, Any]) -> None:
        record = {
            "event": event,
            "logged_at": datetime.now(tz=timezone.utc).isoformat(),
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()

    def close(self) -> None:
        """Writes are flushed per record; retained for explicit runtime shutdown."""


class IntradayMonitor:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        quote_fetcher: QuoteFetcher | None = None,
        state_store: MonitoringStateStore | None = None,
        market_clock: MarketClock | None = None,
        logger: StructuredMonitorLogger | None = None,
        alert_notifier: IntradayAlertNotifier | None = None,
        root: Path | None = None,
    ) -> None:
        self.config = dict(config)
        self.root = root or project_root()
        self.assets = tuple(dict(row) for row in self.config.get("symbols", []))
        self.quote_fetcher = quote_fetcher
        holiday_config = (self.config.get("market_clock") or {}).get("holidays") or {}
        self.market_clock = market_clock or MarketClock(StaticHolidayCalendar.from_config(holiday_config))
        runtime = self.config.get("runtime") or {}
        retention = self.config.get("retention") or {}
        storage = self.config.get("storage") or {}
        state_path = _resolve_path(self.root, storage.get("sqlite_path", "data/monitoring/intraday_monitor.sqlite3"))
        self.state_store = state_store or MonitoringStateStore(
            state_path,
            snapshot_retention_days=int(retention.get("snapshot_retention_days", 7)),
            alert_retention_days=int(retention.get("alert_retention_days", 30)),
            source_health_retention_days=int(retention.get("source_health_retention_days", 30)),
        )
        logging_config = self.config.get("logging") or {}
        log_path = _resolve_path(self.root, logging_config.get("structured_log_path", "logs/intraday_monitor.jsonl"))
        self.logger = logger or StructuredMonitorLogger(log_path)
        self.rule_engine = AlertRuleEngine(self.config.get("rules") or {})
        self.cooldowns = {
            key.upper(): int(value)
            for key, value in (self.config.get("cooldowns_minutes") or {}).items()
        }
        self.max_workers = max(1, int(runtime.get("max_workers", 4)))
        calculation = self.config.get("change_calculation") or {}
        self.five_tolerance_seconds = int(calculation.get("five_minute_tolerance_seconds", 120))
        self.fifteen_tolerance_seconds = int(calculation.get("fifteen_minute_tolerance_seconds", 180))
        self.allow_delayed = bool((self.config.get("source_routing") or {}).get("allow_delayed_quotes", True))
        self.quote_router = None if quote_fetcher else MonitoringQuoteRouter(self.config, self.state_store)
        self.alert_notifier = alert_notifier or IntradayAlertNotifier(self.config, self.state_store)
        self._lifetime_metrics = {
            "rounds": 0,
            "symbols": 0,
            "valid": 0,
            "stale": 0,
            "failed": 0,
            "alerts_triggered": 0,
            "alerts_suppressed": 0,
        }
        self._closed = False

    def filter_symbols(self, symbols: set[str]) -> None:
        wanted = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        aliases = {
            alias: str(asset["symbol"]).upper()
            for asset in self.assets
            for alias in _symbol_aliases(str(asset["symbol"]))
        }
        missing = wanted - set(aliases)
        if missing:
            raise ValueError(f"unknown monitor symbols: {','.join(sorted(missing))}")
        selected = {aliases[symbol] for symbol in wanted}
        self.assets = tuple(asset for asset in self.assets if str(asset["symbol"]).upper() in selected)

    def enable_email_dry_run(self) -> None:
        self.alert_notifier.force_dry_run()

    def run_once(
        self,
        *,
        now: datetime | None = None,
        print_table: bool = True,
        show_alerts: bool = True,
        round_id: str | None = None,
    ) -> MonitorRunResult:
        started_clock = time.perf_counter()
        started_at = now or datetime.now(tz=timezone.utc)
        if started_at.tzinfo is None:
            raise ValueError("monitor run time must include a timezone")
        round_key = round_id or uuid4().hex
        market_statuses = {
            market: self.market_clock.status(market, started_at)
            for market in sorted({str(asset["market"]).upper() for asset in self.assets})
        }
        self.logger.write("round_started", {
            "round_id": round_key,
            "started_at": started_at.isoformat(),
            "symbol_count": len(self.assets),
        })
        raw_quotes = self._fetch_all(started_at)
        snapshots: list[MonitorSnapshot] = []
        emitted: list[Alert] = []
        suppressed: list[Alert] = []
        recoveries: list[RecoveryEvent] = []
        changes_by_symbol: dict[str, dict[str, ChangeResult]] = {}

        for asset in self.assets:
            symbol = str(asset["symbol"])
            market = str(asset["market"]).upper()
            raw = raw_quotes.get(symbol) or _failure_payload("quote result missing")
            snapshot = _normalize_snapshot(
                asset,
                raw,
                market_statuses[market],
                started_at,
                self.config,
            )
            five = self.state_store.calculate_change(
                snapshot,
                captured_at=started_at,
                lookback_minutes=5,
                tolerance_seconds=self.five_tolerance_seconds,
                allow_delayed=self.allow_delayed,
            )
            fifteen = self.state_store.calculate_change(
                snapshot,
                captured_at=started_at,
                lookback_minutes=15,
                tolerance_seconds=self.fifteen_tolerance_seconds,
                allow_delayed=self.allow_delayed,
            )
            changes_by_symbol[symbol] = {"5m": five, "15m": fifteen}
            candidates = self.rule_engine.evaluate(
                snapshot,
                market_statuses[market],
                observed_at=started_at,
                reference_changes={"5m": five.change_percent, "15m": fifteen.change_percent},
            )
            active_fingerprints = {alert.fingerprint for alert in candidates}
            for alert in candidates:
                should_emit = self.state_store.should_emit(alert, now=started_at)
                self.state_store.record_alert(
                    alert,
                    now=started_at,
                    cooldown_minutes=self.cooldowns,
                    emitted=should_emit,
                )
                if should_emit:
                    emitted.append(alert)
                    self.logger.write("alert_emitted", {"round_id": round_key, **alert.to_dict()})
                else:
                    suppressed.append(alert)
                    self.logger.write("alert_suppressed", {"round_id": round_key, **alert.to_dict()})
            restored = self.state_store.recover_inactive_alerts(
                symbol, active_fingerprints, now=started_at
            )
            for event in restored:
                self.logger.write("recovery_event", {"round_id": round_key, **event.to_dict()})
            recoveries.extend(restored)
            for payload in raw.get("health_recoveries") or []:
                event = RecoveryEvent(
                    fingerprint=str(payload["fingerprint"]),
                    event_type=str(payload["event_type"]),
                    subject=str(payload["subject"]),
                    recovered_rule=str(payload["recovered_rule"]),
                    observed_at=_parse_optional_time(payload["observed_at"]) or started_at,
                    message=str(payload["message"]),
                )
                recoveries.append(event)
                self.logger.write("source_recovered", {"round_id": round_key, **event.to_dict()})
            self.state_store.save_snapshot(snapshot, captured_at=started_at)
            snapshots.append(snapshot)
            self.logger.write("snapshot", {
                "round_id": round_key,
                **snapshot.to_dict(),
                "market_phase": market_statuses[market].phase.value,
                "change_5m": five.to_dict(),
                "change_15m": fifteen.to_dict(),
                "source_results": raw.get("source_results") or [],
            })

        ended_at = datetime.now(tz=timezone.utc) if now is None else started_at
        duration_ms = round((time.perf_counter() - started_clock) * 1000.0, 2)
        metrics = {
            "round_id": round_key,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_ms": duration_ms,
            "success_count": sum(s.data_status in {DataStatus.VALID, DataStatus.DELAYED_VALID} for s in snapshots),
            "stale_count": sum(s.data_status is DataStatus.STALE for s in snapshots),
            "conflict_count": sum(s.data_status is DataStatus.CONFLICT for s in snapshots),
            "error_count": sum(s.data_status in {DataStatus.ERROR, DataStatus.MISSING} for s in snapshots),
            "new_alert_count": len(emitted),
            "suppressed_alert_count": len(suppressed),
            "symbol_count": len(snapshots),
            "valid_count": sum(s.data_status in {DataStatus.VALID, DataStatus.DELAYED_VALID} for s in snapshots),
            "failed_count": sum(
                s.data_status in {DataStatus.ERROR, DataStatus.MISSING, DataStatus.CONFLICT}
                for s in snapshots
            ),
            "alerts_triggered": 0,
            "alerts_suppressed": 0,
            "futu_connection_status": str(
                (self.state_store.source_health("futu") or {}).get("status") or "UNAVAILABLE"
            ),
        }
        result = MonitorRunResult(
            snapshots=tuple(snapshots),
            alerts=tuple(emitted),
            suppressed_alerts=tuple(suppressed),
            recovery_events=tuple(recoveries),
            market_statuses=market_statuses,
            change_results=changes_by_symbol,
            metrics=metrics,
        )
        try:
            notification = self.alert_notifier.process(result, now=started_at)
            metrics["alerts_triggered"] = int(notification["triggered"])
            metrics["alerts_suppressed"] = int(notification["suppressed"])
            for delivery in notification["deliveries"]:
                self.logger.write("alert_email", {"round_id": round_key, **delivery})
        except Exception as exc:  # noqa: BLE001 - email application layer cannot stop monitoring
            self.logger.write(
                "alert_email_failed",
                {
                    "round_id": round_key,
                    "error_type": type(exc).__name__,
                    "error_summary": str(exc)[:240],
                },
            )
        self.state_store.record_monitor_run(metrics)
        self.state_store.cleanup(now=started_at)
        self.logger.write("round_completed", metrics)
        self._lifetime_metrics["rounds"] += 1
        self._lifetime_metrics["symbols"] += int(metrics["symbol_count"])
        self._lifetime_metrics["valid"] += int(metrics["valid_count"])
        self._lifetime_metrics["stale"] += int(metrics["stale_count"])
        self._lifetime_metrics["failed"] += int(metrics["failed_count"])
        self._lifetime_metrics["alerts_triggered"] += int(metrics["alerts_triggered"])
        self._lifetime_metrics["alerts_suppressed"] += int(metrics["alerts_suppressed"])
        if print_table:
            print_console_table(result, show_alerts=show_alerts)
        return result

    def watch(
        self,
        *,
        interval_override: float | None = None,
        stop_event: Event | None = None,
        max_rounds: int | None = None,
        print_table: bool = True,
        show_alerts: bool = True,
        wait_function: Callable[[float], bool] | None = None,
    ) -> list[MonitorRunResult]:
        stopper = stop_event or Event()
        results: list[MonitorRunResult] = []
        rounds = 0
        while not stopper.is_set():
            try:
                result = self.run_once(print_table=print_table, show_alerts=show_alerts)
                results.append(result)
            except Exception as exc:  # noqa: BLE001 - a failed round must not kill watch mode
                self.logger.write("round_failed", {
                    "round_id": uuid4().hex,
                    "error_type": type(exc).__name__,
                    "error_summary": str(exc)[:240],
                })
                result = None
            rounds += 1
            if max_rounds is not None and rounds >= max_rounds:
                break
            interval = float(interval_override) if interval_override is not None else self._configured_interval(result)
            if interval <= 0:
                interval = 0.1
            if wait_function:
                if wait_function(interval):
                    break
            elif stopper.wait(interval):
                break
        return results

    def _configured_interval(self, result: MonitorRunResult | None) -> float:
        intervals = self.config.get("intervals") or {}
        if result and any(status.is_trading for status in result.market_statuses.values()):
            return float(intervals.get("trading_interval_seconds", 60))
        if result and result.market_statuses and all(
            status.phase is MarketPhase.WEEKEND for status in result.market_statuses.values()
        ):
            return float(intervals.get("weekend_interval_seconds", 3600))
        return float(intervals.get("closed_interval_seconds", 900))

    def _fetch_all(self, now: datetime) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(self.assets)))) as executor:
            futures = {}
            for asset in self.assets:
                if self.quote_fetcher:
                    future = executor.submit(
                        self.quote_fetcher, str(asset.get("route_symbol") or asset["symbol"])
                    )
                else:
                    future = executor.submit(self.quote_router.fetch, asset, now=now)  # type: ignore[union-attr]
                futures[future] = str(asset["symbol"])
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results[symbol] = dict(future.result())
                except Exception as exc:  # noqa: BLE001
                    results[symbol] = _failure_payload(str(exc))
        return results

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.quote_router is not None:
            self.quote_router.close()
        self.state_store.close()
        self.logger.write(
            "monitor_closed",
            {
                "closed_at": datetime.now(tz=timezone.utc).isoformat(),
                **self._lifetime_metrics,
            },
        )
        self.logger.close()


def load_monitor_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else project_root() / "config" / "intraday_monitor.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("intraday monitor config must be a mapping")
    return payload


def _fetch_without_legacy_cache(symbol: str) -> dict[str, Any]:
    return get_market_quote(symbol, allow_cache=False, write_through_cache=False, log_events=False)


def _normalize_snapshot(
    asset: Mapping[str, Any],
    route_payload: Mapping[str, Any],
    market_status: MarketStatus,
    captured_at: datetime,
    config: Mapping[str, Any],
) -> MonitorSnapshot:
    source = str(route_payload.get("source") or "unavailable")
    price = _float(route_payload.get("current_price", route_payload.get("close", route_payload.get("value"))))
    previous_close = _float(
        route_payload.get("previous_official_close", route_payload.get("previous_close", route_payload.get("previous_value")))
    )
    timestamp = _parse_optional_time(
        route_payload.get("quote_timestamp")
        or route_payload.get("observed_at_utc")
        or route_payload.get("source_observation_time")
    )
    cross_status = str(route_payload.get("cross_validation_status") or "")
    provider_errors = list(route_payload.get("provider_errors") or [])
    raw_status = str(route_payload.get("status") or "missing").lower()
    quote_status = str(route_payload.get("monitor_quote_status") or "")
    cache_used = source.startswith("cache:") or bool(route_payload.get("cache_used"))
    stale = bool(
        cache_used or route_payload.get("is_stale") or route_payload.get("stale")
        or route_payload.get("cache_stale")
        or str(route_payload.get("freshness_status") or "").lower() == "stale"
    )
    freshness = config.get("freshness") or {}
    allow_delayed = bool((config.get("source_routing") or {}).get("allow_delayed_quotes", True))

    if "CONFLICT" in cross_status.upper():
        data_status = DataStatus.CONFLICT
    elif price is None or raw_status not in {"ok", "success", "cached"}:
        data_status = DataStatus.ERROR if provider_errors or route_payload.get("error") else DataStatus.MISSING
    elif quote_status == SourceQuoteStatus.DELAYED_VALID.value and allow_delayed:
        stale = stale or _timestamp_is_stale(timestamp, captured_at, market_status, freshness)
        data_status = DataStatus.STALE if stale else DataStatus.DELAYED_VALID
    elif quote_status == SourceQuoteStatus.REALTIME_VALID.value:
        stale = stale or _timestamp_is_stale(timestamp, captured_at, market_status, freshness)
        data_status = DataStatus.STALE if stale else DataStatus.VALID
    elif quote_status == SourceQuoteStatus.CLOSED_VALID.value:
        stale = stale or market_status.is_trading or timestamp is None
        data_status = DataStatus.STALE if stale else DataStatus.VALID
    elif quote_status == SourceQuoteStatus.DAILY_ONLY.value:
        stale = stale or market_status.is_trading or timestamp is None
        data_status = DataStatus.STALE if stale else DataStatus.VALID
    else:
        stale = stale or _legacy_timestamp_is_stale(route_payload, timestamp, captured_at, market_status, freshness)
        data_status = DataStatus.STALE if stale else DataStatus.VALID

    change = None if price is None or previous_close is None else price - previous_close
    change_percent = None if price is None or previous_close in {None, 0} else (price / float(previous_close) - 1.0) * 100.0
    confidence = _confidence(route_payload, source, data_status)
    return MonitorSnapshot(
        symbol=str(asset["symbol"]),
        asset_name=str(asset.get("asset_name") or asset["symbol"]),
        market=str(asset["market"]).upper(),
        price=price,
        previous_close=previous_close,
        change=change,
        change_percent=change_percent,
        timestamp=timestamp,
        source=source,
        data_status=data_status,
        confidence=confidence,
        is_stale=data_status is DataStatus.STALE,
    )


def _timestamp_is_stale(
    timestamp: datetime | None,
    captured_at: datetime,
    market_status: MarketStatus,
    freshness: Mapping[str, Any],
) -> bool:
    if timestamp is None:
        return True
    age_minutes = (captured_at.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() / 60.0
    if age_minutes < -float(freshness.get("future_tolerance_minutes", 2)):
        return True
    limit = (
        float(freshness.get("intraday_max_age_minutes", 15))
        if market_status.is_trading else float(freshness.get("closed_market_max_age_hours", 120)) * 60.0
    )
    return age_minutes > limit


def _legacy_timestamp_is_stale(
    payload: Mapping[str, Any],
    timestamp: datetime | None,
    captured_at: datetime,
    market_status: MarketStatus,
    freshness: Mapping[str, Any],
) -> bool:
    if market_status.is_trading and not _has_provider_quote_timestamp(payload):
        return True
    return _timestamp_is_stale(timestamp, captured_at, market_status, freshness)


def _has_provider_quote_timestamp(payload: Mapping[str, Any]) -> bool:
    source = str(payload.get("source") or "").replace("cache:", "")
    frequency = str(payload.get("data_frequency") or "").lower()
    timestamp = _parse_optional_time(payload.get("quote_timestamp") or payload.get("observed_at_utc"))
    return timestamp is not None and not (source in {"yfinance", "alpha_vantage"} and frequency in {"daily", "1d"})


def _confidence(payload: Mapping[str, Any], source: str, status: DataStatus) -> float:
    if status in {DataStatus.ERROR, DataStatus.MISSING}:
        return 0.0
    if status is DataStatus.CONFLICT:
        return 0.2
    if status is DataStatus.STALE or source.startswith("cache:"):
        return 0.3
    if status is DataStatus.DELAYED_VALID:
        return 0.8
    cross_status = str(payload.get("cross_validation_status") or "").upper()
    return 0.95 if bool(payload.get("verified_by_second_source")) or cross_status == "VERIFIED" else 0.65


def _parse_optional_time(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _float(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _failure_payload(error: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "source": "unavailable",
        "monitor_quote_status": SourceQuoteStatus.UNAVAILABLE.value,
        "provider_errors": [error],
        "error": error[:240],
    }


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _symbol_aliases(symbol: str) -> set[str]:
    canonical = symbol.strip().upper()
    aliases = {canonical}
    if canonical.endswith(".HK"):
        local = canonical[:-3]
        aliases.add(local)
        aliases.add(local.lstrip("0") or "0")
    return aliases


def print_console_table(result: MonitorRunResult, *, show_alerts: bool = True) -> None:
    headers = ("SYMBOL", "MARKET", "SESSION", "PRICE", "DAY%", "STATUS", "SOURCE")
    rows = []
    for snapshot in result.snapshots:
        phase = result.market_statuses[snapshot.market].phase.value
        rows.append((
            snapshot.symbol,
            snapshot.market,
            phase,
            "-" if snapshot.price is None else f"{snapshot.price:.4f}",
            "-" if snapshot.change_percent is None else f"{snapshot.change_percent:+.2f}%",
            snapshot.data_status.value,
            snapshot.source,
        ))
    widths = [max(len(headers[index]), *(len(str(row[index])) for row in rows)) for index in range(len(headers))]
    print("  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(row[index]).ljust(widths[index]) for index in range(len(headers))))
    if show_alerts:
        for alert in result.alerts:
            print(f"[{alert.severity.value}] {alert.message}")
        for event in result.recovery_events:
            print(f"[RECOVERED] {event.message}")
    print(
        f"round={result.metrics['round_id']} alerts={len(result.alerts)} "
        f"suppressed={len(result.suppressed_alerts)} duration_ms={result.metrics['duration_ms']}"
    )
