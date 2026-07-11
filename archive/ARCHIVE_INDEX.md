# V12.5 Stable Archive Index

归档内容只用于历史追溯，不得被生产流程、GitHub Actions 或本地正式运行调用。

| 原位置 | 归档位置 | 原因 | 允许恢复 |
| -- | -- | -- | -- |
| `run.py` | `archive/legacy_entrypoints/run_deprecated.py` | V12.5之前的一键入口，依赖已废弃的`src.main` | 仅用于历史排查，不得恢复为生产入口 |
| `src/main.py` | `archive/legacy_entrypoints/src_main_deprecated.py` | V12.5之前的业务入口，已由根目录`main.py`和`src/app.py`取代 | 仅用于历史排查，不得恢复为生产入口 |

本次扫描没有发现需要迁移的旧版工作流、多套`requirements.txt`、多套`.env.example`或历史日报副本。`reports/`中保留的是当前生产输出；持仓、交易记录、执行状态和网格模拟记录均未移动或删除。
