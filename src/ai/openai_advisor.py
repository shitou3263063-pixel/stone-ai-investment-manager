from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gpt-5.5"


def _load_env_file(env_path: Path) -> None:
    """读取 .env 文件，不覆盖系统环境变量。"""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, indent=2)


def _fallback_result(message: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "summary": message,
        "most_important_risk": "AI 深度分析未启用，本次以本地规则、资产配置和风险模块为准。",
        "best_action_today": "继续查看本地日报中的定投、再平衡和跨资产联动建议。",
        "avoid_action_today": "不要因为缺少 AI 深度分析而进行临时重仓交易。",
        "one_sentence": message,
        "raw_text": "",
        "model": "",
        "disclaimer": "仅供投资辅助，不构成投资建议；不自动交易，不承诺收益，最终决策由用户自己负责。",
    }


def _build_prompt(context: dict[str, Any]) -> str:
    return f"""
你是 Stone AI Investment Manager Pro V10 的投资经理助手。

硬性风控边界：
- 不允许自动交易。
- 不允许承诺收益。
- 不预测具体涨跌点位。
- 所有内容仅供投资辅助，不构成投资建议。
- 最终决策由用户自己负责。
- 输出必须结合用户当前资产配置，不要只做泛泛市场评论。
- 请使用中文，观点明确，但不要夸大确定性。

请基于以下系统数据，输出固定格式：

【AI 投资经理总结】
不超过 5 句话。

【今日最重要风险】
指出一个最重要风险，并说明它如何影响当前组合。

【今日最建议做的事】
给出一个最建议做的动作，必须是人工确认、非自动交易。

【今日最不建议做的事】
给出一个最不建议做的动作。

【一句话结论】
一句话，明确、稳健。

系统数据：
{_safe_json(context)}
""".strip()


def _extract_sections(raw_text: str) -> dict[str, str]:
    sections = {
        "summary": "",
        "most_important_risk": "",
        "best_action_today": "",
        "avoid_action_today": "",
        "one_sentence": "",
    }
    markers = [
        ("summary", "【AI 投资经理总结】"),
        ("most_important_risk", "【今日最重要风险】"),
        ("best_action_today", "【今日最建议做的事】"),
        ("avoid_action_today", "【今日最不建议做的事】"),
        ("one_sentence", "【一句话结论】"),
    ]

    for index, (key, marker) in enumerate(markers):
        start = raw_text.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = len(raw_text)
        if index + 1 < len(markers):
            next_marker = markers[index + 1][1]
            next_index = raw_text.find(next_marker, start)
            if next_index >= 0:
                end = next_index
        sections[key] = raw_text[start:end].strip()
    return sections


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
    """整理发送给 AI 的上下文，避免把无关配置或密钥传入模型。"""
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
            "live_market": live_market_result,
        },
        "vix_risk": vix_result,
        "macro_events": macro_result,
        "dca_advice": dca_result,
        "rebalance_advice": allocation_rebalance_result,
        "cross_asset_analysis": cross_asset_result,
    }


def generate_openai_advice(context: dict[str, Any], env_path: Path | None = None) -> dict[str, Any]:
    """调用 OpenAI 生成深度分析；任何失败都不影响主程序运行。"""
    _load_env_file(env_path or PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _fallback_result("AI深度分析未启用：未配置 OPENAI_API_KEY")

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        message = f"AI深度分析未启用：openai SDK 未安装：{exc}"
        write_log(message, filename="openai_advisor.log")
        return _fallback_result(message)

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": "你是谨慎、低频、重视回撤控制的投资分析助手。",
                },
                {
                    "role": "user",
                    "content": _build_prompt(context),
                },
            ],
        )
        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text:
            raw_text = str(response)

        sections = _extract_sections(raw_text)
        result = {
            "enabled": True,
            "summary": sections["summary"] or raw_text,
            "most_important_risk": sections["most_important_risk"] or "AI 未明确输出该字段。",
            "best_action_today": sections["best_action_today"] or "AI 未明确输出该字段。",
            "avoid_action_today": sections["avoid_action_today"] or "AI 未明确输出该字段。",
            "one_sentence": sections["one_sentence"] or "AI 深度分析已生成。",
            "raw_text": raw_text,
            "model": model,
            "disclaimer": "仅供投资辅助，不构成投资建议；不自动交易，不承诺收益，最终决策由用户自己负责。",
        }
        write_log(f"OpenAI 深度分析生成成功，model={model}", filename="openai_advisor.log")
        return result
    except Exception as exc:  # noqa: BLE001 - AI 失败不能影响日报生成
        message = f"AI深度分析暂不可用：{exc}"
        write_log(message, filename="openai_advisor.log")
        return _fallback_result(message)

