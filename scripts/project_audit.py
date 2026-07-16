from __future__ import annotations

from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _files(pattern: str) -> list[str]:
    return sorted(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") for path in PROJECT_ROOT.rglob(pattern))


def build_project_audit() -> str:
    python_files = _files("*.py")
    main_files = [path for path in python_files if Path(path).name == "main.py"]
    workflow_files = _files("*.yml") + _files("*.yaml")
    report_files = [path for path in _files("*.md") if path.startswith("reports/")]
    test_files = [path for path in python_files if path.startswith("tests/")]
    legacy_entrypoints = [path for path in main_files if path != "main.py"]

    lines = [
        "# Stone AI Project Audit",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "- 正式版本：Stone AI Investment Manager Pro V12.7.1 Final Freeze",
        "- 唯一正式入口：`python main.py`",
        "",
        "## 1. 入口审计",
        "",
        "- 正式入口：根目录 `main.py`。",
        "- 核心业务逻辑：`src/app.py`。",
        "- 旧 `src/main.py` 与 `run.py` 已移入 `archive/`，不再被正式流程调用。",
        "",
        "## 2. 发现的历史入口",
    ]
    lines.extend([f"- `{path}`" for path in legacy_entrypoints] or ["- 无"])
    lines.extend(
        [
            "",
            "## 3. 正式数据流",
            "",
            "```mermaid",
            "flowchart TD",
            '  A["portfolio.csv / portfolio_master.yaml"] --> B["每日快照"]',
            '  C["FRED / Alpha Vantage / Finnhub / yfinance"] --> B',
            '  B --> D["Portfolio / Market / Risk / DCA / Rebalance"]',
            '  D --> E["V12.7.1 CIO + Smart Grid Decision Engine"]',
            '  E --> F["Consistency Validator"]',
            '  F --> G["decision.json"]',
            '  G --> H["Report Center"]',
            '  H --> I["today_action / daily / weekly / monthly / grid"]',
            '  H --> J["Gmail SMTP"]',
            "```",
            "",
            "## 4. 统一职责",
            "",
            "- Codex：抓取、清洗、资产台账、执行状态、规则校验和候选建议。",
            "- GPT/AI：CIO复核、风险判断、冲突仲裁和最终用户决策辅助。",
            "- 决策层：任何报告和邮件必须读取统一 `decision.json`，不得各说各话。",
            "",
            "## 5. DQS门槛",
            "",
            "- DQS >= 85：允许正常金额建议。",
            "- DQS 75-84：只允许金额区间和分批计划。",
            "- DQS 60-74：只允许方向性建议。",
            "- DQS < 60：禁止新增仓位建议。",
            "- blocking_errors 非空：停止执行单。",
            "",
            "## 6. 测试覆盖",
        ]
    )
    lines.extend([f"- `{path}`" for path in test_files] or ["- 暂无测试文件"])
    lines.extend(
        [
            "",
            "## 7. 文件概览",
            "",
            f"- Python文件数量：{len(python_files)}",
            f"- main.py文件：{', '.join(main_files)}",
            f"- workflow/config文件数量：{len(workflow_files)}",
            f"- report文件数量：{len(report_files)}",
        ]
    )
    return "\n".join(lines)


def write_project_audit(path: Path | None = None) -> Path:
    target = path or PROJECT_ROOT / "reports" / "project_audit.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_project_audit(), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(write_project_audit())
