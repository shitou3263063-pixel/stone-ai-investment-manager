from __future__ import annotations

from typing import Any


SCENARIO_LABELS = {
    "equity_bull": "情景A：权益牛市",
    "range_market": "情景B：震荡市场",
    "risk_shock": "情景C：风险冲击",
    "equity_bond_drawdown": "情景D：股债双杀",
    "inflation_reacceleration": "情景E：通胀重新上行",
    "liquidity_shock": "情景F：流动性冲击",
    "financial_crisis_2008": "情景G：2008式金融危机",
    "equity_bond_selloff_2022": "情景H：2022式股债双杀",
    "rates_up_200bp": "情景I：利率再上升200个基点",
    "hk_tech_crash_40": "情景J：港股科技再下跌40%",
    "global_liquidity_crisis": "情景K：全球流动性危机",
}


def calculate_portfolio_stress_scenarios(
    allocation: list[dict[str, Any]],
    scenario_config: dict[str, Any],
    stress_exposures: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按当前资产金额做静态情景测算；结果用于风险理解，不生成交易指令。"""
    exposure_rows = stress_exposures or allocation
    total = sum(float(row.get("current_amount_yuan", 0) or 0) for row in exposure_rows)
    tolerance_low = float(scenario_config.get("max_drawdown_tolerance_low", 0.25) or 0.25)
    tolerance_high = float(scenario_config.get("max_drawdown_tolerance_high", 0.35) or 0.35)
    results: list[dict[str, Any]] = []

    keys = [key for key in SCENARIO_LABELS if key in scenario_config]
    for key in keys:
        assumptions = scenario_config.get(key, {}) or {}
        contributions: list[dict[str, Any]] = []
        total_change = 0.0
        for row in exposure_rows:
            category = str(row.get("category", ""))
            amount = float(row.get("current_amount_yuan", 0) or 0)
            shock = float(assumptions.get(category, 0) or 0)
            impact = amount * shock
            total_change += impact
            contributions.append(
                {
                    "category": category,
                    "assumption": shock,
                    "impact_yuan": round(impact),
                }
            )

        portfolio_return = total_change / total if total else 0.0
        results.append(
            {
                "key": key,
                "name": SCENARIO_LABELS[key],
                "assumptions": {str(k): float(v) for k, v in assumptions.items()},
                "portfolio_return": round(portfolio_return, 6),
                "portfolio_change_yuan": round(total_change),
                "contributions": contributions,
                "largest_contributors": sorted(contributions, key=lambda item: abs(item["impact_yuan"]), reverse=True)[:3],
                "largest_contributor": (sorted(contributions, key=lambda item: abs(item["impact_yuan"]), reverse=True) or [{"category": "暂无", "impact_yuan": 0}])[0],
                "exceeds_tolerance_low": portfolio_return < -tolerance_low,
                "exceeds_tolerance_high": portfolio_return < -tolerance_high,
                "long_term_allocation_review_required": portfolio_return < -tolerance_low,
                "note": "静态假设测算，不是预测，不直接形成自动交易指令。",
            }
        )
    return results
