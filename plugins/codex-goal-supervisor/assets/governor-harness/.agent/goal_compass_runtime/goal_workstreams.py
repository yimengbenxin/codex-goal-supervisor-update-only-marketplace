"""Hierarchical Goal workstreams for independently executable Codex threads."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
MODE = "HIERARCHICAL_GOAL_WORKSTREAMS"
MIN_WORKSTREAM_HOURS = 2.0
MAX_WORKSTREAM_HOURS = 24.0


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _hours(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if MIN_WORKSTREAM_HOURS <= result <= MAX_WORKSTREAM_HOURS:
        return result
    return None


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def payload_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


def _path_prefix(value: str) -> str:
    path = str(PurePosixPath(str(value).replace("\\", "/"))).lstrip("./")
    parts: list[str] = []
    for part in path.split("/"):
        if any(char in part for char in "*?["):
            break
        if part:
            parts.append(part)
    return "/".join(parts)


def writable_overlap(left: list[str], right: list[str]) -> list[tuple[str, str]]:
    overlaps: list[tuple[str, str]] = []
    for first in left:
        first_prefix = _path_prefix(first)
        for second in right:
            second_prefix = _path_prefix(second)
            if (
                not first_prefix
                or not second_prefix
                or first_prefix == second_prefix
                or first_prefix.startswith(second_prefix + "/")
                or second_prefix.startswith(first_prefix + "/")
            ):
                overlaps.append((first, second))
    return overlaps


def _cycle(workstreams: list[dict[str, Any]]) -> list[str]:
    graph = {str(row.get("workstream_id") or ""): list(row.get("dependencies") or []) for row in workstreams}
    visiting: set[str] = set()
    visited: set[str] = set()
    trail: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visited:
            return []
        if node in visiting:
            try:
                start = trail.index(node)
            except ValueError:
                start = 0
            return [*trail[start:], node]
        visiting.add(node)
        trail.append(node)
        for dependency in graph.get(node, []):
            found = visit(str(dependency))
            if found:
                return found
        trail.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in graph:
        found = visit(node)
        if found:
            return found
    return []


def _contract_ids(value: Any, errors: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    rows = value if isinstance(value, list) else []
    if not rows:
        errors.append("shared_contracts must contain at least one cross-workstream contract")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            errors.append(f"shared_contracts[{index}] must be an object")
            continue
        row = dict(raw)
        contract_id = str(row.get("contract_id") or "").strip()
        if not contract_id:
            errors.append(f"shared_contracts[{index}].contract_id is required")
        elif contract_id in seen:
            errors.append(f"duplicate shared contract: {contract_id}")
        seen.add(contract_id)
        for key in ("subject", "rule"):
            if not str(row.get(key) or "").strip():
                errors.append(f"shared_contracts[{index}].{key} is required")
        consumers = _texts(row.get("consumers"))
        if not consumers:
            errors.append(f"shared_contracts[{index}].consumers must be non-empty")
        row.update({"contract_id": contract_id, "consumers": consumers})
        normalized.append(row)
    return normalized, seen


def validate_plan(
    payload: Any,
    *,
    parent_north_star_goal: str,
    parent_goal_objective_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    raw = payload.get("goal_workstreams") if isinstance(payload, dict) and isinstance(payload.get("goal_workstreams"), dict) else payload
    if not isinstance(raw, dict):
        return {}, ["goal_workstreams must be a JSON object"]
    plan = dict(raw)
    errors: list[str] = []
    if str(plan.get("parent_north_star_goal") or "").strip() != str(parent_north_star_goal or "").strip():
        errors.append("parent_north_star_goal must exactly match the confirmed project North Star")
    for key in ("fanout_reason", "integration_owner"):
        if not str(plan.get(key) or "").strip():
            errors.append(f"{key} is required")
    shared_contracts, known_contracts = _contract_ids(plan.get("shared_contracts"), errors)

    economics = plan.get("expected_net_benefit") if isinstance(plan.get("expected_net_benefit"), dict) else {}
    try:
        serial = float(economics.get("serial_hours") or 0)
        parallel = float(economics.get("parallel_hours") or 0)
        coordination = float(economics.get("coordination_hours") or 0)
        integration_hours = float(economics.get("integration_hours") or 0)
    except (TypeError, ValueError):
        serial = parallel = coordination = integration_hours = 0.0
    net_hours = serial - parallel - coordination - integration_hours
    if min(serial, parallel) <= 0 or min(coordination, integration_hours) < 0:
        errors.append("expected_net_benefit must contain positive serial/parallel hours and non-negative overhead")
    elif net_hours <= 0:
        errors.append("thread fanout must save more time than coordination and integration consume")

    rows = plan.get("workstreams") if isinstance(plan.get("workstreams"), list) else []
    if len(rows) < 2:
        errors.append("workstreams must contain at least two independently useful assignments")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            errors.append(f"workstreams[{index}] must be an object")
            continue
        row = dict(raw_row)
        workstream_id = str(row.get("workstream_id") or row.get("id") or "").strip()
        if not workstream_id:
            errors.append(f"workstreams[{index}].workstream_id is required")
        elif workstream_id in seen:
            errors.append(f"duplicate workstream_id: {workstream_id}")
        seen.add(workstream_id)
        for key in ("title", "responsibility", "parent_contribution"):
            if not str(row.get(key) or "").strip():
                errors.append(f"workstreams[{index}].{key} is required")
        execution_mode = str(row.get("execution_mode") or "PARALLEL").strip().upper()
        if execution_mode not in {"PARALLEL", "SERIAL"}:
            errors.append(f"workstreams[{index}].execution_mode must be PARALLEL or SERIAL")
        parallel_group = str(row.get("parallel_group") or "").strip()
        if execution_mode == "PARALLEL" and not parallel_group:
            errors.append(f"workstreams[{index}].parallel_group is required for parallel work")
        hours = _hours(row.get("estimated_hours"))
        if hours is None:
            errors.append(f"workstreams[{index}].estimated_hours must be between 2 and 24")
        required_lists: dict[str, list[str]] = {}
        for key in ("inputs", "outputs", "consumers", "writable_paths", "validation_ids", "shared_contract_ids"):
            required_lists[key] = _texts(row.get(key))
            if not required_lists[key]:
                errors.append(f"workstreams[{index}].{key} must be non-empty")
        dependencies = _texts(row.get("dependencies"))
        unknown_contracts = sorted(set(required_lists["shared_contract_ids"]) - known_contracts)
        if unknown_contracts:
            errors.append(f"workstream {workstream_id} references unknown shared contracts: " + ", ".join(unknown_contracts))
        row.update({
            "workstream_id": workstream_id,
            "execution_mode": execution_mode,
            "parallel_group": parallel_group,
            "dependencies": dependencies,
            "estimated_hours": hours,
            "read_dependencies": _texts(row.get("read_dependencies")),
            "immutable_paths": _texts(row.get("immutable_paths")),
            **required_lists,
        })
        normalized.append(row)

    known_ids = {str(row.get("workstream_id") or "") for row in normalized}
    by_id = {str(row.get("workstream_id") or ""): row for row in normalized}
    for row in normalized:
        workstream_id = str(row.get("workstream_id") or "")
        dependencies = set(row.get("dependencies") or [])
        unknown = sorted(dependencies - known_ids)
        if unknown:
            errors.append(f"workstream {workstream_id} has unknown dependencies: " + ", ".join(unknown))
        if workstream_id in dependencies:
            errors.append(f"workstream {workstream_id} cannot depend on itself")
        for dependency in dependencies:
            target = by_id.get(dependency, {})
            if (
                row.get("execution_mode") == "PARALLEL"
                and target.get("execution_mode") == "PARALLEL"
                and row.get("parallel_group") == target.get("parallel_group")
            ):
                errors.append(f"dependency edge {dependency} -> {workstream_id} cannot be inside one parallel group")

    cycle = _cycle(normalized)
    if cycle:
        errors.append("workstream dependency cycle: " + " -> ".join(cycle))

    for index, left in enumerate(normalized):
        if left.get("execution_mode") != "PARALLEL":
            continue
        for right in normalized[index + 1:]:
            if right.get("execution_mode") != "PARALLEL" or right.get("parallel_group") != left.get("parallel_group"):
                continue
            overlaps = writable_overlap(left.get("writable_paths", []), right.get("writable_paths", []))
            if overlaps:
                preview = ", ".join(f"{a} <-> {b}" for a, b in overlaps[:4])
                errors.append(
                    f"parallel workstreams {left.get('workstream_id')} and {right.get('workstream_id')} overlap writable paths: {preview}"
                )

    initially_ready = [
        row for row in normalized
        if row.get("execution_mode") == "PARALLEL" and not row.get("dependencies")
    ]
    if len(initially_ready) < 2:
        errors.append("fanout requires at least two dependency-ready parallel workstreams; otherwise use phased Goal execution")

    final_integration = plan.get("final_integration") if isinstance(plan.get("final_integration"), dict) else {}
    if not _texts(final_integration.get("inputs")):
        errors.append("final_integration.inputs must be non-empty")
    if not _texts(final_integration.get("validation_ids")):
        errors.append("final_integration.validation_ids must be non-empty")
    if not str(final_integration.get("acceptance") or "").strip():
        errors.append("final_integration.acceptance is required")

    plan.update({
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "parent_north_star_goal": parent_north_star_goal,
        "parent_north_star_sha256": sha256_text(parent_north_star_goal),
        "parent_goal_objective_sha256": parent_goal_objective_sha256,
        "shared_contracts": shared_contracts,
        "workstreams": normalized,
        "expected_net_benefit": {
            "serial_hours": serial,
            "parallel_hours": parallel,
            "coordination_hours": coordination,
            "integration_hours": integration_hours,
            "net_saved_hours": round(net_hours, 2),
        },
        "final_integration": {
            **final_integration,
            "inputs": _texts(final_integration.get("inputs")),
            "validation_ids": _texts(final_integration.get("validation_ids")),
        },
    })
    return plan, list(dict.fromkeys(errors))


def new_state(plan: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
    workstreams: dict[str, dict[str, Any]] = {}
    for assignment in plan.get("workstreams", []):
        workstream_id = str(assignment.get("workstream_id") or "")
        workstreams[workstream_id] = {
            **assignment,
            "status": "PENDING",
            "thread_id": None,
            "thread_id_sha256": None,
            "goal_definition": None,
            "goal_mode_objective": None,
            "goal_mode_objective_sha256": None,
            "activated_at": None,
            "completed_at": None,
            "completion_summary": None,
            "evidence_ids": [],
        }
    state = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "status": "ACTIVE",
        "created_at": observed_at,
        "updated_at": observed_at,
        "plan_sha256": payload_sha256(plan),
        "parent_north_star_goal": plan.get("parent_north_star_goal"),
        "parent_north_star_sha256": plan.get("parent_north_star_sha256"),
        "parent_goal_objective_sha256": plan.get("parent_goal_objective_sha256"),
        "fanout_reason": plan.get("fanout_reason"),
        "integration_owner": plan.get("integration_owner"),
        "shared_contracts": plan.get("shared_contracts", []),
        "expected_net_benefit": plan.get("expected_net_benefit", {}),
        "final_integration": plan.get("final_integration", {}),
        "workstreams": workstreams,
    }
    return refresh_status(state, observed_at=observed_at)


def parent_alignment(state: dict[str, Any], north_star: dict[str, Any]) -> dict[str, Any]:
    current_goal = str(north_star.get("goal") or "")
    current_objective = str(north_star.get("goal_mode_objective") or "")
    goal_match = sha256_text(current_goal) == str(state.get("parent_north_star_sha256") or "")
    objective_match = sha256_text(current_objective) == str(state.get("parent_goal_objective_sha256") or "")
    return {
        "status": "ALIGNED" if goal_match and objective_match else "PARENT_GOAL_CHANGED",
        "north_star_match": goal_match,
        "goal_objective_match": objective_match,
    }


def ready_workstream_ids(state: dict[str, Any]) -> list[str]:
    rows = state.get("workstreams") if isinstance(state.get("workstreams"), dict) else {}
    completed = {workstream_id for workstream_id, row in rows.items() if isinstance(row, dict) and row.get("status") == "COMPLETE"}
    return sorted(
        workstream_id
        for workstream_id, row in rows.items()
        if isinstance(row, dict)
        and row.get("status") == "PENDING"
        and set(row.get("dependencies") or []).issubset(completed)
    )


def refresh_status(state: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
    result = dict(state)
    rows = result.get("workstreams") if isinstance(result.get("workstreams"), dict) else {}
    statuses = [str(row.get("status") or "PENDING") for row in rows.values() if isinstance(row, dict)]
    if statuses and all(status == "COMPLETE" for status in statuses):
        result["status"] = "READY_FOR_PARENT_INTEGRATION"
    elif any(status in {"ACTIVE", "ACTIVATING"} for status in statuses):
        result["status"] = "ACTIVE"
    else:
        result["status"] = "PLANNED"
    result["ready_workstream_ids"] = ready_workstream_ids(result)
    result["updated_at"] = observed_at
    return result


def compact(state: dict[str, Any], *, alignment: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = state.get("workstreams") if isinstance(state.get("workstreams"), dict) else {}
    counts: dict[str, int] = {}
    for row in rows.values():
        status = str(row.get("status") or "PENDING") if isinstance(row, dict) else "PENDING"
        counts[status] = counts.get(status, 0) + 1
    return {
        "status": state.get("status", "UNSET"),
        "alignment": alignment or {},
        "parent_north_star_goal": state.get("parent_north_star_goal"),
        "plan_sha256": state.get("plan_sha256"),
        "counts": counts,
        "ready_workstream_ids": list(state.get("ready_workstream_ids") or []),
        "expected_net_benefit": state.get("expected_net_benefit", {}),
        "workstreams": [
            {
                "workstream_id": workstream_id,
                "title": row.get("title"),
                "status": row.get("status"),
                "dependencies": list(row.get("dependencies") or []),
                "parallel_group": row.get("parallel_group"),
                "thread_bound": bool(row.get("thread_id_sha256")),
                "goal_ready": bool(row.get("goal_mode_objective_sha256")),
            }
            for workstream_id, row in sorted(rows.items())
            if isinstance(row, dict)
        ],
    }


def workstream_for_thread(state: dict[str, Any], thread_id: str) -> dict[str, Any] | None:
    candidate = str(thread_id or "").strip()
    if not candidate:
        return None
    for row in (state.get("workstreams") or {}).values():
        if isinstance(row, dict) and str(row.get("thread_id") or "") == candidate:
            return row
    return None


def thread_context(state: dict[str, Any], north_star: dict[str, Any], thread_id: str) -> str | None:
    row = workstream_for_thread(state, thread_id)
    if row is None:
        return None
    alignment = parent_alignment(state, north_star)
    if alignment["status"] != "ALIGNED":
        return (
            "[Goal workstream alignment] The parent North Star or parent Goal changed after this child Goal was created. "
            "Do not continue product writes in this child workstream. Return current evidence to the parent thread for reconciliation."
        )
    return (
        f"[Goal workstream {row.get('workstream_id')}] Parent North Star: {state.get('parent_north_star_goal')}\n"
        f"Responsibility: {row.get('responsibility')}\n"
        f"Required outputs: {'; '.join(row.get('outputs') or [])}\n"
        f"Consumers: {'; '.join(row.get('consumers') or [])}. "
        "Remain inside this workstream and return evidence to the parent integration owner."
    )
