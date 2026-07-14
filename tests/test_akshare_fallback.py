from __future__ import annotations

from datetime import date

import pytest

from src.data_sources import akshare_client, cn_hk_p1a
from src.decision.v12_1_decision import _p1a_record_usable


def _record(*, source: str = "akshare", value: float = 18.0, stale: bool = False) -> dict:
    return {
        "status": "cached" if stale else "ok",
        "source": source,
        "underlying_provider": "eastmoney" if source == "akshare" else None,
        "freshness": "stale" if stale else "fresh",
        "scoring_eligible": not stale,
        "metrics": {"pe_ttm": value, "pb": 2.0},
    }


def test_tushare_permission_failure_selects_akshare() -> None:
    tushare = {
        "trade_calendar": {"status": "failed", "error_code": "PERMISSION_DENIED"},
        "valuation": {"items": {"002558.SZ": {"status": "failed", "error_code": "PERMISSION_DENIED"}}},
        "fundamentals": {"002558.SZ": {"status": "failed", "error_code": "PERMISSION_DENIED"}},
    }
    akshare = {
        "trade_calendar": {**_record(), "latest_open_date": date.today().isoformat()},
        "valuation": {"items": {"002558.SZ": _record(), "510300.SS": _record(value=14.0)}},
        "fundamentals": {"002558.SZ": {**_record(), "validated_metric_count": 6}},
        "source_conflicts": [],
    }
    effective = cn_hk_p1a._build_effective_data(tushare, akshare)
    assert effective["selected_sources"]["002558_valuation"] == "akshare"
    assert effective["valuation"]["items"]["002558.SZ"]["underlying_provider"] == "eastmoney"


def test_akshare_adapter_failure_does_not_break_p1a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cn_hk_p1a, "fetch_tushare_p1a_snapshot", lambda: {"status": "failed"})
    monkeypatch.setattr(cn_hk_p1a, "fetch_akshare_p1a_snapshot", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(cn_hk_p1a, "fetch_hkma_liquidity_snapshot", lambda: {"status": "failed", "metrics": {}})
    monkeypatch.setattr(cn_hk_p1a, "fetch_official_announcement_snapshot", lambda: {"status": "failed", "cn": {}, "hk": {}})
    monkeypatch.setattr(cn_hk_p1a, "write_p1a_outputs", lambda *args, **kwargs: None)
    result = cn_hk_p1a.build_cn_hk_p1a_snapshot({})
    assert result["akshare"]["status"] == "failed"
    assert result["analysis_completeness"]["cn_analysis_completeness"]["decision_restricted"] is True


def test_akshare_empty_response_cannot_enter_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(akshare_client, "MAX_RETRIES", 0)
    monkeypatch.setattr(akshare_client, "_execute_api_with_timeout", lambda *args, **kwargs: {"columns": [], "records": []})
    monkeypatch.setattr(akshare_client, "read_cache", lambda *args, **kwargs: None)
    result = akshare_client._fetch_interface(
        interface="002558_valuation", api_name="unused", kwargs={}, symbol="002558.SZ",
        official_name="巨人网络", normalizer=akshare_client._normalise_002558_valuation,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "EMPTY_RESPONSE"
    assert result["scoring_eligible"] is False


def test_akshare_schema_change_is_explicit() -> None:
    result = akshare_client._normalise_002558_valuation({"records": [{"代码": "002558", "名称": "巨人网络"}]})
    assert result["status"] == "failed"
    assert result["error_code"] == "SCHEMA_CHANGED"


def test_akshare_underlying_provider_is_required() -> None:
    result = akshare_client._normalise_002558_valuation({
        "records": [{"代码": "002558", "市盈率-TTM": 20.0, "市净率-MRQ": 3.0}],
    })
    assert result["source"] == "akshare"
    assert result["underlying_provider"] == "eastmoney"
    assert result["market_date"] is None
    assert result["scoring_eligible"] is False


def test_akshare_source_conflict_blocks_scoring() -> None:
    record = {
        **_record(),
        "metrics": {"close": 12.0},
        "symbol": "002558.SZ",
        "market_date": "2026-07-14",
        "currency": "CNY",
    }
    result = akshare_client._apply_price_conflict(record, {
        "close": 10.0, "source": "yfinance", "market_date": "2026-07-14",
        "currency": "CNY", "freshness_status": "fresh",
    })
    assert result["error_code"] == "SOURCE_CONFLICT"
    assert result["scoring_eligible"] is False
    assert not _p1a_record_usable(result)


def test_different_market_dates_are_not_called_source_conflict() -> None:
    record = {
        **_record(), "metrics": {"close": 12.0}, "symbol": "002558.SZ",
        "market_date": "2026-07-14", "currency": "CNY",
    }
    result = akshare_client._apply_price_conflict(record, {
        "close": 10.0, "source": "yfinance", "market_date": "2026-07-13",
        "currency": "CNY", "freshness_status": "stale",
    })
    assert result.get("error_code") != "SOURCE_CONFLICT"
    assert result["comparison_status"] == "not_comparable"
    assert result["scoring_eligible"] is True


def test_missing_values_are_not_filled_with_zero() -> None:
    assert akshare_client._safe_float(None) is None
    result = akshare_client._normalise_002558_valuation({
        "records": [{"代码": "002558", "市盈率-TTM": None, "市净率-MRQ": 2.5}],
    })
    assert result["metrics"]["pe_ttm"] is None
    assert result["metrics"]["pb"] == 2.5


def test_etf_records_do_not_contain_stock_financial_model() -> None:
    payload = {"records": [{"日期": date.today().isoformat(), "收盘": 4.1, "成交量": 1000}]}
    result = akshare_client._normalise_history("510300_history", "510300.SS", "CNY", payload)
    assert result["display_only"] is True
    assert "statements" not in result
    assert result.get("model") != "single_stock_fundamental"


def test_002558_reporting_periods_are_not_mixed() -> None:
    fundamental = {
        **_record(), "reporting_period": "2026-03-31", "metrics": {"roe": 12.0},
        "statements": {"financial_indicators": {"metrics": {"roe": 12.0}}},
    }
    cashflow = {**_record(), "reporting_period": "2025-12-31", "metrics": {"operating_cash_flow": 1.0}}
    result = akshare_client._merge_fundamental_and_cashflow(fundamental, cashflow)
    assert result["period_consistent"] is False
    assert "operating_cash_flow" not in result["metrics"]
    assert result["scoring_eligible"] is False


def test_csi300_price_percentile_is_not_valuation() -> None:
    result = akshare_client._normalise_csi300_valuation({
        "records": [{"日期": date.today().isoformat(), "市盈率1": 14.8, "股息率1": 2.6}],
    })
    assert result["metrics"]["pe_ttm"] == 14.8
    assert result["metrics"]["pb"] is None
    assert result["price_percentile_used_as_valuation"] is False


def test_valid_cache_is_used_after_akshare_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(akshare_client, "MAX_RETRIES", 0)
    monkeypatch.setattr(akshare_client, "_execute_api_with_timeout", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout")))
    monkeypatch.setattr(akshare_client, "read_cache", lambda *args, **kwargs: {
        **_record(), "symbol": "002558.SZ", "cache_stale": False, "cache_age_days": 1,
        "market_date": date.today().isoformat(), "date_basis": "provider_explicit_market_date",
    })
    result = akshare_client._fetch_interface(
        interface="002558_valuation", api_name="unused", kwargs={}, symbol="002558.SZ",
        official_name="巨人网络", normalizer=akshare_client._normalise_002558_valuation,
    )
    assert result["status"] == "cached"
    assert result["fallback_used"] is True
    assert result["scoring_eligible"] is True


def test_stale_cache_cannot_support_high_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(akshare_client, "MAX_RETRIES", 0)
    monkeypatch.setattr(akshare_client, "_execute_api_with_timeout", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout")))
    monkeypatch.setattr(akshare_client, "read_cache", lambda *args, **kwargs: {
        **_record(stale=True), "symbol": "002558.SZ", "cache_stale": True, "cache_age_days": 9,
        "market_date": date.today().isoformat(), "date_basis": "provider_explicit_market_date",
    })
    result = akshare_client._fetch_interface(
        interface="002558_valuation", api_name="unused", kwargs={}, symbol="002558.SZ",
        official_name="巨人网络", normalizer=akshare_client._normalise_002558_valuation,
    )
    assert result["status"] == "cached"
    assert result["scoring_eligible"] is False
    assert not _p1a_record_usable(result)


def test_primary_source_wins_when_tushare_is_usable() -> None:
    tushare = {"valuation": {"items": {"002558.SZ": _record(source="tushare_pro", value=19.0)}}}
    akshare = {"valuation": {"items": {"002558.SZ": _record(value=18.0)}}, "source_conflicts": []}
    effective = cn_hk_p1a._build_effective_data(tushare, akshare)
    assert effective["selected_sources"]["002558_valuation"] == "tushare_pro"
    assert effective["valuation"]["items"]["002558.SZ"]["metrics"]["pe_ttm"] == 19.0
