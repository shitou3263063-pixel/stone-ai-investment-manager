from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENT_TYPES = ["FOMC", "CPI", "PPI", "非农", "美联储主席讲话", "财报季"]


def _parse_event_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_settings_with_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except Exception as exc:  # noqa: BLE001 - 配置失败时使用空配置继续运行
        write_log(f"settings.yaml 读取失败，已跳过宏观事件配置：{exc}", filename="macro_calendar.log")
        return {}


def _load_settings_without_yaml(path: Path) -> dict[str, Any]:
    """简易读取 macro_events 列表，避免缺 PyYAML 时主程序崩溃。"""
    if not path.exists():
        return {}

    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_macro_events = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line == "macro_events:":
            in_macro_events = True
            continue
        if not in_macro_events:
            continue

        if line.startswith("- "):
            if current:
                events.append(current)
            current = {}
            line = line[2:].strip()

        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip().strip('"').strip("'")

    if current:
        events.append(current)

    return {"macro_events": events}


def load_macro_events(settings_path: Path | None = None) -> list[dict[str, Any]]:
    """从 config/settings.yaml 读取宏观事件。"""
    path = settings_path or PROJECT_ROOT / "config" / "settings.yaml"
    if not path.exists():
        write_log("settings.yaml 不存在，宏观事件列表为空", filename="macro_calendar.log")
        return []

    try:
        import yaml  # noqa: F401

        settings = _load_settings_with_yaml(path)
    except ImportError:
        settings = _load_settings_without_yaml(path)

    events: list[dict[str, Any]] = []
    for item in settings.get("macro_events", []) or []:
        event_date = _parse_event_date(item.get("date"))
        if not event_date:
            write_log(f"宏观事件日期无效，已跳过：{item}", filename="macro_calendar.log")
            continue
        events.append(
            {
                "name": str(item.get("name", "未命名事件")).strip(),
                "date": event_date,
                "level": str(item.get("level", "medium")).strip().lower(),
            }
        )
    return events


def analyze_macro_calendar(
    today: date | None = None,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    """判断未来7天是否有重大宏观事件。"""
    current_date = today or date.today()
    window_end = current_date + timedelta(days=7)
    events = load_macro_events(settings_path)

    upcoming = [
        event
        for event in events
        if current_date <= event["date"] <= window_end
    ]
    upcoming.sort(key=lambda item: item["date"])
    high_events = [event for event in upcoming if event["level"] == "high"]

    if high_events:
        reminder = "未来7天有 high 级别宏观事件：重大事件前不追涨，定投可以继续，不建议一次性重仓买入。"
    elif upcoming:
        reminder = "未来7天有宏观事件，建议保持仓位纪律，避免临时冲动交易。"
    else:
        reminder = "未来7天暂未配置重大宏观事件，继续按组合纪律执行。"

    return {
        "as_of": current_date.isoformat(),
        "window_days": 7,
        "important_event_types": DEFAULT_EVENT_TYPES,
        "upcoming_events": upcoming,
        "has_high_event_next_7_days": bool(high_events),
        "reminder": reminder,
        "discipline": [
            "重大事件前不追涨",
            "定投可以继续",
            "不建议一次性重仓买入",
            "所有操作必须人工确认，系统不自动交易",
        ],
    }

