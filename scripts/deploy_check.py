from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import sys
from typing import NamedTuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app import run as run_app  # noqa: E402


SMTP_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
DATA_KEYS = ["FRED_API_KEY", "ALPHA_VANTAGE_API_KEY", "FINNHUB_API_KEY"]
README_REQUIRED_SNIPPETS = [
    "python main.py",
    "python scripts/test_email.py",
    "python scripts/final_check.py",
    "python scripts/deploy_check.py",
    "git init",
    'git commit -m "Stone AI Investment Manager Pro V12.7.1 Final Freeze"',
    "git remote add origin",
    "Run workflow",
    "SMTP_HOST",
    "SMTP_PASSWORD",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
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
    return str(bundled_git) if bundled_git.exists() else None


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    git = _git_executable()
    if not git:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git command not found")
    try:
        return subprocess.run([git, *args], cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=20, check=False)
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _git_initialized() -> CheckItem:
    result = _run_git(["rev-parse", "--is-inside-work-tree"])
    if result.returncode == 0 and result.stdout.strip() == "true":
        return CheckItem("Git 初始化", "OK", "当前目录是 Git 仓库。")
    return CheckItem("Git 初始化", "WARN", "当前目录不是 Git 仓库，部署前需要执行 git init。")


def _github_remote_exists() -> CheckItem:
    result = _run_git(["remote", "get-url", "origin"])
    if result.returncode == 0 and result.stdout.strip():
        return CheckItem("GitHub remote", "OK", f"origin 已配置：{result.stdout.strip()}")
    return CheckItem("GitHub remote", "WARN", "未检测到 origin remote，部署前需要绑定 GitHub 仓库。")


def _workflow_exists_and_uses_main() -> CheckItem:
    path = PROJECT_ROOT / ".github" / "workflows" / "daily.yml"
    if not path.exists():
        return CheckItem("GitHub Actions", "ERROR", ".github/workflows/daily.yml 不存在。")
    content = path.read_text(encoding="utf-8")
    if "python main.py" not in content:
        return CheckItem("GitHub Actions", "ERROR", "daily.yml 没有调用唯一正式入口 python main.py。")
    if "python src/main.py" in content:
        return CheckItem("GitHub Actions", "ERROR", "daily.yml 仍包含旧入口 python src/main.py。")
    return CheckItem("GitHub Actions", "OK", "daily.yml 存在，并调用 python main.py。")


def _env_not_tracked() -> CheckItem:
    result = _run_git(["ls-files", "--error-unmatch", ".env"])
    if result.returncode == 0:
        return CheckItem(".env 安全", "ERROR", ".env 已被 Git 跟踪，请先从索引移除。")
    return CheckItem(".env 安全", "OK", ".env 未被 Git 跟踪。")


def _reports_can_generate() -> list[CheckItem]:
    try:
        run_app(send_email=False)
    except Exception as exc:  # noqa: BLE001
        return [
            CheckItem("日报生成", "ERROR", f"主程序运行失败：{exc.__class__.__name__}: {exc}"),
            CheckItem("今日行动生成", "ERROR", "主程序失败，today_action.md 未确认。"),
        ]

    results: list[CheckItem] = []
    for name, rel_path in [("日报生成", "reports/daily_report.md"), ("今日行动生成", "reports/today_action.md")]:
        path = PROJECT_ROOT / rel_path
        if path.exists() and path.stat().st_size > 0:
            results.append(CheckItem(name, "OK", f"{rel_path} 已生成。"))
        else:
            results.append(CheckItem(name, "ERROR", f"{rel_path} 未生成或为空。"))
    return results


def _email_config(env_values: dict[str, str]) -> CheckItem:
    missing = [key for key in SMTP_KEYS if not os.getenv(key, env_values.get(key, "")).strip()]
    if missing:
        return CheckItem("Gmail SMTP", "WARN", "邮件未完整配置，不影响报告生成。缺少：" + ", ".join(missing))
    return CheckItem("Gmail SMTP", "OK", "SMTP 配置完整。")


def _data_source_config(env_values: dict[str, str]) -> CheckItem:
    missing = [key for key in DATA_KEYS if not os.getenv(key, env_values.get(key, "")).strip()]
    if missing:
        return CheckItem("权威数据源", "WARN", "部分数据源未配置，会降级到备用源或缓存。缺少：" + ", ".join(missing))
    return CheckItem("权威数据源", "OK", "FRED、Alpha Vantage、Finnhub 环境变量均已配置。")


def _workflow_env_keys() -> CheckItem:
    path = PROJECT_ROOT / ".github" / "workflows" / "daily.yml"
    if not path.exists():
        return CheckItem("Actions Secrets 映射", "ERROR", "daily.yml 不存在。")
    content = path.read_text(encoding="utf-8")
    required = SMTP_KEYS + DATA_KEYS + ["OPENAI_API_KEY"]
    missing = [key for key in required if key not in content]
    if missing:
        return CheckItem("Actions Secrets 映射", "ERROR", "daily.yml 缺少：" + ", ".join(missing))
    return CheckItem("Actions Secrets 映射", "OK", "邮件、OpenAI 和数据源 Secrets 均已映射。")


def _readme_docs() -> CheckItem:
    path = PROJECT_ROOT / "README.md"
    if not path.exists():
        return CheckItem("README", "ERROR", "README.md 不存在。")
    content = path.read_text(encoding="utf-8")
    missing = [snippet for snippet in README_REQUIRED_SNIPPETS if snippet not in content]
    if missing:
        return CheckItem("README", "WARN", "README 缺少部署说明片段：" + ", ".join(missing))
    return CheckItem("README", "OK", "README 包含本地运行、部署、Secrets 和手动 Actions 说明。")


def _overall(items: list[CheckItem]) -> str:
    if any(item.status == "ERROR" for item in items):
        return "ERROR"
    if any(item.status == "WARN" for item in items):
        return "WARN"
    return "OK"


def _build_report(items: list[CheckItem]) -> str:
    lines = [
        "# Stone AI Investment Manager Pro V12.7.1 Final Freeze 部署前检查报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总体状态：{_overall(items)}",
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
            "- 不要提交 `FRED_API_KEY`、`ALPHA_VANTAGE_API_KEY`、`FINNHUB_API_KEY`。",
            "- GitHub Actions 请使用仓库 Secrets 保存密钥。",
            "",
            "## 常用命令",
            "",
            "```bash",
            "python main.py",
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
    items = [
        _git_initialized(),
        _github_remote_exists(),
        _workflow_exists_and_uses_main(),
        _env_not_tracked(),
        *_reports_can_generate(),
        _email_config(env_values),
        _data_source_config(env_values),
        _workflow_env_keys(),
        _readme_docs(),
    ]

    report = _build_report(items)
    (PROJECT_ROOT / "reports").mkdir(exist_ok=True)
    (PROJECT_ROOT / "reports" / "deploy_check_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if _overall(items) != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
