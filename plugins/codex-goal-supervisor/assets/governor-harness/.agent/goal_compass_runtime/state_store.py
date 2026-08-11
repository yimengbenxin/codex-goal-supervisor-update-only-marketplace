"""Atomic JSON state and single-writer locking for Goal Compass."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator


_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}


def _local_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, path)


def write_json_exclusive(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomically replace a JSONL projection with a bounded set of rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows os.kill(pid, 0) is not the POSIX no-op existence probe. Use
        # a query-only process handle so checking a live lock owner can never
        # signal or terminate that owner.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == error_access_denied
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ProcessLookupError):
        return False


@contextlib.contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout: float,
    stale_seconds: float,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    local_lock = _local_lock(path)
    if not local_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise RuntimeError("CURRENT_TICKET_BUSY: another Goal Compass lifecycle write is still active")
    token = {"pid": os.getpid(), "created_at": time.time(), "nonce": uuid.uuid4().hex}
    owner_path = path / "owner.json"
    acquired_directory = False
    try:
        while True:
            try:
                path.mkdir(mode=0o700)
                try:
                    write_json_exclusive(owner_path, token)
                except OSError:
                    try:
                        path.rmdir()
                    except OSError:
                        pass
                    raise
                acquired_directory = True
                break
            except FileExistsError:
                stale = False
                existing_owner = path / "owner.json" if path.is_dir() else path
                try:
                    if path.is_dir() and not existing_owner.exists():
                        # mkdir is the atomic acquisition point. A competing
                        # process can observe the directory before owner.json
                        # is installed; that is live initialization, not a dead
                        # owner.
                        age = max(0.0, time.time() - path.stat().st_mtime)
                        stale = age > stale_seconds
                    else:
                        owner = load_json(existing_owner, {})
                        owner_pid = int(owner.get("pid", 0) or 0)
                        age = max(0.0, time.time() - float(owner.get("created_at", 0) or 0))
                        stale = not process_alive(owner_pid) or age > stale_seconds
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    try:
                        stale = time.time() - path.stat().st_mtime > stale_seconds
                    except OSError:
                        stale = False
                if stale:
                    stale_path = path.with_name(f".{path.name}.stale-{uuid.uuid4().hex}")
                    try:
                        os.replace(path, stale_path)
                        if stale_path.is_dir():
                            stale_owner = stale_path / "owner.json"
                            stale_owner.unlink(missing_ok=True)
                            stale_path.rmdir()
                        else:
                            stale_path.unlink(missing_ok=True)
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("CURRENT_TICKET_BUSY: another Goal Compass lifecycle write is still active")
                time.sleep(0.02)
            except OSError:
                if acquired_directory:
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError("CURRENT_TICKET_BUSY: another Goal Compass lifecycle write is still active")
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                owner = load_json(owner_path, {})
                if owner.get("nonce") == token["nonce"]:
                    owner_path.unlink(missing_ok=True)
                    path.rmdir()
            except (OSError, json.JSONDecodeError):
                pass
    finally:
        local_lock.release()
