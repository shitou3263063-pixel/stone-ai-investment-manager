from __future__ import annotations

from pathlib import Path
import os
import sys

from src.main import run as run_main
from src.portfolio import PortfolioFormatError
from src.system.health_check import format_health_report, run_health_check
from utils.data_loader import project_root


DEFAULT_EMAIL_TO = "shitou3263063@gmail.com"


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
        if key and key not in os.environ:
            os.environ[key] = value
    return values


def _print_email_startup_hints() -> None:
    env_values = _load_env(project_root() / ".env")
    email_to = os.getenv("EMAIL_TO", env_values.get("EMAIL_TO", DEFAULT_EMAIL_TO)).strip() or DEFAULT_EMAIL_TO
    smtp_password = os.getenv("SMTP_PASSWORD", env_values.get("SMTP_PASSWORD", "")).strip()

    if email_to != DEFAULT_EMAIL_TO:
        print(f"邮件收件人当前为 {email_to}，默认收件人是 {DEFAULT_EMAIL_TO}，请确认是否正确。")
    if not smtp_password:
        print("邮件未启用：请在 .env 或 GitHub Secrets 填写 SMTP_PASSWORD（Gmail 应用专用密码）")
    print("")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    _print_email_startup_hints()

    result = run_health_check(auto_fix=True)
    print(format_health_report(result))
    print("")

    if not result.get("can_run", False):
        print("发现 ERROR 项，主程序未运行。请按上方修复建议处理后重试。")
        return 1

    print("开始生成投资日报...")
    try:
        print(run_main())
    except PortfolioFormatError as exc:
        print(f"持仓文件错误：{exc}")
        return 1

    reports_dir = project_root() / "reports"
    daily_report = reports_dir / "daily_report.md"
    today_action = reports_dir / "today_action.md"
    weekly_report = reports_dir / "weekly_report.md"
    system_check_report = reports_dir / "system_check_report.md"
    print("")
    expected_reports = [
        ("今日行动摘要", today_action),
        ("日报路径", daily_report),
        ("周报路径", weekly_report),
        ("系统检查报告", system_check_report),
    ]
    for label, path in expected_reports:
        if path.exists():
            print(f"{label}：{path}")
        else:
            print(f"{label}未生成，请查看上方错误信息。")
            return 1

    print("提示：系统不会自动交易，所有内容仅供投资辅助，不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
