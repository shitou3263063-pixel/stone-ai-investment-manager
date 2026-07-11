from __future__ import annotations

from datetime import date, datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASTER_PATH = PROJECT_ROOT / "data" / "portfolio_master.yaml"
EXECUTION_STATE_PATH = PROJECT_ROOT / "data" / "execution_state.json"
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "daily_snapshot.json"
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def natural_week_id(day: date | None = None) -> str:
    day = day or date.today()
    iso_year, iso_week, _ = day.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def asset_total_cny(master: dict[str, Any]) -> int:
    totals = master.get("totals", {}) or {}
    keys = ["us_stock", "hk_stock", "cn_stock", "china_bond", "gold", "cash"]
    return int(sum(float(totals.get(key, 0) or 0) for key in keys))


def declared_total_cny(master: dict[str, Any]) -> int:
    return int(float((master.get("totals", {}) or {}).get("total_assets", 0) or 0))


def check_asset_reconciliation(master: dict[str, Any]) -> dict[str, Any]:
    calculated = asset_total_cny(master)
    declared = declared_total_cny(master)
    ok = calculated == declared
    return {
        "name": "asset_reconciliation",
        "status": "OK" if ok else "ERROR",
        "calculated_total_cny": calculated,
        "declared_total_cny": declared,
        "message": "资产合计与总资产一致。" if ok else "资产合计与总资产不一致，停止执行单。",
    }


def check_duplicate_bond(master: dict[str, Any]) -> dict[str, Any]:
    quantities = master.get("confirmed_quantities", {}) or {}
    duplicated = []
    for name, item in quantities.items():
        if str(item.get("asset_class")) != "china_bond":
            continue
        if item.get("included_in") == "中国债券" and item.get("duplicate_counting_allowed") is False:
            continue
        if item.get("amount_cny_approx"):
            duplicated.append(name)
    ok = not duplicated
    return {
        "name": "duplicate_bond_counting",
        "status": "OK" if ok else "ERROR",
        "duplicated_items": duplicated,
        "message": "10年地债已包含在中国债券中，未重复计算。" if ok else "发现可能重复计入的债券明细。",
    }


def check_cash_floor(master: dict[str, Any], floor_ratio: float = 0.05) -> dict[str, Any]:
    totals = master.get("totals", {}) or {}
    total_assets = float(totals.get("total_assets", 0) or 0)
    cash = float(totals.get("cash", 0) or 0)
    ratio = cash / total_assets if total_assets else 0.0
    ok = ratio >= floor_ratio
    return {
        "name": "cash_floor",
        "status": "OK" if ok else "ERROR",
        "cash_cny": cash,
        "cash_ratio": ratio,
        "floor_ratio": floor_ratio,
        "message": f"现金占比{ratio:.2%}，高于底线。" if ok else f"现金占比{ratio:.2%}低于底线，停止风险资产加仓。",
    }


def records_for_week(execution_state: dict[str, Any], week_id: str, order_type: str = "base_dca") -> list[dict[str, Any]]:
    records = execution_state.get("records", []) or []
    return [
        item
        for item in records
        if item.get("natural_week") == week_id
        and item.get("order_type") == order_type
        and item.get("status") in {"suggested", "pending_confirmation", "executed"}
    ]


def check_weekly_base_dca(execution_state: dict[str, Any], day: date | None = None) -> dict[str, Any]:
    week_id = natural_week_id(day)
    records = records_for_week(execution_state, week_id, "base_dca")
    ok = len(records) <= 1
    return {
        "name": "weekly_base_dca_once",
        "status": "OK" if ok else "ERROR",
        "natural_week": week_id,
        "base_dca_count": len(records),
        "message": "本周基础定投未重复。" if ok else "同一自然周出现多次基础定投，停止执行单。",
    }


def check_unconfirmed_not_booked(execution_state: dict[str, Any]) -> dict[str, Any]:
    records = execution_state.get("records", []) or []
    offenders = [
        item.get("id", "unknown")
        for item in records
        if item.get("status") in {"suggested", "pending_confirmation"}
        and bool(item.get("booked_to_portfolio"))
    ]
    ok = not offenders
    return {
        "name": "unconfirmed_trade_not_booked",
        "status": "OK" if ok else "ERROR",
        "offenders": offenders,
        "message": "未确认交易未入账。" if ok else "存在未确认交易被入账，停止执行单。",
    }


def check_data_freshness(snapshot: dict[str, Any] | None = None, max_age_days: int = 1) -> dict[str, Any]:
    snapshot = snapshot or {}
    as_of = str(snapshot.get("as_of", "") or "")
    if not as_of:
        return {
            "name": "data_freshness",
            "status": "WARN",
            "message": "尚未生成每日快照；本次只能按保守规则运行。",
        }
    try:
        snapshot_day = datetime.fromisoformat(as_of[:10]).date()
        age = (date.today() - snapshot_day).days
    except ValueError:
        return {"name": "data_freshness", "status": "WARN", "message": "每日快照日期格式不可识别。"}
    ok = age <= max_age_days
    return {
        "name": "data_freshness",
        "status": "OK" if ok else "WARN",
        "age_days": age,
        "message": "每日快照时效正常。" if ok else "每日快照可能过期，建议谨慎使用。",
    }


def evaluate_dqs(score: int | float, blocking_errors: list[str] | None = None) -> dict[str, Any]:
    blocking_errors = blocking_errors or []
    score_value = int(score)
    if blocking_errors:
        decision = "blocked"
        precise_amount_allowed = False
        trade_advice_allowed = False
        message = "blocking_errors 非空，停止执行单。"
    elif score_value >= 85:
        decision = "precise_amount_allowed"
        precise_amount_allowed = True
        trade_advice_allowed = True
        message = "DQS>=85，允许输出精确金额。"
    elif score_value >= 70:
        decision = "direction_or_cap_only"
        precise_amount_allowed = False
        trade_advice_allowed = True
        message = "DQS 70-84，只允许方向、比例或金额上限。"
    else:
        decision = "no_trade_advice"
        precise_amount_allowed = False
        trade_advice_allowed = False
        message = "DQS<70，不得给交易建议。"
    return {
        "score": score_value,
        "blocking_errors": blocking_errors,
        "decision": decision,
        "precise_amount_allowed": precise_amount_allowed,
        "trade_advice_allowed": trade_advice_allowed,
        "message": message,
    }


def check_ibkr_status(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    status = (snapshot.get("broker_status", {}) or {}).get("IBKR", "not_connected")
    return {
        "name": "ibkr_status",
        "status": "OK" if status == "connected" else "WARN",
        "ibkr_status": status,
        "message": "IBKR 已连接。" if status == "connected" else "IBKR 未接通，成交事实不能来自券商自动回填。",
    }


def check_market_status(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    dqs = snapshot.get("dqs", {}) or {}
    score = int(dqs.get("score", 0) or 0)
    return {
        "name": "market_status",
        "status": "OK" if score >= 70 else "WARN",
        "dqs": score,
        "message": "市场数据质量可用。" if score >= 70 else "市场数据质量不足，禁止激进建议。",
    }


def check_environment_variables() -> dict[str, Any]:
    required_for_data = ["FRED_API_KEY"]
    optional = ["ALPHA_VANTAGE_API_KEY", "FINNHUB_API_KEY", "OPENAI_API_KEY"]
    missing_required = [key for key in required_for_data if not os.getenv(key, "").strip()]
    missing_optional = [key for key in optional if not os.getenv(key, "").strip()]
    return {
        "name": "environment_variables",
        "status": "OK" if not missing_required else "WARN",
        "missing_required_for_data": missing_required,
        "missing_optional": missing_optional,
        "message": (
            "核心数据环境变量已配置。"
            if not missing_required
            else "FRED_API_KEY 未配置，宏观官方主数据源未启用；DQS 会自动降级。"
        ),
    }


def _requirement_to_module(requirement: str) -> str:
    name = requirement.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
    return {"PyYAML": "yaml"}.get(name, name.replace("-", "_"))


def check_python_dependencies(requirements_path: Path = REQUIREMENTS_PATH) -> dict[str, Any]:
    if not requirements_path.exists():
        return {"name": "python_dependencies", "status": "ERROR", "message": "requirements.txt 不存在。"}
    requirements = [
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    missing = [item for item in requirements if importlib.util.find_spec(_requirement_to_module(item)) is None]
    return {
        "name": "python_dependencies",
        "status": "OK" if not missing else "WARN",
        "requirements": requirements,
        "missing": missing,
        "message": "Python 依赖可导入。" if not missing else f"部分依赖未安装：{', '.join(missing)}。",
    }


def check_git_status() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - preflight must report, not crash
        return {"name": "git_status", "status": "WARN", "message": f"Git 状态检查失败：{exc}"}
    if result.returncode != 0:
        return {"name": "git_status", "status": "WARN", "message": result.stderr.strip() or "Git 状态不可用。"}
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "name": "git_status",
        "status": "OK" if not changed else "WARN",
        "changed_count": len(changed),
        "message": "Git 工作区干净。" if not changed else f"Git 存在 {len(changed)} 项未提交修改；上线前应提交到独立分支。",
    }


def check_data_source_connection_status(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    market = snapshot.get("market", {}) or {}
    items = market.get("items", {}) or {}
    macro_items = ((market.get("macro", {}) or {}).get("items", {}) or {})
    fred_connected = any(item.get("source") == "fred" and item.get("status") != "missing" for item in macro_items.values())
    yfinance_connected = any(item.get("source") == "yfinance" and item.get("status") == "ok" for item in items.values())
    cache_available = any(str(item.get("source", "")).startswith("cache:") for item in items.values())
    audit = snapshot.get("source_audit", {}) or {}
    return {
        "name": "data_source_connections",
        "status": "OK" if fred_connected and (yfinance_connected or cache_available) else "WARN",
        "fred": "connected" if fred_connected else "not_connected",
        "yfinance": "connected" if yfinance_connected else "not_connected",
        "local_cache": "available" if cache_available else "not_available",
        "source_audit_scan": audit.get("scan_status", "unknown"),
        "dqs_cap": audit.get("dqs_cap"),
        "message": (
            "FRED 与日常行情源可用。"
            if fred_connected and (yfinance_connected or cache_available)
            else "数据源未完全接通；系统会使用缓存/手动数据并限制 DQS。"
        ),
    }


def _legacy_check_source_audit_status(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    audit = snapshot.get("source_audit", {}) or {}
    if not audit:
        return {
            "name": "source_audit",
            "status": "WARN",
            "message": "尚未生成 source_audit.json；不得声称全球权威数据扫描完成。",
        }
    scan_complete = bool(audit.get("scan_complete"))
    tier1_coverage = float(audit.get("tier1_coverage", 0.0) or 0.0)
    critical_coverage = float(audit.get("critical_metric_coverage", 0.0) or 0.0)
    ok = scan_complete and tier1_coverage >= 0.80 and critical_coverage >= 0.85
    return {
        "name": "source_audit",
        "status": "OK" if ok else "WARN",
        "scan_complete": scan_complete,
        "tier1_coverage": tier1_coverage,
        "critical_metric_coverage": critical_coverage,
        "message": (
            "全球权威数据扫描覆盖率达标。"
            if ok
            else "数据覆盖不足：一级来源覆盖率低于80%或关键指标覆盖率低于85%，禁止精确金额建议。"
        ),
    }


def check_source_audit_status(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    audit = snapshot.get("source_audit", {}) or {}
    if not audit:
        return {
            "name": "source_audit",
            "status": "WARN",
            "message": "尚未生成 source_audit.json；不得声称全球权威数据扫描完成。",
        }

    scan_complete = bool(audit.get("scan_complete"))
    tier1_coverage = float(audit.get("tier1_coverage", 0.0) or 0.0)
    critical_coverage = float(audit.get("critical_metric_coverage", 0.0) or 0.0)
    data_source_coverage = float(audit.get("data_source_coverage", critical_coverage) or 0.0)
    dual_source_coverage = float(audit.get("dual_source_coverage", 0.0) or 0.0)
    blocking_errors = list(audit.get("blocking_errors", []) or [])
    verification_warnings = list(audit.get("verification_warnings", []) or [])

    ok = scan_complete and data_source_coverage >= 0.85 and not blocking_errors and not verification_warnings
    if blocking_errors:
        message = "数据源覆盖不足或存在硬错误，禁止交易建议。"
    elif verification_warnings:
        message = "数据源覆盖可用，但双源/一级源验证不足，禁止精确金额建议。"
    elif not scan_complete:
        message = "全球权威数据扫描未完成，不得声称扫描完成。"
    else:
        message = "全球权威数据扫描覆盖率和验证均达标。"

    return {
        "name": "source_audit",
        "status": "OK" if ok else "WARN",
        "scan_complete": scan_complete,
        "tier1_coverage": tier1_coverage,
        "critical_metric_coverage": critical_coverage,
        "data_source_coverage": data_source_coverage,
        "dual_source_coverage": dual_source_coverage,
        "blocking_errors": blocking_errors,
        "verification_warnings": verification_warnings,
        "message": message,
    }


def check_gmail_status() -> dict[str, Any]:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
    missing = [key for key in required if not os.getenv(key, "").strip()]
    return {
        "name": "gmail_status",
        "status": "OK" if not missing else "WARN",
        "missing": missing,
        "message": "Gmail 配置完整。" if not missing else "Gmail 配置不完整，只能生成报告不能发送。",
    }


def run_preflight(
    master_path: Path = MASTER_PATH,
    execution_state_path: Path = EXECUTION_STATE_PATH,
    snapshot_path: Path = SNAPSHOT_PATH,
) -> dict[str, Any]:
    master = load_yaml(master_path)
    execution_state = load_json(execution_state_path)
    snapshot = load_json(snapshot_path) if snapshot_path.exists() else {}

    checks = [
        check_asset_reconciliation(master),
        check_duplicate_bond(master),
        check_cash_floor(master),
        check_environment_variables(),
        check_python_dependencies(),
        check_git_status(),
        check_data_freshness(snapshot),
        check_ibkr_status(snapshot),
        check_market_status(snapshot),
        check_data_source_connection_status(snapshot),
        check_source_audit_status(snapshot),
        check_gmail_status(),
        check_weekly_base_dca(execution_state),
        check_unconfirmed_not_booked(execution_state),
    ]
    system_errors = [item["message"] for item in checks if item["status"] == "ERROR"]
    snapshot_dqs_blocking = list(((snapshot.get("dqs") or {}).get("blocking_errors") or []))
    blocking_errors = system_errors + snapshot_dqs_blocking
    dqs_score = int(((snapshot.get("dqs") or {}).get("score") or 0))
    dqs = evaluate_dqs(dqs_score, blocking_errors)
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "overall_status": "ERROR" if system_errors else ("WARN" if blocking_errors or any(item["status"] == "WARN" for item in checks) else "OK"),
        "checks": checks,
        "system_errors": system_errors,
        "blocking_errors": blocking_errors,
        "dqs": dqs,
    }


def main() -> int:
    result = run_preflight()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["overall_status"] == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
