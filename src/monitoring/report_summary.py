from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from .market_clock import MarketClock


REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
TRUSTED_STATUSES = {"VALID", "DELAYED_VALID"}


def load_intraday_report_summary(
    root: Path,
    *,
    config_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read the monitor database without creating or mutating it."""
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        raise ValueError("report summary time must include a timezone")
    config_file = config_path or root / "config" / "intraday_monitor.yaml"
    try:
        config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return _unavailable(current, f"监控配置不可用：{type(exc).__name__}")
    application = config.get("application") or {}
    storage = config.get("storage") or {}
    db_value = application.get("sqlite_path") or storage.get(
        "sqlite_path", "data/monitoring/intraday_monitor.sqlite3"
    )
    db_path = Path(db_value)
    if not db_path.is_absolute():
        db_path = root / db_path
    if not db_path.is_file():
        return _unavailable(current, "监控SQLite尚不存在")

    assets = _selected_assets(config)
    maximum_age = float(application.get("report_snapshot_max_age_minutes", 30))
    tolerances = config.get("change_calculation") or {}
    try:
        connection = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        rows = _latest_rows(connection, tuple(assets))
        health = _one(
            connection,
            "SELECT * FROM source_health WHERE lower(source) = 'futu' LIMIT 1",
        )
        run = _one(
            connection,
            "SELECT * FROM monitor_runs ORDER BY ended_at DESC LIMIT 1",
        )
        items = [
            _row_summary(
                connection,
                row,
                assets[str(row["symbol"])],
                current,
                maximum_age,
                int(tolerances.get("five_minute_tolerance_seconds", 120)),
                int(tolerances.get("fifteen_minute_tolerance_seconds", 180)),
            )
            for row in rows
        ]
    except (sqlite3.Error, ValueError, KeyError, json.JSONDecodeError) as exc:
        return _unavailable(current, f"监控SQLite读取失败：{type(exc).__name__}")
    finally:
        if "connection" in locals():
            connection.close()

    latest_age = min(
        (item["snapshot_age_minutes"] for item in items),
        default=None,
    )
    if not items:
        overall = "DATA_UNAVAILABLE"
    elif all(item["validity_status"] == "STALE" for item in items):
        overall = "STALE"
    else:
        overall = "VALID"
    anomaly_count = 0
    if run:
        anomaly_count = sum(
            int(run.get(key) or 0)
            for key in ("stale_count", "conflict_count", "error_count")
        )
    return {
        "status": overall,
        "generated_at": current.astimezone(REPORT_TIMEZONE).isoformat(),
        "timezone": "Asia/Shanghai",
        "futu_connection_status": str((health or {}).get("status") or "UNAVAILABLE"),
        "monitored_symbol_count": len(items),
        "configured_symbol_count": len(assets),
        "latest_round_id": (run or {}).get("round_id") or "-",
        "round_anomaly_count": anomaly_count,
        "latest_snapshot_age_minutes": latest_age,
        "items": items,
        "notice": (
            ""
            if overall == "VALID"
            else "盘中数据不足，本节仅作观察，不参与主动交易判断。"
        ),
    }


def render_intraday_report_summary(summary: Mapping[str, Any]) -> str:
    lines = [
        "## 盘中监控摘要",
        "",
        f"- 状态：**{summary.get('status', 'DATA_UNAVAILABLE')}**",
        f"- 数据生成时间：{summary.get('generated_at', '-')}（{summary.get('timezone', 'Asia/Shanghai')}）",
        f"- Futu OpenD连接状态：{summary.get('futu_connection_status', 'UNAVAILABLE')}",
        f"- 监控标的：{summary.get('monitored_symbol_count', 0)} / {summary.get('configured_symbol_count', 0)}",
        f"- 最近监控轮次ID：{summary.get('latest_round_id', '-')}",
        f"- 本轮异常数：{summary.get('round_anomaly_count', 0)}",
        "",
        "| symbol | market | session | price | day_change_pct | 5分钟变化 | 15分钟变化 | quote_time | delay_seconds | validity_status | source |",
        "|---|---|---|---:|---:|---:|---:|---|---:|---|---|",
    ]
    for item in summary.get("items", []) or []:
        lines.append(
            "| {symbol} | {market} | {session} | {price} | {day} | {five} | {fifteen} | "
            "{quote_time} | {delay} | {validity} | {source} |".format(
                symbol=item.get("symbol", "-"),
                market=item.get("market", "-"),
                session=item.get("session", "-"),
                price=_number(item.get("price"), 4),
                day=_percent(item.get("day_change_pct")),
                five=_percent(item.get("change_5m")),
                fifteen=_percent(item.get("change_15m")),
                quote_time=item.get("quote_time") or "-",
                delay=_number(item.get("delay_seconds"), 0),
                validity=item.get("validity_status", "DATA_UNAVAILABLE"),
                source=item.get("source", "-"),
            )
        )
    if not summary.get("items"):
        lines.append("| - | - | - | - | - | - | - | - | - | DATA_UNAVAILABLE | - |")
    notice = str(summary.get("notice") or "")
    if notice:
        lines.extend(["", notice])
    lines.extend(
        [
            "",
            "本节仅提供盘中观察信息，不改变总资产、DQS、Risk Score、FinalDecisionBundle或最终操作建议。",
        ]
    )
    return "\n".join(lines)


def _selected_assets(config: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    application = config.get("application") or {}
    wanted = {str(value).upper() for value in application.get("symbols", []) or []}
    selected: dict[str, dict[str, str]] = {}
    for raw in config.get("symbols", []) or []:
        symbol = str(raw.get("symbol") or "")
        aliases = {symbol.upper()}
        if symbol.upper().endswith(".HK"):
            local = symbol.upper()[:-3]
            aliases.add(local)
            aliases.add(local.lstrip("0") or "0")
        if wanted and not (wanted & aliases):
            continue
        selected[symbol] = {
            "market": str(raw.get("market") or ""),
            "asset_name": str(raw.get("asset_name") or symbol),
        }
    return selected


def _latest_rows(connection: sqlite3.Connection, assets: tuple[str, ...]) -> list[sqlite3.Row]:
    if not assets:
        return []
    placeholders = ",".join("?" for _ in assets)
    return connection.execute(
        f"""
        SELECT symbol, captured_at, session_date, data_status, payload_json
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY captured_at DESC) AS rank
            FROM snapshots WHERE symbol IN ({placeholders})
        ) WHERE rank = 1 ORDER BY symbol
        """,
        assets,
    ).fetchall()


def _row_summary(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    asset: Mapping[str, str],
    now: datetime,
    maximum_age_minutes: float,
    five_tolerance: int,
    fifteen_tolerance: int,
) -> dict[str, Any]:
    payload = json.loads(str(row["payload_json"]))
    captured = _parse_time(str(row["captured_at"]))
    quote_time = _parse_optional_time(payload.get("timestamp"))
    delay = None if quote_time is None else max(0.0, (_utc(now) - _utc(quote_time)).total_seconds())
    age_minutes = max(0.0, (_utc(now) - captured).total_seconds() / 60.0)
    market = str(asset["market"]).upper()
    market_status = MarketClock().status(market, now)
    stored_status = str(row["data_status"])
    if age_minutes > maximum_age_minutes or bool(payload.get("is_stale")):
        validity = "STALE"
    elif stored_status == "VALID" and not market_status.is_trading:
        validity = "CLOSED_VALID"
    elif stored_status == "VALID":
        validity = "REALTIME_VALID" if delay is not None and delay <= 90 else "DELAYED_VALID"
    else:
        validity = stored_status
    return {
        "symbol": str(row["symbol"]),
        "market": market,
        "session": market_status.phase.value,
        "price": payload.get("price"),
        "day_change_pct": payload.get("change_percent"),
        "change_5m": _historical_change(
            connection, row, 5, five_tolerance
        ),
        "change_15m": _historical_change(
            connection, row, 15, fifteen_tolerance
        ),
        "quote_time": quote_time.isoformat() if quote_time else None,
        "delay_seconds": delay,
        "validity_status": validity,
        "source": payload.get("source") or "unavailable",
        "snapshot_age_minutes": age_minutes,
    }


def _historical_change(
    connection: sqlite3.Connection,
    current: sqlite3.Row,
    minutes: int,
    tolerance_seconds: int,
) -> float | None:
    if str(current["data_status"]) not in TRUSTED_STATUSES:
        return None
    payload = json.loads(str(current["payload_json"]))
    price = payload.get("price")
    if price in {None, 0}:
        return None
    current_at = _parse_time(str(current["captured_at"]))
    target = current_at - timedelta(minutes=minutes)
    rows = connection.execute(
        """
        SELECT captured_at, payload_json FROM snapshots
        WHERE symbol = ? AND session_date = ? AND data_status IN ('VALID', 'DELAYED_VALID')
          AND captured_at < ?
        """,
        (current["symbol"], current["session_date"], current["captured_at"]),
    ).fetchall()
    matches = [
        row
        for row in rows
        if abs((_parse_time(str(row["captured_at"])) - target).total_seconds()) <= tolerance_seconds
    ]
    if not matches:
        return None
    closest = min(
        matches,
        key=lambda row: abs((_parse_time(str(row["captured_at"])) - target).total_seconds()),
    )
    reference = json.loads(str(closest["payload_json"])).get("price")
    if reference in {None, 0}:
        return None
    return (float(price) / float(reference) - 1.0) * 100.0


def _one(connection: sqlite3.Connection, query: str) -> dict[str, Any] | None:
    row = connection.execute(query).fetchone()
    return dict(row) if row else None


def _unavailable(now: datetime, reason: str) -> dict[str, Any]:
    return {
        "status": "DATA_UNAVAILABLE",
        "generated_at": now.astimezone(REPORT_TIMEZONE).isoformat(),
        "timezone": "Asia/Shanghai",
        "futu_connection_status": "UNAVAILABLE",
        "monitored_symbol_count": 0,
        "configured_symbol_count": 8,
        "latest_round_id": "-",
        "round_anomaly_count": 0,
        "items": [],
        "notice": f"盘中数据不足，本节仅作观察，不参与主动交易判断。原因：{reason}",
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("SQLite monitor timestamp lacks timezone")
    return _utc(parsed)


def _parse_optional_time(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return value.astimezone(timezone.utc)


def _number(value: Any, digits: int) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"


def _percent(value: Any) -> str:
    return "-" if value is None else f"{float(value):+.2f}%"
