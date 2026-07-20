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
    calendar_missing_items = list(macro_result.get("calendar_missing_items", []) or [])
    future_event_gate = macro_result.get("future_event_gate", {}) or {}
    explicit_verified_coverage = (
        macro_result.get("verified_event_coverage")
        if "verified_event_coverage" in macro_result
        else future_event_gate.get("verified_event_coverage")
    )
    verified_event_coverage = (
        bool(explicit_verified_coverage)
        if explicit_verified_coverage is not None
        else calendar_status == "VALID" and not calendar_missing_items
    )

    if macro_result.get("error") or calendar_status == "ERROR":
        status = "SOURCE_ERROR"
    elif (
        calendar_status in {"UNAVAILABLE", "PARTIAL", ""}
        or calendar_missing_items
        or not verified_event_coverage
    ):
        status = "DATA_INSUFFICIENT"
    elif macro_result.get("has_high_event_next_7_days"):
        status = "VALID_EVENTS_FOUND"
    else:
        status = "VALID_NO_HIGH_IMPACT_EVENT"
    if status not in EVENT_STATUSES:
        raise ValueError(f"Invalid event assessment status: {status}")
    gate_passed, reasons = event_gate_for_status(status)
    missing_data: list[dict[str, Any]] = []
    released_data_issues: list[dict[str, Any]] = []
    for event in events:
        event_status = str(event.get("status") or "").upper()
        if event_status not in {"RELEASED_FETCH_FAILED", "PARTIAL_DATA"}:
            continue
        release = event.get("economic_release_data", {}) or {}
        released_data_issues.append(
            {
                "item": event.get("event_name") or event.get("name") or "UNKNOWN_EVENT",
                "missing_fields": [
                    field
                    for field in ("actual_value", "previous_value", "consensus_value", "revision")
                    if release.get(field, event.get(field)) in {None, ""}
                ],
                "data_source": release.get("source") or event.get("release_data_source") or "official_release_source",
                "last_success_at": release.get("as_of") or "无成功记录",
                "score_deduction_item": "opportunity_dqs.released_macro_event_data_quality",
                "release_status": event_status,
            }
        )
    for item in macro_result.get("calendar_missing_items", []) or []:
        missing_data.append(
            {
                "item": str(item),
                "missing_fields": ["release_at_utc", "verification_status"],
                "data_source": "event_calendar",
                "last_success_at": macro_result.get("last_success_at") or "无成功记录",
                "score_deduction_item": "core_dqs.事件状态",
            }
        )
    if status == "DATA_INSUFFICIENT" and not missing_data:
        missing_data.append(
            {
                "item": "event_calendar_coverage",
                "missing_fields": ["verified_event_coverage"],
                "data_source": macro_result.get("source") or "event_calendar",
                "last_success_at": macro_result.get("last_success_at") or "无成功记录",
                "score_deduction_item": "core_dqs.事件状态",
            }
        )
    return {
        "status": status,
        "event_gate_passed": gate_passed,
        "reasons": reasons,
        "events": events,
        "high_impact_events": list(macro_result.get("high_risk_events_7d", []) or []),
        "position_level_event_risk": macro_result.get("position_level_event_risk") or {"status": "UNKNOWN", "events": []},
        "portfolio_level_event_risk": macro_result.get("portfolio_level_event_risk") or {"status": "UNKNOWN", "events": []},
        "future_event_gate": macro_result.get("future_event_gate") or {},
        "released_data_quality": macro_result.get("released_data_quality") or {},
        "released_data_issues": released_data_issues,
        "missing_data": missing_data,
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
