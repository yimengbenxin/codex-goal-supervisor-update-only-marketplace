#!/usr/bin/env python3
"""Route an explicitly targeted active project to its Goal Compass hook."""
from __future__ import annotations

import json
import io
import os
import re
import runpy
import shlex
import sys
import time
from pathlib import Path
from typing import Any


COMPASS = Path(".agent") / "goal_compass.py"
PROJECT_HOOK = Path(".agent") / "goal_compass_runtime" / "project_hook.py"
CURRENT = Path(".agent") / "current_ticket.json"
TOOL_MODE = Path(".agent") / "tool_mode.json"
SESSION_BINDING_TTL_SECONDS = 30 * 24 * 60 * 60


def session_binding_dir() -> Path:
    override = os.environ.get("GOAL_SUPERVISOR_SESSION_BINDING_DIR")
    if override:
        return Path(override).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return codex_home / "runtime" / "goal-supervisor" / "session-bindings"


def session_id(event: dict[str, Any]) -> str | None:
    value = str(event.get("session_id") or event.get("sessionId") or "").strip()
    return value or None


def binding_path(event: dict[str, Any]) -> Path | None:
    value = session_id(event)
    if not value:
        return None
    import hashlib
    return session_binding_dir() / (hashlib.sha256(value.encode("utf-8")).hexdigest()[:32] + ".json")


def remember_root(event: dict[str, Any], root: Path) -> None:
    path = binding_path(event)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "session_id_hash": path.stem,
            "project_root": str(root.resolve()),
            "updated_at_unix": int(time.time()),
        }
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def bound_root(event: dict[str, Any]) -> Path | None:
    path = binding_path(event)
    if path is None or not path.is_file():
        return None
    try:
        if time.time() - path.stat().st_mtime > SESSION_BINDING_TTL_SECONDS:
            path.unlink(missing_ok=True)
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        root = Path(str(payload.get("project_root") or "")).expanduser().resolve()
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not is_compass_root(root) or not root_is_observed(root):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return root


def configure_utf8_output() -> None:
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def is_compass_root(path: Path) -> bool:
    return (path / COMPASS).is_file()


def is_plugin_template_root(path: Path) -> bool:
    for parent in (path, *path.parents):
        if not (parent / ".codex-plugin" / "plugin.json").is_file():
            continue
        try:
            rel = path.resolve().relative_to(parent.resolve())
        except ValueError:
            return False
        return rel.parts[:2] == ("assets", "governor-harness")
    return False


def nearest_root(path: Path) -> Path | None:
    candidate = path if path.is_dir() else path.parent
    if is_plugin_template_root(candidate):
        return None
    for parent in (candidate, *candidate.parents):
        if is_compass_root(parent) and not is_plugin_template_root(parent):
            return parent
    return None


def ticket_status(root: Path) -> str:
    try:
        data = json.loads((root / CURRENT).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "NONE"
    return str(data.get("status") or "NONE")


def tool_mode_enabled(root: Path) -> bool:
    try:
        data = json.loads((root / TOOL_MODE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("enabled") is True and data.get("mode") == "BACKGROUND_ADVISORY"


def root_is_observed(root: Path) -> bool:
    return tool_mode_enabled(root) or ticket_status(root) == "ACTIVE"


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def referenced_paths(event: dict[str, Any], cwd: Path) -> list[Path]:
    out: list[Path] = []
    for value in strings(event.get("tool_input") or event.get("toolInput") or event.get("input") or {}):
        if "\n" not in value and len(value) < 1000:
            path = Path(os.path.expanduser(value))
            if path.is_absolute() or value.startswith(("./", "../")):
                out.append(path if path.is_absolute() else cwd / path)
        for match in re.finditer(r"(?:\*\*\* (?:Add|Update|Delete) File:|\b(?:cd|--cwd|workdir)\s+)[ \t]*([^\n;|&]+)", value):
            raw = match.group(1).strip().strip("'\"")
            path = Path(os.path.expanduser(raw))
            out.append(path if path.is_absolute() else cwd / path)
        if "\n" not in value:
            try:
                tokens = shlex.split(value)
            except ValueError:
                tokens = []
            for token in tokens:
                raw = token.strip("'\"(),")
                if raw.startswith("-") or raw in {"&&", "||", ";", "|"} or "/" not in raw or "://" in raw:
                    continue
                path = Path(os.path.expanduser(raw))
                out.append(path if path.is_absolute() else cwd / path)
    return out


def select_root(event: dict[str, Any]) -> Path | None:
    cwd = Path(str(event.get("cwd") or os.getcwd())).expanduser().resolve()
    # Only the current project or an explicitly referenced project may opt in.
    # Never search neighboring directories: that made unrelated tasks inherit a
    # nearby project's ACTIVE ticket.
    direct_root = nearest_root(cwd)
    if direct_root:
        if root_is_observed(direct_root):
            remember_root(event, direct_root)
            return direct_root
        return None

    path_roots: list[Path] = []
    for path in referenced_paths(event, cwd):
        root = nearest_root(path)
        if root and root not in path_roots:
            path_roots.append(root)
    observed_path_roots = [root for root in path_roots if root_is_observed(root)]
    if len(observed_path_roots) == 1:
        remember_root(event, observed_path_roots[0])
        return observed_path_roots[0]
    if observed_path_roots:
        return None
    return bound_root(event)


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    root = select_root(event)
    if root is None:
        return 0
    event_name = str(event.get("hook_event_name") or event.get("hookEventName") or "")
    project_hook = root / PROJECT_HOOK
    target = project_hook if project_hook.is_file() else root / COMPASS
    old_cwd = Path.cwd()
    old_stdin = sys.stdin
    old_argv = sys.argv
    try:
        os.chdir(root)
        sys.stdin = io.StringIO(raw)
        sys.argv = [str(target)] if target == project_hook else [str(target), "hook"]
        agent_root = str(root / ".agent")
        if agent_root not in sys.path:
            sys.path.insert(0, agent_root)
        runpy.run_path(str(target), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 0
        if code and event_name in {"PreToolUse", "PostToolUse"}:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": "Codex Goal Supervisor observer returned an error; execution continues and the failure was not treated as product evidence."}}, ensure_ascii=False))
        return 0
    except Exception:
        if event_name in {"PreToolUse", "PostToolUse"}:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": "Codex Goal Supervisor observer was unavailable; execution continues without semantic supervision."}}, ensure_ascii=False))
        return 0
    finally:
        os.chdir(old_cwd)
        sys.stdin = old_stdin
        sys.argv = old_argv


if __name__ == "__main__":
    configure_utf8_output()
    raise SystemExit(main())
