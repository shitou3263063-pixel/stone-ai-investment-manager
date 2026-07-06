from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.system.health_check import format_health_report, run_health_check  # noqa: E402


SMTP_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]


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


def _mask_status(key: str, env_values: dict[str, str]) -> str:
    value = os.getenv(key, env_values.get(key, "")).strip()
    return "已配置" if value else "未配置"


def _workflow_has_keys(keys: list[str]) -> dict[str, bool]:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "daily.yml"
    if not workflow_path.exists():
        return {key: False for key in keys}

    content = workflow_path.read_text(encoding="utf-8")
    return {key: key in content for key in keys}


def build_system_check_report() -> str:
    env_values = _load_env(PROJECT_ROOT / ".env")
    health_result = run_health_check(auto_fix=True)
    workflow_status = _workflow_has_keys(SMTP_KEYS)
    all_smtp_configured = all(_mask_status(key, env_values) == "已配置" for key in SMTP_KEYS)

    lines = [
        "# Stone AI Investment Manager Pro V12 系统检查报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总体状态：{health_result.get('status', 'UNKNOWN')}",
        f"- 是否可运行：{'是' if health_result.get('can_run') else '否'}",
        "",
        "## 基础自检",
        "",
        format_health_report(health_result),
        "",
        "## Gmail 邮件推送状态",
        "",
        f"- 是否已配置 SMTP 五项参数：{'是' if all_smtp_configured else '否'}",
        "- 测试命令：`python scripts/test_email.py`",
        "",
        "## SMTP 参数检查",
        "",
    ]

    for key in SMTP_KEYS:
        lines.append(f"- {key}：{_mask_status(key, env_values)}")

    lines.extend(["", "## GitHub Actions Secrets 映射检查", ""])
    for key, present in workflow_status.items():
        lines.append(f"- {key}：{'daily.yml 已映射' if present else 'daily.yml 未映射'}")

    lines.extend(
        [
            "",
            "## 测试命令",
            "",
            "```bash",
            "python scripts/test_email.py",
            "python run.py",
            "```",
            "",
            "## 邮件发送失败常见原因",
            "",
            "- Gmail 未开启两步验证。",
            "- SMTP_PASSWORD 不是 Gmail 应用专用密码。",
            "- GitHub Secrets 没配置或变量名写错。",
            "- 当前运行环境网络受限，无法连接 Gmail SMTP。",
            "",
            "## 安全说明",
            "",
            "- 本报告只显示是否配置，不输出 SMTP_PASSWORD 的具体值。",
            "- 不要把 `.env` 提交到 GitHub。",
            "- 邮件未配置或发送失败不会影响日报生成。",
            "- 系统只提醒，不自动交易；所有内容仅供投资辅助，不构成投资建议。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report = build_system_check_report()
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "system_check_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"系统检查报告已生成：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
