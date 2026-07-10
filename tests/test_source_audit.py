from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from src.data_sources.source_audit import (
    apply_source_audit_to_market,
    build_source_audit,
    choose_preferred_source,
)


def _registry() -> dict:
    return {
        "policy": {
            "tier1_coverage_min": 0.80,
            "critical_metric_coverage_min": 0.85,
            "low_coverage_dqs_cap": 69,
            "trade_candidate_evidence": {
                "required_groups": [
                    "market_valuation",
                    "macro_fundamental",
                    "flow_volatility_behavior",
                ]
            },
        },
        "sources": {
            "fred": {"tier": 1, "type": "official"},
            "cboe_official": {"tier": 1, "type": "official"},
            "alpha_vantage": {"tier": 2, "type": "professional_api"},
            "finnhub": {"tier": 2, "type": "professional_api"},
            "yfinance": {"tier": 3, "type": "free_aggregator"},
            "media_news": {"tier": 4, "type": "media"},
        },
        "critical_metrics": {
            "VOO": {
                "category": "market",
                "metric_type": "price",
                "evidence_group": "market_valuation",
                "freshness_limit": "2d",
                "verification_requirement": "dual_source",
            },
            "^VIX": {
                "category": "index",
                "metric_type": "vix",
                "evidence_group": "flow_volatility_behavior",
                "freshness_limit": "2d",
                "verification_requirement": "dual_source",
            },
            "DGS10": {
                "category": "macro",
                "metric_type": "yield",
                "evidence_group": "macro_fundamental",
                "freshness_limit": "7d",
                "verification_requirement": "dual_source",
            },
        },
    }


class SourceAuditTest(unittest.TestCase):
    def test_official_source_wins_when_media_conflicts(self) -> None:
        now = datetime(2026, 7, 11, 8, 0, 0)
        preferred = choose_preferred_source(
            [
                {"source": "media_news", "value": 4.20, "status": "ok"},
                {"source": "fred", "value": 4.50, "status": "ok"},
            ],
            _registry(),
        )
        self.assertEqual(preferred["source"], "fred")
        market = {
            "items": {
                "VOO": {
                    "status": "ok",
                    "source": "media_news",
                    "close": 420,
                    "fetched_at": now.isoformat(),
                    "candidates": [
                        {"status": "ok", "source": "media_news", "close": 420, "fetched_at": now.isoformat()},
                        {"status": "ok", "source": "fred", "close": 450, "fetched_at": now.isoformat()},
                    ],
                }
            },
            "macro": {"items": {}},
            "data_quality": {"score": 90, "blocking_errors": []},
        }
        audit = build_source_audit(market, _registry(), now=now)
        self.assertEqual(audit["data_conflicts"][0]["preferred_source"], "fred")

    def test_single_non_official_key_source_blocks_precise_trade(self) -> None:
        now = datetime(2026, 7, 11, 8, 0, 0)
        market = {
            "items": {
                "VOO": {"status": "ok", "source": "yfinance", "close": 500, "fetched_at": now.isoformat()},
            },
            "macro": {"items": {}},
            "data_quality": {"score": 92, "blocking_errors": []},
        }
        audit = build_source_audit(market, _registry(), now=now)
        adjusted = apply_source_audit_to_market(market, audit)
        self.assertFalse(audit["precision_allowed"])
        self.assertLessEqual(adjusted["data_quality"]["score"], 69)
        self.assertIn("关键数据未完成双源验证", adjusted["data_quality"]["blocking_errors"])

    def test_stale_source_reduces_dqs(self) -> None:
        now = datetime(2026, 7, 11, 8, 0, 0)
        stale_time = (now - timedelta(days=10)).isoformat()
        market = {
            "items": {
                "VOO": {"status": "ok", "source": "alpha_vantage", "close": 500, "fetched_at": stale_time},
            },
            "macro": {"items": {}},
            "data_quality": {"score": 88, "blocking_errors": []},
        }
        audit = build_source_audit(market, _registry(), now=now)
        adjusted = apply_source_audit_to_market(market, audit)
        self.assertTrue(audit["stale_sources"])
        self.assertLessEqual(adjusted["data_quality"]["score"], 69)

    def test_scan_failure_does_not_claim_completion(self) -> None:
        market = {"items": {}, "macro": {"items": {}}, "data_quality": {"score": 90}}
        with patch("src.data_sources.source_audit.load_source_registry", side_effect=FileNotFoundError("missing")):
            audit = build_source_audit(market, registry=None)
        adjusted = apply_source_audit_to_market(market, audit)
        self.assertFalse(audit["scan_complete"])
        self.assertNotEqual(audit["message"], "全球权威数据扫描完成")
        self.assertLessEqual(adjusted["data_quality"]["score"], 69)


if __name__ == "__main__":
    unittest.main()
