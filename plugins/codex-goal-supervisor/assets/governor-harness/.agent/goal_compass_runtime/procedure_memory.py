"""Project-local procedural memory for verified repetitive operations.

The observer records only bounded, deterministic command evidence.  It never
turns prose into executable code and never injects stored procedures into the
main conversation.  Stable procedures are materialized under ``.agent`` so a
future task can run them without rediscovering setup instructions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from goal_compass_runtime.hook_rules import tool_failed
from goal_compass_runtime.state_store import load_json, utc_now_iso, write_json


SCHEMA_VERSION = 1
MAX_SESSIONS = 96
MAX_COMMANDS_PER_SESSION = 24
MAX_SUMMARIES = 96
MAX_COMMAND_LENGTH = 600
READY_SEQUENCE_OCCURRENCES = 2

_READ_ONLY_COMMANDS = {
    "cat", "find", "grep", "head", "less", "ls", "pwd", "rg", "sed", "tail", "tree", "wc",
}
_PROJECT_RUNNERS = {"bun", "cargo", "go", "just", "make", "npm", "pnpm", "uv", "yarn"}
_PYTHON_MODULES = {"flask", "http.server", "pytest", "unittest", "uvicorn"}
_SENSITIVE = re.compile(
    r"(?i)(?:authorization\s*:|bearer\s+[a-z0-9._~-]+|(?:api[_-]?key|password|passwd|secret|token)\s*=)"
)
_SHELL_CONTROL = re.compile(r"(?:\n|\r|&&|\|\||[;<>`]|\$\(|\$\{)")
_TRANSIENT = re.compile(r"(?i)(?:/tmp/|\\temp\\|mktemp|temporarydirectory|\.agent/runtime/)")
_SERVICE_WORDS = {
    "dev", "develop", "preview", "serve", "server", "start", "uvicorn", "gunicorn", "flask",
}


def _redact_excerpt(value: str) -> str:
    text = re.sub(
        r"(?i)((?:api[_-]?key|password|passwd|secret|token)\s*[=:]\s*)([^\s,;]+)",
        r"\1<redacted>",
        value,
    )
    try:
        home = str(Path.home())
        if home:
            text = text.replace(home, "${HOME}")
    except OSError:
        pass
    return re.sub(r"\s+", " ", text).strip()[:600]


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sessions": {},
        "sequences": {},
        "procedures": {},
        "thread_summaries": [],
        "updated_at": None,
    }


def _session_id(event: dict[str, Any]) -> str:
    raw = str(event.get("session_id") or event.get("sessionId") or "unknown").strip() or "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _command_text(event: dict[str, Any]) -> str:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    if not isinstance(value, dict):
        return ""
    return str(value.get("command") or value.get("cmd") or "").strip()


def _canonical_executable(value: str) -> str:
    base = os.path.basename(value.replace("\\", "/")).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", base):
        return "{python}"
    return base


def _safe_relative_argument(value: str, project_root: Path) -> str | None:
    if not os.path.isabs(value):
        return value.replace("\\", "/")
    try:
        relative = Path(value).resolve().relative_to(project_root.resolve())
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def normalize_command(command: str, project_root: Path) -> dict[str, Any] | None:
    """Return a safe reusable command contract, or None for unsafe/noisy input."""
    value = command.strip()
    if not value or len(value) > MAX_COMMAND_LENGTH:
        return None
    if _SENSITIVE.search(value) or _SHELL_CONTROL.search(value) or _TRANSIENT.search(value):
        return None
    try:
        tokens = shlex.split(value, posix=os.name != "nt")
    except ValueError:
        return None
    if not tokens:
        return None
    executable = _canonical_executable(tokens[0])
    if executable in _READ_ONLY_COMMANDS:
        return None

    allowed = executable in _PROJECT_RUNNERS or executable == "{python}" or executable in {
        "flask", "gunicorn", "pytest", "uvicorn",
    }
    if not allowed:
        return None

    normalized: list[str] = [executable]
    for token in tokens[1:]:
        item = _safe_relative_argument(str(token), project_root)
        if item is None:
            return None
        normalized.append(item)

    if executable == "{python}":
        if len(normalized) >= 3 and normalized[1] == "-m":
            if normalized[2] not in _PYTHON_MODULES:
                return None
        elif len(normalized) >= 2:
            script = normalized[1]
            if script.startswith("-") or not script.lower().endswith(".py"):
                return None
        else:
            return None

    lower = [token.lower() for token in normalized]
    is_service = executable in {"flask", "gunicorn", "uvicorn"}
    if executable == "{python}" and len(lower) >= 3 and lower[1] == "-m":
        is_service = lower[2] in {"flask", "http.server", "uvicorn"}
    elif executable == "{python}" and len(lower) >= 2:
        stem = Path(lower[1]).stem
        is_service = stem in {
            "dev_server", "devserver", "run_server", "runserver",
            "server", "serve", "start_server", "startserver",
        }
    elif executable in {"npm", "pnpm", "yarn", "bun"}:
        if len(lower) >= 3 and lower[1] == "run":
            is_service = lower[2] in _SERVICE_WORDS
        elif len(lower) >= 2:
            is_service = lower[1] in {"dev", "preview", "serve", "start"}
    elif executable in {"make", "just"} and len(lower) >= 2:
        is_service = lower[1] in _SERVICE_WORDS
    elif executable == "uv":
        is_service = any(token in {"flask", "gunicorn", "uvicorn"} for token in lower[1:])

    fingerprint = hashlib.sha256(json.dumps(normalized, ensure_ascii=True).encode("utf-8")).hexdigest()[:20]
    return {
        "fingerprint": fingerprint,
        "argv": normalized,
        "kind": "LOCAL_SERVICE" if is_service else "DETERMINISTIC_COMMAND",
        "display": shlex.join(normalized),
    }


def _procedure_id(kind: str, fingerprint: str) -> str:
    prefix = "service" if kind == "LOCAL_SERVICE" else "procedure"
    return f"{prefix}-{fingerprint[:12]}"


def _skill_description(contract: dict[str, Any]) -> str:
    action = "start or inspect the previously verified local service" if contract["kind"] == "LOCAL_SERVICE" else "repeat the previously verified project command sequence"
    return f"Use when Codex needs to {action} without rediscovering commands or rereading setup files."


def _runner_source(contract: dict[str, Any]) -> str:
    payload = json.dumps({
        "procedure_id": contract["procedure_id"],
        "kind": contract["kind"],
        "commands": contract["commands"],
    }, ensure_ascii=False, indent=2)
    return f'''#!/usr/bin/env python3
"""Generated by Codex Goal Supervisor from verified local command evidence."""
from __future__ import annotations
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CONTRACT = json.loads({json.dumps(payload)})
ROOT = Path(__file__).resolve().parents[4]
RUNTIME = ROOT / ".agent" / "runtime" / "procedure_services"

def argv(command):
    values = list(command["argv"])
    if values and values[0] == "{{python}}":
        values[0] = sys.executable
    return values

def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False

def service(action):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    state_path = RUNTIME / (CONTRACT["procedure_id"] + ".json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {{}}
    pid = state.get("pid")
    if action == "status":
        print(json.dumps({{"status": "RUNNING" if alive(pid) else "STOPPED", "pid": pid}}))
        return 0 if alive(pid) else 1
    if action == "stop":
        if alive(pid):
            os.kill(int(pid), signal.SIGTERM)
        state_path.unlink(missing_ok=True)
        print(json.dumps({{"status": "STOPPED", "pid": pid}}))
        return 0
    if alive(pid):
        print(json.dumps({{"status": "ALREADY_RUNNING", "pid": pid}}))
        return 0
    log_path = RUNTIME / (CONTRACT["procedure_id"] + ".log")
    log = log_path.open("ab")
    kwargs = {{"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT}}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(argv(CONTRACT["commands"][0]), **kwargs)
    time.sleep(0.12)
    if process.poll() is not None:
        print(json.dumps({{"status": "START_FAILED", "exit_code": process.returncode, "log": str(log_path)}}))
        return process.returncode or 1
    state_path.write_text(json.dumps({{"pid": process.pid, "log": str(log_path)}}, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({{"status": "STARTED", "pid": process.pid, "log": str(log_path)}}))
    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", choices=["run", "start", "status", "stop"], default="run")
    args = parser.parse_args()
    if CONTRACT["kind"] == "LOCAL_SERVICE":
        return service("start" if args.action == "run" else args.action)
    if args.action != "run":
        parser.error("non-service procedures support only run")
    for command in CONTRACT["commands"]:
        result = subprocess.run(argv(command), cwd=str(ROOT))
        if result.returncode:
            return result.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


def materialize(project_root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    procedure_id = str(contract["procedure_id"])
    target = project_root / ".agent" / "procedures" / procedure_id
    scripts = target / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    procedure_path = target / "procedure.json"
    skill_path = target / "SKILL.md"
    runner_path = scripts / "run.py"
    write_json(procedure_path, contract)
    skill_path.write_text(
        "---\n"
        f"name: {procedure_id}\n"
        f"description: {_skill_description(contract)}\n"
        "---\n\n"
        "# Verified project procedure\n\n"
        "Use the bundled runner instead of rediscovering startup or execution steps.\n\n"
        f"- Evidence status: `{contract['status']}`\n"
        f"- Kind: `{contract['kind']}`\n"
        f"- Run: `{{python}} .agent/procedures/{procedure_id}/scripts/run.py"
        + (" start`\n" if contract["kind"] == "LOCAL_SERVICE" else " run`\n")
        + (f"- Status: `{{python}} .agent/procedures/{procedure_id}/scripts/run.py status`\n"
           f"- Stop: `{{python}} .agent/procedures/{procedure_id}/scripts/run.py stop`\n"
           if contract["kind"] == "LOCAL_SERVICE" else "")
        + "\nDo not edit this generated procedure from conversational memory. Re-derive it only from new verified command evidence.\n",
        encoding="utf-8",
    )
    runner_path.write_text(_runner_source(contract), encoding="utf-8")
    try:
        runner_path.chmod(0o755)
    except OSError:
        pass
    return {
        "procedure_id": procedure_id,
        "status": contract["status"],
        "kind": contract["kind"],
        "skill_path": str(skill_path.relative_to(project_root)),
        "runner_path": str(runner_path.relative_to(project_root)),
    }


def _write_index(project_root: Path, state: dict[str, Any]) -> None:
    procedures = [
        value for value in state.get("procedures", {}).values()
        if isinstance(value, dict) and value.get("status") == "READY"
    ]
    rows = [
        {
            key: procedure.get(key)
            for key in ("procedure_id", "kind", "status", "display", "skill_path", "runner_path", "verified_sessions", "updated_at")
        }
        for procedure in sorted(procedures, key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    ]
    write_json(project_root / ".agent" / "procedures" / "index.json", {
        "schema_version": SCHEMA_VERSION,
        "procedures": rows,
        "usage": "Read this compact index before rediscovering a repeated project operation; load only the matching SKILL.md.",
    })


def record_successful_command(project_root: Path, state_path: Path, event: dict[str, Any]) -> dict[str, Any] | None:
    if tool_failed(event):
        return None
    normalized = normalize_command(_command_text(event), project_root)
    if not normalized:
        return None
    state = load_json(state_path, empty_state())
    session_id = _session_id(event)
    session = state.setdefault("sessions", {}).setdefault(session_id, {
        "commands": [], "first_seen_at": utc_now_iso(), "last_seen_at": None,
    })
    commands = session.setdefault("commands", [])
    if not commands or commands[-1].get("fingerprint") != normalized["fingerprint"]:
        commands.append(normalized)
        del commands[:-MAX_COMMANDS_PER_SESSION]
    session["last_seen_at"] = utc_now_iso()
    state["sessions"] = dict(list(state["sessions"].items())[-MAX_SESSIONS:])

    promoted = None
    if normalized["kind"] == "LOCAL_SERVICE":
        procedure_id = _procedure_id(normalized["kind"], normalized["fingerprint"])
        contract = {
            "schema_version": SCHEMA_VERSION,
            "procedure_id": procedure_id,
            "kind": normalized["kind"],
            "status": "READY",
            "commands": [normalized],
            "display": normalized["display"],
            "verified_sessions": [session_id],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        previous = state.setdefault("procedures", {}).get(procedure_id)
        if isinstance(previous, dict):
            contract["created_at"] = previous.get("created_at") or contract["created_at"]
            contract["verified_sessions"] = sorted(set(previous.get("verified_sessions", [])) | {session_id})
        promoted = materialize(project_root, contract)
        contract.update(promoted)
        state["procedures"][procedure_id] = contract
        _write_index(project_root, state)
    state["updated_at"] = utc_now_iso()
    write_json(state_path, state)
    return promoted


def finalize_thread(project_root: Path, state_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    state = load_json(state_path, empty_state())
    session_id = _session_id(event)
    session = state.setdefault("sessions", {}).get(session_id, {})
    commands = [row for row in session.get("commands", []) if isinstance(row, dict)]
    sequence_fingerprints = [str(row.get("fingerprint")) for row in commands]
    sequence_id = hashlib.sha256(json.dumps(sequence_fingerprints).encode("utf-8")).hexdigest()[:20] if commands else None
    promoted: list[str] = []

    if sequence_id and any(row.get("kind") != "LOCAL_SERVICE" for row in commands):
        sequence = state.setdefault("sequences", {}).setdefault(sequence_id, {
            "commands": commands,
            "sessions": [],
            "first_seen_at": utc_now_iso(),
        })
        sequence["sessions"] = sorted(set(sequence.get("sessions", [])) | {session_id})
        sequence["last_seen_at"] = utc_now_iso()
        if len(sequence["sessions"]) >= READY_SEQUENCE_OCCURRENCES:
            procedure_id = _procedure_id("DETERMINISTIC_COMMAND", sequence_id)
            contract = {
                "schema_version": SCHEMA_VERSION,
                "procedure_id": procedure_id,
                "kind": "DETERMINISTIC_COMMAND",
                "status": "READY",
                "commands": commands,
                "display": " -> ".join(str(row.get("display") or "") for row in commands),
                "verified_sessions": sequence["sessions"],
                "created_at": sequence.get("first_seen_at"),
                "updated_at": utc_now_iso(),
            }
            generated = materialize(project_root, contract)
            contract.update(generated)
            state.setdefault("procedures", {})[procedure_id] = contract
            promoted.append(procedure_id)

    message = str(event.get("last_assistant_message") or event.get("lastAssistantMessage") or "").strip()
    summary = {
        "session_id": session_id,
        "closed_at": utc_now_iso(),
        "command_count": len(commands),
        "sequence_id": sequence_id,
        "promoted_procedures": promoted,
        "outcome_excerpt": _redact_excerpt(message),
    }
    summaries = state.setdefault("thread_summaries", [])
    summaries[:] = [row for row in summaries if not isinstance(row, dict) or row.get("session_id") != session_id]
    summaries.append(summary)
    del summaries[:-MAX_SUMMARIES]
    state["updated_at"] = utc_now_iso()
    _write_index(project_root, state)
    write_json(state_path, state)
    return summary


def compact_status(project_root: Path, state_path: Path) -> dict[str, Any]:
    state = load_json(state_path, empty_state())
    ready = [row for row in state.get("procedures", {}).values() if isinstance(row, dict) and row.get("status") == "READY"]
    return {
        "ready_count": len(ready),
        "candidate_sequence_count": sum(
            1 for row in state.get("sequences", {}).values()
            if isinstance(row, dict) and len(row.get("sessions", [])) < READY_SEQUENCE_OCCURRENCES
        ),
        "index_path": ".agent/procedures/index.json",
    }
