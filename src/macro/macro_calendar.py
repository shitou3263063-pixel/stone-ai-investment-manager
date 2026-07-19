from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_TIMEZONE = "Asia/Shanghai"
DEFAULT_EVENT_TYPES = ["FOMC", "CPI", "PPI", "非农", "美联储主席讲话", "财报季"]
EVENT_REVIEW_DELAY_MINUTES = 15
RELEASE_EVENT_STATUSES = {
    "NOT_RELEASED",
    "RELEASED_FETCHED",
    "RELEASED_FETCH_FAILED",
    "PARTIAL_DATA",
    "NO_RELEVANT_EVENT",
}
EVENT_RELEASE_SERIES = {"CPI": "CPIAUCSL", "PPI": "PPIACO"}


class EconomicCalendar(TypedDict, total=False):
    event_id: str
    event_name: str
    reference_period: str
    release_at: str
    release_at_utc: str
    importance: str
    is_released: bool
    source: str


class EconomicReleaseData(TypedDict, total=False):
    event_id: str
    actual_value: float | None
    previous_value: float | None
    consensus_value: float | None
    revision: float | None
    source: str | None
    as_of: str | None
    fetch_attempted: bool
    fetch_error: str | None

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

# User-confirmed holding-company event.  Position-level events are assessed
# separately and never promoted into a portfolio-wide macro gate.
CONFIRMED_POSITION_EVENTS: tuple[dict[str, str], ...] = (
    {
        "event_name": "IBKR 2026年第二季度业绩",
        "reference_period": "2026-Q2",
        "release_at": "2026-07-21 16:00:00",
        "source_timezone": "America/New_York",
        "source": "user_confirmed_event_calendar",
        "source_level": "user_confirmed_primary_fact",
        "verification_status": "verified",
        "risk_level": "high",
        "event_scope": "POSITION_LEVEL",
        "security_id": "IBKR",
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
    """Classify release state without treating a calendar page as release data."""
    raw_release = event.get("release_at_utc")
    if not raw_release:
        return "NO_RELEVANT_EVENT"
    try:
        release = datetime.fromisoformat(str(raw_release).replace("Z", "+00:00"))
        if release.tzinfo is None:
            return "NO_RELEVANT_EVENT"
        current = _as_aware_datetime(report_time, report_timezone).astimezone(timezone.utc)
    except (TypeError, ValueError, KeyError):
        return "NO_RELEVANT_EVENT"
    release_utc = release.astimezone(timezone.utc)
    if current < release_utc:
        return "NOT_RELEASED"
    release_data = event.get("economic_release_data", {}) or {}
    actual = release_data.get("actual_value", event.get("actual_value"))
    if actual is None:
        return "RELEASED_FETCH_FAILED"
    non_core_missing = any(
        release_data.get(field, event.get(field)) in {None, ""}
        for field in ("previous_value", "consensus_value", "revision")
    )
    return "PARTIAL_DATA" if non_core_missing else "RELEASED_FETCHED"


def _macro_items(macro_snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    payload = macro_snapshot or {}
    return payload.get("items", payload) or {}


def _release_data_for_event(
    event: dict[str, Any], macro_snapshot: dict[str, Any] | None,
) -> EconomicReleaseData:
    event_name = str(event.get("event_name") or event.get("name") or "").upper()
    series_id = EVENT_RELEASE_SERIES.get(event_name)
    item = _macro_items(macro_snapshot).get(series_id or "", {}) or {}
    attempted = bool(macro_snapshot is not None and series_id)
    status = str(item.get("status") or item.get("data_status") or "").lower()
    actual = item.get("value") if status in {"ok", "success", "valid", "valid_lagged_by_design"} else None
    reference_period = str(event.get("reference_period") or "")
    observation_date = str(item.get("observation_date") or item.get("date") or "")
    if reference_period and observation_date and not observation_date.startswith(reference_period):
        actual = None
    return {
        "event_id": f"{event_name}:{reference_period or 'unknown'}",
        "actual_value": actual,
        "previous_value": item.get("previous_value") if actual is not None else None,
        "consensus_value": item.get("consensus_value"),
        "revision": item.get("revision", item.get("revised_value")),
        "source": item.get("source") if actual is not None else None,
        "as_of": item.get("fetched_at") or item.get("retrieved_at") or item.get("observed_at"),
        "fetch_attempted": attempted,
        "fetch_error": None if actual is not None else str(item.get("error") or item.get("error_message") or "release value unavailable"),
    }


def _attach_release_data(
    event: dict[str, Any], macro_snapshot: dict[str, Any] | None, current: datetime,
) -> dict[str, Any]:
    release = _release_data_for_event(event, macro_snapshot)
    release_at = _parse_release_time(event)
    calendar: EconomicCalendar = {
        "event_id": release["event_id"],
        "event_name": str(event.get("event_name") or event.get("name") or "UNKNOWN"),
        "reference_period": str(event.get("reference_period") or "unknown"),
        "release_at": str(event.get("release_at")),
        "release_at_utc": str(event.get("release_at_utc")),
        "importance": str(event.get("risk_level") or "medium"),
        "is_released": bool(release_at and current.astimezone(timezone.utc) >= release_at),
        "source": str(event.get("source") or "unavailable"),
    }
    combined = {
        **event,
        "economic_calendar": calendar,
        "economic_release_data": release,
        "actual_value": release.get("actual_value"),
        "previous_value": release.get("previous_value"),
        "consensus_value": release.get("consensus_value"),
        "revision": release.get("revision"),
        "release_data_source": release.get("source"),
        "release_data_as_of": release.get("as_of"),
    }
    combined["status"] = classify_event_status(combined, current)
    combined["event_data_status"] = combined["status"]
    return combined


def _parse_release_time(event: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(event.get("release_at_utc") or "").replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except ValueError:
        return None


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
        # Calendar sources only establish release timing. Release values are
        # attached later from EconomicReleaseData.
        "actual_value": None,
        "previous_value": None,
        "revision": None,
        "consensus_value": None,
        "event_data_status": "NOT_RELEASED",
        "event_scope": str(raw.get("event_scope") or "PORTFOLIO_LEVEL"),
        "security_id": raw.get("security_id"),
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
    official = [
        _normalise_official_event(item, report_timezone)
        for item in (*OFFICIAL_BLS_EVENTS, *CONFIRMED_POSITION_EVENTS)
    ]
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
        if status == "NOT_RELEASED" and start < release <= end:
            selected.append({**event, "status": status})
    return sorted(selected, key=lambda item: item["release_at_utc"])


def analyze_macro_calendar(
    today: date | None = None,
    settings_path: Path | None = None,
    *,
    as_of: datetime | None = None,
    report_timezone: str = DEFAULT_REPORT_TIMEZONE,
    macro_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = as_of or _as_aware_datetime(today or date.today(), report_timezone)
    raw_events = load_macro_events(settings_path, report_timezone)
    events = [_attach_release_data(event, macro_snapshot, current) for event in raw_events]
    portfolio_events = [event for event in events if event.get("event_scope") != "POSITION_LEVEL"]
    position_events = [event for event in events if event.get("event_scope") == "POSITION_LEVEL"]
    high_48h = get_upcoming_high_risk_events(current, hours=48, events=portfolio_events, report_timezone=report_timezone)
    high_7d = get_upcoming_high_risk_events(current, days=7, events=portfolio_events, report_timezone=report_timezone)
    position_high_7d = get_upcoming_high_risk_events(
        current, days=7, events=position_events, report_timezone=report_timezone
    )
    end_7d = current.astimezone(timezone.utc) + timedelta(days=7)
    upcoming = [
        event for event in events
        if event.get("release_at_utc")
        and event.get("status") == "NOT_RELEASED"
        and current.astimezone(timezone.utc)
        < datetime.fromisoformat(str(event["release_at_utc"])).astimezone(timezone.utc)
        <= end_7d
    ]
    portfolio_upcoming = [
        event for event in upcoming if event.get("event_scope") != "POSITION_LEVEL"
    ]
    released = [
        {
            **event,
            "event_data_status": event.get("status"),
            "rule_interpretation": (
                "实际值缺失，不能判断相对预期；利率、股票、债券和美元影响保持待复核。"
                if event.get("status") == "RELEASED_FETCH_FAILED"
                else "依据实际值、前值和预期差异复核利率、股票、债券与美元方向。"
            ),
        }
        for event in events if event.get("status") in {"RELEASED_FETCHED", "RELEASED_FETCH_FAILED", "PARTIAL_DATA"}
    ]
    pending_review = next((event for event in events if event.get("status") == "RELEASED_FETCH_FAILED" and event.get("risk_level") == "high"), None)
    next_review = (
        high_48h[0] if high_48h else high_7d[0] if high_7d
        else position_high_7d[0] if position_high_7d else pending_review
    )
    if high_7d:
        reminder = "未来7天存在已核验高等级宏观事件；事件前暂停机会加仓，基础定投只复核，不自动转为大额交易。"
    elif any(item.get("verification_status") == "unverified" for item in portfolio_upcoming):
        reminder = "未来7天存在未验证事件；不得把未验证日期当作已确认高等级事件。"
    else:
        reminder = "未来7天暂无已核验高等级宏观事件。"
    missing_calendar_fields = [
        str(event.get("event_name") or event.get("name") or "UNKNOWN_EVENT")
        for event in events
        if not event.get("release_at_utc") or event.get("verification_status") == "unverified"
    ]
    calendar_status = "UNAVAILABLE" if not events else ("PARTIAL" if missing_calendar_fields else "VALID")
    risk_state = "HIGH_RISK_EVENT_FOUND" if high_7d else ("UNKNOWN" if calendar_status == "UNAVAILABLE" else "CLEAR")
    gate_result = (
        "BLOCK" if risk_state == "HIGH_RISK_EVENT_FOUND"
        else "CONSERVATIVE_BLOCK" if calendar_status == "UNAVAILABLE"
        else "PASS_WITH_LIMITATIONS" if calendar_status == "PARTIAL"
        else "PASS"
    )
    released_statuses = [str(event.get("status")) for event in released]
    released_quality_status = (
        "NO_RELEVANT_EVENT" if not released_statuses
        else "RELEASED_FETCH_FAILED" if "RELEASED_FETCH_FAILED" in released_statuses
        else "PARTIAL_DATA" if "PARTIAL_DATA" in released_statuses
        else "RELEASED_FETCHED"
    )
    return {
        "as_of": current.isoformat(),
        "report_timezone": report_timezone,
        "window_days": 7,
        "important_event_types": DEFAULT_EVENT_TYPES,
        "events": events,
        "economic_calendar": [event.get("economic_calendar") for event in events],
        "economic_release_data": [event.get("economic_release_data") for event in events],
        "event_calendar_data_status": calendar_status,
        "event_risk_state": risk_state,
        "event_gate_result": gate_result,
        "future_event_gate": {
            "status": risk_state,
            "calendar_status": calendar_status,
            "gate_result": gate_result,
            "high_impact_events": high_7d,
        },
        "released_data_quality": {
            "status": released_quality_status,
            "events": released,
            "failed_events": [event for event in released if event.get("status") == "RELEASED_FETCH_FAILED"],
            "partial_events": [event for event in released if event.get("status") == "PARTIAL_DATA"],
        },
        "event_count": len(events),
        "calendar_missing_items": missing_calendar_fields,
        "upcoming_events": upcoming,
        "high_risk_events_48h": high_48h,
        "high_risk_events_7d": high_7d,
        "position_level_event_risk": {
            "status": "HIGH_RISK_EVENT_FOUND" if position_high_7d else "CLEAR",
            "events": position_high_7d,
            "affected_securities": sorted(
                {str(event.get("security_id")) for event in position_high_7d if event.get("security_id")}
            ),
        },
        "portfolio_level_event_risk": {
            "status": "HIGH_RISK_EVENT_FOUND" if high_7d else "CLEAR",
            "events": high_7d,
        },
        "has_high_event_next_48_hours": bool(high_48h),
        "has_high_event_next_7_days": bool(high_7d),
        "has_unconfirmed_high_event_next_7_days": any(
            item.get("risk_level") == "high" and item.get("verification_status") == "unverified"
            for item in portfolio_upcoming
        ),
        "released_events": released,
        "next_review_date": (
            (datetime.fromisoformat(str(next_review["release_at_utc"])).astimezone(timezone.utc) + timedelta(minutes=EVENT_REVIEW_DELAY_MINUTES))
            .astimezone(ZoneInfo(report_timezone)).isoformat(timespec="seconds")
            if next_review else None
        ),
        "next_review_reason": (
            (f"{next_review.get('event_name')}已到公布时间但实际值抓取失败；标记RELEASED_FETCH_FAILED并持续复核官方结果。"
             if next_review.get("status") == "RELEASED_FETCH_FAILED"
             else f"等待{next_review.get('event_name')}公布后{EVENT_REVIEW_DELAY_MINUTES}分钟重新复核市场、DQS和交易条件。")
            if next_review else "未来7天无已核验高等级待发布事件，使用下一基础定投复核时间。"
        ),
        "calendar_confidence": "high" if high_7d else "low" if portfolio_upcoming else "medium",
        "reminder": reminder,
        "discipline": [
            "重大事件前不追涨",
            "基础定投只允许复核",
            "机会加仓暂停",
            "所有操作必须人工确认，系统不自动交易",
        ],
    }
