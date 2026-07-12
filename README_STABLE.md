# Stone AI Investment Manager Pro V12.6 Stable

这是当前项目的最终稳定版收尾版本。系统定位是个人投资辅助与风险复核工具，不自动交易，不连接券商下单权限，不承诺收益。

## 正式入口

本地运行：

```bash
python main.py
```

GitHub Actions 也必须只调用：

```bash
python main.py
```

## 核心能力

- 统一 Portfolio Snapshot 作为资产事实来源。
- 自动生成 today_action、daily、weekly、monthly、grid、system audit 报告。
- 计算资产配置、DQS、风险评分、Opportunity Score、资金预算和债券迁移路线图。
- Smart Grid 默认只运行 SIMULATION，不产生真实下单指令。
- OpenAI 不可用时自动降级到 Rule Enhanced / Safe Mode。
- Gmail 只作为报告发送渠道，不作为资产或成交事实来源。

## 版本冻结

V12.6 Stable 后，主分支只建议接受三类修改：

1. 严重 Bug 修复。
2. 数据源接口失效或第三方 API 变化修复。
3. 用户投资目标、资产分类或运行环境发生重大变化后的必要修改。

日常市场涨跌通过数据、配置、风控和报告处理，不再通过频繁修改代码处理。
