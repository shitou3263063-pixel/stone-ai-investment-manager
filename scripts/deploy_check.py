from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import sys
from typing import NamedTuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.main import run as run_main  # noqa: E402


SMTP_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
README_REQUIRED_SNIPPETS = [
    "python run.py",
    "python scripts/test_email.py",
    "python scripts/final_check.py",
    "python scripts/deploy_check.py",
    "git init",
    'git commit -m "Stone AI Investment Manager Pro V12"',
    "git remote add origin",
    "Run workflow",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "EMAIL_TO",
]


class CheckItem(NamedTuple):
    name: str
    status: str
    message: str


def _git_executable() -> str | None:
    path_git = shutil.which("git")
    if path_git:
        return path_git

    bundled_git = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "native"
        / "git"
        / "cmd"
        / "git.exe"
    )
    if bundled_git.exists():
        return str(bundled_git)
    return None


def _run_git(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    git = _git_executable()
    if not git:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git command not found")

    try:
        return subprocess.run(
            [git, *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git command not found")


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


def _git_initialized() -> CheckItem:
    result = _run_git(["rev-parse", "--is-inside-work-tree"])
    if result.returncode == 0 and result.stdout.strip() == "true":
        return CheckItem("Git 初始化", "OK", "当前目录已在 Git 仓库中。")
    return CheckItem("Git 初始化", "WARN", "当前目录尚未完成 Git 初始化；部署前请执行 git init。")


def _github_remote_exists() -> CheckItem:
    result = _run_git(["remote", "get-url", "origin"])
    if result.returncode == 0 and result.stdout.strip():
        return CheckItem("GitHub remote", "OK", "origin remote 已配置。")
    return CheckItem("GitHub remote", "WARN", "未检测到 origin remote；部署前请添加 GitHub 仓库地址。")


def _workflow_exists() -> CheckItem:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "daily.yml"
    if workflow_path.exists():
        return CheckItem("GitHub Actions 文件", "OK", ".github/workflows/daily.yml 存在。")
    return CheckItem("GitHub Actions 文件", "ERROR", ".github/workflows/daily.yml 不存在。")


def _env_not_tracked() -> CheckItem:
    git_check = _run_git(["rev-parse", "--is-inside-work-tree"])
    if git_check.returncode != 0:
        return CheckItem(".env 跟踪状态", "WARN", "当前不是 Git 仓库，暂时无法确认 .env 是否被跟踪。")

    tracked: list[str] = []
    for path in ["investment_ai_manager/.env", ".env"]:
        result = _run_git(["ls-files", "--error-unmatch", path])
        if result.returncode == 0:
            tracked.append(path)

    if tracked:
        return CheckItem(".env 跟踪状态", "ERROR", ".env 已被 Git 跟踪，请先从索引移除：" + "、".join(tracked))
    return CheckItem(".env 跟踪状态", "OK", ".env 未被 Git 跟踪。")


def _reports_can_generate() -> tuple[CheckItem, CheckItem]:
    try:
        run_main()
    except Exception as exc:  # noqa: BLE001 - 部署检查要友好返回失败原因
        return (
            CheckItem("日报生成", "ERROR", f"运行主程序失败：{exc.__class__.__name__}"),
            CheckItem("今日摘要生成", "ERROR", "主程序失败，today_action.md 未确认。"),
        )

    daily_path = PROJECT_ROOT / "reports" / "daily_report.md"
    today_path = PROJECT_ROOT / "reports" / "today_action.md"
    daily_item = (
        CheckItem("日报生成", "OK", "reports/daily_report.md 已正常生成。")
        if daily_path.exists() and daily_path.stat().st_size > 0
        else CheckItem("日报生成", "ERROR", "reports/daily_report.md 未生成或为空。")
    )
    today_item = (
        CheckItem("今日摘要生成", "OK", "reports/today_action.md 已正常生成。")
        if today_path.exists() and today_path.stat().st_size > 0
        else CheckItem("今日摘要生成", "ERROR", "reports/today_action.md 未生成或为空。")
    )
    return daily_item, today_item


def _gmail_config_complete(env_values: dict[str, str]) -> CheckItem:
    missing = [key for key in SMTP_KEYS if not os.getenv(key, env_values.get(key, "")).strip()]
    if missing:
        return CheckItem("Gmail SMTP 配置", "WARN", "邮件推送未启用，不影响日报生成。缺失：" + "、".join(missing))
    return CheckItem("Gmail SMTP 配置", "OK", "SMTP 五项参数已配置；如 Gmail 应用专用密码正确，可发送日报。")


def _workflow_has_smtp_env() -> CheckItem:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "daily.yml"
    if not workflow_path.exists():
        return CheckItem("Actions SMTP 环境变量", "ERROR", "daily.yml 不存在，无法检查 SMTP env。")

    content = workflow_path.read_text(encoding="utf-8")
    missing = [key for key in SMTP_KEYS if key not in content]
    if missing:
        return CheckItem("Actions SMTP 环境变量", "ERROR", "daily.yml 缺少：" + "、".join(missing))
    return CheckItem("Actions SMTP 环境变量", "OK", "daily.yml 已映射 SMTP/EMAIL_TO Secrets。")


def _readme_has_deploy_docs() -> CheckItem:
    readme_path = PROJECT_ROOT / "README.md"
    if not readme_path.exists():
        return CheckItem("README 部署说明", "ERROR", "README.md 不存在。")

    content = readme_path.read_text(encoding="utf-8")
    missing = [snippet for snippet in README_REQUIRED_SNIPPETS if snippet not in content]
    if missing:
        return CheckItem("README 部署说明", "WARN", "README 部署教程不完整，缺少：" + "、".join(missing))
    return CheckItem("README 部署说明", "OK", "README 已包含 Gmail、GitHub Actions 和部署说明。")


def _overall_status(items: list[CheckItem]) -> str:
    if any(item.status == "ERROR" for item in items):
        return "ERROR"
    if any(item.status == "WARN" for item in items):
        return "WARN"
    return "OK"


def _build_report(items: list[CheckItem]) -> str:
    lines = [
        "# Stone AI Investment Manager Pro V12 部署前检查报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总体状态：{_overall_status(items)}",
        "",
        "## 检查结果",
        "",
    ]
    for item in items:
        lines.append(f"- [{item.status}] {item.name}：{item.message}")

    lines.extend(
        [
            "",
            "## 部署安全提醒",
            "",
            "- 不要提交 `.env`。",
            "- 不要提交 `SMTP_PASSWORD`。",
            "- 不要提交 `OPENAI_API_KEY`。",
            "- GitHub Actions 请使用仓库 Secrets 保存密钥。",
            "",
            "## 常用命令",
            "",
            "```bash",
            "python run.py",
            "python scripts/test_email.py",
            "python scripts/final_check.py",
            "python scripts/deploy_check.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    env_values = _load_env(PROJECT_ROOT / ".env")
    daily_item, today_item = _reports_can_generate()
    items = [
        _git_initialized(),
        _github_remote_exists(),
        _workflow_exists(),
        _env_not_tracked(),
        daily_item,
        today_item,
        _gmail_config_complete(env_values),
        _workflow_has_smtp_env(),
        _readme_has_deploy_docs(),
    ]

    report = _build_report(items)
    print(report)
    return 0 if _overall_status(items) != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
