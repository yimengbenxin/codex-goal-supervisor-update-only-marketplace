"""Recover the active Goal after a bounded user interruption and compaction.

Conversation history is not treated as task state. This module keeps a small,
project-local lifecycle projection for explicit temporary branches and emits a
bounded recovery checkpoint only after Codex compacts the root session.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from goal_compass_runtime.state_store import (
    append_jsonl,
    exclusive_file_lock,
    load_json,
    utc_now_iso,
    write_json,
    write_jsonl,
)


SCHEMA_VERSION = 1
MAX_EVENTS = 512
MAX_EVENT_BYTES = 512 * 1024
MAX_SESSIONS = 16
MAX_INTERRUPTS = 32
MAX_SUMMARY_CHARS = 240
MAX_PATHS = 24
MAX_CONTEXT_CHARS = 1400
MAX_GOAL_CHANGE_CANDIDATES = 16

TEMPORARY_BRANCH = "TEMPORARY_BRANCH"
QUESTION_ONLY = "QUESTION_ONLY"
PERSISTENT_CONSTRAINT = "PERSISTENT_CONSTRAINT"
GOAL_REPLACEMENT_REQUEST = "GOAL_REPLACEMENT_REQUEST"
UNSCOPED = "UNSCOPED"

OPEN = "OPEN"
CLOSE_CANDIDATE = "CLOSE_CANDIDATE"
CLOSED = "CLOSED"
PROMOTED_TO_CONSTRAINT = "PROMOTED_TO_CONSTRAINT"
SUPERSEDED = "SUPERSEDED"

_TEMPORARY_MARKERS = (
    "插一句", "临时", "先回答", "先处理", "顺便", "暂停一下", "暂时", "题外",
    "quick question", "temporary", "for now", "before continuing", "side question",
    "pause and", "first answer", "one-off",
)
_PERSISTENT_MARKERS = (
    "从现在起", "以后都", "接下来始终", "全程", "永久", "持续作为约束",
    "from now on", "always", "for the rest of", "persistent constraint",
)
_GOAL_REPLACEMENT_MARKERS = (
    "替换北极星", "修改北极星", "更改北极星", "更新北极星", "把北极星改成",
    "将北极星改为", "北极星调整为", "更换总目标", "把总目标改成", "重新设定目标",
    "replace the north star", "replace the goal", "change the north star",
    "new primary goal",
)
_DURABLE_DIRECTION_MARKERS = (
    "长期", "以后", "今后", "从现在起", "接下来主要", "未来都", "持续转向",
    "long-term", "long term", "from now on", "going forward", "future direction",
)
_STRATEGIC_DIRECTION_MARKERS = (
    "北极星", "总目标", "核心目标", "长期方向", "产品方向", "产品定位", "战略方向",
    "主要目标", "工作重心", "核心交付", "改为", "转向", "不再做",
    "north star", "primary goal", "core goal", "product direction", "product positioning",
    "strategic direction", "main focus", "core deliverable", "pivot", "shift to", "instead of",
)
_DIRECTION_CONTINUITY_MARKERS = (
    "继续围绕", "仍然围绕", "保持现有", "保持当前", "维持现有", "维持当前",
    "方向不变", "目标不变", "北极星不变", "不改变北极星",
    "continue to focus on", "remain focused on", "keep the current", "maintain the current",
    "direction remains", "goal remains", "north star remains",
)
_GOAL_CHANGE_CONFIRM_MARKERS = (
    "确认更新北极星", "确认修改北极星", "确认更换北极星", "确认更新总目标",
    "确认更换总目标", "同意更新北极星", "confirm the north star change",
    "confirm updating the north star", "yes, update the north star",
)
_GOAL_CHANGE_DISMISS_MARKERS = (
    "不更新北极星", "不要修改北极星", "维持原北极星", "保持原北极星",
    "不更换总目标", "keep the current north star", "do not change the north star",
    "do not update the north star",
)
_GOAL_CHANGE_GENERIC_PHRASES = (
    *_DURABLE_DIRECTION_MARKERS,
    *_STRATEGIC_DIRECTION_MARKERS,
    "项目", "任务", "方向", "目标", "重点", "主要", "核心", "更新", "调整",
    "project", "task", "direction", "goal", "focus", "main", "core", "update", "change",
)
_GOAL_CONTINUATION_MARKERS = {
    "继续", "继续执行", "继续推进", "回到总目标", "回到原目标", "按总目标继续",
    "continue", "continue working", "continue the goal", "resume the goal", "go on",
}
_COMPLETE_MARKERS = (
    "已完成", "完成了", "已修复", "已实现", "已经处理", "处理完", "验证通过",
    "completed", "done", "fixed", "implemented", "resolved", "validated",
)
_INCOMPLETE_MARKERS = (
    "尚未完成", "还没完成", "未完成", "仍在运行", "需要用户", "请提供", "请确认",
    "not complete", "not finished", "still running", "need user", "please provide",
    "please confirm", "blocked",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
)


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "goal_generation": {"id": None, "confirmed": False, "updated_at": None},
        "event_count": 0,
        "sessions": {},
        "updated_at": None,
    }


def _text(value: Any, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def _session_id(event: dict[str, Any]) -> str:
    return _text(event.get("session_id") or event.get("sessionId") or "local", 160) or "local"


def _turn_id(event: dict[str, Any]) -> str | None:
    value = _text(event.get("turn_id") or event.get("turnId"), 160)
    return value or None


def _event_id(event: dict[str, Any], phase: str) -> str:
    for key in ("tool_use_id", "toolUseId", "event_id", "eventId", "call_id", "callId", "turn_id", "turnId"):
        if event.get(key):
            return f"{phase}:{_text(event[key], 180)}"
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    return f"{phase}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def goal_generation_id(north_star: dict[str, Any]) -> str | None:
    if not north_star.get("confirmed") or not str(north_star.get("goal") or "").strip():
        return None
    authority = {
        "goal": north_star.get("goal"),
        "goal_mode_objective": north_star.get("goal_mode_objective"),
        "goal_definition": north_star.get("goal_definition"),
        "confirmed_at": north_star.get("confirmed_at"),
    }
    encoded = json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def classify_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.lower().split())
    if prompt.rstrip().endswith(("?", "？")):
        return QUESTION_ONLY
    if any(marker in normalized for marker in _GOAL_REPLACEMENT_MARKERS):
        return GOAL_REPLACEMENT_REQUEST
    if any(marker in normalized for marker in _PERSISTENT_MARKERS):
        return PERSISTENT_CONSTRAINT
    if normalized in _GOAL_CONTINUATION_MARKERS:
        return UNSCOPED
    if any(marker in normalized for marker in _TEMPORARY_MARKERS):
        return TEMPORARY_BRANCH
    return UNSCOPED


def _long_running_goal(north_star: dict[str, Any], convergence: dict[str, Any]) -> bool:
    if not north_star.get("confirmed") or not str(north_star.get("goal") or "").strip():
        return False
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    process = definition.get("process") if isinstance(definition.get("process"), dict) else {}
    modules = [row for row in process.get("nodes", []) if isinstance(row, dict)]
    final_acceptance = definition.get("final_acceptance") if isinstance(definition.get("final_acceptance"), list) else []
    objective_chars = len(str(north_star.get("goal_mode_objective") or ""))
    stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
    projected = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    projected_modules = [row for row in projected.get("modules", []) if isinstance(row, dict)]
    success_criteria = [row for row in stack.get("l1_success_criteria", []) if isinstance(row, dict)]
    return bool(
        objective_chars >= 800
        or (len(modules) >= 2 and len(final_acceptance) >= 1)
        or (len(projected_modules) >= 2 and len(success_criteria) >= 1)
    )


def _goal_scope_text(north_star: dict[str, Any], convergence: dict[str, Any]) -> str:
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
    values: list[str] = [
        str(north_star.get("goal") or ""),
        str(north_star.get("goal_mode_objective") or ""),
        json.dumps(definition, ensure_ascii=False, default=str),
        json.dumps(stack.get("goal_contract") or {}, ensure_ascii=False, default=str),
    ]
    return " ".join(values).casefold()


def _content_grams(value: str) -> set[str]:
    normalized = value.casefold()
    for phrase in _GOAL_CHANGE_GENERIC_PHRASES:
        normalized = normalized.replace(phrase, " ")
    latin = {term for term in re.findall(r"[a-z0-9][a-z0-9_+.-]{2,}", normalized) if len(term) >= 3}
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    cjk = {
        run[index:index + size]
        for run in cjk_runs
        for size in (2, 3, 4)
        for index in range(max(0, len(run) - size + 1))
    }
    return latin | cjk


def _obviously_contained(prompt: str, north_star: dict[str, Any], convergence: dict[str, Any]) -> bool:
    scope = _goal_scope_text(north_star, convergence)
    grams = _content_grams(prompt)
    if not grams:
        return False
    overlap = sum(1 for gram in grams if gram in scope)
    return overlap >= 3 and overlap / len(grams) >= 0.7


def goal_change_response(prompt: str) -> str | None:
    normalized = " ".join(prompt.casefold().split())
    if not normalized or len(normalized) > 80 or prompt.rstrip().endswith(("?", "？")):
        return None
    if any(marker in normalized for marker in _GOAL_CHANGE_CONFIRM_MARKERS):
        return "CONFIRMED"
    if any(marker in normalized for marker in _GOAL_CHANGE_DISMISS_MARKERS):
        return "DISMISSED"
    return None


def goal_change_candidate(
    north_star: dict[str, Any],
    convergence: dict[str, Any],
    prompt: str,
) -> dict[str, Any] | None:
    """Return only a strict, durable direction-change candidate."""
    prompt = prompt.strip()
    normalized = " ".join(prompt.casefold().split())
    if not prompt or not _long_running_goal(north_star, convergence):
        return None
    if goal_change_response(prompt) or normalized in _GOAL_CONTINUATION_MARKERS:
        return None
    kind = classify_prompt(prompt)
    if kind in {TEMPORARY_BRANCH, QUESTION_ONLY} or prompt.rstrip().endswith(("?", "？")):
        return None
    explicit = kind == GOAL_REPLACEMENT_REQUEST
    if not explicit and any(marker in normalized for marker in _DIRECTION_CONTINUITY_MARKERS):
        return None
    durable = any(marker in normalized for marker in _DURABLE_DIRECTION_MARKERS)
    strategic = any(marker in normalized for marker in _STRATEGIC_DIRECTION_MARKERS)
    if not explicit and not (durable and strategic):
        return None
    if not explicit and _obviously_contained(prompt, north_star, convergence):
        return None
    summary = _text(prompt)
    generation = goal_generation_id(north_star)
    identifier = hashlib.sha256(f"{generation}\n{summary.casefold()}".encode("utf-8")).hexdigest()[:20]
    return {
        "candidate_id": identifier,
        "summary": summary,
        "explicit": explicit,
        "classification": kind,
        "goal_generation_id": generation,
    }


def record_goal_change_confirmation(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
    candidate: dict[str, Any],
    judge: dict[str, Any] | None = None,
) -> bool:
    """Record one project-level pending confirmation per Goal generation."""
    def mutate(state: dict[str, Any], _: dict[str, Any], generation: str | None, now: str) -> tuple[bool, dict[str, Any]]:
        rows = [row for row in state.get("goal_change_candidates", []) if isinstance(row, dict)]
        if any(
            row.get("candidate_id") == candidate.get("candidate_id")
            and row.get("goal_generation_id") == generation
            for row in rows
        ):
            return False, {"candidate_id": candidate.get("candidate_id"), "recorded": False, "reason": "already_seen"}
        if any(
            row.get("goal_generation_id") == generation
            and row.get("status") == "CONFIRMATION_REQUESTED"
            for row in rows
        ):
            return False, {
                "candidate_id": candidate.get("candidate_id"),
                "recorded": False,
                "reason": "confirmation_already_pending",
            }
        rows.append({
            "candidate_id": candidate.get("candidate_id"),
            "goal_generation_id": generation,
            "session_id": _session_id(event),
            "summary": candidate.get("summary"),
            "status": "CONFIRMATION_REQUESTED",
            "requested_at": now,
            "resolved_at": None,
            "judge": {
                key: (judge or {}).get(key)
                for key in ("status", "verdict", "confidence", "fingerprint")
                if (judge or {}).get(key) is not None
            },
        })
        state["goal_change_candidates"] = rows[-MAX_GOAL_CHANGE_CANDIDATES:]
        return True, {"candidate_id": candidate.get("candidate_id"), "recorded": True}

    try:
        return bool(_update(state_path, lock_path, events_path, north_star, event, "GOAL_CHANGE_CONFIRMATION", mutate))
    except (OSError, RuntimeError, json.JSONDecodeError):
        return False


def resolve_goal_change_confirmation(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
    resolution: str,
) -> dict[str, Any] | None:
    def mutate(state: dict[str, Any], _: dict[str, Any], generation: str | None, now: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        rows = [row for row in state.get("goal_change_candidates", []) if isinstance(row, dict)]
        target = next((
            row for row in reversed(rows)
            if row.get("goal_generation_id") == generation
            and row.get("status") == "CONFIRMATION_REQUESTED"
        ), None)
        if not target:
            return None, {"resolved": False, "resolution": resolution}
        target["status"] = resolution
        target["resolved_at"] = now
        state["goal_change_candidates"] = rows[-MAX_GOAL_CHANGE_CANDIDATES:]
        return dict(target), {"resolved": True, "candidate_id": target.get("candidate_id"), "resolution": resolution}

    try:
        return _update(state_path, lock_path, events_path, north_star, event, "GOAL_CHANGE_RESOLUTION", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def _checkpoint(convergence: dict[str, Any]) -> dict[str, Any]:
    stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
    return {
        "stage": _text(stack.get("l2_current_stage")),
        "next_action": _text(stack.get("l3_current_action")),
        "expected_evidence": _text(stack.get("l3_expected_evidence")),
        "convergence_updated_at": convergence.get("updated_at"),
    }


def _session(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    sessions = state.setdefault("sessions", {})
    value = sessions.setdefault(session_id, {
        "active_interrupt_id": None,
        "compaction_revision": 0,
        "last_pre_compact_at": None,
        "last_post_compact_at": None,
        "last_compact_recovery_at": None,
        "interrupts": [],
    })
    while len(sessions) > MAX_SESSIONS:
        oldest = next((key for key in sessions if key != session_id), None)
        if oldest is None:
            break
        sessions.pop(oldest, None)
    return value


def _interrupts(session: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in session.get("interrupts", []) if isinstance(row, dict)]
    session["interrupts"] = rows[-MAX_INTERRUPTS:]
    return session["interrupts"]


def _find_interrupt(session: dict[str, Any], interrupt_id: str | None) -> dict[str, Any] | None:
    if not interrupt_id:
        return None
    return next((row for row in reversed(_interrupts(session)) if row.get("interrupt_id") == interrupt_id), None)


def _active_interrupt(session: dict[str, Any]) -> dict[str, Any] | None:
    row = _find_interrupt(session, session.get("active_interrupt_id"))
    return row if row and row.get("state") in {OPEN, CLOSE_CANDIDATE} else None


def _append_event(
    path: Path,
    row: dict[str, Any],
    *,
    trim_to_limit: bool,
    at_capacity: bool,
) -> None:
    # The JSONL file is diagnostic, not authoritative. Rewriting all 512 rows
    # for every hook after saturation held the lifecycle lock long enough for
    # concurrent Stop events to fail open. Keep the hard bound and preserve the
    # first bounded diagnostic window; the compact state continues to advance.
    if at_capacity:
        try:
            if path.is_file() and path.stat().st_size <= MAX_EVENT_BYTES:
                return
        except OSError:
            return
    append_jsonl(path, row)
    try:
        if not trim_to_limit and path.stat().st_size <= MAX_EVENT_BYTES:
            return
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        write_jsonl(path, rows[-MAX_EVENTS:])
    except OSError:
        return


def _sync_generation(state: dict[str, Any], north_star: dict[str, Any], now: str) -> str | None:
    generation = goal_generation_id(north_star)
    current = state.get("goal_generation") if isinstance(state.get("goal_generation"), dict) else {}
    if current.get("id") != generation:
        for session in state.get("sessions", {}).values():
            if not isinstance(session, dict):
                continue
            for row in _interrupts(session):
                if row.get("state") in {OPEN, CLOSE_CANDIDATE}:
                    row["state"] = SUPERSEDED
                    row["closed_at"] = now
                    row["close_reason"] = "goal_generation_changed"
            session["active_interrupt_id"] = None
        state["goal_generation"] = {
            "id": generation,
            "confirmed": bool(generation),
            "updated_at": now,
        }
    return generation


def _update(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
    phase: str,
    mutate: Any,
) -> Any:
    now = utc_now_iso()
    with exclusive_file_lock(lock_path, timeout=0.35, stale_seconds=30.0):
        state = load_json(state_path, empty_state())
        if not isinstance(state, dict):
            state = empty_state()
        generation = _sync_generation(state, north_star, now)
        session_id = _session_id(event)
        session = _session(state, session_id)
        result, event_detail = mutate(state, session, generation, now)
        state["updated_at"] = now
        previous_event_count = int(state.get("event_count", 0) or 0)
        if not events_path.exists():
            previous_event_count = 0
        event_count = previous_event_count + 1
        at_event_capacity = previous_event_count >= MAX_EVENTS
        trim_events = event_count > MAX_EVENTS
        state["event_count"] = MAX_EVENTS if trim_events else event_count
        write_json(state_path, state)
        _append_event(events_path, {
            "schema_version": SCHEMA_VERSION,
            "event": phase,
            "event_id": _event_id(event, phase),
            "session_id": session_id,
            "turn_id": _turn_id(event),
            "goal_generation_id": generation,
            "observed_at": now,
            **(event_detail if isinstance(event_detail, dict) else {}),
        }, trim_to_limit=trim_events, at_capacity=at_event_capacity)
        return result


def on_user_prompt(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    convergence: dict[str, Any],
    event: dict[str, Any],
) -> str | None:
    prompt = str(event.get("prompt") or "").strip()
    kind = classify_prompt(prompt)

    def mutate(_: dict[str, Any], session: dict[str, Any], generation: str | None, now: str) -> tuple[str | None, dict[str, Any]]:
        if not generation or not prompt:
            return None, {"classification": kind, "recorded": False}
        active = _active_interrupt(session)
        if active:
            if kind == PERSISTENT_CONSTRAINT:
                active["state"] = PROMOTED_TO_CONSTRAINT
                active["closed_at"] = now
                active["close_reason"] = "user_promoted_to_persistent_constraint"
                active["promoted_constraint_summary"] = _text(prompt)
                session["active_interrupt_id"] = None
                return None, {
                    "classification": kind,
                    "recorded": True,
                    "interrupt_id": active.get("interrupt_id"),
                    "transition": PROMOTED_TO_CONSTRAINT,
                }
            if kind == GOAL_REPLACEMENT_REQUEST:
                active["state"] = SUPERSEDED
                active["closed_at"] = now
                active["close_reason"] = "explicit_goal_replacement_requested"
                session["active_interrupt_id"] = None
                return None, {
                    "classification": kind,
                    "recorded": True,
                    "interrupt_id": active.get("interrupt_id"),
                    "transition": SUPERSEDED,
                }
            active["last_turn_id"] = _turn_id(event)
            active["updated_at"] = now
            return None, {"classification": kind, "recorded": False, "active_interrupt_id": active.get("interrupt_id")}
        continuation = " ".join(prompt.lower().split()) in _GOAL_CONTINUATION_MARKERS
        if continuation:
            return None, {"classification": kind, "recorded": False}
        record_kind = TEMPORARY_BRANCH if kind == UNSCOPED else kind
        if record_kind not in {TEMPORARY_BRANCH, QUESTION_ONLY}:
            return None, {"classification": kind, "recorded": False}
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        prior = next((row for row in reversed(_interrupts(session)) if row.get("prompt_hash") == prompt_hash and row.get("state") == CLOSED), None)
        interrupt_id = hashlib.sha256(
            f"{generation}\n{_session_id(event)}\n{_turn_id(event)}\n{prompt_hash}\n{now}".encode("utf-8")
        ).hexdigest()[:20]
        row = {
            "interrupt_id": interrupt_id,
            "goal_generation_id": generation,
            "kind": record_kind,
            "state": OPEN,
            "prompt_hash": prompt_hash,
            "summary": _text(prompt),
            "exit_condition": "answer_delivered" if record_kind == QUESTION_ONLY else "assistant_reports_completion_with_evidence",
            "return_checkpoint": _checkpoint(convergence),
            "affected_paths": [],
            "evidence": [],
            "opened_at": now,
            "opened_turn_id": _turn_id(event),
            "last_turn_id": _turn_id(event),
            "closed_at": None,
            "close_reason": None,
            "replay_count": 0,
            "replay_event_ids": [],
            "write_revision": 0,
            "successful_validation_streak": 0,
            "last_validation_write_revision": None,
            "compaction_revision_opened": int(session.get("compaction_revision", 0) or 0),
            "compaction_revision_closed": None,
            "explicit_reopen_of": prior.get("interrupt_id") if prior else None,
        }
        rows = _interrupts(session)
        rows.append(row)
        session["interrupts"] = rows[-MAX_INTERRUPTS:]
        session["active_interrupt_id"] = interrupt_id
        checkpoint = row["return_checkpoint"]
        context = (
            "[Goal Return Guard] Treat this user input as a bounded temporary branch under the active Goal. "
            "Complete only this branch, then return to the stored Goal checkpoint without repeating it after completion. "
            f"Return stage: {checkpoint.get('stage') or 'current Goal stage'}. "
            f"Next action: {checkpoint.get('next_action') or 'next unfinished Goal action'}."
        )
        return context[:MAX_CONTEXT_CHARS], {"classification": kind, "recorded": True, "interrupt_id": interrupt_id}

    try:
        return _update(state_path, lock_path, events_path, north_star, event, "USER_PROMPT", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def _assistant_asks_for_input(message: str) -> bool:
    lower = message.lower().strip()
    return lower.endswith(("?", "？")) or any(marker in lower for marker in _INCOMPLETE_MARKERS)


def _completion_evidence(kind: str, message: str) -> bool:
    if not message.strip() or _assistant_asks_for_input(message):
        return False
    if kind == QUESTION_ONLY:
        return True
    lower = message.lower()
    return any(marker in lower for marker in _COMPLETE_MARKERS)


def on_stop(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
) -> None:
    message = str(event.get("last_assistant_message") or "")

    def mutate(_: dict[str, Any], session: dict[str, Any], __: str | None, now: str) -> tuple[None, dict[str, Any]]:
        active = _active_interrupt(session)
        if not active:
            return None, {"closed": False}
        if event.get("stop_hook_active"):
            return None, {"closed": False, "interrupt_id": active.get("interrupt_id"), "reason": "stop_hook_already_active"}
        active["state"] = CLOSE_CANDIDATE
        no_side_effects = not active.get("affected_paths") and not active.get("evidence")
        if not _completion_evidence(str(active.get("kind") or ""), message) and not (
            no_side_effects and message.strip() and not _assistant_asks_for_input(message)
        ):
            return None, {"closed": False, "interrupt_id": active.get("interrupt_id"), "reason": "completion_not_established"}
        active["state"] = CLOSED
        active["closed_at"] = now
        active["close_reason"] = "answer_delivered" if active.get("kind") == QUESTION_ONLY else "assistant_completion_evidence"
        active["close_evidence_hash"] = hashlib.sha256(message.encode("utf-8")).hexdigest()[:24]
        active["compaction_revision_closed"] = int(session.get("compaction_revision", 0) or 0)
        session["active_interrupt_id"] = None
        return None, {"closed": True, "interrupt_id": active.get("interrupt_id"), "reason": active.get("close_reason")}

    try:
        _update(state_path, lock_path, events_path, north_star, event, "STOP", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return


def on_pre_compact(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
) -> None:
    def mutate(_: dict[str, Any], session: dict[str, Any], __: str | None, now: str) -> tuple[None, dict[str, Any]]:
        session["compaction_revision"] = int(session.get("compaction_revision", 0) or 0) + 1
        session["last_pre_compact_at"] = now
        return None, {"compaction_revision": session["compaction_revision"], "trigger": event.get("trigger")}

    try:
        _update(state_path, lock_path, events_path, north_star, event, "PRE_COMPACT", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return


def on_post_compact(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
) -> None:
    def mutate(_: dict[str, Any], session: dict[str, Any], __: str | None, now: str) -> tuple[None, dict[str, Any]]:
        session["last_post_compact_at"] = now
        return None, {"compaction_revision": int(session.get("compaction_revision", 0) or 0), "trigger": event.get("trigger")}

    try:
        _update(state_path, lock_path, events_path, north_star, event, "POST_COMPACT", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return


def _recovery_text(north_star: dict[str, Any], convergence: dict[str, Any], session: dict[str, Any]) -> str:
    stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
    goal = _text(stack.get("l0_final_goal") or north_star.get("goal")) or "confirmed project North Star"
    stage = _text(stack.get("l2_current_stage")) or "current Goal stage"
    action = _text(stack.get("l3_current_action")) or "next unfinished Goal action"
    active = _active_interrupt(session)
    closed = [
        row for row in reversed(_interrupts(session))
        if row.get("state") == CLOSED and row.get("goal_generation_id") == goal_generation_id(north_star)
    ][:3]
    lines = [
        "[GOAL RETURN CHECKPOINT]",
        f"Active North Star: {goal}",
        f"Current stage: {stage}",
        f"Next unfinished action: {action}",
    ]
    if active:
        lines.extend([
            f"Open temporary branch: {_text(active.get('summary'))}",
            "Finish that branch only until its exit condition is met, then return to the checkpoint above.",
        ])
    elif closed:
        lines.append("Closed temporary branches (do not resume unless the user explicitly reopens one):")
        lines.extend(f"- {_text(row.get('summary'), 180)}" for row in closed)
        lines.append("Continue the current Goal stage. A closed branch remaining in chat history is not an active task.")
    else:
        lines.append("Continue the current Goal stage; do not infer a new active task from stale compacted chat history.")
    return "\n".join(lines)[:MAX_CONTEXT_CHARS]


def on_session_start(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    convergence: dict[str, Any],
    event: dict[str, Any],
) -> str | None:
    if str(event.get("source") or "") != "compact":
        return None

    def mutate(_: dict[str, Any], session: dict[str, Any], generation: str | None, now: str) -> tuple[str | None, dict[str, Any]]:
        if not generation:
            return None, {"recovered": False}
        session["last_compact_recovery_at"] = now
        context = _recovery_text(north_star, convergence, session)
        return context, {
            "recovered": True,
            "compaction_revision": int(session.get("compaction_revision", 0) or 0),
            "active_interrupt_id": session.get("active_interrupt_id"),
        }

    try:
        return _update(state_path, lock_path, events_path, north_star, event, "COMPACT_RECOVERY", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def on_tool_event(
    state_path: Path,
    lock_path: Path,
    events_path: Path,
    north_star: dict[str, Any],
    event: dict[str, Any],
    *,
    paths: list[str],
    category: str,
    failed: bool,
) -> dict[str, Any] | None:
    phase = str(event.get("hook_event_name") or event.get("hookEventName") or "")
    unique_paths = list(dict.fromkeys(_text(path, 300) for path in paths if _text(path, 300)))[:MAX_PATHS]

    def mutate(_: dict[str, Any], session: dict[str, Any], generation: str | None, now: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not generation:
            return None, {"tracked": False}
        active = _active_interrupt(session)
        if active:
            if unique_paths:
                active["affected_paths"] = list(dict.fromkeys([*active.get("affected_paths", []), *unique_paths]))[:MAX_PATHS]
            if phase == "PreToolUse" and category == "write" and unique_paths:
                active["write_revision"] = int(active.get("write_revision", 0) or 0) + 1
                active["successful_validation_streak"] = 0
                active["last_validation_write_revision"] = None
                if active.get("state") == CLOSE_CANDIDATE:
                    active["state"] = OPEN
            if phase == "PostToolUse" and category == "validation":
                active.setdefault("evidence", []).append({"kind": "validation", "passed": not failed, "observed_at": now})
                active["evidence"] = active["evidence"][-8:]
                if failed:
                    active["successful_validation_streak"] = 0
                    active["last_validation_write_revision"] = None
                    active["state"] = OPEN
                elif active.get("affected_paths"):
                    write_revision = int(active.get("write_revision", 0) or 0)
                    if active.get("last_validation_write_revision") == write_revision:
                        streak = int(active.get("successful_validation_streak", 0) or 0) + 1
                    else:
                        streak = 1
                    active["successful_validation_streak"] = streak
                    active["last_validation_write_revision"] = write_revision
                    if streak >= 2:
                        active["state"] = CLOSED
                        active["closed_at"] = now
                        active["close_reason"] = "consecutive_validation_passes_after_write"
                        active["compaction_revision_closed"] = int(session.get("compaction_revision", 0) or 0)
                        active["return_context_emitted"] = True
                        session["active_interrupt_id"] = None
                        checkpoint = active.get("return_checkpoint") if isinstance(active.get("return_checkpoint"), dict) else {}
                        signal = {
                            "signal": "TEMPORARY_BRANCH_EXIT_REACHED",
                            "interrupt_id": active.get("interrupt_id"),
                            "severity": "CONTEXT",
                            "needs_judge": False,
                            "reason": (
                                "[Goal Return Guard] The temporary branch has product writes followed by two "
                                "successful validations with no intervening write. Its exit evidence is satisfied. "
                                "Do not keep revalidating or extending that branch. Return now to the stored Goal "
                                f"stage: {_text(checkpoint.get('stage')) or 'current Goal stage'}; next action: "
                                f"{_text(checkpoint.get('next_action')) or 'next unfinished Goal action'}."
                            ),
                        }
                        return signal, {
                            "tracked": True,
                            "interrupt_id": active.get("interrupt_id"),
                            "transition": CLOSED,
                            "reason": active.get("close_reason"),
                        }
                    active["state"] = CLOSE_CANDIDATE
            return None, {"tracked": True, "interrupt_id": active.get("interrupt_id"), "path_count": len(unique_paths)}
        if phase != "PreToolUse" or not unique_paths:
            return None, {"tracked": False}
        revision = int(session.get("compaction_revision", 0) or 0)
        candidate = None
        overlap: list[str] = []
        for row in reversed(_interrupts(session)):
            if row.get("state") != CLOSED or row.get("goal_generation_id") != generation:
                continue
            closed_revision = int(row.get("compaction_revision_closed", 0) or 0)
            same_turn_return = row.get("close_reason") == "consecutive_validation_passes_after_write"
            if revision < closed_revision or (revision == closed_revision and not same_turn_return):
                continue
            affected = set(str(path) for path in row.get("affected_paths", []))
            current_overlap = [path for path in unique_paths if path in affected]
            if current_overlap:
                candidate, overlap = row, current_overlap
                break
        if not candidate:
            return None, {"tracked": False}
        event_identifier = _event_id(event, phase)
        seen = list(candidate.get("replay_event_ids", []))
        if event_identifier not in seen:
            seen.append(event_identifier)
            candidate["replay_event_ids"] = seen[-8:]
            candidate["replay_count"] = int(candidate.get("replay_count", 0) or 0) + 1
            candidate["last_replay_at"] = now
        count = int(candidate.get("replay_count", 0) or 0)
        signal = {
            "signal": "CLOSED_BRANCH_REPLAY_CANDIDATE",
            "interrupt_id": candidate.get("interrupt_id"),
            "summary": candidate.get("summary"),
            "affected_paths": overlap,
            "replay_count": count,
            "severity": "CONTEXT" if count == 1 else "WARNING" if count == 2 else "REVIEW",
            "needs_judge": count >= 3,
            "reason": (
                "A completed temporary branch appears to be active again after compaction. "
                "Return to the stored Goal checkpoint unless the user explicitly reopened this branch."
            ),
        }
        return signal, {"tracked": True, "interrupt_id": candidate.get("interrupt_id"), "replay_count": count}

    try:
        return _update(state_path, lock_path, events_path, north_star, event, "TOOL_ACTIVITY", mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def compact_status(state_path: Path) -> dict[str, Any]:
    state = load_json(state_path, empty_state())
    sessions = state.get("sessions") if isinstance(state.get("sessions"), dict) else {}
    return {
        "goal_generation_id": (state.get("goal_generation") or {}).get("id"),
        "session_count": len(sessions),
        "open_interrupts": sum(
            1 for session in sessions.values() if isinstance(session, dict)
            for row in session.get("interrupts", []) if isinstance(row, dict) and row.get("state") in {OPEN, CLOSE_CANDIDATE}
        ),
        "closed_interrupts": sum(
            1 for session in sessions.values() if isinstance(session, dict)
            for row in session.get("interrupts", []) if isinstance(row, dict) and row.get("state") == CLOSED
        ),
        "updated_at": state.get("updated_at"),
    }
