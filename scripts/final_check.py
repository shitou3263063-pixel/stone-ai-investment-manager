from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.system.health_check import HealthItem, format_health_report, run_health_check  # noqa: E402


WECOM_KEYS = ["WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_SECRET", "WECOM_USER_ID"]


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


def _workflow_has_wecom_keys() -> dict[str, bool]:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "daily.yml"
    if not workflow_path.exists():
        return {key: False for key in WECOM_KEYS}

    content = workflow_path.read_text(encoding="utf-8")
    return {key: key in content for key in WECOM_KEYS}


def _find_item(items: list[object], name: str) -> HealthItem | None:
    for item in items:
        if isinstance(item, HealthItem) and item.name == name:
            return item
    return None


def build_system_check_report() -> str:
    env_values = _load_env(PROJECT_ROOT / ".env")
    health_result = run_health_check(auto_fix=True)
    health_items = list(health_result.get("items", []))
    wecom_item = _find_item(health_items, "企业微信应用推送")
    workflow_status = _workflow_has_wecom_keys()
    all_wecom_configured = all(_mask_status(key, env_values) == "已配置" for key in WECOM_KEYS)

    lines = [
        "# Stone AI Investment Manager Pro V11 系统检查报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总体状态：{health_result.get('status', 'UNKNOWN')}",
        f"- 是否可运行：{'是' if health_result.get('can_run') else '否'}",
        "",
        "## 基础自检",
        "",
        format_health_report(health_result),
        "",
        "## 企业微信点对点推送状态",
        "",
        f"- 状态：{wecom_item.status if wecom_item else 'WARN'}",
        f"- 说明：{wecom_item.message if wecom_item else '企业微信点对点推送未启用，不影响本地日报生成。'}",
        f"- 是否已配置 WECOM 四项参数：{'是' if all_wecom_configured else '否'}",
        "",
        "## WECOM 参数检查",
        "",
    ]

    for key in WECOM_KEYS:
        lines.append(f"- {key}：{_mask_status(key, env_values)}")

    lines.extend(
        [
            "",
            "## GitHub Actions Secrets 映射检查",
            "",
        ]
    )
    for key, present in workflow_status.items():
        lines.append(f"- {key}：{'daily.yml 已映射' if present else 'daily.yml 未映射'}")

    lines.extend(
        [
            "",
            "## 测试命令",
            "",
            "```bash",
            "python scripts/test_wecom.py",
            "python run.py",
            "```",
            "",
            "## 企业微信发送失败常见原因",
            "",
            "- Secret 错误。",
            "- UserID 错误。",
            "- 应用可见范围没有包含该用户。",
            "- 企业微信未登录或账号不可用。",
            "- GitHub Secrets 没配置。",
            "- 当前运行环境网络受限，无法连接企业微信 API。",
            "",
            "## 安全说明",
            "",
            "- 本报告只显示是否配置，不输出 WECOM_SECRET 的具体值。",
            "- 不要把 `.env` 提交到 GitHub。",
            "- 企业微信未配置或发送失败不会影响日报生成。",
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
