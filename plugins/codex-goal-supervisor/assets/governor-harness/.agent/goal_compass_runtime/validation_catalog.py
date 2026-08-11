"""Validation catalog loading with one parse per unchanged CLI process."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any


_cached_path: Path | None = None
_cached_signature: tuple[int, int] | None = None
_cached_data: dict[str, Any] = {}


def invalidate(path: Path | None = None) -> None:
    global _cached_path, _cached_signature, _cached_data
    if path is not None and _cached_path is not None and path.resolve() != _cached_path:
        return
    _cached_path = None
    _cached_signature = None
    _cached_data = {}


def load_catalog(path: Path) -> dict[str, Any]:
    global _cached_path, _cached_signature, _cached_data
    resolved = path.resolve()
    try:
        stat = resolved.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        invalidate()
        return {}
    if resolved == _cached_path and signature == _cached_signature:
        return _cached_data
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    _cached_path = resolved
    _cached_signature = signature
    _cached_data = value if isinstance(value, dict) else {}
    return _cached_data


def command_parts(row: dict[str, Any]) -> tuple[list[str], str | None]:
    argv = row.get("argv")
    template = str(row.get("cmd", ""))
    if isinstance(argv, list) and argv:
        return [
            sys.executable if str(value) == "{python}" else str(value).replace("{python}", sys.executable)
            for value in argv
        ], None
    if template:
        try:
            return [
                sys.executable if value == "{python}" else value.replace("{python}", sys.executable)
                for value in shlex.split(template)
            ], None
        except ValueError as exc:
            return [], f"invalid command template: {exc}"
    return [], "empty command"

