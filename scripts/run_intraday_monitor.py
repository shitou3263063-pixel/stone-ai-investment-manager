from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monitoring.intraday_monitor import IntradayMonitor, load_monitor_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stone AI observation-only intraday monitor")
    parser.add_argument("--once", action="store_true", help="run one monitoring pass and exit")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "intraday_monitor.yaml",
        help="monitor configuration path",
    )
    args = parser.parse_args()
    if not args.once:
        parser.error("phase one supports only --once")
    return args


def main() -> int:
    args = parse_args()
    monitor = IntradayMonitor(load_monitor_config(args.config), root=ROOT)
    monitor.run_once(print_table=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
