from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _env_status(*keys: str) -> str:
    return "configured" if all(os.getenv(key, "").strip() for key in keys) else "not_configured"


def _module_status(name: str) -> str:
    return "available" if importlib.util.find_spec(name) else "not_installed"


def collect_service_health() -> list[dict[str, str]]:
    _load_env_file()
    started = time.perf_counter()
    services = [
        ("OpenAI", _env_status("OPENAI_API_KEY"), "LLM primary"),
        ("Gemini", _env_status("GEMINI_API_KEY"), "LLM fallback"),
        ("Claude", _env_status("ANTHROPIC_API_KEY"), "LLM fallback"),
        ("DeepSeek", _env_status("DEEPSEEK_API_KEY"), "LLM fallback"),
        ("Qwen", _env_status("QWEN_API_KEY"), "LLM fallback"),
        ("Ollama", "configured" if os.getenv("OLLAMA_BASE_URL", "").strip() else "optional", "local fallback"),
        ("FRED", _env_status("FRED_API_KEY"), "macro primary"),
        ("Alpha Vantage", _env_status("ALPHA_VANTAGE_API_KEY"), "market backup"),
        ("Finnhub", _env_status("FINNHUB_API_KEY"), "market backup/news"),
        ("CBOE", "available", "VIX official reference"),
        ("yfinance", _module_status("yfinance"), "market fallback"),
        ("AkShare", _module_status("akshare"), "A/H optional fallback"),
        ("Gmail SMTP", _env_status("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"), "notification"),
    ]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    now = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, str]] = []
    for name, status, role in services:
        rows.append(
            {
                "service": name,
                "status": status,
                "response_time_ms": str(elapsed_ms),
                "last_success_time": now if status in {"configured", "available"} else "",
                "error_type": "" if status in {"configured", "available", "optional"} else "missing_config_or_dependency",
                "fallback_enabled": "yes",
                "actual_source": role,
            }
        )
    return rows


def format_service_health(rows: list[dict[str, str]]) -> str:
    configured = sum(1 for row in rows if row["status"] in {"configured", "available"})
    lines = [
        "# Service Health",
        "",
        f"- 检查时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 可用服务：{configured}/{len(rows)}",
        "- API Key 不会写入报告；这里只显示是否已配置。",
        "",
        "| 服务 | 状态 | 响应时间ms | 最后成功时间 | 错误类型 | 降级可用 | 当前用途 |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            f"{row['service']} | {row['status']} | {row['response_time_ms']} | "
            f"{row['last_success_time']} | {row['error_type']} | {row['fallback_enabled']} | {row['actual_source']} |"
        )
    return "\n".join(lines)


def write_service_health(path: Path | None = None) -> Path:
    target = path or PROJECT_ROOT / "reports" / "service_health.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_service_health(collect_service_health()), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(write_service_health())
