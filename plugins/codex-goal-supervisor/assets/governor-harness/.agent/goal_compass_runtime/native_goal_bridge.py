"""Bounded bridge to Codex app-server's native thread Goal lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 8.0
DISABLED_VALUES = {"0", "false", "off", "disabled", "no"}


class NativeGoalBridgeError(RuntimeError):
    pass


def objective_sha256(objective: str) -> str:
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


def infer_thread_id(explicit: str | None = None, event: dict[str, Any] | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    source = event if isinstance(event, dict) else {}
    for key in ("session_id", "sessionId", "thread_id", "threadId"):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def availability(
    *,
    thread_id: str | None = None,
    event: dict[str, Any] | None = None,
    executable: str | None = None,
) -> dict[str, Any]:
    setting = str(os.environ.get("GOAL_SUPERVISOR_NATIVE_GOAL_BRIDGE", "auto")).strip().lower()
    if setting in DISABLED_VALUES:
        return {"available": False, "reason": "bridge_disabled", "thread_id": None, "executable": None}
    resolved_thread = infer_thread_id(thread_id, event)
    resolved_executable = executable or os.environ.get("CODEX_EXECUTABLE") or shutil.which("codex")
    if not resolved_thread:
        return {"available": False, "reason": "codex_thread_id_unavailable", "thread_id": None, "executable": resolved_executable}
    if not resolved_executable:
        return {"available": False, "reason": "codex_executable_unavailable", "thread_id": resolved_thread, "executable": None}
    return {
        "available": True,
        "reason": None,
        "thread_id": resolved_thread,
        "executable": str(resolved_executable),
    }


class _AppServerSession:
    def __init__(self, command: list[str], timeout: float) -> None:
        self.command = command
        self.timeout = max(0.5, float(timeout))
        self.process: subprocess.Popen[str] | None = None
        self.stderr_file: Any = None
        self.messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.next_id = 1

    def __enter__(self) -> "_AppServerSession":
        self.stderr_file = tempfile.TemporaryFile(mode="w+b")
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": self.stderr_file,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(self.command, **kwargs)
        threading.Thread(target=self._read_stdout, name="goal-app-server-reader", daemon=True).start()
        self.request(
            "initialize",
            {"clientInfo": {"name": "codex-goal-supervisor", "title": "Codex Goal Supervisor", "version": "2"}},
        )
        self.notify("initialized", {})
        return self

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
        except BaseException as exc:  # reader failures are surfaced to the caller
            self.messages.put(exc)
        finally:
            self.messages.put(None)

    def _write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise NativeGoalBridgeError("Codex app-server exited before the Goal request was sent")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._write({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill_process_group()
                raise NativeGoalBridgeError(f"Codex app-server timed out during {method}")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                self._kill_process_group()
                raise NativeGoalBridgeError(f"Codex app-server timed out during {method}") from exc
            if isinstance(message, BaseException):
                raise NativeGoalBridgeError(f"Codex app-server output reader failed: {type(message).__name__}")
            if message is None:
                raise NativeGoalBridgeError(
                    f"Codex app-server exited during {method}: {self._stderr_tail()}"
                )
            if message.get("id") != request_id:
                continue
            if message.get("error") is not None:
                raise NativeGoalBridgeError(
                    f"Codex app-server rejected {method}: {json.dumps(message['error'], ensure_ascii=False)}"
                )
            result = message.get("result")
            if not isinstance(result, dict):
                raise NativeGoalBridgeError(f"Codex app-server returned an invalid {method} response")
            return result

    def _stderr_tail(self) -> str:
        if self.stderr_file is None:
            return "no diagnostics"
        try:
            self.stderr_file.flush()
            self.stderr_file.seek(0)
            return self.stderr_file.read().decode("utf-8", errors="replace")[-1200:] or "no diagnostics"
        except OSError:
            return "diagnostics unavailable"

    def _kill_process_group(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    def close(self) -> None:
        process = self.process
        if process is not None and process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                self._kill_process_group()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        if self.stderr_file is not None:
            self.stderr_file.close()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _app_server_command(executable: str | Path | list[str]) -> list[str]:
    if isinstance(executable, list):
        return [*executable, "app-server", "--stdio"]
    return [str(executable), "app-server", "--stdio"]


def replace_goal(
    objective: str,
    *,
    thread_id: str | None = None,
    event: dict[str, Any] | None = None,
    status: str = "active",
    token_budget: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    executable: str | Path | list[str] | None = None,
) -> dict[str, Any]:
    objective = str(objective or "")
    if not objective.strip():
        raise NativeGoalBridgeError("Native Goal objective cannot be empty")
    if len(objective) > 4000:
        raise NativeGoalBridgeError("Native Goal objective exceeds Codex's 4000-character limit")

    if isinstance(executable, list):
        resolved_thread = infer_thread_id(thread_id, event)
        if not resolved_thread:
            raise NativeGoalBridgeError("Codex thread id is unavailable")
        command = _app_server_command(executable)
    else:
        state = availability(thread_id=thread_id, event=event, executable=str(executable) if executable else None)
        if not state["available"]:
            raise NativeGoalBridgeError(str(state["reason"]))
        resolved_thread = str(state["thread_id"])
        command = _app_server_command(str(state["executable"]))

    with _AppServerSession(command, timeout) as session:
        before = session.request("thread/goal/get", {"threadId": resolved_thread}).get("goal")
        if isinstance(before, dict) and before.get("objective") == objective:
            params: dict[str, Any] = {"threadId": resolved_thread, "status": status}
            operation = "STATUS_REFRESHED" if before.get("status") != status else "ALREADY_SYNCED"
        else:
            params = {"threadId": resolved_thread, "objective": objective, "status": status}
            if token_budget is not None:
                params["tokenBudget"] = int(token_budget)
            operation = "REPLACED" if isinstance(before, dict) else "CREATED"
        session.request("thread/goal/set", params)
        after = session.request("thread/goal/get", {"threadId": resolved_thread}).get("goal")

    if not isinstance(after, dict):
        raise NativeGoalBridgeError("Native Goal was absent after thread/goal/set")
    if after.get("objective") != objective:
        raise NativeGoalBridgeError("Native Goal objective did not match after thread/goal/set")
    if after.get("status") != status:
        raise NativeGoalBridgeError("Native Goal status did not match after thread/goal/set")
    return {
        "ok": True,
        "status": "SYNCED",
        "operation": operation,
        "thread_id": resolved_thread,
        "objective_chars": len(objective),
        "objective_sha256": objective_sha256(objective),
        "previous": before if isinstance(before, dict) else None,
        "current": after,
        "verified": True,
    }
