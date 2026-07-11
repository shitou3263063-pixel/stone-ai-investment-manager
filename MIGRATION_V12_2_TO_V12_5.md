# MIGRATION V12.2 TO V12.5

## 已创建备份

- 备份分支：`backup/v12.2-stable`
- 备份标签：`v12.2-stable-backup`

## 升级方式

V12.5 没有创建第二套主程序，仍使用：

```bash
python main.py
```

## 主要变化

- `portfolio_master.yaml` 升级为唯一资产事实来源。
- 新增 `security_master.yaml`。
- 新增 Portfolio Snapshot。
- 修复现金、黄金、Opportunity持仓映射和一致性验证。
- Smart Grid 保持模拟模式。

## 回退方式

如需回退到V12.2稳定点：

```bash
git switch backup/v12.2-stable
```

或查看标签：

```bash
git checkout v12.2-stable-backup
```

不要在未确认的情况下强制推送回退。
