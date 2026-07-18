from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.macro.macro_calendar import get_upcoming_high_risk_events

from .models import GridSignal, GridSymbolState, RiskReview
from .position_manager import estimate_tech_exposure_yuan


def _event_within_48h(events: list[dict[str, Any]], as_of: date | datetime | None = None) -> bool:
    """Grid uses the same authoritative selector as risk and reporting."""
    return bool(get_upcoming_high_risk_events(as_of or date.today(), hours=48, events=events))


def review_grid_signal(
    *,
    signal: GridSignal,
    state: GridSymbolState,
    decision: dict[str, Any],
    portfolio_result: dict[str, Any],
    grid_budget: dict[str, Any],
    config: dict[str, Any],
    symbol_cfg: dict[str, Any],
    price_available: bool,
) -> RiskReview:
    smart = config.get("smart_grid", {})
    risk_cfg = smart.get("risk", {})
    reasons: list[str] = []
    paper_mode = bool(smart.get("paper_mode", True))
    require_dqs = int(risk_cfg.get("require_dqs", 85) or 85)
    quality = decision.get("data_quality_snapshot", decision.get("dqs", {})) or {}
    dqs_score = int(quality.get("grid_dqs", 0) or 0)
    dqs_mode = str(quality.get("mode", "safe"))

    if signal.action == "NONE":
        reasons.append(signal.reason)
    if not price_available:
        reasons.append("核心行情数据缺失。")
    if dqs_score < require_dqs:
        reasons.append(f"DQS={dqs_score}，低于网格金额建议门槛{require_dqs}。")
    if risk_cfg.get("block_if_dqs_mode_not_exact", True) and dqs_mode != "exact":
        reasons.append("DQS模式不是 exact，禁止精确网格金额建议。")
    if decision.get("budget", {}).get("confirmed_cash_available_yuan", 0) <= 0 and signal.action == "BUY":
        reasons.append("现金低于或接近安全线，禁止新增买入。")
    if grid_budget.get("live_available_yuan", 0) <= 0 and signal.action == "BUY" and not paper_mode:
        reasons.append("实盘网格预算不足。")
    decision_as_of: date | datetime
    try:
        decision_as_of = datetime.fromisoformat(str(decision.get("generated_at")).replace("Z", "+00:00")) if decision.get("generated_at") else date.fromisoformat(str(decision.get("date")))
    except (TypeError, ValueError):
        decision_as_of = date.today()
    if _event_within_48h(decision.get("events", []), decision_as_of):
        reasons.append("未来48小时内存在高等级宏观事件，进入谨慎模式。")
    if signal.action == "BUY" and state.month_trade_count >= int(risk_cfg.get("max_monthly_trades_per_symbol", 8)):
        reasons.append("本月该标的网格交易次数已达上限。")
    if signal.action in {"BUY", "SELL"} and state.day_trade_count >= int(symbol_cfg.get("max_daily_trades", 1)):
        reasons.append("当日该标的网格交易次数已达上限。")
    if signal.action == "BUY" and state.consecutive_buys >= int(symbol_cfg.get("max_consecutive_buys", 3)):
        reasons.append("连续买入层数达到上限。")
    if signal.action == "SELL" and signal.quantity > max(0.0, state.grid_quantity):
        reasons.append("卖出数量会触及核心仓。")
    us_row = next((row for row in decision.get("allocation", []) if row.get("category") == "美股"), {})
    if signal.action == "SELL" and us_row.get("status") == "严重低配":
        reasons.append("整体美股仍严重低配，普通网格卖出被总风控否决。")
    if signal.action == "SELL" and state.sell_spacing_pct * 10000 <= float(smart.get("transaction_cost", {}).get("estimated_slippage_bps", 5)) + float(smart.get("transaction_cost", {}).get("min_expected_profit_bps", 20)):
        reasons.append("扣除交易成本和滑点后预期利润不足。")
    if signal.symbol in {"QQQ", "QQQM"} and signal.action == "BUY":
        total_assets = float(
            ((decision.get("portfolio_snapshot", {}) or {}).get("total_valued_assets"))
            or decision.get("portfolio_value_yuan", 0)
            or 0
        )
        exposure = estimate_tech_exposure_yuan(portfolio_result)
        limit_pct = float(symbol_cfg.get("tech_concentration_limit_pct", 18) or 18)
        if total_assets and exposure / total_assets * 100 >= limit_pct:
            reasons.append(f"科技风险暴露约{exposure / total_assets * 100:.1f}%，达到QQQ网格限制。")
    if paper_mode:
        reasons.append("当前为模拟模式，禁止生成真实执行金额建议。")
    if smart.get("auto_trade") is not False:
        reasons.append("auto_trade必须保持false。")

    approved = signal.action != "NONE" and not reasons and not paper_mode
    rejected = signal.action != "NONE" and bool(reasons)
    if signal.action == "NONE":
        final_advice = "继续监控，不执行。"
    elif paper_mode:
        final_advice = "仅记录模拟信号，不生成真实交易建议。"
    elif approved:
        final_advice = "通过总风控，可提交用户人工确认。"
    else:
        final_advice = "总风控否决，不执行。"
    return RiskReview(signal.symbol, approved, rejected, paper_mode, reasons, final_advice)
