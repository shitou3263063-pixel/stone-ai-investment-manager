from __future__ import annotations

from datetime import datetime
from threading import Lock
import time
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


FUTU_SYMBOLS = {
    "VOO": "US.VOO",
    "NVDA": "US.NVDA",
    "03033.HK": "HK.03033",
}

MARKET_TIMEZONES = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
}

TRADING_STATES = {
    "MORNING",
    "AFTERNOON",
    "FUTURE_DAY_OPEN",
    "NIGHT_OPEN",
}


class FutuQuoteError(RuntimeError):
    """Sanitized quote-only failure suitable for source-health classification."""

    def __init__(self, classification: str, message: str) -> None:
        self.classification = classification
        super().__init__(f"{classification}: {message}")


def map_futu_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized in FUTU_SYMBOLS:
        return FUTU_SYMBOLS[normalized]
    if normalized.startswith(("US.", "HK.")):
        return normalized
    if normalized.endswith(".HK"):
        return f"HK.{normalized[:-3]}"
    return f"US.{normalized}"


class FutuQuoteAdapter:
    """Minimal OpenD quote adapter with a lazily created, reusable context."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 11111,
        realtime_max_age_seconds: int = 90,
        delayed_max_age_seconds: int = 1200,
        market_state_cache_seconds: int = 20,
        context_factory: Callable[..., Any] | None = None,
        sdk: Any | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.realtime_max_age_seconds = int(realtime_max_age_seconds)
        self.delayed_max_age_seconds = int(delayed_max_age_seconds)
        self.market_state_cache_seconds = int(market_state_cache_seconds)
        self._context_factory = context_factory
        self._sdk = sdk
        self._context: Any | None = None
        self._subscribed_codes: set[str] = set()
        self._market_state_cache: dict[str, tuple[float, str]] = {}
        self._lock = Lock()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "FutuQuoteAdapter":
        settings = config.get("futu") or {}
        return cls(
            host=str(settings.get("host", "127.0.0.1")),
            port=int(settings.get("port", 11111)),
            realtime_max_age_seconds=int(settings.get("realtime_max_age_seconds", 90)),
            delayed_max_age_seconds=int(settings.get("delayed_max_age_seconds", 1200)),
            market_state_cache_seconds=int(settings.get("market_state_cache_seconds", 20)),
        )

    def get_quote(self, symbol: str) -> dict[str, Any]:
        code = map_futu_symbol(symbol)
        with self._lock:
            sdk, context = self._ensure_context()
            snapshot = self._request_row(context.get_market_snapshot([code]), "snapshot", sdk)
            market_state = self._market_state(code, context, sdk)
            subtype = sdk.SubType.QUOTE
            if code not in self._subscribed_codes:
                ret, payload = context.subscribe(
                    [code], [subtype], is_first_push=False, subscribe_push=False
                )
                self._require_ok(ret, payload, "QUOTE subscription", sdk)
                self._subscribed_codes.add(code)
            quote = self._request_row(context.get_stock_quote([code]), "QUOTE data", sdk)

            market_prefix = code.split(".", 1)[0]
            timezone_name = MARKET_TIMEZONES[market_prefix]
            quote_time = _parse_quote_time(
                quote.get("update_time") or snapshot.get("update_time"), timezone_name
            )
            price = _required_float(
                quote.get("last_price", snapshot.get("last_price")), "last price"
            )
            previous_close = _optional_float(
                quote.get("prev_close_price", snapshot.get("prev_close_price"))
            )
            classification = self._classify(quote_time, market_state)

            return {
                "status": "ok",
                "source": "futu",
                "symbol": symbol,
                "provider_symbol": code,
                "current_price": price,
                "close": price,
                "previous_close": previous_close,
                "previous_official_close": previous_close,
                "quote_timestamp": quote_time.isoformat(),
                "published_at": quote_time.isoformat(),
                "source_timezone": timezone_name,
                "market_state": market_state,
                "security_status": str(snapshot.get("sec_status") or "UNKNOWN"),
                "data_frequency": "quote",
                "monitor_quote_status": classification,
                "is_realtime": classification == "REALTIME_VALID",
                "quote_permission": _permission_label(classification),
                "snapshot_success": True,
                "subscription_supported": True,
            }

    def close(self) -> None:
        with self._lock:
            context = self._context
            self._context = None
            if context is None:
                return
            try:
                if self._subscribed_codes and self._sdk is not None:
                    context.unsubscribe(sorted(self._subscribed_codes), [self._sdk.SubType.QUOTE])
            except Exception:
                pass
            finally:
                self._subscribed_codes.clear()
                self._market_state_cache.clear()
                context.close()

    def _market_state(self, code: str, context: Any, sdk: Any) -> str:
        market_prefix = code.split(".", 1)[0]
        cached = self._market_state_cache.get(market_prefix)
        current = time.monotonic()
        if cached and current - cached[0] <= self.market_state_cache_seconds:
            return cached[1]
        row = self._request_row(context.get_market_state([code]), "market state", sdk)
        value = str(row.get("market_state") or "UNKNOWN").upper()
        self._market_state_cache[market_prefix] = (current, value)
        return value

    def _ensure_context(self) -> tuple[Any, Any]:
        if self._sdk is None:
            try:
                import futu  # type: ignore
            except ImportError as exc:
                raise FutuQuoteError("UNAVAILABLE", "official futu-api is not installed") from exc
            self._sdk = futu
        if self._context is None:
            factory = self._context_factory or self._sdk.OpenQuoteContext
            try:
                self._context = factory(host=self.host, port=self.port)
            except Exception as exc:
                raise FutuQuoteError("UNAVAILABLE", _sanitize_error(exc)) from exc
        return self._sdk, self._context

    def _request_row(self, result: Any, operation: str, sdk: Any) -> dict[str, Any]:
        try:
            ret, payload = result
        except (TypeError, ValueError) as exc:
            raise FutuQuoteError("UNAVAILABLE", f"invalid {operation} response") from exc
        self._require_ok(ret, payload, operation, sdk)
        records = _records(payload)
        if not records:
            raise FutuQuoteError("UNAVAILABLE", f"empty {operation} response")
        return records[0]

    @staticmethod
    def _require_ok(ret: Any, payload: Any, operation: str, sdk: Any) -> None:
        if ret == sdk.RET_OK:
            return
        message = _sanitize_error(payload)
        classification = _failure_classification(str(payload))
        raise FutuQuoteError(classification, f"{operation} failed: {message}")

    def _classify(self, quote_time: datetime, market_state: str) -> str:
        if market_state not in TRADING_STATES:
            return "CLOSED_VALID"
        now = datetime.now(quote_time.tzinfo)
        age_seconds = (now - quote_time).total_seconds()
        if -5 <= age_seconds <= self.realtime_max_age_seconds:
            return "REALTIME_VALID"
        return "DELAYED_VALID"


def _records(payload: Any) -> list[dict[str, Any]]:
    if hasattr(payload, "to_dict"):
        return [dict(row) for row in payload.to_dict("records")]
    if isinstance(payload, list):
        return [dict(row) for row in payload]
    if isinstance(payload, Mapping):
        return [dict(payload)]
    return []


def _parse_quote_time(value: Any, timezone_name: str) -> datetime:
    if value in {None, ""}:
        raise FutuQuoteError("UNAVAILABLE", "quote timestamp is missing")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise FutuQuoteError("UNAVAILABLE", "quote timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def _required_float(value: Any, label: str) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        raise FutuQuoteError("UNAVAILABLE", f"{label} is missing")
    return parsed


def _optional_float(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def _failure_classification(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ("监管要求", "问卷", "协议确认", "permission", "权限", "401", "403")):
        return "AUTH_ERROR"
    if any(token in lowered for token in ("rate limit", "too many", "频率", "限频", "429")):
        return "RATE_LIMITED"
    return "UNAVAILABLE"


def _sanitize_error(value: Any) -> str:
    text = str(value)
    if "监管要求" in text or "问卷" in text or "协议确认" in text:
        return "OpenAPI compliance authorization is required"
    if "http" in text:
        text = text.split("http", 1)[0]
    return text[:200]


def _permission_label(classification: str) -> str:
    return {
        "REALTIME_VALID": "REALTIME_TIMESTAMP_VERIFIED",
        "DELAYED_VALID": "DELAYED_TIMESTAMP_VERIFIED",
        "CLOSED_VALID": "CLOSED_SNAPSHOT",
    }.get(classification, "UNKNOWN")
