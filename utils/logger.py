from __future__ import annotations

from datetime import datetime
from pathlib import Path

from utils.data_loader import project_root


def write_log(message: str, filename: str = "market_data.log") -> None:
    """写入简单文本日志。"""

    log_dir = project_root() / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = log_dir / filename
    with path.open("a", encoding="utf-8", errors="replace") as file:
        file.write(f"[{timestamp}] {message}\n")
