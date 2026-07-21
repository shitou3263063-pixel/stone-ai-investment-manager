"""Runtime context and market calendar for the two pre-open reports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo


SESSION_SPECS = {
    "REGULAR": {
        "timezone": "Asia/Shanghai",
        "label": "",
        "slug": "regular",
        "market": "CN",
    },
    "CN_PREOPEN": {
        "timezone": "Asia/Shanghai",
        "label": "A股开盘前",
        "slug": "cn_preopen",
        "market": "CN",
    },
    "US_PREOPEN": {
        "timezone": "America/New_York",
        "label": "美股开盘前",
        "slug": "us_preopen",
        "market": "US",
    },
}


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    cursor = date(year, month, 1)
    cursor += timedelta(days=(weekday - cursor.weekday()) % 7)
    return cursor + timedelta(weeks=occurrence - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    cursor = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    month_seed = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * month_seed) // 451
    month = (h + month_seed - 7 * m + 114) // 31
    day = (h + month_seed - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _us_market_holidays(year: int) -> dict[date, str]:
    holidays = {
        _observed(date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        _easter_sunday(year) - timedelta(days=2): "Good Friday",
        _last_weekday(year, 5, 0): "Memorial Day",
        _observed(date(year, 6, 19)): "Juneteenth",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        _observed(date(year, 12, 25)): "Christmas Day",
    }
    # A Saturday New Year's Day is observed on the preceding calendar year.
    next_new_year = date(year + 1, 1, 1)
    if next_new_year.weekday() == 5:
        holidays[date(year, 12, 31)] = "New Year's Day (observed)"
    return holidays


# SSE-published full-day closures. Keeping explicit annual dates avoids treating
# mainland make-up workdays as exchange sessions. Unknown years still degrade to
# the weekday calendar and can be enriched from the provider trade calendar.
_CN_MARKET_HOLIDAYS: dict[int, dict[date, str]] = {
    2025: {
        **{date(2025, 1, day): "元旦" for day in (1,)},
        **{date(2025, 1, day): "春节" for day in range(28, 32)},
        **{date(2025, 2, day): "春节" for day in range(1, 5)},
        **{date(2025, 4, day): "清明节" for day in range(4, 7)},
        **{date(2025, 5, day): "劳动节" for day in range(1, 6)},
        date(2025, 6, 2): "端午节",
        **{date(2025, 10, day): "国庆节/中秋节" for day in range(1, 9)},
    },
    2026: {
        **{date(2026, 1, day): "元旦" for day in range(1, 4)},
        **{date(2026, 2, day): "春节" for day in range(15, 24)},
        **{date(2026, 4, day): "清明节" for day in range(4, 7)},
        **{date(2026, 5, day): "劳动节" for day in range(1, 6)},
        **{date(2026, 6, day): "端午节" for day in range(19, 22)},
        **{date(2026, 9, day): "中秋节" for day in range(25, 28)},
        **{date(2026, 10, day): "国庆节" for day in range(1, 8)},
    },
}


def market_holiday_name(session: str, day: date) -> str | None:
    session = session.upper()
    if day.weekday() >= 5:
        return "周末"
    if session == "US_PREOPEN":
        return _us_market_holidays(day.year).get(day)
    return _CN_MARKET_HOLIDAYS.get(day.year, {}).get(day)


def is_market_trading_day(session: str, day: date) -> bool:
    return market_holiday_name(session, day) is None


def next_market_trading_day(session: str, day: date) -> date:
    cursor = day + timedelta(days=1)
    while not is_market_trading_day(session, cursor):
        cursor += timedelta(days=1)
    return cursor


@dataclass(frozen=True)
class ReportSessionContext:
    report_session: str
    report_timezone: str
    report_label: str
    local_now: datetime

    @property
    def local_report_date(self) -> date:
        return self.local_now.date()

    @property
    def output_slug(self) -> str:
        return str(SESSION_SPECS[self.report_session]["slug"])

    @property
    def dedupe_key(self) -> str:
        return f"{self.local_report_date.isoformat()}:{self.report_session}"

    @property
    def market_is_trading_day(self) -> bool:
        return is_market_trading_day(self.report_session, self.local_report_date)

    @property
    def market_holiday(self) -> str | None:
        return market_holiday_name(self.report_session, self.local_report_date)

    @property
    def next_trading_day(self) -> date:
        return next_market_trading_day(self.report_session, self.local_report_date)

    def output_dir(self, project_root: Path) -> Path:
        if self.report_session == "REGULAR":
            return project_root / "reports"
        return project_root / "outputs" / self.local_report_date.isoformat() / self.output_slug

    def report_filename(self, stem: str, suffix: str) -> str:
        if self.report_session == "REGULAR":
            return f"{stem}{suffix}"
        return f"{stem}_{self.report_session}{suffix}"

    def report_path(self, reports_dir: Path, stem: str, suffix: str) -> Path:
        return reports_dir / self.report_filename(stem, suffix)

    def email_attachment_paths(self, reports_dir: Path) -> list[Path]:
        return [
            self.report_path(reports_dir, "today_action", ".md"),
            self.report_path(reports_dir, "daily_report", ".md"),
            self.report_path(reports_dir, "weekly_report", ".md"),
            reports_dir / "run_status.json",
        ]

    def delivery_marker(self, project_root: Path) -> Path:
        return (
            project_root
            / "outputs"
            / ".mail_state"
            / self.output_slug
            / f"{self.local_report_date.isoformat()}.json"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "report_session": self.report_session,
            "report_timezone": self.report_timezone,
            "report_label": self.report_label,
            "local_report_date": self.local_report_date.isoformat(),
            "dedupe_key": self.dedupe_key,
            "market_is_trading_day": self.market_is_trading_day,
            "market_holiday": self.market_holiday,
            "next_trading_day": self.next_trading_day.isoformat(),
            "report_variant": "REGULAR" if self.market_is_trading_day else "MARKET_CLOSED",
        }


def get_report_session_context(
    *,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
) -> ReportSessionContext:
    env = environ if environ is not None else os.environ
    session = str(env.get("STONE_REPORT_SESSION") or "REGULAR").strip().upper()
    if session not in SESSION_SPECS:
        raise ValueError(f"Unsupported STONE_REPORT_SESSION: {session}")
    spec = SESSION_SPECS[session]
    timezone_name = str(env.get("STONE_REPORT_TIMEZONE") or spec["timezone"]).strip()
    label = str(env.get("STONE_REPORT_LABEL") or spec["label"]).strip()
    zone = ZoneInfo(timezone_name)
    if now is None:
        local_now = datetime.now(tz=zone)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=zone)
    else:
        local_now = now.astimezone(zone)
    return ReportSessionContext(session, timezone_name, label, local_now)


def current_report_date() -> date:
    return get_report_session_context().local_report_date
