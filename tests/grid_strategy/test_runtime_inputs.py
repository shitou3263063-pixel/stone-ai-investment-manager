from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import yaml

from src.grid.long_term_v1.runtime import (
    GridRuntimeDataProvider,
    _load_latest_formal_run,
)
from src.grid.long_term_v1.risk_inputs import fetch_usd_cny


NOW = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)


def _payload(*, generated_at: str, dqs: int | None = 90, risk: int | None = 40) -> dict:
    payload = {
        "report_metadata": {
            "report_business_date": generated_at[:10],
            "report_generated_at": generated_at,
        },
        "dqs_results": {"grid_dqs": {"total": dqs}},
        "risk_snapshot": {"score": risk},
        "market_snapshot": {"market": {"USD/CNY": {"current_price": 7.2}}},
        "validation": {"ok": True},
    }
    return payload


def _write(root: Path, name: str, payload: dict) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _provider(tmp_path: Path) -> GridRuntimeDataProvider:
    provider = GridRuntimeDataProvider.__new__(GridRuntimeDataProvider)
    provider.root = tmp_path
    provider.config = {
        "runtime_inputs": {
            "formal_input_paths": ["reports/run_status.json"],
            "formal_max_age_minutes": 1440,
            "formal_timezone": "Asia/Shanghai",
            "dqs_name": "grid_dqs",
            "fx_symbol": "USD/CNY",
        },
        "risk_gates": {"vix_max_age_seconds": 300},
    }
    return provider


def test_dqs_reads_latest_same_day_formal_value(tmp_path: Path) -> None:
    _write(tmp_path, "reports/run_status.json", _payload(generated_at="2026-07-24T20:00:00+08:00", dqs=88))
    result = _provider(tmp_path)._bundle_inputs(now=NOW)
    assert result["dqs"] == 88
    assert result["metadata"]["dqs"]["validity"] == "VALID"
    assert result["metadata"]["dqs"]["report_date"] == "2026-07-24"


def test_missing_dqs_is_unavailable_not_zero(tmp_path: Path) -> None:
    _write(tmp_path, "reports/run_status.json", _payload(generated_at="2026-07-24T20:00:00+08:00", dqs=None))
    result = _provider(tmp_path)._bundle_inputs(now=NOW)
    assert result["dqs"] is None
    assert "DQS_UNAVAILABLE" in result["errors"]


def test_stale_dqs_and_risk_are_unavailable(tmp_path: Path) -> None:
    _write(tmp_path, "reports/run_status.json", _payload(generated_at="2026-07-22T20:00:00+08:00", dqs=95, risk=40))
    result = _provider(tmp_path)._bundle_inputs(now=NOW)
    assert result["dqs"] is None
    assert result["risk_score"] is None
    assert result["metadata"]["dqs"]["validity"] == "STALE"


def test_risk_score_53_remains_real_value_when_fresh(tmp_path: Path) -> None:
    _write(tmp_path, "reports/run_status.json", _payload(generated_at="2026-07-24T20:00:00+08:00", dqs=90, risk=53))
    result = _provider(tmp_path)._bundle_inputs(now=NOW)
    assert result["risk_score"] == 53
    assert result["metadata"]["risk_score"]["validity"] == "VALID"


def test_report_date_timezone_mismatch_is_stale(tmp_path: Path) -> None:
    payload = _payload(generated_at="2026-07-23T23:30:00+00:00", dqs=90, risk=40)
    payload["report_metadata"]["report_business_date"] = "2026-07-23"
    _write(tmp_path, "reports/run_status.json", payload)
    result = _provider(tmp_path)._bundle_inputs(now=NOW)
    assert result["dqs"] is None
    assert result["metadata"]["dqs"]["validity"] == "STALE"


def test_multiple_runs_choose_latest_valid_version(tmp_path: Path) -> None:
    older = _write(tmp_path, "reports/run_status_old.json", _payload(generated_at="2026-07-24T18:00:00+08:00", dqs=86, risk=45))
    newer = _write(tmp_path, "reports/run_status_new.json", _payload(generated_at="2026-07-24T20:00:00+08:00", dqs=91, risk=42))
    candidate, metadata = _load_latest_formal_run(
        tmp_path,
        {"formal_input_paths": [str(older), str(newer)], "formal_timezone": "Asia/Shanghai"},
        now=NOW,
    )
    assert candidate is not None
    assert candidate["dqs_results"]["grid_dqs"]["total"] == 91
    assert metadata is not None and metadata["source"].endswith("run_status_new.json")


def test_same_day_conflicting_formal_inputs_are_flagged(tmp_path: Path) -> None:
    first = _write(tmp_path, "reports/run_status_a.json", _payload(generated_at="2026-07-24T18:00:00+08:00", dqs=86, risk=45))
    second = _write(tmp_path, "reports/run_status_b.json", _payload(generated_at="2026-07-24T20:00:00+08:00", dqs=91, risk=42))
    provider = _provider(tmp_path)
    provider.config["runtime_inputs"]["formal_input_paths"] = [str(first), str(second)]
    result = provider._bundle_inputs(now=NOW)
    assert result["dqs"] is not None
    assert "DQS_INPUT_CONFLICT" in result["errors"]
    assert "RISK_SCORE_INPUT_CONFLICT" in result["errors"]


def test_vix_official_fresh_value_is_used(tmp_path: Path, monkeypatch) -> None:
    provider = _provider(tmp_path)
    import src.data_sources.data_router as router

    monkeypatch.setattr(
        router,
        "_official_vix_quote",
        lambda: {"close": 18.0, "source": "cboe_official", "published_at": "2026-07-24 14:59:00"},
    )
    value, timestamp, error, metadata = provider._vix(now=NOW)
    assert value == 18.0
    assert timestamp is not None
    assert error == ""
    assert metadata["validity"] == "VALID"


def test_vix_stale_value_is_blocked(tmp_path: Path, monkeypatch) -> None:
    provider = _provider(tmp_path)
    import src.data_sources.data_router as router

    monkeypatch.setattr(
        router,
        "_official_vix_quote",
        lambda: {"close": 18.0, "source": "cboe_official", "published_at": "2026-07-24 14:00:00"},
    )
    value, timestamp, error, metadata = provider._vix(now=NOW)
    assert value == 18.0
    assert timestamp is not None
    assert error == "VIX_STALE"
    assert metadata["validity"] == "STALE"


def test_grid_thresholds_remain_unchanged(tmp_path: Path) -> None:
    config = yaml.safe_load((Path(__file__).parents[2] / "config" / "long_term_grid.yaml").read_text(encoding="utf-8"))
    assert config["risk_gates"]["minimum_dqs"] == 85
    assert config["risk_gates"]["maximum_risk_score"] == 50
    assert config["risk_gates"]["maximum_vix"] == 40


def test_usd_cny_primary_quote_has_current_day_metadata() -> None:
    value, metadata, errors = fetch_usd_cny(
        now=NOW,
        settings={"fx_max_age_seconds": 900},
        primary_fetch=lambda: {
            "value": 7.2,
            "source": "data_router",
            "quote_timestamp": "2026-07-24T14:59:00+00:00",
        },
        fallback_fetch=lambda: (_ for _ in ()).throw(AssertionError("fallback not expected")),
    )
    assert value == 7.2
    assert metadata["validity"] == "VALID"
    assert metadata["source"] == "data_router"
    assert metadata["age_minutes"] == 1.0
    assert errors == ()


def test_usd_cny_primary_failure_uses_independent_fallback() -> None:
    value, metadata, errors = fetch_usd_cny(
        now=NOW,
        settings={"fx_max_age_seconds": 900},
        primary_fetch=lambda: (_ for _ in ()).throw(RuntimeError("primary down")),
        fallback_fetch=lambda: {
            "value": 7.21,
            "source": "fallback_fx",
            "quote_timestamp": "2026-07-24T14:58:00+00:00",
        },
    )
    assert value == 7.21
    assert metadata["validity"] == "VALID"
    assert metadata["fallback_used"] is True
    assert metadata["fallback_source"] == "fallback_fx"
    assert errors and errors[0].startswith("USD_CNY_PRIMARY_")


def test_usd_cny_two_failures_remain_unavailable() -> None:
    value, metadata, errors = fetch_usd_cny(
        now=NOW,
        settings={"fx_max_age_seconds": 900},
        primary_fetch=lambda: (_ for _ in ()).throw(RuntimeError("primary down")),
        fallback_fetch=lambda: (_ for _ in ()).throw(RuntimeError("fallback down")),
    )
    assert value is None
    assert metadata["validity"] == "MISSING"
    assert "USD_CNY_PRIMARY_RUNTIMEERROR" in errors
    assert "USD_CNY_FALLBACK_RUNTIMEERROR" in errors


def test_usd_cny_yesterday_is_not_silently_accepted() -> None:
    value, metadata, _ = fetch_usd_cny(
        now=NOW,
        settings={"fx_max_age_seconds": 86400},
        primary_fetch=lambda: {
            "value": 7.2,
            "source": "data_router",
            "quote_timestamp": "2026-07-23T23:59:00+00:00",
        },
        fallback_fetch=lambda: (_ for _ in ()).throw(RuntimeError("no fallback")),
    )
    assert value is None
    assert metadata["validity"] == "STALE"
