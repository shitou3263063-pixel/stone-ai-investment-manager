from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.grid.long_term_v1.report_summary import load_grid_strategy_summary  # noqa: E402
from src.grid.long_term_v1.runtime import LongTermGridRuntime, load_grid_config  # noqa: E402
from src.monitoring.process_lock import (  # noqa: E402
    LOCK_HELD_EXIT_CODE,
    LockHeldError,
    MonitorProcessLock,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stone AI simulation-only long-term index grid strategy"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="evaluate one round")
    mode.add_argument("--watch", action="store_true", help="evaluate continuously")
    mode.add_argument("--summary", action="store_true", help="read the simulation ledger")
    parser.add_argument("--symbols", default="VOO,QQQ", help="VOO and/or QQQ")
    parser.add_argument("--interval", type=float, help="watch interval in seconds")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="format eligible grid emails without connecting to SMTP",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "long_term_grid.yaml",
    )
    args = parser.parse_args(argv)
    if args.interval is not None and args.interval <= 0:
        parser.error("--interval must be greater than zero")
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    unsupported = sorted(set(symbols) - {"VOO", "QQQ"})
    if unsupported:
        parser.error(f"unsupported symbols: {','.join(unsupported)}")
    if not symbols:
        parser.error("--symbols must contain VOO and/or QQQ")
    args.symbol_list = list(dict.fromkeys(symbols))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.summary:
        print(
            json.dumps(
                load_grid_strategy_summary(ROOT, config_path=args.config),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    config = load_grid_config(args.config)
    storage = config.get("storage") or {}
    lock_path = Path(
        storage.get("lock_path", "data/grid_strategy/long_term_grid_v1.lock")
    )
    if not lock_path.is_absolute():
        lock_path = ROOT / lock_path
    lock = MonitorProcessLock(lock_path, stale_timeout_seconds=7200)
    runtime: LongTermGridRuntime | None = None
    try:
        lock.acquire()
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return LOCK_HELD_EXIT_CODE
    try:
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)
        runtime = LongTermGridRuntime(config, root=ROOT)
        if args.dry_run:
            runtime.enable_email_dry_run()
        if args.once:
            runtime.run_once(args.symbol_list)
        else:
            interval = (
                args.interval
                if args.interval is not None
                else float((config.get("watch") or {}).get("interval_seconds", 60))
            )
            print(
                "Stone AI grid simulation started; "
                "SIMULATION_ONLY | NO_AUTOMATIC_TRADING | Ctrl+C to stop"
            )
            runtime.watch(args.symbol_list, interval_seconds=interval)
        return 0
    except KeyboardInterrupt:
        print("long-term grid simulation stopped gracefully")
        return 0
    finally:
        if runtime is not None:
            runtime.close()
        lock.release()


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    raise SystemExit(main())
