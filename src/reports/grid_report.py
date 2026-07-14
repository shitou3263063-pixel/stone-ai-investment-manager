from __future__ import annotations

from typing import Any


def _yuan(value: Any) -> str:
    try:
        return f"{float(value):,.0f}元"
    except (TypeError, ValueError):
        return "暂无数据"


def _pct_decimal(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "暂无数据"


def _price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "暂无数据"


def _text(value: Any, fallback: str = "暂无数据") -> str:
    text = "" if value is None else str(value).strip()
    return text or fallback


def _symbol_status_table(symbol: str, item: dict[str, Any]) -> list[str]:
    state = item.get("state", {}) or {}
    signal = item.get("signal", {}) or {}
    review = item.get("review", {}) or {}
    comparable = bool(item.get("snapshot_comparable", True))
    next_buy = _price(state.get("next_buy_price")) if comparable else "暂不计算（历史参数仅供参考）"
    next_sell = _price(state.get("next_sell_price")) if comparable else "暂不计算（历史参数仅供参考）"
    distance_buy = f"{_text(item.get('distance_to_buy_pct'), '暂无数据')}%" if comparable else "暂不计算"
    distance_sell = f"{_text(item.get('distance_to_sell_pct'), '暂无数据')}%" if comparable else "暂不计算"
    rows = [
        f"## {symbol}状态",
        "",
        "| 项目 | 内容 |",
        "| -- | -- |",
        f"| 当前价格 | {_price(item.get('price'))} |",
        f"| 数据时间 | {_text(item.get('data_time'))} |",
        f"| 决策快照 | {'可比' if comparable else '不可比：DATA_NOT_COMPARABLE'} |",
        f"| 市场状态 | {_text(item.get('regime', {}).get('regime'))} |",
        f"| 网格锚点 | {_price(state.get('anchor_price'))} |",
        f"| 动态买入间距 | {_pct_decimal(state.get('buy_spacing_pct'))} |",
        f"| 动态卖出间距 | {_pct_decimal(state.get('sell_spacing_pct'))} |",
        f"| 下一买入价 | {next_buy} |",
        f"| 距离下一买入价 | {distance_buy} |",
        f"| 下一卖出价 | {next_sell} |",
        f"| 距离下一卖出价 | {distance_sell} |",
        f"| 核心仓 | {_text(state.get('core_quantity'), '0')} 股 |",
        f"| 网格仓 | {_text(state.get('grid_quantity'), '0')} 股 |",
        f"| 网格现金 | {_yuan(state.get('available_grid_cash_yuan'))} |",
        f"| 今日信号 | {_text(signal.get('raw_signal'))} |",
        f"| 总风控结论 | {_text(review.get('final_advice'))} |",
        "",
    ]
    return rows


def generate_grid_daily_section(grid_result: dict[str, Any]) -> str:
    if not grid_result or not grid_result.get("enabled"):
        return "\n".join(["# Stone Smart Grid", "", "智能网格模块未启用。"])
    symbols = grid_result.get("symbols", {}) or {}
    total_advice = grid_result.get("today_total_advice_yuan", 0)
    lines = [
        "# Stone Smart Grid",
        "",
        "## 1. 今日网格结论",
        "",
        f"- VOO是否触发：{'是' if symbols.get('VOO', {}).get('signal', {}).get('action') in {'BUY', 'SELL'} else '否'}",
        f"- QQQ是否触发：{'是' if symbols.get('QQQ', {}).get('signal', {}).get('action') in {'BUY', 'SELL'} else '否'}",
        f"- 是否建议执行：{'是' if grid_result.get('approved_count', 0) > 0 else '否'}",
        f"- 今日总建议金额：{_yuan(total_advice)}",
        f"- 总风控是否批准：{'是' if grid_result.get('approved_count', 0) > 0 else '否'}",
        f"- 一句话结论：{grid_result.get('summary')}",
        f"- 决策快照状态：{(grid_result.get('decision_snapshot', {}) or {}).get('status', '暂无数据')}；{(grid_result.get('decision_snapshot', {}) or {}).get('reason', '暂无数据')}",
        "",
    ]
    if "VOO" in symbols:
        lines.extend(_symbol_status_table("VOO", symbols["VOO"]))
    if "QQQ" in symbols:
        lines.extend(_symbol_status_table("QQQ", symbols["QQQ"]))

    lines.extend(
        [
            "## 4. 今日候选操作",
            "",
            "| 标的 | 原始信号 | 建议金额 | 资金来源 | 风控结论 | 是否执行 |",
            "| -- | ---- | ---: | ---- | ---- | ---- |",
        ]
    )
    for symbol, item in symbols.items():
        signal = item.get("signal", {}) or {}
        review = item.get("review", {}) or {}
        lines.append(
            f"| {symbol} | {_text(signal.get('raw_signal'))} | {_yuan(signal.get('amount_yuan'))} | 网格专用预算/模拟资金池 | {_text(review.get('final_advice'))} | {'是' if review.get('approved') else '否'} |"
        )

    lines.extend(["", "## 5. 未触发原因", ""])
    for symbol, item in symbols.items():
        signal = item.get("signal", {}) or {}
        review = item.get("review", {}) or {}
        reasons = review.get("reasons") or [signal.get("reason", "暂无")]
        lines.append(f"- {symbol}：{'；'.join(str(reason) for reason in reasons)}")

    budget = grid_result.get("grid_budget", {}) or {}
    per_symbol_cash = {
        symbol: float((item.get("state", {}) or {}).get("available_grid_cash_yuan", 0) or 0)
        for symbol, item in symbols.items()
    }
    allocation_text = "；".join(f"{symbol}模拟分配{_yuan(amount)}" for symbol, amount in per_symbol_cash.items()) or "暂无分配"
    lines.extend(
        [
            "",
            "## 6. 网格账户状态",
            "",
            f"- 网格总资金：{_yuan(budget.get('configured_total_yuan'))}",
            f"- 模拟现金储备：{_yuan(budget.get('reserved_grid_cash_yuan'))}",
            f"- 可用模拟现金：{_yuan(budget.get('simulated_available_yuan'))}",
            f"- 标的模拟分配：{allocation_text}",
            f"- 可用实盘现金：{_yuan(budget.get('live_available_yuan'))}",
            "- 计算关系：网格总资金 = 模拟现金储备 + 可用模拟现金；标的分配合计与可用模拟现金的差额仅允许为整数四舍五入。以上均为SIMULATION，不计入真实资产或可投资现金。",
            f"- 累计已实现收益：{_yuan(sum((item.get('state', {}) or {}).get('realized_profit_yuan', 0) for item in symbols.values()))}",
            f"- 未实现收益：{_yuan(sum((item.get('state', {}) or {}).get('unrealized_profit_yuan', 0) for item in symbols.values()))}",
            f"- 本月交易次数：{sum((item.get('state', {}) or {}).get('month_trade_count', 0) for item in symbols.values())}",
            "- 本月资金使用率：模拟运行阶段仅记录，不占用真实资金。",
            "- 最大回撤：等待至少20个交易日模拟记录后统计。",
            "",
            "## 7. 后续网格触发价",
            "",
        ]
    )
    for symbol, item in symbols.items():
        state = item.get("state", {}) or {}
        if item.get("snapshot_comparable", True):
            lines.append(f"- {symbol} 下跌至 {_price(state.get('next_buy_price'))}附近触发买入候选；上涨至 {_price(state.get('next_sell_price'))}附近触发卖出候选。")
        else:
            lines.append(
                f"- {symbol}：当前决策快照不可比，暂不计算新的精确触发价或距离；"
                f"历史买入参数{_price(item.get('historical_next_buy_price'))}、历史卖出参数{_price(item.get('historical_next_sell_price'))}仅供追溯。"
            )
    lines.extend(
        [
            "- 即使价格触发，只要 DQS 不足、现金低于安全线、重大事件临近、预算冲突或总风控否决，也不会生成真实执行建议。",
            "",
            "## 8. 风险提示",
            "",
            "- 单边上涨可能导致网格仓过早卖出，跑输买入持有。",
            "- 单边下跌可能连续触发买入，占用资金并扩大浮亏。",
            "- 参数可能失效，尤其在高波动、政策冲击或流动性异常时。",
            "- 数据延迟、汇率、税务、滑点和交易成本会影响实际结果。",
            "- 网格建议不得覆盖长期资产配置、基础定投和机会加仓纪律。",
            "- 系统不自动交易，不承诺收益，所有操作必须人工确认。",
        ]
    )
    return "\n".join(lines)


def generate_grid_weekly_report(grid_result: dict[str, Any]) -> str:
    symbols = grid_result.get("symbols", {}) if grid_result else {}
    lines = [
        "# Stone Smart Grid 周报",
        "",
        f"- 模式：{'模拟' if grid_result.get('paper_mode', True) else '实盘建议'}",
        f"- 本周触发次数：{grid_result.get('candidate_count', 0)}",
        f"- 总风控批准次数：{grid_result.get('approved_count', 0)}",
        f"- 今日建议金额：{_yuan(grid_result.get('today_total_advice_yuan', 0))}",
        f"- 人工确认次数：{len(grid_result.get('applied_manual_trades', []))}",
        "- 买入金额：模拟信号记录在 data/grid/simulation_trades.csv。",
        "- 卖出金额：模拟信号记录在 data/grid/simulation_trades.csv。",
        "- 已实现收益：仅统计人工 confirmed 或模拟记录，不把建议当成交。",
        "- 未实现收益：等待模拟样本累积后统计。",
        "- 资金利用率：模拟阶段不占用真实资金。",
        "",
        "## 标的状态",
        "",
        "| 标的 | 市场状态 | 今日信号 | 风控结论 | 下周触发价 |",
        "| -- | -- | -- | -- | -- |",
    ]
    for symbol, item in symbols.items():
        state = item.get("state", {}) or {}
        trigger_text = (
            f"买{_price(state.get('next_buy_price'))}/卖{_price(state.get('next_sell_price'))}"
            if item.get("snapshot_comparable", True)
            else "DATA_NOT_COMPARABLE（历史参数仅供参考）"
        )
        lines.append(
            f"| {symbol} | {_text(item.get('regime', {}).get('regime'))} | {_text(item.get('signal', {}).get('raw_signal'))} | {_text(item.get('review', {}).get('final_advice'))} | {trigger_text} |"
        )
    lines.extend(
        [
            "",
            "## 下周观察",
            "",
            "- 不自动修改参数。",
            "- 如果连续20个交易日状态稳定、数据完整、模拟交易记录无重复，再考虑人工评估是否开启实盘建议模式。",
            "- 当前仍不自动交易，不承诺收益。",
        ]
    )
    return "\n".join(lines)


def generate_grid_backtest_report(grid_result: dict[str, Any]) -> str:
    backtest = grid_result.get("backtest", {}) if grid_result else {}
    lines = ["# Stone Smart Grid 回测报告", "", f"- 摘要：{backtest.get('summary', '暂无回测结果')}", ""]
    for result in backtest.get("results", []) or []:
        lines.extend([f"## {result.get('symbol')}", "", f"- 状态：{result.get('status')}", f"- 数据源：{result.get('source')}", f"- 覆盖范围：{result.get('coverage')}", ""])
        if result.get("error"):
            lines.extend([f"- 限制：{result.get('error')}", ""])
        if result.get("strategies"):
            lines.extend(
                [
                    "| 策略 | 总收益 | 年化 | 最大回撤 | 波动率 | 夏普 | 卡玛 | 交易次数 | 胜率 | 已实现网格收益 | 超额收益 | 回撤改善 |",
                    "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
                ]
            )
            for strategy in result["strategies"]:
                lines.append(
                    f"| {strategy['name']} | {_pct_decimal(strategy['total_return'])} | {_pct_decimal(strategy['annual_return'])} | {_pct_decimal(strategy['max_drawdown'])} | {_pct_decimal(strategy['volatility'])} | {strategy['sharpe']} | {strategy['calmar']} | {strategy['trade_count']} | {_pct_decimal(strategy['win_rate'])} | {_yuan(strategy['realized_grid_profit_yuan'])} | {_pct_decimal(strategy['excess_vs_buy_hold'])} | {_pct_decimal(strategy['drawdown_improvement_vs_buy_hold'])} |"
                )
            lines.extend(["", "### 参数敏感性", "", "| 网格宽度 | 总收益 | 年化 | 最大回撤 |", "| --: | --: | --: | --: |"])
            for row in result.get("sensitivity", []) or []:
                lines.append(f"| {row['grid_width_pct']}% | {_pct_decimal(row['total_return'])} | {_pct_decimal(row['annual_return'])} | {_pct_decimal(row['max_drawdown'])} |")
            lines.append("")
    lines.extend(["## 说明", "", "- 回测使用复权收盘价；若数据源无法覆盖全部历史，报告只展示实际覆盖范围。", "- 不使用未来数据，不展示伪造收益，不把模拟收益当作真实收益。"])
    return "\n".join(lines)
