from __future__ import annotations

from datetime import date
from typing import Any


TARGET_ALLOCATION = {
    "美股": 0.30,
    "港股": 0.12,
    "A股": 0.10,
    "债券": 0.25,
    "黄金": 0.15,
    "现金": 0.08,
}

EQUITY_CATEGORIES = ("美股", "港股", "A股")


class ReportAgent:
    """Report Agent：整合分析结果，生成 V12 生产版报告。"""

    def __init__(
        self,
        market_result: dict[str, Any],
        portfolio_result: dict[str, Any],
        risk_result: dict[str, Any],
        decision_result: dict[str, Any],
        config: dict[str, Any] | None = None,
        live_market_result: dict[str, Any] | None = None,
        rebalance_result: dict[str, Any] | None = None,
        macro_result: dict[str, Any] | None = None,
        vix_result: dict[str, Any] | None = None,
        dca_result: dict[str, Any] | None = None,
        allocation_rebalance_result: dict[str, Any] | None = None,
        execution_plan_result: dict[str, Any] | None = None,
        cross_asset_result: dict[str, Any] | None = None,
        ai_advice_result: dict[str, Any] | None = None,
        history_review_result: dict[str, Any] | None = None,
    ):
        self.market = market_result
        self.portfolio = portfolio_result
        self.risk = risk_result
        self.decision = decision_result
        self.config = config or {}
        self.live_market = live_market_result or {}
        self.rebalance = rebalance_result or {}
        self.macro = macro_result or {}
        self.vix = vix_result or {}
        self.dca = dca_result or {}
        self.allocation_rebalance = allocation_rebalance_result or {}
        self.execution_plan = execution_plan_result or {}
        self.cross_asset = cross_asset_result or {}
        self.ai_advice = ai_advice_result or {}
        self.history_review = history_review_result or {}

    def generate_daily_report(self, report_date: date | None = None) -> str:
        report_date = report_date or date.today()
        return "\n".join(
            [
                self._stone_cio_decision_text(),
                "",
                "【今日/本周/本月执行计划】",
                "",
                self._execution_plan_text(),
                "",
                "【债券转权益路径】",
                "",
                self._bond_to_equity_text(),
                "",
                "【黄金金条每日估值】",
                "",
                self._gold_bar_text(),
                "",
                f"日期：{report_date.isoformat()}",
                "系统定位：进取型长期增长组合",
                "长期设计目标：年化 10%–15%，不保证收益",
                "声明：仅供投资辅助，不构成投资建议。",
                "",
                "【目标年化状态】",
                "",
                self._target_annual_status_text(),
                "",
                "【新增资金投向建议】",
                "",
                self._opportunity_score_text(),
                "",
                "【暂停加仓清单】",
                "",
                self._pause_add_list_text(),
                "",
                "【风险约束】",
                "",
                self._growth_risk_constraints_text(),
                "",
                "【数据来源与质量】",
                "",
                self._data_quality_text(),
                "",
                f"总资产：{self.portfolio['total_assets_wan']:.2f} 万元",
                f"市场风险评分：{self.market['market_risk_score']} / 100",
                f"进攻指数：{self.market['offense_index']} / 100",
                f"防守指数：{self.market['defense_index']} / 100",
                f"是否适合加仓：{'是' if self.market['suitable_to_add'] else '否'}",
                f"是否适合减仓：{'是' if self.market['suitable_to_reduce'] else '否'}",
                "",
                "① 隔夜全球市场（3分钟看完）",
                "",
                self.market["summary"],
                "",
                "② 我的资产变化",
                "",
                self._asset_change_text(),
                "",
                "③ 今日市场数据",
                "",
                self._live_market_text(),
                "",
                "④ 风险评分",
                "",
                self._risk_score_text(),
                "",
                "⑤ 宏观事件与 VIX 风险",
                "",
                self._macro_and_vix_text(),
                "",
                "⑥ 跨资产联动分析",
                "",
                self._cross_asset_text(),
                "",
                "⑦ AI 投资经理深度分析",
                "",
                self._ai_advice_text(),
                "",
                "⑧ 历史复盘",
                "",
                self._history_review_text(),
                "",
                "⑨ 我的组合影响分析",
                "",
                self._portfolio_impact_text(),
                "",
                "⑩ 规则触发与例外机制",
                "",
                self._rule_and_exception_text(),
                "",
                "⑪ 调仓建议",
                "",
                self._rebalance_text(),
                "",
                "⑫ 今日建议与置信度",
                "",
                self._decision_context_text(),
                f"✅ 买：{self._orders_text(self.decision['buy_orders'])}",
                f"❌ 卖：{self._orders_text(self.decision['sell_orders'])}",
                f"⭕ 持有：{self._hold_text()}",
                f"⏳ 等待：{self._wait_text()}",
                "",
                "⑬ 本月定投建议",
                "",
                self._monthly_investment_text(),
                "",
                "⑭ 未来7天重点关注",
                "",
                self._next_7_days_text(),
                "",
                "免责声明：仅供投资辅助，不构成投资建议；不接入真实交易，不自动买卖，不保证收益。",
                "",
                "【今日最终结论】",
                "",
                self._final_decision_text(),
                "",
            ]
        )

    def _categories_by_name(self) -> dict[str, dict[str, Any]]:
        return {item["category"]: item for item in self.portfolio.get("categories", [])}

    def _category_ratio(self, category: str) -> float:
        item = self._categories_by_name().get(category, {})
        return float(item.get("current_ratio", 0.0) or 0.0)

    def _category_target(self, category: str) -> float:
        item = self._categories_by_name().get(category, {})
        return float(item.get("target_ratio", TARGET_ALLOCATION.get(category, 0.0)) or 0.0)

    def _category_deviation(self, category: str) -> float:
        return self._category_ratio(category) - self._category_target(category)

    def _fmt_pct(self, value: float) -> str:
        return f"{value * 100:.2f}%"

    def _vix_value(self) -> float | None:
        value = self.vix.get("vix")
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _vix_policy_text(self) -> str:
        vix = self._vix_value()
        if vix is None:
            return "VIX 暂不可用：按中性规则执行，正常定投但不额外追高。"
        if vix < 20:
            return f"VIX {vix:.2f} < 20：正常定投。"
        if vix < 30:
            return f"VIX {vix:.2f} 在20–30：定投金额降低30%，不追高。"
        return f"VIX {vix:.2f} >= 30：暂停追涨，只允许小额分批低吸。"

    def _equity_current_target_gap(self) -> tuple[float, float, float]:
        current = sum(self._category_ratio(category) for category in EQUITY_CATEGORIES)
        target = sum(self._category_target(category) for category in EQUITY_CATEGORIES)
        return current, target, target - current

    def _max_abs_deviation(self) -> float:
        categories = self._categories_by_name()
        if not categories:
            return 0.0
        return max(abs(float(item.get("deviation_ratio", 0.0) or 0.0)) for item in categories.values())

    def _rebalance_path_text(self) -> str:
        max_deviation = self._max_abs_deviation()
        if max_deviation <= 0.05:
            return f"否，最大偏离{self._fmt_pct(max_deviation)}，在5%以内，无需调仓。"
        if max_deviation <= 0.08:
            return f"轻微偏离，最大偏离{self._fmt_pct(max_deviation)}，观察，优先用新增资金修正。"
        return f"是，最大偏离{self._fmt_pct(max_deviation)}超过8%，需要给出再平衡方向，但优先少卖出、用新增资金调整。"

    def _today_operation_text(self) -> str:
        cash_ratio = self._category_ratio("现金")
        if cash_ratio < 0.05:
            return f"需要：现金仅{self._fmt_pct(cash_ratio)}，暂停新增风险资产，优先补现金。"
        if self._max_abs_deviation() > 0.08:
            return "需要：组合偏离超过8%，给出再平衡方向，优先用新增资金修正。"
        if self.dca.get("today_continue"):
            return "不需要临时交易；可按定投纪律执行。"
        return "不需要；等待更明确的价格或配置触发。"

    def _dca_answer_text(self) -> str:
        cash_ratio = self._category_ratio("现金")
        if cash_ratio < 0.05:
            return f"否。现金低于5%（当前{self._fmt_pct(cash_ratio)}），本期新增资金优先补现金。"
        if 0.05 <= cash_ratio <= 0.08:
            return f"是。现金{self._fmt_pct(cash_ratio)}处于5%–8%正常区间，可继续定投。{self._vix_policy_text()}"
        return f"是，但不追高。当前现金{self._fmt_pct(cash_ratio)}，{self._vix_policy_text()}"

    def _new_money_priority_text(self) -> str:
        cash_ratio = self._category_ratio("现金")
        if cash_ratio < 0.05:
            return "现金。现金低于5%，先恢复流动性缓冲。"
        _, _, equity_gap = self._equity_current_target_gap()
        if equity_gap >= 0.05:
            return "权益资产。顺序：VOO / QQQ / 沪深300ETF / 恒生科技ETF小额分批。"
        rows = self._opportunity_rows()
        best = max(rows, key=lambda item: item["score"]) if rows else None
        if best:
            return f"{best['asset']}。Opportunity Score {best['score']}，{best['reason']}"
        return "暂不新增，等待下一次配置或估值触发。"

    def _paused_asset_names(self) -> list[str]:
        paused: list[str] = []
        cash_ratio = self._category_ratio("现金")
        if cash_ratio < 0.05:
            paused.append("所有风险资产")
        if self._category_ratio("债券") > 0.30:
            paused.append("债券")
        if self._category_ratio("黄金") >= 0.15:
            paused.append("黄金")
        if self._category_deviation("港股") > 0.05:
            paused.append("港股新增大额加仓")
        if self._category_ratio("美股") >= self._category_target("美股"):
            paused.append("高估值美股追高")
        return paused

    def _one_sentence_cio_text(self) -> str:
        cash_ratio = self._category_ratio("现金")
        _, _, equity_gap = self._equity_current_target_gap()
        if cash_ratio < 0.05:
            return "现金偏低，今天优先补现金，不新增风险资产。"
        if equity_gap >= 0.05:
            return "权益资产低于目标，新增资金优先补VOO、QQQ、沪深300ETF和恒生科技ETF。"
        if self._max_abs_deviation() <= 0.05:
            return "配置接近目标，保持定投和持有，不做追涨式调仓。"
        return "组合有偏离，但优先用新增资金慢慢修正，少卖出、少折腾。"

    def _stone_cio_decision_text(self) -> str:
        paused = self._paused_asset_names()
        paused_text = "、".join(paused) if paused else "无明确暂停项，但高估值资产不追高"
        if self.execution_plan:
            plan_paused = "、".join(self.execution_plan.get("pause_list", []))
            if plan_paused:
                paused_text = plan_paused
        return "\n".join(
            [
                "【Stone CIO 今日决策】",
                "",
                f"1. 今天是否需要操作？{self._today_operation_text()} 今日计划买入{self.execution_plan.get('today_buy_wan', 0.0) * 10000:.0f}元。",
                f"2. 今天是否继续定投？{self._dca_answer_text()}",
                f"3. 今天是否需要调仓？{self._rebalance_path_text()}",
                f"4. 今日最大风险是什么？{self.decision.get('max_risk', '暂无')}",
                f"5. 新增资金优先投向哪里？{self._new_money_priority_text()}",
                f"6. 哪些资产暂停加仓？{paused_text}。",
                f"7. 一句话结论。{self._one_sentence_cio_text()}",
                "",
                "仅供投资辅助，不构成投资建议。",
            ]
        )

    def _format_orders(self, orders: list[dict[str, Any]]) -> str:
        if not orders:
            return "无"
        return "；".join(
            f"{item['name']} {float(item.get('amount_yuan', 0.0)):.0f}元，分{int(item.get('parts', 1))}笔"
            for item in orders
        )

    def _execution_plan_text(self) -> str:
        if not self.execution_plan:
            return "执行计划模块暂不可用。"

        lines = [
            f"- 操作等级：{self.execution_plan.get('action_level', self.decision.get('operation_level', '暂无'))}",
            f"- 今日买多少：{self.execution_plan.get('today_buy_wan', 0.0) * 10000:.0f}元",
            f"- 今日买什么：{self._format_orders(self.execution_plan.get('today_orders', []))}",
            f"- 本周计划买入：{self.execution_plan.get('week_buy_wan', 0.0) * 10000:.0f}元",
            f"- 本周买什么：{self._format_orders(self.execution_plan.get('week_orders', []))}",
            f"- 本月计划买入：{self.execution_plan.get('month_buy_wan', 0.0) * 10000:.0f}元",
            f"- 本月买什么：{self._format_orders(self.execution_plan.get('month_orders', []))}",
            f"- 现金纪律：{self.execution_plan.get('cash_policy', '暂无')}",
            f"- 权益路径：{self.execution_plan.get('equity_path', '暂无')}",
            f"- 数据纪律：{self.execution_plan.get('data_policy', '暂无')}",
            "- 为什么这样执行：",
        ]
        for reason in self.execution_plan.get("risk_reasons", []):
            lines.append(f"  - {reason}")
        lines.extend(
            [
                f"- 暂停加仓：{'、'.join(self.execution_plan.get('pause_list', [])) or '无'}",
                f"- 声明：{self.execution_plan.get('disclaimer', '仅供投资辅助，不构成投资建议。')}",
            ]
        )
        return "\n".join(lines)

    def _bond_to_equity_text(self) -> str:
        path = self.execution_plan.get("bond_to_equity_path", {}) if self.execution_plan else {}
        if not path:
            return "债券转权益路径暂不可用。"
        return "\n".join(
            [
                f"- 本周从债券转出：{float(path.get('this_week_transfer_wan', 0.0)):.2f}万元，先进入现金/权益定投池。",
                f"- 本月从债券转出：{float(path.get('this_month_transfer_wan', 0.0)):.2f}万元，分批转向VOO、QQQ、沪深300ETF和恒生科技ETF。",
                f"- 未来三个月计划转出上限：{float(path.get('three_month_transfer_wan', 0.0)):.2f}万元。",
                f"- 原因：{path.get('reason', '暂无')}",
                "- 原则：优先用到期、赎回和新增资金修正，不一次性大幅卖出长期资产。",
            ]
        )

    def _gold_bar_text(self) -> str:
        gold_bar = self.execution_plan.get("gold_bar", {}) if self.execution_plan else {}
        if not gold_bar:
            return "金条估值模块暂不可用。"
        return "\n".join(
            [
                f"- 估值状态：{gold_bar.get('status', 'unknown')}",
                f"- 金条说明：{gold_bar.get('text', '暂无')}",
                "- 操作建议：黄金仓位达到或超过目标时暂停新增；金条不建议因单日价格波动频繁买卖。",
            ]
        )

    def _target_annual_status_text(self) -> str:
        max_deviation = self._max_abs_deviation()
        if max_deviation <= 0.05:
            path_text = "否，资产偏离目标5%以内。"
        elif max_deviation <= 0.08:
            path_text = "轻微偏离，先观察并用新增资金修正。"
        else:
            path_text = "是，偏离超过8%，需要给出再平衡方向。"
        return "\n".join(
            [
                "- 长期设计目标：10%–15%",
                "- 当前策略类型：进取型长期增长",
                "- 当前风险容忍：最大回撤 25%–35%",
                f"- 今日是否偏离目标路径：{path_text}",
                "- 目标配置：美股30% / 港股12% / A股10% / 债券25% / 黄金15% / 现金8%",
                "- 说明：该目标为长期设计目标，不保证收益；仅供投资辅助，不构成投资建议。",
            ]
        )

    def _score_for_asset(self, asset: str, category: str) -> tuple[int, str, str]:
        current = self._category_ratio(category)
        target = self._category_target(category)
        gap = target - current
        cash_ratio = self._category_ratio("现金")
        vix = self._vix_value()
        score = 50 + int(max(min(gap * 250, 25), -25))
        advice = "可小额分批"
        reason = f"{category}当前{self._fmt_pct(current)}，目标{self._fmt_pct(target)}。"

        if asset == "现金":
            if cash_ratio < 0.05:
                return 95, "优先补现金", f"现金{self._fmt_pct(cash_ratio)}低于5%，先恢复安全垫。"
            if cash_ratio <= 0.08:
                return 70, "维持正常现金", f"现金{self._fmt_pct(cash_ratio)}在5%–8%区间，可继续定投。"
            return 35, "不优先增加", f"现金{self._fmt_pct(cash_ratio)}已高于目标附近，新增资金可投向低配资产。"

        if category == "债券" and current > 0.30:
            return 10, "暂停新增", f"债券{self._fmt_pct(current)}超过30%，暂停新增债券。"
        if category == "黄金" and current >= 0.15:
            return 15, "暂停新增", f"黄金{self._fmt_pct(current)}达到或超过15%，不追高。"
        if category == "港股":
            advice = "只小额分批" if current <= target + 0.05 else "暂停新增"
            reason += "港股波动较高，按小额分批处理。"
        if asset == "QQQ" and current >= target:
            score -= 8
            reason += "科技成长暴露已不低，不追高。"
        if category in EQUITY_CATEGORIES:
            _, _, equity_gap = self._equity_current_target_gap()
            if equity_gap >= 0.05:
                score += 15
                advice = "新增资金优先补"
                reason += "权益总仓低于目标5%以上。"
        if vix is not None and 20 <= vix < 30:
            score -= 5
            reason += "VIX 20–30，金额降低30%。"
        elif vix is not None and vix >= 30:
            score -= 10
            advice = "仅小额低吸"
            reason += "VIX >= 30，不追涨。"

        return max(0, min(100, score)), advice, reason

    def _opportunity_rows(self) -> list[dict[str, Any]]:
        assets = [
            ("VOO", "美股"),
            ("QQQ", "美股"),
            ("沪深300ETF", "A股"),
            ("恒生科技ETF", "港股"),
            ("恒生医疗ETF", "港股"),
            ("TLT", "债券"),
            ("黄金", "黄金"),
            ("现金", "现金"),
        ]
        rows = []
        for asset, category in assets:
            score, advice, reason = self._score_for_asset(asset, category)
            rows.append({"asset": asset, "score": score, "advice": advice, "reason": reason})
        return rows

    def _opportunity_score_text(self) -> str:
        lines = [
            "| 资产 | 评分 | 建议 | 原因 |",
            "| --- | ---: | --- | --- |",
        ]
        for row in sorted(self._opportunity_rows(), key=lambda item: item["score"], reverse=True):
            lines.append(f"| {row['asset']} | {row['score']} | {row['advice']} | {row['reason']} |")
        return "\n".join(lines)

    def _pause_add_list_text(self) -> str:
        gold_ratio = self._category_ratio("黄金")
        bond_ratio = self._category_ratio("债券")
        hk_ratio = self._category_ratio("港股")
        us_ratio = self._category_ratio("美股")
        return "\n".join(
            [
                f"- 黄金是否暂停：{'是，黄金已达到或超过15%，暂停新增黄金，不追高。' if gold_ratio >= 0.15 else '否，黄金未超过目标，但仍只做纪律性配置。'} 当前{self._fmt_pct(gold_ratio)}。",
                f"- 债券是否暂停：{'是，债券超过30%，暂停新增债券，不建议继续加仓债券。' if bond_ratio > 0.30 else '否，债券未超过30%，但是否加仓仍看配置缺口。'} 当前{self._fmt_pct(bond_ratio)}。",
                f"- 港股是否只小额分批：{'是，港股波动较高或已不低配，只小额分批。' if hk_ratio >= self._category_target('港股') - 0.05 else '否，港股低配较多时可小额补足，但仍不一次性重仓。'} 当前{self._fmt_pct(hk_ratio)}。",
                f"- 高估值美股是否不追高：{'是，美股已达到或超过目标，不追高。' if us_ratio >= self._category_target('美股') else '是，即使美股低配，也只按VOO/QQQ分批补，不追涨。'} 当前{self._fmt_pct(us_ratio)}。",
            ]
        )

    def _growth_risk_constraints_text(self) -> str:
        return "\n".join(
            [
                "- 追求 10%–15% 年化意味着必须接受更高波动。",
                "- 如果无法承受 25%–35% 回撤，不能执行该目标配置。",
                "- 不使用杠杆，不融资，不做期权，不日内交易。",
                "- 所有内容仅供投资辅助，不构成投资建议；系统不保证收益，不自动交易。",
            ]
        )

    def _data_quality_text(self) -> str:
        quality = self.live_market.get("data_quality", {}) or {}
        rows = quality.get("key_rows", []) or []
        score = int(quality.get("score", 0) or 0)
        lines = [
            f"- 数据可信度评分：{score} / 100",
            f"- 市场数据是否可用：{'是' if quality.get('market_available') else '否'}",
            f"- 宏观数据是否可用：{'是' if quality.get('macro_available') else '否'}",
            f"- 是否仅使用 yfinance/缓存：{'是，已降低建议置信度。' if quality.get('only_yfinance') else '否'}",
            f"- 是否有关键数据缺失：{'是，数据缺失，不做激进判断。' if quality.get('critical_missing') else '否'}",
            f"- 是否使用过期缓存：{'是，数据可能过期，请谨慎使用。' if quality.get('stale_cache_used') else '否'}",
            "",
            "| 关键数据 | 来源 | 实时数据 | 使用缓存 | 是否缺失 | 备注 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        if rows:
            for row in rows:
                lines.append(
                    "| "
                    f"{row.get('name', '未知')} | "
                    f"{row.get('source', 'unavailable')} | "
                    f"{'是' if row.get('is_realtime') else '否'} | "
                    f"{'是' if row.get('cache_used') else '否'} | "
                    f"{'是' if row.get('missing') else '否'} | "
                    f"{row.get('warning', '') or '正常'} |"
                )
        else:
            lines.append("| 关键数据 | unavailable | 否 | 否 | 是 | 数据质量模块未返回明细，不做激进判断。 |")

        warnings = [warning for warning in quality.get("warnings", []) if warning]
        if warnings:
            lines.extend(["", "数据缺失/降级说明："])
            lines.extend(f"- {warning}" for warning in warnings[:8])
        lines.append("")
        lines.append("仅供投资辅助，不构成投资建议。")
        return "\n".join(lines)

    def generate_weekly_report(self, report_date: date | None = None) -> str:
        report_date = report_date or date.today()
        return "\n".join(
            [
                f"# Stone AI Investment Manager Pro V12 每周投资报告 - {report_date.isoformat()}",
                "",
                f"- 总资产：{self.portfolio['total_assets_wan']:.2f} 万元",
                f"- 市场风险评分：{self.market['market_risk_score']} / 100",
                f"- 进攻指数：{self.market['offense_index']} / 100",
                f"- 防守指数：{self.market['defense_index']} / 100",
                f"- 操作等级：{self.decision['operation_level']}",
                f"- 置信度：{self.decision['confidence']}%",
                "",
                "## 本周重点",
                "",
                self.market["summary"],
                "",
                "## 周度复盘",
                "",
                self._weekly_review_text(),
                "",
                "## 配置偏离",
                "",
                self._asset_allocation_text(),
                "",
                "## 本周建议",
                "",
                f"- 买：{self._orders_text(self.decision['buy_orders'])}",
                f"- 卖：{self._orders_text(self.decision['sell_orders'])}",
                f"- 等待：{self._wait_text()}",
                f"- 最大风险：{self.decision['max_risk']}",
                "",
            ]
        )

    def _asset_change_text(self) -> str:
        categories = {item["category"]: item for item in self.portfolio["categories"]}
        order = ["美股", "港股", "A股", "黄金", "债券", "现金"]
        lines = []
        for category in order:
            item = categories.get(category)
            if not item:
                continue
            direction = "超配" if item["deviation_ratio"] > 0 else "低配"
            if abs(item["deviation_ratio"]) < 0.005:
                direction = "接近目标"
            lines.append(
                f"- {category}：{item['amount_wan']:.2f}万元，占比{item['current_ratio'] * 100:.2f}%，"
                f"{direction}{abs(item['deviation_ratio']) * 100:.2f}%。"
            )
        data_warnings = self.portfolio.get("data_warnings", [])
        if data_warnings:
            lines.extend(["", "未估值/估值提示："])
            for warning in data_warnings:
                lines.append(f"- {warning}")
        return "\n".join(lines)

    def _asset_allocation_text(self) -> str:
        return "\n".join(
            f"- {item['category']}：{item['amount_wan']:.2f}万元，占比{item['current_ratio'] * 100:.2f}%，"
            f"目标{item['target_ratio'] * 100:.2f}%，偏离{item['deviation_ratio'] * 100:.2f}%。"
            for item in self.portfolio["categories"]
        )

    def _portfolio_impact_text(self) -> str:
        impacts = self.market.get("portfolio_impacts", {})
        order = ["美股", "港股", "A股", "黄金", "债券", "现金"]
        return "\n".join(f"- 对{category}的影响：{impacts.get(category, '暂无说明')}" for category in order)

    def _live_market_text(self) -> str:
        items = self.live_market.get("items", {})
        if not items:
            errors = self.live_market.get("errors", [])
            if errors:
                return "实时行情暂未获取成功，已写入日志；本次使用手动 market_data.csv 继续分析。"
            return "未启用实时行情数据，本次使用手动 market_data.csv。"

        lines = [f"- 数据源：{self.live_market.get('source', 'unknown')}，时间：{self.live_market.get('fetched_at', '未知')}"]
        for ticker in [
            "VOO",
            "QQQ",
            "^GSPC",
            "^IXIC",
            "3067.HK",
            "3033.HK",
            "2800.HK",
            "510300.SS",
            "GLD",
            "TLT",
            "IEF",
            "UUP",
            "DX-Y.NYB",
            "^VIX",
        ]:
            item = items.get(ticker, {})
            if item.get("status") == "ok":
                cache_text = "，使用缓存" if item.get("cache_used") else ""
                stale_text = "，数据可能过期，请谨慎使用" if item.get("cache_stale") else ""
                lines.append(
                    f"- {ticker}：收盘 {item['close']}，日涨跌 {item['change_pct']}%，"
                    f"来源 {item.get('source', 'unknown')}{cache_text}{stale_text}"
                )
            else:
                lines.append(f"- {ticker}：获取失败，详见 logs/data_router.log")
        return "\n".join(lines)

    def _risk_score_text(self) -> str:
        return "\n".join(
            [
                f"- 市场风险评分：{self.market['market_risk_score']} / 100",
                f"- 进攻指数：{self.market['offense_index']} / 100",
                f"- 防守指数：{self.market['defense_index']} / 100",
                f"- 是否适合加仓：{'是' if self.market['suitable_to_add'] else '否'}",
                f"- 是否适合减仓：{'是' if self.market['suitable_to_reduce'] else '否'}",
                f"- 综合判断是否适合加仓：{self._add_suitability_text()}",
            ]
        )

    def _macro_and_vix_text(self) -> str:
        lines = [
            "未来7天重大宏观事件：",
            self._macro_event_text(),
            "",
            "VIX 风险预警：",
            self._vix_text(),
            "",
            "投资纪律：",
        ]
        for item in self.macro.get("discipline", []):
            lines.append(f"- {item}")
        if not self.macro.get("discipline"):
            lines.append("- 不自动交易，所有建议仅供辅助。")
        return "\n".join(lines)

    def _cross_asset_text(self) -> str:
        if not self.cross_asset:
            return "跨资产数据暂不可用，本次不输出联动判断；仅供投资辅助，不构成投资建议。"

        lines = [
            "跨资产联动分析：",
        ]
        for signal in self.cross_asset.get("signals", []):
            lines.append(f"- {signal}")

        lines.extend(
            [
                "",
                self.cross_asset.get("gold_judgement", "黄金当前判断：暂无。"),
                "",
                self.cross_asset.get("bond_judgement", "债券当前判断：暂无。"),
                "",
                self.cross_asset.get("dollar_judgement", "美元当前判断：暂无。"),
                "",
                self.cross_asset.get("us_hk_relative", "美股与港股强弱对比：暂无。"),
                "",
                self.cross_asset.get("portfolio_impact", "对当前组合的影响：暂无。"),
                "",
                self.cross_asset.get(
                    "disclaimer",
                    "仅供投资辅助，不构成投资建议；系统不会自动交易，也不承诺收益。",
                ),
            ]
        )
        return "\n".join(lines)

    def _ai_advice_text(self) -> str:
        if not self.ai_advice:
            return "AI深度分析未启用：未配置 OPENAI_API_KEY"

        lines = []
        if self.ai_advice.get("enabled"):
            lines.append(f"- 使用模型：{self.ai_advice.get('model', 'unknown')}")

        lines.extend(
            [
                "",
                "AI 投资经理总结：",
                self.ai_advice.get("summary", "AI深度分析未启用：未配置 OPENAI_API_KEY"),
                "",
                "今日最重要风险：",
                self.ai_advice.get("most_important_risk", "暂无"),
                "",
                "今日最建议做的事：",
                self.ai_advice.get("best_action_today", "暂无"),
                "",
                "今日最不建议做的事：",
                self.ai_advice.get("avoid_action_today", "暂无"),
                "",
                "一句话结论：",
                self.ai_advice.get("one_sentence", "暂无"),
                "",
                self.ai_advice.get(
                    "disclaimer",
                    "仅供投资辅助，不构成投资建议；不自动交易，不承诺收益，最终决策由用户自己负责。",
                ),
            ]
        )
        return "\n".join(lines)

    def _history_review_text(self) -> str:
        daily = self.history_review.get("daily", {})
        if not daily:
            return "暂无历史复盘数据；系统将从今天开始积累 investment_log.csv。"

        allocation = daily.get("allocation_7", {})
        advices = daily.get("recent_advices", [])
        lines = [
            f"- 最近7天风险变化：{daily.get('risk_7', '暂无')}",
            f"- 最近30天 Stone Score 变化：{daily.get('stone_30', '暂无')}",
            f"- 最近30天建议风格：{daily.get('advice_bias_30', {}).get('bias', '暂无')}",
            "- 最近7天资产配置变化：",
            f"  - {allocation.get('stock', '股票占比变化数据不足。')}",
            f"  - {allocation.get('bond', '债券占比变化数据不足。')}",
            f"  - {allocation.get('gold', '黄金占比变化数据不足。')}",
            f"  - {allocation.get('cash', '现金占比变化数据不足。')}",
            "- 最近几次系统建议：",
        ]
        if advices:
            lines.extend(f"  - {item}" for item in advices)
        else:
            lines.append("  - 暂无历史建议。")
        lines.extend(
            [
                f"- 当前策略是否需要调整：{daily.get('strategy_adjustment', '暂无')}",
                f"- {self.history_review.get('disclaimer', '历史复盘仅供投资辅助，不构成投资建议。')}",
            ]
        )
        return "\n".join(lines)

    def _weekly_review_text(self) -> str:
        weekly = self.history_review.get("weekly", {})
        if not weekly:
            return "暂无周度复盘数据；系统将从今天开始积累 investment_log.csv。"

        lines = [
            f"- 本周资产配置变化：{weekly.get('asset_change', '暂无')}",
            f"- 本周风险评分变化：{weekly.get('risk_change', '暂无')}",
            "- 本周主要建议：",
        ]
        advices = weekly.get("main_advices", [])
        if advices:
            lines.extend(f"  - {item}" for item in advices)
        else:
            lines.append("  - 暂无历史建议。")
        lines.append("- 下周关注事项：")
        for item in weekly.get("next_week_focus", []):
            lines.append(f"  - {item}")
        lines.append(f"- {self.history_review.get('disclaimer', '历史复盘仅供投资辅助，不构成投资建议。')}")
        return "\n".join(lines)

    def _macro_event_text(self) -> str:
        events = self.macro.get("upcoming_events", [])
        if not events:
            return "- 未来7天暂无已配置的重大宏观事件。"

        lines = []
        for event in events:
            event_date = event.get("date")
            if hasattr(event_date, "isoformat"):
                event_date_text = event_date.isoformat()
            else:
                event_date_text = str(event_date)
            lines.append(f"- {event_date_text}：{event.get('name', '未命名事件')}（{event.get('level', 'medium')}）")
        lines.append(f"- 提醒：{self.macro.get('reminder', '重大事件前保持谨慎。')}")
        return "\n".join(lines)

    def _vix_text(self) -> str:
        vix = self.vix.get("vix")
        current = "暂不可用" if vix is None else f"{float(vix):.2f}"
        return "\n".join(
            [
                f"- VIX 当前水平：{current}",
                f"- 数据来源：{self.vix.get('source', 'unknown')}",
                f"- 风险状态：{self.vix.get('risk_level', '未知')}",
                f"- 风险解释：{self.vix.get('explanation', 'VIX 数据暂不可用，按中性偏谨慎处理。')}",
                f"- 今日是否适合加仓：{self._add_suitability_text()}",
            ]
        )

    def _add_suitability_text(self) -> str:
        if self.macro.get("has_high_event_next_7_days"):
            return "否，未来7天有 high 级别宏观事件，重大事件前不追涨；定投可以继续。"
        if self.vix.get("pause_chasing"):
            return "否，VIX 风险提示要求暂停追涨或等待更清晰信号。"
        if not self.market.get("suitable_to_add"):
            return "否，当前市场风险评分或进攻指数不支持主动加仓。"
        return "是，但仍建议分批执行，避免一次性重仓。"

    def _rule_and_exception_text(self) -> str:
        lines = []
        for item in self.risk["triggered_rules"]:
            lines.append(
                f"- {item['category']}规则触发：是，偏离{item['deviation_ratio'] * 100:.2f}%，"
                f"偏离金额{item['deviation_amount_wan']:.2f}万元。"
            )
        for note in self.decision["exception_notes"]:
            lines.append(f"- 例外机制：{note}")
        return "\n".join(lines) if lines else "- 今日没有触发主要调仓规则。"

    def _rebalance_text(self) -> str:
        if not self.rebalance and not self.allocation_rebalance:
            return "暂无独立调仓建议。"

        lines = []
        if self.allocation_rebalance:
            lines.extend(
                [
                    "当前资产配置 vs 目标配置：",
                    "| 资产类别 | 当前占比 | 目标占比 | 偏离 | 状态 |",
                    "| --- | ---: | ---: | ---: | --- |",
                ]
            )
            for item in self.allocation_rebalance.get("items", []):
                lines.append(
                    "| "
                    f"{item['category']} | "
                    f"{item['current_ratio'] * 100:.2f}% | "
                    f"{item['target_ratio'] * 100:.2f}% | "
                    f"{item['deviation_ratio'] * 100:.2f}% | "
                    f"{item['status']} |"
                )
            lines.extend(
                [
                    "",
                    f"- 是否需要再平衡：{'是' if self.allocation_rebalance.get('need_rebalance') else '否'}",
                    f"- 再平衡规则：{self.allocation_rebalance.get('rule', '偏离<3%不调仓；3%-5%观察；>5%提示再平衡。')}",
                    f"- 再平衡结论：{self.allocation_rebalance.get('summary', '暂无')}",
                    f"- 执行优先级：{self.allocation_rebalance.get('priority', '优先用新增资金再平衡。')}",
                    "- 具体调仓方向：",
                ]
            )
            for direction in self.allocation_rebalance.get("directions", []):
                lines.append(f"  - {direction}")
            lines.append("")

        if self.rebalance:
            lines.extend(
                [
                    f"- 股票类资产占比：{self.rebalance['stock_ratio'] * 100:.2f}%",
                    f"- 现金占比：{self.rebalance['cash_ratio'] * 100:.2f}%",
                    f"- 黄金占比：{self.rebalance['gold_ratio'] * 100:.2f}%",
                    f"- 债券占比：{self.rebalance['bond_ratio'] * 100:.2f}%",
                ]
            )
        for warning in self.rebalance.get("warnings", []):
            lines.append(f"- 风险提示：{warning}")
        for suggestion in self.rebalance.get("suggestions", []):
            lines.append(f"- 配置提示：{suggestion}")
        if self.allocation_rebalance and not self.allocation_rebalance.get("need_rebalance"):
            lines.append(f"- 旧规则观察清单：{self.rebalance.get('today_suggestion', '暂无')}")
        else:
            lines.append(f"- 今日调仓建议：{self.rebalance.get('today_suggestion', '暂无')}")
        lines.append(
            f"- {self.allocation_rebalance.get('disclaimer', self.rebalance.get('disclaimer', '仅供投资辅助，不构成投资建议。'))}"
        )
        return "\n".join(lines)

    def _decision_context_text(self) -> str:
        if not self.allocation_rebalance:
            return ""
        if self.allocation_rebalance.get("need_rebalance"):
            return "说明：本轮再平衡模块提示需要再平衡，以下买卖清单仍需人工确认，不自动交易。"
        return "说明：本轮再平衡模块未提示必须调仓，以下买卖内容仅作为旧目标规则触发后的观察清单。"

    def _orders_text(self, orders: list[dict[str, Any]]) -> str:
        if not orders:
            return "无"
        return "；".join(
            f"{item['name']} {item['amount_wan']:.2f}万元（置信度{item['confidence']}%，{item['reason']}）"
            for item in orders
        )

    def _hold_text(self) -> str:
        hold_list = self.decision.get("hold_list", [])
        return "、".join(hold_list) if hold_list else "无"

    def _wait_text(self) -> str:
        waits = self.decision.get("wait_orders", [])
        if not waits:
            return "无"
        return "；".join(
            f"{item['name']}：{item['action']}（置信度{item['confidence']}%，{item['reason']}）"
            for item in waits
        )

    def _monthly_investment_text(self) -> str:
        if self.execution_plan:
            return "\n".join(
                [
                    f"本月计划买入：{self.execution_plan.get('month_buy_wan', 0.0) * 10000:.0f}元。",
                    f"本周计划买入：{self.execution_plan.get('week_buy_wan', 0.0) * 10000:.0f}元。",
                    f"今日计划买入：{self.execution_plan.get('today_buy_wan', 0.0) * 10000:.0f}元。",
                    f"本月买入拆分：{self._format_orders(self.execution_plan.get('month_orders', []))}",
                    f"本周买入拆分：{self._format_orders(self.execution_plan.get('week_orders', []))}",
                    f"今日买入拆分：{self._format_orders(self.execution_plan.get('today_orders', []))}",
                    f"债券转权益：本月从债券转出{self.execution_plan.get('bond_to_equity_path', {}).get('this_month_transfer_wan', 0.0):.2f}万元，先放入现金/权益定投池。",
                    f"现金纪律：{self.execution_plan.get('cash_policy', '暂无')}",
                    f"暂停新增：{'、'.join(self.execution_plan.get('pause_list', [])) or '无'}",
                    "执行纪律：不自动交易，不满仓，不借钱投资；所有买入都需要人工确认。",
                    "声明：仅供投资辅助，不构成投资建议；不承诺收益。",
                ]
            )

        if self.dca:
            lines = [
                f"本月定投计划：预算{float(self.dca.get('monthly_budget', 0.0)):.2f}元，"
                f"今日建议合计{float(self.dca.get('total_suggested_amount', 0.0)):.2f}元。",
                f"今日是否继续定投：{'是' if self.dca.get('today_continue') else '否'}。",
                f"定投判断：{self.dca.get('summary', '暂无')}",
                f"VIX规则：{self.dca.get('vix_rule', '暂无')}",
                f"宏观规则：{self.dca.get('macro_rule', '暂无')}",
                "",
                "| 标的 | 基础金额 | 今日建议金额 | 动作 | 理由 |",
                "| --- | ---: | ---: | --- | --- |",
            ]
            for item in self.dca.get("targets", []):
                lines.append(
                    "| "
                    f"{item['name']}（{item['symbol']}） | "
                    f"{item['base_amount']:.2f}元 | "
                    f"{item['suggested_amount']:.2f}元 | "
                    f"{item['action']} | "
                    f"{item['reason']} |"
                )
            lines.extend(
                [
                    "",
                    f"纪律：{self.dca.get('discipline', '不自动交易，所有建议仅供辅助。')}",
                    f"声明：{self.dca.get('disclaimer', '仅供投资辅助，不构成投资建议。')}",
                ]
            )
            return "\n".join(lines)

        monthly = self.config.get("monthly_investment", {})
        if self.decision["operation_level"].startswith("D"):
            return "现金低于5%时暂停所有加仓，本月定投资金优先补现金。"
        if not monthly:
            return "未配置月度定投计划。"

        return (
            f"本月定投总额{float(monthly.get('total_wan', 0.0)):.2f}万元："
            f"VOO {float(monthly.get('VOO', 0.0)):.2f}万元、"
            f"QQQ {float(monthly.get('QQQ', 0.0)):.2f}万元、"
            f"沪深300ETF {float(monthly.get('沪深300ETF', 0.0)):.2f}万元、"
            f"现金保留 {float(monthly.get('现金保留', 0.0)):.2f}万元。"
            "V12 建议定投继续，但大额调仓按市场风险分批执行。"
        )

    def _next_7_days_text(self) -> str:
        items = [
            "1. 关注纳斯达克和科技股是否出现连续回撤，决定美股ETF补仓节奏。",
            "2. 关注美国10年国债收益率和美元指数，判断黄金与债券的对冲价值。",
            "3. 关注黄金趋势是否转弱；若转弱，再执行黄金分批减仓。",
        ]
        events = self.macro.get("upcoming_events", [])
        if events:
            event_names = "、".join(str(event.get("name", "未命名事件")) for event in events)
            items.append(f"4. 未来7天关注：{event_names}；重大事件前不追涨，定投可继续。")
        else:
            items.append("4. 未来7天暂无已配置的 high 级别宏观事件，继续关注新增日历。")
        return "\n".join(items)

    def _final_decision_text(self) -> str:
        planned_buy = self._format_orders(self.execution_plan.get("today_orders", [])) if self.execution_plan else self._orders_text(self.decision["buy_orders"])
        planned_wait = self._wait_text()
        if self.execution_plan and self.execution_plan.get("risk_reasons"):
            planned_wait = planned_wait + "；" + "；".join(self.execution_plan.get("risk_reasons", []))
        return "\n".join(
            [
                f"操作等级：{self.decision['operation_level']}",
                f"今日是否调仓：{'是' if self.decision['today_rebalance'] else '否'}",
                f"再平衡模块结论：{'需要再平衡' if self.allocation_rebalance.get('need_rebalance') else '暂不需要再平衡'}",
                f"建议买入：{planned_buy}",
                f"今日买入金额：{self.execution_plan.get('today_buy_wan', 0.0) * 10000:.0f}元" if self.execution_plan else "今日买入金额：详见买入清单",
                f"本周买入金额：{self.execution_plan.get('week_buy_wan', 0.0) * 10000:.0f}元" if self.execution_plan else "本周买入金额：暂无",
                f"本月买入金额：{self.execution_plan.get('month_buy_wan', 0.0) * 10000:.0f}元" if self.execution_plan else "本月买入金额：暂无",
                f"债券转权益：本周{self.execution_plan.get('bond_to_equity_path', {}).get('this_week_transfer_wan', 0.0):.2f}万元，本月{self.execution_plan.get('bond_to_equity_path', {}).get('this_month_transfer_wan', 0.0):.2f}万元" if self.execution_plan else "债券转权益：暂无",
                f"建议卖出：{self._orders_text(self.decision['sell_orders'])}",
                f"建议继续持有：{self._hold_text()}",
                f"建议等待：{planned_wait}",
                f"最大风险：{self.decision['max_risk']}",
                f"一句话结论：{self.decision['one_sentence_conclusion']}",
            ]
        )
