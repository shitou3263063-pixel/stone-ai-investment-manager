from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4


LOCK_HELD_EXIT_CODE = 73


class LockHeldError(RuntimeError):
    def __init__(self, owner: dict | None = None) -> None:
        self.owner = owner or {}
        pid = self.owner.get("pid", "unknown")
        super().__init__(f"an intraday monitor instance is already running (pid={pid})")


class MonitorProcessLock:
    def __init__(self, path: str | Path, *, stale_timeout_seconds: int = 7200) -> None:
        self.path = Path(path)
        self.stale_timeout_seconds = int(stale_timeout_seconds)
        self.instance_id = uuid4().hex
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            payload = {
                "pid": os.getpid(),
                "started_at": datetime.now(tz=timezone.utc).isoformat(),
                "instance_id": self.instance_id,
            }
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self._read_owner()
                if self._is_stale(owner):
                    self._remove_stale()
                    continue
                raise LockHeldError(owner) from None
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            self.acquired = True
            return
        raise LockHeldError(self._read_owner())

    def release(self) -> None:
        if not self.acquired:
            return
        owner = self._read_owner()
        if owner.get("instance_id") == self.instance_id:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def _read_owner(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _is_stale(self, owner: dict) -> bool:
        try:
            pid = int(owner.get("pid"))
        except (TypeError, ValueError):
            pid = -1
        if pid > 0:
            return not _pid_exists(pid)
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True
        return age > self.stale_timeout_seconds

    def _remove_stale(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "MonitorProcessLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_exists(pid: int) -> bool:
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
