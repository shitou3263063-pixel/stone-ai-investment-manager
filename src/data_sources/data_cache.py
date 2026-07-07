from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import json
import re
from typing import Any

from utils.data_loader import project_root
from utils.logger import write_log


CACHE_MAX_AGE_DAYS = 7


def _cache_dir() -> Path:
    path = project_root() / "data" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())


def cache_path(namespace: str, key: str) -> Path:
    return _cache_dir() / f"{_safe_key(namespace)}__{_safe_key(key)}.json"


def write_cache(namespace: str, key: str, data: dict[str, Any], source: str) -> None:
    payload = {
        "namespace": namespace,
        "key": key,
        "source": source,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "data": data,
    }
    try:
        cache_path(namespace, key).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - cache failure must not break reports
        write_log(f"缓存写入失败 {namespace}/{key}: {exc}", filename="data_router.log")


def read_cache(namespace: str, key: str, max_age_days: int = CACHE_MAX_AGE_DAYS) -> dict[str, Any] | None:
    path = cache_path(namespace, key)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at_text = str(payload.get("fetched_at", ""))
        fetched_at = datetime.fromisoformat(fetched_at_text)
        age = datetime.now() - fetched_at
        stale = age > timedelta(days=max_age_days)
        data = dict(payload.get("data", {}) or {})
        data.update(
            {
                "source": payload.get("source", "cache"),
                "fetched_at": fetched_at_text,
                "cache_used": True,
                "cache_stale": stale,
                "cache_age_days": round(age.total_seconds() / 86400, 2),
                "warning": "数据可能过期，请谨慎使用。" if stale else "",
            }
        )
        return data
    except Exception as exc:  # noqa: BLE001 - corrupted cache must not break reports
        write_log(f"缓存读取失败 {namespace}/{key}: {exc}", filename="data_router.log")
        return None
