"""Bounded read continuity for long Codex turns.

The runtime stores only file metadata and a small mechanical capsule. It never
copies source text into Goal Supervisor state. Semantic synthesis remains an
agent task and is requested only after a genuinely broad read phase.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path
from typing import Any

from goal_compass_runtime.state_store import exclusive_file_lock, load_json, utc_now_iso, write_json


SCHEMA_VERSION = 2
MAX_TRACKED_FILES = 128
LARGE_READ_FILES = 16
LARGE_OUTPUT_BYTES = 512 * 1024
LARGE_BROAD_READS = 3
BROAD_READ_MIN_OUTPUT_BYTES = 64 * 1024
SUBAGENT_MIN_FILES = 8
SUBAGENT_MIN_DIRECTORIES = 2
MAX_RESPONSE_MEASURE = 2 * 1024 * 1024
MAX_CHECKPOINT_ITEMS = 50
MAX_CHECKPOINT_ITEM_CHARS = 1000
MAX_EVIDENCE_FILES = 64

PATH_KEYS = {
    "path",
    "paths",
    "file",
    "files",
    "filename",
    "file_path",
    "filepath",
    "relative_path",
    "relative_paths",
    "directory",
    "root",
}
READ_COMMANDS = {
    "cat",
    "sed",
    "head",
    "tail",
    "nl",
    "less",
    "more",
    "wc",
    "rg",
    "grep",
    "find",
}

SERENA_READ_TOOLS = {
    "find_symbol",
    "find_referencing_symbols",
    "get_symbols_overview",
    "search_for_pattern",
    "read_file",
    "find_file",
    "list_dir",
}

FASTCTX_READ_TERMS = {
    "read",
    "grep",
    "glob",
    "search",
    "list",
    "stat",
}

BOUNDED_INPUT_KEYS = {
    "offset",
    "limit",
    "start_line",
    "end_line",
    "line_start",
    "line_end",
    "page",
    "page_size",
    "cursor",
    "max_bytes",
    "max_chars",
    "max_results",
    "depth",
}


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": None,
        "turn_id": None,
        "read_calls": 0,
        "broad_read_calls": 0,
        "bounded_read_calls": 0,
        "large_output_calls": 0,
        "unbounded_large_output_calls": 0,
        "repeat_read_calls": 0,
        "unique_files": 0,
        "unique_file_bytes": 0,
        "observed_output_bytes": 0,
        "reader_kinds": [],
        "files": [],
        "checkpoint_due": False,
        "checkpoint_due_reason": None,
        "subagent_recommended": False,
        "partitionable_directories": [],
        "recommendation_emitted": False,
        "recommendation_emitted_at": None,
        "last_compact_at": None,
        "last_recovered_at": None,
        "last_event_at": None,
    }


def _norm_relative(project_root: Path, value: str) -> str | None:
    raw = value.strip().strip("'\"")
    if not raw or "\n" in raw or "://" in raw or raw.startswith("-"):
        return None
    path = Path(os.path.expanduser(raw))
    candidate = path if path.is_absolute() else project_root / path
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(project_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    if relative.startswith(".agent/") or relative.startswith(".codex/"):
        return None
    return relative


def _path_values(value: Any, *, key: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            normalized = str(child_key).lower()
            if normalized in PATH_KEYS:
                out.extend(_path_values(child, key=normalized))
            elif isinstance(child, (dict, list)):
                out.extend(_path_values(child, key=normalized))
    elif isinstance(value, list):
        for child in value:
            out.extend(_path_values(child, key=key))
    elif isinstance(value, str) and key in PATH_KEYS:
        out.append(value)
    return out


def _command(event: dict[str, Any]) -> str:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    if not isinstance(value, dict):
        return ""
    return str(value.get("command") or value.get("cmd") or "")


def _tool_name(event: dict[str, Any]) -> str:
    return str(event.get("tool_name") or event.get("toolName") or "").lower()


def _tool_input(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    return value if isinstance(value, dict) else {}


def reader_kind(event: dict[str, Any]) -> str:
    """Classify an observed reader without requiring the external tool."""
    tool = _tool_name(event)
    command = _command(event).lower()
    semantic_serena_tools = SERENA_READ_TOOLS - {"read_file", "find_file", "list_dir", "search_for_pattern"}
    if "serena" in tool or tool in semantic_serena_tools:
        return "serena"
    if "fastctx" in tool or "fastctx" in command:
        return "fastctx"
    if command:
        return "shell"
    return "builtin"


def _input_is_bounded(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in BOUNDED_INPUT_KEYS and child is not None and child != "" and child != 0 and child is not False:
                return True
            if isinstance(child, (dict, list)) and _input_is_bounded(child):
                return True
    elif isinstance(value, list):
        return any(_input_is_bounded(child) for child in value)
    return False


def bounded_read(event: dict[str, Any]) -> bool:
    kind = reader_kind(event)
    tool = _tool_name(event)
    if kind == "serena" and any(tool.endswith(term) for term in SERENA_READ_TOOLS - {"read_file"}):
        return True
    return _input_is_bounded(_tool_input(event))


def is_read_event(event: dict[str, Any]) -> bool:
    """Recognize built-in, shell, Serena, and FastCtx read operations."""
    tool = _tool_name(event)
    command = _command(event)
    if "serena" in tool:
        return any(tool.endswith(term) for term in SERENA_READ_TOOLS)
    if "fastctx" in tool:
        return any(term in tool for term in FASTCTX_READ_TERMS)
    if command:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        commands = {Path(token).name.lower() for token in tokens if token and not token.startswith("-")}
        return bool(commands & READ_COMMANDS) and not any(
            marker in command for marker in (">", "tee ", "rm ", "mv ", "cp ", "sed -i")
        )
    if any(marker in tool for marker in ("write", "edit", "patch", "delete", "remove", "move", "create", "update")):
        return False
    return any(marker in tool for marker in ("read", "search", "grep", "glob", "find", "list", "symbol", "reference", "overview"))


def _command_paths(project_root: Path, command: str) -> tuple[list[str], bool]:
    if not command:
        return [], False
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    commands = {Path(token).name.lower() for token in tokens if token and not token.startswith("-")}
    is_read_command = bool(commands & READ_COMMANDS)
    broad = False
    if not is_read_command:
        return [], False
    out: list[str] = []
    for token in tokens:
        cleaned = token.strip("'\"(),")
        relative = _norm_relative(project_root, cleaned)
        if relative is None:
            continue
        candidate = project_root / relative
        if candidate.is_file():
            out.append(relative)
        elif candidate.is_dir() and relative not in {".", ""}:
            broad = True
    if commands & {"rg", "grep", "find"} and not out:
        broad = True
    return list(dict.fromkeys(out)), broad


def read_paths(project_root: Path, event: dict[str, Any]) -> tuple[list[str], bool]:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    explicit = []
    for raw in _path_values(value):
        relative = _norm_relative(project_root, raw)
        if relative and (project_root / relative).is_file():
            explicit.append(relative)
    command_paths, broad = _command_paths(project_root, _command(event))
    return list(dict.fromkeys([*explicit, *command_paths])), broad


def _partitionable_directories(files: list[dict[str, Any]]) -> list[str]:
    directories = {
        Path(str(row.get("path"))).parent.as_posix()
        for row in files
        if isinstance(row, dict) and row.get("path")
    }
    return sorted(directory for directory in directories if directory not in {"", "."})


def _bounded_size(value: Any, limit: int = MAX_RESPONSE_MEASURE) -> int:
    """Estimate model-facing response size without copying a large payload."""
    total = 0
    stack = [value]
    while stack and total < limit:
        item = stack.pop()
        if isinstance(item, str):
            total += min(len(item.encode("utf-8", errors="replace")), limit - total)
        elif isinstance(item, bytes):
            total += min(len(item), limit - total)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif item is not None:
            total += min(len(str(item)), limit - total)
    return total


def _fingerprint(path: Path) -> tuple[str, int]:
    try:
        stat = path.stat()
    except OSError:
        return "unavailable", 0
    raw = f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16], int(stat.st_size)


def _semantic_checkpoint_present(capsule: dict[str, Any]) -> bool:
    semantic = capsule.get("semantic_checkpoint")
    if not isinstance(semantic, dict):
        return False
    return any(bool(semantic.get(key)) for key in (
        "confirmed_facts",
        "key_interfaces",
        "dependencies",
        "open_questions",
        "next_action",
    ))


def _bounded_checkpoint_items(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        cleaned = " ".join(str(value).split()).strip()
        if not cleaned:
            continue
        cleaned = cleaned[:MAX_CHECKPOINT_ITEM_CHARS]
        if cleaned not in out:
            out.append(cleaned)
        if len(out) >= MAX_CHECKPOINT_ITEMS:
            break
    return out


def _merge_checkpoint_items(existing: Any, incoming: list[str] | None) -> list[str]:
    previous = [str(value) for value in existing] if isinstance(existing, list) else []
    return _bounded_checkpoint_items([*previous, *(incoming or [])])


def _checkpoint_directory(project_root: Path, value: str) -> str:
    relative = _norm_relative(project_root, value)
    if relative is None:
        raise ValueError("context directory must stay inside the project and outside .agent/.codex")
    target = project_root if relative in {"", "."} else project_root / relative
    if not target.is_dir():
        raise ValueError(f"context directory does not exist: {value}")
    return "." if relative in {"", "."} else relative


def _checkpoint_evidence(project_root: Path, values: list[str] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values or []:
        relative = _norm_relative(project_root, str(raw))
        if relative is None or relative in seen:
            continue
        target = project_root / relative
        if not target.is_file():
            raise ValueError(f"context evidence is not a project file: {raw}")
        fingerprint, size = _fingerprint(target)
        rows.append({"path": relative, "fingerprint": fingerprint, "size": size})
        seen.add(relative)
        if len(rows) >= MAX_EVIDENCE_FILES:
            break
    return rows


def _merge_evidence(existing: Any, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if isinstance(existing, list):
        for row in existing:
            if isinstance(row, dict) and row.get("path"):
                rows[str(row["path"])] = dict(row)
    for row in incoming:
        rows[str(row["path"])] = dict(row)
    return list(rows.values())[-MAX_EVIDENCE_FILES:]


def _stale_evidence(project_root: Path, semantic: Any) -> list[str]:
    if not isinstance(semantic, dict):
        return []
    stale: list[str] = []
    evidence = semantic.get("evidence") if isinstance(semantic.get("evidence"), list) else []
    for row in evidence[:MAX_EVIDENCE_FILES]:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        relative = str(row["path"])
        current, _ = _fingerprint(project_root / relative)
        if current != str(row.get("fingerprint") or ""):
            stale.append(relative)
    return stale


def _semantic_status(project_root: Path, capsule: dict[str, Any]) -> tuple[str, list[str]]:
    if not _semantic_checkpoint_present(capsule):
        return "MISSING", []
    stale = _stale_evidence(project_root, capsule.get("semantic_checkpoint"))
    return ("STALE", stale) if stale else ("CURRENT", [])


def _mechanical_capsule(state: dict[str, Any], capsule: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = dict(capsule or {})
    semantic = previous.get("semantic_checkpoint")
    if not isinstance(semantic, dict):
        semantic = {
            "updated_at": None,
            "confirmed_facts": [],
            "key_interfaces": [],
            "dependencies": [],
            "open_questions": [],
            "next_action": "",
        }
    status = "SEMANTIC_CHECKPOINT_PRESENT" if _semantic_checkpoint_present(previous) else "MECHANICAL_LEDGER_ONLY"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "updated_at": utc_now_iso(),
        "session_id": state.get("session_id"),
        "turn_id": state.get("turn_id"),
        "read_summary": {
            "read_calls": state.get("read_calls", 0),
            "broad_read_calls": state.get("broad_read_calls", 0),
            "bounded_read_calls": state.get("bounded_read_calls", 0),
            "large_output_calls": state.get("large_output_calls", 0),
            "unbounded_large_output_calls": state.get("unbounded_large_output_calls", 0),
            "repeat_read_calls": state.get("repeat_read_calls", 0),
            "unique_files": state.get("unique_files", 0),
            "unique_file_bytes": state.get("unique_file_bytes", 0),
            "observed_output_bytes": state.get("observed_output_bytes", 0),
            "reader_kinds": state.get("reader_kinds", []),
            "checkpoint_due_reason": state.get("checkpoint_due_reason"),
            "partitionable_directories": state.get("partitionable_directories", []),
        },
        "directory_index": [],
        "semantic_checkpoint": semantic,
    }


def _directory_capsule_path(index_path: Path, directory: str) -> Path:
    relative = Path("_root") if directory in {"", "."} else Path(directory)
    return index_path.parent / "by-directory" / relative / "_context.json"


def _project_relative(project_root: Path, path: Path) -> str:
    candidate = path if path.is_absolute() else project_root / path
    return candidate.resolve().relative_to(project_root.resolve()).as_posix()


def _write_hierarchical_capsules(project_root: Path, state: dict[str, Any], index_path: Path) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in state.get("files", []):
        if not isinstance(row, dict) or not row.get("path"):
            continue
        directory = Path(str(row["path"])).parent.as_posix()
        grouped.setdefault(directory, []).append(dict(row))

    index = _mechanical_capsule(state, load_json(index_path, {}))
    directory_index: list[dict[str, Any]] = []
    for directory in sorted(grouped):
        rows = sorted(grouped[directory], key=lambda row: str(row.get("path") or ""))
        target = _directory_capsule_path(index_path, directory)
        existing = load_json(target, {})
        semantic = existing.get("semantic_checkpoint")
        if not isinstance(semantic, dict):
            semantic = {
                "updated_at": None,
                "confirmed_facts": [],
                "key_interfaces": [],
                "dependencies": [],
                "open_questions": [],
                "next_action": "",
            }
        semantic_status, stale_evidence = _semantic_status(project_root, {"semantic_checkpoint": semantic})
        payload = {
            "schema_version": SCHEMA_VERSION,
            "directory": directory,
            "updated_at": utc_now_iso(),
            "file_count": len(rows),
            "total_bytes": sum(int(row.get("size", 0) or 0) for row in rows),
            "files": rows,
            "semantic_checkpoint": semantic,
            "semantic_checkpoint_status": semantic_status,
            "stale_evidence": stale_evidence,
        }
        write_json(target, payload)
        directory_index.append({
            "directory": directory,
            "file_count": payload["file_count"],
            "total_bytes": payload["total_bytes"],
            "capsule": _project_relative(project_root, target),
            "semantic_checkpoint_present": semantic_status != "MISSING",
            "semantic_checkpoint_status": semantic_status,
        })
    index["directory_index"] = directory_index
    write_json(index_path, index)


def record_semantic_checkpoint(
    project_root: Path,
    state_path: Path,
    lock_path: Path,
    index_path: Path,
    *,
    directory: str,
    confirmed_facts: list[str] | None = None,
    key_interfaces: list[str] | None = None,
    dependencies: list[str] | None = None,
    open_questions: list[str] | None = None,
    next_action: str | None = None,
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Persist an explicit conclusion checkpoint without source or hidden reasoning.

    The execution agent calls this only after it has formed a useful conclusion.
    The hook never synthesizes these fields and never injects them into a prompt.
    """
    normalized_directory = _checkpoint_directory(project_root, directory)
    incoming = {
        "confirmed_facts": _bounded_checkpoint_items(confirmed_facts),
        "key_interfaces": _bounded_checkpoint_items(key_interfaces),
        "dependencies": _bounded_checkpoint_items(dependencies),
        "open_questions": _bounded_checkpoint_items(open_questions),
    }
    next_action_value = " ".join(str(next_action or "").split()).strip()[:MAX_CHECKPOINT_ITEM_CHARS]
    evidence = _checkpoint_evidence(project_root, evidence_paths)
    if not any(incoming.values()) and not next_action_value and not evidence:
        raise ValueError("context checkpoint requires at least one conclusion or evidence file")

    now = utc_now_iso()
    target = _directory_capsule_path(index_path, normalized_directory)
    try:
        with exclusive_file_lock(lock_path, timeout=0.5, stale_seconds=30.0):
            state = load_json(state_path, empty_state())
            existing = load_json(target, {})
            semantic = existing.get("semantic_checkpoint")
            if not isinstance(semantic, dict):
                semantic = {}
            semantic = {
                "updated_at": now,
                "confirmed_facts": _merge_checkpoint_items(semantic.get("confirmed_facts"), incoming["confirmed_facts"]),
                "key_interfaces": _merge_checkpoint_items(semantic.get("key_interfaces"), incoming["key_interfaces"]),
                "dependencies": _merge_checkpoint_items(semantic.get("dependencies"), incoming["dependencies"]),
                "open_questions": _merge_checkpoint_items(semantic.get("open_questions"), incoming["open_questions"]),
                "next_action": next_action_value or str(semantic.get("next_action") or ""),
                "evidence": _merge_evidence(semantic.get("evidence"), evidence),
            }
            files = existing.get("files") if isinstance(existing.get("files"), list) else []
            payload = {
                "schema_version": SCHEMA_VERSION,
                "directory": normalized_directory,
                "updated_at": now,
                "file_count": len(files),
                "total_bytes": sum(int(row.get("size", 0) or 0) for row in files if isinstance(row, dict)),
                "files": files,
                "semantic_checkpoint": semantic,
                "semantic_checkpoint_status": "CURRENT",
                "stale_evidence": [],
            }
            write_json(target, payload)

            index = _mechanical_capsule(state, load_json(index_path, {}))
            entries = {
                str(row.get("directory")): dict(row)
                for row in index.get("directory_index", [])
                if isinstance(row, dict) and row.get("directory")
            }
            entries[normalized_directory] = {
                "directory": normalized_directory,
                "file_count": payload["file_count"],
                "total_bytes": payload["total_bytes"],
                "capsule": _project_relative(project_root, target),
                "semantic_checkpoint_present": True,
                "semantic_checkpoint_status": "CURRENT",
            }
            index["directory_index"] = [entries[key] for key in sorted(entries)]
            index["status"] = "SEMANTIC_CHECKPOINT_PRESENT"
            index["updated_at"] = now
            write_json(index_path, index)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to write context checkpoint: {exc}") from exc

    return {
        "status": "RECORDED",
        "directory": normalized_directory,
        "capsule": _project_relative(project_root, target),
        "counts": {
            "confirmed_facts": len(semantic["confirmed_facts"]),
            "key_interfaces": len(semantic["key_interfaces"]),
            "dependencies": len(semantic["dependencies"]),
            "open_questions": len(semantic["open_questions"]),
            "evidence": len(semantic["evidence"]),
        },
        "next_action_present": bool(semantic["next_action"]),
        "source_text_stored": False,
        "hidden_reasoning_stored": False,
        "proactive_injection": False,
    }


def _new_session(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    session_id = str(event.get("session_id") or event.get("sessionId") or "") or None
    if session_id is None:
        return state
    if state.get("session_id") in {None, session_id}:
        state["session_id"] = session_id
        return state
    fresh = empty_state()
    fresh["session_id"] = session_id
    return fresh


def _checkpoint_reason(state: dict[str, Any]) -> str | None:
    if int(state.get("observed_output_bytes", 0) or 0) >= LARGE_OUTPUT_BYTES:
        return "model_facing_output_budget"
    if int(state.get("unique_files", 0) or 0) >= LARGE_READ_FILES:
        return "many_unique_files"
    if int(state.get("broad_read_calls", 0) or 0) >= LARGE_BROAD_READS:
        return "repeated_broad_reads"
    return None


def record_read(
    project_root: Path,
    state_path: Path,
    lock_path: Path,
    capsule_path: Path,
    event: dict[str, Any],
) -> str | None:
    paths, broad = read_paths(project_root, event)
    response = event.get("tool_response") or event.get("toolResponse") or event.get("response") or {}
    response_bytes = _bounded_size(response)
    if not paths and not broad and response_bytes < 32 * 1024:
        return None
    now = utc_now_iso()
    try:
        with exclusive_file_lock(lock_path, timeout=0.2, stale_seconds=30.0):
            state = _new_session(load_json(state_path, empty_state()), event)
            state["turn_id"] = str(event.get("turn_id") or event.get("turnId") or "") or state.get("turn_id")
            state["read_calls"] = int(state.get("read_calls", 0) or 0) + 1
            qualified_broad = broad and (response_bytes >= BROAD_READ_MIN_OUTPUT_BYTES or len(paths) >= 8)
            state["broad_read_calls"] = int(state.get("broad_read_calls", 0) or 0) + int(qualified_broad)
            state["bounded_read_calls"] = int(state.get("bounded_read_calls", 0) or 0) + int(bounded_read(event))
            state["large_output_calls"] = int(state.get("large_output_calls", 0) or 0) + int(response_bytes >= LARGE_OUTPUT_BYTES)
            state["unbounded_large_output_calls"] = int(state.get("unbounded_large_output_calls", 0) or 0) + int(
                response_bytes >= LARGE_OUTPUT_BYTES and not bounded_read(event)
            )
            state["observed_output_bytes"] = min(
                MAX_RESPONSE_MEASURE * 4,
                int(state.get("observed_output_bytes", 0) or 0) + response_bytes,
            )
            rows = {str(row.get("path")): dict(row) for row in state.get("files", []) if isinstance(row, dict) and row.get("path")}
            for relative in paths:
                fingerprint, size = _fingerprint(project_root / relative)
                previous = rows.get(relative, {})
                state["repeat_read_calls"] = int(state.get("repeat_read_calls", 0) or 0) + int(bool(previous))
                rows[relative] = {
                    "path": relative,
                    "fingerprint": fingerprint,
                    "size": size,
                    "read_count": int(previous.get("read_count", 0) or 0) + 1,
                    "last_read_at": now,
                }
            state["files"] = list(rows.values())[-MAX_TRACKED_FILES:]
            state["unique_files"] = len(rows)
            state["unique_file_bytes"] = sum(int(row.get("size", 0) or 0) for row in rows.values())
            readers = [str(value) for value in state.get("reader_kinds", []) if value]
            current_reader = reader_kind(event)
            if current_reader not in readers:
                readers.append(current_reader)
            state["reader_kinds"] = readers[-8:]
            directories = _partitionable_directories(state["files"])
            state["partitionable_directories"] = directories
            was_due = bool(state.get("checkpoint_due"))
            reason = _checkpoint_reason(state)
            state["checkpoint_due"] = reason is not None
            state["checkpoint_due_reason"] = reason
            state["subagent_recommended"] = bool(
                state["checkpoint_due"]
                and int(state.get("unique_files", 0) or 0) >= SUBAGENT_MIN_FILES
                and len(directories) >= SUBAGENT_MIN_DIRECTORIES
            )
            state["last_event_at"] = now
            if state["checkpoint_due"] and (not was_due or int(state["read_calls"]) % 8 == 0):
                _write_hierarchical_capsules(project_root, state, capsule_path)
            write_json(state_path, state)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None
    return None


def seal_before_compact(project_root: Path, state_path: Path, lock_path: Path, capsule_path: Path, event: dict[str, Any]) -> None:
    try:
        with exclusive_file_lock(lock_path, timeout=0.2, stale_seconds=30.0):
            state = _new_session(load_json(state_path, empty_state()), event)
            state["last_compact_at"] = utc_now_iso()
            state["turn_id"] = str(event.get("turn_id") or event.get("turnId") or "") or state.get("turn_id")
            if int(state.get("read_calls", 0) or 0) > 0:
                _write_hierarchical_capsules(project_root, state, capsule_path)
            write_json(state_path, state)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return


def recovery_context(project_root: Path, state_path: Path, lock_path: Path, capsule_path: Path, event: dict[str, Any]) -> str | None:
    source = str(event.get("source") or "")
    if source != "compact":
        return None
    try:
        state = load_json(state_path, empty_state())
        capsule = load_json(capsule_path, {})
    except (OSError, json.JSONDecodeError):
        return None
    if int(state.get("read_calls", 0) or 0) <= 0:
        return None
    directory_statuses = _directory_statuses(project_root, capsule_path, capsule)
    current_semantic = [
        row for row in directory_statuses if row.get("semantic_checkpoint_status") == "CURRENT"
    ]
    stale_semantic = [
        row for row in directory_statuses if row.get("semantic_checkpoint_status") == "STALE"
    ]
    semantic = bool(current_semantic) or _semantic_checkpoint_present(capsule)
    directories = [
        str(row.get("directory"))
        for row in capsule.get("directory_index", [])
        if isinstance(row, dict) and row.get("directory")
    ]
    try:
        with exclusive_file_lock(lock_path, timeout=0.2, stale_seconds=30.0):
            current = load_json(state_path, state)
            current["last_recovered_at"] = utc_now_iso()
            write_json(state_path, current)
    except (OSError, RuntimeError, json.JSONDecodeError):
        pass
    sample = ", ".join(directories[-8:]) or "no explicit directories captured"
    checkpoint = "contains current semantic checkpoints" if semantic else "contains only a mechanical read ledger"
    stale_note = (
        f" {len(stale_semantic)} directory checkpoints are stale and must be revalidated from their evidence paths."
        if stale_semantic else ""
    )
    return (
        f"Compaction continuity: {_project_relative(project_root, capsule_path)} {checkpoint} "
        f"covering {state.get('unique_files', 0)} files. Indexed directories: {sample}.{stale_note} Continue "
        "from that capsule and the current task goal; do not blindly reread unchanged covered files. "
        "If semantic findings are still missing and the archive is large, delegate independent "
        "read-only slices to subagents and merge their structured summaries before implementation."
    )


def _assignment_text(event: dict[str, Any] | None) -> str:
    if not isinstance(event, dict):
        return ""
    pieces: list[str] = []
    stack: list[Any] = [event]
    while stack and len(" ".join(pieces)) < 4096:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if str(key).lower() in {"task", "prompt", "instruction", "instructions", "message", "description"} and isinstance(value, str):
                    pieces.append(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return " ".join(pieces).lower()


def _read_only_assignment(event: dict[str, Any] | None) -> bool:
    text = _assignment_text(event)
    if not text:
        return event is None
    read_terms = ("read", "inspect", "research", "analyze", "analyse", "audit", "map", "review", "查阅", "读取", "调研", "分析", "审计", "梳理")
    write_terms = ("implement", "edit", "write code", "fix", "build", "create", "修改", "实现", "修复", "创建", "编写")
    return any(term in text for term in read_terms) and not any(term in text for term in write_terms)


def subagent_context(
    project_root: Path,
    state_path: Path,
    capsule_path: Path,
    event: dict[str, Any] | None = None,
) -> str | None:
    try:
        state = load_json(state_path, empty_state())
        capsule = load_json(capsule_path, {})
    except (OSError, json.JSONDecodeError):
        return None
    if not state.get("subagent_recommended") or not _read_only_assignment(event):
        return None
    return (
        "If this assignment is part of the current read-heavy discovery, stay read-only and inspect "
        "only the assigned module/archive slice. Return a concise structured summary containing "
        "confirmed facts, key interfaces, dependencies, relevance to the current goal, open questions, "
        f"and file fingerprints. The main thread will merge it into {_project_relative(project_root, capsule_path)}."
    )


def _directory_statuses(project_root: Path, capsule_path: Path, capsule: dict[str, Any]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for row in capsule.get("directory_index", [])[:MAX_TRACKED_FILES]:
        if not isinstance(row, dict) or not row.get("capsule"):
            continue
        target = project_root / str(row["capsule"])
        directory_capsule = load_json(target, {})
        semantic_status, stale = _semantic_status(project_root, directory_capsule)
        statuses.append({
            "directory": row.get("directory"),
            "capsule": row.get("capsule"),
            "semantic_checkpoint_status": semantic_status,
            "stale_evidence": stale,
        })
    return statuses


def compact_status(project_root: Path, state_path: Path, capsule_path: Path) -> dict[str, Any]:
    """Return a local, opt-in summary without injecting it into the model."""
    try:
        state = load_json(state_path, empty_state())
        capsule = load_json(capsule_path, {})
    except (OSError, json.JSONDecodeError):
        state = empty_state()
        capsule = {}
    due = bool(state.get("checkpoint_due"))
    directory_index = capsule.get("directory_index", []) if isinstance(capsule, dict) else []
    directory_statuses = _directory_statuses(project_root, capsule_path, capsule)
    semantic_directory_count = sum(
        1 for row in directory_statuses if row.get("semantic_checkpoint_status") == "CURRENT"
    )
    stale_directories = [
        str(row.get("directory")) for row in directory_statuses
        if row.get("semantic_checkpoint_status") == "STALE"
    ]
    subagent_recommended = bool(state.get("subagent_recommended"))
    unbounded_large_outputs = int(state.get("unbounded_large_output_calls", 0) or 0)
    if due and unbounded_large_outputs:
        recommended_action = "switch_to_symbol_or_paged_reads_then_checkpoint"
    elif due and subagent_recommended:
        recommended_action = "checkpoint_findings_and_partition_independent_read_only_slices"
    elif due:
        recommended_action = "checkpoint_findings_and_continue_bounded_reading"
    else:
        recommended_action = "continue_normal_execution"
    return {
        "status": "CHECKPOINT_DUE" if due else "READING" if state.get("read_calls") else "IDLE",
        "read_calls": int(state.get("read_calls", 0) or 0),
        "unique_files": int(state.get("unique_files", 0) or 0),
        "unique_file_bytes": int(state.get("unique_file_bytes", 0) or 0),
        "observed_output_bytes": int(state.get("observed_output_bytes", 0) or 0),
        "bounded_read_calls": int(state.get("bounded_read_calls", 0) or 0),
        "large_output_calls": int(state.get("large_output_calls", 0) or 0),
        "unbounded_large_output_calls": unbounded_large_outputs,
        "repeat_read_calls": int(state.get("repeat_read_calls", 0) or 0),
        "reader_kinds": list(state.get("reader_kinds", [])),
        "checkpoint_due_reason": state.get("checkpoint_due_reason"),
        "directory_count": len(directory_index),
        "semantic_directory_count": semantic_directory_count,
        "stale_directories": stale_directories,
        "partitionable_directories": list(state.get("partitionable_directories", [])),
        "subagent_recommended": subagent_recommended,
        "capsule": _project_relative(project_root, capsule_path) if (project_root / capsule_path if not capsule_path.is_absolute() else capsule_path).exists() else None,
        "semantic_checkpoint_present": bool(semantic_directory_count) or _semantic_checkpoint_present(capsule),
        "recommended_action": recommended_action,
        "proactive_injection": False,
        "llm_assistance": False,
    }


def post_compact(state_path: Path, lock_path: Path, event: dict[str, Any]) -> None:
    try:
        with exclusive_file_lock(lock_path, timeout=0.2, stale_seconds=30.0):
            state = _new_session(load_json(state_path, empty_state()), event)
            state["last_compact_at"] = utc_now_iso()
            write_json(state_path, state)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return
