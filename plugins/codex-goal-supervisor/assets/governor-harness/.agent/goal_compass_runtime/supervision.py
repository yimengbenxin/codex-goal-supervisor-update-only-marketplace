"""Pure capability routing for the advisory-first Goal Compass tool."""
from __future__ import annotations

from typing import Any


NARROW_TERMS = (
    "one ", "single ", "rename", "literal", "assertion", "existing module", "existing file",
    "一个", "单个", "重命名", "字面量", "断言", "现有模块", "现有文件",
)

CLEANUP_TERMS = (
    "cleanup", "clean up", "prune", "remove unused", "dead code", "duplicate scaffold",
    "archive noise", "premature abstraction", "simplify architecture", "repository hygiene",
    "清理", "裁剪", "删除未使用", "死代码", "重复脚手架", "归档噪音", "过早抽象", "简化架构",
)


def decide(context: dict[str, Any]) -> dict[str, Any]:
    """Route optional capabilities without turning them into workflow gates."""
    text = str(context.get("text") or "").lower()
    complexity = context.get("complexity", {})
    max_minutes = int(context.get("max_minutes") or 0)
    max_tool_calls = int(context.get("max_tool_calls") or 0)
    max_changed_files = int(context.get("max_changed_files") or 0)
    max_diff_lines = int(context.get("max_diff_lines") or 0)
    allowed_count = int(context.get("allowed_count") or 0)
    requested_departments = list(context.get("requested_departments") or [])
    quality_dimensions = {str(value).lower() for value in context.get("quality_dimensions", [])}

    read_only = bool(context.get("read_only"))
    narrow = (
        0 < max_minutes <= 30
        and 0 < max_tool_calls <= 40
        and 0 < max_changed_files <= 5
        and 0 < max_diff_lines <= 300
        and allowed_count <= 3
        and (complexity.get("depth") == "D0_ROUTINE" or any(term in text for term in NARROW_TERMS))
    )
    micro_direct = (
        narrow
        and max_minutes <= 20
        and max_tool_calls <= 25
        and max_changed_files <= 3
        and max_diff_lines <= 180
        and allowed_count <= 3
        and (
            complexity.get("depth") == "D0_ROUTINE"
            or context.get("budget_tier") == "MICRO_BOUNDED"
            or any(term in text for term in NARROW_TERMS)
        )
        and not requested_departments
        and not quality_dimensions
    )
    deep = (
        complexity.get("tier") == "T3_CRITICAL"
        or str(context.get("relationship_mode") or "").upper() == "PARALLEL"
        or len(requested_departments) > 4
        or bool(quality_dimensions & {"artifact", "product", "market"})
    )
    cleanup_risk = bool(context.get("janitor_required")) or any(term in text for term in CLEANUP_TERMS)
    broad_change_surface = (
        max_changed_files > 8
        or max_diff_lines > 800
        or allowed_count > 5
        or complexity.get("breadth") in {"B3_CROSS_FUNCTION", "B4_ENTERPRISE"}
    )

    if read_only:
        level = "NONE"
        reason = "Read-only/status work needs no intervention."
        controls: list[str] = []
        benefit_basis = ["no product mutation", "observe silently"]
    elif micro_direct and not deep:
        level = "NONE"
        reason = "This is a tiny reversible mutation; observe silently and rely on direct validation."
        controls = []
        benefit_basis = ["micro bounded mutation", "no independent review or cleanup risk", "direct validation is cheaper than ticket lifecycle"]
    elif narrow and not deep:
        level = "LIGHT"
        reason = "A narrow edit benefits from a compact acceptance reminder, not a mandatory ticket lifecycle."
        controls = ["background_observer", "acceptance_advisory"]
        benefit_basis = ["bounded mutation", "direct validation is sufficient", "company and cleanup capabilities stay dormant"]
    elif deep:
        level = "DEEP"
        reason = "High-consequence or cross-functional work justifies optional specialist roles and delivery audit."
        controls = ["background_observer", "custodian_on_change", "company_roles_on_decision", "auditor_on_delivery"]
        benefit_basis = ["high consequence or parallel integration", "specialist evidence may reduce expensive rework", "normal execution remains unblocked"]
    else:
        level = "STANDARD"
        reason = "The work has meaningful implementation risk; targeted reminders are enough unless an event requests a specialist capability."
        controls = ["background_observer", "custodian_on_change", "auditor_on_delivery"]
        benefit_basis = ["meaningful implementation risk", "event-triggered review avoids standing process cost", "normal execution remains unblocked"]

    janitor_needed = level == "DEEP" or (level == "STANDARD" and (cleanup_risk or broad_change_surface))
    if janitor_needed and "janitor_on_sprawl" not in controls:
        controls.append("janitor_on_sprawl")
        benefit_basis.append("cleanup or broad-change signal justifies a mark-only Janitor pass")
    elif level == "STANDARD":
        benefit_basis.append("no cleanup or broad-change signal; Janitor omitted")

    decisions = {
        "NONE": "SKIP",
        "LIGHT": "APPLY_MINIMAL",
        "STANDARD": "APPLY_TARGETED",
        "DEEP": "APPLY_DEEP",
    }
    return {
        "level": level,
        "tool_mode": "BACKGROUND_ADVISORY",
        "ticket_required": False,
        "ticket_mode": "optional_explicit_contract",
        "controls": controls,
        "company_mode": "recommended_on_cross_function_decision" if level == "DEEP" else "on_demand" if level == "STANDARD" else "not_required",
        "custodian_mode": "on_goal_or_scope_change" if level in {"STANDARD", "DEEP"} else "not_required",
        "auditor_mode": "on_delivery_or_failed_validation" if level != "NONE" else "not_required",
        "janitor_mode": "on_artifact_sprawl" if janitor_needed else "not_required",
        "intermediate_check": "on_demand",
        "reason": reason,
        "net_benefit": {
            "decision": decisions[level],
            "basis": benefit_basis,
            "rule": "Observe first; call a capability only for a concrete event, and never require ceremony for ordinary execution.",
        },
        "intervention_policy": {
            "ordinary_action": "SILENT",
            "uncertain_semantic_risk": "STRONG_WARNING",
            "deterministic_irreversible_boundary": "BLOCK_ACTION",
            "warning_does_not_require_ticket": True,
            "warning_does_not_stop_execution": True,
        },
        "capability_pool": {
            "company_roles": "optional_task_shaped",
            "custodian": "incoming_goal_or_scope_change",
            "auditor": "delivery_or_machine_evidence",
            "janitor": "artifact_sprawl_mark_only",
        },
        "complexity_tier": complexity.get("tier"),
        "breadth": complexity.get("breadth"),
    }
