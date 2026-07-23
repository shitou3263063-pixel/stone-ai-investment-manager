from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

import yaml


def load_grid_strategy_summary(
    root: str | Path,
    *,
    config_path: str | Path = "config/long_term_grid.yaml",
) -> dict[str, Any]:
    """Read the isolated simulation ledger without creating or modifying it."""
    project_root = Path(root)
    configured_path = Path(config_path)
    if not configured_path.is_absolute():
        configured_path = project_root / configured_path
    try:
        config = yaml.safe_load(configured_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return _unavailable_summary()
    database_path = Path(
        (config.get("storage") or {}).get(
            "sqlite_path", "data/grid_strategy/long_term_grid_v1.sqlite3"
        )
    )
    if not database_path.is_absolute():
        database_path = project_root / database_path
    if not database_path.is_file():
        return _unavailable_summary()

    try:
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        with connection:
            latest = connection.execute(
                """
                SELECT payload_json FROM (
                    SELECT payload_json,
                           ROW_NUMBER() OVER(
                               PARTITION BY symbol ORDER BY generated_at DESC
                           ) AS row_rank
                    FROM evaluations
                ) WHERE row_rank = 1 ORDER BY json_extract(payload_json, '$.symbol')
                """
            ).fetchall()
            lots = connection.execute("SELECT * FROM grid_lots").fetchall()
            trade_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM ledger_events WHERE event_type LIKE 'SIMULATED_%'"
                ).fetchone()[0]
            )
            equity = connection.execute(
                "SELECT * FROM equity_history ORDER BY observed_at"
            ).fetchall()
            benchmarks = connection.execute("SELECT * FROM benchmark_state").fetchall()
            try:
                runtime_rows = connection.execute(
                    "SELECT * FROM runtime_inputs ORDER BY name"
                ).fetchall()
            except sqlite3.OperationalError:
                runtime_rows = []
    except (sqlite3.Error, OSError, json.JSONDecodeError):
        return _unavailable_summary()
    finally:
        if "connection" in locals():
            connection.close()

    items = [json.loads(str(row["payload_json"])) for row in latest]
    realized = sum(float(row["realized_profit"]) for row in lots)
    fees = sum(float(row["fees"]) for row in lots)
    slippage = sum(float(row["slippage"]) for row in lots)
    latest_prices = {
        str(item["symbol"]): float(item["current_price"])
        for item in items
        if item.get("current_price") is not None
    }
    unrealized = sum(
        (
            latest_prices.get(str(row["symbol"]), float(row["simulated_entry_price"]))
            - float(row["simulated_entry_price"])
        )
        * int(row["remaining_quantity"])
        for row in lots
    )
    net = realized + unrealized - fees - slippage
    used_cash = sum(
        float(row["allocated_cash"])
        * int(row["remaining_quantity"])
        / max(1, int(row["simulated_quantity"]))
        for row in lots
        if str(row["status"]) in {"OPEN", "PARTIAL"}
    )
    maximum_occupied = max(
        [used_cash, *[float(row["capital_used_cny"]) for row in equity]]
    )
    strategy_values = [float(row["strategy_value_cny"]) for row in equity]
    max_drawdown = _max_drawdown(strategy_values)
    total_budget = float((config.get("budget") or {}).get("total_cny", 60000))
    benchmark_value = sum(
        float(row["budget_cny"])
        * latest_prices.get(str(row["symbol"]), float(row["baseline_price"]))
        / float(row["baseline_price"])
        for row in benchmarks
    )
    buy_hold_return = (
        benchmark_value / total_budget - 1.0
        if benchmark_value and total_budget
        else 0.0
    )
    strategy_return = net / total_budget if total_budget else 0.0
    runtime_inputs = {
        str(row["name"]): {
            "value": row["value"],
            "source": row["source"],
            "as_of": row["as_of"],
            "age_minutes": row["age_minutes"],
            "validity": row["validity"],
            "unavailable_reason": row["unavailable_reason"],
            "fallback_used": bool(row["fallback_used"]),
            "fallback_source": row["fallback_source"],
            "updated_at": row["updated_at"],
        }
        for row in runtime_rows
    }
    return {
        "status": "AVAILABLE" if items else "DATA_INSUFFICIENT",
        "strategy_id": "LONG_TERM_GRID_V1",
        "simulation_only": True,
        "automatic_trading": False,
        "items": items,
        "runtime_inputs": runtime_inputs,
        "metrics": {
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
            "excess_return_vs_buy_hold": round(
                strategy_return - buy_hold_return, 6
            ),
        },
        "notice": (
            "候选仅为模拟观察，未成交；模拟盈亏不计入真实组合收益。"
            if items
            else "网格数据不足"
        ),
    }


def render_grid_strategy_summary(summary: Mapping[str, Any]) -> str:
    lines = [
        "## 网格策略观察",
        "",
        "- 模式：SIMULATION_ONLY",
        "- 自动交易：关闭（NO_AUTOMATIC_TRADING）",
        "- 仓位范围：仅 GRID_POSITION；不得卖出 CORE_POSITION 或 DCA_POSITION",
    ]
    items = list(summary.get("items") or [])
    runtime_inputs = summary.get("runtime_inputs") or {}
    if runtime_inputs:
        lines.extend(["", "### 当前风险输入"])
        for name in ("dqs", "risk_score", "usd_cny"):
            detail = runtime_inputs.get(name) or {}
            lines.append(
                f"- {name}: value={detail.get('value')}, source={detail.get('source') or '-'}, "
                f"as_of={detail.get('as_of') or '-'}, age_minutes={detail.get('age_minutes')}, "
                f"validity={detail.get('validity') or 'MISSING'}, "
                f"unavailable_reason={detail.get('unavailable_reason') or '-'}"
            )
    if not items:
        lines.extend(
            [
                "- 网格数据不足",
                "- 模拟盈亏不计入真实组合收益，也不改变日报投资结论。",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "| 标的 | 最新状态 | 当前价 | 参考中心 | 档位 | 模拟候选金额(CNY) | 阻断原因 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in items:
        reasons = ", ".join(str(value) for value in item.get("blocked_reasons") or []) or "-"
        lines.append(
            f"| {item.get('symbol', '-')} | {item.get('status', '-')} | "
            f"{_display(item.get('current_price'))} | "
            f"{_display(item.get('reference_center'))} | "
            f"{item.get('grid_level') if item.get('grid_level') is not None else '-'} | "
            f"{_display(item.get('adjusted_amount_cny'))} | {reasons} |"
        )
    metrics = summary.get("metrics") or {}
    lines.extend(
        [
            "",
            f"- 网格累计净收益（模拟）：{_display(metrics.get('cumulative_net_profit_cny'))} 元",
            f"- 已实现 / 未实现（模拟）：{_display(metrics.get('realized_profit_cny'))} / {_display(metrics.get('unrealized_profit_cny'))} 元",
            f"- 手续费 / 滑点（模拟）：{_display(metrics.get('fees_cny'))} / {_display(metrics.get('slippage_cny'))} 元",
            f"- 最大资金占用（模拟）：{_display(metrics.get('maximum_capital_occupied_cny'))} 元",
            f"- 最大回撤（模拟）：{_percent(metrics.get('maximum_drawdown'))}",
            f"- 交易事件数（模拟）：{int(metrics.get('trade_count') or 0)}",
            f"- 同期买入持有收益：{_percent(metrics.get('buy_hold_return'))}",
            f"- 相对买入持有超额收益：{_percent(metrics.get('excess_return_vs_buy_hold'))}",
            "- 候选仅为模拟观察，不能描述为已成交。",
            "- 模拟盈亏不计入真实组合收益，不改变总资产、DQS、Risk Score、FinalDecisionBundle 或最终操作建议。",
        ]
    )
    return "\n".join(lines)


def _unavailable_summary() -> dict[str, Any]:
    return {
        "status": "DATA_INSUFFICIENT",
        "strategy_id": "LONG_TERM_GRID_V1",
        "simulation_only": True,
        "automatic_trading": False,
        "items": [],
        "runtime_inputs": {},
        "metrics": {},
        "notice": "网格数据不足",
    }


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


def _display(value: Any) -> str:
    return "-" if value is None else f"{float(value):,.2f}"


def _percent(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2%}"
