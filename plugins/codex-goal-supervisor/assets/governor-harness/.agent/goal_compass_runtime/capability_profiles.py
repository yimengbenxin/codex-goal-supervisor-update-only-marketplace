"""Read-only capability catalog and monotonic runtime-profile resolution."""
from __future__ import annotations

import copy
import functools
import json
from pathlib import Path
from typing import Any


CATALOG_FILE = "capability_catalog.v1.json"
PROFILE_FILES = {
    "general-initial": "general_profile_initial.v1.json",
    "goal-2.8.10-compatibility": "goal_profile_2_8_10.v1.json",
}
POLICY_ENUMS = {
    "availability": {"available", "compatibility_only", "disabled"},
    "obligation": {"optional", "conditional", "required"},
    "invocation": {"explicit", "background", "either", "internal"},
    "enforcement": {"none", "advisory", "targeted_block"},
}
OBLIGATION_RANK = {"optional": 0, "conditional": 1, "required": 2}
ENFORCEMENT_RANK = {"none": 0, "advisory": 1, "targeted_block": 2}


class CapabilityProfileError(ValueError):
    pass


def default_contracts_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "contracts"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityProfileError(f"invalid capability contract: {path}") from exc
    if not isinstance(value, dict):
        raise CapabilityProfileError(f"capability contract must be an object: {path}")
    return value


def load_catalog(contracts_dir: Path | None = None) -> dict[str, Any]:
    root = contracts_dir or default_contracts_dir()
    catalog = _load_json(root / CATALOG_FILE)
    rows = catalog.get("capabilities")
    if not isinstance(rows, list) or not rows:
        raise CapabilityProfileError("capability catalog is empty")
    ids = [str(row.get("id") or "") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise CapabilityProfileError("capability ids must be non-empty and unique")
    owners = [str(row.get("owner") or "") for row in rows if isinstance(row, dict)]
    if any(not value for value in owners):
        raise CapabilityProfileError("every capability requires one implementation owner")
    return catalog


def load_profile(profile_id: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    root = contracts_dir or default_contracts_dir()
    filename = PROFILE_FILES.get(str(profile_id))
    if not filename:
        raise CapabilityProfileError(f"unknown capability profile: {profile_id}")
    profile = _load_json(root / filename)
    if profile.get("profile_id") != profile_id:
        raise CapabilityProfileError(f"profile identity mismatch: {profile_id}")
    return profile


def _validate_policy(capability_id: str, policy: dict[str, Any]) -> None:
    for key, allowed in POLICY_ENUMS.items():
        if policy.get(key) not in allowed:
            raise CapabilityProfileError(f"{capability_id}.{key} has unsupported value: {policy.get(key)}")
    if not isinstance(policy.get("preconditions"), list):
        raise CapabilityProfileError(f"{capability_id}.preconditions must be a list")


def _merge_policy(capability_id: str, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(parent)
    merged.update(copy.deepcopy(child))
    _validate_policy(capability_id, merged)
    if OBLIGATION_RANK[merged["obligation"]] < OBLIGATION_RANK[parent["obligation"]]:
        raise CapabilityProfileError(f"{capability_id} cannot weaken inherited obligation")
    if ENFORCEMENT_RANK[merged["enforcement"]] < ENFORCEMENT_RANK[parent["enforcement"]]:
        raise CapabilityProfileError(f"{capability_id} cannot weaken inherited enforcement")
    return merged


@functools.lru_cache(maxsize=16)
def _resolve_profile_cached(profile_id: str, root_value: str) -> dict[str, Any]:
    root = Path(root_value)
    catalog = load_catalog(root)
    capability_ids = [str(row["id"]) for row in catalog["capabilities"]]
    seen: set[str] = set()

    def resolve(current_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        if current_id in seen:
            raise CapabilityProfileError(f"profile inheritance cycle: {current_id}")
        seen.add(current_id)
        profile = load_profile(current_id, root)
        defaults = profile.get("defaults") if isinstance(profile.get("defaults"), dict) else {}
        parent_id = str(profile.get("inherits") or "").strip()
        if parent_id:
            policies, sources = resolve(parent_id)
        else:
            base = {
                "availability": str(defaults.get("availability") or "available"),
                "obligation": str(defaults.get("obligation") or "optional"),
                "invocation": str(defaults.get("invocation") or "explicit"),
                "enforcement": str(defaults.get("enforcement") or "none"),
                "preconditions": list(defaults.get("preconditions") or []),
            }
            _validate_policy("defaults", base)
            policies = {capability_id: copy.deepcopy(base) for capability_id in capability_ids}
            sources = {capability_id: [current_id + ":defaults"] for capability_id in capability_ids}
        overrides = profile.get("policies") if isinstance(profile.get("policies"), dict) else {}
        unknown = sorted(set(overrides) - set(capability_ids))
        if unknown:
            raise CapabilityProfileError("profile references unknown capabilities: " + ", ".join(unknown))
        for capability_id, override in overrides.items():
            if not isinstance(override, dict):
                raise CapabilityProfileError(f"{capability_id} policy must be an object")
            policies[capability_id] = _merge_policy(capability_id, policies[capability_id], override)
            sources[capability_id] = [*sources[capability_id], current_id]
        seen.remove(current_id)
        return policies, sources

    policies, sources = resolve(profile_id)
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "capability_count": len(capability_ids),
        "policies": policies,
        "sources": sources,
    }


def resolve_profile(profile_id: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    root = (contracts_dir or default_contracts_dir()).resolve()
    return copy.deepcopy(_resolve_profile_cached(profile_id, str(root)))


def explain_capability(profile_id: str, capability_id: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    resolved = resolve_profile(profile_id, contracts_dir)
    if capability_id not in resolved["policies"]:
        raise CapabilityProfileError(f"unknown capability: {capability_id}")
    return {
        "profile_id": profile_id,
        "capability_id": capability_id,
        "policy": copy.deepcopy(resolved["policies"][capability_id]),
        "sources": list(resolved["sources"][capability_id]),
    }
