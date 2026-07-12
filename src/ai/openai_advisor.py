from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gpt-4o-mini"
AI_FIELDS = [
    "market_regime",
    "why_action_or_no_action",
    "key_risk_3_7_days",
    "portfolio_priority",
    "best_opportunity",
    "required_trigger_conditions",
    "cio_commentary",
    "one_sentence_conclusion",
]


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))


def _fallback_result(reason: str = "rule_only", retry_count: int = 0, model: str = "") -> dict[str, Any]:
    neutral = "Stone CIO规则引擎已完成完整分析；OpenAI可选复核本次未参与，不影响核心风控与决策。"
    return {
        "enabled": False,
        "ai_status": "rule_only",
        "actual_provider": "stone_rule_engine",
        "fallback_reason": reason,
        "retry_count": retry_count,
        "degrade_level": "RULE_ENHANCED",
        "response_timestamp": "",
        "model": model,
        "market_regime": "由规则引擎依据风险评分、DQS和市场数据判断",
        "why_action_or_no_action": neutral,
        "key_risk_3_7_days": "以规则引擎识别的首要风险为准。",
        "portfolio_priority": "优先服从现金安全线、资产偏离和已确认资金来源。",
        "best_opportunity": "以Opportunity Score排序为观察线索，不直接转化为交易。",
        "required_trigger_conditions": ["DQS、现金、预算、事件和总风控同时通过"],
        "cio_commentary": neutral,
        "one_sentence_conclusion": neutral,
        # 保留旧字段，避免历史报告调用断裂。
        "summary": neutral,
        "most_important_risk": "以规则引擎识别的首要风险为准。",
        "best_action_today": "按规则引擎结论执行，所有真实操作人工确认。",
        "avoid_action_today": "不要绕过DQS、现金安全线、预算和事件纪律。",
        "one_sentence": neutral,
        "raw_text": "",
        "disclaimer": "仅供投资辅助，不构成投资建议；不自动交易，不承诺收益，最终决策由用户负责。",
    }


def build_ai_context(
    portfolio_result: dict[str, Any],
    market_result: dict[str, Any],
    live_market_result: dict[str, Any],
    vix_result: dict[str, Any],
    macro_result: dict[str, Any],
    dca_result: dict[str, Any],
    allocation_rebalance_result: dict[str, Any],
    cross_asset_result: dict[str, Any],
) -> dict[str, Any]:
    """兼容旧调用；新生产链路使用 build_cio_review_context。"""
    return {
        "current_asset_allocation": {
            "total_assets_wan": portfolio_result.get("total_assets_wan"),
            "categories": portfolio_result.get("categories", []),
        },
        "today_market_data": {
            "market_score": market_result.get("market_score"),
            "market_risk_score": market_result.get("market_risk_score"),
            "summary": market_result.get("summary"),
            "data_quality": live_market_result.get("data_quality", {}),
        },
        "vix_risk": vix_result,
        "macro_events": macro_result,
        "dca_advice": dca_result,
        "rebalance_advice": allocation_rebalance_result,
        "cross_asset_analysis": cross_asset_result,
    }


def build_cio_review_context(
    decision: dict[str, Any],
    live_market_result: dict[str, Any],
    macro_result: dict[str, Any],
) -> dict[str, Any]:
    """只向解释层发送规则裁决后的精简结构，不发送完整日报。"""
    budget = decision.get("budget", {}) or {}
    return {
        "report_date": decision.get("date"),
        "trading_day_status": decision.get("trading_day_status"),
        "allocation": decision.get("allocation", []),
        "cash": {
            "account_total": budget.get("account_total_cash_yuan"),
            "safety_reserve": budget.get("cash_safety_reserve_yuan"),
            "investable": budget.get("investable_cash_yuan"),
        },
        "budgets": budget.get("rows", []),
        "dqs": decision.get("dqs", {}),
        "risk": decision.get("risk", {}),
        "market": decision.get("market_table", []),
        "events": macro_result.get("upcoming_events", []),
        "opportunity_top": (decision.get("opportunity", []) or [])[:5],
        "rule_decision": {
            "today_trade": decision.get("today_trade"),
            "trade_type": decision.get("trade_type"),
            "today_amount_yuan": decision.get("today_amount_yuan"),
            "targets": decision.get("targets"),
            "funding_source": decision.get("funding_source"),
            "no_trade_reasons": decision.get("no_trade_reasons", []),
            "next_triggers": decision.get("next_triggers", []),
        },
        "hard_prohibitions": [
            "不自动交易，所有真实操作人工确认",
            "不得使用现金安全储备、未到账债券资金或模拟网格资金",
            "不得绕过DQS、预算、事件和总风控",
            "不得自动建议ST股票加仓",
            "不得把条件性计划写成今日执行",
        ],
        "market_data_quality": live_market_result.get("data_quality", {}),
    }


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "你是Stone AI Investment Manager Pro V12.5 Stable的CIO解释与复核层。"
        "规则引擎已经完成交易裁决；你不得修改金额、标的、资金来源或硬风控。"
        "不自动交易、不承诺收益、不预测具体点位。只返回一个JSON对象，不要Markdown。"
        f"JSON必须且只能包含这些字段：{','.join(AI_FIELDS)}。"
        "required_trigger_conditions必须是字符串数组，其余字段必须是简洁中文字符串。"
        "若可投资现金为0，必须明确写出‘当前没有真实可执行买入预算’。"
        "结构化输入：" + _safe_json(context)
    )


def _parse_json_output(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError("OpenAI返回值不是JSON对象")
    missing = [field for field in AI_FIELDS if field not in loaded]
    if missing:
        raise ValueError(f"OpenAI返回字段缺失：{', '.join(missing)}")
    if not isinstance(loaded.get("required_trigger_conditions"), list):
        raise ValueError("required_trigger_conditions必须是数组")
    for field in AI_FIELDS:
        if field == "required_trigger_conditions":
            continue
        if not isinstance(loaded.get(field), str) or not loaded[field].strip():
            raise ValueError(f"OpenAI字段无效：{field}")
    return loaded


def validate_openai_advice(advice: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, list[str]]:
    """阻止解释层突破现金、DQS、事件、ST和规则裁决。"""
    errors: list[str] = []
    combined = " ".join(str(advice.get(field, "")) for field in AI_FIELDS)
    budget = decision.get("budget", {}) or {}
    dqs = decision.get("dqs", {}) or {}
    investable = float(budget.get("investable_cash_yuan", 0) or 0)
    affirmative_buy = bool(
        re.search(r"(?<!不)建议(?:立即)?(?:买入|加仓)|(?:应当|立即)(?:买入|加仓)|执行(?:买入|加仓)|今日(?:买入|加仓)", combined)
    )
    amount_mentioned = bool(re.search(r"\d[\d,]*(?:\.\d+)?\s*(?:元|万元)", combined))

    if not decision.get("today_trade") and affirmative_buy:
        errors.append("OpenAI建议与规则引擎今日不交易结论冲突")
    if investable <= 0 and affirmative_buy:
        errors.append("OpenAI建议违反可投资现金约束")
    if int(dqs.get("score", 0) or 0) < 85 and amount_mentioned:
        errors.append("OpenAI输出金额超过DQS允许精度")
    if "*ST" in combined and affirmative_buy:
        errors.append("OpenAI建议ST股票加仓")
    if decision.get("macro_event_high_next_7_days") and affirmative_buy:
        errors.append("OpenAI建议违反重大事件纪律")
    return not errors, errors


def generate_openai_advice(context: dict[str, Any], env_path: Path | None = None) -> dict[str, Any]:
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _fallback_result("missing_openai_key")

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        write_log("OpenAI SDK not installed", filename="openai_advisor.log")
        return _fallback_result("openai_sdk_missing")

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    max_retries = min(2, max(0, int(os.getenv("MAX_LLM_RETRIES", "2") or 2)))
    timeout_seconds = max(5.0, min(60.0, float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30") or 30)))
    prompt = _build_prompt(context)
    if len(prompt) > 18000:
        prompt = prompt[:18000] + "\n[输入已按长度上限截断]"
    last_error = ""
    try:
        client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        for attempt in range(max_retries + 1):
            try:
                write_log(f"OpenAI请求开始：model={model} attempt={attempt + 1}", filename="stone_ai.log")
                response = client.responses.create(
                    model=model,
                    input=[
                        {"role": "system", "content": "只返回符合用户要求的JSON对象；你只负责解释，不改变规则裁决。"},
                        {"role": "user", "content": prompt},
                    ],
                )
                raw_text = getattr(response, "output_text", "") or ""
                parsed = _parse_json_output(raw_text)
                return {
                    **parsed,
                    "enabled": True,
                    "ai_status": "available",
                    "actual_provider": "openai",
                    "fallback_reason": "",
                    "retry_count": attempt,
                    "degrade_level": "AI_FULL",
                    "response_timestamp": "",
                    "model": model,
                    "summary": parsed["cio_commentary"],
                    "most_important_risk": parsed["key_risk_3_7_days"],
                    "best_action_today": parsed["portfolio_priority"],
                    "avoid_action_today": "不得突破规则引擎硬约束。",
                    "one_sentence": parsed["one_sentence_conclusion"],
                    "raw_text": "",
                    "disclaimer": "仅供投资辅助，不构成投资建议；不自动交易，不承诺收益。",
                }
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"invalid_json_or_schema: {exc}"
                    if isinstance(exc, (json.JSONDecodeError, ValueError))
                    else str(exc)
                )
                retryable = any(code in last_error.lower() for code in ["429", "500", "502", "503", "504", "rate_limit", "timeout"])
                write_log(f"OpenAI请求失败：{type(exc).__name__} retryable={retryable}", filename="openai_advisor.log")
                if not retryable or attempt >= max_retries:
                    break
                time.sleep(min(8, 2**attempt))
    except Exception as exc:  # noqa: BLE001
        last_error = str(exc)

    lowered = last_error.lower()
    if "insufficient_quota" in lowered or "exceeded your current quota" in lowered:
        reason = "insufficient_quota"
    elif "429" in lowered or "rate_limit" in lowered:
        reason = "rate_limit"
    elif "timeout" in lowered:
        reason = "network_or_timeout"
    elif "invalid_json_or_schema" in lowered or "json" in lowered or "字段" in last_error:
        reason = "invalid_json_or_schema"
    elif "401" in lowered or "authentication" in lowered:
        reason = "authentication_failed"
    else:
        reason = "openai_request_failed"
    write_log(f"OpenAI unavailable: {reason}", filename="openai_advisor.log")
    return _fallback_result(reason, max_retries, model)


def apply_openai_review(decision: dict[str, Any], advice: dict[str, Any]) -> dict[str, Any]:
    if advice.get("ai_status") != "available":
        return advice
    valid, errors = validate_openai_advice(advice, decision)
    if valid:
        return advice
    write_log(f"OPENAI_VALIDATION_REJECTED: {'; '.join(errors)}", filename="openai_advisor.log")
    fallback = _fallback_result("OPENAI_VALIDATION_REJECTED", int(advice.get("retry_count", 0) or 0), str(advice.get("model", "")))
    fallback["validation_errors"] = errors
    return fallback
