# CONFIG GUIDE

## 更新持仓

优先修改：

```text
data/portfolio_master.yaml
```

不要直接修改代码。修改后运行：

```bash
python main.py
```

## 更新股票或ETF

在 `holdings` 中修改：

- `quantity`
- `market_value_cny`
- `valuation_time`
- `data_source`

如果使用行情代理，填写 `pricing_proxy`，但不要把代理代码当成真实持仓代码。

## 更新债券

中国债券总额写入 `totals.china_bond`。

10年地债可以作为明细列出，但必须仍包含在中国债券总额内，不得重复计算。

## 更新实物黄金

修改实物金条：

- `quantity`
- `market_value_cny`
- `gold_price_cny_per_gram`
- `manual_override`

黄金分类总额必须等于实物黄金 + 黄金ETF + 其他黄金资产。

## 更新现金

修改：

```yaml
cash_policy:
  account_total_cash_cny:
```

系统会自动计算现金安全储备和可投资现金。

## 标记债券赎回到账

债券资金没有实际到账前，不得计入可投资现金。

到账后，先增加 `cash_policy.account_total_cash_cny`，再根据实际情况降低债券持仓。

## 设置网格预算

默认保持：

```yaml
paper_mode: true
live_advice_enabled: false
auto_trade: false
```

真实网格预算保持 0，除非用户明确手动开启实盘建议模式。
