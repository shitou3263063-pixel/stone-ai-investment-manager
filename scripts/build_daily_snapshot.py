from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.preflight_check import (  # noqa: E402
    EXECUTION_STATE_PATH,
    MASTER_PATH,
    evaluate_dqs,
    load_json,
    load_yaml,
    natural_week_id,
    run_preflight,
)
from src.data_sources.source_audit import build_and_write_source_audit  # noqa: E402
from utils.market_data_provider import fetch_yfinance_market_data  # noqa: E402


SNAPSHOT_PATH = PROJECT_ROOT / "data" / "daily_snapshot.json"


def _portfolio_summary(master: dict[str, Any]) -> dict[str, Any]:
    totals = master.get("totals", {}) or {}
    total_assets = float(totals.get("total_assets", 0) or 0)
    labels = master.get("asset_class_labels", {}) or {}
    classes = {}
    for key in ["us_stock", "hk_stock", "cn_stock", "china_bond", "gold", "cash"]:
        amount = float(totals.get(key, 0) or 0)
        classes[key] = {
            "label": labels.get(key, key),
            "amount_cny": amount,
            "ratio": amount / total_assets if total_assets else 0.0,
        }
    return {
        "total_assets_cny": total_assets,
        "asset_classes": classes,
        "confirmed_quantities": master.get("confirmed_quantities", {}) or {},
        "notes": master.get("notes", []) or [],
    }


def _market_snapshot() -> dict[str, Any]:
    try:
        return fetch_yfinance_market_data()
    except Exception as exc:  # noqa: BLE001 - snapshot generation must not crash
        return {
            "source": "unavailable",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "items": {},
            "macro": {},
            "news": {},
            "data_quality": {
                "score": 0,
                "critical_missing": True,
                "blocking_errors": [f"市场数据快照生成失败：{exc}"],
                "warnings": [str(exc)],
            },
            "errors": [str(exc)],
        }


def _task_gates(day: date, dqs: dict[str, Any], execution_state: dict[str, Any]) -> dict[str, Any]:
    weekday = day.weekday()  # Monday=0, Wednesday=2
    week_id = natural_week_id(day)
    records = execution_state.get("records", []) or []
    base_dca_count = sum(
        1
        for item in records
        if item.get("natural_week") == week_id
        and item.get("order_type") == "base_dca"
        and item.get("status") in {"suggested", "pending_confirmation", "executed"}
    )
    is_wednesday = weekday == 2
    dqs_ok_for_any_trade = bool(dqs.get("trade_advice_allowed"))
    return {
        "daily_cio": {
            "role": "候选建议汇总，不做最终裁决。",
            "trade_gate": dqs_ok_for_any_trade and (is_wednesday or False),
            "rule": "非周三且无特殊机会时默认不交易；Codex只输出候选，最终由GPT复核。",
        },
        "wednesday_dca": {
            "role": "唯一基础定投任务。",
            "trade_gate": dqs_ok_for_any_trade and is_wednesday and base_dca_count == 0,
            "base_dca_count_this_week": base_dca_count,
            "rule": "同一自然周最多一次基础定投。",
        },
        "risk_committee": {
            "role": "只在重大风险时通知。",
            "notify_gate": bool(dqs.get("blocking_errors")) or dqs.get("score", 0) < 70,
            "rule": "低DQS或blocking_errors触发风险通知；不输出交易裁决。",
        },
        "global_monitor": {
            "role": "只处理独立高价值机会。",
            "trade_gate": False,
            "rule": "默认不交易；仅当独立机会通过DQS和GPT复核后进入待确认。",
        },
        "weekly_review": {
            "role": "核对建议、确认和实际成交。",
            "reconcile_gate": True,
            "rule": "未确认交易不得入账；Gmail不得作为成交事实唯一来源。",
        },
    }


def build_snapshot(snapshot_date: date | None = None) -> dict[str, Any]:
    snapshot_date = snapshot_date or date.today()
    master = load_yaml(MASTER_PATH)
    execution_state = load_json(EXECUTION_STATE_PATH)
    market = _market_snapshot()
    market, source_audit = build_and_write_source_audit(market)
    quality = market.get("data_quality", {}) or {}
    blocking_errors = list(quality.get("blocking_errors", []) or [])
    if quality.get("critical_missing"):
        blocking_errors.append("关键市场数据缺失。")
    preflight = run_preflight(snapshot_path=Path("__snapshot_not_written_yet__.json"))
    if preflight.get("blocking_errors"):
        blocking_errors.extend(preflight["blocking_errors"])
    dqs = evaluate_dqs(int(quality.get("score", 0) or 0), blocking_errors=blocking_errors)

    return {
        "schema_version": 1,
        "as_of": snapshot_date.isoformat(),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "data_priority": [
            "portfolio_master.yaml",
            "broker_and_trade_records",
            "execution_state.json",
            "market_data",
            "codex_candidate_suggestions",
            "gmail_reports",
            "news_explanation",
        ],
        "portfolio": _portfolio_summary(master),
        "execution_state": execution_state,
        "market": market,
        "source_audit": source_audit,
        "dqs": dqs,
        "preflight": preflight,
        "task_gates": _task_gates(snapshot_date, dqs, execution_state),
        "broker_status": {
            "IBKR": "not_connected",
            "note": "IBKR未接通；券商成交记录尚不能自动入账。",
        },
        "gmail_policy": "Gmail只作为报告和通知渠道，不得作为资产事实或成交事实的唯一来源。",
    }


def write_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    snapshot = build_snapshot()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def main() -> int:
    snapshot = write_snapshot()
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
