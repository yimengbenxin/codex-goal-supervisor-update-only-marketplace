"""Bounded technical-route stagnation detection.

The detector stores only hashes and compact cause families. It does not retain
commands, tool output, source text, credentials, or network addresses. Route
semantics come from the current structured Goal node, so the policy is not tied
to one industry or transport choice.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any


POLICY_VERSION = "1.0"
REASSESS_AFTER_SECONDS = 30 * 60
IMMEDIATE_REASSESS_ATTEMPTS = 3
RAIL_AFTER_ATTEMPTS = 4
CORRECTION_MONITOR_SECONDS = 7 * 24 * 60 * 60
MAX_INCIDENTS = 32
MAX_FAILED_ACTIONS = 8
MAX_ROUTE_LABEL = 280
MAX_RESPONSE_TEXT = 4096


CAUSE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ENVIRONMENT_POLICY_BLOCK", (
        "blocked by policy", "security policy", "antivirus", "anti-virus",
        "endpoint protection", "防火墙", "杀毒", "安全策略", "被阻止", "被拦截",
    )),
    ("PERMISSION_OR_AUTH_BLOCK", (
        "permission denied", "access denied", "unauthorized", "forbidden", "http 401",
        "http 403", "not authorized", "权限不足", "拒绝访问", "未授权",
    )),
    ("NETWORK_OR_PORT_BLOCK", (
        "connection refused", "connection reset", "network unreachable", "host unreachable",
        "address already in use", "cannot bind", "failed to bind", "timed out connecting",
        "端口", "连接被拒绝", "网络不可达", "地址已被使用", "无法绑定",
    )),
    ("DEPENDENCY_OR_TOOL_MISSING", (
        "command not found", "no such file or directory", "module not found", "modulenotfounderror",
        "cannot find module", "not recognized as an internal", "missing required tool",
        "missing required platform tool", "required executable is unavailable", "找不到命令", "缺少依赖",
    )),
    ("BUILD_OR_COMPILE_FAILURE", (
        "compile error", "compilation failed", "build failed", "syntaxerror", "type error",
        "编译失败", "构建失败", "语法错误",
    )),
    ("VALIDATION_FAILURE", (
        "assertionerror", "test failed", "tests failed", "failed assertions", "validation failed",
        "测试失败", "验证失败", "断言失败",
    )),
    ("RESOURCE_EXHAUSTION", (
        "out of memory", "oom", "no space left", "resource exhausted", "too many open files",
        "内存不足", "磁盘空间不足", "资源耗尽",
    )),
    ("TOOL_TIMEOUT", (
        "timeout", "timed out", "deadline exceeded", "超时",
    )),
)


def _timestamp(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(start: Any, end: Any) -> float:
    left = _timestamp(start)
    right = _timestamp(end)
    if left is None or right is None:
        return 0.0
    if left.tzinfo is None:
        left = left.replace(tzinfo=dt.timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (right - left).total_seconds())


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _bounded_strings(value: Any, *, limit: int = MAX_RESPONSE_TEXT) -> str:
    parts: list[str] = []
    used = 0

    def visit(item: Any) -> None:
        nonlocal used
        if used >= limit:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).casefold() in {"token", "authorization", "cookie", "secret", "password"}:
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item[:24]:
                visit(child)
        elif isinstance(item, str):
            text = item[: max(0, limit - used)]
            parts.append(text)
            used += len(text)

    visit(value)
    return "\n".join(parts)[:limit]


def _normalized_failure_text(text: str) -> str:
    value = text.casefold()
    value = re.sub(r"(?:[a-z]:)?[/\\][^\s'\"<>]+", " <path> ", value)
    value = re.sub(r"\b(?:[0-9a-f]{8,}|\d{3,})\b", " <id> ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:1200]


def failure_cause(event: dict[str, Any]) -> tuple[str, str] | None:
    response = (
        event.get("tool_response") or event.get("toolResponse")
        or event.get("tool_output") or event.get("toolOutput")
        or event.get("tool_result") or event.get("toolResult")
        or event.get("output") or event.get("result") or event.get("error")
    )
    text = _normalized_failure_text(_bounded_strings(response))
    for family, markers in CAUSE_PATTERNS:
        if any(marker in text for marker in markers):
            return family, _hash({"family": family})
    status = ""
    if isinstance(response, dict):
        status = str(response.get("status") or response.get("exit_code") or response.get("exitCode") or response.get("returncode") or "")
    normalized = text or ("tool_failure:" + status)
    return "UNCLASSIFIED_TOOL_FAILURE", _hash({"normalized": normalized})


def _tool_input(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    return value if isinstance(value, dict) else {}


def action_fingerprint(event: dict[str, Any], paths: list[str]) -> str:
    payload = _tool_input(event)
    command = str(payload.get("command") or payload.get("cmd") or "")
    patch = str(payload.get("patch") or "")
    tool = str(event.get("tool_name") or event.get("toolName") or "").casefold()
    source = command or patch
    source = re.sub(r"(?:[a-z]:)?[/\\][^\s'\"<>]+", " <path> ", source.casefold())
    source = re.sub(r"\b(?:[0-9a-f]{8,}|\d{3,})\b", " <id> ", source)
    source = re.sub(r"\s+", " ", source).strip()[:1200]
    return _hash({"tool": tool, "action": source, "paths": sorted(paths)[:16]})


def build_context(
    *,
    north_star: dict[str, Any],
    convergence: dict[str, Any],
    event: dict[str, Any],
    paths: list[str],
    failed: bool,
) -> dict[str, Any] | None:
    """Build a domain-neutral route identity from the current Goal node."""
    stack = convergence.get("goal_stack") if isinstance(convergence.get("goal_stack"), dict) else {}
    contract = stack.get("goal_contract") if isinstance(stack.get("goal_contract"), dict) else {}
    modules = [row for row in contract.get("modules", []) if isinstance(row, dict)]
    segments = convergence.get("segments") if isinstance(convergence.get("segments"), dict) else {}
    active = segments.get("active") if isinstance(segments.get("active"), dict) else {}
    active_ids = [str(value) for value in active if str(value)]
    module = next((row for row in modules if str(row.get("node_id") or "") in active_ids), None)
    stage = str(stack.get("l2_current_stage") or "").strip()
    current_action = str(stack.get("l3_current_action") or "").strip()
    node_id = str((module or {}).get("node_id") or (active_ids[0] if active_ids else "")).strip()
    route_label = " | ".join(value for value in (
        stage,
        str((module or {}).get("name") or "").strip(),
        str((module or {}).get("objective") or "").strip(),
        current_action,
    ) if value)
    if not route_label:
        return None
    first_principles = contract.get("first_principles") if isinstance(contract.get("first_principles"), list) else []
    source_requirements = contract.get("source_requirements") if isinstance(contract.get("source_requirements"), list) else []
    final_acceptance = contract.get("final_acceptance") if isinstance(contract.get("final_acceptance"), list) else []
    progress = convergence.get("progress") if isinstance(convergence.get("progress"), dict) else {}
    cause = failure_cause(event) if failed else None
    route_anchor = node_id or current_action
    return {
        # Keep one identity for the Goal route. A parameter tweak or renamed
        # command must not erase the history of the same blocked route.
        "route_id": _hash({"goal": north_star.get("goal"), "stage": stage, "route_anchor": route_anchor}),
        "route_label": route_label[:MAX_ROUTE_LABEL],
        "node_id": node_id[:80],
        "action_fingerprint": action_fingerprint(event, paths),
        "cause_family": cause[0] if cause else None,
        "cause_fingerprint": cause[1] if cause else None,
        "evidence_count": int(progress.get("evidence_count", 0) or 0),
        "first_principle_count": len(first_principles),
        "source_requirement_count": len(source_requirements),
        "final_acceptance_count": len(final_acceptance),
        "requirement_anchor": _hash({
            "first_principles": first_principles,
            "source_requirements": source_requirements,
            "final_acceptance": final_acceptance,
        }),
    }


def _route_reason(incident: dict[str, Any], *, escalated: bool) -> str:
    prefix = "[Technical route targeted rail]" if escalated else "[Technical route reassessment required]"
    return (
        f"{prefix} Route '{incident.get('route_label') or incident.get('node_id') or 'current Goal route'}' "
        f"has repeated the same {incident.get('cause_family')} blocker {incident.get('attempts_since_progress')} "
        "times without new Goal evidence. Before another equivalent retry, re-check the route against the detailed "
        "Goal's source requirements, first principles, and final acceptance; research current external tools and "
        "documented solutions online; compare at least two materially different routes; then execute the smallest "
        "route that can satisfy the same acceptance. Do not merely rename the same retry."
    )


def _prune_incidents(incidents: dict[str, Any], observed_at: str) -> dict[str, Any]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for key, value in incidents.items():
        if not isinstance(value, dict):
            continue
        if value.get("status") == "CORRECTED_MONITORING" and _elapsed_seconds(value.get("corrected_at"), observed_at) >= CORRECTION_MONITOR_SECONDS:
            continue
        rows.append((key, value))
    rows.sort(key=lambda item: str(item[1].get("last_failure_at") or item[1].get("first_failure_at") or ""))
    return dict(rows[-MAX_INCIDENTS:])


def process_observation(
    state: dict[str, Any],
    event: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context = event.get("route_context") if isinstance(event.get("route_context"), dict) else None
    if not context or not context.get("route_id"):
        return state, []
    observed_at = str(event.get("ts") or "")
    incidents = _prune_incidents(dict(state.get("route_incidents") or {}), observed_at)
    signals: list[dict[str, Any]] = []
    route_id = str(context.get("route_id"))

    if event.get("phase") == "PreToolUse":
        for incident in reversed(list(incidents.values())):
            if not isinstance(incident, dict) or incident.get("route_id") != route_id:
                continue
            if incident.get("status") not in {"REASSESSMENT_REQUIRED", "RAIL_PENDING"}:
                continue
            action = str(context.get("action_fingerprint") or "")
            repeated = action in set(str(value) for value in incident.get("failed_action_fingerprints", []))
            if incident.get("status") == "RAIL_PENDING" and repeated:
                signals.append({
                    "signal": "ROUTE_STAGNATION",
                    "status": "RAIL_ENFORCED",
                    "strike_count": int(incident.get("attempts_since_progress", 0) or 0),
                    "intervention": "TARGETED_RAIL",
                    "deny": True,
                    "needs_judge": True,
                    "reason": _route_reason(incident, escalated=True),
                    "recommended_action": "research_compare_and_switch_route",
                    "route_id": route_id,
                    "route_label": incident.get("route_label"),
                    "cause_family": incident.get("cause_family"),
                })
            elif repeated:
                signals.append({
                    "signal": "ROUTE_STAGNATION",
                    "status": "CORRECTION_REQUIRED",
                    "strike_count": int(incident.get("attempts_since_progress", 0) or 0),
                    "intervention": "STRONG_WARNING",
                    "deny": False,
                    "needs_judge": False,
                    "reason": _route_reason(incident, escalated=False),
                    "recommended_action": "research_compare_and_switch_route",
                    "route_id": route_id,
                    "route_label": incident.get("route_label"),
                    "cause_family": incident.get("cause_family"),
                })
            else:
                incident["status"] = "ALTERNATIVE_TRIAL"
                incident["alternative_trial_at"] = observed_at
                incident["alternative_action_fingerprint"] = action
            break
        state["route_incidents"] = incidents
        return state, signals

    if event.get("phase") != "PostToolUse" or not event.get("failed"):
        state["route_incidents"] = incidents
        return state, []

    cause_fingerprint = str(context.get("cause_fingerprint") or "")
    if not cause_fingerprint:
        state["route_incidents"] = incidents
        return state, []
    key = _hash({"route_id": route_id, "cause": cause_fingerprint})
    incident = dict(incidents.get(key) or {})
    evidence_count = int(context.get("evidence_count", 0) or 0)
    if not incident:
        incident = {
            "incident_id": key,
            "policy_version": POLICY_VERSION,
            "route_id": route_id,
            "route_label": str(context.get("route_label") or "")[:MAX_ROUTE_LABEL],
            "node_id": str(context.get("node_id") or "")[:80],
            "cause_family": str(context.get("cause_family") or "UNCLASSIFIED_TOOL_FAILURE"),
            "cause_fingerprint": cause_fingerprint,
            "status": "OBSERVING",
            "first_failure_at": observed_at,
            "last_failure_at": observed_at,
            "total_attempts": 0,
            "attempts_since_progress": 0,
            "baseline_evidence_count": evidence_count,
            "failed_action_fingerprints": [],
            "requirement_anchor": context.get("requirement_anchor"),
            "first_principle_count": int(context.get("first_principle_count", 0) or 0),
            "source_requirement_count": int(context.get("source_requirement_count", 0) or 0),
            "final_acceptance_count": int(context.get("final_acceptance_count", 0) or 0),
        }
    if evidence_count > int(incident.get("baseline_evidence_count", 0) or 0):
        incident["baseline_evidence_count"] = evidence_count
        incident["attempts_since_progress"] = 0
        incident["status"] = "OBSERVING"
        incident["progress_observed_at"] = observed_at
    incident["total_attempts"] = int(incident.get("total_attempts", 0) or 0) + 1
    incident["attempts_since_progress"] = int(incident.get("attempts_since_progress", 0) or 0) + 1
    incident["last_failure_at"] = observed_at
    actions = list(incident.get("failed_action_fingerprints") or [])
    action = str(context.get("action_fingerprint") or "")
    if action and action not in actions:
        actions.append(action)
    incident["failed_action_fingerprints"] = actions[-MAX_FAILED_ACTIONS:]
    attempts = int(incident.get("attempts_since_progress", 0) or 0)
    elapsed = _elapsed_seconds(incident.get("first_failure_at"), observed_at)
    due = attempts >= IMMEDIATE_REASSESS_ATTEMPTS or (attempts >= 2 and elapsed >= REASSESS_AFTER_SECONDS)
    if due and incident.get("status") not in {"REASSESSMENT_REQUIRED", "RAIL_PENDING"}:
        incident["status"] = "REASSESSMENT_REQUIRED"
        incident["reassessment_required_at"] = observed_at
        signals.append({
            "signal": "ROUTE_REASSESSMENT_REQUIRED",
            "status": "CORRECTION_REQUIRED",
            "strike_count": attempts,
            "intervention": "STRONG_WARNING",
            "deny": False,
            "needs_judge": False,
            "reason": _route_reason(incident, escalated=False),
            "recommended_action": "research_compare_and_switch_route",
            "route_id": route_id,
            "route_label": incident.get("route_label"),
            "cause_family": incident.get("cause_family"),
        })
    elif attempts >= RAIL_AFTER_ATTEMPTS and incident.get("status") in {"REASSESSMENT_REQUIRED", "ALTERNATIVE_TRIAL"}:
        incident["status"] = "RAIL_PENDING"
        incident["rail_pending_at"] = observed_at
        signals.append({
            "signal": "ROUTE_REASSESSMENT_REQUIRED",
            "status": "RAIL_ENFORCED",
            "strike_count": attempts,
            "intervention": "TARGETED_RAIL_PENDING",
            "deny": False,
            "needs_judge": False,
            "reason": _route_reason(incident, escalated=True),
            "recommended_action": "research_compare_and_switch_route",
            "route_id": route_id,
            "route_label": incident.get("route_label"),
            "cause_family": incident.get("cause_family"),
        })
    incidents[key] = incident
    state["route_incidents"] = _prune_incidents(incidents, observed_at)
    return state, signals


def compact_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    incidents = (state or {}).get("route_incidents") if isinstance((state or {}).get("route_incidents"), dict) else {}
    active = [row for row in incidents.values() if isinstance(row, dict) and row.get("status") != "CORRECTED_MONITORING"]
    return {
        "active_count": len(active),
        "reassessment_required": sum(row.get("status") in {"REASSESSMENT_REQUIRED", "RAIL_PENDING"} for row in active),
        "latest": [
            {
                "incident_id": row.get("incident_id"),
                "status": row.get("status"),
                "node_id": row.get("node_id"),
                "cause_family": row.get("cause_family"),
                "attempts_since_progress": row.get("attempts_since_progress"),
            }
            for row in sorted(active, key=lambda value: str(value.get("last_failure_at") or ""), reverse=True)[:3]
        ],
    }
