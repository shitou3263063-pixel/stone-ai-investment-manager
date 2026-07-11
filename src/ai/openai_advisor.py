from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gpt-4o-mini"


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
    return json.dumps(data, ensure_ascii=False, default=str, indent=2)


def _fallback_result(message: str, reason: str = "rule_only", retry_count: int = 0) -> dict[str, Any]:
    return {
        "enabled": False,
        "ai_status": "rule_only",
        "actual_provider": "rule_only",
        "fallback_reason": reason,
        "retry_count": retry_count,
        "degrade_level": "RULE_ENHANCED",
        "response_timestamp": "",
        "summary": message,
        "most_important_risk": "AI深度分析不可用，本次以本地规则、资产配置、数据质量和风险模型为准。",
        "best_action_today": "按日报中的DQS、现金安全线、定投和再平衡约束执行，所有操作人工确认。",
        "avoid_action_today": "不要因为AI不可用而临时重仓交易，不要绕过DQS和现金约束。",
        "one_sentence": message,
        "raw_text": "",
        "model": "",
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
    return {
        "current_asset_allocation": {
            "total_assets_wan": portfolio_result.get("total_assets_wan"),
            "categories": portfolio_result.get("categories", []),
        },
        "today_market_data": {
            "market_score": market_result.get("market_score"),
            "market_risk_score": market_result.get("market_risk_score"),
            "offense_index": market_result.get("offense_index"),
            "defense_index": market_result.get("defense_index"),
            "summary": market_result.get("summary"),
            "data_quality": live_market_result.get("data_quality", {}),
        },
        "vix_risk": vix_result,
        "macro_events": macro_result,
        "dca_advice": dca_result,
        "rebalance_advice": allocation_rebalance_result,
        "cross_asset_analysis": cross_asset_result,
    }


def _build_prompt(context: dict[str, Any]) -> str:
    return f"""
你是 Stone AI Investment Manager Pro V12.5 Stable 的 AI CIO 复核助手。

硬边界：
- 不允许自动交易。
- 不允许承诺收益。
- 不预测具体点位。
- 不得绕过 DQS、现金约束、风险约束和统一 decision 对象。
- 只解释结构化数据，不修改资产、行情、目标配置或交易金额。

请输出五段：
【AI投资经理总结】
【今日最重要风险】
【今日最建议做的事】
【今日最不建议做的事】
【一句话结论】

结构化数据：
{_safe_json(context)}
""".strip()


def _extract(raw_text: str, marker: str, next_marker: str | None = None) -> str:
    start = raw_text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = len(raw_text)
    if next_marker:
        next_index = raw_text.find(next_marker, start)
        if next_index >= 0:
            end = next_index
    return raw_text[start:end].strip()


def _extract_sections(raw_text: str) -> dict[str, str]:
    markers = [
        ("summary", "【AI投资经理总结】"),
        ("most_important_risk", "【今日最重要风险】"),
        ("best_action_today", "【今日最建议做的事】"),
        ("avoid_action_today", "【今日最不建议做的事】"),
        ("one_sentence", "【一句话结论】"),
    ]
    result: dict[str, str] = {}
    for index, (key, marker) in enumerate(markers):
        next_marker = markers[index + 1][1] if index + 1 < len(markers) else None
        result[key] = _extract(raw_text, marker, next_marker)
    return result


def generate_openai_advice(context: dict[str, Any], env_path: Path | None = None) -> dict[str, Any]:
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _fallback_result("AI深度分析未启用：未配置 OPENAI_API_KEY", "missing_openai_key", 0)

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        write_log("OpenAI SDK not installed", filename="openai_advisor.log")
        return _fallback_result("AI深度分析暂不可用，系统已切换规则增强模式。", "openai_sdk_missing", 0)

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    max_retries = int(os.getenv("MAX_LLM_RETRIES", "2") or 2)
    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30") or 30)
    prompt = _build_prompt(context)
    if len(prompt) > 18000:
        prompt = prompt[:18000] + "\n\n[输入因长度限制已截断，系统只保留关键结构化数据。]"
    last_error = ""
    try:
        client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        for attempt in range(max_retries + 1):
            try:
                write_log(f"OpenAI请求开始：model={model} attempt={attempt + 1}", filename="stone_ai.log")
                response = client.responses.create(
                    model=model,
                    input=[
                        {
                            "role": "system",
                            "content": "你是谨慎的投资复核助手，只做解释和风险复核，不做自动交易。请优先返回清晰分段文本。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                raw_text = getattr(response, "output_text", "") or ""
                sections = _extract_sections(raw_text)
                return {
                    "enabled": True,
                    "ai_status": "available",
                    "actual_provider": "openai",
                    "fallback_reason": "",
                    "retry_count": attempt,
                    "degrade_level": "AI_FULL",
                    "response_timestamp": "",
                    "summary": sections.get("summary") or raw_text,
                    "most_important_risk": sections.get("most_important_risk") or "AI未明确输出该字段。",
                    "best_action_today": sections.get("best_action_today") or "AI未明确输出该字段。",
                    "avoid_action_today": sections.get("avoid_action_today") or "AI未明确输出该字段。",
                    "one_sentence": sections.get("one_sentence") or "AI深度分析已生成。",
                    "raw_text": raw_text,
                    "model": model,
                    "disclaimer": "仅供投资辅助，不构成投资建议；不自动交易，不承诺收益。",
                }
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                retryable = any(code in last_error for code in ["429", "500", "502", "503", "504", "rate_limit"])
                write_log(f"OpenAI请求失败：{type(exc).__name__} retryable={retryable}", filename="openai_advisor.log")
                if not retryable or attempt >= max_retries:
                    break
                time.sleep(min(8, 2**attempt))
    except Exception as exc:  # noqa: BLE001
        last_error = str(exc)

    if "insufficient_quota" in last_error or "You exceeded your current quota" in last_error:
        write_log("OpenAI unavailable: insufficient_quota", filename="openai_advisor.log")
        return _fallback_result("OpenAI深度分析暂不可用：额度不足，系统已切换规则增强模式。", "insufficient_quota", max_retries)
    if "rate_limit" in last_error or "429" in last_error:
        write_log("OpenAI unavailable: rate_limit", filename="openai_advisor.log")
        return _fallback_result("OpenAI深度分析暂不可用：遇到限流，系统已切换规则增强模式。", "rate_limit", max_retries)
    reason = "network_or_timeout" if "timeout" in last_error.lower() else (last_error[:80] or "unknown_error")
    write_log(f"OpenAI unavailable: {reason}", filename="openai_advisor.log")
    return _fallback_result("AI深度分析暂不可用，系统已切换规则增强模式。", reason, max_retries)
