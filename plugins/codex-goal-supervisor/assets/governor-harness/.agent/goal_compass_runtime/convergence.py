"""Project-scoped execution convergence state for Codex Goal Supervisor.

The projection distinguishes activity from evidence-backed progress. It is
bounded, cheap to update from hooks, and disposable with the project.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = "1.1"
MAX_ITERATIONS = 64
MAX_EVIDENCE = 128
MAX_COLLABORATION_ROUNDS = 32
MAX_GOAL_MODULES = 16
MAX_GOAL_LIST_ITEMS = 8
MAX_GOAL_TEXT = 360
MAX_GOAL_COVERAGE_LABELS = 8
MAX_SEGMENT_HISTORY = 64
MAX_GOAL_HISTORY = 16
COLLABORATION_PROGRESS_TRANSITIONS = {
    "BLOCKED_WITH_EVIDENCE",
    "DELIVERED",
    "IMPLEMENTED",
    "REVERTED_WITH_EVIDENCE",
    "VALIDATED",
}


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "goal_stack": {
            "l0_final_goal": None,
            "goal_identity_sha256": None,
            "l1_success_criteria": [],
            "l2_current_stage": None,
            "l3_current_action": None,
            "l3_expected_evidence": None,
            "goal_contract": {
                "objective": None,
                "current_state": None,
                "desired_state": None,
                "source_requirements": [],
                "first_principles": [],
                "modules": [],
                "completed_program_phases": [],
                "module_count_total": 0,
                "projection_truncated": False,
                "deliverables": [],
                "final_acceptance": [],
                "constraints": [],
                "non_goals": [],
                "execution_plan_ref": None,
            },
        },
        "activity": {
            "events": 0,
            "writes": 0,
            "validations": 0,
            "failed_events": 0,
            "last_activity_at": None,
        },
        "progress": {
            "evidence_count": 0,
            "completed_criteria": [],
            "last_evidence_at": None,
            "last_progress_at": None,
            "no_progress_iterations": 0,
        },
        "evidence": [],
        "iterations": [],
        "collaboration": {
            "rounds": [],
            "no_evidence_rounds": 0,
            "status": "IDLE",
            "required_action": "none",
            "last_round": None,
        },
        "segments": {
            "active": {},
            "completed": [],
            "superseded": [],
            "last_reminder": None,
        },
        "goal_history": [],
        "recovery": {
            "latest_checkpoint": None,
            "blocked_reason": None,
            "blocker_scope_review": None,
            "recommended_action": "select_highest_value_action",
        },
        "judge": {
            "pending": None,
            "last_result": None,
        },
        "goal_completion": {
            "status": "NOT_CERTIFIED",
            "north_star_hash": None,
            "validation_ids": [],
            "validated_at": None,
            "input_fingerprint": None,
            "summary": None,
            "failure_reasons": [],
        },
        "updated_at": None,
    }


def _strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _bounded_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:MAX_GOAL_TEXT] if text else None


def _bounded_strings(values: Any) -> list[str]:
    return [value[:MAX_GOAL_TEXT] for value in _strings(values)[:MAX_GOAL_LIST_ITEMS]]


def goal_contract_fingerprint(north_star: dict[str, Any]) -> str:
    """Hash authority-bearing Goal fields, excluding read-time/runtime metadata."""
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    anti_goals = list(dict.fromkeys([
        *_strings(north_star.get("anti_goals")),
        *_strings(definition.get("non_goals")),
    ]))
    payload = {
        "confirmed": bool(north_star.get("confirmed")),
        "goal": str(north_star.get("goal") or ""),
        "goal_mode_objective": str(north_star.get("goal_mode_objective") or ""),
        "goal_definition": definition,
        "main_path": _strings(north_star.get("main_path")),
        "allowed_subgoals": _strings(north_star.get("allowed_subgoals")),
        "anti_goals": anti_goals,
        "backlog_domains": _strings(north_star.get("backlog_domains")),
        "protected_principles": _strings(north_star.get("protected_principles")),
        "core_path_patterns": _strings(north_star.get("core_path_patterns")),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _bounded_actions(values: Any) -> list[dict[str, Any]]:
    actions = []
    for index, value in enumerate(values[:MAX_GOAL_LIST_ITEMS] if isinstance(values, list) else []):
        if isinstance(value, dict):
            name = _bounded_text(value.get("name") or value.get("action") or value.get("description"))
            if not name:
                continue
            actions.append({
                "action_id": _bounded_text(value.get("action_id")) or f"A{index + 1}",
                "name": name,
                "from": _bounded_text(value.get("from")),
                "to": _bounded_text(value.get("to")),
                "inputs": _bounded_strings(value.get("inputs")),
                "outputs": _bounded_strings(value.get("outputs")),
                "consumer": _bounded_text(value.get("consumer")),
            })
            continue
        name = _bounded_text(value)
        if name:
            actions.append({"action_id": f"A{index + 1}", "name": name})
    return actions


def _dependency_node_id(value: Any, node_ids: list[str]) -> str:
    """Resolve an exact or explicitly prefixed dependency to one node ID.

    Older detailed Goals sometimes described a dependency as ``N1 output`` or
    ``DOMAIN 的领域结果`` even though the runtime requires node identities.
    Accept only a unique node ID at the beginning of the text; unrelated prose
    remains unresolved and therefore continues to block execution.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    exact = [node_id for node_id in node_ids if text.casefold() == node_id.casefold()]
    if len(exact) == 1:
        return exact[0]
    prefixed = [
        node_id
        for node_id in node_ids
        if re.match(
            rf"^{re.escape(node_id)}(?:\s|[:：/\\—-]|的|$)",
            text,
            flags=re.IGNORECASE,
        )
    ]
    return prefixed[0] if len(prefixed) == 1 else text


def _module_dependencies(state: dict[str, Any], module: dict[str, Any]) -> list[str]:
    stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    modules = [row for row in contract.get("modules", []) if isinstance(row, dict)]
    node_ids = [str(row.get("node_id") or "").strip() for row in modules]
    node_ids = [value for value in node_ids if value]
    completed_program_phases = {
        str(value).strip().casefold()
        for value in contract.get("completed_program_phases", [])
        if str(value).strip()
    }
    return [
        resolved
        for value in _strings(module.get("dependencies"))
        if (resolved := _dependency_node_id(value, node_ids))
        and resolved.casefold() not in completed_program_phases
    ]


def goal_contract_projection(north_star: dict[str, Any]) -> dict[str, Any]:
    """Project the detailed Goal contract without copying the long Goal prose."""
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    process = definition.get("process") if isinstance(definition.get("process"), dict) else {}
    nodes = [row for row in process.get("nodes", []) if isinstance(row, dict)]
    node_ids = [str(node.get("node_id") or f"N{index + 1}").strip() for index, node in enumerate(nodes)]
    dependent_consumers: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for index, node in enumerate(nodes):
        consumer_id = node_ids[index]
        for value in _bounded_strings(node.get("dependencies")):
            dependency = _dependency_node_id(value, node_ids)
            if dependency in dependent_consumers and consumer_id not in dependent_consumers[dependency]:
                dependent_consumers[dependency].append(consumer_id)
    modules = []
    for index, node in enumerate(nodes[:MAX_GOAL_MODULES]):
        node_id = _bounded_text(node.get("node_id")) or f"N{index + 1}"
        consumers = list(dict.fromkeys([
            *_bounded_strings(node.get("consumers")),
            *dependent_consumers.get(node_id, []),
        ]))
        modules.append({
            "node_id": node_id,
            "name": _bounded_text(node.get("name")),
            "objective": _bounded_text(node.get("objective")),
            "dependencies": [
                _dependency_node_id(value, node_ids)
                for value in _bounded_strings(node.get("dependencies"))
            ],
            "inputs": _bounded_strings(node.get("inputs")),
            "actions": _bounded_actions(node.get("actions")),
            "outputs": _bounded_strings(node.get("outputs")),
            "consumers": consumers,
            "affected_paths": _bounded_strings(node.get("affected_paths")),
            "affected_modules": _bounded_strings(node.get("affected_modules")),
            "exit_criteria": _bounded_strings(node.get("exit_criteria")),
            "execution_mode": _bounded_text(node.get("execution_mode")),
            "contribution_to_goal": _bounded_text(node.get("contribution_to_goal")),
            "timebox_hours": node.get("timebox_hours"),
            "reminder_interval_hours": node.get("reminder_interval_hours", 0),
        })
    deliverables = []
    for value in definition.get("deliverables", [])[:MAX_GOAL_LIST_ITEMS] if isinstance(definition.get("deliverables"), list) else []:
        if isinstance(value, dict):
            deliverables.append({
                "name": _bounded_text(value.get("name")),
                "description": _bounded_text(value.get("description")),
                "consumer": _bounded_text(value.get("consumer")),
                "acceptance": _bounded_strings(value.get("acceptance")),
            })
        elif _bounded_text(value):
            deliverables.append({"name": _bounded_text(value)})
    final_acceptance = []
    values = definition.get("final_acceptance") if isinstance(definition.get("final_acceptance"), list) else []
    for value in values[:MAX_GOAL_LIST_ITEMS]:
        if isinstance(value, dict):
            final_acceptance.append({
                "criterion": _bounded_text(value.get("criterion")),
                "evidence": _bounded_text(value.get("evidence")),
                "validation_method": _bounded_text(value.get("validation_method")),
            })
        elif _bounded_text(value):
            final_acceptance.append({"criterion": _bounded_text(value)})
    return {
        "objective": _bounded_text(definition.get("precise_goal") or north_star.get("goal")),
        "current_state": _bounded_text(definition.get("current_state")),
        "desired_state": _bounded_text(definition.get("desired_state")),
        "source_requirements": _bounded_strings(definition.get("source_requirements")),
        "first_principles": [
            {
                "principle": _bounded_text(value.get("principle")),
                "rationale": _bounded_text(value.get("rationale")),
                "implications": _bounded_strings(value.get("implications")),
            }
            for value in definition.get("first_principles", [])[:MAX_GOAL_LIST_ITEMS]
            if isinstance(value, dict) and _bounded_text(value.get("principle"))
        ],
        "modules": modules,
        "module_count_total": len(nodes),
        "projection_truncated": len(nodes) > MAX_GOAL_MODULES,
        "deliverables": deliverables,
        "final_acceptance": final_acceptance,
        "constraints": _bounded_strings(definition.get("constraints")),
        "non_goals": _bounded_strings(definition.get("non_goals")),
        "execution_plan_ref": _bounded_text(definition.get("execution_plan_ref")),
    }


_STOP_STALL_MARKERS = (
    "进入安全暂停", "安全暂停", "暂停执行", "暂停项目", "等待你", "等你", "等待用户",
    "等用户", "需要你操作", "需要用户操作", "人工动作", "人工操作", "恢复条件",
    "才能继续", "才可继续", "再告诉我即可继续", "醒来后", "不再空转",
    "pause execution", "pause the project", "waiting for you", "waiting for the user",
    "requires human action", "requires manual action", "blocked until", "cannot continue until",
    "before continuing", "resume when", "stop here",
)
_EXTERNAL_PREREQUISITE_MARKERS = (
    "wi-fi", "wifi", "真机", "物理设备", "物理打开", "人工", "用户操作", "登录",
    "授权", "凭证", "插入设备", "连接设备", "打开设备", "点击确认", "签名",
    "external device", "physical device", "manual", "human", "log in", "login",
    "credential", "connect the device", "turn on", "click", "signing",
)
_GLOBAL_BLOCKER_PROOF_MARKERS = (
    "所有剩余路径", "全部剩余路径", "所有未完成路径", "全部未完成路径", "没有独立路径",
    "无独立路径", "不存在独立路径", "都依赖", "均依赖", "全部依赖",
    "all remaining paths", "every remaining path", "all unfinished paths",
    "no independent path", "no executable path remains", "all paths depend",
)


def _goal_coverage(state: dict[str, Any]) -> dict[str, Any]:
    stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    modules = [row for row in contract.get("modules", []) if isinstance(row, dict)]
    acceptance = [row for row in contract.get("final_acceptance", []) if isinstance(row, dict)]
    labels: list[str] = []
    for row in modules:
        label = str(row.get("name") or row.get("node_id") or "").strip()
        if label and label not in labels:
            labels.append(label[:MAX_GOAL_TEXT])
    if not labels:
        for row in acceptance:
            label = str(row.get("criterion") or "").strip()
            if label and label not in labels:
                labels.append(label[:MAX_GOAL_TEXT])
    return {
        "module_count": int(contract.get("module_count_total", len(modules)) or 0),
        "acceptance_count": len(acceptance),
        "success_criteria_count": len([
            row for row in stack.get("l1_success_criteria", []) if isinstance(row, dict)
        ]),
        "labels": labels[:MAX_GOAL_COVERAGE_LABELS],
        "projection_truncated": bool(contract.get("projection_truncated")),
    }


def _segment_ids(state: dict[str, Any], key: str) -> set[str]:
    segments = state.get("segments") if isinstance(state.get("segments"), dict) else {}
    if key == "active":
        values = segments.get("active") if isinstance(segments.get("active"), dict) else {}
        return {str(value).strip() for value in values if str(value).strip()}
    values = segments.get("completed") if isinstance(segments.get("completed"), list) else []
    return {
        str(value.get("node_id") or "").strip()
        for value in values
        if isinstance(value, dict) and str(value.get("node_id") or "").strip()
    }


def _module_matches_message(module: dict[str, Any], lower_message: str) -> bool:
    for key, minimum in (("node_id", 2), ("name", 4)):
        value = " ".join(str(module.get(key) or "").casefold().split())
        if len(value) >= minimum and value in lower_message:
            return True
    return False


def _independent_goal_paths(state: dict[str, Any], lower_message: str) -> list[dict[str, Any]]:
    """Return dependency-ready unfinished modules other than the blocked path."""
    stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    modules = [value for value in contract.get("modules", []) if isinstance(value, dict)]
    completed = _segment_ids(state, "completed")
    active = _segment_ids(state, "active")
    candidates: list[dict[str, Any]] = []
    for module in modules:
        node_id = str(module.get("node_id") or "").strip()
        if not node_id or node_id in completed or node_id in active:
            continue
        if _module_matches_message(module, lower_message):
            continue
        dependencies = _module_dependencies(state, module)
        if not all(value in completed for value in dependencies):
            continue
        candidates.append({
            "node_id": node_id,
            "name": _bounded_text(module.get("name")),
            "objective": _bounded_text(module.get("objective")),
        })
    return candidates[:3]


def _path_summary(paths: list[dict[str, Any]]) -> str:
    values = []
    for path in paths:
        label = " ".join(value for value in (
            str(path.get("node_id") or "").strip(),
            str(path.get("name") or "").strip(),
        ) if value)
        objective = str(path.get("objective") or "").strip()
        if objective:
            label += ": " + objective
        if label:
            values.append(label)
    return "; ".join(values)


def _substantive_progress_since(state: dict[str, Any], review: dict[str, Any]) -> dict[str, int]:
    activity = state.get("activity") if isinstance(state.get("activity"), dict) else {}
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    return {
        "writes": max(0, int(activity.get("writes", 0) or 0) - int(review.get("baseline_writes", 0) or 0)),
        "validations": max(0, int(activity.get("validations", 0) or 0) - int(review.get("baseline_validations", 0) or 0)),
        "evidence": max(0, int(progress.get("evidence_count", 0) or 0) - int(review.get("baseline_evidence", 0) or 0)),
    }


def external_prerequisite_stop_review(
    state: dict[str, Any] | None,
    message: str,
    *,
    stop_hook_active: bool = False,
) -> dict[str, Any]:
    """Select a dependency-ready Goal path before a local prerequisite stops work.

    A productive continuation may renew the lease. A planning-only continuation
    receives one execution retry and then fails open so the hook cannot create an
    infinite explanation loop.
    """
    row = state or empty_state()
    lower = str(message or "").strip().lower()
    completion = row.get("goal_completion") if isinstance(row.get("goal_completion"), dict) else {}
    stack = row.get("goal_stack") if isinstance(row.get("goal_stack"), dict) else {}
    if (
        not lower
        or not stack.get("l0_final_goal")
        or completion.get("status") == "CERTIFIED_COMPLETE"
        or not any(marker in lower for marker in _STOP_STALL_MARKERS)
        or not any(marker in lower for marker in _EXTERNAL_PREREQUISITE_MARKERS)
    ):
        return {"should_continue": False, "status": "NO_SCOPE_REVIEW"}

    coverage = _goal_coverage(row)
    if not any(
        int(coverage.get(key, 0) or 0) > 0
        for key in ("module_count", "acceptance_count", "success_criteria_count")
    ):
        return {"should_continue": False, "status": "INSUFFICIENT_GOAL_STRUCTURE"}
    paths = _independent_goal_paths(row, lower)
    modules_present = int(coverage.get("module_count", 0) or 0) > 0
    recovery = row.get("recovery") if isinstance(row.get("recovery"), dict) else {}
    previous = recovery.get("blocker_scope_review") if isinstance(recovery.get("blocker_scope_review"), dict) else {}
    if stop_hook_active:
        if not previous:
            return {"should_continue": False, "status": "NO_ACTIVE_RECOVERY_LEASE"}
        if not paths and any(marker in lower for marker in _GLOBAL_BLOCKER_PROOF_MARKERS):
            return {"should_continue": False, "status": "GLOBAL_BLOCKER_REPORTED"}
        delta = _substantive_progress_since(row, previous)
        if not any(delta.values()):
            attempt = int(previous.get("attempt_count", 1) or 1)
            if attempt >= 2:
                return {
                    "should_continue": False,
                    "status": "NO_PROGRESS_RETRY_EXHAUSTED",
                    "progress_delta": delta,
                }
            paths = [value for value in previous.get("candidate_paths", []) if isinstance(value, dict)]
            summary = _path_summary(paths)
            target = summary or "the highest-value dependency-ready Goal module"
            return {
                "should_continue": True,
                "status": "EXECUTION_RETRY_REQUIRED",
                "attempt_count": attempt + 1,
                "reason": (
                    "[Execute the alternate path now] The previous continuation produced no product write, "
                    "validation result, or new evidence. Do not return another plan or status explanation. "
                    f"Use tools now on {target}. Stop only after producing evidence, or after proving that every "
                    "unfinished Goal path depends on the same external prerequisite."
                ),
                "coverage": coverage,
                "candidate_paths": paths,
                "progress_delta": delta,
            }

    if modules_present and not paths:
        return {"should_continue": False, "status": "NO_DEPENDENCY_READY_INDEPENDENT_PATH"}
    labels = ", ".join(coverage["labels"])
    coverage_text = labels or str(stack.get("l0_final_goal") or "confirmed North Star")[:MAX_GOAL_TEXT]
    if coverage.get("projection_truncated"):
        coverage_text += ", plus additional Goal modules"
    if paths:
        target = _path_summary(paths)
        reason = (
            "[Automatic alternate-path continuation] One external or manual prerequisite blocks only a local path; "
            "the confirmed North Star is unfinished and a dependency-ready path remains. Mark the local prerequisite "
            "DEFERRED_LOCAL and use tools now on: " + target + ". Do not merely describe the plan, invent substitute "
            "work, or claim the deferred acceptance passed. Stop only after producing evidence, or after proving every "
            "unfinished Goal path depends on the same prerequisite."
        )
        status = "CONTINUE_INDEPENDENT_PATH"
    else:
        reason = (
            "[Goal-wide blocker scope check] One external or manual prerequisite was treated as a reason to stop "
            "the whole project, but the confirmed North Star is not certified complete. Treat it as DEFERRED_LOCAL, "
            "classify every unfinished acceptance path, and immediately execute any independent path. Stop only if "
            "all remaining paths share the blocker. Goal coverage snapshot: " + coverage_text + "."
        )
        status = "REQUIRES_SCOPE_CHECK"
    return {
        "should_continue": True,
        "status": status,
        "attempt_count": 1,
        "reason": reason,
        "coverage": coverage,
        "candidate_paths": paths,
    }


def record_blocker_scope_review(
    state: dict[str, Any] | None,
    *,
    review: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    """Persist bounded blocker metadata without storing assistant text."""
    row = copy.deepcopy(state or empty_state())
    recovery = row.setdefault("recovery", {})
    coverage = review.get("coverage") if isinstance(review.get("coverage"), dict) else {}
    recovery["blocked_reason"] = None
    activity = row.get("activity") if isinstance(row.get("activity"), dict) else {}
    progress = row.get("progress") if isinstance(row.get("progress"), dict) else {}
    recovery["blocker_scope_review"] = {
        "status": str(review.get("status") or "REQUIRES_SCOPE_CHECK"),
        "kind": "EXTERNAL_OR_MANUAL_PREREQUISITE",
        "observed_at": observed_at,
        "attempt_count": int(review.get("attempt_count", 1) or 1),
        "goal_module_count": int(coverage.get("module_count", 0) or 0),
        "goal_acceptance_count": int(coverage.get("acceptance_count", 0) or 0),
        "goal_success_criteria_count": int(coverage.get("success_criteria_count", 0) or 0),
        "candidate_paths": [
            {
                "node_id": str(value.get("node_id") or "")[:64],
                "name": _bounded_text(value.get("name")),
                "objective": _bounded_text(value.get("objective")),
            }
            for value in review.get("candidate_paths", [])[:3]
            if isinstance(value, dict)
        ],
        "baseline_writes": int(activity.get("writes", 0) or 0),
        "baseline_validations": int(activity.get("validations", 0) or 0),
        "baseline_evidence": int(progress.get("evidence_count", 0) or 0),
    }
    recovery["recommended_action"] = "execute_dependency_ready_goal_path_or_prove_global_blocker"
    row["updated_at"] = observed_at
    return row


def _acceptance_criteria(north_star: dict[str, Any], ticket: dict[str, Any]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    for index, value in enumerate(_strings(definition.get("success_criteria"))):
        criteria.append({"id": f"goal-contract-{index + 1}", "criterion": value, "source": "goal_contract"})
    for index, value in enumerate(definition.get("final_acceptance", []) if isinstance(definition.get("final_acceptance"), list) else []):
        if isinstance(value, dict) and str(value.get("criterion") or "").strip():
            criteria.append({
                "id": str(value.get("id") or f"goal-contract-final-{index + 1}"),
                "criterion": str(value.get("criterion")).strip(),
                "source": "goal_contract_final_acceptance",
            })
    acceptance = ticket.get("acceptance") if isinstance(ticket.get("acceptance"), dict) else {}
    for key in ("commands_pass", "files_exist", "contains", "assertions"):
        values = acceptance.get(key) if isinstance(acceptance.get(key), list) else []
        for index, value in enumerate(values):
            criteria.append({
                "id": f"ticket-{key}-{index + 1}",
                "criterion": value,
                "source": "ticket_acceptance",
            })
    unique: dict[str, dict[str, Any]] = {}
    for row in criteria:
        encoded = json.dumps(row.get("criterion"), ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
        unique.setdefault(key, row)
    return list(unique.values())[:64]


def build_goal_stack(
    north_star: dict[str, Any],
    phase: dict[str, Any],
    ticket: dict[str, Any],
    *,
    current_action: str | None = None,
    expected_evidence: str | None = None,
) -> dict[str, Any]:
    active_phase = phase if phase.get("status") == "ACTIVE" else {}
    active_ticket = ticket if ticket.get("status") == "ACTIVE" else {}
    action = current_action or str(active_ticket.get("task_goal") or "").strip() or None
    expectation = expected_evidence
    if not expectation and active_ticket:
        validation_ids = _strings(active_ticket.get("validation_ids"))
        if validation_ids:
            expectation = "validation_catalog: " + ", ".join(validation_ids[:6])
        elif _acceptance_criteria({}, active_ticket):
            expectation = "machine acceptance evidence"
    stage = str(active_phase.get("goal") or active_ticket.get("task_goal") or "").strip() or None
    contract = goal_contract_projection(north_star)
    contract["completed_program_phases"] = [
        str(row.get("phase_id")).strip()
        for row in phase.get("completed_phases", [])
        if isinstance(row, dict) and str(row.get("phase_id") or "").strip()
    ]
    identity_source = str(north_star.get("goal_mode_objective") or north_star.get("goal") or "").strip()
    return {
        "l0_final_goal": str(north_star.get("goal") or "").strip() or None,
        "goal_identity_sha256": (
            hashlib.sha256(identity_source.encode("utf-8")).hexdigest()
            if identity_source else None
        ),
        "l1_success_criteria": _acceptance_criteria(north_star, active_ticket),
        "l2_current_stage": stage,
        "l3_current_action": action,
        "l3_expected_evidence": expectation or None,
        "goal_contract": contract,
    }


def refresh(
    state: dict[str, Any] | None,
    *,
    north_star: dict[str, Any],
    phase: dict[str, Any],
    ticket: dict[str, Any],
    updated_at: str,
    current_action: str | None = None,
    expected_evidence: str | None = None,
) -> dict[str, Any]:
    row = copy.deepcopy(state or empty_state())
    defaults = empty_state()
    for key, value in defaults.items():
        row.setdefault(key, copy.deepcopy(value))
    row["schema_version"] = SCHEMA_VERSION
    recovery = row.get("recovery") if isinstance(row.get("recovery"), dict) else {}
    for key, value in defaults["recovery"].items():
        recovery.setdefault(key, copy.deepcopy(value))
    row["recovery"] = recovery
    previous = row.get("goal_stack") if isinstance(row.get("goal_stack"), dict) else {}
    next_stack = build_goal_stack(
        north_star,
        phase,
        ticket,
        current_action=current_action if current_action is not None else previous.get("l3_current_action"),
        expected_evidence=expected_evidence if expected_evidence is not None else previous.get("l3_expected_evidence"),
    )
    segments = row.get("segments") if isinstance(row.get("segments"), dict) else {}
    active_segments = segments.get("active") if isinstance(segments.get("active"), dict) else {}
    valid_node_ids = {
        str(item.get("node_id") or "").strip()
        for item in next_stack.get("goal_contract", {}).get("modules", [])
        if isinstance(item, dict) and str(item.get("node_id") or "").strip()
    }
    previous_goal = str(previous.get("l0_final_goal") or "").strip()
    next_goal = str(next_stack.get("l0_final_goal") or "").strip()
    previous_identity = str(previous.get("goal_identity_sha256") or "").strip()
    next_identity = str(next_stack.get("goal_identity_sha256") or "").strip()
    identity_changed = bool(previous_identity and next_identity and previous_identity != next_identity)
    goal_text_changed = bool(previous_goal and next_goal and previous_goal != next_goal)
    legacy_active_mismatch = bool(active_segments) and bool(valid_node_ids) and any(
        str(node_id) not in valid_node_ids for node_id in active_segments
    )
    goal_generation_changed = identity_changed or goal_text_changed or legacy_active_mismatch

    completion = row.get("goal_completion") if isinstance(row.get("goal_completion"), dict) else {}
    if goal_generation_changed:
        history = [item for item in row.get("goal_history", []) if isinstance(item, dict)]
        history.append({
            "transition": "SUPERSEDED_BY_GOAL_CHANGE",
            "superseded_at": updated_at,
            "goal": previous_goal or None,
            "goal_identity_sha256": previous_identity or None,
            "activity": copy.deepcopy(row.get("activity", {})),
            "progress": copy.deepcopy(row.get("progress", {})),
            "goal_completion_status": completion.get("status"),
            "active_segment_ids": [str(value) for value in active_segments][:MAX_GOAL_MODULES],
            "completed_segment_ids": [
                str(item.get("node_id") or "")
                for item in segments.get("completed", [])
                if isinstance(item, dict)
            ][:MAX_SEGMENT_HISTORY],
        })
        superseded = [item for item in segments.get("superseded", []) if isinstance(item, dict)]
        for item in list(active_segments.values()) + [
            value for value in segments.get("completed", []) if isinstance(value, dict)
        ]:
            archived = copy.deepcopy(item)
            archived.update({
                "previous_status": archived.get("status"),
                "status": "SUPERSEDED",
                "transition": "SUPERSEDED_BY_GOAL_CHANGE",
                "superseded_at": updated_at,
                "next_reminder_at": None,
            })
            superseded.append(archived)
        for key in ("activity", "progress", "evidence", "iterations", "collaboration", "recovery", "judge"):
            row[key] = copy.deepcopy(defaults[key])
        row["segments"] = copy.deepcopy(defaults["segments"])
        row["segments"]["superseded"] = superseded[-MAX_SEGMENT_HISTORY:]
        row["goal_history"] = history[-MAX_GOAL_HISTORY:]
        if completion.get("status") != "CERTIFIED_COMPLETE":
            completion = copy.deepcopy(defaults["goal_completion"])

    row["goal_stack"] = next_stack
    if completion.get("status") == "CERTIFIED_COMPLETE":
        current_goal_hash = goal_contract_fingerprint(north_star)
        if completion.get("north_star_hash") != current_goal_hash:
            completion = copy.deepcopy(completion)
            completion["status"] = "STALE_GOAL_CHANGED"
            completion["failure_reasons"] = ["The confirmed North Star changed after final regression."]
    row["goal_completion"] = completion
    row["updated_at"] = updated_at
    return row


def _parse_time(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def _segment_module(state: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    for row in contract.get("modules", []) if isinstance(contract.get("modules"), list) else []:
        if isinstance(row, dict) and str(row.get("node_id") or "").strip() == node_id:
            return row
    return None


def start_segment(
    state: dict[str, Any] | None,
    *,
    node_id: str,
    observed_at: str,
    started_by: str = "EXPLICIT",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Start one Goal module and derive its real wall-clock deadline."""
    row = copy.deepcopy(state or empty_state())
    module = _segment_module(row, str(node_id).strip())
    if module is None:
        raise ValueError(f"unknown Goal segment: {node_id}")
    segments = row.setdefault("segments", copy.deepcopy(empty_state()["segments"]))
    active = segments.setdefault("active", {})
    key = str(node_id).strip()
    if key in active:
        return row, copy.deepcopy(active[key])
    completed = {
        str(item.get("node_id") or "")
        for item in segments.get("completed", [])
        if isinstance(item, dict)
    }
    missing = [value for value in _module_dependencies(row, module) if value not in completed]
    if missing:
        raise ValueError("segment dependencies are not complete: " + ", ".join(missing))
    try:
        timebox = float(module.get("timebox_hours"))
    except (TypeError, ValueError):
        timebox = 0.0
    if timebox <= 0:
        raise ValueError(f"Goal segment {key} has no positive timebox_hours")
    try:
        cadence = float(module.get("reminder_interval_hours") or 0)
    except (TypeError, ValueError):
        cadence = 0.0
    started = _parse_time(observed_at) or dt.datetime.now(dt.timezone.utc)
    deadline = started + dt.timedelta(hours=timebox)
    next_reminder = deadline if timebox <= 2 or cadence <= 0 else min(
        started + dt.timedelta(hours=cadence), deadline
    )
    runtime = {
        "node_id": key,
        "name": module.get("name"),
        "objective": module.get("objective"),
        "status": "ACTIVE",
        "started_at": _iso(started),
        "deadline_at": _iso(deadline),
        "timebox_hours": timebox,
        "reminder_interval_hours": cadence,
        "next_reminder_at": _iso(next_reminder),
        "reminder_count": 0,
        "started_by": started_by,
    }
    active[key] = runtime
    row["updated_at"] = _iso(started)
    return row, copy.deepcopy(runtime)


def auto_start_segment(
    state: dict[str, Any] | None,
    *,
    observed_at: str,
    hints: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Silently start a segment only when the current implementation target is unambiguous."""
    row = copy.deepcopy(state or empty_state())
    segments = row.setdefault("segments", copy.deepcopy(empty_state()["segments"]))
    active = segments.setdefault("active", {})
    completed = {
        str(item.get("node_id") or "")
        for item in segments.get("completed", [])
        if isinstance(item, dict)
    }
    stack = row.get("goal_stack") if isinstance(row.get("goal_stack"), dict) else {}
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    modules = [item for item in contract.get("modules", []) if isinstance(item, dict)]
    eligible = [
        item for item in modules
        if str(item.get("node_id") or "").strip()
        and str(item.get("node_id") or "").strip() not in active
        and str(item.get("node_id") or "").strip() not in completed
        and all(value in completed for value in _module_dependencies(row, item))
    ]
    if not eligible:
        return row, None

    hint = " ".join(str(value or "").strip() for value in (hints or []) if str(value or "").strip())
    normalized_hint = " " + " ".join(hint.casefold().split()) + " "
    matches: list[dict[str, Any]] = []
    if normalized_hint.strip():
        for item in eligible:
            node_id = " ".join(str(item.get("node_id") or "").casefold().split())
            name = " ".join(str(item.get("name") or "").casefold().split())
            node_match = len(node_id) >= 2 and f" {node_id} " in normalized_hint
            name_match = len(name) >= 4 and name in normalized_hint
            if node_match or name_match:
                matches.append(item)

    target = matches[0] if len(matches) == 1 else None
    if target is None and not active and len(eligible) == 1:
        target = eligible[0]
    if target is None:
        return row, None
    return start_segment(
        row,
        node_id=str(target.get("node_id") or ""),
        observed_at=observed_at,
        started_by="BACKGROUND_HIGH_CONFIDENCE",
    )


def complete_segment(
    state: dict[str, Any] | None,
    *,
    node_id: str,
    observed_at: str,
    evidence_ids: list[str] | None = None,
    completed_criteria: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Complete a segment only with an explicit result or acceptance signal."""
    row = copy.deepcopy(state or empty_state())
    segments = row.setdefault("segments", copy.deepcopy(empty_state()["segments"]))
    active = segments.setdefault("active", {})
    key = str(node_id).strip()
    runtime = active.get(key)
    if not isinstance(runtime, dict):
        raise ValueError(f"Goal segment is not active: {key}")
    evidence = _strings(evidence_ids)
    criteria = _strings(completed_criteria)
    if not evidence and not criteria:
        raise ValueError("segment completion requires --evidence-id or --completed-criterion")
    completed = copy.deepcopy(runtime)
    completed.update({
        "status": "COMPLETED",
        "completed_at": observed_at,
        "evidence_ids": evidence[:16],
        "completed_criteria": criteria[:16],
        "next_reminder_at": None,
    })
    active.pop(key, None)
    history = [item for item in segments.get("completed", []) if isinstance(item, dict)]
    history.append(completed)
    segments["completed"] = history[-MAX_SEGMENT_HISTORY:]
    row["updated_at"] = observed_at
    return row, copy.deepcopy(completed)


def due_segment_reminder(
    state: dict[str, Any] | None,
    *,
    observed_at: str,
    consume: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return one bounded reminder on the next hook event after it becomes due."""
    row = copy.deepcopy(state or empty_state())
    segments = row.setdefault("segments", copy.deepcopy(empty_state()["segments"]))
    active = segments.setdefault("active", {})
    current = _parse_time(observed_at) or dt.datetime.now(dt.timezone.utc)
    due: list[tuple[dt.datetime, str, dict[str, Any]]] = []
    for key, runtime in active.items():
        if not isinstance(runtime, dict):
            continue
        next_at = _parse_time(runtime.get("next_reminder_at"))
        if next_at is not None and current >= next_at:
            due.append((next_at, str(key), runtime))
    if not due:
        return row, None
    _, key, runtime = sorted(due, key=lambda item: item[0])[0]
    deadline = _parse_time(runtime.get("deadline_at"))
    overdue = bool(deadline and current >= deadline)
    reminder = {
        "node_id": key,
        "name": runtime.get("name"),
        "objective": runtime.get("objective"),
        "status": "OVERDUE" if overdue else "TIMEBOX_CHECKPOINT",
        "deadline_at": runtime.get("deadline_at"),
        "timebox_hours": runtime.get("timebox_hours"),
        "required_action": (
            "finish_validate_or_split_with_reason"
            if overdue else "check_progress_and_continue_highest_value_path"
        ),
    }
    if consume:
        try:
            cadence = float(runtime.get("reminder_interval_hours") or 0)
        except (TypeError, ValueError):
            cadence = 0.0
        cadence = cadence if cadence > 0 else max(1.0, min(float(runtime.get("timebox_hours") or 1), 4.0))
        runtime["reminder_count"] = int(runtime.get("reminder_count", 0) or 0) + 1
        runtime["last_reminded_at"] = _iso(current)
        next_reminder = current + dt.timedelta(hours=cadence)
        if not overdue and deadline is not None:
            next_reminder = min(next_reminder, deadline)
        runtime["next_reminder_at"] = _iso(next_reminder)
        segments["last_reminder"] = copy.deepcopy(reminder)
        row["updated_at"] = _iso(current)
    return row, reminder


def apply_observation(state: dict[str, Any] | None, event: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(state or empty_state())
    activity = row.setdefault("activity", {})
    activity["events"] = int(activity.get("events", 0) or 0) + 1
    category = str(event.get("category") or "read")
    if event.get("phase") == "PostToolUse":
        if category == "write":
            activity["writes"] = int(activity.get("writes", 0) or 0) + 1
        if category == "validation":
            activity["validations"] = int(activity.get("validations", 0) or 0) + 1
        if event.get("failed"):
            activity["failed_events"] = int(activity.get("failed_events", 0) or 0) + 1
    activity["last_activity_at"] = event.get("ts")

    # A successful validation is evidence, but not an automatic claim that a
    # product success criterion is complete.
    if event.get("phase") == "PostToolUse" and category == "validation" and not event.get("failed"):
        evidence_id = "validation-event:" + str(event.get("event_id") or "")
        evidence = list(row.get("evidence") or [])
        if evidence_id not in {str(item.get("evidence_id")) for item in evidence if isinstance(item, dict)}:
            evidence.append({
                "evidence_id": evidence_id,
                "kind": "validation_observation",
                "summary": "A validation command completed successfully.",
                "observed_at": event.get("ts"),
            })
            row["evidence"] = evidence[-MAX_EVIDENCE:]
            progress = row.setdefault("progress", {})
            progress["evidence_count"] = len(row["evidence"])
            progress["last_evidence_at"] = event.get("ts")
            progress["last_progress_at"] = event.get("ts")
    row["updated_at"] = event.get("ts")
    return row


def record_evidence(
    state: dict[str, Any] | None,
    *,
    evidence_id: str,
    kind: str,
    summary: str,
    observed_at: str,
) -> dict[str, Any]:
    row = copy.deepcopy(state or empty_state())
    evidence = [item for item in row.get("evidence", []) if isinstance(item, dict)]
    if evidence_id not in {str(item.get("evidence_id")) for item in evidence}:
        evidence.append({
            "evidence_id": evidence_id,
            "kind": kind,
            "summary": summary,
            "observed_at": observed_at,
        })
    row["evidence"] = evidence[-MAX_EVIDENCE:]
    progress = row.setdefault("progress", {})
    progress["evidence_count"] = len(row["evidence"])
    progress["last_evidence_at"] = observed_at
    progress["last_progress_at"] = observed_at
    row["updated_at"] = observed_at
    return row


def record_iteration(
    state: dict[str, Any] | None,
    *,
    hypothesis: str,
    change: str,
    expected_result: str,
    validation: str,
    result: str,
    decision: str,
    evidence_ids: list[str],
    completed_criteria: list[str],
    observed_at: str,
) -> dict[str, Any]:
    row = copy.deepcopy(state or empty_state())
    progress_made = bool(evidence_ids or completed_criteria)
    iteration = {
        "iteration_id": hashlib.sha256(
            (observed_at + hypothesis + change + result).encode("utf-8")
        ).hexdigest()[:16],
        "hypothesis": hypothesis,
        "change": change,
        "expected_result": expected_result,
        "validation": validation,
        "result": result,
        "decision": decision,
        "evidence_ids": list(dict.fromkeys(evidence_ids))[:20],
        "completed_criteria": list(dict.fromkeys(completed_criteria))[:20],
        "progress_made": progress_made,
        "completed_at": observed_at,
    }
    iterations = [item for item in row.get("iterations", []) if isinstance(item, dict)]
    iterations.append(iteration)
    row["iterations"] = iterations[-MAX_ITERATIONS:]
    progress = row.setdefault("progress", {})
    if progress_made:
        evidence = [item for item in row.get("evidence", []) if isinstance(item, dict)]
        known_evidence = {str(item.get("evidence_id")) for item in evidence}
        for evidence_id in iteration["evidence_ids"]:
            if evidence_id not in known_evidence:
                evidence.append({
                    "evidence_id": evidence_id,
                    "kind": "iteration_validation",
                    "summary": result,
                    "observed_at": observed_at,
                })
                known_evidence.add(evidence_id)
        row["evidence"] = evidence[-MAX_EVIDENCE:]
        progress["evidence_count"] = len(row["evidence"])
        if iteration["evidence_ids"]:
            progress["last_evidence_at"] = observed_at
        progress["no_progress_iterations"] = 0
        progress["last_progress_at"] = observed_at
        known = list(progress.get("completed_criteria") or [])
        for value in completed_criteria:
            if value not in known:
                known.append(value)
        progress["completed_criteria"] = known[:64]
    else:
        progress["no_progress_iterations"] = int(progress.get("no_progress_iterations", 0) or 0) + 1
    stack = row.setdefault("goal_stack", {})
    stack["l3_current_action"] = change or hypothesis
    stack["l3_expected_evidence"] = expected_result or validation
    recovery = row.setdefault("recovery", {})
    recovery["latest_checkpoint"] = {
        "iteration_id": iteration["iteration_id"],
        "decision": decision,
        "evidence_ids": iteration["evidence_ids"],
        "completed_at": observed_at,
    }
    if progress_made:
        recovery["blocked_reason"] = None
        recovery["recommended_action"] = "select_highest_value_action"
    elif int(progress.get("no_progress_iterations", 0) or 0) >= 2:
        recovery["blocked_reason"] = "two_completed_iterations_without_new_evidence"
        recovery["recommended_action"] = "review_strategy_or_restore_last_evidence_checkpoint"
    row["updated_at"] = observed_at
    return row


def record_collaboration_round(
    state: dict[str, Any] | None,
    *,
    source: str,
    target: str,
    claim: str,
    evidence_ids: list[str],
    artifact_refs: list[str],
    state_transition: str | None,
    observed_at: str,
) -> dict[str, Any]:
    """Record cross-thread progress while refusing praise or agreement as evidence."""
    row = copy.deepcopy(state or empty_state())
    transition = str(state_transition or "").strip().upper()
    accepted_transition = transition if transition in COLLABORATION_PROGRESS_TRANSITIONS else None
    unique_evidence = list(dict.fromkeys(value for value in _strings(evidence_ids)))[:20]
    unique_artifacts = list(dict.fromkeys(value for value in _strings(artifact_refs)))[:20]
    progress_made = bool(unique_evidence or unique_artifacts or accepted_transition)
    round_row = {
        "round_id": hashlib.sha256(
            (observed_at + source + target + claim).encode("utf-8")
        ).hexdigest()[:16],
        "source": _bounded_text(source),
        "target": _bounded_text(target),
        "claim": _bounded_text(claim),
        "evidence_ids": unique_evidence,
        "artifact_refs": unique_artifacts,
        "state_transition": accepted_transition,
        "progress_made": progress_made,
        "observed_at": observed_at,
    }
    collaboration = row.setdefault("collaboration", copy.deepcopy(empty_state()["collaboration"]))
    rounds = [item for item in collaboration.get("rounds", []) if isinstance(item, dict)]
    rounds.append(round_row)
    collaboration["rounds"] = rounds[-MAX_COLLABORATION_ROUNDS:]
    collaboration["last_round"] = round_row

    progress = row.setdefault("progress", {})
    recovery = row.setdefault("recovery", {})
    if progress_made:
        collaboration["no_evidence_rounds"] = 0
        collaboration["status"] = "EVIDENCE_PROGRESS"
        collaboration["required_action"] = "continue_from_new_evidence"
        progress["no_progress_iterations"] = 0
        progress["last_progress_at"] = observed_at
        evidence = [item for item in row.get("evidence", []) if isinstance(item, dict)]
        known = {str(item.get("evidence_id")) for item in evidence}
        evidence_values = list(unique_evidence)
        evidence_values.extend(
            "collaboration-artifact:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
            for value in unique_artifacts
        )
        if accepted_transition:
            evidence_values.append(
                "collaboration-transition:"
                + hashlib.sha256((accepted_transition + claim).encode("utf-8")).hexdigest()[:20]
            )
        for evidence_id in evidence_values:
            if evidence_id in known:
                continue
            evidence.append({
                "evidence_id": evidence_id,
                "kind": "collaboration_progress",
                "summary": _bounded_text(claim),
                "observed_at": observed_at,
            })
            known.add(evidence_id)
        row["evidence"] = evidence[-MAX_EVIDENCE:]
        progress["evidence_count"] = len(row["evidence"])
        progress["last_evidence_at"] = observed_at
        recovery["blocked_reason"] = None
        recovery["recommended_action"] = "select_highest_value_action"
    else:
        count = int(collaboration.get("no_evidence_rounds", 0) or 0) + 1
        collaboration["no_evidence_rounds"] = count
        if count >= 2:
            collaboration["status"] = "CONSENSUS_WITHOUT_PROGRESS"
            collaboration["required_action"] = "stop_mutual_review_and_execute_validate_or_escalate"
            recovery["blocked_reason"] = "two_collaboration_rounds_without_new_evidence"
            recovery["recommended_action"] = "execute_validate_or_escalate_one_concrete_blocker"
        else:
            collaboration["status"] = "NO_EVIDENCE_WARNING"
            collaboration["required_action"] = "produce_evidence_or_execute"
    row["updated_at"] = observed_at
    return row


def judge_trigger(
    state: dict[str, Any] | None,
    *,
    pending_targeted_rail: bool = False,
    high_cost_ambiguous_action: bool = False,
    appeal_with_new_evidence: bool = False,
    explicit_request: bool = False,
    novelty: bool = True,
) -> dict[str, Any]:
    row = state or empty_state()
    no_progress = int((row.get("progress") or {}).get("no_progress_iterations", 0) or 0)
    reasons: list[str] = []
    if pending_targeted_rail:
        reasons.append("pending_targeted_rail")
    if high_cost_ambiguous_action:
        reasons.append("high_cost_ambiguous_action")
    if appeal_with_new_evidence:
        reasons.append("appeal_with_new_evidence")
    if no_progress >= 2:
        reasons.append("two_completed_iterations_without_evidence_progress")
    if explicit_request:
        reasons.append("explicit_request")
    consequential = bool(pending_targeted_rail or high_cost_ambiguous_action or appeal_with_new_evidence or no_progress >= 2)
    eligible = bool(explicit_request or (reasons and consequential and novelty))
    return {
        "eligible": eligible,
        "reasons": reasons,
        "novelty": bool(novelty),
        "policy": "ambiguity_and_consequence_and_novelty",
    }


def compact_status(state: dict[str, Any] | None) -> dict[str, Any]:
    row = state or empty_state()
    progress = row.get("progress") if isinstance(row.get("progress"), dict) else {}
    activity = row.get("activity") if isinstance(row.get("activity"), dict) else {}
    iterations = [item for item in row.get("iterations", []) if isinstance(item, dict)]
    latest = iterations[-1] if iterations else None
    stack = copy.deepcopy(row.get("goal_stack", empty_state()["goal_stack"]))
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    modules = [item for item in contract.get("modules", []) if isinstance(item, dict)]
    contract_summary = {
        "module_count": int(contract.get("module_count_total", len(modules)) or 0),
        "final_acceptance_count": len(contract.get("final_acceptance") or []),
    }
    if contract.get("execution_plan_ref"):
        contract_summary["execution_plan_ref"] = contract.get("execution_plan_ref")
    stack["goal_contract"] = contract_summary
    collaboration = row.get("collaboration") if isinstance(row.get("collaboration"), dict) else {}
    segments = row.get("segments") if isinstance(row.get("segments"), dict) else {}
    active_segments = [
        copy.deepcopy(value) for value in (segments.get("active") or {}).values()
        if isinstance(value, dict)
    ]
    return {
        "goal_stack": stack,
        "progress": {
            "evidence_count": int(progress.get("evidence_count", 0) or 0),
            "completed_criteria_count": len(progress.get("completed_criteria") or []),
            "no_progress_iterations": int(progress.get("no_progress_iterations", 0) or 0),
            "last_progress_at": progress.get("last_progress_at"),
        },
        "activity": {
            "events": int(activity.get("events", 0) or 0),
            "writes": int(activity.get("writes", 0) or 0),
            "validations": int(activity.get("validations", 0) or 0),
            "failed_events": int(activity.get("failed_events", 0) or 0),
            "last_activity_at": activity.get("last_activity_at"),
        },
        "latest_iteration": latest,
        "collaboration": {
            "status": collaboration.get("status", "IDLE"),
            "no_evidence_rounds": int(collaboration.get("no_evidence_rounds", 0) or 0),
            "required_action": collaboration.get("required_action", "none"),
            "last_round": collaboration.get("last_round"),
        },
        "segments": {
            "active": active_segments,
            "active_count": len(active_segments),
            "completed_count": len(segments.get("completed") or []),
            "last_reminder": segments.get("last_reminder"),
        },
        "recovery": row.get("recovery", {}),
        "judge": row.get("judge", {}),
        "goal_completion": row.get("goal_completion", empty_state()["goal_completion"]),
        "updated_at": row.get("updated_at"),
    }
