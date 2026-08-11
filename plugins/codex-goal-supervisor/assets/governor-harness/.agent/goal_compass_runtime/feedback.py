"""Local-only Goal Supervisor diagnostic capture.

This distribution intentionally contains no network transport, device
registration, remote endpoint, or credential handling.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from .state_store import exclusive_file_lock, load_json, utc_now_iso, write_json, write_json_exclusive


CONFIG_NAME = "feedback_config.json"
OUTBOX_DIR = "feedback-outbox"
DELIVERY_STATE = "feedback_delivery_state.json"
DELIVERY_LOCK = "feedback_delivery.lock"
EVENT_SCHEMA_VERSION = 1
MAX_TEXT = 2000

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_TOKEN_SHAPES = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9._~+/-]{12,})\b"
)


def default_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capture_enabled": True,
        "privacy_mode": "governance_metadata_only",
        "delivery": "local_outbox_only",
        "project_id": uuid.uuid4().hex,
    }


def ensure_config(agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    path = agent_dir / CONFIG_NAME
    current = load_json(path, {})
    if not isinstance(current, dict) or not current:
        current = default_config()
        write_json(path, current)
    return current


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "<truncated>"
    if isinstance(value, str):
        text = value[:MAX_TEXT].replace(str(Path.home()), "<HOME>")
        text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
        return _TOKEN_SHAPES.sub("[REDACTED]", text)
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 30:
                cleaned["_truncated"] = True
                break
            low = str(key).lower()
            if any(term in low for term in ("prompt", "source_text", "file_content", "environment", "secret", "password", "token")):
                continue
            cleaned[str(key)[:120]] = _redact(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in list(value)[:30]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _redact(str(value), depth + 1)


def _project_fingerprint(config: dict[str, Any]) -> str:
    seed = f"{config.get('project_id', '')}:{Path.cwd().resolve()}"
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:24]


def _plugin_version(agent_dir: Path) -> str | None:
    provenance = load_json(agent_dir / "goal_compass_install.json", {})
    if isinstance(provenance, dict) and provenance.get("plugin_version"):
        return str(provenance["plugin_version"])
    return None


def _paths(agent_dir: Path) -> tuple[Path, Path, Path]:
    runtime = agent_dir / "runtime"
    return runtime / OUTBOX_DIR, runtime / DELIVERY_STATE, runtime / DELIVERY_LOCK


def record(
    *,
    kind: str,
    message: str,
    source: str,
    severity: str = "warning",
    rule_id: str | None = None,
    command: str | None = None,
    ticket_id: str | None = None,
    status: str | None = None,
    context: dict[str, Any] | None = None,
    agent_dir: Path = Path(".agent"),
    request_iteration: bool = True,
) -> dict[str, Any]:
    config = ensure_config(agent_dir)
    if config.get("capture_enabled") is False:
        return {"captured": False, "delivery": "DISABLED"}
    event_id = uuid.uuid4().hex
    clean_message = str(_redact(message))
    fingerprint_seed = "|".join([kind, str(rule_id or ""), str(status or ""), clean_message])
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "event_fingerprint": hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest(),
        "occurred_at": utc_now_iso(),
        "project_fingerprint": _project_fingerprint(config),
        "plugin_version": _plugin_version(agent_dir),
        "runtime": {"os": platform.system(), "python": f"{sys.version_info.major}.{sys.version_info.minor}"},
        "source": source,
        "kind": kind,
        "severity": severity,
        "rule_id": rule_id,
        "command": command,
        "ticket_id": ticket_id,
        "status": status,
        "message": clean_message,
        "context": _redact(context or {}),
        "privacy_mode": "governance_metadata_only",
        "maintainer_action": "OPEN_REPRODUCTION_AND_REPAIR_TICKET" if request_iteration else "OBSERVE",
    }
    outbox, state_path, lock_path = _paths(agent_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    write_json_exclusive(outbox / f"{event_id}.json", event)
    try:
        with exclusive_file_lock(lock_path, timeout=0.5, stale_seconds=30.0):
            state = load_json(state_path, {})
            if not isinstance(state, dict):
                state = {}
            state["pending"] = int(state.get("pending", 0) or 0) + 1
            state["last_captured_at"] = event["occurred_at"]
            state["last_event_fingerprint"] = event["event_fingerprint"]
            write_json(state_path, state)
    except RuntimeError:
        pass
    return {
        "captured": True,
        "event_id": event_id,
        "queued_locally": True,
        "delivery": {"status": "LOCAL_ONLY", "sent": 0},
    }


def status(agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    config = ensure_config(agent_dir)
    outbox, state_path, _ = _paths(agent_dir)
    state = load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    try:
        pending = sum(1 for _ in outbox.glob("*.json"))
    except OSError:
        pending = int(state.get("pending", 0) or 0)
    return {
        "capture_enabled": config.get("capture_enabled") is not False,
        "delivery_mode": "local_outbox_only",
        "delivery_status": "LOCAL_ONLY",
        "pending": pending,
        "last_captured_at": state.get("last_captured_at"),
        "privacy_mode": "governance_metadata_only",
    }

