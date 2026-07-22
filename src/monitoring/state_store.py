from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .models import (
    Alert,
    ChangeResult,
    ChangeStatus,
    DataStatus,
    MonitorSnapshot,
    RecoveryEvent,
    SourceHealthStatus,
)


SCHEMA_VERSION = 2
TRUSTED_STATUSES = {DataStatus.VALID, DataStatus.DELAYED_VALID}
MARKET_TIMEZONES = {"US": "America/New_York", "HK": "Asia/Hong_Kong", "CN": "Asia/Shanghai"}


class MonitoringStateStore:
    """Versioned SQLite state isolated from all portfolio and trading state."""

    def __init__(
        self,
        path: str | Path,
        *,
        snapshot_retention_hours: int | None = None,
        snapshot_retention_days: int = 7,
        alert_retention_days: int = 30,
        source_health_retention_days: int = 30,
    ) -> None:
        self.path = Path(path)
        if snapshot_retention_hours is not None:
            snapshot_retention_days = max(1, (int(snapshot_retention_hours) + 23) // 24)
        self.snapshot_retention_days = int(snapshot_retention_days)
        self.alert_retention_days = int(alert_retention_days)
        self.source_health_retention_days = int(source_health_retention_days)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def close(self) -> None:
        """Connections are scoped per operation; retained for runtime lifecycle symmetry."""

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS monitor_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        session_date TEXT NOT NULL,
                        data_status TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time
                        ON snapshots(symbol, captured_at DESC);
                    CREATE TABLE IF NOT EXISTS alert_state (
                        fingerprint TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        rule_id TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        last_alert_at TEXT NOT NULL,
                        cooldown_until TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS alert_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fingerprint TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        rule_id TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        event_at TEXT NOT NULL,
                        emitted INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS source_health (
                        source TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        last_success_at TEXT,
                        last_failure_at TEXT,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        last_error_type TEXT,
                        last_error_message TEXT,
                        latency_ms REAL,
                        rate_limit_until TEXT,
                        next_retry_at TEXT,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS source_health_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error_type TEXT,
                        event_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS monitor_runs (
                        round_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        ended_at TEXT NOT NULL,
                        duration_ms REAL NOT NULL,
                        success_count INTEGER NOT NULL,
                        stale_count INTEGER NOT NULL,
                        conflict_count INTEGER NOT NULL,
                        error_count INTEGER NOT NULL,
                        new_alert_count INTEGER NOT NULL,
                        suppressed_alert_count INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS recovery_events (
                        fingerprint TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        recovered_rule TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        message TEXT NOT NULL
                    );
                    """
                )
                self._ensure_column(connection, "alert_state", "active", "INTEGER NOT NULL DEFAULT 1")
                self._ensure_column(connection, "alert_state", "last_observed_at", "TEXT")
                self._ensure_column(connection, "alert_state", "occurrence_count", "INTEGER NOT NULL DEFAULT 1")
                connection.execute(
                    """
                    INSERT INTO snapshots(symbol, captured_at, session_date, data_status, payload_json)
                    SELECT legacy.symbol, legacy.captured_at,
                           substr(legacy.captured_at, 1, 10),
                           COALESCE(json_extract(legacy.payload_json, '$.data_status'), 'STALE'),
                           legacy.payload_json
                    FROM monitor_snapshots AS legacy
                    WHERE NOT EXISTS (
                        SELECT 1 FROM snapshots AS current
                        WHERE current.symbol = legacy.symbol
                          AND current.captured_at = legacy.captured_at
                          AND current.payload_json = legacy.payload_json
                    )
                    """
                )
                now = datetime.now(tz=timezone.utc).isoformat()
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now),
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def save_snapshot(self, snapshot: MonitorSnapshot, *, captured_at: datetime) -> None:
        captured = _as_utc(captured_at)
        session_date = _session_date(snapshot.market, captured)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO snapshots(symbol, captured_at, session_date, data_status, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.symbol,
                    captured.isoformat(),
                    session_date,
                    snapshot.data_status.value,
                    json.dumps(snapshot.to_dict(), ensure_ascii=False),
                ),
            )

    def calculate_change(
        self,
        snapshot: MonitorSnapshot,
        *,
        captured_at: datetime,
        lookback_minutes: int,
        tolerance_seconds: int,
        allow_delayed: bool = True,
    ) -> ChangeResult:
        current_at = _as_utc(captured_at)
        trusted = {DataStatus.VALID}
        if allow_delayed:
            trusted.add(DataStatus.DELAYED_VALID)
        if snapshot.data_status not in trusted or snapshot.price is None:
            return ChangeResult(
                lookback_minutes, ChangeStatus.UNTRUSTED_CURRENT, None, None,
                snapshot.price, None, current_at,
            )
        session_date = _session_date(snapshot.market, current_at)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT captured_at, data_status, payload_json
                FROM snapshots
                WHERE symbol = ? AND session_date = ? AND captured_at < ?
                ORDER BY captured_at DESC
                """,
                (snapshot.symbol, session_date, current_at.isoformat()),
            ).fetchall()
        trusted_rows = [row for row in rows if DataStatus(str(row["data_status"])) in trusted]
        if not trusted_rows:
            return ChangeResult(
                lookback_minutes, ChangeStatus.INSUFFICIENT_HISTORY, None, None,
                snapshot.price, None, current_at,
            )
        target = current_at - timedelta(minutes=lookback_minutes)
        matching = [
            row for row in trusted_rows
            if abs((_parse_time(str(row["captured_at"])) - target).total_seconds()) <= tolerance_seconds
        ]
        if not matching:
            return ChangeResult(
                lookback_minutes, ChangeStatus.NO_MATCHING_SNAPSHOT, None, None,
                snapshot.price, None, current_at,
            )
        closest = min(matching, key=lambda row: abs((_parse_time(str(row["captured_at"])) - target).total_seconds()))
        reference = MonitorSnapshot.from_dict(json.loads(str(closest["payload_json"])))
        reference_at = _parse_time(str(closest["captured_at"]))
        change = None if reference.price in {None, 0} else (snapshot.price / float(reference.price) - 1.0) * 100.0
        return ChangeResult(
            lookback_minutes, ChangeStatus.OK, change, reference.price,
            snapshot.price, reference_at, current_at,
        )

    def reference_price(
        self,
        symbol: str,
        *,
        captured_at: datetime,
        lookback_minutes: int,
        tolerance_minutes: int,
    ) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM snapshots WHERE symbol = ? ORDER BY captured_at DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        if not row:
            return None
        current = MonitorSnapshot.from_dict(json.loads(str(row["payload_json"])))
        result = self.calculate_change(
            current,
            captured_at=captured_at,
            lookback_minutes=lookback_minutes,
            tolerance_seconds=tolerance_minutes * 60,
        )
        return result.reference_price

    def should_emit(self, alert: Alert, *, now: datetime) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cooldown_until FROM alert_state WHERE fingerprint = ?",
                (alert.fingerprint,),
            ).fetchone()
        return row is None or _as_utc(now) >= _parse_time(str(row["cooldown_until"]))

    def record_alert(
        self,
        alert: Alert,
        *,
        now: datetime,
        cooldown_minutes: Mapping[str, int],
        emitted: bool = True,
    ) -> None:
        current = _as_utc(now)
        minutes = int(cooldown_minutes[alert.severity.value])
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT active, occurrence_count, cooldown_until, last_alert_at FROM alert_state WHERE fingerprint = ?",
                (alert.fingerprint,),
            ).fetchone()
            occurrence = int(existing["occurrence_count"] or 1) if existing else 1
            if existing and not bool(existing["active"]):
                occurrence += 1
            last_alert_at = current.isoformat() if emitted or not existing else str(existing["last_alert_at"])
            cooldown_until = (
                (current + timedelta(minutes=minutes)).isoformat()
                if emitted or not existing else str(existing["cooldown_until"])
            )
            connection.execute(
                """
                INSERT INTO alert_state(
                    fingerprint, symbol, rule_id, direction, severity, last_alert_at,
                    cooldown_until, active, last_observed_at, occurrence_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    severity = excluded.severity,
                    last_alert_at = excluded.last_alert_at,
                    cooldown_until = excluded.cooldown_until,
                    active = 1,
                    last_observed_at = excluded.last_observed_at,
                    occurrence_count = excluded.occurrence_count
                """,
                (
                    alert.fingerprint, alert.symbol, alert.rule_id, alert.direction,
                    alert.severity.value, last_alert_at, cooldown_until,
                    current.isoformat(), occurrence,
                ),
            )
            connection.execute(
                """
                INSERT INTO alert_history(
                    fingerprint, symbol, rule_id, direction, severity, event_at, emitted
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.fingerprint, alert.symbol, alert.rule_id, alert.direction,
                    alert.severity.value, current.isoformat(), int(emitted),
                ),
            )

    def recover_inactive_alerts(
        self,
        symbol: str,
        active_fingerprints: set[str],
        *,
        now: datetime,
    ) -> list[RecoveryEvent]:
        current = _as_utc(now)
        events: list[RecoveryEvent] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT fingerprint, rule_id, occurrence_count FROM alert_state
                WHERE symbol = ? AND active = 1
                """,
                (symbol,),
            ).fetchall()
            for row in rows:
                original = str(row["fingerprint"])
                if original in active_fingerprints:
                    continue
                occurrence = int(row["occurrence_count"] or 1)
                fingerprint = sha256(f"{original}|RECOVERED|{occurrence}".encode("utf-8")).hexdigest()
                event = RecoveryEvent(
                    fingerprint=fingerprint,
                    event_type="ALERT_RECOVERED",
                    subject=symbol,
                    recovered_rule=str(row["rule_id"]),
                    observed_at=current,
                    message=f"{symbol} recovered from {row['rule_id']}",
                )
                connection.execute("UPDATE alert_state SET active = 0 WHERE fingerprint = ?", (original,))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO recovery_events(
                        fingerprint, event_type, subject, recovered_rule, observed_at, message
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.fingerprint, event.event_type, event.subject,
                        event.recovered_rule, event.observed_at.isoformat(), event.message,
                    ),
                )
                events.append(event)
        return events

    def source_retry_allowed(self, source: str, *, now: datetime) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT next_retry_at, rate_limit_until FROM source_health WHERE source = ?",
                (source,),
            ).fetchone()
        if not row:
            return True
        current = _as_utc(now)
        retry_values = [row["next_retry_at"], row["rate_limit_until"]]
        return all(not value or current >= _parse_time(str(value)) for value in retry_values)

    def record_source_success(
        self,
        source: str,
        *,
        now: datetime,
        latency_ms: float,
    ) -> RecoveryEvent | None:
        current = _as_utc(now)
        with self._connect() as connection:
            previous = connection.execute("SELECT * FROM source_health WHERE source = ?", (source,)).fetchone()
            recovered = bool(previous and str(previous["status"]) != SourceHealthStatus.HEALTHY.value)
            connection.execute(
                """
                INSERT INTO source_health(
                    source, status, last_success_at, last_failure_at, consecutive_failures,
                    last_error_type, last_error_message, latency_ms, rate_limit_until,
                    next_retry_at, updated_at
                ) VALUES (?, ?, ?, NULL, 0, NULL, NULL, ?, NULL, NULL, ?)
                ON CONFLICT(source) DO UPDATE SET
                    status = excluded.status,
                    last_success_at = excluded.last_success_at,
                    consecutive_failures = 0,
                    last_error_type = NULL,
                    last_error_message = NULL,
                    latency_ms = excluded.latency_ms,
                    rate_limit_until = NULL,
                    next_retry_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (source, SourceHealthStatus.HEALTHY.value, current.isoformat(), latency_ms, current.isoformat()),
            )
            connection.execute(
                "INSERT INTO source_health_history(source, status, error_type, event_at) VALUES (?, ?, NULL, ?)",
                (source, SourceHealthStatus.HEALTHY.value, current.isoformat()),
            )
            if not recovered:
                return None
            fingerprint = sha256(f"SOURCE|{source}|RECOVERED|{current.isoformat()}".encode("utf-8")).hexdigest()
            event = RecoveryEvent(
                fingerprint, "SOURCE_RECOVERED", source, "source_health", current,
                f"data source {source} recovered",
            )
            connection.execute(
                """
                INSERT INTO recovery_events(
                    fingerprint, event_type, subject, recovered_rule, observed_at, message
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.fingerprint, event.event_type, event.subject,
                    event.recovered_rule, event.observed_at.isoformat(), event.message,
                ),
            )
            return event

    def record_source_failure(
        self,
        source: str,
        *,
        now: datetime,
        error_type: str,
        error_message: str,
        latency_ms: float,
        retry_initial_seconds: int,
        retry_max_seconds: int,
        failure_threshold: int,
    ) -> dict[str, Any]:
        current = _as_utc(now)
        safe_message = _sanitize_error(error_message)
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT consecutive_failures FROM source_health WHERE source = ?", (source,)
            ).fetchone()
            failures = int(previous["consecutive_failures"] or 0) + 1 if previous else 1
            kind = error_type.upper()
            if kind == "AUTH_ERROR":
                status = SourceHealthStatus.AUTH_ERROR
                delay = retry_max_seconds
            elif kind == "RATE_LIMITED":
                status = SourceHealthStatus.RATE_LIMITED
                delay = retry_max_seconds
            else:
                status = (
                    SourceHealthStatus.UNAVAILABLE
                    if failures >= failure_threshold else SourceHealthStatus.DEGRADED
                )
                delay = min(retry_max_seconds, retry_initial_seconds * (2 ** (failures - 1)))
            retry_at = current + timedelta(seconds=delay)
            rate_limit_until = retry_at.isoformat() if status is SourceHealthStatus.RATE_LIMITED else None
            connection.execute(
                """
                INSERT INTO source_health(
                    source, status, last_success_at, last_failure_at, consecutive_failures,
                    last_error_type, last_error_message, latency_ms, rate_limit_until,
                    next_retry_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    status = excluded.status,
                    last_failure_at = excluded.last_failure_at,
                    consecutive_failures = excluded.consecutive_failures,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    latency_ms = excluded.latency_ms,
                    rate_limit_until = excluded.rate_limit_until,
                    next_retry_at = excluded.next_retry_at,
                    updated_at = excluded.updated_at
                """,
                (
                    source, status.value, current.isoformat(), failures, kind,
                    safe_message, latency_ms, rate_limit_until, retry_at.isoformat(), current.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO source_health_history(source, status, error_type, event_at) VALUES (?, ?, ?, ?)",
                (source, status.value, kind, current.isoformat()),
            )
        return {
            "source": source,
            "status": status.value,
            "consecutive_failures": failures,
            "next_retry_at": retry_at.isoformat(),
            "rate_limit_until": rate_limit_until,
        }

    def source_health(self, source: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM source_health WHERE source = ?", (source,)).fetchone()
        return dict(row) if row else None

    def record_monitor_run(self, metrics: Mapping[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO monitor_runs(
                    round_id, started_at, ended_at, duration_ms, success_count,
                    stale_count, conflict_count, error_count, new_alert_count,
                    suppressed_alert_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics["round_id"], metrics["started_at"], metrics["ended_at"],
                    metrics["duration_ms"], metrics["success_count"], metrics["stale_count"],
                    metrics["conflict_count"], metrics["error_count"],
                    metrics["new_alert_count"], metrics["suppressed_alert_count"],
                ),
            )

    def cleanup(self, *, now: datetime) -> None:
        current = _as_utc(now)
        snapshot_cutoff = current - timedelta(days=self.snapshot_retention_days)
        alert_cutoff = current - timedelta(days=self.alert_retention_days)
        health_cutoff = current - timedelta(days=self.source_health_retention_days)
        with self._connect() as connection:
            connection.execute("DELETE FROM snapshots WHERE captured_at < ?", (snapshot_cutoff.isoformat(),))
            connection.execute("DELETE FROM alert_history WHERE event_at < ?", (alert_cutoff.isoformat(),))
            connection.execute("DELETE FROM monitor_runs WHERE ended_at < ?", (alert_cutoff.isoformat(),))
            connection.execute("DELETE FROM recovery_events WHERE observed_at < ?", (alert_cutoff.isoformat(),))
            connection.execute("DELETE FROM source_health_history WHERE event_at < ?", (health_cutoff.isoformat(),))

    def alert_state(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM alert_state WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return dict(row) if row else None

    def table_count(self, table: str) -> int:
        allowed = {
            "snapshots", "alert_state", "alert_history", "source_health",
            "monitor_runs", "recovery_events", "monitor_snapshots",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _session_date(market: str, captured_at: datetime) -> str:
    timezone_name = MARKET_TIMEZONES.get(market.upper(), "UTC")
    return captured_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("state timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _sanitize_error(message: str) -> str:
    text = str(message)
    for marker in ("token=", "apikey=", "api_key="):
        lower = text.lower()
        position = lower.find(marker)
        if position >= 0:
            end = text.find("&", position)
            end = len(text) if end < 0 else end
            text = text[: position + len(marker)] + "REDACTED" + text[end:]
    return text[:240]
