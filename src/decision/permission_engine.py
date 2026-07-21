from __future__ import annotations

from typing import Any

from src.decision.scenario_dependencies import SCENARIO_DEPENDENCIES, scenario_dependency
from src.domain.dqs_result import DQS_BINDINGS


CANONICAL_PERMISSIONS = {
    "ALLOW_EXECUTION",
    "ALLOW_REDUCED_EXECUTION",
    "ALLOW_EVALUATION_ONLY",
    "ALLOW_SIMULATION_ONLY",
    "ALLOW_RECONCILIATION",
    "ACTIVE",
    "PARTIAL_MONITORING",
    "WARN",
    "DENY",
    "PASS",
    "FAIL",
}

SCENARIO_NAMES = {
    "scheduled_dca": "Scheduled DCA",
    "opportunity_add": "Opportunity Add",
    "strategic_rebalance": "Strategic Rebalance",
    "grid": "Grid Trading",
    "risk_monitoring": "Risk Monitoring",
    "transaction_reconciliation": "Transaction Reconciliation",
}

COMPARABILITY_KEYS = {
    "scheduled_dca": "core_decision_comparability",
    "opportunity_add": "cross_asset_comparability",
    "strategic_rebalance": "core_decision_comparability",
    "grid": "grid_snapshot_comparability",
}


def _event_is_complete(event_assessment: dict[str, Any]) -> bool:
    return str(event_assessment.get("status") or "") in {
        "VALID_NO_HIGH_IMPACT_EVENT",
        "VALID_EVENTS_FOUND",
    }


def _event_is_clear(event_assessment: dict[str, Any]) -> bool:
    return bool(event_assessment.get("event_gate_passed"))


def _event_reason(event_assessment: dict[str, Any]) -> str:
    reasons = [str(reason) for reason in event_assessment.get("reasons", []) or []]
    return "；".join(reasons) or f"事件状态={event_assessment.get('status') or 'DATA_INSUFFICIENT'}"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _base_row(
    *,
    scenario: str,
    dqs_name: str,
    dqs_score: int,
    required_dqs: int,
    dqs_gate: bool,
    plan_gate: bool,
    cash_gate: bool,
    risk_gate: bool,
    event_gate: bool,
    event_gate_applicable: bool,
    comp_gate: bool,
    permission: str,
    rejection_reasons: list[str],
    warning_reasons: list[str],
    live_rejection_reasons: list[str] | None = None,
) -> dict[str, Any]:
    dependency = scenario_dependency(scenario)
    if permission not in dependency["allowed_permissions"]:
        raise ValueError(f"{scenario} cannot use permission {permission}")
    hard_conditions = _dedupe(rejection_reasons)
    soft_conditions = _dedupe(warning_reasons)
    return {
        "scenario": scenario,
        "scenario_key": scenario,
        "scenario_name": SCENARIO_NAMES[scenario],
        "used_dqs_name": dqs_name,
        "dqs_score": dqs_score,
        "used_dqs_value": dqs_score,
        "required_dqs": required_dqs,
        "required_data": list(dependency["required_data"]),
        "optional_data": list(dependency["optional_data"]),
        "allowed_permissions": list(dependency["allowed_permissions"]),
        "risk_threshold": int(dependency["risk_threshold"]),
        "event_data_mode": dependency["event_data_mode"],
        "dqs_gate_passed": dqs_gate,
        "plan_gate_passed": plan_gate,
        "schedule_gate_passed": plan_gate,
        "cash_gate_passed": cash_gate,
        "risk_gate_passed": risk_gate,
        "event_gate_passed": event_gate,
        "event_gate_applicable": event_gate_applicable,
        "comparability_gate_passed": comp_gate,
        "comparability_gate": comp_gate,
        "hard_block_conditions": hard_conditions,
        "soft_warning_conditions": soft_conditions,
        "final_permission": permission,
        "rejection_reasons": hard_conditions,
        "blocking_reasons": hard_conditions,
        "exact_denial_reasons": hard_conditions,
        "warning_reasons": soft_conditions,
        "live_rejection_reasons": _dedupe(live_rejection_reasons or []),
        "manual_confirmation_required": permission in {
            "ALLOW_EXECUTION",
            "ALLOW_REDUCED_EXECUTION",
            "ALLOW_EVALUATION_ONLY",
            "ALLOW_RECONCILIATION",
        },
    }


def build_scenario_decisions(
    *,
    dqs_results: dict[str, dict[str, Any]],
    dqs_thresholds: dict[str, int],
    budget: dict[str, Any],
    risk: dict[str, Any],
    event_assessment: dict[str, Any],
    comparability: dict[str, Any],
    today_trade: bool,
) -> dict[str, Any]:
    """Build each scenario once using its declared dependency policy."""
    risk_score_raw = risk.get("score")
    risk_score = int(risk_score_raw) if risk_score_raw is not None else 100
    investable_cash = float(
        budget.get("confirmed_cash_available_yuan", budget.get("investable_cash_yuan", 0)) or 0
    )
    live_grid_cash = float(budget.get("live_grid_cash_yuan", 0) or 0)
    normal_dca_score = int(dqs_thresholds.get("scheduled_dca_normal", 75))
    event_complete = _event_is_complete(event_assessment)
    event_clear = _event_is_clear(event_assessment)
    event_reason = _event_reason(event_assessment)
    portfolio_available = bool(budget.get("portfolio_data_available", True))
    target_allocation_available = bool(budget.get("target_allocation_available", True))
    execution_data_available = bool(budget.get("execution_data_available", True))

    rows: list[dict[str, Any]] = []
    for scenario in SCENARIO_NAMES:
        dependency = SCENARIO_DEPENDENCIES[scenario]
        dqs_name = DQS_BINDINGS[scenario]
        dqs_score = int(dqs_results[dqs_name]["total"])
        required_dqs = int(dqs_thresholds[scenario])
        dqs_gate = dqs_score >= required_dqs
        comp_key = COMPARABILITY_KEYS.get(scenario)
        comp_status = str(comparability.get(comp_key) or "NOT_APPLICABLE")
        comp_gate = comp_status in {"COMPARABLE", "NOT_APPLICABLE", "NOT_EVALUATED"}
        risk_gate = risk_score <= int(dependency["risk_threshold"])
        plan_gate = True
        cash_gate = True
        event_gate = event_clear
        event_gate_applicable = dependency["event_data_mode"] != "ignored"
        rejection_reasons: list[str] = []
        warning_reasons: list[str] = []
        live_rejection_reasons: list[str] = []

        if scenario == "scheduled_dca":
            plan_gate = bool(budget.get("is_dca_day"))
            cash_gate = investable_cash > 0
            core_data_ready = dqs_gate and comp_gate and portfolio_available
            if not plan_gate:
                rejection_reasons.append("当前不在计划执行窗口")
            if not dqs_gate:
                rejection_reasons.append(f"{dqs_name}={dqs_score}低于硬门槛{required_dqs}")
            if not comp_gate:
                rejection_reasons.append(f"{comp_key}={comp_status}")
            if not portfolio_available:
                rejection_reasons.append("统一真实资产快照不可用")
            if not cash_gate:
                rejection_reasons.append("专项可投资现金不足")
            if not risk_gate:
                rejection_reasons.append(
                    f"风险分数{risk_score}高于场景上限{dependency['risk_threshold']}"
                )
            if not event_complete or not event_clear:
                warning_reasons.append(f"事件数据仅作软警告：{event_reason}")
            if rejection_reasons:
                permission = "DENY"
            elif not event_complete or not event_clear or dqs_score < normal_dca_score:
                permission = "ALLOW_REDUCED_EXECUTION"
                warning_reasons.append("采用保守减额档位，执行前仍需人工复核")
            elif core_data_ready:
                permission = "ALLOW_EXECUTION"
            else:
                permission = "WARN"

        elif scenario == "opportunity_add":
            cash_gate = investable_cash > 0
            if not dqs_gate:
                rejection_reasons.append(f"{dqs_name}={dqs_score}低于门槛{required_dqs}")
            if not risk_gate:
                rejection_reasons.append(
                    f"风险分数{risk_score}高于场景上限{dependency['risk_threshold']}"
                )
            if not event_complete or not event_clear:
                rejection_reasons.append(f"事件数据硬阻断：{event_reason}")
            if not portfolio_available:
                rejection_reasons.append("统一真实资产快照不可用")
            if not cash_gate:
                rejection_reasons.append("专项可投资现金不足")
            if not comp_gate:
                rejection_reasons.append(f"{comp_key}={comp_status}")
            permission = "DENY" if rejection_reasons else "ALLOW_EXECUTION"

        elif scenario == "strategic_rebalance":
            if not dqs_gate:
                rejection_reasons.append(f"{dqs_name}={dqs_score}低于门槛{required_dqs}")
            if not risk_gate:
                rejection_reasons.append(
                    f"风险分数{risk_score}高于场景上限{dependency['risk_threshold']}"
                )
            if not portfolio_available:
                rejection_reasons.append("统一真实资产快照不可用")
            if not target_allocation_available:
                rejection_reasons.append("目标资产配置不可用")
            if not comp_gate:
                rejection_reasons.append(f"{comp_key}={comp_status}")
            if not event_complete or not event_clear:
                warning_reasons.append(f"事件数据不足，不影响偏离评估：{event_reason}")
            if rejection_reasons:
                permission = "DENY"
            else:
                permission = "ALLOW_EVALUATION_ONLY"
                warning_reasons.append("仅输出资产偏离与修复方向，不生成即时成交指令")

        elif scenario == "grid":
            # Smart Grid stays SIMULATION_ONLY. Live gates are recorded separately
            # and never collapse an otherwise valid simulation into a global DENY.
            plan_gate = True
            cash_gate = live_grid_cash > 0
            simulation_data_ready = dqs_gate and comp_gate
            if not dqs_gate:
                rejection_reasons.append(f"{dqs_name}={dqs_score}低于模拟信号门槛{required_dqs}")
            if not comp_gate:
                rejection_reasons.append(f"{comp_key}={comp_status}")
            if not cash_gate:
                live_rejection_reasons.append("实盘网格现金为0")
            if not risk_gate:
                live_rejection_reasons.append(
                    f"风险分数{risk_score}高于实盘上限{dependency['risk_threshold']}"
                )
            if not event_complete or not event_clear:
                live_rejection_reasons.append(f"实盘事件门槛未通过：{event_reason}")
                warning_reasons.append("事件数据不足仅阻断实盘，不阻断数据完整的模拟评估")
            warning_reasons.append("Smart Grid固定为SIMULATION_ONLY，模拟资金与实盘隔离")
            permission = "ALLOW_SIMULATION_ONLY" if simulation_data_ready else "DENY"

        elif scenario == "risk_monitoring":
            event_gate_applicable = False
            event_gate = True
            risk_data_available = risk_score_raw is not None
            any_monitoring_data = portfolio_available or risk_data_available or dqs_score > 0
            if not any_monitoring_data:
                rejection_reasons.append("资产、持仓、行情、风险及数据质量信息均不可用")
                permission = "DENY"
            elif not portfolio_available or not risk_data_available or not dqs_gate:
                permission = "PARTIAL_MONITORING"
                warning_reasons.append("部分风险指标缺失，继续运行可用部分并列出缺口")
            elif not event_complete or not event_clear:
                permission = "PARTIAL_MONITORING"
                warning_reasons.append(f"事件数据不足，仅降低监控完整度：{event_reason}")
            else:
                permission = "ACTIVE"
            dqs_gate = any_monitoring_data
            risk_gate = True
            comp_gate = True

        else:  # transaction_reconciliation
            event_gate_applicable = False
            event_gate = True
            risk_gate = True
            comp_gate = True
            plan_gate = True
            cash_gate = True
            if not execution_data_available or dqs_score <= 0:
                rejection_reasons.append("成交、持仓、现金或快照数据完全不足")
                permission = "DENY"
            elif dqs_gate:
                permission = "PASS"
            else:
                permission = "WARN"
                warning_reasons.append(
                    f"{dqs_name}={dqs_score}低于完整对账门槛{required_dqs}，请核对差异字段"
                )

        rows.append(
            _base_row(
                scenario=scenario,
                dqs_name=dqs_name,
                dqs_score=dqs_score,
                required_dqs=required_dqs,
                dqs_gate=dqs_gate,
                plan_gate=plan_gate,
                cash_gate=cash_gate,
                risk_gate=risk_gate,
                event_gate=event_gate,
                event_gate_applicable=event_gate_applicable,
                comp_gate=comp_gate,
                permission=permission,
                rejection_reasons=rejection_reasons,
                warning_reasons=warning_reasons,
                live_rejection_reasons=live_rejection_reasons,
            )
        )

    contexts = {row["scenario"]: row for row in rows}
    executable = {"ALLOW_EXECUTION", "ALLOW_REDUCED_EXECUTION"}
    eligible = next((row for row in rows if row["final_permission"] in executable), None)
    selected = eligible if today_trade else None
    return {
        "scenarios": rows,
        "contexts": contexts,
        "selected_scenario": selected["scenario"] if selected else None,
        "global_final_permission": selected["final_permission"] if selected else "DENY",
        "today_trade_permission": selected["final_permission"] if selected else "DENY",
        "final_trade_permission": bool(selected),
        "final_trade_permission_source": selected["scenario_name"] if selected else "NO_CURRENT_TRADE_SCENARIO",
        "monitoring_permission": contexts["risk_monitoring"]["final_permission"],
        "reconciliation_permission": contexts["transaction_reconciliation"]["final_permission"],
        "automatic_trading_enabled": False,
    }
