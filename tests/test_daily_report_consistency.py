from __future__ import annotations

from copy import deepcopy

from src.reports.bundle_report import render_daily_report
from tests.test_final_decision_bundle import _fixture_bundle


def _report_bundle() -> dict:
    return deepcopy(_fixture_bundle())


def _risk_56(bundle: dict) -> None:
    components = [
        {"item": "估值", "score": 10, "weight": 20, "basis": "估值数据不完整时按中性风险处理。"},
        {"item": "波动率", "score": 5, "weight": 15, "basis": "VIX有效。"},
        {"item": "利率", "score": 13, "weight": 15, "basis": "官方日度数据有效。"},
        {"item": "宏观事件", "score": 5, "weight": 15, "basis": "未来7天暂无高等级事件。"},
        {"item": "趋势", "score": 8, "weight": 10, "basis": "正式收盘有效。"},
        {"item": "政策与地缘", "score": 7, "weight": 10, "basis": "按现有信息计分。"},
        {
            "item": "市场宽度与资金流",
            "score": 8,
            "weight": 15,
            "basis": "市场宽度与ETF资金流缺失可核验风险值。",
            "data_status": "MISSING_NEUTRAL",
            "confidence": "low",
        },
    ]
    bundle["risk_snapshot"] = {
        "score": 56,
        "market_risk_weights_sum": 100,
        "market_risk": {
            "score": 56,
            "components": components,
            "market_risk_weights_sum": 100,
            "confidence": "low",
        },
    }


def test_event_insufficient_report_uses_non_deterministic_coverage_conclusion() -> None:
    bundle = _report_bundle()
    _risk_56(bundle)
    bundle["event_assessment"]["status"] = "DATA_INSUFFICIENT"

    report = render_daily_report(bundle)

    expected = "已获取数据中未发现高等级事件，但事件覆盖不足，不能确认未来7天不存在高等级事件。"
    assert expected in report
    assert "未来7天暂无高等级事件" not in report


def test_risk_table_labels_weighted_points_without_recalculating_total() -> None:
    bundle = _report_bundle()
    _risk_56(bundle)

    report = render_daily_report(bundle)

    assert "| 风险因子 | 依据 | 风险得分 | 该项最高分 | 对总风险贡献 | 缺失处理 |" in report
    assert "子分数" not in report
    assert "| **合计** | 置信度：low | **56** | **100** | **56点** | - |" in report


def test_missing_risk_inputs_show_neutral_score_and_lower_confidence() -> None:
    bundle = _report_bundle()
    _risk_56(bundle)

    report = render_daily_report(bundle)

    expected = "数据缺失，按中性风险计分并降低置信度。"
    assert report.count(expected) >= 2
    assert "不适用（数据有效）" not in report


def test_allocation_repair_wording_matches_each_current_deviation() -> None:
    bundle = _report_bundle()
    bundle["asset_allocation"] = [
        {"category": "美股", "current_amount_yuan": 1, "current_ratio": 0.12, "target_ratio": 0.30, "deviation_ratio": -0.18, "status": "严重低配"},
        {"category": "债券", "current_amount_yuan": 1, "current_ratio": 0.41, "target_ratio": 0.25, "deviation_ratio": 0.16, "status": "严重超配"},
        {"category": "黄金", "current_amount_yuan": 1, "current_ratio": 0.19, "target_ratio": 0.15, "deviation_ratio": 0.04, "status": "超配"},
        {"category": "A股", "current_amount_yuan": 1, "current_ratio": 0.10, "target_ratio": 0.10, "deviation_ratio": 0, "status": "接近目标"},
        {"category": "港股", "current_amount_yuan": 1, "current_ratio": 0.10, "target_ratio": 0.12, "deviation_ratio": -0.02, "status": "接近目标"},
        {"category": "现金", "current_amount_yuan": 1, "current_ratio": 0.08, "target_ratio": 0.08, "deviation_ratio": 0, "status": "接近目标"},
    ]
    bundle["portfolio_snapshot"]["portfolio_repair_priority"] = [
        {"category": "美股", "repair_direction": "ADD_WITH_NEW_MONEY", "portfolio_repair_priority": 90},
        {"category": "债券", "repair_direction": "REDUCE_OR_PAUSE_NEW_MONEY", "portfolio_repair_priority": 56},
        {"category": "黄金", "repair_direction": "REDUCE_OR_PAUSE_NEW_MONEY", "portfolio_repair_priority": 15},
        {"category": "A股", "repair_direction": "MAINTAIN", "portfolio_repair_priority": 2},
        {"category": "港股", "repair_direction": "MAINTAIN", "portfolio_repair_priority": 7},
        {"category": "现金", "repair_direction": "MAINTAIN", "portfolio_repair_priority": 2},
    ]

    report = render_daily_report(bundle)

    assert "新增资金优先修复，优先宽基ETF，不强制一次性完成" in report
    assert "暂停新增，通过新增权益资金逐步稀释，不默认强制卖出" in report
    assert "暂停新增，观察后续偏离" in report
    assert report.count("维持现有配置") == 3


def test_next_window_distinguishes_twice_monthly_cadence_from_weekly_cap() -> None:
    bundle = _report_bundle()
    trade = bundle["portfolio_snapshot"]["confirmed_transactions"][0]
    trade.update({"trade_origin": "SCHEDULED_BASE_DCA", "order_type": "base_dca"})
    bundle["report_data"]["budget"] = {"next_dca_date": "2026-08-05"}

    report = render_daily_report(bundle)

    assert "DCA cadence：每月两次（每月第1、3个周三" in report
    assert "同一自然周最多执行一次是频率上限，不代表每周定投" in report
    assert "上一次执行日期：2026-07-15" in report
    assert "下一次理论执行日期：2026-08-05" in report
    assert "第2、4、5周的周三不属于当前每月第1、3周执行计划" in report


def test_opportunity_macro_event_deduction_is_rendered_as_audit_item() -> None:
    bundle = _report_bundle()
    bundle["dqs_results"]["opportunity_dqs"] = {
        "name": "opportunity_dqs",
        "total": 63,
        "breakdown": [
            {"item": "field_completeness", "score": 20, "max": 20},
            {"item": "timeliness", "score": 13, "max": 15},
            {"item": "source_quality", "score": 4, "max": 15},
            {"item": "dual_source_validation", "score": 1, "max": 15},
            {"item": "valuation_readiness", "score": 15, "max": 15},
            {"item": "transaction_reconciliation_quality", "score": 10, "max": 10},
            {"item": "consistency", "score": 10, "max": 10},
            {"item": "released_macro_event_data_quality", "score": -10, "max": 0},
        ],
    }

    report = render_daily_report(bundle)

    assert "#### 审计扣分项" in report
    assert report.count("released_macro_event_data_quality") == 1
    assert "普通评分小计 73 + 审计扣分 -10 = **63**" in report


def test_execution_dqs_100_has_directly_reconcilable_trade_audit_row() -> None:
    bundle = _report_bundle()
    trade = bundle["portfolio_snapshot"]["confirmed_transactions"][0]
    trade.update(
        {
            "invested_amount_cny": 9000,
            "fee_currency": "USD",
            "actual_fx_rate_cny_per_usd": None,
            "reconciliation_status": "RECONCILED",
        }
    )
    bundle["report_data"]["trade_reconciliation"] = {
        "status": "PASS",
        "transactions": [
            {
                "trade_id": "VOO-1",
                "status": "PASS",
                "position_total_quantity": 30.166,
                "fx_status": "NOT_APPLICABLE_USD_CASH",
            }
        ],
    }

    report = render_daily_report(bundle)

    assert "execution_dqs：**100**" in report
    assert "| 交易日期 | 标的 | 交易前数量 | 成交数量 | 交易后数量 | 成交金额 | 费用 | 汇率 | 现金变化 | 对账状态 |" in report
    assert "| 2026-07-15 | VOO | 28 | 2.166 | 30.166 | 1,499.955 USD（人民币等值记录9,000元） | 0 USD | 不适用（美元账户现金） | -1,499.955 USD | PASS |" in report
