"""Project-scoped execution convergence state for Codex Goal Supervisor.

The projection distinguishes activity from evidence-backed progress. It is
bounded, cheap to update from hooks, and disposable with the project.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "1.0"
MAX_ITERATIONS = 64
MAX_EVIDENCE = 128
MAX_GOAL_MODULES = 16
MAX_GOAL_LIST_ITEMS = 8
MAX_GOAL_TEXT = 360


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "goal_stack": {
            "l0_final_goal": None,
            "l1_success_criteria": [],
            "l2_current_stage": None,
            "l3_current_action": None,
            "l3_expected_evidence": None,
            "goal_contract": {
                "objective": None,
                "current_state": None,
                "desired_state": None,
                "modules": [],
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
        "recovery": {
            "latest_checkpoint": None,
            "blocked_reason": None,
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


def goal_contract_projection(north_star: dict[str, Any]) -> dict[str, Any]:
    """Project the detailed Goal contract without copying the long Goal prose."""
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    process = definition.get("process") if isinstance(definition.get("process"), dict) else {}
    nodes = [row for row in process.get("nodes", []) if isinstance(row, dict)]
    modules = []
    for index, node in enumerate(nodes[:MAX_GOAL_MODULES]):
        modules.append({
            "node_id": _bounded_text(node.get("node_id")) or f"N{index + 1}",
            "name": _bounded_text(node.get("name")),
            "objective": _bounded_text(node.get("objective")),
            "dependencies": _bounded_strings(node.get("dependencies")),
            "inputs": _bounded_strings(node.get("inputs")),
            "outputs": _bounded_strings(node.get("outputs")),
            "exit_criteria": _bounded_strings(node.get("exit_criteria")),
            "execution_mode": _bounded_text(node.get("execution_mode")),
            "contribution_to_goal": _bounded_text(node.get("contribution_to_goal")),
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
        "modules": modules,
        "module_count_total": len(nodes),
        "projection_truncated": len(nodes) > MAX_GOAL_MODULES,
        "deliverables": deliverables,
        "final_acceptance": final_acceptance,
        "constraints": _bounded_strings(definition.get("constraints")),
        "non_goals": _bounded_strings(definition.get("non_goals")),
        "execution_plan_ref": _bounded_text(definition.get("execution_plan_ref")),
    }


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
    return {
        "l0_final_goal": str(north_star.get("goal") or "").strip() or None,
        "l1_success_criteria": _acceptance_criteria(north_star, active_ticket),
        "l2_current_stage": stage,
        "l3_current_action": action,
        "l3_expected_evidence": expectation or None,
        "goal_contract": goal_contract_projection(north_star),
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
    previous = row.get("goal_stack") if isinstance(row.get("goal_stack"), dict) else {}
    row["goal_stack"] = build_goal_stack(
        north_star,
        phase,
        ticket,
        current_action=current_action if current_action is not None else previous.get("l3_current_action"),
        expected_evidence=expected_evidence if expected_evidence is not None else previous.get("l3_expected_evidence"),
    )
    completion = row.get("goal_completion") if isinstance(row.get("goal_completion"), dict) else {}
    if completion.get("status") == "CERTIFIED_COMPLETE":
        current_goal_hash = hashlib.sha256(
            json.dumps(north_star, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if completion.get("north_star_hash") != current_goal_hash:
            completion = copy.deepcopy(completion)
            completion["status"] = "STALE_GOAL_CHANGED"
            completion["failure_reasons"] = ["The confirmed North Star changed after final regression."]
            row["goal_completion"] = completion
    row["updated_at"] = updated_at
    return row


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
        "recovery": row.get("recovery", {}),
        "judge": row.get("judge", {}),
        "goal_completion": row.get("goal_completion", empty_state()["goal_completion"]),
        "updated_at": row.get("updated_at"),
    }
