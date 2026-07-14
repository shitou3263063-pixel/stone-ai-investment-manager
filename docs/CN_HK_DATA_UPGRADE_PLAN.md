# A股与港股数据最小升级方案

适用版本：Stone AI Investment Manager V12.6 Stable  
原则：不改变 `main.py`、投资规则、目标配置、现金安全线、预算、DQS阈值或网格模式；只增强现有数据层。

## 1. 推荐架构

```text
main.py
  -> 现有 daily snapshot
  -> 现有 DataRouter
       -> CNMarketAdapter
            主源：Tushare Pro
            备用：AKShare
            官方校验：SSE / SZSE / 巨潮资讯
       -> HKMarketAdapter
            主源：Tushare Pro或现有专业行情API
            备用：AKShare / yfinance
            官方校验：HKEX / HKMA
       -> 现有 US/FRED 路由（不改）
  -> 统一 DataPoint schema
  -> A股/港股专项覆盖率
  -> 现有 DQS、Risk Score、Opportunity Score
```

不建议新建第二套路由。应在 `src/data_sources/` 内增加适配器，再由现有 `data_router.py` 调用。

## 2. 统一数据结构

每个数据点至少保存：

```json
{
  "metric": "510300_close",
  "value": 4.744,
  "previous_value": 4.829,
  "source": "tushare",
  "source_type": "professional_api",
  "source_level": 2,
  "fetched_at": "2026-07-14T08:30:00+08:00",
  "market_date": "2026-07-13",
  "timezone": "Asia/Shanghai",
  "currency": "CNY",
  "unit": "CNY_per_unit",
  "adjustment": "none",
  "status": "ok",
  "freshness": "fresh",
  "confidence": 0.8,
  "fallback_used": false,
  "verified_by_second_source": false,
  "error_message": null
}
```

规则：缺失使用 `null`，不得使用0；过期数据可以显示但不得参与高置信度评分；ETF、指数和代理证券必须使用不同 canonical_id。

## 3. 证券与指数映射

| 用户资产/指标 | canonical_id | 真实代码 | 行情代码 | 类型 | 币种 |
|---|---|---|---|---|---|
| 沪深300ETF | CN_510300 | 510300 | 510300.SH / 510300.SS | 实际持仓ETF | CNY |
| 巨人网络 | CN_002558 | 002558 | 002558.SZ | 实际持仓个股 | CNY |
| 南方恒生科技ETF | HK_03033 | 03033 | 3033.HK | 实际持仓ETF | HKD |
| 恒生医疗ETF | HK_513060 | 513060 | 513060.SH / 513060.SS | 境内QDII ETF | CNY |
| 香港证券ETF | HK_513090 | 513090 | 513090.SH / 513090.SS | 境内港股主题ETF | CNY |
| BABA ADR | US_BABA | BABA | BABA | 美国ADR实际持仓 | USD |
| 阿里巴巴港股 | HK_09988 | 09988 | 9988.HK | 交叉验证/市场参考 | HKD |
| 沪深300指数 | INDEX_CSI300 | 000300.SH | 数据源规范代码 | 指数 | point |
| 恒生指数 | INDEX_HSI | HSI | 数据源规范代码 | 指数 | point |
| 恒生科技指数 | INDEX_HSTECH | HSTECH | 数据源规范代码 | 指数 | point |
| 恒生医疗保健指数 | INDEX_HSHCI | 待官方确认 | 数据源规范代码 | 指数 | point |

`3067.HK` 可作为另一只同指数 ETF 的辅助验证，但不得再作为 `03033` 的直接价格。

## 4. 数据源分层

### A股

| 数据类别 | 主源 | 备用源 | 官方校验 | 备注 |
|---|---|---|---|---|
| 日线/历史/复权 | Tushare Pro | AKShare | SSE/SZSE | Tushare需Token和相应权限 |
| 指数行情/估值 | Tushare Pro | AKShare | 中证指数 | 估值权限需先验证 |
| 个股估值/财务 | Tushare Pro | 无稳定免费完整替代 | 巨潮/SSE/SZSE | 公告只作事实校验，不自动解析全部PDF |
| 成交额/宽度/涨跌停 | Tushare Pro | AKShare | 交易所统计 | 盘后使用，不追求不必要实时 |
| 融资融券 | SSE/SZSE或Tushare | AKShare | SSE/SZSE | 统一人民币元/股数单位 |
| 互联互通 | 交易所/Tushare | AKShare | HKEX/SSE/SZSE | 遵守最新披露口径 |
| 中国宏观 | 国家统计局/人民银行 | Tushare | 官方 | CPI同比/环比与指数水平分开 |

### 港股

| 数据类别 | 主源 | 备用源 | 官方校验 | 备注 |
|---|---|---|---|---|
| 日线/历史/复权 | Tushare Pro或现有专业API | AKShare/yfinance | HKEX | 实时行情可能涉及许可；日终足够日报使用 |
| 指数行情 | 专业API | yfinance代理 | 指数公司/HKEX | 指数不可被ETF冒充 |
| 公司公告/财报日历 | HKEX披露易 | Tushare | HKEX | 只抓与持仓相关的增量 |
| 南向统计 | HKEX | Tushare/AKShare | HKEX | 采用日/月总成交新口径 |
| 卖空比例 | HKEX | 专业API | HKEX | 历史深度可能需要付费 |
| HIBOR/港元流动性 | HKMA API | 本地缓存 | HKMA | 免费、结构化、适合P1 |
| 汇率 | HKMA/可靠行情源 | yfinance | HKMA | USDHKD、HKDCNY需明确直接或交叉计算 |

官方参考：

- [Tushare数据权限与更新频率](https://tushare.pro/document/1?doc_id=108)
- [AKShare股票和港股历史/复权文档](https://akshare.akfamily.xyz/data/stock/stock.html)
- [HKMA HIBOR官方API](https://apidocs.hkma.gov.hk/documentation/market-data-and-statistics/monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily/)
- [HKMA每日港元流动性API](https://apidocs.hkma.gov.hk/documentation/market-data-and-statistics/daily-monetary-statistics/daily-figures-interbank-liquidity/)
- [HKEX卖空统计](https://www.hkex.com.hk/Market-Data/Statistics/Securities-Market/Short-Selling-Turnover-Today?sc_lang=en)
- [巨潮资讯](https://www.cninfo.com.cn/)

## 5. 数据质量规则

1. 主源成功且新鲜：正常使用。
2. 主源失败：调用备用源，并设置 `fallback_used=true`。
3. 两源同时成功：按资产类别阈值比较。
4. 价格差异超过1%、指数点位差异超过0.5%、汇率差异超过0.3%时标记冲突；阈值应放入配置。
5. 冲突时保留更高等级来源，但将 `confidence` 降至不高于0.6，禁止精确金额建议。
6. A/H 关键持仓价格缺失、映射不一致或币种未知时，该市场 `trade_confidence_gate=false`。
7. 市场数据过期规则按交易日而不是自然小时机械判断；周末允许最近有效收盘，但必须标注 `previous_close`。
8. A股时区固定为 `Asia/Shanghai`，港股固定为 `Asia/Hong_Kong`，不能由数据供应商名称推断。
9. 港币资产换算必须保存原始HKD价格、汇率、CNY估值和各自时间戳。
10. 复权数据必须保存 `adjustment=none/qfq/hfq`，不同口径不得直接拼接。

## 6. A/H 专项覆盖率与决策门槛

建议在现有 Source Audit 中增加市场级结果，不改变全局DQS阈值：

```text
cn_market_coverage_pct
hk_market_coverage_pct
cn_direct_holding_quote_coverage_pct
hk_direct_holding_quote_coverage_pct
cn_critical_missing
hk_critical_missing
```

建议门槛：

- 关键持仓直接行情覆盖 < 80%：该市场只允许方向观察。
- 关键持仓映射冲突：该标的禁止形成交易候选。
- 市场行情、估值/基本面、宽度/资金流三类证据未至少覆盖两类：不得给高置信度买入建议。
- 只有 Level 3 单源：可以展示普通观察，不得宣称双源验证完成。

## 7. 实施优先级

### P0：正确性修复，最小范围

预计修改：

- `data/security_master.yaml`：03033直接行情改为3033.HK；3067改为独立代理证券。
- `src/data_sources/data_router.py`：加入002558、513060、513090；按交易所设置时区/币种/单位。
- `config/source_registry.yaml`：登记新增关键持仓与正确主备源。
- `src/decision/v12_1_decision.py`：恒生科技评分改用3033.HK；缺失标的不再使用无证据默认高分。
- `src/data_sources/source_audit.py`：输出A/H专项覆盖率，并保留各提供方失败原因。
- `tests/`：增加代码映射、时区、缺失不为0、代理不冒充持仓测试。

P0 不新增依赖，不改变美股和宏观模块。

### P1：稳定主源接入

预计新增：

- `src/data_sources/tushare_client.py`
- `src/data_sources/cn_market_adapter.py`
- `src/data_sources/hk_market_adapter.py`
- `src/data_sources/hkma_client.py`
- 可选 `src/data_sources/akshare_client.py`

依赖与变量：

```text
tushare>=1.4,<2.0
TUSHARE_TOKEN=
```

AKShare 建议在确认接口稳定后再锁定版本；若启用：

```text
akshare>=1.18,<2.0
```

P1 先覆盖行情、复权、基础估值、财务、指数、HIBOR和持仓公告；不一次接入所有增强字段。

### P2：增强数据

- 市场宽度、涨跌停、行业强弱
- ETF资金流
- 南向/互联互通统计
- 港股卖空比例
- AH溢价
- 盈利预测

P2 只有在来源许可、稳定性和维护成本明确后实施。

## 8. 测试清单

- `03033` 必须映射到 `3033.HK`，不得映射到 `3067.HK`。
- 002558、513060、513090必须被路由实际请求。
- A/H时间戳的交易所时区正确。
- 港币、人民币、美元原值和换算值不混用。
- ETF与指数 canonical_id 不同。
- qfq/hfq/none 不能混算。
- 主源失败后备用源启用，并保留错误原因。
- 缺失值保持 `null`，不得变成0。
- 单源、过期或冲突数据压低专项完整度和标的置信度。
- A/H关键覆盖不足时，Opportunity Score只能“观察”。
- 美股、FRED、邮件、网格和现有资产配置回归测试保持通过。

## 9. 影响评估

- 对美股行情：无结构性影响。
- 对FRED宏观：无影响。
- 对目标配置与交易规则：无影响。
- 对运行时间：P0影响很小；P1会增加A/H请求，需要缓存、批量接口与超时控制。
- 对GitHub Actions：需增加可选 `TUSHARE_TOKEN` Secret；没有Token时应安全降级，不能让日报崩溃。
- 对自动交易：无；系统仍只生成建议并人工确认。

## 10. 推荐决策

先实施 P0，再运行至少5个交易日观察数据稳定性；P0稳定后再决定是否购买或配置 Tushare 权限。不要在同一提交同时引入 Tushare、AKShare、HKEX网页解析和评分重写。

