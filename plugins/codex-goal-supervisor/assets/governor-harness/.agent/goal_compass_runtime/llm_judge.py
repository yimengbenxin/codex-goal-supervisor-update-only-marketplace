"""Sparse, read-only Codex CLI judgment for ambiguous high-cost decisions."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from goal_compass_runtime.state_store import load_json, utc_now_iso, write_json


POLICY_VERSION = "1.0"
MAX_CACHE_ENTRIES = 64
DEFAULT_TIMEOUT_SECONDS = 45.0
VERDICTS = {
    "CONFIRM_GOAL_CHANGE",
    "CONFIRM_TARGETED_RAIL",
    "WARN_AND_RECHECK",
    "ALLOW_SCOPED_ACTION",
    "INSUFFICIENT_EVIDENCE",
}
SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "confidence", "rationale", "recommended_action", "evidence_needed"],
    "properties": {
        "verdict": {"type": "string", "enum": sorted(VERDICTS)},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string", "maxLength": 1200},
        "recommended_action": {"type": "string", "maxLength": 400},
        "evidence_needed": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 8},
    },
}


def ensure_schema(path: Path) -> None:
    if load_json(path, {}) != SCHEMA:
        write_json(path, SCHEMA)


def _codex_command() -> list[str] | None:
    configured = str(os.environ.get("GOAL_SUPERVISOR_JUDGE_CMD") or "").strip()
    if configured:
        if os.name == "nt":
            return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"} else part
                    for part in shlex.split(configured, posix=False)]
        return shlex.split(configured)
    found = shutil.which("codex")
    if found:
        return [found]
    mac_app = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    if mac_app.is_file():
        return [str(mac_app)]
    return None


def _safe_packet(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "trigger", "north_star_goal", "success_criteria", "current_stage",
        "current_action", "expected_evidence", "observed_evidence",
        "policy_boundary", "alignment_layer", "goal_contract",
        "affected_paths", "consequence", "appeal",
    }
    packet = {key: value.get(key) for key in allowed if key in value}
    packet["affected_paths"] = [str(path)[:300] for path in packet.get("affected_paths", [])[:16]] if isinstance(packet.get("affected_paths"), list) else []
    packet["success_criteria"] = packet.get("success_criteria", [])[:24] if isinstance(packet.get("success_criteria"), list) else []
    packet["observed_evidence"] = packet.get("observed_evidence", [])[:24] if isinstance(packet.get("observed_evidence"), list) else []
    contract = packet.get("goal_contract") if isinstance(packet.get("goal_contract"), dict) else {}
    modules = [row for row in contract.get("modules", []) if isinstance(row, dict)][:12]
    packet["goal_contract"] = {
        "objective": str(contract.get("objective") or "")[:800],
        "current_state": str(contract.get("current_state") or "")[:600],
        "desired_state": str(contract.get("desired_state") or "")[:600],
        "source_requirements": [str(item)[:400] for item in contract.get("source_requirements", [])[:12]]
        if isinstance(contract.get("source_requirements"), list) else [],
        "first_principles": [
            {
                "principle": str(row.get("principle") or "")[:400],
                "rationale": str(row.get("rationale") or "")[:500],
                "implications": [str(item)[:300] for item in row.get("implications", [])[:8]],
            }
            for row in contract.get("first_principles", [])[:8]
            if isinstance(row, dict)
        ] if isinstance(contract.get("first_principles"), list) else [],
        "modules": [
            {
                "node_id": str(row.get("node_id") or "")[:120],
                "name": str(row.get("name") or "")[:240],
                "objective": str(row.get("objective") or "")[:500],
                "dependencies": [str(item)[:200] for item in row.get("dependencies", [])[:8]],
                "outputs": [str(item)[:300] for item in row.get("outputs", [])[:8]],
                "exit_criteria": [str(item)[:300] for item in row.get("exit_criteria", [])[:8]],
                "contribution_to_goal": str(row.get("contribution_to_goal") or "")[:500],
            }
            for row in modules
        ],
        "final_acceptance": [
            {
                "criterion": str(row.get("criterion") or "")[:500],
                "evidence": str(row.get("evidence") or "")[:500],
                "validation_method": str(row.get("validation_method") or "")[:500],
            }
            if isinstance(row, dict) else {"criterion": str(row)[:500]}
            for row in (
                contract.get("final_acceptance", [])[:8]
                if isinstance(contract.get("final_acceptance"), list) else []
            )
        ],
        "constraints": [str(item)[:300] for item in contract.get("constraints", [])[:8]]
        if isinstance(contract.get("constraints"), list) else [],
        "non_goals": [str(item)[:300] for item in contract.get("non_goals", [])[:8]]
        if isinstance(contract.get("non_goals"), list) else [],
    }
    for key in ("north_star_goal", "current_stage", "current_action", "expected_evidence", "policy_boundary", "alignment_layer", "consequence", "appeal", "trigger"):
        if key in packet and packet[key] is not None:
            packet[key] = str(packet[key])[:1600]
    return packet


def packet_fingerprint(packet: dict[str, Any]) -> str:
    payload = json.dumps(_safe_packet(packet), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((POLICY_VERSION + "\n" + payload).encode("utf-8")).hexdigest()


def _validate_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("verdict") not in VERDICTS:
        return None
    if value.get("confidence") not in {"low", "medium", "high"}:
        return None
    if not isinstance(value.get("rationale"), str) or not value["rationale"].strip():
        return None
    if not isinstance(value.get("recommended_action"), str):
        return None
    evidence = value.get("evidence_needed")
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        return None
    return {
        "verdict": value["verdict"],
        "confidence": value["confidence"],
        "rationale": value["rationale"][:1200],
        "recommended_action": value["recommended_action"][:400],
        "evidence_needed": value["evidence_needed"][:8],
    }


def _cache_rows(path: Path) -> list[dict[str, Any]]:
    value = load_json(path, {"entries": []})
    return [row for row in value.get("entries", []) if isinstance(row, dict)] if isinstance(value, dict) else []


def cached_result(cache_path: Path, fingerprint: str) -> dict[str, Any] | None:
    for row in reversed(_cache_rows(cache_path)):
        if row.get("fingerprint") == fingerprint and isinstance(row.get("result"), dict):
            return {**row["result"], "status": "CACHED", "fingerprint": fingerprint}
    return None


def _save_cache(cache_path: Path, fingerprint: str, result: dict[str, Any]) -> None:
    rows = [row for row in _cache_rows(cache_path) if row.get("fingerprint") != fingerprint]
    rows.append({"fingerprint": fingerprint, "judged_at": utc_now_iso(), "result": result})
    write_json(cache_path, {"policy_version": POLICY_VERSION, "entries": rows[-MAX_CACHE_ENTRIES:]})


def _prompt(packet: dict[str, Any]) -> str:
    if packet.get("trigger") == "possible_north_star_change":
        return (
            "You are a sparse, read-only Goal-direction judge. Decide whether the latest user request "
            "clearly establishes a durable product direction that is outside, rather than contained by, "
            "the confirmed North Star and detailed Goal contract. CONFIRM_GOAL_CHANGE at high confidence "
            "only when the request materially changes the enduring product outcome, audience, business "
            "direction, or core deliverable. Do not confirm for a temporary request, question, implementation "
            "detail, validation requirement, sequencing change, current module, allowed subgoal, or ambiguous "
            "exploration. Uncertainty must return INSUFFICIENT_EVIDENCE or WARN_AND_RECHECK. Return only JSON "
            "matching the supplied schema.\n\n"
            + json.dumps(_safe_packet(packet), ensure_ascii=False, indent=2)
        )
    return (
        "You are a sparse, read-only execution-convergence judge. Evaluate only the structured "
        "project metadata below. Do not assume access to source code. Distinguish useful exploration "
        "from unproductive deviation. Use the North Star for durable direction and the Goal contract "
        "for concrete modules, dependencies, outputs, exit criteria, and final acceptance. Lack of an "
        "obvious module match is not a violation: it may be a prerequisite or bounded exploration. A "
        "targeted rail is justified only for a high-confidence conflict with an explicit North Star "
        "anti-goal, Goal-contract non-goal, source requirement, or first-principle implication, or a repeated "
        "high-cost technical route without new evidence. When the trigger is route reassessment, test whether "
        "the current route can satisfy the required user scenario at all, not merely whether its local step can run. "
        "Prefer a scoped warning or request for evidence when uncertain. Return only JSON matching the "
        "supplied schema.\n\n"
        + json.dumps(_safe_packet(packet), ensure_ascii=False, indent=2)
    )


def invoke(
    packet: dict[str, Any],
    *,
    schema_path: Path,
    cache_path: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    force: bool = False,
) -> dict[str, Any]:
    safe = _safe_packet(packet)
    fingerprint = packet_fingerprint(safe)
    if not force:
        cached = cached_result(cache_path, fingerprint)
        if cached:
            return cached
    command = _codex_command()
    if not command:
        return {
            "status": "UNAVAILABLE",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "confidence": "low",
            "rationale": "Codex CLI is unavailable; semantic judgment failed open.",
            "recommended_action": "continue_with_scripted_advisory_only",
            "evidence_needed": [],
            "fingerprint": fingerprint,
        }
    ensure_schema(schema_path)
    with tempfile.TemporaryDirectory(prefix="goal-supervisor-judge-") as temporary:
        neutral = Path(temporary)
        output_path = neutral / "result.json"
        argv = [
            *command,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path.resolve()),
            "-o",
            str(output_path),
            "-",
        ]
        environment = {
            **os.environ,
            "GOAL_SUPERVISOR_LLM_JUDGE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        }
        kwargs: dict[str, Any] = {
            "cwd": str(neutral),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": environment,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(argv, **kwargs)
        except OSError as exc:
            return {
                "status": "UNAVAILABLE",
                "verdict": "INSUFFICIENT_EVIDENCE",
                "confidence": "low",
                "rationale": "Codex CLI could not start; semantic judgment failed open.",
                "recommended_action": "continue_with_scripted_advisory_only",
                "evidence_needed": [],
                "fingerprint": fingerprint,
                "diagnostic": str(exc)[:400],
            }
        try:
            stdout, stderr = process.communicate(input=_prompt(safe), timeout=max(1.0, timeout_seconds))
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return {
                "status": "TIMEOUT",
                "verdict": "INSUFFICIENT_EVIDENCE",
                "confidence": "low",
                "rationale": "Codex CLI judgment timed out; semantic judgment failed open.",
                "recommended_action": "continue_with_scripted_advisory_only",
                "evidence_needed": [],
                "fingerprint": fingerprint,
            }
        if process.returncode != 0 or not output_path.is_file():
            return {
                "status": "UNAVAILABLE",
                "verdict": "INSUFFICIENT_EVIDENCE",
                "confidence": "low",
                "rationale": "Codex CLI judgment did not return valid structured output; semantic judgment failed open.",
                "recommended_action": "continue_with_scripted_advisory_only",
                "evidence_needed": [],
                "fingerprint": fingerprint,
                "diagnostic": (stderr or stdout)[-400:],
            }
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = None
        result = _validate_result(value)
        if not result:
            return {
                "status": "MALFORMED",
                "verdict": "INSUFFICIENT_EVIDENCE",
                "confidence": "low",
                "rationale": "Codex CLI output failed schema validation; semantic judgment failed open.",
                "recommended_action": "continue_with_scripted_advisory_only",
                "evidence_needed": [],
                "fingerprint": fingerprint,
            }
        _save_cache(cache_path, fingerprint, result)
        return {**result, "status": "COMPLETED", "fingerprint": fingerprint}
