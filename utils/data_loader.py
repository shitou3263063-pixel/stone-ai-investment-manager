from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.portfolio import load_portfolio as _load_portfolio


def _to_number_or_text(value: Any) -> Any:
    text = str(value or "").strip()
    if text == "":
        return ""
    if text.lower() in {"null", "none", "~"}:
        return None
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _load_yaml_without_dependency(path: Path) -> dict[str, Any]:
    """Minimal YAML reader used only when PyYAML is unavailable.

    It supports the subset this project uses: nested dictionaries with
    `key: value` lines. Lists are ignored because they are not needed for
    portfolio totals or hard data-quality gates.
    """

    result: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("- ") or ":" not in line:
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key, value = line.strip().split(":", 1)
        key = key.strip().strip('"').strip("'")
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else result

        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _to_number_or_text(value)

    return result


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        import yaml  # type: ignore

        with config_path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except ImportError:
        return _load_yaml_without_dependency(config_path)


def load_portfolio(path: str | Path) -> list[dict[str, Any]]:
    portfolio_path = Path(path)
    master_path = portfolio_path.with_name("portfolio_master.yaml")
    if master_path.exists():
        return _load_portfolio_master(master_path)
    return _load_portfolio(portfolio_path)


def _load_portfolio_master(path: Path) -> list[dict[str, Any]]:
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as file:
            master = yaml.safe_load(file) or {}
    except ImportError:
        master = _load_yaml_without_dependency(path)
    except Exception:
        return _load_portfolio(path.with_name("portfolio.csv"))

    totals = master.get("totals", {}) or {}
    labels = master.get("asset_class_labels", {}) or {}
    rows: list[dict[str, Any]] = []
    for key, fallback_label in [
        ("us_stock", "美股"),
        ("hk_stock", "港股"),
        ("cn_stock", "A股"),
        ("china_bond", "债券"),
        ("gold", "黄金"),
        ("cash", "现金"),
    ]:
        amount_cny = float(totals.get(key, 0) or 0)
        category = str(labels.get(key, fallback_label) or fallback_label)
        rows.append(
            {
                "category": category,
                "name": f"{category}合计",
                "symbol": "",
                "amount_wan": amount_cny / 10000,
                "currency": "CNY",
                "quantity": None,
                "unit": "",
                "note": "来自portfolio_master.yaml；类别总额为最高优先级资产事实。",
                "valuation_status": "master",
                "valuation_note": "",
                "price_cny_per_gram": None,
            }
        )
    return rows


def load_market_data(path: str | Path) -> dict[str, dict[str, Any]]:
    market: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            indicator = row["indicator"].strip()
            market[indicator] = {
                "value": row.get("value", "").strip(),
                "change": _to_number_or_text(row.get("change", "0")),
                "score_impact": float(row.get("score_impact", 0) or 0),
                "risk_note": row.get("risk_note", "").strip(),
                "valuation": row.get("valuation", "").strip(),
                "trend": row.get("trend", "").strip(),
                "macro_risk": row.get("macro_risk", "").strip(),
                "defense_support": row.get("defense_support", "").strip(),
                "as_of": row.get("as_of", "").strip(),
            }
    return market


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
