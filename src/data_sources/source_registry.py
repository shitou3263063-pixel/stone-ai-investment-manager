from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.data_loader import load_config, project_root


REGISTRY_PATH = project_root() / "config" / "source_registry.yaml"


@dataclass(frozen=True)
class SourceDefinition:
    provider_name: str
    source_tier: int
    supported_markets: tuple[str, ...]
    supported_fields: tuple[str, ...]
    expected_frequency: str
    timeout: int
    retry_policy: dict[str, Any]
    cache_policy: dict[str, Any]
    freshness_policy: dict[str, Any]
    authentication_required: bool
    license_or_usage_note: str
    fallback_order: tuple[str, ...]
    enabled: bool
    health_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "source_tier": self.source_tier,
            "supported_markets": list(self.supported_markets),
            "supported_fields": list(self.supported_fields),
            "expected_frequency": self.expected_frequency,
            "timeout": self.timeout,
            "retry_policy": dict(self.retry_policy),
            "cache_policy": dict(self.cache_policy),
            "freshness_policy": dict(self.freshness_policy),
            "authentication_required": self.authentication_required,
            "license_or_usage_note": self.license_or_usage_note,
            "fallback_order": list(self.fallback_order),
            "enabled": self.enabled,
            "health_status": self.health_status,
        }


class DataSourceRegistry:
    """Single authority for provider metadata, routing order and health state."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.defaults = dict(payload.get("source_defaults", {}) or {})
        self.sources = dict(payload.get("sources", {}) or {})
        self.metrics = dict(payload.get("critical_metrics", {}) or {})

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "DataSourceRegistry":
        return cls(load_config(path) if path.exists() else {})

    def source(self, provider_name: str) -> SourceDefinition:
        configured = {**self.defaults, **(self.sources.get(provider_name, {}) or {})}
        tier = configured.get("source_tier", configured.get("tier", 99))
        return SourceDefinition(
            provider_name=provider_name,
            source_tier=int(tier or 99),
            supported_markets=tuple(configured.get("supported_markets", []) or []),
            supported_fields=tuple(configured.get("supported_fields", []) or []),
            expected_frequency=str(configured.get("expected_frequency") or "UNKNOWN"),
            timeout=int(configured.get("timeout", 20) or 20),
            retry_policy=dict(configured.get("retry_policy", {}) or {}),
            cache_policy=dict(configured.get("cache_policy", {}) or {}),
            freshness_policy=dict(configured.get("freshness_policy", {}) or {}),
            authentication_required=bool(configured.get("authentication_required", False)),
            license_or_usage_note=str(configured.get("license_or_usage_note") or "usage subject to provider terms"),
            fallback_order=tuple(configured.get("fallback_order", []) or []),
            enabled=bool(configured.get("enabled", True)),
            health_status=str(configured.get("health_status") or "UNKNOWN").upper(),
        )

    def provider_order(self, metric: str, *, default: list[str] | None = None) -> list[str]:
        spec = self.metrics.get(metric, {}) or {}
        ordered = [spec.get("primary_source"), spec.get("backup_source")]
        ordered.extend(spec.get("fallback_order", []) or [])
        ordered.extend(default or [])
        result: list[str] = []
        for provider in ordered:
            name = str(provider or "").strip()
            if not name or name in result:
                continue
            definition = self.source(name)
            if definition.enabled and definition.health_status not in {"DISABLED", "RETIRED"}:
                result.append(name)
        return result

    def metric(self, name: str) -> dict[str, Any]:
        return dict(self.metrics.get(name, {}) or {})

    def health_snapshot(self) -> dict[str, Any]:
        return {name: self.source(name).to_dict() for name in sorted(self.sources)}
