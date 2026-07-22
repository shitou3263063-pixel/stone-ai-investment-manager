from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo


class MarketPhase(str, Enum):
    OPEN = "OPEN"
    LUNCH_BREAK = "LUNCH_BREAK"
    PRE_OPEN = "PRE_OPEN"
    CLOSED = "CLOSED"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"


@dataclass(frozen=True)
class MarketStatus:
    market: str
    phase: MarketPhase
    local_time: datetime
    is_trading: bool


class HolidayCalendar(Protocol):
    def is_holiday(self, market: str, session_date: date) -> bool: ...


class StaticHolidayCalendar:
    """Minimal holiday interface; callers may replace it with an exchange calendar."""

    def __init__(self, holidays: Mapping[str, set[date]] | None = None) -> None:
        self._holidays = {market.upper(): set(days) for market, days in (holidays or {}).items()}

    @classmethod
    def from_config(cls, payload: Mapping[str, list[str]] | None) -> "StaticHolidayCalendar":
        holidays: dict[str, set[date]] = {}
        for market, values in (payload or {}).items():
            holidays[market.upper()] = {date.fromisoformat(str(value)) for value in values}
        return cls(holidays)

    def is_holiday(self, market: str, session_date: date) -> bool:
        return session_date in self._holidays.get(market.upper(), set())


@dataclass(frozen=True)
class _Schedule:
    timezone_name: str
    sessions: tuple[tuple[time, time], ...]


SCHEDULES: dict[str, _Schedule] = {
    "CN": _Schedule(
        timezone_name="Asia/Shanghai",
        sessions=((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))),
    ),
    "HK": _Schedule(
        timezone_name="Asia/Hong_Kong",
        sessions=((time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))),
    ),
    "US": _Schedule(
        timezone_name="America/New_York",
        sessions=((time(9, 30), time(16, 0)),),
    ),
}


class MarketClock:
    def __init__(self, holiday_calendar: HolidayCalendar | None = None) -> None:
        self.holiday_calendar = holiday_calendar or StaticHolidayCalendar()

    def status(self, market: str, at: datetime | None = None) -> MarketStatus:
        market_key = market.upper()
        if market_key not in SCHEDULES:
            raise ValueError(f"unsupported market: {market}")
        instant = at or datetime.now(tz=timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("market clock input must include a timezone")
        schedule = SCHEDULES[market_key]
        local = instant.astimezone(ZoneInfo(schedule.timezone_name))
        if local.weekday() >= 5:
            return MarketStatus(market_key, MarketPhase.WEEKEND, local, False)
        if self.holiday_calendar.is_holiday(market_key, local.date()):
            return MarketStatus(market_key, MarketPhase.HOLIDAY, local, False)

        local_time = local.timetz().replace(tzinfo=None)
        for start, end in schedule.sessions:
            if start <= local_time < end:
                return MarketStatus(market_key, MarketPhase.OPEN, local, True)
        first_open = schedule.sessions[0][0]
        last_close = schedule.sessions[-1][1]
        if local_time < first_open:
            phase = MarketPhase.PRE_OPEN
        elif len(schedule.sessions) > 1 and schedule.sessions[0][1] <= local_time < schedule.sessions[1][0]:
            phase = MarketPhase.LUNCH_BREAK
        elif local_time >= last_close:
            phase = MarketPhase.CLOSED
        else:
            phase = MarketPhase.CLOSED
        return MarketStatus(market_key, phase, local, False)
