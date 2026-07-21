from __future__ import annotations

from functools import lru_cache
from typing import Any

from utils.data_loader import load_config, project_root


def _key(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


@lru_cache(maxsize=1)
def security_aliases() -> dict[str, str]:
    payload = load_config(project_root() / "data" / "security_master.yaml")
    aliases: dict[str, str] = {}
    for row in payload.get("securities", []) or []:
        canonical = str(row.get("canonical_id") or row.get("ticker") or "").strip()
        if not canonical:
            continue
        values = {
            canonical,
            row.get("ticker"),
            row.get("pricing_proxy"),
            row.get("display_name"),
            *(row.get("aliases", []) or []),
        }
        for value in values:
            normalized = _key(value)
            if normalized:
                aliases[normalized] = canonical
        ticker = _key(row.get("ticker"))
        if ticker:
            aliases[f"{ticker}.US"] = canonical
    return aliases


def canonical_security_id(*values: Any) -> str:
    aliases = security_aliases()
    for value in values:
        normalized = _key(value)
        if normalized in aliases:
            return aliases[normalized]
        if normalized.endswith(".US") and normalized[:-3] in aliases:
            return aliases[normalized[:-3]]
    return next((_key(value) for value in values if _key(value)), "UNKNOWN")


@lru_cache(maxsize=1)
def security_definitions() -> dict[str, dict[str, Any]]:
    payload = load_config(project_root() / "data" / "security_master.yaml")
    return {
        str(row.get("canonical_id") or row.get("ticker")): dict(row)
        for row in payload.get("securities", []) or []
        if row.get("canonical_id") or row.get("ticker")
    }


def security_definition(security_id: str) -> dict[str, Any]:
    return security_definitions().get(canonical_security_id(security_id), {})
