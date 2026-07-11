# DATA DICTIONARY

## Portfolio Snapshot

- `snapshot_date`：快照日期。
- `total_assets`：总资产，单位人民币元。
- `asset_class`：资产类别，统一为美股、港股、A股、债券、黄金、现金。
- `security_name`：用户实际持有资产名称。
- `security_code`：用户实际持仓代码。
- `pricing_proxy`：行情参考代码，不等于真实持仓时必须明确标注。
- `quantity`：持仓数量。
- `market_value_cny`：人民币市值。
- `data_source`：资产事实来源。
- `confidence`：数据置信度。
- `strategy_bucket`：资产策略分桶，例如 core_etf、single_stock、defensive_gold。

## 现金字段

- `account_total_cash_yuan`：账户总现金。
- `cash_safety_reserve_yuan`：现金安全储备。
- `investable_cash_yuan`：可投资现金。
- `live_grid_cash_yuan`：网格实盘专用现金。
- `paper_grid_cash_yuan`：网格模拟现金，不计入真实资产。
- `actual_bond_cash_arrived_yuan`：实际到账的债券赎回或到期资金。

## 预算字段

- `budget_id`：预算池唯一编号。
- `today_total_yuan`：今日可执行真实金额。
- `conditional_bond_to_equity_month_yuan`：条件性债券转权益月度上限。
- `approved_bond_to_equity_month_yuan`：本月已批准可执行额度。

## 评分字段

- `DQS`：数据质量评分。
- `Risk Score`：市场风险评分。
- `Opportunity Score`：机会排序评分，不等于交易指令。
- `consistency.status`：PASS / WARNING / FAIL。
