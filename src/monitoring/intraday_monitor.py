from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from src.data_sources.data_router import get_market_quote
from utils.data_loader import project_root

from .alert_rules import AlertRuleEngine, price_change_percent
from .market_clock import MarketClock, MarketStatus, StaticHolidayCalendar
from .models import Alert, DataStatus, MonitorSnapshot
from .state_store import MonitoringStateStore


QuoteFetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class MonitorRunResult:
    snapshots: tuple[MonitorSnapshot, ...]
    alerts: tuple[Alert, ...]
    suppressed_alerts: tuple[Alert, ...]
    market_statuses: Mapping[str, MarketStatus]


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


class IntradayMonitor:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        quote_fetcher: QuoteFetcher | None = None,
        state_store: MonitoringStateStore | None = None,
        market_clock: MarketClock | None = None,
        logger: StructuredMonitorLogger | None = None,
        root: Path | None = None,
    ) -> None:
        self.config = dict(config)
        self.root = root or project_root()
        self.assets = tuple(dict(row) for row in self.config.get("symbols", []))
        self.quote_fetcher = quote_fetcher or _fetch_without_legacy_cache
        holiday_config = (self.config.get("market_clock") or {}).get("holidays") or {}
        self.market_clock = market_clock or MarketClock(StaticHolidayCalendar.from_config(holiday_config))
        runtime = self.config.get("runtime") or {}
        storage = self.config.get("storage") or {}
        state_path = _resolve_path(self.root, storage.get("sqlite_path", "data/monitoring/intraday_monitor.sqlite3"))
        self.state_store = state_store or MonitoringStateStore(
            state_path,
            snapshot_retention_hours=int(runtime.get("snapshot_retention_hours", 24)),
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
        self.reference_tolerance = max(0, int(runtime.get("reference_tolerance_minutes", 2)))

    def run_once(self, *, now: datetime | None = None, print_table: bool = True) -> MonitorRunResult:
        captured_at = now or datetime.now(tz=timezone.utc)
        if captured_at.tzinfo is None:
            raise ValueError("monitor run time must include a timezone")
        market_statuses = {
            market: self.market_clock.status(market, captured_at)
            for market in sorted({str(asset["market"]).upper() for asset in self.assets})
        }
        self.logger.write("run_started", {"captured_at": captured_at.isoformat(), "symbol_count": len(self.assets)})
        raw_quotes = self._fetch_all()
        snapshots: list[MonitorSnapshot] = []
        emitted: list[Alert] = []
        suppressed: list[Alert] = []

        for asset in self.assets:
            symbol = str(asset["symbol"])
            market = str(asset["market"]).upper()
            raw = raw_quotes.get(symbol) or _failure_payload("quote result missing")
            snapshot = _normalize_snapshot(
                asset,
                raw,
                market_statuses[market],
                captured_at,
                self.config.get("freshness") or {},
            )
            references = {
                "5m": self.state_store.reference_price(
                    symbol,
                    captured_at=captured_at,
                    lookback_minutes=5,
                    tolerance_minutes=self.reference_tolerance,
                ),
                "15m": self.state_store.reference_price(
                    symbol,
                    captured_at=captured_at,
                    lookback_minutes=15,
                    tolerance_minutes=self.reference_tolerance,
                ),
            }
            changes = {key: price_change_percent(snapshot.price, value) for key, value in references.items()}
            candidates = self.rule_engine.evaluate(
                snapshot,
                market_statuses[market],
                observed_at=captured_at,
                reference_changes=changes,
            )
            for alert in candidates:
                if self.state_store.should_emit(alert, now=captured_at):
                    self.state_store.record_alert(alert, now=captured_at, cooldown_minutes=self.cooldowns)
                    emitted.append(alert)
                    self.logger.write("alert_emitted", alert.to_dict())
                else:
                    suppressed.append(alert)
                    self.logger.write("alert_suppressed", alert.to_dict())
            self.state_store.save_snapshot(snapshot, captured_at=captured_at)
            snapshots.append(snapshot)
            self.logger.write(
                "snapshot",
                {
                    **snapshot.to_dict(),
                    "market_phase": market_statuses[market].phase.value,
                    "change_5m_percent": changes["5m"],
                    "change_15m_percent": changes["15m"],
                },
            )

        result = MonitorRunResult(
            snapshots=tuple(snapshots),
            alerts=tuple(emitted),
            suppressed_alerts=tuple(suppressed),
            market_statuses=market_statuses,
        )
        self.logger.write(
            "run_completed",
            {
                "captured_at": captured_at.isoformat(),
                "snapshot_count": len(snapshots),
                "alert_count": len(emitted),
                "suppressed_alert_count": len(suppressed),
            },
        )
        if print_table:
            print_console_table(result)
        return result

    def _fetch_all(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(self.assets)))) as executor:
            futures = {
                executor.submit(self.quote_fetcher, str(asset.get("route_symbol") or asset["symbol"])): str(asset["symbol"])
                for asset in self.assets
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results[symbol] = dict(future.result())
                except Exception as exc:  # noqa: BLE001 - one source/symbol must not abort the batch
                    results[symbol] = _failure_payload(str(exc))
        return results


def load_monitor_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else project_root() / "config" / "intraday_monitor.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("intraday monitor config must be a mapping")
    return payload


def _fetch_without_legacy_cache(symbol: str) -> dict[str, Any]:
    return get_market_quote(
        symbol,
        allow_cache=False,
        write_through_cache=False,
        log_events=False,
    )


def _normalize_snapshot(
    asset: Mapping[str, Any],
    route_payload: Mapping[str, Any],
    market_status: MarketStatus,
    captured_at: datetime,
    freshness: Mapping[str, Any],
) -> MonitorSnapshot:
    payload = _select_intraday_payload(route_payload, market_status)
    source = str(payload.get("source") or route_payload.get("source") or "unavailable")
    price = _float(payload.get("current_price", payload.get("close", payload.get("value"))))
    previous_close = _float(
        payload.get("previous_official_close", payload.get("previous_close", payload.get("previous_value")))
    )
    timestamp = _parse_optional_time(
        payload.get("quote_timestamp")
        or payload.get("observed_at_utc")
        or payload.get("source_observation_time")
    )
    cross_status = str(route_payload.get("cross_validation_status") or payload.get("cross_validation_status") or "")
    provider_errors = list(route_payload.get("provider_errors") or payload.get("provider_errors") or [])
    raw_status = str(payload.get("status") or route_payload.get("status") or "missing").lower()
    cache_used = (
        source.startswith("cache:")
        or bool(payload.get("cache_used"))
        or bool(payload.get("fallback_used") and source.startswith("cache"))
    )
    stale = bool(
        cache_used
        or payload.get("is_stale")
        or payload.get("stale")
        or payload.get("cache_stale")
        or str(payload.get("freshness_status") or "").lower() == "stale"
    )

    if "CONFLICT" in cross_status.upper():
        data_status = DataStatus.CONFLICT
    elif price is None or raw_status not in {"ok", "success", "cached"}:
        data_status = DataStatus.ERROR if provider_errors or payload.get("error") else DataStatus.MISSING
    else:
        stale = stale or _timestamp_is_stale(payload, timestamp, captured_at, market_status, freshness)
        data_status = DataStatus.STALE if stale else DataStatus.VALID

    change = None if price is None or previous_close is None else price - previous_close
    change_percent = (
        None
        if price is None or previous_close in {None, 0}
        else (price / float(previous_close) - 1.0) * 100.0
    )
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


def _select_intraday_payload(payload: Mapping[str, Any], market_status: MarketStatus) -> Mapping[str, Any]:
    if not market_status.is_trading:
        return payload
    candidates = [row for row in (payload.get("candidates") or []) if isinstance(row, Mapping)]
    eligible = [row for row in candidates if _has_provider_quote_timestamp(row)]
    if not eligible:
        return payload
    return max(eligible, key=lambda row: _parse_optional_time(row.get("quote_timestamp")) or datetime.min.replace(tzinfo=timezone.utc))


def _has_provider_quote_timestamp(payload: Mapping[str, Any]) -> bool:
    source = str(payload.get("source") or "").replace("cache:", "")
    frequency = str(payload.get("data_frequency") or "").lower()
    timestamp = _parse_optional_time(payload.get("quote_timestamp") or payload.get("observed_at_utc"))
    return timestamp is not None and not (source in {"yfinance", "alpha_vantage"} and frequency in {"daily", "1d"})


def _timestamp_is_stale(
    payload: Mapping[str, Any],
    timestamp: datetime | None,
    captured_at: datetime,
    market_status: MarketStatus,
    freshness: Mapping[str, Any],
) -> bool:
    if timestamp is None:
        return True
    age_minutes = (captured_at.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() / 60.0
    future_tolerance = float(freshness.get("future_tolerance_minutes", 2))
    if age_minutes < -future_tolerance:
        return True
    if market_status.is_trading:
        if not _has_provider_quote_timestamp(payload):
            return True
        return age_minutes > float(freshness.get("intraday_max_age_minutes", 15))
    return age_minutes > float(freshness.get("closed_market_max_age_hours", 120)) * 60.0


def _confidence(payload: Mapping[str, Any], source: str, status: DataStatus) -> float:
    if status in {DataStatus.ERROR, DataStatus.MISSING}:
        return 0.0
    if status is DataStatus.CONFLICT:
        return 0.2
    if status is DataStatus.STALE or source.startswith("cache:"):
        return 0.3
    cross_status = str(payload.get("cross_validation_status") or "").upper()
    if bool(payload.get("verified_by_second_source")) or cross_status == "VERIFIED":
        return 0.95
    return 0.65


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
        "provider_errors": [error],
        "error": error,
    }


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def print_console_table(result: MonitorRunResult) -> None:
    headers = ("SYMBOL", "MARKET", "SESSION", "PRICE", "DAY%", "STATUS", "SOURCE")
    rows = []
    for snapshot in result.snapshots:
        phase = result.market_statuses[snapshot.market].phase.value
        rows.append(
            (
                snapshot.symbol,
                snapshot.market,
                phase,
                "-" if snapshot.price is None else f"{snapshot.price:.4f}",
                "-" if snapshot.change_percent is None else f"{snapshot.change_percent:+.2f}%",
                snapshot.data_status.value,
                snapshot.source,
            )
        )
    widths = [max(len(headers[index]), *(len(str(row[index])) for row in rows)) for index in range(len(headers))]
    print("  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(row[index]).ljust(widths[index]) for index in range(len(headers))))
    print(f"alerts={len(result.alerts)} suppressed={len(result.suppressed_alerts)}")
