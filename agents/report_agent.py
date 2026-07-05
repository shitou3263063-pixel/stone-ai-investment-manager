from __future__ import annotations

from datetime import date
from typing import Any


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
        self.cross_asset = cross_asset_result or {}
        self.ai_advice = ai_advice_result or {}
        self.history_review = history_review_result or {}

    def generate_daily_report(self, report_date: date | None = None) -> str:
        report_date = report_date or date.today()
        return "\n".join(
            [
                "📅 Stone AI Investment Manager Pro V12 每日投资简报",
                "",
                f"日期：{report_date.isoformat()}",
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
                lines.append(
                    f"- {ticker}：收盘 {item['close']}，日涨跌 {item['change_pct']}%"
                )
            else:
                lines.append(f"- {ticker}：获取失败，详见 logs/market_data.log")
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
        return "\n".join(
            [
                f"操作等级：{self.decision['operation_level']}",
                f"今日是否调仓：{'是' if self.decision['today_rebalance'] else '否'}",
                f"再平衡模块结论：{'需要再平衡' if self.allocation_rebalance.get('need_rebalance') else '暂不需要再平衡'}",
                f"建议买入：{self._orders_text(self.decision['buy_orders'])}",
                f"建议卖出：{self._orders_text(self.decision['sell_orders'])}",
                f"建议继续持有：{self._hold_text()}",
                f"建议等待：{self._wait_text()}",
                f"最大风险：{self.decision['max_risk']}",
                f"一句话结论：{self.decision['one_sentence_conclusion']}",
            ]
        )
