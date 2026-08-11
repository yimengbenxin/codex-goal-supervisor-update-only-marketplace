"""Mandatory, cached software-reuse reconnaissance for implementation work.

The probe searches public GitHub repositories before a mutation ticket starts,
persists candidates, and refreshes them after five days.  It does not clone or
execute third-party code.  A strong direct-reuse candidate requires one short
ticket decision before custom implementation may begin.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .state_store import load_json, utc_now_iso, write_json


CONFIG_NAME = "reuse_probe_config.json"
STATE_NAME = "reuse_probe.json"
SCHEMA_VERSION = 1
DEFAULT_TTL_DAYS = 5
DEFAULT_TIMEOUT_SECONDS = 6.0
MAX_CANDIDATES = 5
VALID_DECISIONS = {
    "ADOPT_EXISTING",
    "EXTEND_EXISTING",
    "REJECT_WITH_EVIDENCE",
    "NO_SUITABLE_REUSE",
}
VALID_UPDATE_DECISIONS = {"INCORPORATE", "DEFER", "NOT_APPLICABLE"}

STOP_WORDS = {
    "add", "build", "create", "develop", "implement", "make", "project", "system", "tool",
    "feature", "current", "task", "ticket", "use", "using", "with", "from", "into", "this", "that",
    "continue", "confirmed", "remaining", "integrate", "integration",
    "the", "and", "for", "are", "one", "new", "minimal", "mvp", "support", "service",
    "增加", "实现", "开发", "项目", "系统", "功能", "当前", "任务", "支持", "一个", "这个",
}

# These words describe desirable properties of almost any project. They may
# help discover references, but they cannot establish direct reuse on their
# own; otherwise unrelated popular repositories become blocking candidates.
LOW_SIGNAL_DIRECT_TERMS = {
    "artifact", "bounded", "deliver", "delivery", "maintain", "maintenance",
    "manager", "management", "pipeline", "product", "quality", "reliable",
    "reliability", "validate", "validated", "validation", "verification",
    "verify", "workflow",
    "产物", "交付", "产品", "可靠", "工作流", "维护", "验证", "质量",
}


def default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": True,
        "required_before_mutation": True,
        "ttl_days": DEFAULT_TTL_DAYS,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_candidates": MAX_CANDIDATES,
        "github_token_env": "GITHUB_TOKEN",
        "source": "github_repository_search",
        "direct_reuse_policy": "decision_required_before_custom_implementation",
    }


def ensure_config(agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    path = agent_dir / CONFIG_NAME
    current = load_json(path, {})
    if not isinstance(current, dict) or not current:
        current = default_config()
        write_json(path, current)
        return current
    merged = default_config()
    merged.update(current)
    if merged != current:
        write_json(path, merged)
    return merged


def _state_path(agent_dir: Path) -> Path:
    return agent_dir / "runtime" / STATE_NAME


def _load_state(agent_dir: Path) -> dict[str, Any]:
    state = load_json(_state_path(agent_dir), {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("schema_version", SCHEMA_VERSION)
    state.setdefault("probes", {})
    state.setdefault("seen_candidates", {})
    state.setdefault("project_started_at", None)
    state.setdefault("last_probe", {})
    state.setdefault("project_decision", {})
    state.setdefault("project_integration", {})
    state.setdefault("project_update_decision", {})
    return state


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _expires_at(checked_at: str, ttl_days: int) -> str:
    base = _parse_time(checked_at) or _now()
    return (base + dt.timedelta(days=max(1, ttl_days))).replace(microsecond=0).isoformat()


def _project_languages() -> list[str]:
    markers = {
        "pyproject.toml": "Python",
        "requirements.txt": "Python",
        "package.json": "JavaScript",
        "tsconfig.json": "TypeScript",
        "go.mod": "Go",
        "Cargo.toml": "Rust",
        "pom.xml": "Java",
        "build.gradle": "Java",
        "Package.swift": "Swift",
        "CMakeLists.txt": "C++",
    }
    found = [language for path, language in markers.items() if Path(path).is_file()]
    return list(dict.fromkeys(found))


def _query_terms(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}|[\u4e00-\u9fff]{2,12}", text.lower())
    terms: list[str] = []
    for term in raw:
        cleaned = term.strip("._+-")
        if len(cleaned) < 3 or cleaned in STOP_WORDS or cleaned in terms:
            continue
        terms.append(cleaned)
        if len(terms) >= 8:
            break
    return terms


def _context_query_terms(
    ticket: dict[str, Any],
    north_star: dict[str, Any],
    remaining_actions: list[str] | None,
) -> list[str]:
    action_text = " ".join([
        str(ticket.get("task_goal") or ""),
        *[str(value) for value in ticket.get("must_do", [])],
        *[str(value) for value in (remaining_actions or [])],
    ])
    goal_text = " ".join([
        str(north_star.get("goal") or ""),
        *[str(value) for value in north_star.get("main_path", [])],
        *[str(value) for value in north_star.get("allowed_subgoals", [])],
    ])
    action_terms = _query_terms(action_text)
    goal_terms = _query_terms(goal_text)
    combined: list[str] = []
    for index in range(max(len(action_terms), len(goal_terms))):
        for source in (action_terms, goal_terms):
            if index < len(source) and source[index] not in combined:
                combined.append(source[index])
            if len(combined) >= 8:
                return combined
    return combined


def _fingerprint(
    ticket: dict[str, Any],
    north_star: dict[str, Any],
    remaining_actions: list[str] | None = None,
) -> str:
    payload = {
        "goal": north_star.get("goal"),
        "task_goal": ticket.get("task_goal"),
        "must_do": ticket.get("must_do", []),
        "remaining_actions": remaining_actions or [],
        "languages": _project_languages(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "codex-goal-supervisor-reuse-probe/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get(str(config.get("github_token_env") or "GITHUB_TOKEN"), "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_json(url: str, config: dict[str, Any], timeout: float | None = None) -> tuple[Any, str | None]:
    request = urllib.request.Request(url, headers=_headers(config), method="GET")
    request_timeout = max(0.5, min(float(timeout or config.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS), 8.0))
    try:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            return json.loads(response.read(2_000_000).decode("utf-8", errors="replace")), None
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__


def _fixture_payload() -> dict[str, Any] | None:
    fixture = os.environ.get("GOAL_COMPASS_REUSE_PROBE_FIXTURE", "").strip()
    if not fixture:
        return None
    data = load_json(Path(fixture), {})
    return data if isinstance(data, dict) else {}


def _search_payload(terms: list[str], config: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    fixture = _fixture_payload()
    if fixture is not None:
        return fixture, None
    # GitHub combines bare search terms with AND. A long task sentence therefore
    # over-constrains discovery; two high-signal terms recover the base project,
    # while the candidate scorer still requires explicit compatibility evidence.
    query = " ".join(terms[:2]) or "software"
    params = urllib.parse.urlencode({
        "q": f"{query} in:name,description archived:false",
        "sort": "stars",
        "order": "desc",
        "per_page": max(1, min(int(config.get("max_candidates") or MAX_CANDIDATES), 10)),
    })
    base = os.environ.get("GOAL_COMPASS_GITHUB_API_BASE", "https://api.github.com").rstrip("/")
    data, error = _http_json(f"{base}/search/repositories?{params}", config)
    return data if isinstance(data, dict) else None, error


def _latest_release(full_name: str, config: dict[str, Any], fixture_release: Any = None) -> str | None:
    if fixture_release is not None:
        return str(fixture_release) if fixture_release else None
    if _fixture_payload() is not None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        return None
    base = os.environ.get("GOAL_COMPASS_GITHUB_API_BASE", "https://api.github.com").rstrip("/")
    data, error = _http_json(f"{base}/repos/{full_name}/releases/latest", config, timeout=2.0)
    if error or not isinstance(data, dict):
        return None
    return str(data.get("tag_name") or "") or None


def _candidate(item: dict[str, Any], terms: list[str], languages: list[str], config: dict[str, Any]) -> dict[str, Any]:
    license_data = item.get("license") if isinstance(item.get("license"), dict) else {}
    full_name = str(item.get("full_name") or item.get("name") or "")
    searchable = " ".join([
        full_name,
        str(item.get("description") or ""),
        " ".join(str(value) for value in item.get("topics", []) if value),
    ]).lower()
    matched = [term for term in terms if term.lower() in searchable]
    distinctive_matches = [term for term in matched if term.lower() not in LOW_SIGNAL_DIRECT_TERMS]
    language = str(item.get("language") or "")
    language_match = not languages or not language or language.lower() in {value.lower() for value in languages}
    stars = int(item.get("stargazers_count", 0) or 0)
    license_id = str(license_data.get("spdx_id") or license_data.get("key") or "").strip()
    archived = bool(item.get("archived"))
    direct = bool(
        len(matched) >= 2
        and distinctive_matches
        and license_id
        and not archived
        and stars >= 50
        and language_match
    )
    release = _latest_release(full_name, config, fixture_release=item.get("latest_release"))
    return {
        "name": full_name,
        "url": item.get("html_url"),
        "description": str(item.get("description") or "")[:500],
        "stars": stars,
        "language": language or None,
        "license": license_id or None,
        "archived": archived,
        "pushed_at": item.get("pushed_at"),
        "updated_at": item.get("updated_at"),
        "latest_release": release,
        "matched_terms": matched,
        "reuse_fit": "DIRECT_REUSE_CANDIDATE" if direct else "REFERENCE_CANDIDATE",
        "requires_compatibility_review": True,
    }


def _updates(candidates: list[dict[str, Any]], seen: dict[str, Any]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for row in candidates:
        previous = seen.get(str(row.get("name")))
        if not isinstance(previous, dict):
            continue
        old_release = previous.get("latest_release")
        new_release = row.get("latest_release")
        old_push = previous.get("pushed_at")
        new_push = row.get("pushed_at")
        if (old_release and new_release and old_release != new_release) or (old_push and new_push and old_push != new_push):
            updates.append({
                "name": row.get("name"),
                "url": row.get("url"),
                "previous_release": old_release,
                "latest_release": new_release,
                "previous_pushed_at": old_push,
                "latest_pushed_at": new_push,
                "required_action": "review_new_capabilities_before_custom_implementation",
            })
    return updates


def _refresh_previously_seen_updates(
    seen: dict[str, Any],
    current_candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Refresh old candidates that are no longer returned by the current goal query."""
    if _fixture_payload() is not None:
        return []
    current_names = {str(row.get("name") or "") for row in current_candidates}
    names = [
        name for name, row in seen.items()
        if name not in current_names and isinstance(row, dict)
    ][: max(1, min(int(config.get("max_candidates") or MAX_CANDIDATES), 10))]
    if not names:
        return []

    base = os.environ.get("GOAL_COMPASS_GITHUB_API_BASE", "https://api.github.com").rstrip("/")

    def refresh(name: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
            return None
        data, error = _http_json(f"{base}/repos/{name}", config, timeout=3.0)
        if error or not isinstance(data, dict):
            return None
        previous = seen.get(name, {})
        latest_release = _latest_release(name, config)
        old_release = previous.get("latest_release")
        old_push = previous.get("pushed_at")
        new_push = data.get("pushed_at")
        if not (
            (old_release and latest_release and old_release != latest_release)
            or (old_push and new_push and old_push != new_push)
        ):
            return None
        return {
            "name": name,
            "url": data.get("html_url") or previous.get("url"),
            "previous_release": old_release,
            "latest_release": latest_release,
            "previous_pushed_at": old_push,
            "latest_pushed_at": new_push,
            "required_action": "review_new_capabilities_against_remaining_project_actions",
        }

    updates: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(names))) as pool:
        for row in pool.map(refresh, names):
            if row:
                updates.append(row)
    return updates


def _fresh(entry: dict[str, Any]) -> bool:
    expires = _parse_time(str(entry.get("expires_at") or ""))
    return bool(expires and expires > _now())


def is_due(discovery: dict[str, Any] | None) -> bool:
    if not isinstance(discovery, dict) or not discovery:
        return True
    if discovery.get("status") == "SKIPPED_READ_ONLY":
        return False
    if discovery.get("status") not in {"COMPLETE", "NO_CANDIDATES"}:
        return True
    return not _fresh(discovery)


def project_contract(agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    state = _load_state(agent_dir)
    return {
        "discovery": json.loads(json.dumps(state.get("last_probe", {}))),
        "decision": json.loads(json.dumps(state.get("project_decision", {}))),
        "integration": json.loads(json.dumps(state.get("project_integration", {}))),
        "update_decision": json.loads(json.dumps(state.get("project_update_decision", {}))),
        "project_started_at": state.get("project_started_at"),
    }


def attach_project_contract(ticket: dict[str, Any], agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    contract = project_contract(agent_dir)
    if not ticket.get("reuse_discovery") and contract.get("discovery"):
        ticket["reuse_discovery"] = contract["discovery"]
    ticket["reuse_decision"] = contract.get("decision", {})
    ticket["reuse_integration"] = contract.get("integration", {})
    ticket["reuse_update_decision"] = contract.get("update_decision", {})
    return ticket


def probe(
    ticket: dict[str, Any],
    north_star: dict[str, Any],
    *,
    agent_dir: Path = Path(".agent"),
    force: bool = False,
    remaining_actions: list[str] | None = None,
) -> dict[str, Any]:
    config = ensure_config(agent_dir)
    if config.get("enabled") is False or ticket.get("execution_mode") == "read_only":
        return {
            "status": "SKIPPED_READ_ONLY" if ticket.get("execution_mode") == "read_only" else "DISABLED",
            "checked_at": utc_now_iso(),
            "expires_at": None,
            "candidates": [],
            "direct_reuse_candidates": [],
            "updates": [],
            "required_action": "continue",
        }
    state = _load_state(agent_dir)
    checked_at = utc_now_iso()
    if not state.get("project_started_at"):
        state["project_started_at"] = checked_at
    fingerprint = _fingerprint(ticket, north_star, remaining_actions)
    cached = state.get("last_probe")
    if (
        isinstance(cached, dict)
        and cached.get("status") in {"COMPLETE", "NO_CANDIDATES"}
        and _fresh(cached)
        and not force
    ):
        result = json.loads(json.dumps(cached))
        result["cache_reused"] = True
        result["project_scope"] = "PROJECT"
        result["context_changed_since_probe"] = fingerprint != cached.get("context_fingerprint")
        return result

    terms = _context_query_terms(ticket, north_star, remaining_actions)
    payload, error = _search_payload(terms, config)
    if error or payload is None:
        result = {
            "status": "UNAVAILABLE",
            "checked_at": checked_at,
            "expires_at": _expires_at(checked_at, 1),
            "context_fingerprint": fingerprint,
            "query_terms": terms,
            "remaining_actions": list(remaining_actions or [])[:40],
            "project_scope": "PROJECT",
            "source": config.get("source"),
            "error": error or "invalid_search_response",
            "candidates": [],
            "direct_reuse_candidates": [],
            "updates": [],
            "required_action": "retry_reuse_probe_before_implementation",
        }
    else:
        items = payload.get("items", []) if isinstance(payload.get("items"), list) else []
        languages = _project_languages()
        candidates = [
            _candidate(item, terms, languages, config)
            for item in items[: max(1, min(int(config.get("max_candidates") or MAX_CANDIDATES), 10))]
            if isinstance(item, dict)
        ]
        candidates = [row for row in candidates if row.get("matched_terms")]
        updates = _updates(candidates, state.get("seen_candidates", {}))
        seen_updates = _refresh_previously_seen_updates(
            state.get("seen_candidates", {}), candidates, config,
        )
        known_update_names = {str(row.get("name") or "") for row in updates}
        updates.extend(row for row in seen_updates if str(row.get("name") or "") not in known_update_names)
        direct = [row for row in candidates if row.get("reuse_fit") == "DIRECT_REUSE_CANDIDATE"]
        ttl_days = max(1, int(config.get("ttl_days") or DEFAULT_TTL_DAYS))
        result = {
            "status": "COMPLETE" if candidates else "NO_CANDIDATES",
            "checked_at": checked_at,
            "expires_at": _expires_at(checked_at, ttl_days),
            "context_fingerprint": fingerprint,
            "query_terms": terms,
            "remaining_actions": list(remaining_actions or [])[:40],
            "project_scope": "PROJECT",
            "source": config.get("source"),
            "candidates": candidates,
            "direct_reuse_candidates": direct,
            "updates": updates,
            "cache_reused": False,
            "required_action": (
                "review_candidate_updates" if updates else
                "choose_reuse_disposition" if direct else
                "continue"
            ),
        }
        for row in candidates:
            state.setdefault("seen_candidates", {})[str(row.get("name"))] = {
                "url": row.get("url"),
                "latest_release": row.get("latest_release"),
                "pushed_at": row.get("pushed_at"),
                "observed_at": checked_at,
            }
    previous = state.get("last_probe", {}) if isinstance(state.get("last_probe"), dict) else {}
    previous_candidates = {
        str(row.get("url") or row.get("name") or "")
        for row in previous.get("direct_reuse_candidates", [])
        if isinstance(row, dict)
    }
    current_candidates = {
        str(row.get("url") or row.get("name") or "")
        for row in result.get("direct_reuse_candidates", [])
        if isinstance(row, dict)
    }
    if previous_candidates != current_candidates:
        state["project_decision"] = {}
        state["project_integration"] = {}
        state["project_update_decision"] = {}
    elif result.get("updates"):
        state["project_update_decision"] = {}
    state["probes"] = {fingerprint: result}
    state["last_probe"] = result
    state["last_activity_at"] = checked_at
    state["pending_context_fingerprint"] = None
    state["pending_remaining_actions"] = []
    write_json(_state_path(agent_dir), state)
    return result


def apply_decision(
    ticket: dict[str, Any],
    *,
    decision: str,
    rationale: str,
    candidate: str | None = None,
    update_decision: str | None = None,
    integration_plan: str | None = None,
    integration_validation_ids: list[str] | None = None,
    agent_dir: Path | None = None,
) -> dict[str, Any]:
    normalized = decision.strip().upper()
    if normalized not in VALID_DECISIONS:
        raise ValueError(f"unsupported reuse decision: {decision}")
    ticket["reuse_decision"] = {
        "status": normalized,
        "candidate": candidate,
        "rationale": rationale.strip(),
        "decided_at": utc_now_iso(),
    }
    if normalized in {"ADOPT_EXISTING", "EXTEND_EXISTING"}:
        validation_ids = list(dict.fromkeys(str(value) for value in (integration_validation_ids or []) if value))
        ticket["reuse_integration"] = {
            "status": "PLANNED",
            "candidate": candidate,
            "mode": "ADOPT" if normalized == "ADOPT_EXISTING" else "EXTEND",
            "plan": str(integration_plan or "").strip(),
            "validation_ids": validation_ids,
            "added_to_ticket_at": utc_now_iso(),
        }
        must_do = ticket.setdefault("must_do", [])
        integration_requirement = (
            f"Reuse {candidate} through this integration plan: {str(integration_plan or '').strip()}"
        )
        if integration_requirement not in must_do:
            must_do.append(integration_requirement)
        ticket_validation_ids = ticket.setdefault("validation_ids", [])
        acceptance = ticket.setdefault("acceptance", {})
        acceptance_commands = acceptance.setdefault("commands_pass", [])
        for validation_id in validation_ids:
            if validation_id not in ticket_validation_ids:
                ticket_validation_ids.append(validation_id)
            if validation_id not in acceptance_commands:
                acceptance_commands.append(validation_id)
    else:
        ticket["reuse_integration"] = {}
    if update_decision:
        update_normalized = update_decision.strip().upper()
        if update_normalized not in VALID_UPDATE_DECISIONS:
            raise ValueError(f"unsupported update decision: {update_decision}")
        ticket["reuse_update_decision"] = {
            "status": update_normalized,
            "rationale": rationale.strip(),
            "decided_at": utc_now_iso(),
        }
    if agent_dir is not None:
        state = _load_state(agent_dir)
        state["project_decision"] = json.loads(json.dumps(ticket.get("reuse_decision", {})))
        state["project_integration"] = json.loads(json.dumps(ticket.get("reuse_integration", {})))
        state["project_update_decision"] = json.loads(json.dumps(ticket.get("reuse_update_decision", {})))
        write_json(_state_path(agent_dir), state)
    return ticket


def mark_integration_verified(ticket: dict[str, Any], agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    integration = ticket.get("reuse_integration", {}) if isinstance(ticket.get("reuse_integration"), dict) else {}
    if integration.get("status") != "PLANNED":
        return ticket
    integration = json.loads(json.dumps(integration))
    integration["status"] = "VERIFIED"
    integration["verified_at"] = utc_now_iso()
    ticket["reuse_integration"] = integration
    state = _load_state(agent_dir)
    state["project_integration"] = json.loads(json.dumps(integration))
    write_json(_state_path(agent_dir), state)
    return ticket


def contract_errors(ticket: dict[str, Any]) -> list[str]:
    if ticket.get("execution_mode") == "read_only":
        return []
    discovery = ticket.get("reuse_discovery")
    if not isinstance(discovery, dict) or not discovery:
        return ["reuse discovery is required before implementation"]
    if discovery.get("status") not in {"COMPLETE", "NO_CANDIDATES"}:
        return [f"reuse discovery unavailable: {discovery.get('status')}; retry before implementation"]
    errors: list[str] = []
    direct = discovery.get("direct_reuse_candidates", [])
    decision = ticket.get("reuse_decision", {}) if isinstance(ticket.get("reuse_decision"), dict) else {}
    if direct:
        status = str(decision.get("status") or "").upper()
        rationale = str(decision.get("rationale") or "").strip()
        if status not in VALID_DECISIONS or status == "NO_SUITABLE_REUSE":
            errors.append("direct reuse candidate exists; choose ADOPT_EXISTING, EXTEND_EXISTING, or REJECT_WITH_EVIDENCE")
        if len(rationale) < 20:
            errors.append("reuse decision requires a concrete compatibility rationale (20+ characters)")
        if status in {"ADOPT_EXISTING", "EXTEND_EXISTING"} and not decision.get("candidate"):
            errors.append("reuse decision must name the adopted or extended candidate URL")
        if status in {"ADOPT_EXISTING", "EXTEND_EXISTING"}:
            valid_candidates = {
                str(row.get("url") or row.get("name") or "")
                for row in direct if isinstance(row, dict)
            }
            if str(decision.get("candidate") or "") not in valid_candidates:
                errors.append("adopted or extended candidate must be one of the current direct-reuse candidates")
            integration = ticket.get("reuse_integration", {}) if isinstance(ticket.get("reuse_integration"), dict) else {}
            if integration.get("status") not in {"PLANNED", "VERIFIED"}:
                errors.append("a suitable reusable tool must have a PLANNED or VERIFIED project integration")
            if len(str(integration.get("plan") or "").strip()) < 20:
                errors.append("reuse integration requires a concrete project usage plan (20+ characters)")
            validation_ids = [str(value) for value in integration.get("validation_ids", []) if value]
            if not validation_ids:
                errors.append("reuse integration requires at least one validation catalog id")
            if integration.get("status") != "VERIFIED":
                ticket_validations = {
                    str(value) for value in ticket.get("validation_ids", [])
                } | {
                    str(value) for value in ticket.get("acceptance", {}).get("commands_pass", [])
                }
                missing = [value for value in validation_ids if value not in ticket_validations]
                if missing:
                    errors.append("reuse integration validations are missing from the ticket contract: " + ", ".join(missing))
    updates = discovery.get("updates", [])
    if updates:
        update = ticket.get("reuse_update_decision", {}) if isinstance(ticket.get("reuse_update_decision"), dict) else {}
        if str(update.get("status") or "").upper() not in VALID_UPDATE_DECISIONS:
            errors.append("previously seen reusable software changed; record INCORPORATE, DEFER, or NOT_APPLICABLE")
    return errors


def compact_status(ticket: dict[str, Any] | None, agent_dir: Path = Path(".agent")) -> dict[str, Any]:
    contract = project_contract(agent_dir)
    discovery = ticket.get("reuse_discovery", {}) if isinstance(ticket, dict) else {}
    decision = ticket.get("reuse_decision", {}) if isinstance(ticket, dict) else {}
    integration = ticket.get("reuse_integration", {}) if isinstance(ticket, dict) else {}
    if not discovery:
        discovery = contract.get("discovery", {})
    if not decision:
        decision = contract.get("decision", {})
    if not integration:
        integration = contract.get("integration", {})
    update_decision = ticket.get("reuse_update_decision", {}) if isinstance(ticket, dict) else {}
    if not update_decision:
        update_decision = contract.get("update_decision", {})
    required_action = discovery.get("required_action", "run_reuse_probe")
    decision_status = str(decision.get("status") or "")
    integration_status = str(integration.get("status") or "")
    if discovery.get("updates") and not update_decision.get("status"):
        required_action = "review_candidate_updates"
    elif decision_status in {"ADOPT_EXISTING", "EXTEND_EXISTING"}:
        required_action = "implement_and_validate_reuse" if integration_status == "PLANNED" else "continue"
    elif decision_status == "REJECT_WITH_EVIDENCE" or not discovery.get("direct_reuse_candidates"):
        required_action = "continue"
    return {
        "status": discovery.get("status"),
        "checked_at": discovery.get("checked_at"),
        "refresh_due_at": discovery.get("expires_at"),
        "refresh_due": is_due(discovery) if discovery else True,
        "candidate_count": len(discovery.get("candidates", [])) if isinstance(discovery, dict) else 0,
        "direct_reuse_candidate_count": len(discovery.get("direct_reuse_candidates", [])) if isinstance(discovery, dict) else 0,
        "update_count": len(discovery.get("updates", [])) if isinstance(discovery, dict) else 0,
        "decision": decision.get("status"),
        "integration_status": integration.get("status"),
        "project_scope": discovery.get("project_scope"),
        "context_changed_since_probe": bool(discovery.get("context_changed_since_probe")),
        "required_action": required_action,
    }
