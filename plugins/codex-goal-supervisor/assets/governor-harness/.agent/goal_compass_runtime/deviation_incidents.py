"""Persistent direction and Goal-contract deviation incidents.

The module enforces only a confirmed explicit boundary. North Star anti-goals
protect durable direction; structured Goal non-goals protect the concrete
execution contract. It never treats a missing positive module match as a hard
violation and never pauses the project as a whole.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any


RECHECK_SECONDS = 30 * 60
RECHECK_WRITE_EVENTS = 10
RECHECK_ADDED_LINES = 300
RECHECK_PATHS = 5
CLEAN_WINDOW_SECONDS = 7 * 24 * 60 * 60
MIN_CLEAN_ACTIVE_DAYS = 3
CORRECTION_LEASE_SECONDS = 30 * 60
MAX_INCIDENTS = 64
MAX_HISTORY = 24

POLICY_STOP_WORDS = {
    "add", "after", "all", "avoid", "before", "build", "create", "current", "do",
    "does", "dont", "goal", "implement", "into", "mvp", "must", "never",
    "not", "now", "only", "prohibit", "project", "should", "support", "system", "the",
    "this", "use", "using", "yet",
}
WEAK_POLICY_TOKENS = {
    "adapter", "ai", "artifact", "generation", "model", "permission",
    "security", "system", "video",
}

DEVIATION_DETECTED = "DEVIATION_DETECTED"
CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
RAIL_ENFORCED = "RAIL_ENFORCED"
CORRECTION_IN_PROGRESS = "CORRECTION_IN_PROGRESS"
CORRECTED_MONITORING = "CORRECTED_MONITORING"
CLEARED_AFTER_7D = "CLEARED_AFTER_7D"
NORTH_STAR_ALIGNMENT = "NORTH_STAR"
GOAL_CONTRACT_ALIGNMENT = "GOAL_CONTRACT"


def _parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _normal_text(value: str) -> str:
    low = value.lower().replace("_", " ").replace("-", " ")
    replacements = {
        "企业级权限平台": " enterprise rbac permission platform ",
        "企业权限平台": " enterprise rbac permission platform ",
        "基于角色的权限": " rbac permission ",
        "真实视频模型": " real video model ",
        "通用插件平台": " generic plugin platform ",
        "不允许": " prohibit ",
        "不要": " not ",
        "禁止": " prohibit ",
        "避免": " avoid ",
        "建设": " build ",
        "供应商市场": " provider marketplace ",
        "插件市场": " plugin marketplace ",
        "公共市场": " public marketplace ",
        "代理市场": " agent marketplace ",
        "权限系统": " rbac permission platform ",
        "企业级": " enterprise ",
        "安全网关": " security gateway ",
        "合规": " compliance ",
        "平台": " platform ",
        "插件": " plugin ",
        "真实": " real ",
        "视频": " video ",
        "模型": " model ",
        "权限": " permission ",
    }
    for source, target in replacements.items():
        low = low.replace(source, target)
    return " ".join(re.sub(r"[^\w]+", " ", low, flags=re.UNICODE).split())


def _stem_token(value: str) -> str:
    if value.endswith("ies") and len(value) > 5:
        return value[:-3] + "y"
    if value.endswith("s") and not value.endswith("ss") and len(value) > 4:
        return value[:-1]
    return value


def _word_tokens(value: str) -> list[str]:
    normalized = _normal_text(value)
    words = [_stem_token(word) for word in re.findall(r"[a-z][a-z0-9]{1,}", normalized)]
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        words.extend(segment[index:index + 2] for index in range(len(segment) - 1))
    return words


def _policy_signature(value: str) -> list[str]:
    return [word for word in _word_tokens(value) if word not in POLICY_STOP_WORDS]


def alignment_policy_sources(north_star: dict[str, Any]) -> dict[str, str]:
    """Return explicit project boundaries and the layer that owns each one."""
    sources: dict[str, str] = {}
    for value in north_star.get("anti_goals", []):
        policy = str(value).strip()
        if policy:
            sources.setdefault(policy, NORTH_STAR_ALIGNMENT)
    definition = north_star.get("goal_definition") if isinstance(north_star.get("goal_definition"), dict) else {}
    for value in definition.get("non_goals", []):
        policy = str(value).strip()
        if policy:
            sources.setdefault(policy, GOAL_CONTRACT_ALIGNMENT)
    return sources


def north_star_policies(north_star: dict[str, Any]) -> list[str]:
    """Compatibility view of all explicit alignment boundaries."""
    return list(alignment_policy_sources(north_star))


def _has_adjacent_policy_pair(policy_words: list[str], target_words: list[str]) -> bool:
    target_pairs = set(zip(target_words, target_words[1:]))
    return any(pair in target_pairs for pair in zip(policy_words, policy_words[1:]))


def semantic_payload(tool_input: dict[str, Any]) -> str:
    """Return intent-bearing additions, avoiding removed patch text."""
    raw = str(tool_input.get("patch") or tool_input.get("command") or tool_input.get("cmd") or "")
    if "*** Begin Patch" in raw:
        additions = [
            line[1:]
            for line in raw.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        headers = [
            match.group(1)
            for match in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", raw, re.MULTILINE)
        ]
        return "\n".join([*headers, *additions])
    try:
        return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(tool_input)


def estimate_added_lines(tool_input: dict[str, Any]) -> int:
    raw = str(tool_input.get("patch") or tool_input.get("command") or tool_input.get("cmd") or "")
    if "*** Begin Patch" not in raw:
        return 0
    return sum(1 for line in raw.splitlines() if line.startswith("+") and not line.startswith("+++"))


def build_context(
    *,
    north_star_goal: str,
    policies: list[str],
    tool_input: dict[str, Any],
    paths: list[str],
    policy_sources: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Build bounded, source-free metadata that survives observer fallback."""
    if not north_star_goal or not policies or not paths:
        return None
    hits = matching_policies(semantic_payload(tool_input), paths, policies)
    return {
        "north_star_goal": north_star_goal,
        "matched_policies": hits[:16],
        "matched_policy_sources": {
            policy: str((policy_sources or {}).get(policy) or NORTH_STAR_ALIGNMENT)
            for policy in hits[:16]
        },
        "added_lines": estimate_added_lines(tool_input),
    }


def matching_policies(text: str, paths: list[str], policies: list[str]) -> list[str]:
    combined = "\n".join([text, *paths])
    haystack = _normal_text(combined)
    target_words = _word_tokens(combined)
    target_set = set(target_words)
    hits: list[str] = []
    for policy in policies:
        value = str(policy).strip()
        needle = _normal_text(value)
        if needle and needle in haystack and value not in hits:
            hits.append(value)
            continue
        signature = _policy_signature(value)
        if not signature:
            continue
        signature_set = set(signature)
        overlap = signature_set & target_set
        if len(signature_set) == 1:
            matched = bool(overlap) and not signature_set <= WEAK_POLICY_TOKENS
        else:
            required = max(2, (len(signature_set) * 3 + 4) // 5)
            matched = len(overlap) >= required and _has_adjacent_policy_pair(signature, target_words)
        if matched and value not in hits:
            hits.append(value)
    return hits


def incident_id(
    north_star_goal: str,
    policy: str,
    alignment_layer: str = NORTH_STAR_ALIGNMENT,
) -> str:
    payload = _normal_text(north_star_goal) + "\n" + _normal_text(policy)
    if alignment_layer != NORTH_STAR_ALIGNMENT:
        payload += "\n" + alignment_layer
    return "DEV-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12].upper()


def _path_root(path: str, policy: str) -> str:
    value = path.replace("\\", "/").strip().strip("/")
    if not value:
        return value
    parts = list(PurePosixPath(value).parts)
    policy_tokens = {token for token in _normal_text(policy).split() if len(token) >= 3}
    for index, part in enumerate(parts):
        part_tokens = set(_normal_text(part).split())
        if policy_tokens & part_tokens:
            return "/".join(parts[: index + 1])
    return value


def _path_matches(path: str, root: str) -> bool:
    value = path.replace("\\", "/").strip().strip("/")
    expected = root.replace("\\", "/").strip().strip("/")
    return bool(expected and (value == expected or value.startswith(expected + "/")))


def _touch_paths(incident: dict[str, Any], paths: list[str]) -> None:
    roots = list(incident.get("affected_path_roots") or [])
    related = list(incident.get("related_paths") or [])
    policy = str(incident.get("policy") or "")
    for path in paths:
        if path and path not in related:
            related.append(path)
        root = _path_root(path, policy)
        if root and root not in roots:
            roots.append(root)
    incident["affected_path_roots"] = roots[:24]
    incident["related_paths"] = related[-40:]


def _append_history(incident: dict[str, Any], *, at: str, action: str, detail: str = "") -> None:
    history = list(incident.get("history") or [])
    history.append({"at": at, "action": action, "detail": detail})
    incident["history"] = history[-MAX_HISTORY:]


def _new_incident(
    identifier: str,
    goal: str,
    policy: str,
    paths: list[str],
    observed_at: str,
    alignment_layer: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "incident_id": identifier,
        "north_star_fingerprint": hashlib.sha256(_normal_text(goal).encode("utf-8")).hexdigest()[:16],
        "policy": policy,
        "alignment_layer": alignment_layer,
        "status": DEVIATION_DETECTED,
        "strike_count": 1,
        "first_detected_at": observed_at,
        "last_detected_at": observed_at,
        "last_confirmation_at": observed_at,
        "last_event_at": observed_at,
        "affected_path_roots": [],
        "related_paths": [],
        "writes_since_confirmation": 0,
        "added_lines_since_confirmation": 0,
        "paths_since_confirmation": [],
        "recurrence_count": 0,
        "correction": None,
        "clean_window": None,
        "history": [],
    }
    _touch_paths(row, paths)
    _append_history(row, at=observed_at, action="DETECTED", detail=policy)
    return row


def _confirm(incident: dict[str, Any], observed_at: str, reason: str) -> None:
    strike = min(3, int(incident.get("strike_count", 0) or 0) + 1)
    incident["strike_count"] = strike
    incident["status"] = RAIL_ENFORCED if strike >= 3 else CORRECTION_REQUIRED
    incident["last_confirmation_at"] = observed_at
    incident["last_detected_at"] = observed_at
    incident["last_event_at"] = observed_at
    incident["writes_since_confirmation"] = 0
    incident["added_lines_since_confirmation"] = 0
    incident["paths_since_confirmation"] = []
    _append_history(incident, at=observed_at, action=f"STRIKE_{strike}", detail=reason)


def _outcome(incident: dict[str, Any], *, deny: bool = False, detail: str = "") -> dict[str, Any]:
    status = str(incident.get("status") or DEVIATION_DETECTED)
    strike = int(incident.get("strike_count", 0) or 0)
    alignment_layer = str(incident.get("alignment_layer") or NORTH_STAR_ALIGNMENT)
    is_goal_contract = alignment_layer == GOAL_CONTRACT_ALIGNMENT
    label = "Goal contract deviation" if is_goal_contract else "North Star deviation"
    return_target = "Goal contract" if is_goal_contract else "North Star"
    if status == RAIL_ENFORCED or deny:
        reason = (
            f"{label} {incident['incident_id']} reached strike {strike}/3. "
            "This wrong-direction write is blocked; aligned work may continue. "
            "Use deviation-correct to open a scoped repair lane."
        )
        action = "open_scoped_correction_lane"
        intervention = RAIL_ENFORCED
    elif status == CORRECTION_REQUIRED:
        reason = (
            f"{label} {incident['incident_id']} is confirmed again at strike {strike}/3. "
            f"Return to the {return_target} before continuing this direction."
        )
        action = "correct_now"
        intervention = "STRONG_WARNING"
    else:
        reason = (
            f"{label} {incident['incident_id']} detected at strike {strike}/3. "
            "Correct this direction; unrelated successful commands do not clear the incident."
        )
        action = "return_to_goal_contract" if is_goal_contract else "return_to_north_star"
        intervention = "STRONG_WARNING"
    if detail:
        reason += " " + detail
    return {
        "signal": "GOAL_CONTRACT_DEVIATION" if is_goal_contract else "NORTH_STAR_DEVIATION",
        "incident_id": incident.get("incident_id"),
        "policy": incident.get("policy"),
        "alignment_layer": alignment_layer,
        "status": status,
        "strike_count": strike,
        "intervention": intervention,
        "deny": bool(status == RAIL_ENFORCED or deny),
        "reason": reason,
        "recommended_action": action,
        "affected_path_roots": list(incident.get("affected_path_roots") or []),
    }


def _correction_open(incident: dict[str, Any], paths: list[str], observed: dt.datetime) -> bool:
    if incident.get("status") != CORRECTION_IN_PROGRESS:
        return False
    correction = incident.get("correction") if isinstance(incident.get("correction"), dict) else {}
    try:
        expires = _parse_time(str(correction.get("expires_at") or ""))
    except (TypeError, ValueError):
        return False
    allowed = [str(path) for path in correction.get("allowed_paths", []) if str(path)]
    return observed <= expires and bool(paths) and all(any(_path_matches(path, root) for root in allowed) for path in paths)


def _recheck_due(incident: dict[str, Any], observed: dt.datetime) -> bool:
    try:
        previous = _parse_time(str(incident.get("last_confirmation_at") or incident.get("first_detected_at")))
    except (TypeError, ValueError):
        return True
    return any((
        (observed - previous).total_seconds() >= RECHECK_SECONDS,
        int(incident.get("writes_since_confirmation", 0) or 0) >= RECHECK_WRITE_EVENTS,
        int(incident.get("added_lines_since_confirmation", 0) or 0) >= RECHECK_ADDED_LINES,
        len(incident.get("paths_since_confirmation") or []) >= RECHECK_PATHS,
    ))


def _record_clean_activity(incident: dict[str, Any], observed_at: str, observed: dt.datetime) -> None:
    clean = incident.get("clean_window") if isinstance(incident.get("clean_window"), dict) else {}
    active_days = list(clean.get("active_days") or [])
    day = observed.date().isoformat()
    if day not in active_days:
        active_days.append(day)
    clean["active_days"] = active_days[-14:]
    clean["last_activity_at"] = observed_at
    incident["clean_window"] = clean


def _clear_if_mature(incident: dict[str, Any], observed_at: str, observed: dt.datetime) -> bool:
    clean = incident.get("clean_window") if isinstance(incident.get("clean_window"), dict) else {}
    try:
        started = _parse_time(str(clean.get("started_at") or ""))
    except (TypeError, ValueError):
        return False
    if (observed - started).total_seconds() < CLEAN_WINDOW_SECONDS:
        return False
    if len(set(clean.get("active_days") or [])) < MIN_CLEAN_ACTIVE_DAYS:
        return False
    incident["status"] = CLEARED_AFTER_7D
    incident["strike_count"] = 0
    incident["cleared_at"] = observed_at
    incident["last_event_at"] = observed_at
    _append_history(incident, at=observed_at, action="CLEARED_AFTER_7D", detail="corrected with active clean window")
    return True


def process_write(
    state: dict[str, Any] | None,
    *,
    north_star_goal: str,
    policies: list[str],
    tool_input: dict[str, Any],
    paths: list[str],
    observed_at: str,
    matched_policy_hits: list[str] | None = None,
    added_lines_override: int | None = None,
    policy_sources: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Update incidents for one product write and return visible interventions."""
    row = copy.deepcopy(state or {})
    incidents = dict(row.get("deviation_incidents") or {})
    observed = _parse_time(observed_at)
    semantic = semantic_payload(tool_input)
    hits = list(matched_policy_hits) if matched_policy_hits is not None else matching_policies(semantic, paths, policies)
    added_lines = int(added_lines_override) if added_lines_override is not None else estimate_added_lines(tool_input)
    outcomes: list[dict[str, Any]] = []
    handled: set[str] = set()

    for policy in hits:
        alignment_layer = str((policy_sources or {}).get(policy) or NORTH_STAR_ALIGNMENT)
        identifier = incident_id(north_star_goal, policy, alignment_layer)
        handled.add(identifier)
        incident = incidents.get(identifier)
        if alignment_layer != NORTH_STAR_ALIGNMENT and not isinstance(incident, dict):
            legacy_identifier = incident_id(north_star_goal, policy)
            legacy = incidents.pop(legacy_identifier, None)
            if isinstance(legacy, dict) and str(legacy.get("policy") or "") == policy:
                incident = legacy
                incident["incident_id"] = identifier
                incident["alignment_layer"] = alignment_layer
                _append_history(
                    incident,
                    at=observed_at,
                    action="ALIGNMENT_LAYER_MIGRATED",
                    detail="legacy North Star label corrected to Goal contract",
                )
                incidents[identifier] = incident
        if not isinstance(incident, dict) or incident.get("status") == CLEARED_AFTER_7D:
            incident = _new_incident(
                identifier,
                north_star_goal,
                policy,
                paths,
                observed_at,
                alignment_layer,
            )
            incidents[identifier] = incident
            outcomes.append(_outcome(incident, detail=f"Boundary: {policy}"))
            continue
        _touch_paths(incident, paths)
        incident["last_event_at"] = observed_at
        status = str(incident.get("status") or "")
        if status == CORRECTED_MONITORING:
            incident["recurrence_count"] = int(incident.get("recurrence_count", 0) or 0) + 1
            incident["strike_count"] = max(3, int(incident.get("strike_count", 0) or 0))
            incident["status"] = RAIL_ENFORCED
            incident["last_detected_at"] = observed_at
            incident["clean_window"] = None
            _append_history(incident, at=observed_at, action="RECURRENCE_DURING_CLEAN_WINDOW", detail=policy)
            outcomes.append(_outcome(incident, deny=True, detail=f"Boundary: {policy}"))
        elif status == CORRECTION_IN_PROGRESS and _correction_open(incident, paths, observed):
            _append_history(incident, at=observed_at, action="CORRECTION_WRITE", detail=", ".join(paths[:4]))
        elif status == RAIL_ENFORCED:
            outcomes.append(_outcome(incident, deny=True, detail=f"Boundary: {policy}"))
        else:
            _confirm(incident, observed_at, "same explicit boundary appeared again")
            outcomes.append(_outcome(incident, detail=f"Boundary: {policy}"))

    for identifier, incident in list(incidents.items()):
        if identifier in handled or not isinstance(incident, dict):
            continue
        status = str(incident.get("status") or "")
        if status == CORRECTED_MONITORING:
            _record_clean_activity(incident, observed_at, observed)
            _clear_if_mature(incident, observed_at, observed)
            continue
        roots = [str(root) for root in incident.get("affected_path_roots", []) if str(root)]
        related = bool(paths and any(_path_matches(path, root) for path in paths for root in roots))
        if not related:
            continue
        incident["last_event_at"] = observed_at
        if status == CORRECTION_IN_PROGRESS:
            if _correction_open(incident, paths, observed):
                _append_history(incident, at=observed_at, action="CORRECTION_WRITE", detail=", ".join(paths[:4]))
                continue
            incident["status"] = RAIL_ENFORCED
            _append_history(incident, at=observed_at, action="CORRECTION_LEASE_EXPIRED")
            outcomes.append(_outcome(incident, deny=True))
            continue
        if status == RAIL_ENFORCED:
            outcomes.append(_outcome(incident, deny=True))
            continue
        if status not in {DEVIATION_DETECTED, CORRECTION_REQUIRED}:
            continue
        incident["writes_since_confirmation"] = int(incident.get("writes_since_confirmation", 0) or 0) + 1
        incident["added_lines_since_confirmation"] = int(incident.get("added_lines_since_confirmation", 0) or 0) + added_lines
        pending_paths = list(incident.get("paths_since_confirmation") or [])
        for path in paths:
            if path not in pending_paths:
                pending_paths.append(path)
        incident["paths_since_confirmation"] = pending_paths[-20:]
        if _recheck_due(incident, observed):
            _confirm(incident, observed_at, "continued writes on the affected path reached the recheck threshold")
            outcomes.append(_outcome(incident))

    row["deviation_incidents"] = incidents
    _bound_incidents(row)
    outcomes.sort(key=lambda item: (not bool(item.get("deny")), -int(item.get("strike_count", 0) or 0)))
    return row, outcomes


def _bound_incidents(state: dict[str, Any]) -> None:
    incidents = dict(state.get("deviation_incidents") or {})
    if len(incidents) <= MAX_INCIDENTS:
        return
    removable = sorted(
        (
            (str(row.get("cleared_at") or row.get("last_event_at") or ""), identifier)
            for identifier, row in incidents.items()
            if isinstance(row, dict) and row.get("status") == CLEARED_AFTER_7D
        )
    )
    for _, identifier in removable:
        if len(incidents) <= MAX_INCIDENTS:
            break
        incidents.pop(identifier, None)
    state["deviation_incidents"] = incidents


def open_correction(
    state: dict[str, Any] | None,
    *,
    identifier: str,
    reason: str,
    allowed_paths: list[str],
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = copy.deepcopy(state or {})
    incidents = dict(row.get("deviation_incidents") or {})
    incident = incidents.get(identifier)
    if not isinstance(incident, dict):
        raise ValueError(f"unknown deviation incident: {identifier}")
    if incident.get("status") == CLEARED_AFTER_7D:
        raise ValueError("cleared deviation incident does not need a correction lane")
    paths = [path for path in allowed_paths if path] or list(incident.get("affected_path_roots") or [])
    if not paths:
        raise ValueError("correction lane requires at least one affected path")
    observed = _parse_time(observed_at)
    incident["status"] = CORRECTION_IN_PROGRESS
    incident["correction"] = {
        "started_at": observed_at,
        "expires_at": (observed + dt.timedelta(seconds=CORRECTION_LEASE_SECONDS)).isoformat(),
        "reason": reason,
        "allowed_paths": list(dict.fromkeys(paths)),
    }
    incident["last_event_at"] = observed_at
    _append_history(incident, at=observed_at, action="CORRECTION_IN_PROGRESS", detail=reason)
    row["deviation_incidents"] = incidents
    return row, copy.deepcopy(incident)


def mark_corrected(
    state: dict[str, Any] | None,
    *,
    identifier: str,
    evidence: str,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = copy.deepcopy(state or {})
    incidents = dict(row.get("deviation_incidents") or {})
    incident = incidents.get(identifier)
    if not isinstance(incident, dict):
        raise ValueError(f"unknown deviation incident: {identifier}")
    if incident.get("status") != CORRECTION_IN_PROGRESS:
        raise ValueError("incident must be in CORRECTION_IN_PROGRESS before it can be marked corrected")
    if not evidence.strip():
        raise ValueError("correction evidence is required")
    incident["status"] = CORRECTED_MONITORING
    incident["corrected_at"] = observed_at
    incident["last_event_at"] = observed_at
    incident["clean_window"] = {
        "started_at": observed_at,
        "active_days": [],
        "last_activity_at": None,
        "required_seconds": CLEAN_WINDOW_SECONDS,
        "required_active_days": MIN_CLEAN_ACTIVE_DAYS,
        "evidence": evidence,
    }
    _append_history(incident, at=observed_at, action="CORRECTED_MONITORING", detail=evidence)
    row["deviation_incidents"] = incidents
    return row, copy.deepcopy(incident)


def compact_summary(state: dict[str, Any] | None) -> dict[str, Any]:
    incidents = dict((state or {}).get("deviation_incidents") or {})
    active = [
        row for row in incidents.values()
        if isinstance(row, dict) and row.get("status") != CLEARED_AFTER_7D
    ]
    active.sort(key=lambda row: str(row.get("last_event_at") or ""), reverse=True)
    counts: dict[str, int] = {}
    for incident in incidents.values():
        if isinstance(incident, dict):
            status = str(incident.get("status") or "UNKNOWN")
            counts[status] = counts.get(status, 0) + 1
    return {
        "active_count": len(active),
        "rail_enforced_count": counts.get(RAIL_ENFORCED, 0),
        "corrected_monitoring_count": counts.get(CORRECTED_MONITORING, 0),
        "recheck_interval_minutes": RECHECK_SECONDS // 60,
        "clean_window_days": CLEAN_WINDOW_SECONDS // (24 * 60 * 60),
        "incidents": [
            {
                "incident_id": row.get("incident_id"),
                "status": row.get("status"),
                "strike_count": int(row.get("strike_count", 0) or 0),
                "policy": row.get("policy"),
                "alignment_layer": row.get("alignment_layer", NORTH_STAR_ALIGNMENT),
                "affected_path_roots": list(row.get("affected_path_roots") or [])[:4],
                "last_event_at": row.get("last_event_at"),
            }
            for row in active[:8]
        ],
        "status_counts": dict(sorted(counts.items())),
    }
