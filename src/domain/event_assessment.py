from __future__ import annotations

from typing import Any


EVENT_STATUSES = {
    "VALID_NO_HIGH_IMPACT_EVENT",
    "VALID_EVENTS_FOUND",
    "DATA_INSUFFICIENT",
    "SOURCE_ERROR",
}


def build_event_assessment(macro_result: dict[str, Any]) -> dict[str, Any]:
    calendar_status = str(macro_result.get("event_calendar_data_status") or "").upper()
    events = list(macro_result.get("events", []) or [])
    released_data_missing = any(
        str(event.get("status") or "").upper() == "RELEASED_DATA_MISSING"
        or (
            str(event.get("event_data_status") or "").upper() in {"PENDING_RELEASE", "DATA_INSUFFICIENT"}
            and str(event.get("release_at_report_timezone") or event.get("release_at") or "")
            <= str(macro_result.get("as_of") or "")
        )
        for event in events
    )
    if macro_result.get("error") or calendar_status == "ERROR":
        status = "SOURCE_ERROR"
    elif calendar_status in {"UNAVAILABLE", "PARTIAL", ""} or released_data_missing:
        status = "DATA_INSUFFICIENT"
    elif macro_result.get("has_high_event_next_7_days"):
        status = "VALID_EVENTS_FOUND"
    else:
        status = "VALID_NO_HIGH_IMPACT_EVENT"
    if status not in EVENT_STATUSES:
        raise ValueError(f"Invalid event assessment status: {status}")
    gate_passed, reasons = event_gate_for_status(status)
    return {
        "status": status,
        "event_gate_passed": gate_passed,
        "reasons": reasons,
        "events": events,
        "high_impact_events": list(macro_result.get("high_risk_events_7d", []) or []),
        "source_status": calendar_status or "UNAVAILABLE",
        "as_of": macro_result.get("as_of"),
    }


def event_gate_for_status(status: str) -> tuple[bool, list[str]]:
    mapping = {
        "VALID_NO_HIGH_IMPACT_EVENT": (True, []),
        "VALID_EVENTS_FOUND": (False, ["存在已核验高影响事件"]),
        "DATA_INSUFFICIENT": (False, ["事件数据不足，不能静默通过"]),
        "SOURCE_ERROR": (False, ["事件数据源失败，不能静默通过"]),
    }
    if status not in mapping:
        raise ValueError(f"Unknown event status: {status}")
    return mapping[status]
