from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest

from src.data_sources import announcement_client, cn_hk_p1a, hkma_client, tushare_client
from src.decision.v12_1_decision import (
    build_opportunity_scores,
    load_strategy,
    resolve_next_review_datetime,
)
from src.reports.report_center import build_run_status, generate_daily_report


def _allocation() -> list[dict]:
    values = {
        "美股": (385000, -0.16), "港股": (272600, -0.02), "A股": (266500, -0.01),
        "债券": (1130000, 0.15), "黄金": (547000, 0.04), "现金": (220000, 0.00),
    }
    return [
        {"category": category, "current_amount_yuan": amount, "deviation_ratio": deviation}
        for category, (amount, deviation) in values.items()
    ]


def _quote(symbol: str, currency: str, timezone: str) -> dict:
    return {
        "symbol": symbol, "close": 10.0, "previous_close": 9.9, "change_pct": 1.01,
        "status": "ok", "data_status": "VALID", "source": "yfinance", "source_level": 3,
        "fetched_at": f"{date.today().isoformat()}T15:00:00+08:00", "market_date": date.today().isoformat(),
        "freshness_status": "fresh", "currency": currency, "timezone": timezone,
        "candidates": [], "source_count": 1, "missing_fields": [],
    }


def _p1a_snapshot() -> dict:
    return {
        "generated_at": f"{date.today().isoformat()}T18:00:00+08:00",
        "tushare": {
            "configured": True,
            "trade_calendar": {"status": "ok", "latest_open_date": date.today().isoformat()},
            "valuation": {"items": {
                "002558.SZ": {
                    "status": "ok", "freshness": "fresh", "valuation_basis": "security_itself",
                    "metrics": {"pe_ttm": 18.0, "pb": 2.2},
                },
                "510300.SS": {
                    "status": "ok", "freshness": "fresh", "valuation_basis": "benchmark_index_000300.SH",
                    "metrics": {"pe_ttm": 13.0, "pb": 1.4},
                },
            }},
            "fundamentals": {"002558.SZ": {
                "status": "ok", "successful_statement_count": 4,
                "statements": {"financial_indicators": {
                    "status": "ok", "freshness": "fresh",
                    "metrics": {"roe": 18.0, "netprofit_margin": 22.0, "debt_to_assets": 25.0, "or_yoy": 12.0, "netprofit_yoy": 16.0},
                }},
            }},
        },
        "hkma": {
            "status": "ok",
            "metrics": {"hibor_1m_pct": 2.1, "usd_hkd": 7.8, "aggregate_balance_hkd_mn": 120000},
            "datasets": {
                "hibor": {"status": "ok", "freshness": "fresh"},
                "exchange_rate": {"status": "ok", "freshness": "fresh"},
                "liquidity": {"status": "ok", "freshness": "fresh"},
            },
        },
        "announcements": {"status": "ok", "cn": {"status": "ok", "record_count": 1}, "hk": {"status": "framework_ready", "record_count": 0}},
        "analysis_completeness": {
            "cn_analysis_completeness": {"score_pct": 100.0, "decision_restricted": False, "missing_fields": []},
            "hk_analysis_completeness": {"score_pct": 95.0, "decision_restricted": False, "missing_fields": ["港交所官方公告"]},
        },
    }


def _live_market(p1a: dict | None = None) -> dict:
    items = {
        "510300.SS": _quote("510300.SS", "CNY", "Asia/Shanghai"),
        "002558.SZ": _quote("002558.SZ", "CNY", "Asia/Shanghai"),
        "03033.HK": _quote("03033.HK", "HKD", "Asia/Hong_Kong"),
        "513060.SS": _quote("513060.SS", "CNY", "Asia/Shanghai"),
        "513090.SS": _quote("513090.SS", "CNY", "Asia/Shanghai"),
    }
    return {
        "items": items,
        "macro": {"items": {}},
        "market_completeness": {
            "cn_data_completeness": {"score_pct": 100.0, "decision_restricted": False},
            "hk_data_completeness": {"score_pct": 100.0, "decision_restricted": False},
        },
        "cn_hk_p1a": p1a or _p1a_snapshot(),
    }


def test_tushare_missing_token_degrades_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN_NOT_CONFIGURED"):
        tushare_client.query("trade_cal", {}, ["cal_date"])


def test_tushare_payload_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tushare_client, "_post_json", lambda payload: {
        "code": 0, "msg": None, "data": {"fields": ["ts_code", "pe_ttm"], "items": [["002558.SZ", 18.2]]},
    })
    rows = tushare_client.query("daily_basic", {"ts_code": "002558.SZ"}, ["ts_code", "pe_ttm"], token="test")
    assert rows == [{"ts_code": "002558.SZ", "pe_ttm": 18.2}]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("抱歉，您没有接口(trade_cal)访问权限", "PERMISSION_DENIED"),
        ("抱歉，您的积分不足，至少需要2000积分", "INSUFFICIENT_POINTS"),
        ("每分钟最多访问10次，请稍后重试", "RATE_LIMITED"),
        ("empty_response", "EMPTY_RESPONSE"),
    ],
)
def test_tushare_error_classification(message: str, expected: str) -> None:
    code, summary = tushare_client.classify_tushare_error(message)
    assert code == expected
    assert summary


def test_tushare_error_summary_never_leaks_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "a" * 56
    monkeypatch.setenv("TUSHARE_TOKEN", token)
    code, summary = tushare_client.classify_tushare_error(f"permission denied token={token}")
    assert code == "PERMISSION_DENIED"
    assert token not in summary
    assert "***" in summary


def test_trade_calendar_supports_required_diagnostic_window(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_query(api_name: str, cache_key: str, params: dict, fields: list[str]):
        captured.update({"api_name": api_name, "params": params})
        return ([{"exchange": "SSE", "cal_date": "20260715", "is_open": 1}], {
            "status": "ok", "market_date": "2026-07-15", "records_count": 1,
        })

    monkeypatch.setattr(tushare_client, "_query_with_cache", fake_query)
    result = tushare_client.fetch_trade_calendar(
        date(2026, 7, 15), start_date="20260701", end_date="20260715"
    )
    assert captured == {
        "api_name": "trade_cal",
        "params": {"exchange": "SSE", "start_date": "20260701", "end_date": "20260715"},
    }
    assert result["latest_open_date"] == "2026-07-15"


def test_tushare_response_schema_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tushare_client, "_post_json", lambda payload: {"code": 0, "data": {"fields": [], "items": []}})
    with pytest.raises(tushare_client.TushareClientError) as captured:
        tushare_client.query("trade_cal", {}, ["cal_date"], token="test")
    assert captured.value.error_code == "EMPTY_RESPONSE"


def test_hkma_official_data_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, params: dict, timeout: int = 15) -> dict:
        if "daily-figures" in url:
            row = {"end_of_date": "2026-07-14", "closing_balance": 120000, "opening_balance": 119000, "cu_weakside": 7.85, "cu_strongside": 7.75}
        elif "hk-interbank" in url:
            row = {"end_of_day": "2026-07-14", "ir_overnight": 1.8, "ir_1m": 2.1, "ir_3m": 2.3}
        else:
            row = {"end_of_day": "2026-07-14", "usd": 7.8, "cny": 1.09}
        return {"header": {"success": True}, "result": {"records": [row]}}
    monkeypatch.setattr(hkma_client, "_get_json", fake_get)
    monkeypatch.setattr(hkma_client, "write_cache", lambda *args, **kwargs: None)
    result = hkma_client.fetch_hkma_liquidity_snapshot()
    assert result["status"] == "ok"
    assert result["metrics"]["hibor_1m_pct"] == 2.1
    assert result["metrics"]["usd_hkd"] == 7.8
    assert result["metrics"]["aggregate_balance_hkd_mn"] == 120000


def test_cninfo_announcement_framework_parses_official_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(announcement_client, "_get_json", lambda *args, **kwargs: {
        "announcements": [{"announcementTitle": "2026年一季度报告", "announcementTime": "2026-04-27", "adjunctUrl": "/test.pdf"}]
    })
    result = announcement_client.fetch_cninfo_announcements()
    assert result["status"] == "ok"
    assert result["record_count"] == 1
    assert result["records"][0]["source"] == "cninfo_official"


def test_002558_fundamentals_enter_score_but_etfs_do_not() -> None:
    rows = build_opportunity_scores(_allocation(), _live_market(), load_strategy())
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["002558.SZ"]["financial_model"] == "single_stock_fundamental"
    assert "Tushare:002558财务指标" in by_symbol["002558.SZ"]["p1a_inputs_used"]
    for symbol in ["510300.SS", "03033.HK", "513060.SS", "513090.SS"]:
        assert by_symbol[symbol]["financial_model"] == "not_applicable_etf"
        assert "Tushare:002558财务指标" not in by_symbol[symbol]["p1a_inputs_used"]


def test_hkma_liquidity_enters_hk_scoring_only() -> None:
    rows = build_opportunity_scores(_allocation(), _live_market(), load_strategy())
    by_symbol = {row["symbol"]: row for row in rows}
    assert "HKMA:1个月HIBOR" in by_symbol["03033.HK"]["p1a_inputs_used"]
    assert "HKMA:1个月HIBOR" not in by_symbol["510300.SS"]["p1a_inputs_used"]


def test_stale_hkma_metric_does_not_enter_scoring() -> None:
    p1a = _p1a_snapshot()
    p1a["hkma"]["datasets"]["hibor"]["freshness"] = "stale"
    rows = build_opportunity_scores(_allocation(), _live_market(p1a), load_strategy())
    by_symbol = {row["symbol"]: row for row in rows}
    assert "HKMA:1个月HIBOR" not in by_symbol["03033.HK"]["p1a_inputs_used"]
    assert "HKMA:银行体系总结余" in by_symbol["03033.HK"]["p1a_inputs_used"]


def test_low_p1a_completeness_restricts_cn_hk_advice() -> None:
    p1a = _p1a_snapshot()
    p1a["analysis_completeness"]["cn_analysis_completeness"]["score_pct"] = 45
    p1a["analysis_completeness"]["hk_analysis_completeness"]["score_pct"] = 35
    rows = build_opportunity_scores(_allocation(), _live_market(p1a), load_strategy())
    target_symbols = {"510300.SS", "002558.SZ", "03033.HK", "513060.SS", "513090.SS"}
    target = [row for row in rows if row["symbol"] in target_symbols]
    assert target
    assert all(row["decision_restricted"] for row in target)
    assert all(row["advice"] in {"观察", "等待数据补齐", "继续持有", "暂停新增", "风险复核或回避"} for row in target)


def test_p1a_output_files_are_machine_readable(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cn_hk_p1a, "project_root", lambda: tmp_path)
    snapshot = _p1a_snapshot()
    p0 = {"cn_data_completeness": {"score_pct": 100}, "hk_data_completeness": {"score_pct": 100}}
    cn_hk_p1a.write_p1a_outputs(snapshot, p0)
    expected = [
        "cn_hk_p1a_validation.json", "cn_hk_fundamental_snapshot.json", "cn_hk_valuation_snapshot.json",
        "hk_liquidity_snapshot.json", "cn_hk_announcement_snapshot.json", "cn_hk_data_coverage.json",
    ]
    for name in expected:
        assert json.loads((tmp_path / "outputs" / name).read_text(encoding="utf-8"))


def test_run_status_exposes_p1a_state() -> None:
    decision = {
        "date": "2026-07-14", "generated_at": "2026-07-14T08:30:00+08:00", "data_cutoff": "2026-07-14T08:29:00+08:00",
        "portfolio_value_yuan": 2821100, "today_trade": False, "trade_type": "无操作", "targets": "不适用", "funding_source": "不适用",
        "next_review_date": "2026-07-15", "no_trade_reasons": ["测试"], "market_table": [], "opportunity": [],
        "dqs": {"score": 75, "mode_label": "区间", "blocking_errors": []}, "risk": {"score": 56, "level": "中高风险"},
        "budget": {"today_total_yuan": 0, "account_total_cash_yuan": 220000, "cash_safety_reserve_yuan": 220000, "investable_cash_yuan": 0},
        "consistency": {"errors": [], "warnings": []},
        "cn_hk_p1a": _p1a_snapshot(), "cn_hk_analysis_completeness": _p1a_snapshot()["analysis_completeness"],
    }
    status = build_run_status(decision, report_files=[], email_status="skipped")
    assert status["cn_hk_p1a"]["cn_analysis_completeness"] == 100.0
    assert status["cn_hk_p1a"]["hkma_status"] == "ok"
    assert status["cn_hk_p1a"]["tushare_status"] == "missing"
    assert "tushare_002558_valuation_status" in status["cn_hk_p1a"]
    assert "tushare_002558_fundamental_status" in status["cn_hk_p1a"]
    assert "tushare_csi300_valuation_status" in status["cn_hk_p1a"]


def test_next_review_is_strictly_after_run_time_and_handles_offsets() -> None:
    result = resolve_next_review_datetime(
        "2026-07-14T23:59:13+08:00",
        macro_candidate="2026-07-14T20:30:00+08:00",
        dca_candidate="2026-07-15",
    )
    assert datetime.fromisoformat(result) > datetime.fromisoformat("2026-07-14T23:59:13+08:00")
    assert result.startswith("2026-07-15T08:30:00")

    ny_candidate = "2026-07-15T09:00:00-04:00"
    result_ny = resolve_next_review_datetime(
        "2026-07-15T08:00:00+08:00",
        macro_candidate=ny_candidate,
        dca_candidate="2026-07-20",
    )
    assert datetime.fromisoformat(result_ny).astimezone(timezone.utc) == datetime.fromisoformat(ny_candidate).astimezone(timezone.utc)


def test_p1a_utf8_json_round_trip_preserves_chinese(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cn_hk_p1a, "project_root", lambda: tmp_path)
    snapshot = _p1a_snapshot()
    snapshot["tushare"]["error_summary"] = "接口权限不足，不影响主程序运行"
    p0 = {"cn_data_completeness": {"score_pct": 45}, "hk_data_completeness": {"score_pct": 65}}
    cn_hk_p1a.write_p1a_outputs(snapshot, p0)
    path = tmp_path / "outputs" / "cn_hk_p1a_validation.json"
    raw = path.read_bytes()
    decoded = raw.decode("utf-8")
    parsed = json.loads(decoded)
    assert parsed["tushare_error_summary"] == "接口权限不足，不影响主程序运行"

    from src.reports import report_center
    markdown_path = tmp_path / "p1a.md"
    markdown_path.write_text("\n".join(report_center._cn_hk_p1a_table({"cn_hk_p1a": snapshot})), encoding="utf-8")
    markdown = markdown_path.read_bytes().decode("utf-8")
    assert "A股整体分析" in markdown
    assert "港股整体分析" in markdown


def test_report_includes_p1a_status_section() -> None:
    # Report rendering is exercised with the production decision fixture style by checking the section helper path.
    from src.reports import report_center
    decision = {"cn_hk_p1a": _p1a_snapshot()}
    text = "\n".join(report_center._cn_hk_p1a_table(decision))
    assert "A股整体分析" in text
    assert "HKMA官方" in text
    assert "ETF财务规则" in text
    assert "002558个股财务评分" in text
