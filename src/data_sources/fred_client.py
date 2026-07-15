from __future__ import annotations

from datetime import date, datetime, time
import csv
from io import StringIO
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo


BASE_URL = "https://api.stlouisfed.org/fred"
FRESHNESS_DAYS = {
    "DGS10": 10,
    "DGS2": 10,
    "T10Y2Y": 10,
    "BAMLH0A0HYM2": 10,
    "CPIAUCSL": 60,
    "PPIACO": 60,
    "PCEPI": 60,
    "UNRATE": 60,
    "GDP": 150,
}
MONTHLY_SERIES = {"CPIAUCSL", "PPIACO", "PCEPI", "UNRATE"}
QUARTERLY_SERIES = {"GDP"}


def _api_key() -> str:
    return os.getenv("FRED_API_KEY", "").strip()


def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    key = _api_key()
    if not key:
        if path != "series/observations" or not params.get("series_id"):
            raise RuntimeError("FRED_API_KEY 未配置")
        # FRED's official graph CSV endpoint is public and preserves the actual
        # observation date. It is a Level-1 fallback, not a realtime quote.
        series_id = params["series_id"]
        csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        with urlopen(csv_url, timeout=20) as response:  # noqa: S310 - official FRED URL
            rows = list(csv.DictReader(StringIO(response.read().decode("utf-8"))))
        observations = [
            {"date": row.get("observation_date") or row.get("DATE"), "value": row.get(series_id)}
            for row in rows
            if row.get(series_id) not in (None, "", ".")
        ]
        limit = int(params.get("limit", "2") or 2)
        return {"observations": list(reversed(observations[-limit:]))}
    url = f"{BASE_URL}/{path}?{urlencode({**params, 'api_key': key, 'file_type': 'json'})}"
    with urlopen(url, timeout=20) as response:  # noqa: S310 - controlled FRED URL
        return json.loads(response.read().decode("utf-8"))


def _frequency(series_id: str) -> str:
    if series_id in MONTHLY_SERIES:
        return "monthly"
    if series_id in QUARTERLY_SERIES:
        return "quarterly"
    return "daily"


def get_series_latest(series_id: str) -> dict[str, Any]:
    payload = _get_json(
        "series/observations",
        {"series_id": series_id, "sort_order": "desc", "limit": "2"},
    )
    observations = payload.get("observations") or []
    if not observations:
        raise RuntimeError(f"FRED {series_id} 返回空数据")
    observation = observations[0]
    value = observation.get("value")
    if value in (None, "", "."):
        raise RuntimeError(f"FRED {series_id} 最新值不可用")

    fetched = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
    observed_date = str(observation.get("date") or "")
    try:
        observed = date.fromisoformat(observed_date)
        age_days = max(0, (fetched.date() - observed).days)
        observed_at = datetime.combine(observed, time.min, tzinfo=ZoneInfo("America/New_York"))
        age_hours = round((fetched.astimezone(ZoneInfo("America/New_York")) - observed_at).total_seconds() / 3600, 1)
    except ValueError:
        age_days = 9999
        age_hours = None
        observed_at = None
    stale = age_days > FRESHNESS_DAYS.get(series_id, 60)
    previous = observations[1].get("value") if len(observations) > 1 else None
    previous_value = None if previous in (None, "", ".") else float(previous)
    return {
        "series_id": series_id,
        "value": float(value),
        "previous_value": previous_value,
        "date": observed_date or None,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "observation_date": observed_date or None,
        "quote_timestamp": None,
        "fetched_at": fetched.isoformat(),
        "market_timezone": "America/New_York",
        "data_frequency": _frequency(series_id),
        "data_session": "official_lagged_macro",
        "freshness_status": "stale" if stale else "official_lagged",
        "age_hours": age_hours,
        "source_level": 1,
        "comparable_date": observed_date or None,
        "status": "ok",
        "source": "fred",
        "published_at": None,
        "retrieved_at": fetched.isoformat(),
        "stale": stale,
        "age_days": age_days,
        "is_realtime": False,
        "cache_used": False,
        "cache_stale": False,
    }
