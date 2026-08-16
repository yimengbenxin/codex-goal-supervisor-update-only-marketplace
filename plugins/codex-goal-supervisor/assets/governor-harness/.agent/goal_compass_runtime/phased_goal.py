from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any


MODE = "STRUCTURED_PHASED_GOAL"
SCHEMA_VERSION = 2
MIN_PHASE_HOURS = 2.0
MAX_PHASE_HOURS = 24.0


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _hours(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if MIN_PHASE_HOURS <= result <= MAX_PHASE_HOURS else None


def _research_material(research: dict[str, Any]) -> dict[str, Any]:
    sources = _texts(research.get("sources"))
    queries = _texts(research.get("queries"))
    decisions = research.get("reuse_decisions") if isinstance(research.get("reuse_decisions"), list) else []
    decision = str(research.get("reuse_decision") or research.get("no_suitable_reuse_reason") or "").strip()
    reviewed = int(research.get("tool_sources_reviewed") or 0) + int(research.get("article_sources_reviewed") or 0)
    return {
        "completed": research.get("completed") is True,
        "sources": sources,
        "queries": queries,
        "decision": decision,
        "decisions": decisions,
        "reviewed": reviewed,
    }


def research_errors(research: Any, label: str) -> list[str]:
    if not isinstance(research, dict):
        return [f"{label}.planning_research is required"]
    material = _research_material(research)
    errors: list[str] = []
    if not material["completed"]:
        errors.append(f"{label}.planning_research.completed must be true")
    if not material["sources"] and material["reviewed"] < 1:
        errors.append(f"{label}.planning_research must record reviewed sources")
    if not material["queries"] and material["reviewed"] < 1:
        errors.append(f"{label}.planning_research must record the researched scope")
    if not material["decision"] and not material["decisions"]:
        errors.append(f"{label}.planning_research must record a reuse decision")
    return errors


def research_fingerprint(research: Any) -> str:
    material = _research_material(research if isinstance(research, dict) else {})
    payload = {
        "sources": material["sources"],
        "queries": material["queries"],
        "decision": material["decision"],
        "decisions": material["decisions"],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def validate_outline(payload: Any, north_star_goal: str) -> tuple[dict[str, Any], list[str]]:
    raw = payload.get("program_outline") if isinstance(payload, dict) and isinstance(payload.get("program_outline"), dict) else payload
    if not isinstance(raw, dict):
        return {}, ["program_outline must be a JSON object"]
    outline = dict(raw)
    errors: list[str] = []
    if not outline.get("north_star_goal") and outline.get("north_star"):
        outline["north_star_goal"] = outline["north_star"]
    if not outline.get("shared_contracts") and isinstance(outline.get("shared_contract"), list):
        outline["shared_contracts"] = outline["shared_contract"]
    if not outline.get("final_acceptance") and isinstance(outline.get("total_acceptance"), list):
        outline["final_acceptance"] = outline["total_acceptance"]
    if str(outline.get("north_star_goal") or "").strip() != str(north_star_goal or "").strip():
        errors.append("program_outline.north_star_goal must exactly match the confirmed North Star")
    errors.extend(research_errors(outline.get("planning_research"), "program_outline"))
    phases = outline.get("phases") if isinstance(outline.get("phases"), list) else []
    if not phases:
        errors.append("program_outline.phases must contain at least one phase")
    normalized_phases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(phases):
        if not isinstance(item, dict):
            errors.append(f"program_outline.phases[{index}] must be an object")
            continue
        row = dict(item)
        row.setdefault("phase_id", row.get("id"))
        row.setdefault("title", row.get("name"))
        row.setdefault("outcome", row.get("business_result"))
        row.setdefault("dependencies", row.get("depends_on"))
        phase_id = str(row.get("phase_id") or "").strip()
        if not phase_id:
            errors.append(f"program_outline.phases[{index}].phase_id is required")
        elif phase_id in seen:
            errors.append(f"duplicate phase_id: {phase_id}")
        seen.add(phase_id)
        for key in ("title", "outcome", "contribution_to_goal"):
            if not str(row.get(key) or "").strip():
                errors.append(f"program_outline.phases[{index}].{key} is required")
        for key in ("outputs", "consumers"):
            if not _texts(row.get(key)):
                errors.append(f"program_outline.phases[{index}].{key} must be non-empty")
        hours = _hours(row.get("estimated_hours"))
        if hours is None:
            errors.append(f"program_outline.phases[{index}].estimated_hours must be between 2 and 24")
        row["phase_id"] = phase_id
        row["dependencies"] = _texts(row.get("dependencies"))
        row["outputs"] = _texts(row.get("outputs"))
        row["consumers"] = _texts(row.get("consumers"))
        row["estimated_hours"] = hours
        normalized_phases.append(row)
    if not _texts(outline.get("shared_contracts")):
        errors.append("program_outline.shared_contracts must be non-empty")
    if not _texts(outline.get("final_acceptance")):
        errors.append("program_outline.final_acceptance must be non-empty")
    outline["schema_version"] = SCHEMA_VERSION
    outline["phases"] = normalized_phases
    outline["shared_contracts"] = _texts(outline.get("shared_contracts"))
    outline["final_acceptance"] = _texts(outline.get("final_acceptance"))
    phase_ids = {row["phase_id"] for row in normalized_phases if row.get("phase_id")}
    for row in normalized_phases:
        phase_id = str(row.get("phase_id") or "")
        dependencies = set(row.get("dependencies") or [])
        unknown = sorted(dependencies - phase_ids)
        if unknown:
            errors.append(f"phase {phase_id} has unknown dependencies: " + ", ".join(unknown))
        if phase_id and phase_id in dependencies:
            errors.append(f"phase {phase_id} cannot depend on itself")
    return outline, errors


def outline_phase(outline: dict[str, Any], phase_id: str) -> dict[str, Any] | None:
    for row in outline.get("phases", []):
        if isinstance(row, dict) and row.get("phase_id") == phase_id:
            return row
    return None


def validate_phase(
    payload: Any,
    outline: dict[str, Any],
    goal_definition: dict[str, Any],
    goal_mode_objective: str,
    completed_phase_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict):
        return {}, ["phase definition must be a JSON object"]
    phase = dict(payload)
    phase_id = str(phase.get("phase_id") or "").strip()
    listed = outline_phase(outline, phase_id)
    errors: list[str] = []
    if listed is None:
        errors.append("phase_id is not present in program_outline.phases")
        listed = {}
    hours = _hours(phase.get("estimated_hours", listed.get("estimated_hours")))
    if hours is None:
        errors.append("phase estimated_hours must be between 2 and 24")
    dependencies = _texts(phase.get("dependencies", listed.get("dependencies")))
    listed_dependencies = _texts(listed.get("dependencies"))
    if set(dependencies) != set(listed_dependencies):
        errors.append("phase dependencies must exactly match the program outline")
    missing_dependencies = sorted(set(dependencies) - completed_phase_ids)
    if missing_dependencies:
        errors.append("phase dependencies are incomplete: " + ", ".join(missing_dependencies))
    validation_ids = _texts(phase.get("validation_ids"))
    if not validation_ids:
        errors.append("phase validation_ids must be non-empty")
    research = phase.get("planning_research")
    errors.extend(research_errors(research, f"phase {phase_id or '?'}"))
    if research_fingerprint(research) == research_fingerprint(outline.get("planning_research")):
        errors.append("phase planning research must be distinct from program-outline research")
    if str(goal_definition.get("quality") or "") != "STRUCTURED_DETAILED":
        errors.append("phase goal_definition must be STRUCTURED_DETAILED")
    if not 2000 <= len(str(goal_mode_objective or "")) <= 3500:
        errors.append("phase goal_mode_objective must contain 2000-3500 characters")
    phase.update({
        "phase_id": phase_id,
        "title": str(phase.get("title") or listed.get("title") or "").strip(),
        "outcome": str(phase.get("outcome") or listed.get("outcome") or "").strip(),
        "dependencies": dependencies,
        "outputs": _texts(phase.get("outputs")) or _texts(listed.get("outputs")),
        "consumers": _texts(phase.get("consumers")) or _texts(listed.get("consumers")),
        "contribution_to_goal": str(phase.get("contribution_to_goal") or listed.get("contribution_to_goal") or "").strip(),
        "estimated_hours": hours,
        "validation_ids": validation_ids,
        "goal_definition": goal_definition,
        "goal_mode_objective": goal_mode_objective,
        "research_fingerprint": research_fingerprint(research),
    })
    return phase, errors


def _deadline(started_at: str, hours: float) -> str:
    started = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    return (started + dt.timedelta(hours=hours)).isoformat()


def activate(
    *,
    north_star_goal: str,
    outline: dict[str, Any],
    phase: dict[str, Any],
    observed_at: str,
    completed_phases: list[dict[str, Any]] | None = None,
    previous_phase_id: str | None = None,
) -> dict[str, Any]:
    hours = float(phase["estimated_hours"])
    current = dict(phase)
    current["telemetry"] = {
        "estimated_hours": hours,
        "started_at": observed_at,
        "deadline_at": _deadline(observed_at, hours),
        "first_product_action_at": None,
        "first_valid_evidence_at": None,
        "completed_at": None,
        "actual_hours": None,
        "validation_attempts": 0,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "status": "ACTIVE",
        "north_star_goal": north_star_goal,
        "program_outline": outline,
        "phase_id": current["phase_id"],
        "goal": current.get("outcome") or current.get("title"),
        "exit_criteria": list(current.get("goal_definition", {}).get("success_criteria", [])),
        "validation_ids": list(current.get("validation_ids", [])),
        "current_phase": current,
        "completed_phases": list(completed_phases or []),
        "previous_phase_id": previous_phase_id,
        "source": "user_confirmed_structured_phase",
        "confirmed_at": observed_at,
    }


def record_activity(state: dict[str, Any], category: str, failed: bool, observed_at: str) -> tuple[dict[str, Any], bool]:
    if state.get("mode") != MODE or state.get("status") != "ACTIVE" or failed:
        return state, False
    current = state.get("current_phase") if isinstance(state.get("current_phase"), dict) else {}
    telemetry = current.get("telemetry") if isinstance(current.get("telemetry"), dict) else {}
    changed = False
    if category == "write" and not telemetry.get("first_product_action_at"):
        telemetry["first_product_action_at"] = observed_at
        changed = True
    if category == "validation" and not telemetry.get("first_valid_evidence_at"):
        telemetry["first_valid_evidence_at"] = observed_at
        changed = True
    if changed:
        current["telemetry"] = telemetry
        state = dict(state)
        state["current_phase"] = current
    return state, changed


def complete(state: dict[str, Any], observed_at: str, reason: str, validation: dict[str, Any]) -> dict[str, Any]:
    current = dict(state.get("current_phase") or {})
    telemetry = dict(current.get("telemetry") or {})
    started_at = str(telemetry.get("started_at") or observed_at)
    started = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    completed = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if (
        not telemetry.get("first_valid_evidence_at")
        and str(validation.get("status") or "").upper() in {"PASS", "PASSED", "OK"}
    ):
        telemetry["first_valid_evidence_at"] = observed_at
    telemetry.update({
        "completed_at": observed_at,
        "actual_hours": round(max(0.0, (completed - started).total_seconds() / 3600.0), 4),
        "validation_attempts": int(telemetry.get("validation_attempts") or 0) + 1,
    })
    current["telemetry"] = telemetry
    current["status"] = "COMPLETED"
    current["completion_reason"] = reason
    current["validation"] = validation
    history = list(state.get("completed_phases") or [])
    history.append(current)
    result = dict(state)
    result.update({
        "status": "COMPLETED",
        "completed_at": observed_at,
        "completion_reason": reason,
        "current_phase": current,
        "completed_phases": history,
    })
    return result


def compact(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("mode") != MODE:
        return {
            key: state.get(key)
            for key in ("status", "phase_id", "goal", "exit_criteria")
            if key in state
        }
    current = state.get("current_phase") if isinstance(state.get("current_phase"), dict) else {}
    telemetry = current.get("telemetry") if isinstance(current.get("telemetry"), dict) else {}
    return {
        "mode": MODE,
        "status": state.get("status"),
        "phase_id": state.get("phase_id"),
        "title": current.get("title"),
        "outcome": current.get("outcome"),
        "estimated_hours": current.get("estimated_hours"),
        "deadline_at": telemetry.get("deadline_at"),
        "validation_ids": list(current.get("validation_ids") or []),
        "completed_phase_ids": [
            row.get("phase_id") for row in state.get("completed_phases", [])
            if isinstance(row, dict) and row.get("phase_id")
        ],
        "native_goal_sync": {
            "objective_chars": len(str(current.get("goal_mode_objective") or "")),
            "objective_sha256": hashlib.sha256(str(current.get("goal_mode_objective") or "").encode("utf-8")).hexdigest()
            if current.get("goal_mode_objective") else None,
        },
    }
