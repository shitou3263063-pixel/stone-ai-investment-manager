from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
from threading import Lock
import time
from typing import Any, Callable, Mapping

from src.data_sources import alpha_vantage_client, finnhub_client, yfinance_client
from src.data_sources.data_router import provider_symbol_for

from .models import SourceQuoteStatus
from .state_store import MonitoringStateStore


Provider = Callable[[str], dict[str, Any]]


PROVIDERS: dict[str, Provider] = {
    "finnhub": finnhub_client.get_quote,
    "alpha_vantage": alpha_vantage_client.get_quote,
    "yfinance": yfinance_client.get_quote,
}


class MonitoringQuoteRouter:
    """Monitoring-only route; it never reads or writes the daily-report cache."""

    def __init__(
        self,
        config: Mapping[str, Any],
        state_store: MonitoringStateStore,
        *,
        providers: Mapping[str, Provider] | None = None,
    ) -> None:
        self.config = config
        self.state_store = state_store
        routing = config.get("source_routing") or {}
        self.priority = routing.get("source_priority") or {}
        self.providers = dict(providers or PROVIDERS)
        self.conflict_threshold = float(
            routing.get("source_conflict_threshold", routing.get("source_conflict_threshold_percent", 1.0))
        )
        self.allow_delayed = bool(routing.get("allow_delayed_quotes", True))
        retry = config.get("retry") or {}
        self.retry_initial = int(retry.get("retry_initial_seconds", 30))
        self.retry_max = int(retry.get("retry_max_seconds", 900))
        self.failure_threshold = int(retry.get("failure_threshold", 3))
        self._source_locks = {source: Lock() for source in self.providers}

    def fetch(self, asset: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
        market = str(asset["market"]).upper()
        route_symbol = str(asset.get("route_symbol") or asset["symbol"])
        source_order = list(self.priority.get(market) or self.priority.get("default") or self.providers)
        observations: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        recoveries: list[dict[str, Any]] = []

        for source in source_order:
            provider = self.providers.get(str(source))
            if provider is None:
                continue
            guard = self._source_locks[str(source)] if source != "yfinance" else nullcontext()
            with guard:
                if not self.state_store.source_retry_allowed(str(source), now=now):
                    health = self.state_store.source_health(str(source)) or {}
                    observations.append({
                        "source": source,
                        "classification": str(health.get("status") or SourceQuoteStatus.UNAVAILABLE.value),
                        "attempted": False,
                        "reason": "BACKOFF_ACTIVE",
                    })
                    continue
                started = time.perf_counter()
                try:
                    provider_symbol = provider_symbol_for(route_symbol, str(source))
                    raw = dict(provider(provider_symbol))
                    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
                    classification = classify_quote(str(source), raw, now=now)
                    recovery = self.state_store.record_source_success(
                        str(source), now=now, latency_ms=latency_ms
                    )
                    if recovery:
                        recoveries.append(recovery.to_dict())
                    row = {
                        **raw,
                        "source": str(source),
                        "provider_symbol": provider_symbol,
                        "monitor_quote_status": classification.value,
                        "latency_ms": latency_ms,
                    }
                    observations.append({
                        "source": source,
                        "classification": classification.value,
                        "attempted": True,
                        "latency_ms": latency_ms,
                        "timestamp": raw.get("quote_timestamp") or raw.get("published_at"),
                        "previous_close_present": raw.get("previous_close") is not None,
                    })
                    candidates.append(row)
                except Exception as exc:  # noqa: BLE001 - isolate every provider
                    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
                    classification = classify_failure(exc)
                    health = self.state_store.record_source_failure(
                        str(source),
                        now=now,
                        error_type=classification.value,
                        error_message=str(exc),
                        latency_ms=latency_ms,
                        retry_initial_seconds=self.retry_initial,
                        retry_max_seconds=self.retry_max,
                        failure_threshold=self.failure_threshold,
                    )
                    observations.append({
                        "source": source,
                        "classification": classification.value,
                        "attempted": True,
                        "latency_ms": latency_ms,
                        "error_type": type(exc).__name__,
                        "error_summary": str(health.get("status")),
                    })

        intraday = [
            row for row in candidates
            if row.get("monitor_quote_status") in {
                SourceQuoteStatus.REALTIME_VALID.value,
                SourceQuoteStatus.DELAYED_VALID.value,
            }
            and (
                self.allow_delayed
                or row.get("monitor_quote_status") == SourceQuoteStatus.REALTIME_VALID.value
            )
        ]
        conflict = _has_conflict(intraday, self.conflict_threshold)
        if intraday:
            selected = dict(intraday[0])
            selected.update({
                "candidates": candidates,
                "source_results": observations,
                "health_recoveries": recoveries,
                "cross_validation_status": "SOURCE_CONFLICT" if conflict else (
                    "VERIFIED" if len(intraday) >= 2 else "LATEST_DATE_SINGLE_SOURCE"
                ),
                "verified_by_second_source": len(intraday) >= 2 and not conflict,
            })
            return selected

        daily = [row for row in candidates if row.get("monitor_quote_status") == SourceQuoteStatus.DAILY_ONLY.value]
        if daily:
            selected = dict(daily[0])
            selected.update({
                "candidates": candidates,
                "source_results": observations,
                "health_recoveries": recoveries,
                "cross_validation_status": "DAILY_REFERENCE_ONLY",
                "verified_by_second_source": False,
            })
            return selected

        classifications = [str(row.get("classification")) for row in observations]
        final_status = _failure_precedence(classifications)
        return {
            "status": "failed",
            "source": "unavailable",
            "monitor_quote_status": final_status,
            "source_results": observations,
            "health_recoveries": recoveries,
            "provider_errors": classifications,
            "error": final_status,
        }


def classify_quote(source: str, payload: Mapping[str, Any], *, now: datetime) -> SourceQuoteStatus:
    declared = str(payload.get("monitor_quote_status") or "")
    if declared:
        try:
            return SourceQuoteStatus(declared)
        except ValueError:
            pass
    frequency = str(payload.get("data_frequency") or "quote").lower()
    timestamp = _parse_timestamp(payload.get("quote_timestamp") or payload.get("published_at"))
    if frequency in {"daily", "1d"} or timestamp is None:
        return SourceQuoteStatus.DAILY_ONLY
    if bool(payload.get("is_realtime")):
        return SourceQuoteStatus.REALTIME_VALID
    return SourceQuoteStatus.DELAYED_VALID


def classify_failure(exc: Exception) -> SourceQuoteStatus:
    text = str(exc).lower()
    if "auth_error" in text:
        return SourceQuoteStatus.AUTH_ERROR
    if any(token in text for token in ("api_key", "apikey", "未配置", "unauthorized", "forbidden", "401", "403")):
        return SourceQuoteStatus.AUTH_ERROR
    if any(token in text for token in ("rate limit", "rate_limit", "限频", "too many requests", "429", "frequency")):
        return SourceQuoteStatus.RATE_LIMITED
    return SourceQuoteStatus.UNAVAILABLE


def _parse_timestamp(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _price(row: Mapping[str, Any]) -> float | None:
    try:
        value = row.get("current_price", row.get("close", row.get("value")))
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _has_conflict(candidates: list[Mapping[str, Any]], threshold_percent: float) -> bool:
    values = [value for value in (_price(row) for row in candidates) if value is not None and value > 0]
    return len(values) >= 2 and (max(values) / min(values) - 1.0) * 100.0 > threshold_percent


def _failure_precedence(classifications: list[str]) -> str:
    for value in (
        SourceQuoteStatus.RATE_LIMITED.value,
        SourceQuoteStatus.AUTH_ERROR.value,
        SourceQuoteStatus.UNAVAILABLE.value,
    ):
        if value in classifications:
            return value
    return SourceQuoteStatus.UNAVAILABLE.value
