from __future__ import annotations

from datetime import date

import pytest

from src.data_sources import akshare_client, hkma_client
from src.reports.report_center import build_run_status


def test_03033_and_hstech_use_distinct_sina_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, tuple[str, dict]] = {}

    def fake_fetch(**kwargs):
        calls[kwargs["interface"]] = (kwargs["api_name"], kwargs["kwargs"])
        return {
            "interface": kwargs["interface"], "symbol": kwargs["symbol"], "status": "failed",
            "source": "akshare", "underlying_provider": akshare_client.UNDERLYING_SOURCES[kwargs["interface"]],
            "scoring_eligible": False,
        }

    monkeypatch.setattr(akshare_client, "installed_version", lambda: "1.18.64")
    monkeypatch.setattr(akshare_client, "_fetch_interface", fake_fetch)
    monkeypatch.setattr(akshare_client, "write_akshare_outputs", lambda snapshot: None)
    akshare_client.fetch_akshare_p1a_snapshot({})
    assert calls["03033_history"] == ("stock_hk_daily", {"symbol": "03033", "adjust": "qfq"})
    assert calls["hstech_history"] == ("stock_hk_index_daily_sina", {"symbol": "HSTECH"})
    assert calls["03033_history"] != calls["hstech_history"]


def test_03033_and_hstech_metadata_are_not_substituted() -> None:
    assert akshare_client.OFFICIAL_NAMES["03033.HK"] == "南方东英恒生科技指数ETF"
    assert akshare_client.OFFICIAL_NAMES["HSTECH"] == "恒生科技指数"
    assert akshare_client.OFFICIAL_NAMES["03033.HK"] != akshare_client.OFFICIAL_NAMES["HSTECH"]


def test_hk_history_normalization_records_sina_and_date() -> None:
    payload = {"records": [{"date": "2026-07-14T00:00:00.000", "close": 4.6, "volume": 100}]}
    record = akshare_client._normalise_history("03033_history", "03033.HK", "HKD", payload)
    assert record["symbol"] == "03033.HK"
    assert record["underlying_provider"] == "sina_finance"
    assert record["market_date"] == "2026-07-14"
    assert record["currency"] == "HKD"


def test_hstech_history_is_index_not_03033() -> None:
    payload = {"records": [{"date": "2026-07-14", "close": 4679.45996, "volume": 100}]}
    record = akshare_client._normalise_history("hstech_history", "HSTECH", "HKD", payload)
    assert record["symbol"] == "HSTECH"
    assert record["official_name"] == "恒生科技指数"
    assert record["symbol"] != "03033.HK"
    assert record["unit"] == "index_points"


def test_same_day_03033_conflict_is_detected() -> None:
    record = akshare_client._normalise_history(
        "03033_history", "03033.HK", "HKD",
        {"records": [{"date": "2026-07-14", "close": 4.6}]},
    )
    result = akshare_client._apply_price_conflict(record, {
        "close": 5.0, "source": "yfinance", "market_date": "2026-07-14",
        "currency": "HKD", "freshness_status": "fresh",
    })
    assert result["error_code"] == "SOURCE_CONFLICT"
    assert result["scoring_eligible"] is False


def test_hkma_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("timed out")
        return {"header": {"success": True}, "result": {"records": [{
            "end_of_day": date.today().isoformat(), "ir_1m": 2.9,
        }]}}

    hkma_client._DATASET_FAILURES.clear()
    monkeypatch.setattr(hkma_client, "_get_json", fake_get)
    monkeypatch.setattr(hkma_client, "write_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(hkma_client.time, "sleep", lambda *_: None)
    row, meta = hkma_client._fetch_dataset("hibor", {"segment": "hibor.fixing"}, "end_of_day")
    assert row["ir_1m"] == 2.9
    assert meta["status"] == "ok"
    assert meta["attempts"] == 2


def test_hkma_proxy_failure_opens_dataset_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def fail(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("ProxyError: connection reset")

    hkma_client._DATASET_FAILURES.clear()
    monkeypatch.setattr(hkma_client, "_get_json", fail)
    monkeypatch.setattr(hkma_client, "read_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(hkma_client.time, "sleep", lambda *_: None)
    _, first = hkma_client._fetch_dataset("exchange_rate", {}, "end_of_day")
    count_after_first = calls["count"]
    _, second = hkma_client._fetch_dataset("exchange_rate", {}, "end_of_day")
    assert first["error_code"] == "NETWORK_PROXY_ERROR"
    assert first["circuit_open"] is True
    assert second["error_code"] == "CIRCUIT_OPEN"
    assert calls["count"] == count_after_first


def test_run_status_exposes_hk_dates_and_sources() -> None:
    decision = {
        "date": "2026-07-15", "generated_at": "2026-07-15T08:30:00+08:00",
        "data_cutoff": "2026-07-15T08:29:00+08:00", "portfolio_value_yuan": 2821100,
        "today_trade": False, "trade_type": "无操作", "targets": "不适用",
        "funding_source": "不适用", "next_review_date": "2026-07-16",
        "no_trade_reasons": ["测试"], "market_table": [], "opportunity": [],
        "dqs": {"score": 65, "mode_label": "方向", "blocking_errors": []},
        "risk": {"score": 60, "level": "中高风险"},
        "budget": {"today_total_yuan": 0, "account_total_cash_yuan": 220000,
                   "cash_safety_reserve_yuan": 220000, "investable_cash_yuan": 0},
        "consistency": {"errors": [], "warnings": []},
        "cn_hk_analysis_completeness": {
            "cn_analysis_completeness": {"score_pct": 90},
            "hk_analysis_completeness": {"score_pct": 65},
        },
        "cn_hk_p1a": {
            "tushare": {},
            "akshare": {"market_references": {
                "03033.HK": {"status": "ok", "market_date": "2026-07-14", "underlying_provider": "sina_finance"},
                "HSTECH": {"status": "ok", "market_date": "2026-07-14", "underlying_provider": "sina_finance"},
            }},
            "hkma": {"datasets": {
                "hibor": {"status": "ok", "market_date": "2026-06-30", "freshness": "stale"},
                "exchange_rate": {"status": "ok", "market_date": "2026-06-30", "freshness": "stale"},
            }},
        },
    }
    status = build_run_status(decision, report_files=[], email_status="skipped")
    hk = status["cn_hk_p1a"]
    assert hk["akshare_03033_history_date"] == "2026-07-14"
    assert hk["akshare_hstech_history_date"] == "2026-07-14"
    assert hk["hkma_hibor_date"] == "2026-06-30"
    assert hk["hkma_hibor_freshness"] == "stale"
