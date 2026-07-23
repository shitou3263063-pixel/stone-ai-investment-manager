from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4
from contextlib import contextmanager

from .models import GridDecision, PositionType, STRATEGY_ID


SCHEMA_VERSION = 1


class LongTermGridStateStore:
    """SQLite ledger isolated from portfolio, execution and legacy Smart Grid state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        """Yield a connection and always close it after the transaction scope.

        ``sqlite3.Connection`` commits/rolls back when used as a context manager,
        but it does not close the underlying handle.  Explicit closure is
        important on Windows so the isolated simulation database can be removed
        or rotated after a run and by pytest temporary-directory cleanup.
        """
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        """Connections are operation-scoped."""

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations(
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS evaluations(
                        event_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        generated_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        grid_level INTEGER,
                        payload_json TEXT NOT NULL,
                        decision_inputs_hash TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_grid_evaluations_symbol_time
                        ON evaluations(symbol, generated_at DESC);
                    CREATE TABLE IF NOT EXISTS grid_lots(
                        event_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        position_type TEXT NOT NULL CHECK(position_type = 'GRID_POSITION'),
                        grid_level INTEGER NOT NULL,
                        reference_center REAL NOT NULL,
                        simulated_entry_time TEXT NOT NULL,
                        simulated_entry_price REAL NOT NULL,
                        simulated_quantity INTEGER NOT NULL,
                        allocated_cash REAL NOT NULL,
                        remaining_quantity INTEGER NOT NULL,
                        take_profit_1 REAL NOT NULL,
                        take_profit_2 REAL NOT NULL,
                        tp1_completed INTEGER NOT NULL DEFAULT 0,
                        tp2_completed INTEGER NOT NULL DEFAULT 0,
                        realized_profit REAL NOT NULL DEFAULT 0,
                        unrealized_profit REAL NOT NULL DEFAULT 0,
                        fees REAL NOT NULL,
                        slippage REAL NOT NULL,
                        status TEXT NOT NULL,
                        source_quote_time TEXT NOT NULL,
                        decision_inputs_hash TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_open_level
                        ON grid_lots(strategy_id, symbol, grid_level)
                        WHERE status IN ('OPEN', 'PARTIAL');
                    CREATE TABLE IF NOT EXISTS ledger_events(
                        event_id TEXT PRIMARY KEY,
                        strategy_id TEXT NOT NULL,
                        lot_event_id TEXT,
                        event_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        grid_level INTEGER,
                        event_time TEXT NOT NULL,
                        price REAL,
                        quantity INTEGER NOT NULL DEFAULT 0,
                        amount_cny REAL NOT NULL DEFAULT 0,
                        fees REAL NOT NULL DEFAULT 0,
                        slippage REAL NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL,
                        FOREIGN KEY(lot_event_id) REFERENCES grid_lots(event_id)
                    );
                    CREATE TABLE IF NOT EXISTS reference_centers(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        center REAL NOT NULL,
                        status TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        proposed_at TEXT NOT NULL,
                        confirmed_at TEXT,
                        superseded_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_grid_center_symbol
                        ON reference_centers(strategy_id, symbol, proposed_at DESC);
                    CREATE TABLE IF NOT EXISTS notification_state(
                        fingerprint TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        last_attempt_at TEXT NOT NULL,
                        last_sent_at TEXT,
                        cooldown_until TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS benchmark_state(
                        symbol TEXT PRIMARY KEY,
                        baseline_time TEXT NOT NULL,
                        baseline_price REAL NOT NULL,
                        budget_cny REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS equity_history(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        observed_at TEXT NOT NULL,
                        strategy_value_cny REAL NOT NULL,
                        benchmark_value_cny REAL NOT NULL,
                        capital_used_cny REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS strategy_meta(
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                now = datetime.now(tz=timezone.utc).isoformat()
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO strategy_meta(key, value, updated_at) VALUES ('strategy_id', ?, ?)",
                    (STRATEGY_ID, now),
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def record_evaluation(self, decision: GridDecision) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO evaluations(
                    event_id, strategy_id, symbol, generated_at, status,
                    grid_level, payload_json, decision_inputs_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.event_id,
                    decision.strategy_id,
                    decision.symbol,
                    _utc(decision.generated_at).isoformat(),
                    decision.status.value,
                    decision.grid_level,
                    json.dumps(decision.to_dict(), ensure_ascii=False, default=str),
                    decision.decision_inputs_hash,
                ),
            )

    def latest_evaluations(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM (
                    SELECT payload_json,
                           ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY generated_at DESC) AS rank
                    FROM evaluations
                ) WHERE rank = 1 ORDER BY json_extract(payload_json, '$.symbol')
                """
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def active_lot(self, symbol: str, grid_level: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM grid_lots
                WHERE strategy_id = ? AND symbol = ? AND grid_level = ?
                  AND status IN ('OPEN', 'PARTIAL')
                LIMIT 1
                """,
                (STRATEGY_ID, symbol, int(grid_level)),
            ).fetchone()
        return dict(row) if row else None

    def open_lots(self, symbol: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM grid_lots WHERE status IN ('OPEN', 'PARTIAL')"
        params: tuple[Any, ...] = ()
        if symbol:
            query += " AND symbol = ?"
            params = (symbol,)
        query += " ORDER BY simulated_entry_time"
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def record_simulated_entry(
        self,
        decision: GridDecision,
        *,
        entry_time: datetime | None = None,
    ) -> str:
        if decision.grid_level is None or decision.current_price is None:
            raise ValueError("simulated entry requires a priced grid level")
        if decision.estimated_quantity < 1:
            raise ValueError("simulated entry requires at least one whole share")
        if decision.position_scope != PositionType.GRID_POSITION.value:
            raise ValueError("long-term grid ledger accepts GRID_POSITION only")
        if self.active_lot(decision.symbol, decision.grid_level):
            raise ValueError("grid level already has an unfinished profit cycle")
        event_time = _utc(entry_time or decision.generated_at)
        lot_event_id = decision.event_id
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO grid_lots(
                    event_id, strategy_id, symbol, position_type, grid_level,
                    reference_center, simulated_entry_time, simulated_entry_price,
                    simulated_quantity, allocated_cash, remaining_quantity,
                    take_profit_1, take_profit_2, realized_profit, unrealized_profit,
                    fees, slippage, status, source_quote_time, decision_inputs_hash
                ) VALUES (?, ?, ?, 'GRID_POSITION', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 'OPEN', ?, ?)
                """,
                (
                    lot_event_id,
                    STRATEGY_ID,
                    decision.symbol,
                    decision.grid_level,
                    decision.reference_center,
                    event_time.isoformat(),
                    decision.current_price,
                    decision.estimated_quantity,
                    decision.adjusted_amount_cny,
                    decision.estimated_quantity,
                    decision.take_profit_1,
                    decision.take_profit_2,
                    decision.estimated_fees_cny,
                    decision.estimated_slippage_cny,
                    _utc(decision.quote_time or event_time).isoformat(),
                    decision.decision_inputs_hash,
                ),
            )
            connection.execute(
                """
                INSERT INTO ledger_events(
                    event_id, strategy_id, lot_event_id, event_type, symbol,
                    grid_level, event_time, price, quantity, amount_cny, fees,
                    slippage, payload_json
                ) VALUES (?, ?, ?, 'SIMULATED_BUY', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{lot_event_id}:BUY",
                    STRATEGY_ID,
                    lot_event_id,
                    decision.symbol,
                    decision.grid_level,
                    event_time.isoformat(),
                    decision.current_price,
                    decision.estimated_quantity,
                    decision.adjusted_amount_cny,
                    decision.estimated_fees_cny,
                    decision.estimated_slippage_cny,
                    json.dumps(decision.to_dict(), ensure_ascii=False, default=str),
                ),
            )
            connection.commit()
        return lot_event_id

    def record_take_profit(
        self,
        lot_event_id: str,
        *,
        stage: int,
        price: float,
        event_time: datetime,
        fees: float,
        slippage: float,
    ) -> dict[str, Any]:
        if stage not in {1, 2}:
            raise ValueError("take-profit stage must be 1 or 2")
        current = _utc(event_time)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM grid_lots WHERE event_id = ? AND status IN ('OPEN', 'PARTIAL')",
                (lot_event_id,),
            ).fetchone()
            if not row:
                raise ValueError("open GRID_POSITION lot not found")
            lot = dict(row)
            if str(lot["position_type"]) != PositionType.GRID_POSITION.value:
                raise ValueError("take profit may sell GRID_POSITION only")
            if stage == 1:
                if bool(lot["tp1_completed"]):
                    raise ValueError("take-profit stage 1 already completed")
                quantity = max(1, int(lot["simulated_quantity"]) // 2)
            else:
                if not bool(lot["tp1_completed"]) or bool(lot["tp2_completed"]):
                    raise ValueError("take-profit stage 2 requires completed stage 1")
                quantity = int(lot["remaining_quantity"])
            quantity = min(quantity, int(lot["remaining_quantity"]))
            gross_profit = (float(price) - float(lot["simulated_entry_price"])) * quantity
            remaining = int(lot["remaining_quantity"]) - quantity
            status = "COMPLETED" if remaining == 0 else "PARTIAL"
            connection.execute(
                """
                UPDATE grid_lots SET
                    remaining_quantity = ?,
                    tp1_completed = CASE WHEN ? = 1 THEN 1 ELSE tp1_completed END,
                    tp2_completed = CASE WHEN ? = 2 OR (? = 1 AND ? = 0) THEN 1 ELSE tp2_completed END,
                    realized_profit = realized_profit + ?,
                    fees = fees + ?,
                    slippage = slippage + ?,
                    status = ?
                WHERE event_id = ?
                """,
                (remaining, stage, stage, stage, remaining, gross_profit, fees, slippage, status, lot_event_id),
            )
            event_id = f"{lot_event_id}:TP{stage}:{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO ledger_events(
                    event_id, strategy_id, lot_event_id, event_type, symbol,
                    grid_level, event_time, price, quantity, amount_cny, fees,
                    slippage, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    STRATEGY_ID,
                    lot_event_id,
                    f"SIMULATED_TAKE_PROFIT_{stage}",
                    lot["symbol"],
                    lot["grid_level"],
                    current.isoformat(),
                    price,
                    quantity,
                    price * quantity,
                    fees,
                    slippage,
                    json.dumps({"position_scope": PositionType.GRID_POSITION.value}),
                ),
            )
            connection.commit()
        return {"event_id": event_id, "quantity": quantity, "remaining_quantity": remaining, "status": status}

    def used_cash(self, symbol: str | None = None) -> float:
        lots = self.open_lots(symbol)
        return sum(
            float(row["allocated_cash"])
            * int(row["remaining_quantity"])
            / max(1, int(row["simulated_quantity"]))
            for row in lots
        )

    def simulated_buy_amount_since(self, symbol: str, since: datetime) -> float:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(amount_cny), 0) AS amount
                FROM ledger_events
                WHERE symbol = ? AND event_type = 'SIMULATED_BUY' AND event_time >= ?
                """,
                (symbol, _utc(since).isoformat()),
            ).fetchone()
        return float(row["amount"] or 0)

    def active_center(self, symbol: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM reference_centers
                WHERE strategy_id = ? AND symbol = ? AND status = 'ACTIVE'
                ORDER BY confirmed_at DESC, proposed_at DESC LIMIT 1
                """,
                (STRATEGY_ID, symbol),
            ).fetchone()
        return dict(row) if row else None

    def ensure_initial_center(self, symbol: str, center: float, *, now: datetime) -> dict[str, Any]:
        active = self.active_center(symbol)
        if active:
            return active
        current = _utc(now)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO reference_centers(
                    strategy_id, symbol, center, status, reason,
                    proposed_at, confirmed_at
                ) VALUES (?, ?, ?, 'ACTIVE', 'INITIAL_LOWER_OF_PREVIOUS_CLOSE_AND_MA20', ?, ?)
                """,
                (STRATEGY_ID, symbol, float(center), current.isoformat(), current.isoformat()),
            )
        return self.active_center(symbol) or {}

    def propose_center(
        self,
        symbol: str,
        center: float,
        *,
        reason: str,
        now: datetime,
    ) -> int | None:
        active = self.active_center(symbol)
        if not active or float(center) <= float(active["center"]):
            return None
        current = _utc(now)
        month_prefix = current.strftime("%Y-%m")
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT id FROM reference_centers
                WHERE strategy_id = ? AND symbol = ?
                  AND substr(proposed_at, 1, 7) = ?
                  AND reason <> 'INITIAL_LOWER_OF_PREVIOUS_CLOSE_AND_MA20'
                LIMIT 1
                """,
                (STRATEGY_ID, symbol, month_prefix),
            ).fetchone()
            if existing:
                return None
            cursor = connection.execute(
                """
                INSERT INTO reference_centers(
                    strategy_id, symbol, center, status, reason, proposed_at
                ) VALUES (?, ?, ?, 'CANDIDATE', ?, ?)
                """,
                (STRATEGY_ID, symbol, float(center), reason, current.isoformat()),
            )
        return int(cursor.lastrowid)

    def confirm_center(self, center_id: int, *, now: datetime) -> None:
        current = _utc(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                "SELECT * FROM reference_centers WHERE id = ? AND status = 'CANDIDATE'",
                (int(center_id),),
            ).fetchone()
            if not candidate:
                raise ValueError("reference-center candidate not found")
            connection.execute(
                """
                UPDATE reference_centers SET status = 'SUPERSEDED', superseded_at = ?
                WHERE strategy_id = ? AND symbol = ? AND status = 'ACTIVE'
                """,
                (current.isoformat(), STRATEGY_ID, candidate["symbol"]),
            )
            connection.execute(
                """
                UPDATE reference_centers SET status = 'ACTIVE', confirmed_at = ?
                WHERE id = ?
                """,
                (current.isoformat(), int(center_id)),
            )
            connection.commit()

    def center_history(self, symbol: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reference_centers
                WHERE strategy_id = ? AND symbol = ? ORDER BY proposed_at
                """,
                (STRATEGY_ID, symbol),
            ).fetchall()
        return [dict(row) for row in rows]

    def notification_allowed(self, fingerprint: str, *, now: datetime) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cooldown_until FROM notification_state WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return not row or _utc(now) >= _parse_time(str(row["cooldown_until"]))

    def record_notification(
        self,
        fingerprint: str,
        *,
        symbol: str,
        event_type: str,
        now: datetime,
        cooldown_minutes: int,
        sent: bool,
    ) -> None:
        current = _utc(now)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO notification_state(
                    fingerprint, symbol, event_type, last_attempt_at,
                    last_sent_at, cooldown_until
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_sent_at = COALESCE(excluded.last_sent_at, notification_state.last_sent_at),
                    cooldown_until = excluded.cooldown_until
                """,
                (
                    fingerprint,
                    symbol,
                    event_type,
                    current.isoformat(),
                    current.isoformat() if sent else None,
                    (current + timedelta(minutes=int(cooldown_minutes))).isoformat(),
                ),
            )

    def ensure_benchmark(self, symbol: str, *, price: float, budget_cny: float, now: datetime) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO benchmark_state(symbol, baseline_time, baseline_price, budget_cny)
                VALUES (?, ?, ?, ?)
                """,
                (symbol, _utc(now).isoformat(), float(price), float(budget_cny)),
            )

    def record_equity(
        self,
        *,
        observed_at: datetime,
        strategy_value_cny: float,
        benchmark_value_cny: float,
        capital_used_cny: float,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO equity_history(
                    observed_at, strategy_value_cny, benchmark_value_cny, capital_used_cny
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _utc(observed_at).isoformat(),
                    float(strategy_value_cny),
                    float(benchmark_value_cny),
                    float(capital_used_cny),
                ),
            )

    def performance_summary(
        self,
        *,
        current_prices: Mapping[str, float],
        total_budget_cny: float,
        pause_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            lots = [dict(row) for row in connection.execute("SELECT * FROM grid_lots").fetchall()]
            benchmarks = [dict(row) for row in connection.execute("SELECT * FROM benchmark_state").fetchall()]
            equity = [dict(row) for row in connection.execute("SELECT * FROM equity_history ORDER BY observed_at").fetchall()]
            trade_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM ledger_events WHERE event_type LIKE 'SIMULATED_%'"
                ).fetchone()[0]
            )
            failures = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM evaluations
                    WHERE status = 'GRID_BLOCKED'
                      AND generated_at >= ?
                    """,
                    ((datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat(),),
                ).fetchone()[0]
            )
            meta = connection.execute(
                "SELECT value FROM strategy_meta WHERE key = 'strategy_id'"
            ).fetchone()
        realized = sum(float(row["realized_profit"]) for row in lots)
        unrealized = sum(
            (float(current_prices.get(str(row["symbol"]), row["simulated_entry_price"]))
             - float(row["simulated_entry_price"]))
            * int(row["remaining_quantity"])
            for row in lots
        )
        fees = sum(float(row["fees"]) for row in lots)
        slippage = sum(float(row["slippage"]) for row in lots)
        net = realized + unrealized - fees - slippage
        used = self.used_cash()
        benchmark_value = 0.0
        for row in benchmarks:
            price = float(current_prices.get(str(row["symbol"]), row["baseline_price"]))
            benchmark_value += float(row["budget_cny"]) * price / float(row["baseline_price"])
        buy_hold_return = (
            benchmark_value / float(total_budget_cny) - 1.0
            if benchmark_value and total_budget_cny else 0.0
        )
        strategy_return = net / float(total_budget_cny) if total_budget_cny else 0.0
        max_drawdown = _max_drawdown([float(row["strategy_value_cny"]) for row in equity])
        benchmark_drawdown = _max_drawdown([float(row["benchmark_value_cny"]) for row in equity])
        maximum_occupied = max([used, *[float(row["capital_used_cny"]) for row in equity]])
        config = pause_config or {}
        pause_reasons: list[str] = []
        if equity:
            history_days = (
                _parse_time(str(equity[-1]["observed_at"]))
                - _parse_time(str(equity[0]["observed_at"]))
            ).days
            if (
                history_days >= int(config.get("underperformance_months", 12)) * 30
                and buy_hold_return - strategy_return
                > float(config.get("underperformance_threshold_pct", 3)) / 100.0
            ):
                pause_reasons.append("12_MONTH_UNDERPERFORMANCE")
        if abs(max_drawdown) - abs(benchmark_drawdown) > float(
            config.get("drawdown_excess_threshold_pct", 5)
        ) / 100.0:
            pause_reasons.append("DRAWDOWN_WORSE_THAN_BUY_HOLD")
        if failures >= int(config.get("data_quality_failure_threshold", 5)):
            pause_reasons.append("PERSISTENT_DATA_QUALITY_FAILURE")
        if not meta or str(meta["value"]) != STRATEGY_ID:
            pause_reasons.append("STRATEGY_VERSION_NOT_REVALIDATED")
        return {
            "strategy_id": STRATEGY_ID,
            "simulation_only": True,
            "cumulative_net_profit_cny": round(net, 2),
            "realized_profit_cny": round(realized, 2),
            "unrealized_profit_cny": round(unrealized, 2),
            "fees_cny": round(fees, 2),
            "slippage_cny": round(slippage, 2),
            "maximum_capital_occupied_cny": round(maximum_occupied, 2),
            "maximum_drawdown": round(max_drawdown, 6),
            "trade_count": trade_count,
            "buy_hold_return": round(buy_hold_return, 6),
            "strategy_return": round(strategy_return, 6),
            "excess_return_vs_buy_hold": round(strategy_return - buy_hold_return, 6),
            "pause_reasons": pause_reasons,
        }

    def table_count(self, table: str) -> int:
        allowed = {
            "evaluations",
            "grid_lots",
            "ledger_events",
            "reference_centers",
            "notification_state",
            "benchmark_state",
            "equity_history",
        }
        if table not in allowed:
            raise ValueError("unsupported table")
        with self._connection() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("grid timestamp must include timezone")
    return value.astimezone(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored grid timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            drawdown = min(drawdown, value / peak - 1.0)
    return drawdown
