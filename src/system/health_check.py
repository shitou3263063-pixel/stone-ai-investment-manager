from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
from typing import Iterable

from src.portfolio import PortfolioFormatError, load_portfolio
from utils.data_loader import project_root


STATUS_ORDER = {"OK": 0, "WARN": 1, "ERROR": 2}
DEFAULT_EMAIL_TO = "shitou3263063@gmail.com"
IMPORT_NAME_MAP = {
    "PyYAML": "yaml",
}


@dataclass
class HealthItem:
    name: str
    status: str
    message: str
    fix: str = ""


DEFAULT_PORTFOLIO = """category,name,amount_wan,currency,quantity,unit,note
美股,VOO,0,CNY,,,标普500ETF
港股,恒生科技ETF,0,CNY,,,港股科技ETF
A股,沪深300ETF,0,CNY,,,A股宽基ETF
债券,中国债券,0,CNY,,,中国债券资产
黄金,实物金条,,CNY,565,克,每日按黄金价格自动估值
黄金,黄金ETF,0,CNY,,,黄金ETF
现金,现金,0,CNY,,,现金与货币资金
"""

DEFAULT_SETTINGS = """macro_events:
  - name: FOMC利率决议
    date: 2026-07-29
    level: high
  - name: CPI数据
    date: 2026-07-15
    level: high
  - name: 非农就业数据
    date: 2026-08-07
    level: high

dca_plan:
  enabled: true
  monthly_budget: 10000
  targets:
    - symbol: VOO
      name: 标普500ETF
      base_amount: 3500
    - symbol: QQQ
      name: 纳斯达克100ETF
      base_amount: 2500
    - symbol: 510300.SS
      name: 沪深300ETF
      base_amount: 2000
    - symbol: 3067.HK
      name: 恒生科技ETF
      base_amount: 2000

target_allocation:
  us_stock: 30
  hk_stock: 12
  cn_stock: 10
  bond: 25
  gold: 15
  cash: 8

data_sources:
  fred:
    enabled: true
    api_key_env: FRED_API_KEY
  alpha_vantage:
    enabled: true
    api_key_env: ALPHA_VANTAGE_API_KEY
  finnhub:
    enabled: true
    api_key_env: FINNHUB_API_KEY
  yfinance:
    enabled: true
"""

DEFAULT_ENV_EXAMPLE = """SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=你的Gmail邮箱
SMTP_PASSWORD=你的Gmail应用专用密码
EMAIL_TO=shitou3263063@gmail.com
OPENAI_API_KEY=你的OpenAI API Key
FRED_API_KEY=
ALPHA_VANTAGE_API_KEY=
FINNHUB_API_KEY=
"""

DEFAULT_ENV = """EMAIL_TO=shitou3263063@gmail.com
"""


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _legacy_config_has_password(path: Path) -> bool:
    if not path.exists():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("password:"):
            _, value = stripped.split(":", 1)
            return bool(value.strip())
    return False


def _package_name(requirement: str) -> str:
    name = requirement.strip()
    for marker in [">=", "==", "<=", "~=", ">", "<"]:
        if marker in name:
            name = name.split(marker, 1)[0]
            break
    name = name.strip()
    return IMPORT_NAME_MAP.get(name, name).replace("-", "_")


def _missing_requirements(requirements_path: Path) -> list[str]:
    if not requirements_path.exists():
        return []
    missing = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        package = _package_name(line)
        if package and importlib.util.find_spec(package) is None:
            missing.append(line)
    return missing


def _ensure_file(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _status_summary(items: Iterable[HealthItem]) -> str:
    worst = "OK"
    for item in items:
        if STATUS_ORDER[item.status] > STATUS_ORDER[worst]:
            worst = item.status
    return worst


def run_health_check(auto_fix: bool = True) -> dict[str, object]:
    """执行系统自检；可修复项会自动创建模板或文件夹。"""
    root = project_root()
    repo_root = root
    items: list[HealthItem] = []

    py_version = sys.version_info
    if py_version >= (3, 10):
        items.append(HealthItem("Python版本", "OK", f"当前 Python {py_version.major}.{py_version.minor}.{py_version.micro}。"))
    else:
        items.append(
            HealthItem(
                "Python版本",
                "ERROR",
                f"当前 Python {py_version.major}.{py_version.minor}.{py_version.micro} 过低。",
                "请安装 Python 3.10 或更高版本，推荐 Python 3.11。",
            )
        )

    requirements_path = root / "requirements.txt"
    missing = _missing_requirements(requirements_path)
    if not requirements_path.exists():
        items.append(HealthItem("依赖文件", "ERROR", "requirements.txt 不存在。", "请恢复 requirements.txt。"))
    elif missing:
        items.append(
            HealthItem(
                "requirements依赖",
                "WARN",
                "部分依赖未安装：" + "、".join(missing),
                "可运行：python -m pip install -r requirements.txt",
            )
        )
    else:
        items.append(HealthItem("requirements依赖", "OK", "requirements.txt 中的依赖已安装。"))

    portfolio_path = root / "data" / "portfolio.csv"
    created = _ensure_file(portfolio_path, DEFAULT_PORTFOLIO) if auto_fix else False
    if portfolio_path.exists():
        try:
            load_portfolio(portfolio_path)
            portfolio_status = "OK" if not created else "WARN"
            portfolio_message = (
                "data/portfolio.csv 存在且格式可读取。"
                if not created
                else "data/portfolio.csv 缺失，已创建模板，请填写真实持仓。"
            )
        except PortfolioFormatError as exc:
            portfolio_status = "WARN"
            portfolio_message = f"data/portfolio.csv 格式需要修复：{exc}"
        items.append(
            HealthItem(
                "持仓文件",
                portfolio_status,
                portfolio_message,
            )
        )
    else:
        items.append(HealthItem("持仓文件", "ERROR", "data/portfolio.csv 不存在。", "请创建或恢复持仓文件。"))

    settings_path = root / "config" / "settings.yaml"
    created = _ensure_file(settings_path, DEFAULT_SETTINGS) if auto_fix else False
    if settings_path.exists():
        items.append(
            HealthItem(
                "策略配置",
                "OK" if not created else "WARN",
                "config/settings.yaml 存在。" if not created else "config/settings.yaml 缺失，已创建默认模板。",
            )
        )
    else:
        items.append(HealthItem("策略配置", "ERROR", "config/settings.yaml 不存在。", "请创建或恢复策略配置。"))

    reports_dir = root / "reports"
    if auto_fix:
        reports_dir.mkdir(exist_ok=True)
    items.append(
        HealthItem(
            "报告目录",
            "OK" if reports_dir.exists() else "ERROR",
            "reports 文件夹存在。" if reports_dir.exists() else "reports 文件夹不存在。",
            "" if reports_dir.exists() else "请创建 reports 文件夹。",
        )
    )

    env_example_path = root / ".env.example"
    created = _ensure_file(env_example_path, DEFAULT_ENV_EXAMPLE) if auto_fix else False
    items.append(
        HealthItem(
            ".env.example",
            "OK" if not created else "WARN",
            ".env.example 存在。" if not created else ".env.example 缺失，已自动生成模板。",
        )
    )

    env_path = root / ".env"
    env_created = _ensure_file(env_path, DEFAULT_ENV) if auto_fix else False
    env_values = _read_env(env_path)
    if env_path.exists():
        items.append(
            HealthItem(
                ".env文件",
                "OK" if not env_created else "WARN",
                ".env 存在。" if not env_created else f".env 不存在，已自动生成并默认 EMAIL_TO={DEFAULT_EMAIL_TO}。",
            )
        )
    else:
        items.append(HealthItem(".env文件", "WARN", ".env 不存在；邮件和 OpenAI 深度分析会跳过，但基础日报可运行。"))

    workflow_path = repo_root / ".github" / "workflows" / "daily.yml"
    if workflow_path.exists():
        items.append(HealthItem("GitHub Actions", "OK", ".github/workflows/daily.yml 存在。"))
    else:
        items.append(
            HealthItem(
                "GitHub Actions",
                "WARN",
                ".github/workflows/daily.yml 不存在；不影响本地运行。",
                "如需云端自动运行，请恢复 daily.yml。",
            )
        )

    email_keys = ["SMTP_USER", "SMTP_PASSWORD"]
    missing_email = [key for key in email_keys if not env_values.get(key) and not os.getenv(key)]
    if missing_email:
        items.append(
            HealthItem(
                "邮件配置",
                "WARN",
                "邮件配置不完整，将跳过发送：" + "、".join(missing_email),
                "需要邮件时，在 .env 或 GitHub Secrets 中补齐 SMTP 配置。",
            )
        )
    else:
        items.append(HealthItem("邮件配置", "OK", "邮件配置完整。"))

    legacy_config_path = root / "data" / "config.yaml"
    if _legacy_config_has_password(legacy_config_path):
        items.append(
            HealthItem(
                "旧版密码配置",
                "WARN",
                "data/config.yaml 中存在旧版 password 字段；当前 V12 不需要把真实密码保存在配置文件里。",
                "建议把授权码迁移到 .env 或 GitHub Secrets，然后清空 data/config.yaml 中的 password。",
            )
        )

    if env_values.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"):
        items.append(HealthItem("OpenAI API Key", "OK", "OPENAI_API_KEY 已配置。"))
    else:
        items.append(
            HealthItem(
                "OpenAI API Key",
                "WARN",
                "未配置 OPENAI_API_KEY，AI 深度分析会跳过，基础日报可运行。",
                "需要 AI 深度分析时，在 .env 或 GitHub Secrets 中添加 OPENAI_API_KEY。",
            )
        )

    status = _status_summary(items)
    return {
        "status": status,
        "items": items,
        "can_run": status != "ERROR",
        "project_root": root,
    }


def format_health_report(result: dict[str, object]) -> str:
    items = result.get("items", [])
    lines = [
        "Stone AI Investment Manager Pro V12 系统自检",
        f"总体状态：{result.get('status', 'UNKNOWN')}",
        "",
    ]
    for item in items:
        if not isinstance(item, HealthItem):
            continue
        lines.append(f"[{item.status}] {item.name}：{item.message}")
        if item.fix:
            lines.append(f"    修复建议：{item.fix}")
    return "\n".join(lines)
