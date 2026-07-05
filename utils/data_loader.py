from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.portfolio import load_portfolio as _load_portfolio


def _to_number_or_text(value: str) -> Any:
    """把配置里的文本转成布尔值、数字或普通字符串。"""

    value = value.strip()
    if value == "":
        return ""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _load_yaml_without_dependency(path: Path) -> dict[str, Any]:
    """简易 YAML 读取器。

    本项目的 config.yaml 只有两层字典，用这个函数即可运行。
    如果安装了 PyYAML，系统会优先使用 PyYAML。
    """

    result: dict[str, Any] = {}
    current_section: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if not raw_line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                result[key] = {}
                current_section = key
            else:
                result[key] = _to_number_or_text(value)
                current_section = None
            continue

        if current_section is None:
            continue

        key, _, value = line.strip().partition(":")
        result[current_section][key.strip()] = _to_number_or_text(value)

    return result


def load_config(path: str | Path) -> dict[str, Any]:
    """读取系统配置。"""

    config_path = Path(path)
    try:
        import yaml  # type: ignore

        with config_path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except ImportError:
        return _load_yaml_without_dependency(config_path)


def load_portfolio(path: str | Path) -> list[dict[str, Any]]:
    """读取持仓 CSV。金额单位为万元。"""
    return _load_portfolio(path)


def load_market_data(path: str | Path) -> dict[str, dict[str, Any]]:
    """读取手动维护的市场数据。"""

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
    """返回项目根目录。"""

    return Path(__file__).resolve().parents[1]
