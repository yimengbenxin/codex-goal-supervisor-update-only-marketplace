"""Lightweight project hook for Codex Goal Supervisor.

Normal project work is observed here without importing the full Goal Compass
runtime. An explicitly ACTIVE ticket delegates to the full contract hook.
"""
from __future__ import annotations

import fnmatch
import functools
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

AGENT_IMPORT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_IMPORT_ROOT))

from goal_compass_runtime.hook_rules import destructive_git_command, shell_segments, shell_write_targets, tool_failed
from goal_compass_runtime.deviation_incidents import (
    alignment_policy_sources,
    build_context as build_deviation_context,
    north_star_policies,
)
from goal_compass_runtime.observer import (
    apply_observation,
    apply_pending_events,
    empty_state,
    finalize_pending_events,
    observation_event,
    persist_recent_events,
    queue_pending_event,
)
from goal_compass_runtime.state_store import exclusive_file_lock, load_json, utc_now_iso, write_json
from goal_compass_runtime.convergence import (
    apply_observation as apply_convergence_observation,
    empty_state as empty_convergence_state,
    external_prerequisite_stop_review,
    record_blocker_scope_review,
    refresh as refresh_convergence_state,
)
from goal_compass_runtime.llm_judge import invoke as invoke_llm_judge
from goal_compass_runtime.context_continuity import (
    is_read_event as is_context_read_event,
    post_compact as record_post_compact,
    record_read as record_context_read,
    recovery_context,
    seal_before_compact,
    subagent_context,
)
from goal_compass_runtime.goal_return import (
    goal_change_candidate,
    goal_change_response,
    on_post_compact as goal_return_post_compact,
    on_pre_compact as goal_return_pre_compact,
    on_session_start as goal_return_session_start,
    on_stop as goal_return_stop,
    on_tool_event as goal_return_tool_event,
    on_user_prompt as goal_return_user_prompt,
    record_goal_change_confirmation,
    resolve_goal_change_confirmation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT = PROJECT_ROOT / ".agent"
CURRENT = AGENT / "current_ticket.json"
NORTH_STAR = AGENT / "north_star_goal.json"
VALIDATION_CATALOG = AGENT / "validation_catalog.json"
TOOL_MODE = AGENT / "tool_mode.json"
PROGRAM_PHASE = AGENT / "program_phase.json"
OBSERVER_STATE = AGENT / "runtime" / "observer_state.json"
OBSERVER_LOCK = AGENT / "runtime" / "observer_state.lock"
OBSERVER_EVENTS = AGENT / "runtime" / "observer_events.jsonl"
OBSERVER_PENDING = AGENT / "runtime" / "observer_pending"
CONVERGENCE_STATE = AGENT / "runtime" / "convergence_state.json"
CONVERGENCE_LOCK = AGENT / "runtime" / "convergence_state.lock"
LLM_JUDGE_CACHE = AGENT / "runtime" / "llm_judge_cache.json"
LLM_JUDGE_SCHEMA = AGENT / "protocols" / "llm_judge.schema.json"
CONTEXT_STATE = AGENT / "runtime" / "context_continuity.json"
CONTEXT_LOCK = AGENT / "runtime" / "context_continuity.lock"
CONTEXT_CAPSULE = AGENT / "runtime" / "context" / "index.json"
GOAL_RETURN_STATE = AGENT / "runtime" / "goal_return" / "state.json"
GOAL_RETURN_LOCK = AGENT / "runtime" / "goal_return" / "state.lock"
GOAL_RETURN_EVENTS = AGENT / "runtime" / "goal_return" / "events.jsonl"
FULL_COMPASS = AGENT / "goal_compass.py"

CONTROL_PATTERNS = (
    ".agent/current_ticket.json",
    ".agent/north_star_goal.json",
    ".agent/validation_catalog.json",
    ".agent/prune_plan.json",
    ".agent/tool_mode.json",
)


def norm(path: str) -> str:
    value = path.replace("\\", "/").strip().strip("'\"")
    if os.path.isabs(value):
        try:
            value = str(Path(value).resolve().relative_to(PROJECT_ROOT.resolve()))
        except ValueError:
            return "__outside_repo__/" + value.lstrip("/")
    while value.startswith("./"):
        value = value[2:]
    return os.path.normpath(value).replace("\\", "/")


def match_path(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    value = norm(path)
    for raw in patterns:
        pattern = norm(str(raw))
        if pattern.endswith("/**") and (value == pattern[:-3] or value.startswith(pattern[:-2])):
            return True
        if fnmatch.fnmatch(value, pattern):
            return True
    return False


def event_name(event: dict[str, Any]) -> str:
    return str(event.get("hook_event_name") or event.get("hookEventName") or "")


def tool_input(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    return value if isinstance(value, dict) else {}


def command_text(event: dict[str, Any]) -> str:
    value = tool_input(event)
    return str(value.get("command") or value.get("cmd") or "")


def extract_patch_paths(text: str) -> list[str]:
    return [
        norm(match.group(1).strip())
        for match in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", text, re.MULTILINE)
    ]


def shell_write_paths(command: str) -> list[str]:
    paths = [*extract_patch_paths(command), *shell_write_targets(command)]
    return [norm(path) for path in paths if path]


def generic_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"path", "file", "target", "destination", "output"} and isinstance(item, str):
                paths.append(norm(item))
            else:
                paths.extend(generic_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(generic_paths(item))
    return paths


def write_paths(event: dict[str, Any]) -> list[str]:
    tool = str(event.get("tool_name") or event.get("toolName") or "").lower()
    value = tool_input(event)
    patch = str(value.get("patch") or value.get("command") or "")
    if "apply_patch" in tool:
        paths = extract_patch_paths(patch)
    elif tool in {"bash", "shell", "exec_command", "terminal"}:
        paths = shell_write_paths(command_text(event))
    elif any(word in tool for word in ("write", "edit", "patch", "delete", "remove", "move", "create", "update")):
        paths = [*extract_patch_paths(patch), *generic_paths(value)]
    else:
        paths = []
    return list(dict.fromkeys(path for path in paths if path))


def failed(event: dict[str, Any]) -> bool:
    return tool_failed(event)


def _canonical_command_tokens(tokens: list[str]) -> tuple[str, ...]:
    canonical: list[str] = []
    for token in tokens:
        value = str(token).strip()
        if not value:
            continue
        base = os.path.basename(value.replace("\\", "/")).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if value == "{python}" or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", base):
            canonical.append("{python}")
            continue
        if os.path.isabs(value):
            try:
                value = str(Path(value).resolve().relative_to(PROJECT_ROOT.resolve()))
            except (OSError, ValueError):
                pass
        canonical.append(value.replace("\\", "/"))
    return tuple(canonical)


@functools.lru_cache(maxsize=1)
def catalog_validation_commands() -> tuple[tuple[str, ...], ...]:
    try:
        payload = json.loads(VALIDATION_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    commands: list[tuple[str, ...]] = []
    if not isinstance(payload, dict):
        return ()
    for row in payload.values():
        if not isinstance(row, dict):
            continue
        command = str(row.get("cmd") or row.get("command") or "").strip()
        if not command:
            continue
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            continue
        canonical = _canonical_command_tokens(tokens)
        if canonical:
            commands.append(canonical)
    return tuple(dict.fromkeys(commands))


def command_matches_validation_catalog(command: str) -> bool:
    registered = set(catalog_validation_commands())
    if not registered:
        return False
    for segment in shell_segments(command):
        if _canonical_command_tokens(segment) in registered:
            return True
    return False


def category(event: dict[str, Any], paths: list[str]) -> str:
    tool = str(event.get("tool_name") or event.get("toolName") or "").lower()
    command = command_text(event).lower()
    if any(term in tool for term in ("agent", "subagent", "spawn")):
        return "agent"
    if any(term in tool for term in ("mcp", "browser", "web", "chrome", "computer")):
        return "external"
    validation_terms = (
        "pytest", "unittest", "py_compile", "compileall", "ruff check", "mypy", "pyright",
        "npm test", "npm run test", "npm run build", "pnpm test", "pnpm run test", "pnpm build",
        "yarn test", "yarn build", "cargo test", "cargo check", "go test", "eslint", "tsc --noemit",
    )
    if any(term in command for term in validation_terms):
        return "validation"
    if command and command_matches_validation_catalog(command_text(event)):
        return "validation"
    return "write" if paths else "read"


_COMPLETION_MARKERS = (
    "已完成", "已经完成", "修复完成", "实现完成", "交付完成", "全部通过", "均已通过",
    "验证通过", "回归通过", "all tests pass", "all tests passed", "completed", "done",
    "implemented and verified", "fixed and tested", "delivered",
)
_INCOMPLETE_MARKERS = (
    "未完成", "尚未完成", "没有完成", "仍在进行", "还在进行", "等待验证", "尚未验证",
    "不能声称完成", "not complete", "not completed", "incomplete", "still working",
    "needs validation", "not yet verified", "not done",
)
_GOAL_COMPLETION_MARKERS = (
    "北极星目标已完成", "整个项目已完成", "全部目标已完成", "最终交付完成",
    "north star is complete", "entire project is complete", "all goals complete", "final delivery complete",
)


def _assistant_message(event: dict[str, Any]) -> str:
    return str(event.get("last_assistant_message") or event.get("lastAssistantMessage") or "").strip()


def _claims_completion(message: str) -> bool:
    lower = message.lower()
    if not lower or any(marker in lower for marker in _INCOMPLETE_MARKERS):
        return False
    return any(marker in lower for marker in _COMPLETION_MARKERS)


def _claims_goal_completion(message: str) -> bool:
    lower = message.lower()
    return bool(lower) and not any(marker in lower for marker in _INCOMPLETE_MARKERS) and any(
        marker in lower for marker in _GOAL_COMPLETION_MARKERS
    )


def stop_completion_context(event: dict[str, Any]) -> str | None:
    """Return one low-noise completion reminder when evidence is missing."""
    if event.get("stop_hook_active"):
        return None
    message = _assistant_message(event)
    goal_claim = _claims_goal_completion(message)
    action_claim = _claims_completion(message)
    if not goal_claim and not action_claim:
        return None

    north = load_json(NORTH_STAR, {})
    convergence = load_json(CONVERGENCE_STATE, empty_convergence_state())
    completion = convergence.get("goal_completion") if isinstance(convergence.get("goal_completion"), dict) else {}
    if goal_claim and north.get("confirmed") and completion.get("status") != "CERTIFIED_COMPLETE":
        return (
            "[Goal completion check] The response claims the confirmed North Star or final project is complete, "
            "but no current CERTIFIED_COMPLETE final-regression certificate exists. Run the project-level final "
            "regression through `convergence --certify-goal --final-validation-id <catalog-id>` before making the "
            "completion claim."
        )

    try:
        with exclusive_file_lock(OBSERVER_LOCK, timeout=0.2, stale_seconds=30.0):
            state = load_json(OBSERVER_STATE, empty_state())
            debt = state.get("verification_debt") if isinstance(state.get("verification_debt"), dict) else {}
            if not debt.get("pending"):
                return None
            fingerprint = hashlib.sha256(json.dumps({
                "write_started_at": debt.get("write_started_at"),
                "write_paths": debt.get("write_paths") or [],
                "goal_claim": goal_claim,
            }, sort_keys=True).encode("utf-8")).hexdigest()[:24]
            if debt.get("last_reminded_fingerprint") == fingerprint:
                return None
            debt["last_reminded_fingerprint"] = fingerprint
            state["verification_debt"] = debt
            write_json(OBSERVER_STATE, state)
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None

    paths = ", ".join(str(path) for path in (debt.get("write_paths") or [])[:4])
    if debt.get("validation_started_at") and not debt.get("validation_result_observed"):
        reason = "A validation command was started after the product write, but its successful result was not observed."
    elif debt.get("validation_failed_at"):
        reason = "The latest observed validation after the product write failed."
    else:
        reason = "Product writes were observed without a later successful validation."
    suffix = f" Affected paths: {paths}." if paths else ""
    return (
        "[Verification required before completion] " + reason + suffix
        + " Run the smallest relevant deterministic check, then report completion only after it passes."
    )


def stop_stall_recovery(event: dict[str, Any]) -> str | None:
    """Force one bounded Goal-wide review before an external prerequisite stops work."""
    north = load_json(NORTH_STAR, {})
    if not north.get("confirmed"):
        return None
    observed_at = utc_now_iso()
    state = refresh_convergence_state(
        load_json(CONVERGENCE_STATE, empty_convergence_state()),
        north_star=north,
        phase=load_json(PROGRAM_PHASE, {}),
        ticket=load_json(CURRENT, {}),
        updated_at=observed_at,
    )
    review = external_prerequisite_stop_review(
        state,
        _assistant_message(event),
        stop_hook_active=bool(event.get("stop_hook_active")),
    )
    if not review.get("should_continue"):
        return None
    state = record_blocker_scope_review(state, review=review, observed_at=observed_at)
    try:
        with exclusive_file_lock(CONVERGENCE_LOCK, timeout=0.2, stale_seconds=30.0):
            write_json(CONVERGENCE_STATE, state)
    except (OSError, RuntimeError, json.JSONDecodeError):
        pass
    return str(review.get("reason") or "").strip() or None


def output(
    *,
    deny: str | None = None,
    advisory: str | None = None,
    context: str | None = None,
    stop_block: str | None = None,
    hook_event_name: str | None = None,
) -> None:
    if stop_block:
        print(json.dumps({"decision": "block", "reason": stop_block}, ensure_ascii=False))
        return
    event = hook_event_name or ("PreToolUse" if deny or advisory else "PostToolUse")
    if deny:
        payload = {"hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": deny}
    elif advisory:
        payload = {"hookEventName": event, "additionalContext": "Codex Goal Supervisor reminder: " + advisory}
    elif context:
        payload = {"hookEventName": event, "additionalContext": context}
    else:
        return
    print(json.dumps({"hookSpecificOutput": payload}, ensure_ascii=False))


def handle_context_event(event: dict[str, Any]) -> str | None:
    phase = event_name(event)
    north = load_json(NORTH_STAR, {})
    convergence = load_json(CONVERGENCE_STATE, empty_convergence_state())
    if phase == "SessionStart":
        contexts = [
            recovery_context(PROJECT_ROOT, CONTEXT_STATE, CONTEXT_LOCK, CONTEXT_CAPSULE, event),
            goal_return_session_start(
                GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS,
                north, convergence, event,
            ),
        ]
        return "\n\n".join(value for value in contexts if value) or None
    if phase == "SubagentStart":
        return subagent_context(PROJECT_ROOT, CONTEXT_STATE, CONTEXT_CAPSULE, event)
    if phase == "UserPromptSubmit":
        goal_change = goal_change_confirmation_context(north, convergence, event)
        if goal_change:
            return goal_change
        return goal_return_user_prompt(
            GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS,
            north, convergence, event,
        )
    if phase == "Stop":
        goal_return_stop(GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS, north, event)
        return None
    if phase == "PreCompact":
        seal_before_compact(PROJECT_ROOT, CONTEXT_STATE, CONTEXT_LOCK, CONTEXT_CAPSULE, event)
        goal_return_pre_compact(GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS, north, event)
        return None
    if phase == "PostCompact":
        record_post_compact(CONTEXT_STATE, CONTEXT_LOCK, event)
        goal_return_post_compact(GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS, north, event)
        return None
    if phase == "PostToolUse":
        if is_context_read_event(event):
            return record_context_read(PROJECT_ROOT, CONTEXT_STATE, CONTEXT_LOCK, CONTEXT_CAPSULE, event)
    return None


def goal_change_confirmation_context(
    north: dict[str, Any],
    convergence: dict[str, Any],
    event: dict[str, Any],
) -> str | None:
    """Ask once before changing a confirmed long-running project direction."""
    prompt = str(event.get("prompt") or "").strip()
    response = goal_change_response(prompt)
    if response:
        resolved = resolve_goal_change_confirmation(
            GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS,
            north, event, response,
        )
        if not resolved:
            return None
        if response == "DISMISSED":
            return (
                "[Goal Direction Check] The user declined the proposed North Star change. "
                "Preserve the current North Star and detailed Goal contract; treat the request only within the scope the user confirmed."
            )
        return (
            "[Goal Direction Check] The user confirmed a durable direction change. Before continuing implementation, "
            "rebuild the concise North Star and the detailed Goal-mode contract together from the confirmed direction, "
            "then use `goal-set --replace-existing --require-detailed`. Do not leave the old Goal contract attached to the new North Star."
        )

    candidate = goal_change_candidate(north, convergence, prompt)
    if not candidate:
        return None
    result: dict[str, Any] = {}
    confirmed = bool(candidate.get("explicit"))
    if not confirmed:
        if os.environ.get("GOAL_SUPERVISOR_DISABLE_LLM_JUDGE") == "1":
            return None
        stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
        result = invoke_llm_judge(
            {
                "trigger": "possible_north_star_change",
                "north_star_goal": north.get("goal"),
                "goal_contract": stack.get("goal_contract"),
                "success_criteria": [
                    row.get("criterion") for row in stack.get("l1_success_criteria", [])
                    if isinstance(row, dict) and row.get("criterion")
                ],
                "current_stage": stack.get("l2_current_stage"),
                "current_action": stack.get("l3_current_action"),
                "appeal": "Latest user request: " + str(candidate.get("summary") or ""),
                "consequence": "A false prompt creates process noise; a missed durable change leaves North Star and Goal mode stale.",
            },
            schema_path=LLM_JUDGE_SCHEMA,
            cache_path=LLM_JUDGE_CACHE,
            timeout_seconds=8.0,
        )
        confirmed = result.get("verdict") == "CONFIRM_GOAL_CHANGE" and result.get("confidence") == "high"
    if not confirmed:
        return None
    first = record_goal_change_confirmation(
        GOAL_RETURN_STATE, GOAL_RETURN_LOCK, GOAL_RETURN_EVENTS,
        north, event, candidate, result,
    )
    if not first:
        return None
    summary = str(candidate.get("summary") or "")
    if re.search(r"[\u4e00-\u9fff]", prompt):
        return (
            "[Goal Direction Check] 这条需求明显可能形成现有北极星之外的新长期方向："
            f"{summary} 请只向用户确认一次：是否确认更新北极星指标？"
            "用户确认前不得修改 North Star 或详细 Goal；确认后必须同步重建两者，不能只改一句北极星。"
        )
    return (
        "[Goal Direction Check] This request clearly may establish a durable direction outside the current North Star: "
        f"{summary} Ask the user once whether to update the North Star. Do not change the North Star or detailed Goal "
        "before confirmation; after confirmation, rebuild both together."
    )


def deviation_context(event: dict[str, Any], paths: list[str]) -> dict[str, Any] | None:
    north = load_json(NORTH_STAR, {})
    if not north.get("confirmed"):
        return None
    sources = alignment_policy_sources(north)
    return build_deviation_context(
        north_star_goal=str(north.get("goal") or ""),
        policies=north_star_policies(north),
        tool_input=tool_input(event),
        paths=paths,
        policy_sources=sources,
    )


def observe(event: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    phase = event_name(event)
    paths = write_paths(event)
    kind = category(event, paths)
    event_identifier = next(
        (str(event.get(key)) for key in ("tool_use_id", "toolUseId", "event_id", "eventId", "call_id", "callId") if event.get(key)),
        uuid.uuid4().hex,
    )
    row = observation_event(
        event_id=f"{phase}:{event_identifier}",
        phase=phase,
        category=kind,
        paths=paths,
        failed=failed(event) if phase == "PostToolUse" else False,
        observed_at=utc_now_iso(),
    )
    if phase == "PreToolUse" and paths:
        context = deviation_context(event, paths)
        if context:
            row["deviation_context"] = context
    signals: list[dict[str, Any]] = []
    processed_pending: list[Path] = []
    try:
        with exclusive_file_lock(OBSERVER_LOCK, timeout=1.0, stale_seconds=30.0):
            state = load_json(OBSERVER_STATE, empty_state())
            state, pending_signals, processed_pending = apply_pending_events(state, OBSERVER_PENDING)
            state, current_signals = apply_observation(state, row)
            signals = [*current_signals, *pending_signals]
            write_json(OBSERVER_STATE, state)
            persist_recent_events(OBSERVER_EVENTS, state)
            finalize_pending_events(processed_pending, OBSERVER_PENDING)
    except (OSError, RuntimeError, json.JSONDecodeError):
        queued = queue_pending_event(OBSERVER_PENDING, row)
        return [], {"paths": paths, "category": kind, "fallback_queued": queued}
    try:
        with exclusive_file_lock(CONVERGENCE_LOCK, timeout=0.2, stale_seconds=30.0):
            convergence = refresh_convergence_state(
                load_json(CONVERGENCE_STATE, empty_convergence_state()),
                north_star=load_json(NORTH_STAR, {}),
                phase=load_json(PROGRAM_PHASE, {}),
                ticket=load_json(CURRENT, {}),
                updated_at=str(row.get("ts") or utc_now_iso()),
            )
            convergence = apply_convergence_observation(convergence, row)
            write_json(CONVERGENCE_STATE, convergence)
    except (OSError, RuntimeError, json.JSONDecodeError):
        pass
    return signals, {"paths": paths, "category": kind}


def semantic_judgment(signal: dict[str, Any]) -> dict[str, Any]:
    """Require sparse model confirmation before a semantic targeted rail."""
    if os.environ.get("GOAL_SUPERVISOR_DISABLE_LLM_JUDGE") == "1":
        return signal
    if signal.get("signal") not in {"NORTH_STAR_DEVIATION", "GOAL_CONTRACT_DEVIATION"}:
        return signal
    status = str(signal.get("status") or "")
    strike = int(signal.get("strike_count", 0) or 0)
    if status not in {"CORRECTION_REQUIRED", "RAIL_ENFORCED"} or strike < 2:
        return signal
    state = refresh_convergence_state(
        load_json(CONVERGENCE_STATE, empty_convergence_state()),
        north_star=load_json(NORTH_STAR, {}),
        phase=load_json(PROGRAM_PHASE, {}),
        ticket=load_json(CURRENT, {}),
        updated_at=utc_now_iso(),
        current_action="Write under " + ", ".join(signal.get("affected_path_roots", [])[:6]),
    )
    stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
    packet = {
        "trigger": "pending_targeted_rail",
        "north_star_goal": stack.get("l0_final_goal"),
        "goal_contract": stack.get("goal_contract"),
        "alignment_layer": signal.get("alignment_layer"),
        "success_criteria": [
            row.get("criterion") for row in stack.get("l1_success_criteria", [])
            if isinstance(row, dict) and row.get("criterion") is not None
        ],
        "current_stage": stack.get("l2_current_stage"),
        "current_action": stack.get("l3_current_action"),
        "expected_evidence": stack.get("l3_expected_evidence"),
        "observed_evidence": [
            {"kind": row.get("kind"), "summary": row.get("summary")}
            for row in state.get("evidence", [])[-12:] if isinstance(row, dict)
        ],
        "policy_boundary": signal.get("policy"),
        "affected_paths": list(signal.get("affected_path_roots") or [])[:16],
        "consequence": (
            "A false rail delays aligned work; a missed rail permits repeated explicit Goal-contract deviation."
            if signal.get("signal") == "GOAL_CONTRACT_DEVIATION"
            else "A false rail delays aligned work; a missed rail permits repeated North Star deviation."
        ),
    }
    result = invoke_llm_judge(
        packet,
        schema_path=LLM_JUDGE_SCHEMA,
        cache_path=LLM_JUDGE_CACHE,
    )
    state.setdefault("judge", {})["last_result"] = {
        key: result.get(key)
        for key in (
            "status", "verdict", "confidence", "rationale", "recommended_action",
            "evidence_needed", "fingerprint",
        )
    }
    state["judge"]["pending"] = None
    try:
        with exclusive_file_lock(CONVERGENCE_LOCK, timeout=0.2, stale_seconds=30.0):
            write_json(CONVERGENCE_STATE, state)
    except (OSError, RuntimeError):
        pass
    reviewed = dict(signal)
    confirmed = result.get("verdict") == "CONFIRM_TARGETED_RAIL" and result.get("confidence") == "high"
    if status == "RAIL_ENFORCED" and not confirmed:
        reviewed["deny"] = False
        reviewed["intervention"] = "STRONG_WARNING"
        reviewed["recommended_action"] = result.get("recommended_action") or "return_to_alignment_target_or_add_evidence"
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " LLM Judge did not confirm a targeted rail at high confidence; execution remains available. "
            + str(result.get("rationale") or "")
        ).strip()
    elif status == "RAIL_ENFORCED":
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " Sparse LLM Judge confirmed the scoped rail at high confidence. "
            + str(result.get("rationale") or "")
        ).strip()
    else:
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " Sparse LLM Judge: "
            + str(result.get("verdict") or "INSUFFICIENT_EVIDENCE")
            + ". "
            + str(result.get("rationale") or "")
        ).strip()
    reviewed["llm_judge"] = {
        "status": result.get("status"),
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "fingerprint": result.get("fingerprint"),
    }
    return reviewed


def goal_return_judgment(signal: dict[str, Any]) -> dict[str, Any]:
    """Escalate only a third evidence-backed closed-branch replay candidate."""
    reviewed = dict(signal)
    reviewed["deny"] = False
    if not signal.get("needs_judge") or os.environ.get("GOAL_SUPERVISOR_DISABLE_LLM_JUDGE") == "1":
        return reviewed
    convergence = refresh_convergence_state(
        load_json(CONVERGENCE_STATE, empty_convergence_state()),
        north_star=load_json(NORTH_STAR, {}),
        phase=load_json(PROGRAM_PHASE, {}),
        ticket=load_json(CURRENT, {}),
        updated_at=utc_now_iso(),
        current_action="Write under " + ", ".join(signal.get("affected_paths", [])[:6]),
    )
    stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
    result = invoke_llm_judge(
        {
            "trigger": "closed_temporary_branch_replay_after_compaction",
            "north_star_goal": stack.get("l0_final_goal"),
            "goal_contract": stack.get("goal_contract"),
            "current_stage": stack.get("l2_current_stage"),
            "current_action": stack.get("l3_current_action"),
            "expected_evidence": stack.get("l3_expected_evidence"),
            "policy_boundary": "A CLOSED temporary branch is not active unless the user explicitly reopens it.",
            "affected_paths": signal.get("affected_paths", []),
            "appeal": "Closed branch summary: " + str(signal.get("summary") or ""),
            "consequence": "A false rail delays aligned work; a missed rail lets stale compacted history replace the active Goal.",
        },
        schema_path=LLM_JUDGE_SCHEMA,
        cache_path=LLM_JUDGE_CACHE,
    )
    confirmed = result.get("verdict") == "CONFIRM_TARGETED_RAIL" and result.get("confidence") == "high"
    reviewed["deny"] = confirmed
    reviewed["llm_judge"] = {
        "status": result.get("status"),
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "fingerprint": result.get("fingerprint"),
    }
    if confirmed:
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " Sparse LLM Judge confirmed at high confidence that this write resumes a closed temporary branch."
        ).strip()
    else:
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " Semantic review did not justify a hard rail; execution remains available."
        ).strip()
    return reviewed


def destructive_git(command: str) -> str | None:
    return destructive_git_command(command)


def delegate_full(raw: str) -> int:
    try:
        result = subprocess.run(
            [sys.executable, str(FULL_COMPASS), "hook"],
            cwd=str(PROJECT_ROOT),
            input=raw,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=14,
        )
    except (OSError, subprocess.TimeoutExpired):
        output(context="Observer contract runtime was unavailable; execution continues.")
        return 0
    if result.returncode != 0:
        output(context="Observer contract runtime returned an error; execution continues.")
        return 0
    if result.stdout:
        sys.stdout.write(result.stdout)
    return 0


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    phase = event_name(event)
    context_message = handle_context_event(event)
    if phase in {"PreCompact", "PostCompact"}:
        return 0
    if context_message:
        output(context=context_message, hook_event_name=phase)
    if phase == "Stop":
        stall_recovery = stop_stall_recovery(event)
        reminder = stop_completion_context(event) if not stall_recovery else None
        if stall_recovery:
            output(stop_block=stall_recovery)
        elif reminder:
            output(context=reminder, hook_event_name="Stop")
        else:
            print("{}")
        return 0
    if phase in {"SessionStart", "SubagentStart", "UserPromptSubmit"}:
        return 0

    ticket = load_json(CURRENT, {})
    paths = write_paths(event)
    kind = category(event, paths)
    goal_return_signal = goal_return_tool_event(
        GOAL_RETURN_STATE,
        GOAL_RETURN_LOCK,
        GOAL_RETURN_EVENTS,
        load_json(NORTH_STAR, {}),
        event,
        paths=paths,
        category=kind,
        failed=failed(event) if phase == "PostToolUse" else False,
    )
    if ticket.get("status") == "ACTIVE":
        return delegate_full(raw)

    signals, metadata = observe(event)
    signals = [semantic_judgment(signal) for signal in signals]
    if goal_return_signal:
        signals.insert(0, goal_return_judgment(goal_return_signal))
    if phase == "PreToolUse":
        command = command_text(event)
        destructive = destructive_git(command)
        if destructive:
            output(deny=f"Destructive git {destructive} is blocked; use an explicit reviewed recovery action.")
            return 0
        controls = [path for path in metadata["paths"] if match_path(path, CONTROL_PATTERNS)]
        if controls:
            output(deny="Goal Supervisor control state can only be changed through its CLI: " + ", ".join(controls[:8]))
            return 0
        if signals:
            first = signals[0]
            if first.get("deny"):
                output(deny=str(first.get("reason") or "This wrong-direction write is blocked."))
            elif first.get("severity") == "CONTEXT":
                output(context=str(first.get("reason") or "Return to the current Goal checkpoint."), hook_event_name=phase)
            else:
                output(advisory=str(first.get("reason") or "Review the current operation."))
    elif phase == "PostToolUse":
        if signals:
            output(context=str(signals[0].get("reason") or "Review the current operation."))
    return 0


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    raise SystemExit(main())
