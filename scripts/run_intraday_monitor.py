from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monitoring.intraday_monitor import IntradayMonitor, load_monitor_config  # noqa: E402
from src.monitoring.process_lock import (  # noqa: E402
    LOCK_HELD_EXIT_CODE,
    LockHeldError,
    MonitorProcessLock,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stone AI observation-only intraday monitor")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run one monitoring pass and exit")
    mode.add_argument("--watch", action="store_true", help="run continuously with market-aware intervals")
    parser.add_argument("--interval", type=float, help="override all configured watch intervals in seconds")
    parser.add_argument("--symbols", help="comma-separated configured symbols")
    parser.add_argument(
        "--no-alert-output",
        action="store_true",
        help="hide console alert lines while preserving SQLite and JSONL state",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="enable alert formatting but print only; never connect to SMTP",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "intraday_monitor.yaml",
        help="monitor configuration path",
    )
    args = parser.parse_args(argv)
    if args.interval is not None and args.interval <= 0:
        parser.error("--interval must be greater than zero")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_monitor_config(args.config)
    lock_config = config.get("process_lock") or {}
    lock_path = Path(lock_config.get("lock_file_path", "data/monitoring/intraday_monitor.lock"))
    if not lock_path.is_absolute():
        lock_path = ROOT / lock_path
    lock = MonitorProcessLock(
        lock_path,
        stale_timeout_seconds=int(lock_config.get("stale_lock_timeout_seconds", 7200)),
    )
    monitor: IntradayMonitor | None = None
    try:
        lock.acquire()
    except LockHeldError as exc:
        print(str(exc), file=sys.stderr)
        return LOCK_HELD_EXIT_CODE
    try:
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)
        monitor = IntradayMonitor(config, root=ROOT)
        if args.dry_run:
            monitor.enable_email_dry_run()
        if args.symbols:
            monitor.filter_symbols(set(args.symbols.split(",")))
        if args.once:
            monitor.run_once(print_table=True, show_alerts=not args.no_alert_output)
        else:
            monitor.watch(
                interval_override=args.interval,
                print_table=True,
                show_alerts=not args.no_alert_output,
            )
        return 0
    except KeyboardInterrupt:
        print("intraday monitor stopped gracefully")
        return 0
    finally:
        if monitor is not None:
            monitor.close()
        lock.release()


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    raise SystemExit(main())
