from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_TIMEZONE = "Asia/Shanghai"
DEFAULT_EVENT_TYPES = ["FOMC", "CPI", "PPI", "非农", "美联储主席讲话", "财报季"]
EVENT_REVIEW_DELAY_MINUTES = 15

# BLS 2026 release calendar. These official timestamps take precedence over
# user_configured_calendar entries with the same event/reference period.
OFFICIAL_BLS_EVENTS: tuple[dict[str, str], ...] = (
    {
        "event_name": "CPI",
        "reference_period": "2026-06",
        "release_at": "2026-07-14 08:30:00",
        "source_timezone": "America/New_York",
        "source": "https://www.bls.gov/schedule/2026/07_sched.htm",
        "source_level": "official_primary",
        "verification_status": "verified",
        "risk_level": "high",
    },
    {
        "event_name": "PPI",
        "reference_period": "2026-06",
        "release_at": "2026-07-15 08:30:00",
        "source_timezone": "America/New_York",
        "source": "https://www.bls.gov/schedule/2026/07_sched.htm",
        "source_level": "official_primary",
        "verification_status": "verified",
        "risk_level": "high",
    },
)


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception as exc:  # noqa: BLE001
        write_log(f"宏观事件备用配置读取失败：{exc}", filename="macro_calendar.log")
        return {}


def _as_aware_datetime(value: date | datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    if isinstance(value, datetime):
        return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    return datetime.combine(value, time.min, tzinfo=zone)


def classify_event_status(event: dict[str, Any], report_time: date | datetime, report_timezone: str = DEFAULT_REPORT_TIMEZONE) -> str:
    """Classify an event against report time using UTC only and never retain stale UPCOMING states."""
    if str(event.get("status") or "").upper() == "CANCELLED" or event.get("cancelled"):
        return "CANCELLED"
    raw_release = event.get("release_at_utc")
    if not raw_release:
        return "INVALID_TIME"
    try:
        release = datetime.fromisoformat(str(raw_release).replace("Z", "+00:00"))
        if release.tzinfo is None:
            return "INVALID_TIME"
        current = _as_aware_datetime(report_time, report_timezone).astimezone(timezone.utc)
    except (TypeError, ValueError, KeyError):
        return "INVALID_TIME"
    release_utc = release.astimezone(timezone.utc)
    if current >= release_utc:
        if event.get("reviewed_at"):
            return "REVIEWED"
        # A calendar confirmation verifies timing, not the released economic
        # value.  Keep post-release data in a safe pending-review state until
        # a source explicitly confirms the result.
        if event.get("release_result_confirmed") is False and event.get("actual_value") is None:
            return "RELEASED_DATA_MISSING"
        return "RELEASED"
    restriction_hours = int(event.get("restriction_window_hours", 0) or 0)
    if current >= release_utc - timedelta(hours=restriction_hours):
        return "IN_RESTRICTION_WINDOW"
    return "UPCOMING"


def _normalise_official_event(raw: dict[str, Any], report_timezone: str) -> dict[str, Any]:
    source_zone = ZoneInfo(str(raw["source_timezone"]))
    report_zone = ZoneInfo(report_timezone)
    local_release = datetime.fromisoformat(str(raw["release_at"]))
    if local_release.tzinfo is None:
        local_release = local_release.replace(tzinfo=source_zone)
    else:
        local_release = local_release.astimezone(source_zone)
    release_utc = local_release.astimezone(timezone.utc)
    release_report = local_release.astimezone(report_zone)
    verified = str(raw.get("verification_status")) == "verified"
    event = {
        "event_name": str(raw["event_name"]),
        "reference_period": str(raw.get("reference_period") or "unknown"),
        "release_at": local_release.isoformat(),
        "source_timezone": str(raw["source_timezone"]),
        "release_at_utc": release_utc.isoformat(),
        "release_at_report_timezone": release_report.isoformat(),
        "source": str(raw.get("source") or "unavailable"),
        "source_level": str(raw.get("source_level") or "unverified"),
        "verification_status": str(raw.get("verification_status") or "unverified"),
        "risk_level": str(raw.get("risk_level") or "medium").lower(),
        "report_timezone": report_timezone,
        # Backward-compatible display keys used by the existing report code.
        "name": str(raw["event_name"]),
        "date": release_report.date().isoformat(),
        "time": release_report.strftime("%H:%M"),
        "timezone": report_timezone,
        "level": str(raw.get("risk_level") or "medium").lower(),
        "confirmed": verified,
        "status": "UPCOMING" if verified else "INVALID_TIME",
        "actual_value": raw.get("actual_value"),
        "previous_value": raw.get("previous_value"),
        "revised_value": raw.get("revised_value"),
        "consensus_value": raw.get("consensus_value"),
        "release_result_confirmed": bool(raw.get("actual_value") is not None),
        "event_data_status": "AVAILABLE" if raw.get("actual_value") is not None else "PENDING_RELEASE",
    }
    return event


def _normalise_config_event(raw: dict[str, Any], report_timezone: str) -> dict[str, Any] | None:
    """Convert a user-configured fallback without inventing a missing timezone."""
    event_name = str(raw.get("event_name") or raw.get("name") or "未命名事件").strip()
    reference_period = str(raw.get("reference_period") or "unknown")
    release_at = raw.get("release_at")
    source_timezone = raw.get("source_timezone") or raw.get("timezone")
    if not release_at and raw.get("date") and raw.get("time") and source_timezone:
        release_at = f"{raw['date']} {raw['time']}"

    base = {
        "event_name": event_name,
        "reference_period": reference_period,
        "source": str(raw.get("source") or "user_configured_calendar"),
        "source_level": "user_configured_calendar",
        "verification_status": "unverified",
        "risk_level": str(raw.get("risk_level") or raw.get("level") or "medium").lower(),
    }
    if not release_at or not source_timezone:
        # A date-only entry is retained for audit, but never promoted to a
        # confirmed high-risk event and never receives a guessed timestamp.
        return {
            **base,
            "release_at": None,
            "source_timezone": str(source_timezone or "unknown"),
            "release_at_utc": None,
            "release_at_report_timezone": None,
            "report_timezone": report_timezone,
            "name": event_name,
            "date": None,
            "time": "未验证",
            "timezone": str(source_timezone or "unknown"),
            "level": base["risk_level"],
            "confirmed": False,
            "status": "INVALID_TIME",
        }
    try:
        return _normalise_official_event(
            {**base, "release_at": str(release_at), "source_timezone": str(source_timezone)},
            report_timezone,
        ) | {"confirmed": False, "status": "UPCOMING", "verification_status": "unverified"}
    except (ValueError, TypeError):
        return None


def load_macro_events(
    settings_path: Path | None = None,
    report_timezone: str = DEFAULT_REPORT_TIMEZONE,
) -> list[dict[str, Any]]:
    """Load official events first; user configuration is fallback-only."""
    official = [_normalise_official_event(item, report_timezone) for item in OFFICIAL_BLS_EVENTS]
    official_keys = {(item["event_name"].upper(), item["reference_period"]) for item in official}
    settings = _load_settings(settings_path or PROJECT_ROOT / "config" / "settings.yaml")
    fallback: list[dict[str, Any]] = []
    for raw in settings.get("macro_events", []) or []:
        event = _normalise_config_event(raw, report_timezone)
        if not event:
            continue
        key = (event["event_name"].upper(), event["reference_period"])
        # Official BLS dates can never be overwritten by user configuration.
        if key in official_keys or event["event_name"].upper() in {"CPI", "PPI", "CPI数据", "PPI数据"}:
            continue
        fallback.append(event)
    return sorted(
        official + fallback,
        key=lambda item: item.get("release_at_utc") or "9999-12-31T23:59:59+00:00",
    )


def get_upcoming_high_risk_events(
    as_of: date | datetime,
    *,
    hours: int | None = None,
    days: int | None = None,
    events: list[dict[str, Any]] | None = None,
    report_timezone: str = DEFAULT_REPORT_TIMEZONE,
) -> list[dict[str, Any]]:
    """Return the single authoritative verified high-risk event selection."""
    if (hours is None) == (days is None):
        raise ValueError("hours 和 days 必须且只能指定一个")
    start = _as_aware_datetime(as_of, report_timezone).astimezone(timezone.utc)
    end = start + (timedelta(hours=hours) if hours is not None else timedelta(days=days or 0))
    selected: list[dict[str, Any]] = []
    for event in events if events is not None else load_macro_events(report_timezone=report_timezone):
        if event.get("risk_level") != "high" or event.get("verification_status") != "verified":
            continue
        raw_release = event.get("release_at_utc")
        if not raw_release:
            continue
        release = datetime.fromisoformat(str(raw_release).replace("Z", "+00:00")).astimezone(timezone.utc)
        status = classify_event_status(event, start, report_timezone)
        if status in {"UPCOMING", "IN_RESTRICTION_WINDOW"} and start < release <= end:
            selected.append({**event, "status": status})
    return sorted(selected, key=lambda item: item["release_at_utc"])


def analyze_macro_calendar(
    today: date | None = None,
    settings_path: Path | None = None,
    *,
    as_of: datetime | None = None,
    report_timezone: str = DEFAULT_REPORT_TIMEZONE,
) -> dict[str, Any]:
    current = as_of or _as_aware_datetime(today or date.today(), report_timezone)
    raw_events = load_macro_events(settings_path, report_timezone)
    events = [{**event, "status": classify_event_status(event, current, report_timezone)} for event in raw_events]
    high_48h = get_upcoming_high_risk_events(current, hours=48, events=events, report_timezone=report_timezone)
    high_7d = get_upcoming_high_risk_events(current, days=7, events=events, report_timezone=report_timezone)
    end_7d = current.astimezone(timezone.utc) + timedelta(days=7)
    upcoming = [
        event for event in events
        if event.get("release_at_utc")
        and event.get("status") == "UPCOMING"
        and current.astimezone(timezone.utc)
        < datetime.fromisoformat(str(event["release_at_utc"])).astimezone(timezone.utc)
        <= end_7d
    ]
    released = [
        {
            **event,
            "event_data_status": "RELEASED_DATA_MISSING" if event.get("status") == "RELEASED_DATA_MISSING" else "AVAILABLE",
            "rule_interpretation": (
                "实际值缺失，不能判断相对预期；利率、股票、债券和美元影响保持待复核。"
                if event.get("status") == "RELEASED_DATA_MISSING"
                else "依据实际值、前值和预期差异复核利率、股票、债券与美元方向。"
            ),
        }
        for event in events if event.get("status") in {"RELEASED", "RELEASED_DATA_MISSING", "REVIEWED"}
    ]
    pending_review = next((event for event in events if event.get("status") == "RELEASED_DATA_MISSING" and event.get("risk_level") == "high"), None)
    next_review = (high_48h[0] if high_48h else high_7d[0] if high_7d else pending_review)
    if high_7d:
        reminder = "未来7天存在已核验高等级宏观事件；事件前暂停机会加仓，基础定投只复核，不自动转为大额交易。"
    elif any(item.get("verification_status") == "unverified" for item in upcoming):
        reminder = "未来7天存在未验证事件；不得把未验证日期当作已确认高等级事件。"
    else:
        reminder = "未来7天暂无已核验高等级宏观事件。"
    return {
        "as_of": current.isoformat(),
        "report_timezone": report_timezone,
        "window_days": 7,
        "important_event_types": DEFAULT_EVENT_TYPES,
        "events": events,
        "upcoming_events": upcoming,
        "high_risk_events_48h": high_48h,
        "high_risk_events_7d": high_7d,
        "has_high_event_next_48_hours": bool(high_48h),
        "has_high_event_next_7_days": bool(high_7d),
        "has_unconfirmed_high_event_next_7_days": any(
            item.get("risk_level") == "high" and item.get("verification_status") == "unverified"
            for item in upcoming
        ),
        "released_events": released,
        "next_review_date": (
            (datetime.fromisoformat(str(next_review["release_at_utc"])).astimezone(timezone.utc) + timedelta(minutes=EVENT_REVIEW_DELAY_MINUTES))
            .astimezone(ZoneInfo(report_timezone)).isoformat(timespec="seconds")
            if next_review else None
        ),
        "next_review_reason": (
            (f"{next_review.get('event_name')}已到公布时间但实际值缺失；标记RELEASED_DATA_MISSING并持续复核官方结果。"
             if next_review.get("status") == "RELEASED_DATA_MISSING"
             else f"等待{next_review.get('event_name')}公布后{EVENT_REVIEW_DELAY_MINUTES}分钟重新复核市场、DQS和交易条件。")
            if next_review else "未来7天无已核验高等级待发布事件，使用下一基础定投复核时间。"
        ),
        "calendar_confidence": "high" if high_7d else "low" if upcoming else "medium",
        "reminder": reminder,
        "discipline": [
            "重大事件前不追涨",
            "基础定投只允许复核",
            "机会加仓暂停",
            "所有操作必须人工确认，系统不自动交易",
        ],
    }
