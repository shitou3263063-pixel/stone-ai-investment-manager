from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from utils.logger import write_log


CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/fulltextSearch/full"
HKEX_SEARCH_URL = "https://www1.hkexnews.hk/search/titlesearch.xhtml"


def _now() -> str:
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _get_json(url: str, params: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={
            "Accept": "application/json",
            "Referer": "https://www.cninfo.com.cn/",
            "User-Agent": "Mozilla/5.0 Stone-AI-Investment-Manager/12.6",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official disclosure endpoint
        return json.loads(response.read().decode("utf-8"))


def fetch_cninfo_announcements(symbol: str = "002558", days: int = 30) -> dict[str, Any]:
    end = date.today()
    start = end - timedelta(days=days)
    try:
        payload = _get_json(
            CNINFO_SEARCH_URL,
            {
                "searchkey": symbol,
                "sdate": start.isoformat(),
                "edate": end.isoformat(),
                "isfulltext": "false",
                "sortName": "pubdate",
                "sortType": "desc",
                "pageNum": 1,
                "pageSize": 10,
            },
        )
        announcements = payload.get("announcements") or payload.get("classifiedAnnouncements") or []
        records = []
        for row in announcements[:10]:
            records.append({
                "symbol": symbol,
                "title": row.get("announcementTitle") or row.get("announcementTitleList") or row.get("title"),
                "published_at": row.get("announcementTime") or row.get("pubdate") or row.get("announcementTimeStr"),
                "document_url": row.get("adjunctUrl") or row.get("url"),
                "source": "cninfo_official",
            })
        return {
            "status": "ok", "source": "cninfo_official", "source_level": 1,
            "source_type": "official_disclosure_platform", "fetched_at": _now(),
            "market_date": end.isoformat(), "timezone": "Asia/Shanghai", "records": records,
            "record_count": len(records), "fallback_used": False, "error_message": "",
        }
    except Exception as exc:  # noqa: BLE001 - announcements are enhancement data
        write_log(f"巨潮资讯公告读取失败：{exc}", filename="stone_ai.log")
        return {
            "status": "failed", "source": "cninfo_official", "source_level": 1,
            "source_type": "official_disclosure_platform", "fetched_at": _now(),
            "market_date": None, "timezone": "Asia/Shanghai", "records": [], "record_count": 0,
            "fallback_used": False, "error_message": str(exc),
        }


def build_hkex_announcement_framework(symbols: list[str] | None = None) -> dict[str, Any]:
    symbols = symbols or ["03033"]
    return {
        "status": "framework_ready",
        "source": "hkex_official",
        "source_level": 1,
        "source_type": "official_exchange_disclosure",
        "fetched_at": _now(),
        "market_date": None,
        "timezone": "Asia/Hong_Kong",
        "symbols": symbols,
        "records": [],
        "record_count": 0,
        "fallback_used": False,
        "search_url": HKEX_SEARCH_URL,
        "error_message": "HKEX标题检索需要稳定的证券内部ID映射；P1A仅建立官方读取框架，不伪造公告记录。",
    }


def fetch_official_announcement_snapshot() -> dict[str, Any]:
    cn = fetch_cninfo_announcements()
    hk = build_hkex_announcement_framework()
    return {
        "fetched_at": _now(),
        "status": "ok" if cn.get("status") == "ok" else "partial",
        "cn": cn,
        "hk": hk,
        "policy": "仅登记官方公告；接口失败或内部ID未核验时不以其他网页替代。",
    }
