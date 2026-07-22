from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Mapping

from .models import Alert, MonitorSnapshot


class MonitoringStateStore:
    """SQLite state isolated from portfolio, execution, and Smart Grid state."""

    def __init__(self, path: str | Path, *, snapshot_retention_hours: int = 24) -> None:
        self.path = Path(path)
        self.snapshot_retention_hours = int(snapshot_retention_hours)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitor_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_snapshots_symbol_time
                    ON monitor_snapshots(symbol, captured_at DESC);
                CREATE TABLE IF NOT EXISTS alert_state (
                    fingerprint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    last_alert_at TEXT NOT NULL,
                    cooldown_until TEXT NOT NULL
                );
                """
            )

    def save_snapshot(self, snapshot: MonitorSnapshot, *, captured_at: datetime) -> None:
        captured = _as_utc(captured_at)
        cutoff = captured - timedelta(hours=self.snapshot_retention_hours)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO monitor_snapshots(symbol, captured_at, payload_json) VALUES (?, ?, ?)",
                (snapshot.symbol, captured.isoformat(), json.dumps(snapshot.to_dict(), ensure_ascii=False)),
            )
            connection.execute(
                "DELETE FROM monitor_snapshots WHERE captured_at < ?",
                (cutoff.isoformat(),),
            )

    def reference_price(
        self,
        symbol: str,
        *,
        captured_at: datetime,
        lookback_minutes: int,
        tolerance_minutes: int,
    ) -> float | None:
        target = _as_utc(captured_at) - timedelta(minutes=lookback_minutes)
        lower = target - timedelta(minutes=tolerance_minutes)
        upper = target + timedelta(minutes=tolerance_minutes)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT captured_at, payload_json
                FROM monitor_snapshots
                WHERE symbol = ? AND captured_at BETWEEN ? AND ?
                ORDER BY captured_at DESC
                """,
                (symbol, lower.isoformat(), upper.isoformat()),
            ).fetchall()
        if not rows:
            return None
        closest = min(rows, key=lambda row: abs((_parse_time(row["captured_at"]) - target).total_seconds()))
        snapshot = MonitorSnapshot.from_dict(json.loads(str(closest["payload_json"])))
        return snapshot.price if snapshot.data_status.value == "VALID" and not snapshot.is_stale else None

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
    ) -> None:
        current = _as_utc(now)
        minutes = int(cooldown_minutes[alert.severity.value])
        cooldown_until = current + timedelta(minutes=minutes)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_state(
                    fingerprint, symbol, rule_id, direction, severity,
                    last_alert_at, cooldown_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    severity = excluded.severity,
                    last_alert_at = excluded.last_alert_at,
                    cooldown_until = excluded.cooldown_until
                """,
                (
                    alert.fingerprint,
                    alert.symbol,
                    alert.rule_id,
                    alert.direction,
                    alert.severity.value,
                    current.isoformat(),
                    cooldown_until.isoformat(),
                ),
            )

    def alert_state(self, fingerprint: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM alert_state WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return dict(row) if row else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("state timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
