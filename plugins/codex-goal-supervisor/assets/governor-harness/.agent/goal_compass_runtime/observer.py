"""Low-cost background observation for Codex Goal Supervisor.

The observer records bounded metadata only. It never decides semantic drift and
never requires a ticket. Expensive or ambiguous review is requested only after
a concrete event threshold is crossed.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from goal_compass_runtime.state_store import load_json, write_json_exclusive, write_jsonl
from goal_compass_runtime.deviation_incidents import (
    compact_summary as compact_deviation_summary,
    process_write as process_deviation_write,
)


POLICY_VERSION = "2.0"
MAX_TRACKED_PATHS = 100
MAX_RECENT_EVENT_IDS = 512
MAX_RECENT_EVENTS = 128
MAX_RECENT_EVENT_BYTES = 64 * 1024
MAX_PENDING_EVENTS = 512


def empty_verification_debt() -> dict[str, Any]:
    return {
        "pending": False,
        "write_started_at": None,
        "write_paths": [],
        "validation_started_at": None,
        "validation_passed_at": None,
        "validation_failed_at": None,
        "validation_result_observed": False,
        "last_reminded_fingerprint": None,
    }


def empty_state() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "pre_events": 0,
        "post_events": 0,
        "failed_events": 0,
        "consecutive_failures": 0,
        "writes": 0,
        "reads": 0,
        "validations": 0,
        "agents": 0,
        "external": 0,
        "changed_path_candidates": [],
        "emitted_signals": [],
        "recent_event_ids": [],
        "recent_events": [],
        "fallback_events_recovered": 0,
        "fallback_overflow_detected": False,
        "deviation_incidents": {},
        "verification_debt": empty_verification_debt(),
        "last_event_at": None,
    }


def _is_product_path(path: str) -> bool:
    value = str(path or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return bool(value) and not (
        value == ".agent"
        or value.startswith(".agent/")
        or value == ".codex"
        or value.startswith(".codex/")
        or value.startswith("__outside_repo__/")
    )


def _update_verification_debt(
    state: dict[str, Any],
    *,
    phase: str,
    category: str,
    paths: list[str],
    failed: bool,
    observed_at: str,
) -> None:
    debt = dict(state.get("verification_debt") or empty_verification_debt())
    for key, value in empty_verification_debt().items():
        debt.setdefault(key, value)

    product_paths = [path for path in paths if _is_product_path(path)]
    if phase == "PreToolUse" and category == "write" and product_paths:
        known = list(debt.get("write_paths") or [])
        for path in product_paths:
            if path not in known:
                known.append(path)
        debt.update({
            "pending": True,
            "write_started_at": observed_at,
            "write_paths": known[-20:],
            "validation_started_at": None,
            "validation_passed_at": None,
            "validation_failed_at": None,
            "validation_result_observed": False,
            "last_reminded_fingerprint": None,
        })
    elif phase == "PreToolUse" and category == "validation" and debt.get("pending"):
        debt["validation_started_at"] = observed_at
        debt["validation_result_observed"] = False
        debt["validation_failed_at"] = None
        debt["last_reminded_fingerprint"] = None
    elif phase == "PostToolUse" and category == "validation" and debt.get("pending"):
        # A post result can clear debt only when this observer first saw a
        # validation start after the latest product write. Missing post hooks
        # therefore degrade to "unverified" instead of manufacturing success.
        if debt.get("validation_started_at"):
            debt["validation_result_observed"] = True
            if failed:
                debt["validation_failed_at"] = observed_at
                debt["last_reminded_fingerprint"] = None
            else:
                debt.update({
                    "pending": False,
                    "validation_passed_at": observed_at,
                    "validation_failed_at": None,
                    "last_reminded_fingerprint": None,
                })
    state["verification_debt"] = debt


def update_state(
    state: dict[str, Any] | None,
    *,
    phase: str,
    category: str,
    paths: list[str],
    failed: bool,
    observed_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Update compact counters and return newly crossed advisory thresholds."""
    row = dict(state or empty_state())
    signals: list[dict[str, Any]] = []
    key = "pre_events" if phase == "PreToolUse" else "post_events"
    row[key] = int(row.get(key, 0) or 0) + 1
    row["last_event_at"] = observed_at

    if phase == "PostToolUse":
        category_key = {
            "write": "writes",
            "read": "reads",
            "validation": "validations",
            "agent": "agents",
            "external": "external",
        }.get(category, "reads")
        row[category_key] = int(row.get(category_key, 0) or 0) + 1
        if failed:
            row["failed_events"] = int(row.get("failed_events", 0) or 0) + 1
            row["consecutive_failures"] = int(row.get("consecutive_failures", 0) or 0) + 1
        else:
            row["consecutive_failures"] = 0

    known_paths = list(row.get("changed_path_candidates") or [])
    for path in paths:
        if path and path not in known_paths:
            known_paths.append(path)
    row["changed_path_candidates"] = known_paths[-MAX_TRACKED_PATHS:]
    _update_verification_debt(
        row,
        phase=phase,
        category=category,
        paths=paths,
        failed=failed,
        observed_at=observed_at,
    )

    emitted = set(str(value) for value in row.get("emitted_signals", []))
    if int(row.get("consecutive_failures", 0) or 0) >= 3 and "REPEATED_FAILURE" not in emitted:
        signals.append({
            "signal": "REPEATED_FAILURE",
            "intervention": "STRONG_WARNING",
            "reason": "Three consecutive tool failures need root-cause review before repeating the same action.",
            "recommended_action": "inspect_first_root_cause",
        })
        emitted.add("REPEATED_FAILURE")
    if len(known_paths) >= 50 and "BROAD_ARTIFACT_SURFACE" not in emitted:
        signals.append({
            "signal": "BROAD_ARTIFACT_SURFACE",
            "intervention": "STRONG_WARNING",
            "reason": "The observed write surface reached 50 paths; verify that this is intentional batch work or a declared artifact set.",
            "recommended_action": "confirm_batch_or_review_scope",
        })
        emitted.add("BROAD_ARTIFACT_SURFACE")
    row["emitted_signals"] = sorted(emitted)
    return row, signals


def observation_event(
    *,
    event_id: str,
    phase: str,
    category: str,
    paths: list[str],
    failed: bool,
    observed_at: str,
    fallback: bool = False,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "ts": observed_at,
        "phase": phase,
        "category": category,
        "failed": bool(failed),
        "paths": paths[:20],
        "fallback": bool(fallback),
    }


def _bounded_recent_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    used = 0
    for row in reversed(rows[-MAX_RECENT_EVENTS:]):
        size = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 1
        if kept and used + size > MAX_RECENT_EVENT_BYTES:
            break
        kept.append(row)
        used += size
    return list(reversed(kept))


def apply_observation(
    state: dict[str, Any] | None,
    event: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = dict(state or empty_state())
    event_id = str(event.get("event_id") or "")
    recent_ids = list(row.get("recent_event_ids") or [])
    if event_id and event_id in recent_ids:
        return row, []
    row, signals = update_state(
        row,
        phase=str(event.get("phase") or ""),
        category=str(event.get("category") or "read"),
        paths=[str(path) for path in event.get("paths", []) if str(path)],
        failed=bool(event.get("failed")),
        observed_at=str(event.get("ts") or ""),
    )
    deviation = event.get("deviation_context") if isinstance(event.get("deviation_context"), dict) else None
    if deviation and event.get("phase") == "PreToolUse":
        row, deviation_signals = process_deviation_write(
            row,
            north_star_goal=str(deviation.get("north_star_goal") or ""),
            policies=[],
            tool_input={},
            paths=[str(path) for path in event.get("paths", []) if str(path)],
            observed_at=str(event.get("ts") or ""),
            matched_policy_hits=[str(value) for value in deviation.get("matched_policies", []) if str(value)],
            added_lines_override=int(deviation.get("added_lines", 0) or 0),
            policy_sources={
                str(policy): str(layer)
                for policy, layer in (deviation.get("matched_policy_sources") or {}).items()
                if str(policy) and str(layer)
            } if isinstance(deviation.get("matched_policy_sources"), dict) else None,
        )
        signals = [*deviation_signals, *signals]
    if event_id:
        recent_ids.append(event_id)
        row["recent_event_ids"] = recent_ids[-MAX_RECENT_EVENT_IDS:]
    if event.get("fallback"):
        row["fallback_events_recovered"] = int(row.get("fallback_events_recovered", 0) or 0) + 1
    if event.get("category") != "read" or event.get("failed") or signals:
        recent = list(row.get("recent_events") or [])
        recent.append({
            "ts": event.get("ts"),
            "phase": event.get("phase"),
            "category": event.get("category"),
            "failed": bool(event.get("failed")),
            "paths": list(event.get("paths") or [])[:20],
            "signals": [signal.get("signal") for signal in signals],
        })
        row["recent_events"] = _bounded_recent_events(recent)
    return row, signals


def queue_pending_event(directory: Path, event: dict[str, Any]) -> bool:
    """Retain a lock-contended observation without blocking product work."""
    directory.mkdir(parents=True, exist_ok=True)
    files = list(directory.glob("*.json"))
    if len(files) >= MAX_PENDING_EVENTS:
        marker = directory / "overflow"
        try:
            marker.touch(exist_ok=True)
        except OSError:
            pass
        return False
    row = dict(event)
    row["fallback"] = True
    target = directory / f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex}.json"
    try:
        write_json_exclusive(target, row)
        return True
    except (FileExistsError, OSError):
        return False


def load_pending_events(directory: Path) -> tuple[list[tuple[Path, dict[str, Any]]], bool]:
    if not directory.exists():
        return [], False
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json"))[:MAX_PENDING_EVENTS]:
        try:
            value = load_json(path, {})
        except (OSError, json.JSONDecodeError):
            value = {}
        if isinstance(value, dict) and value.get("event_id"):
            rows.append((path, value))
        else:
            rows.append((path, {}))
    return rows, (directory / "overflow").exists()


def apply_pending_events(
    state: dict[str, Any],
    directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Path]]:
    pending, overflow = load_pending_events(directory)
    signals: list[dict[str, Any]] = []
    processed: list[Path] = []
    row = state
    for path, event in pending:
        if event:
            row, event_signals = apply_observation(row, event)
            for signal in event_signals:
                deferred = dict(signal)
                if deferred.get("deny"):
                    deferred["deny"] = False
                    deferred["intervention"] = "DEFERRED_RAIL_ACTIVATED"
                    deferred["reason"] = str(deferred.get("reason") or "") + " The rail applies to the next matching write; the current unrelated action remains available."
                signals.append(deferred)
        else:
            row["fallback_events_corrupt"] = int(row.get("fallback_events_corrupt", 0) or 0) + 1
        processed.append(path)
    if overflow:
        row["fallback_overflow_detected"] = True
    return row, signals, processed


def finalize_pending_events(paths: list[Path], directory: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass


def persist_recent_events(path: Path, state: dict[str, Any]) -> None:
    write_jsonl(path, list(state.get("recent_events") or []))


def pending_event_summary(directory: Path) -> dict[str, int | bool]:
    rows, overflow = load_pending_events(directory)
    counts = {"pre": 0, "post": 0, "failed": 0}
    for _, row in rows:
        if row.get("phase") == "PreToolUse":
            counts["pre"] += 1
        elif row.get("phase") == "PostToolUse":
            counts["post"] += 1
        if row.get("failed"):
            counts["failed"] += 1
    return {**counts, "total": len(rows), "overflow": overflow}


def compact_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    row = state or empty_state()
    return {
        "mode": "BACKGROUND_OBSERVING",
        "policy_version": row.get("policy_version", POLICY_VERSION),
        "events": {
            "pre": int(row.get("pre_events", 0) or 0),
            "post": int(row.get("post_events", 0) or 0),
            "failed": int(row.get("failed_events", 0) or 0),
        },
        "writes": int(row.get("writes", 0) or 0),
        "tracked_path_count": len(row.get("changed_path_candidates") or []),
        "fallback_events_recovered": int(row.get("fallback_events_recovered", 0) or 0),
        "fallback_overflow_detected": bool(row.get("fallback_overflow_detected")),
        "last_event_at": row.get("last_event_at"),
        "deviations": compact_deviation_summary(row),
        "verification_debt": {
            "pending": bool((row.get("verification_debt") or {}).get("pending")),
            "write_path_count": len((row.get("verification_debt") or {}).get("write_paths") or []),
            "validation_result_observed": bool(
                (row.get("verification_debt") or {}).get("validation_result_observed")
            ),
        },
        "visible_ticket_required": False,
    }
