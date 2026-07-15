from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.market_agent import MarketAgent
from agents.portfolio_agent import PortfolioAgent
from scripts.build_daily_snapshot import write_snapshot
from scripts.check_all_services import write_service_health
from scripts.project_audit import write_project_audit
from src.ai.openai_advisor import apply_openai_review, build_cio_review_context, generate_openai_advice
from src.analysis.cross_asset_engine import analyze_cross_asset
from src.decision.v12_1_decision import (
    VERSION_NAME,
    apply_ai_explanation,
    build_consistency_checks,
    build_system_audit_text,
    build_v12_1_decision,
)
from src.journal.investment_journal import build_log_row, upsert_investment_log
from src.journal.review_engine import build_history_review
from src.macro.macro_calendar import analyze_macro_calendar
from src.notifier.email_notifier import send_daily_reports
from src.reports.grid_report import generate_grid_backtest_report, generate_grid_daily_section, generate_grid_weekly_report
from src.reports.report_center import (
    build_run_status,
    generate_daily_report,
    generate_monthly_report,
    generate_portfolio_snapshot_report,
    generate_today_action,
    generate_weekly_report,
)
from src.risk.vix_risk import analyze_vix_risk
from src.strategy.dca_engine import build_dca_plan
from src.strategy.execution_plan import build_execution_plan
from src.strategy.rebalance_engine import build_rebalance_plan
from src.strategies.smart_grid_strategy import build_smart_grid_result
from src.system.health_check import format_health_report, run_health_check
from src.validators.decision_validator import write_validation_report
from src.portfolio_snapshot import portfolio_rows_for_legacy_agents
from utils.data_loader import load_config, load_market_data, load_portfolio, project_root
from utils.logger import write_log


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _build_context(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    root = project_root()
    write_log("阶段：持仓加载与统一快照派生开始", filename="stone_ai.log")
    config = load_config(root / "data" / "config.yaml")
    config["target_allocation"] = load_config(root / "config" / "strategy.yaml")["target_allocation"]
    # Portfolio Snapshot 是唯一生产资产事实源；CSV仅保留为兼容和人工查看文件。
    portfolio = portfolio_rows_for_legacy_agents()
    market_data = load_market_data(root / "data" / "market_data.csv")
    live_market_result = (snapshot or {}).get("market") or {}
    macro_result = analyze_macro_calendar()
    vix_result = analyze_vix_risk(live_market_result, market_data)

    market_result = MarketAgent(config, market_data).analyze()
    portfolio_result = PortfolioAgent(config, portfolio).analyze()
    write_log("阶段：资产、DQS、风险、预算与规则引擎计算开始", filename="stone_ai.log")
    dca_result = build_dca_plan(market_data, vix_result, macro_result)
    allocation_rebalance_result = build_rebalance_plan(portfolio_result)
    execution_plan_result = build_execution_plan(
        portfolio_result,
        market_result,
        live_market_result,
        vix_result,
        macro_result,
        dca_result,
        allocation_rebalance_result,
        config,
    )
    cross_asset_result = analyze_cross_asset(live_market_result, market_data, portfolio_result)
    decision = build_v12_1_decision(
        portfolio_result=portfolio_result,
        live_market_result=live_market_result,
        macro_result=macro_result,
        ai_advice_result={"ai_status": "rule_only", "fallback_reason": "pre_ai_rule_pass"},
    )
    try:
        grid_result = build_smart_grid_result(
            decision=decision,
            live_market_result=live_market_result,
            portfolio_result=portfolio_result,
        )
    except Exception as exc:  # noqa: BLE001 - grid must never break the CIO report
        write_log(f"智能网格模块失败，已隔离：{exc}", filename="stone_ai.log")
        grid_result = {
            "enabled": False,
            "error": str(exc),
            "summary": "智能网格模块异常，主日报继续生成。",
        }
    decision["grid"] = grid_result
    # 先完成规则与网格一致性校验，再允许OpenAI对已裁决结果做解释复核。
    decision["consistency"] = build_consistency_checks(decision)
    write_log(f"阶段：规则前置一致性校验={decision['consistency'].get('status')}", filename="stone_ai.log")
    ai_context = build_cio_review_context(decision, live_market_result, macro_result)
    write_log("阶段：OpenAI可选解释复核开始", filename="stone_ai.log")
    ai_advice_result = apply_openai_review(decision, generate_openai_advice(ai_context))
    decision = apply_ai_explanation(decision, ai_advice_result)
    write_log(f"阶段：OpenAI复核后一致性校验={decision['consistency'].get('status')}", filename="stone_ai.log")
    today = date.today()
    log_row = build_log_row(
        today,
        portfolio_result,
        market_result,
        vix_result,
        {"one_sentence_conclusion": decision.get("one_sentence", "")},
        dca_result,
        allocation_rebalance_result,
    )
    upsert_investment_log(log_row)
    history_review_result = build_history_review(today)
    return {
        "config": config,
        "market_result": market_result,
        "portfolio_result": portfolio_result,
        "live_market_result": live_market_result,
        "macro_result": macro_result,
        "vix_result": vix_result,
        "dca_result": dca_result,
        "allocation_rebalance_result": allocation_rebalance_result,
        "execution_plan_result": execution_plan_result,
        "cross_asset_result": cross_asset_result,
        "ai_advice_result": ai_advice_result,
        "decision": decision,
        "validation": decision.get("consistency", {}),
        "history_review_result": history_review_result,
    }


def _system_check_report() -> str:
    return format_health_report(run_health_check(auto_fix=True))


def write_weekly_report_if_due(reports_dir: Path, decision: dict[str, Any], run_date: date) -> dict[str, Any]:
    """周日刷新周报；非周日保留最近有效文件。首次缺失时仅做初始化恢复。"""
    weekly_path = reports_dir / "weekly_report.md"
    should_update = run_date.weekday() == 6
    initialized = False
    if should_update or not weekly_path.exists():
        initialized = not weekly_path.exists() and not should_update
        weekly_path.write_text(generate_weekly_report(decision), encoding="utf-8")
        reason = "周日例行更新" if should_update else "固定周报缺失，执行首次初始化"
        write_log(f"周报状态：{reason}；路径={weekly_path}", filename="stone_ai.log")
        return {"updated": True, "initialized": initialized, "path": str(weekly_path), "reason": reason}
    write_log(f"周报状态：非周日保留最近有效周报；路径={weekly_path}", filename="stone_ai.log")
    return {"updated": False, "initialized": False, "path": str(weekly_path), "reason": "非周日保留"}


def run(*, send_email: bool = True) -> str:
    root = project_root()
    reports_dir = root / "reports"
    reports_dir.mkdir(exist_ok=True)
    today = date.today()

    write_log("V12.6.2 Stable 正式运行开始", filename="stone_ai.log")
    write_log("阶段：数据获取与每日统一快照开始", filename="stone_ai.log")
    snapshot = write_snapshot()
    context = _build_context(snapshot)
    decision = context["decision"]
    validation = context["validation"]

    write_log("阶段：报告生成与文件落盘开始", filename="stone_ai.log")
    _write_json(reports_dir / "decision.json", decision)
    write_validation_report(reports_dir / "validation_report.md", validation, decision)
    write_project_audit(reports_dir / "project_audit.md")
    write_service_health(reports_dir / "service_health.md")
    (reports_dir / "system_audit.md").write_text(build_system_audit_text(context, decision), encoding="utf-8")

    today_action_path = reports_dir / "today_action.md"
    daily_report_path = reports_dir / "daily_report.md"
    portfolio_snapshot_path = reports_dir / "portfolio_snapshot.md"
    weekly_report_path = reports_dir / "weekly_report.md"
    run_status_path = reports_dir / "run_status.json"

    today_action_path.write_text(generate_today_action(decision), encoding="utf-8")
    (reports_dir / "grid_report.md").write_text(generate_grid_daily_section(decision.get("grid", {})), encoding="utf-8")
    (reports_dir / "grid_weekly_report.md").write_text(generate_grid_weekly_report(decision.get("grid", {})), encoding="utf-8")
    (reports_dir / "grid_backtest_report.md").write_text(generate_grid_backtest_report(decision.get("grid", {})), encoding="utf-8")
    daily_report_path.write_text(
        generate_daily_report(
            decision=decision,
            portfolio_result=context["portfolio_result"],
            market_result=context["market_result"],
            live_market_result=context["live_market_result"],
            macro_result=context["macro_result"],
            allocation_rebalance_result=context["allocation_rebalance_result"],
            ai_advice_result=context["ai_advice_result"],
            validation=validation,
        ),
        encoding="utf-8",
    )
    portfolio_snapshot_path.write_text(generate_portfolio_snapshot_report(decision), encoding="utf-8")
    weekly_result = write_weekly_report_if_due(reports_dir, decision, today)
    (reports_dir / "monthly_report.md").write_text(generate_monthly_report(decision), encoding="utf-8")
    (reports_dir / "system_check_report.md").write_text(_system_check_report(), encoding="utf-8")

    fixed_report_files = [
        "reports/today_action.md",
        "reports/daily_report.md",
        "reports/portfolio_snapshot.md",
        "reports/weekly_report.md",
        "reports/run_status.json",
    ]
    pre_email_status = "sent" if send_email else "skipped"
    run_status = build_run_status(
        decision,
        report_files=fixed_report_files,
        email_status=pre_email_status,
    )
    if weekly_result.get("initialized"):
        run_status["warnings"].append("weekly_report.md在非周日首次初始化；后续仅周日更新。")
        run_status["status"] = "warning"
    _write_json(run_status_path, run_status)
    write_log(
        "固定报告已生成："
        f"{today_action_path}；{daily_report_path}；{weekly_report_path}；{run_status_path}",
        filename="stone_ai.log",
    )

    email_result = {"sent": False, "skipped": True, "message": "本次运行未发送邮件", "error": ""}
    if send_email:
        email_result = send_daily_reports(reports_dir=reports_dir, subject_date=today)
    final_email_status = "sent" if email_result.get("sent") else ("skipped" if email_result.get("skipped") else "failed")
    run_status = build_run_status(
        decision,
        report_files=fixed_report_files,
        email_status=final_email_status,
        email_error=str(email_result.get("error") or ""),
    )
    if weekly_result.get("initialized"):
        run_status["warnings"].append("weekly_report.md在非周日首次初始化；后续仅周日更新。")
        run_status["status"] = "warning"
    _write_json(run_status_path, run_status)
    write_log(f"邮件发送状态：{email_result['message']}", filename="stone_ai.log")

    budget = decision["budget"]
    return "\n".join(
        [
            f"{VERSION_NAME} 运行完成",
            f"总资产：{decision.get('portfolio_value_wan', 0):.2f} 万元",
            f"今日是否交易：{'是' if decision.get('today_trade') else '否'}",
            f"今日建议金额：{budget.get('today_total_yuan', 0):.0f} 元",
            f"本周确认买入：{budget.get('week_confirmed_yuan', 0):.0f} 元",
            f"本月确认买入：{budget.get('month_confirmed_yuan', 0):.0f} 元",
            f"条件性债券转权益：{budget.get('conditional_bond_to_equity_month_yuan', 0):.0f} 元",
            f"DQS：{decision['dqs']['score']}；模式：{decision['dqs']['mode_label']}",
            f"风险评分：{decision['risk']['score']}；等级：{decision['risk']['level']}",
            f"一致性校验：{validation.get('status', 'PASS' if validation.get('ok') else 'FAIL')}",
            f"邮件通知：{email_result['message']}",
            "固定联动文件：reports/today_action.md、reports/daily_report.md、reports/weekly_report.md、reports/run_status.json",
            "声明：系统不自动交易，不接券商下单权限；仅供投资辅助，不构成投资建议，不承诺收益。",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    health = run_health_check(auto_fix=True)
    print(format_health_report(health))
    print("")
    if not health.get("can_run", False):
        print("发现 ERROR 项，主程序未运行。请按系统检查报告修复后重试。")
        return 1

    print("开始生成投资日报...")
    print(run(send_email=True))
    try:
        status = json.loads((project_root() / "reports" / "run_status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    return 1 if status.get("status") == "failed" else 0
