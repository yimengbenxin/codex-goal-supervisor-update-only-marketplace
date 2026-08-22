"""Keep completed corrections and temporary requests from becoming active work again."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from goal_compass_runtime.state_store import exclusive_file_lock, load_json, utc_now_iso, write_json


SCHEMA_VERSION = 1
MAX_SESSIONS = 16
MAX_BRANCHES = 16
MAX_CORRECTIONS = 16
MAX_SUMMARY_CHARS = 240
MAX_CONTEXT_CHARS = 1100

_TEMPORARY_MARKERS = (
    "插一句", "临时", "顺便", "先回答", "先处理", "暂停一下", "题外",
    "quick question", "temporary", "for now", "before continuing", "side question", "one-off",
)
_CONTINUE_MARKERS = {
    "继续", "继续执行", "继续推进", "回到主任务", "回到原任务",
    "continue", "continue working", "resume", "go on",
}
_REOPEN_MARKERS = (
    "加回来", "重新加入", "重新加", "重新开放", "恢复", "重新启用", "现在需要", "为什么不", "解释为什么",
    "add it back", "restore", "re-enable", "reopen", "now include", "why not",
)
_RESOLUTION_MARKERS = (
    "去掉", "移除", "删除", "不需要", "没有必要", "不再", "已改为", "已修正",
    "removed", "omitted", "dropped", "unnecessary", "no longer", "corrected",
)
_INCOMPLETE_MARKERS = (
    "尚未", "还没", "未完成", "需要用户", "请提供", "请确认", "等待",
    "not complete", "not finished", "need user", "please provide", "please confirm", "waiting",
)
_GENERIC_TARGETS = {
    "这个", "这个东西", "它", "内容", "东西", "功能", "方案", "代码", "部分",
    "this", "it", "that", "thing", "feature", "code", "content",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
)

_CHINESE_DIRECT = re.compile(
    r"(?:不要|不需要|别再?|去掉|移除|删除|取消)\s*(?:再)?(?:加入|加|保留|提及|写|强调|解释)?\s*[“\"']?([^，。！？?\n]{1,48})",
    re.IGNORECASE,
)
_CHINESE_RHETORICAL = re.compile(
    r"有必要(?:再)?(?:加入|加|保留|提及|写|强调|解释)\s*[“\"']?([^，。！？?\n]{1,48}?)[”\"']?吗(?:[？?]|$)",
    re.IGNORECASE,
)
_ENGLISH_DIRECT = re.compile(
    r"(?:do not|don't|remove|omit|drop|stop mentioning)\s+(?:add|include|keep|mention|explain\s+)?[\"']?([^,.!?\n]{2,48})",
    re.IGNORECASE,
)
_ENGLISH_RHETORICAL = re.compile(
    r"do (?:we|you) (?:really )?need to (?:add|include|keep|mention|explain)\s+[\"']?([^,.!?\n]{2,48})",
    re.IGNORECASE,
)


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sessions": {}, "updated_at": None}


def _clean(value: Any, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def _session_id(event: dict[str, Any]) -> str:
    return _clean(event.get("session_id") or event.get("sessionId") or "local", 160) or "local"


def _turn_id(event: dict[str, Any]) -> str | None:
    value = _clean(event.get("turn_id") or event.get("turnId"), 160)
    return value or None


def _session(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    sessions = state.setdefault("sessions", {})
    session_id = _session_id(event)
    value = sessions.setdefault(session_id, {
        "primary_anchor": None,
        "active_branch_id": None,
        "branches": [],
        "corrections": [],
        "compaction_revision": 0,
    })
    state["last_session_id"] = session_id
    while len(sessions) > MAX_SESSIONS:
        oldest = next((key for key in sessions if key != session_id), None)
        if oldest is None:
            break
        sessions.pop(oldest, None)
    return value


def _mutate(state_path: Path, lock_path: Path, event: dict[str, Any], function: Any) -> Any:
    with exclusive_file_lock(lock_path, timeout=0.25, stale_seconds=30.0):
        state = load_json(state_path, empty_state())
        if not isinstance(state, dict):
            state = empty_state()
        session = _session(state, event)
        result = function(state, session, utc_now_iso())
        state["schema_version"] = SCHEMA_VERSION
        state["updated_at"] = utc_now_iso()
        write_json(state_path, state)
        return result


def _normalize_target(value: str) -> str | None:
    target = _clean(value, 96).strip(" \t\r\n'\"“”‘’：:，,。.!！？?")
    target = re.sub(r"(?:了|吧|呢|即可|就行)$", "", target).strip()
    if "[redacted]" in target or not target or target.casefold() in _GENERIC_TARGETS or len(target) < 2:
        return None
    return target[:48]


def correction_candidate(prompt: str) -> dict[str, Any] | None:
    for pattern, confidence in (
        (_CHINESE_DIRECT, "explicit"),
        (_ENGLISH_DIRECT, "explicit"),
        (_CHINESE_RHETORICAL, "candidate"),
        (_ENGLISH_RHETORICAL, "candidate"),
    ):
        match = pattern.search(prompt)
        if not match:
            continue
        target = _normalize_target(match.group(1))
        if target:
            return {"target": target, "confidence": confidence}
    return None


def _active_branch(session: dict[str, Any]) -> dict[str, Any] | None:
    branch_id = session.get("active_branch_id")
    return next((row for row in reversed(session.get("branches", [])) if row.get("branch_id") == branch_id), None)


def _contains(text: str, target: str) -> bool:
    return bool(target and target.casefold() in text.casefold())


def _explicit_reopen(prompt: str, correction: dict[str, Any]) -> bool:
    return _contains(prompt, str(correction.get("target") or "")) and any(
        marker in prompt.casefold() for marker in _REOPEN_MARKERS
    )


def _looks_like_bounded_question(prompt: str) -> bool:
    value = _clean(prompt, 200)
    if not value or len(value) > 160:
        return False
    lower = value.casefold()
    return value.endswith(("?", "？")) or lower.startswith((
        "为什么", "怎么", "是否", "是不是", "能不能", "可以吗", "确定吗", "这是什么",
        "why ", "how ", "is ", "are ", "can ", "could ", "would ", "do ", "does ",
    ))


def on_user_prompt(
    state_path: Path,
    lock_path: Path,
    event: dict[str, Any],
    *,
    goal_active: bool,
) -> str | None:
    prompt = str(event.get("prompt") or "").strip()
    if not prompt:
        return None

    def mutate(_: dict[str, Any], session: dict[str, Any], now: str) -> str | None:
        corrections = [row for row in session.get("corrections", []) if isinstance(row, dict)]
        candidate = correction_candidate(prompt)
        if candidate:
            target = candidate["target"]
            identifier = hashlib.sha256(target.casefold().encode("utf-8")).hexdigest()[:20]
            prior = next((row for row in reversed(corrections) if row.get("correction_id") == identifier), None)
            row = prior or {
                "correction_id": identifier,
                "target": target,
                "created_at": now,
                "confirmed_at": None,
                "confirmed_turn_id": None,
            }
            row.update({
                "status": "CONFIRMED" if candidate["confidence"] == "explicit" else "PENDING_CONFIRMATION",
                "source_turn_id": _turn_id(event),
                "updated_at": now,
            })
            if row["status"] == "CONFIRMED":
                row["confirmed_at"] = now
                row["confirmed_turn_id"] = _turn_id(event)
                row["skip_next_stop"] = row["confirmed_turn_id"] is None
            if prior is None:
                corrections.append(row)
            session["corrections"] = corrections[-MAX_CORRECTIONS:]
            return (
                "[Instruction Hygiene] Apply the subtraction once and keep the canonical result positive. "
                "After the correction is complete, do not preserve the rejected variant in names, comments, "
                "documentation, or completion summaries unless the user explicitly reopens it."
            )

        for row in reversed(corrections):
            if row.get("status") == "CONFIRMED" and _explicit_reopen(prompt, row):
                row["status"] = "REOPENED_BY_USER"
                row["reopened_at"] = now

        normalized = " ".join(prompt.casefold().split())
        active = _active_branch(session)
        if active:
            active["updated_at"] = now
            active["last_turn_id"] = _turn_id(event)
            return None
        if normalized in _CONTINUE_MARKERS:
            return None
        anchor_exists = isinstance(session.get("primary_anchor"), dict)
        is_temporary = any(marker in normalized for marker in _TEMPORARY_MARKERS) or (
            anchor_exists and _looks_like_bounded_question(prompt)
        )
        if not goal_active and is_temporary and anchor_exists:
            branch_id = hashlib.sha256(
                f"{_session_id(event)}\n{_turn_id(event)}\n{prompt}\n{now}".encode("utf-8")
            ).hexdigest()[:20]
            row = {
                "branch_id": branch_id,
                "summary": _clean(prompt),
                "state": "OPEN",
                "opened_at": now,
                "opened_turn_id": _turn_id(event),
                "closed_at": None,
                "affected_paths": [],
                "compaction_revision_closed": None,
            }
            branches = [value for value in session.get("branches", []) if isinstance(value, dict)]
            branches.append(row)
            session["branches"] = branches[-MAX_BRANCHES:]
            session["active_branch_id"] = branch_id
            anchor = _clean(session["primary_anchor"].get("summary"), 180)
            return (
                "[General Return Guard] Treat this as a bounded temporary request. Complete it once, then resume "
                f"the primary task: {anchor}. Do not make this temporary request the new task after compaction."
            )[:MAX_CONTEXT_CHARS]
        if not goal_active and not is_temporary:
            session["primary_anchor"] = {
                "summary": _clean(prompt),
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24],
                "updated_at": now,
            }
        return None

    try:
        return _mutate(state_path, lock_path, event, mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def on_compact(state_path: Path, lock_path: Path, event: dict[str, Any], *, phase: str) -> None:
    def mutate(_: dict[str, Any], session: dict[str, Any], now: str) -> None:
        if phase == "PreCompact":
            session["compaction_revision"] = int(session.get("compaction_revision", 0) or 0) + 1
        session[f"last_{phase.casefold()}_at"] = now
        return None

    try:
        _mutate(state_path, lock_path, event, mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return


def on_session_start(
    state_path: Path,
    lock_path: Path,
    event: dict[str, Any],
    *,
    goal_active: bool,
) -> str | None:
    if str(event.get("source") or "") != "compact":
        return None

    def mutate(_: dict[str, Any], session: dict[str, Any], now: str) -> str | None:
        session["last_recovered_at"] = now
        active_corrections = [row for row in session.get("corrections", []) if row.get("status") == "CONFIRMED"]
        if goal_active:
            if not active_corrections:
                return None
            return (
                "[Instruction Hygiene] Resolved subtraction corrections remain canonical after compaction. "
                "Stale rejection dialogue is not an active task; do not repeat rejected variants in names, "
                "comments, documentation, or summaries unless the user explicitly reopens them."
            )
        anchor = session.get("primary_anchor") if isinstance(session.get("primary_anchor"), dict) else None
        if not anchor and not active_corrections:
            return None
        branch = _active_branch(session)
        closed = [row for row in session.get("branches", []) if row.get("state") == "CLOSED"]
        lines = ["[GENERAL CONTINUITY CHECKPOINT]"]
        if anchor:
            lines.append("Current primary task: " + _clean(anchor.get("summary"), 220))
        if branch:
            lines.append("Open temporary request: " + _clean(branch.get("summary"), 180))
            lines.append("Finish it once, then resume the primary task.")
        elif closed:
            lines.append("Completed temporary requests are tombstoned; do not resume them from compacted history.")
            lines.append("Continue the current primary task.")
        if active_corrections:
            lines.append(
                "Resolved subtraction corrections remain canonical; stale rejection dialogue must not appear in "
                "names, comments, documentation, or summaries."
            )
        return "\n".join(lines)[:MAX_CONTEXT_CHARS]

    try:
        return _mutate(state_path, lock_path, event, mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def _assistant_waits(message: str) -> bool:
    lower = message.casefold().strip()
    return lower.endswith(("?", "？")) or any(marker in lower for marker in _INCOMPLETE_MARKERS)


def on_stop(state_path: Path, lock_path: Path, event: dict[str, Any]) -> str | None:
    message = str(event.get("last_assistant_message") or event.get("lastAssistantMessage") or "").strip()

    def mutate(_: dict[str, Any], session: dict[str, Any], now: str) -> str | None:
        turn_id = _turn_id(event)
        branch = _active_branch(session)
        if branch and message and not _assistant_waits(message):
            branch["state"] = "CLOSED"
            branch["closed_at"] = now
            branch["compaction_revision_closed"] = int(session.get("compaction_revision", 0) or 0)
            session["active_branch_id"] = None

        corrections = [row for row in session.get("corrections", []) if isinstance(row, dict)]
        confirmed_before_stop = {
            str(row.get("correction_id") or "")
            for row in corrections
            if row.get("status") == "CONFIRMED"
        }
        skip_this_stop: set[str] = set()
        for row in corrections:
            correction_id = str(row.get("correction_id") or "")
            if correction_id in confirmed_before_stop and row.pop("skip_next_stop", False):
                skip_this_stop.add(correction_id)
        for row in corrections:
            if row.get("status") != "PENDING_CONFIRMATION":
                continue
            if _contains(message, str(row.get("target") or "")) and any(
                marker in message.casefold() for marker in _RESOLUTION_MARKERS
            ):
                row["status"] = "CONFIRMED"
                row["confirmed_at"] = now
                row["confirmed_turn_id"] = turn_id

        residue = next((
            row for row in reversed(corrections)
            if row.get("status") == "CONFIRMED"
            and str(row.get("correction_id") or "") in confirmed_before_stop
            and str(row.get("correction_id") or "") not in skip_this_stop
            and (row.get("confirmed_turn_id") is None or row.get("confirmed_turn_id") != turn_id)
            and _contains(message, str(row.get("target") or ""))
        ), None)
        if residue:
            residue["last_residue_at"] = now
            residue["residue_count"] = int(residue.get("residue_count", 0) or 0) + 1
            return (
                "A resolved rejected variant was repeated in the outgoing result. Rewrite only the canonical "
                "positive result; remove the rejected variant and its explanatory residue from names, comments, "
                "documentation, and the completion summary."
            )
        return None

    try:
        return _mutate(state_path, lock_path, event, mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None


def _added_artifact_content(event: dict[str, Any], paths: list[str]) -> str:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    if not isinstance(value, dict):
        return ""
    command = str(value.get("command") or value.get("cmd") or "")
    if command and any(marker in command.casefold() for marker in ("gh pr create", "git commit", "git tag")):
        return command
    tool = str(event.get("tool_name") or event.get("toolName") or "").casefold()
    if any(marker in tool for marker in ("pull_request", "pullrequest", "create_pr", "commit", "create_tag")):
        return "\n".join(str(value.get(key) or "") for key in ("title", "body", "message", "description", "name"))
    patch = str(value.get("patch") or "")
    if patch and paths:
        return "\n".join(
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
    if not paths:
        return ""
    return "\n".join(
        str(value.get(key) or "")
        for key in ("content", "text", "body", "title", "message", "new_string")
    )


def on_pre_tool(
    state_path: Path,
    lock_path: Path,
    event: dict[str, Any],
    *,
    paths: list[str],
) -> dict[str, Any] | None:
    produced = _added_artifact_content(event, paths)
    if not produced:
        return None

    def mutate(_: dict[str, Any], session: dict[str, Any], now: str) -> dict[str, Any] | None:
        residue = next((
            row for row in reversed(session.get("corrections", []))
            if row.get("status") == "CONFIRMED" and _contains(produced, str(row.get("target") or ""))
        ), None)
        if not residue:
            return None
        residue["last_residue_at"] = now
        residue["residue_count"] = int(residue.get("residue_count", 0) or 0) + 1
        return {
            "signal": "CORRECTION_RESIDUE",
            "deny": True,
            "reason": (
                "A resolved rejected variant is being reintroduced into a product or publication artifact. "
                "Write only the canonical positive result; use an explicit user reopen before restoring the "
                "rejected variant."
            ),
        }

    try:
        return _mutate(state_path, lock_path, event, mutate)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None
