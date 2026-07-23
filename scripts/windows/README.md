# Stone AI 盘中监控（Windows）

- 双击 `start_intraday_monitor.bat`：按 60 秒间隔持续监控。
- 双击 `run_intraday_once.bat`：执行一次行情检查并保留窗口。
- 双击 `start_intraday_monitor_dry_run.bat`：持续监控；提醒只打印，不连接 SMTP。

运行前请先启动并登录 Futu OpenD，确认本机 `127.0.0.1:11111` 可用，并按项目依赖创建 `.venv`。脚本不会自动安装依赖、修改 `.env` 或启用交易权限。

停止持续监控时在窗口内按 `Ctrl+C`。程序会关闭 Futu 行情连接、SQLite 和日志。不要默认使用强制 `taskkill`。

结构化日志写入 `logs/intraday_monitor/`；SQLite 仅使用 `data/monitoring/intraday_monitor.sqlite3`。两者均为运行产物，不进入 Git。
