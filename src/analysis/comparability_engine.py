from __future__ import annotations

from datetime import date, datetime
from typing import Any


FREQUENCY_BY_METRIC = {
    "CPI": "monthly", "CPIAUCSL": "monthly", "PPI": "monthly", "PPIACO": "monthly",
    "PCE": "monthly", "PCEPI": "monthly", "UNRATE": "monthly", "GDP": "quarterly",
    "DGS10": "daily_official", "DGS2": "daily_official", "T10Y2Y": "daily_official",
    "BAMLH0A0HYM2": "daily_official",
}

DEFAULT_WINDOWS = {
    "daily_market": 5,
    "daily_official": 5,
    "monthly": 50,
    "quarterly": 130,
    "event_driven": 14,
    "manual": 30,
}


def _date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _status_usable(row: dict[str, Any]) -> bool:
    status = str(row.get("data_status") or row.get("status") or "").upper()
    return bool(row.get("success", status in {"OK", "SUCCESS", "VALID", "VALID_LAGGED_BY_DESIGN"}))


def build_comparability_snapshot(
    rows: list[dict[str, Any]],
    *,
    decision_as_of: str,
    settings: dict[str, Any] | None = None,
    blocking_dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Compare data against its own frequency, never by identical calendar date."""
    settings = settings or {}
    windows = {**DEFAULT_WINDOWS, **(settings.get("frequency_windows_days") or {})}
    decision_date = _date(decision_as_of) or date.today()
    blocking = set(blocking_dimensions or settings.get("blocking_dimensions") or ["VOO", "^VIX", "DGS10"])
    observations: list[dict[str, Any]] = []
    for row in rows:
        metric = str(row.get("name") or row.get("metric") or row.get("symbol") or "UNKNOWN")
        frequency = str(row.get("frequency") or FREQUENCY_BY_METRIC.get(metric) or "daily_market")
        observed = _date(row.get("comparable_date") or row.get("market_date") or row.get("published_at") or row.get("observed_at"))
        age = (decision_date - observed).days if observed else None
        alias = {
            "daily_market": "DAILY_MARKET_CLOSE",
            "daily_official": "DAILY_OFFICIAL_MACRO",
            "monthly": "MONTHLY",
            "quarterly": "QUARTERLY",
            "event_driven": "EVENT_DRIVEN",
        }.get(frequency, frequency.upper())
        window = int(windows.get(frequency, windows.get(alias, DEFAULT_WINDOWS.get(frequency, 5))))
        usable = _status_usable(row)
        within_window = usable and age is not None and 0 <= age <= window
        if within_window:
            data_status = "VALID" if frequency == "daily_market" and age == 0 else "VALID_LAGGED_BY_DESIGN"
            comp_status = "COMPARABLE"
        elif not usable:
            data_status = str(row.get("data_status") or row.get("status") or "DATA_INSUFFICIENT").upper()
            comp_status = "DATA_NOT_COMPARABLE"
        else:
            data_status = "DATA_INSUFFICIENT"
            comp_status = "DATA_NOT_COMPARABLE"
        observations.append({
            "metric": metric,
            "metric_frequency": frequency,
            "observation_date": observed.isoformat() if observed else None,
            "reference_window": f"<={window} calendar days",
            "lag_days": age,
            "release_lag": row.get("release_lag"),
            "market_status": row.get("market_status"),
            "session_status": row.get("data_session"),
            "data_status": data_status,
            "source": row.get("source"),
            "comparability_status": comp_status,
            "is_blocking_dimension": metric in blocking,
        })
    comparable = [row for row in observations if row["comparability_status"] == "COMPARABLE"]
    noncomparable = [row for row in observations if row["comparability_status"] != "COMPARABLE"]
    blocking_noncomparable = [row for row in noncomparable if row["is_blocking_dimension"]]
    coverage = len(comparable) / len(observations) if observations else 0.0
    minimum = float(settings.get("overall_coverage_min", 0.75) or 0.75)
    final_status = "COMPARABLE" if coverage >= minimum and not blocking_noncomparable else "DATA_NOT_COMPARABLE"
    return {
        "as_of": decision_as_of,
        "observations": observations,
        "comparable_dimensions": [row["metric"] for row in comparable],
        "non_comparable_dimensions": [row["metric"] for row in noncomparable],
        "blocking_non_comparable_dimensions": [row["metric"] for row in blocking_noncomparable],
        "coverage_pct": round(coverage * 100, 2),
        "confidence": "high" if coverage == 1 else "medium" if final_status == "COMPARABLE" else "low",
        "final_status": final_status,
    }
