#!/usr/bin/env python3
"""
Codex Goal Compass.

Codex Goal Supervisor is an advisory-first project tool. It observes normal Codex
work in the background and calls specialist capabilities only for concrete
events. Tickets remain optional explicit contracts, not a default workflow.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import difflib
import fnmatch
import functools
import glob
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from goal_compass_runtime.state_store import (
    append_jsonl,
    exclusive_file_lock,
    load_json,
    utc_now_iso,
    write_json as atomic_write_json,
    write_json_exclusive,
)
from goal_compass_runtime.hook_rules import destructive_git_command, shell_write_targets, tool_failed
from goal_compass_runtime.deviation_incidents import (
    GOAL_CONTRACT_ALIGNMENT,
    alignment_policy_sources,
    build_context as build_deviation_context,
    mark_corrected as mark_deviation_corrected,
    north_star_policies,
    open_correction as open_deviation_correction,
)
from goal_compass_runtime.supervision import decide as decide_supervision
from goal_compass_runtime.observer import (
    apply_observation as apply_observer_observation,
    apply_pending_events as apply_pending_observer_events,
    compact_summary as compact_observer_summary,
    empty_state as empty_observer_state,
    finalize_pending_events as finalize_pending_observer_events,
    observation_event as make_observer_event,
    pending_event_summary,
    persist_recent_events as persist_observer_events,
    queue_pending_event as queue_pending_observer_event,
)
from goal_compass_runtime.feedback import (
    ensure_config as ensure_feedback_config,
    record as record_feedback,
    status as feedback_status,
)
from goal_compass_runtime.reuse_probe import (
    VALID_DECISIONS as REUSE_DECISIONS,
    VALID_UPDATE_DECISIONS as REUSE_UPDATE_DECISIONS,
    apply_decision as apply_reuse_decision,
    attach_project_contract as attach_reuse_project_contract,
    compact_status as reuse_compact_status,
    contract_errors as reuse_contract_errors,
    ensure_config as ensure_reuse_probe_config,
    is_due as reuse_probe_due,
    mark_integration_verified as mark_reuse_integration_verified,
    probe as run_reuse_probe,
)
from goal_compass_runtime.validation_catalog import (
    command_parts as catalog_command_parts,
    invalidate as invalidate_catalog,
    load_catalog,
)
from goal_compass_runtime.convergence import (
    apply_observation as apply_convergence_observation,
    compact_status as compact_convergence_status,
    empty_state as empty_convergence_state,
    judge_trigger as convergence_judge_trigger,
    record_collaboration_round as record_convergence_collaboration_round,
    record_evidence as record_convergence_evidence,
    record_iteration as record_convergence_iteration,
    refresh as refresh_convergence_state,
)
from goal_compass_runtime.llm_judge import (
    SCHEMA as LLM_JUDGE_SCHEMA,
    ensure_schema as ensure_llm_judge_schema,
    invoke as invoke_llm_judge,
)
from goal_compass_runtime.context_continuity import (
    compact_status as context_continuity_status,
    record_semantic_checkpoint,
)
from goal_compass_runtime.goal_return import compact_status as goal_return_status


AGENT = Path(".agent")
CODEX = Path(".codex")
CURRENT_TICKET = AGENT / "current_ticket.json"
LAST_TICKET = AGENT / "last_ticket.json"
NORTH_STAR = AGENT / "north_star_goal.json"
PROGRAM_PHASE = AGENT / "program_phase.json"
GOAL_REPORT_JSON = AGENT / "goal_alignment_report.json"
GOAL_REPORT_MD = AGENT / "goal_alignment_report.md"
BACKLOG = AGENT / "backlog.jsonl"
VALIDATION_CATALOG = AGENT / "validation_catalog.json"
PRUNE_PLAN = AGENT / "prune_plan.json"
REQUEST_DECISIONS = AGENT / "request_decisions.jsonl"
QUARANTINE_MANIFEST = AGENT / "quarantine_manifest.jsonl"
JANITOR_CAPABILITY_LEVEL = "MARK_ONLY"
LAST_SCAN_SUMMARY: dict[str, Any] = {}
LENSES = AGENT / "lenses"
PENDING = AGENT / "tickets" / "pending"
DONE = AGENT / "tickets" / "done"
FAILED = AGENT / "tickets" / "failed"
AGENT_DOCS = AGENT / "docs"
SELFTEST = AGENT / "selftest"
PROTOCOLS = AGENT / "protocols"
COORDINATION_CONTRACTS = AGENT / "contracts"
RUNTIME = AGENT / "runtime"
BASELINES = RUNTIME / "baselines"
HOOK_STATE = RUNTIME / "hook_state.json"
HOOK_STATE_LOCK = RUNTIME / "hook_state.lock"
STATE_LOCK = RUNTIME / "current_ticket.lock"
HOOK_EVENTS = RUNTIME / "hook-events"
TOOL_MODE = AGENT / "tool_mode.json"
OBSERVER_STATE = RUNTIME / "observer_state.json"
OBSERVER_STATE_LOCK = RUNTIME / "observer_state.lock"
OBSERVER_EVENTS = RUNTIME / "observer_events.jsonl"
OBSERVER_PENDING = RUNTIME / "observer_pending"
CONVERGENCE_STATE = RUNTIME / "convergence_state.json"
CONVERGENCE_STATE_LOCK = RUNTIME / "convergence_state.lock"
LLM_JUDGE_CACHE = RUNTIME / "llm_judge_cache.json"
CONTEXT_CONTINUITY_STATE = RUNTIME / "context_continuity.json"
CONTEXT_CAPSULE = RUNTIME / "context" / "index.json"
GOAL_RETURN_STATE = RUNTIME / "goal_return" / "state.json"
LLM_JUDGE_SCHEMA_PATH = PROTOCOLS / "llm_judge.schema.json"
HOOKS = CODEX / "hooks.json"
PARALLEL_REGISTRY_DIR = "goal-compass"
PARALLEL_REGISTRY_FILE = "active-tickets.json"

MISMATCH_MESSAGE = "目标与项目内容不一致，请确认这个项目的原始目标。"
NORTH_STAR_CONFIRMATION_MESSAGE = "North Star is not confirmed. Confirm the project goal before goal-bound decisions."
EDGE_CASE_MESSAGE = "A valid edge case must not redefine the core product."
MISSING_ACCEPTANCE_MESSAGE = "missing machine-checkable acceptance"
NO_PASS_ACCEPTANCE_MESSAGE = "No machine-checkable acceptance. Refusing PASS."
ACTIVE_BUDGET_IDLE_GAP_MINUTES = 5.0
MDCP_PROTOCOL_VERSION = "1.0"
NORTH_STAR_CONTRACT_VERSION = "1.2"
GOAL_DEFINITION_SCHEMA_VERSION = "2.1"
GOAL_MODE_OBJECTIVE_MIN_CHARS = 2000
GOAL_MODE_OBJECTIVE_MAX_CHARS = 3500
COMPLEX_PLAN_MIN_CHARS = 4001
COMPANY_SUBAGENT_POLICY_VERSION = "2.0"
REQUEST_ROUTER_VERSION = "2.1"
COMPANY_AUTO_DEPARTMENT_LIMIT = 4
COMPANY_ALLOWED_MODEL_EFFORTS = {
    "gpt-5.6-sol": {"max"},
    "gpt-5.6-terra": {"high", "max"},
    "gpt-5.6-luna": {"high", "max"},
}
BATCH_EXECUTION_KINDS = {
    "independent_annotation",
    "independent_labeling",
    "dataset_annotation",
}
STATE_LOCK_TIMEOUT_SECONDS = 8.0
STATE_LOCK_STALE_SECONDS = 1800.0
TERMINAL_TICKET_STATUSES = {
    "PASS",
    "FAIL",
    "DRIFT",
    "UPSTREAM_EVIDENCE_INVALID",
    "SUPERSEDED_BY_RECOVERY",
    "ENVIRONMENT_DIRTY",
    "ARTIFACT_SPRAWL",
}
ABORT_CLASSIFICATIONS = {
    "DRIFT",
    "UPSTREAM_EVIDENCE_INVALID",
    "SUPERSEDED_BY_RECOVERY",
    "ENVIRONMENT_DIRTY",
    "ARTIFACT_SPRAWL",
    "FAIL",
}
COMPANY_DEPARTMENT_CONTRACT_FIELDS = [
    "responsibility",
    "decision_authority",
    "required_inputs",
    "deliverables",
    "acceptance_criteria",
    "consumers",
    "forbidden_scope",
    "dependencies",
    "stop_condition",
    "model_range",
    "effort_range",
]

AXIS_COMMON_TERMS = {
    "add",
    "after",
    "before",
    "control",
    "packaging",
    "manufacturing",
    "bounded",
    "evidence",
    "file",
    "format",
    "json",
    "only",
    "produce",
    "quality",
    "result",
    "release",
    "rule",
    "test",
    "testing",
    "tolerance",
    "verification",
    "verify",
    "with",
    "workflow",
    "record",
    "implement",
    "lot",
    "traceability",
    "xml",
    "yaml",
}

GENERATED = [
    ".agent/**",
    ".codex/**",
]

PROTECTED_CONTROL_PATTERNS = [
    ".agent/current_ticket.json",
    ".agent/north_star_goal.json",
    ".agent/program_phase.json",
    ".agent/validation_catalog.json",
    ".agent/prune_plan.json",
    ".agent/goal_alignment_report.json",
    ".agent/quarantine_manifest.jsonl",
    ".agent/feedback_config.json",
    ".agent/reuse_probe_config.json",
    ".agent/tool_mode.json",
    ".agent/contracts/**",
]

PLANNING_EDIT_PATTERNS = [
    ".agent/tickets/pending/**",
    ".agent/validation_catalog.json",
    ".agent/contracts/**",
]

PARALLEL_BASE_CONTRACT_SECTIONS = [
    "independence_evidence",
    "quality_validation",
    "integration_plan",
]
PARALLEL_TECHNICAL_CONTRACT_SECTIONS = {
    "language_runtime",
    "architecture_boundaries",
    "interfaces",
    "data_contracts",
    "naming_conventions",
    "error_contract",
    "serialization_units_time",
    "versioning_compatibility",
    "dependency_toolchain",
    "observability",
    "resource_ownership",
}
PARALLEL_MIN_NET_GAIN_MINUTES = 5.0
PARALLEL_MIN_GAIN_RATIO = 0.10

SCAN_IGNORE = [
    ".git/**",
    ".agents/**",
    ".codex/**",
    ".agent/goal_compass.py",
    ".agent/governor.py",
    ".agent/docs/**",
    ".agent/selftest/**",
    ".agent/protocols/**",
    ".agent/legacy/**",
    ".agent/goal_alignment_report.*",
    ".agent/prune_plan.json",
    "node_modules/**",
    ".venv/**",
    "__pycache__/**",
    "research/**",
    "artifacts/**",
    "tmp/**",
]

SCAN_SKIP_DIR_NAMES = {
    ".git",
    ".agents",
    ".codex",
    ".agent",
    "node_modules",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tmp",
    "artifacts",
    "dist",
    "build",
}

# These directories are runtime/vendor state in most projects. They are not
# product edits, so non-git budget tracking skips them unless the ticket
# explicitly names a path inside them as allowed, forbidden, or acceptance.
TRACKING_IGNORE_PATTERNS = [
    ".git/**",
    ".agent/**",
    ".codex/**",
    ".venv*/**",
    "venv/**",
    "node_modules/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    ".mypy_cache/**",
    "**/.mypy_cache/**",
    ".ruff_cache/**",
    "**/.ruff_cache/**",
    "dist/**",
    "build/**",
    "coverage/**",
    "tmp/**",
    "artifacts/**",
    "storage/**",
    "logs/**",
    "data/**",
    "outputs/handoff/**",
    "external_research/source_cache/**",
    "research/repos/**",
]

# Large runtime and vendor roots must stay sparse even when a ticket explicitly
# protects one file below them. Expanding one `data/processed/foo.json` contract
# to the entire `data/**` tree makes every active-ticket hook hash gigabytes of
# unrelated runtime state. Generated artifact roots such as `artifacts/**` keep
# their broader sibling tracking so unapproved neighboring deliverables still
# surface as drift.
SPARSE_EXPLICIT_TRACKING_ROOTS = {
    ".venv*",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "storage",
    "logs",
    "data",
    "outputs/handoff",
    "external_research/source_cache",
    "research/repos",
}

RUNTIME_CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

DEFAULT_VOLATILE_PATTERNS = [
    "**/*.sqlite-wal",
    "**/*.sqlite-shm",
    "**/*.sqlite3-wal",
    "**/*.sqlite3-shm",
    "**/*.db-wal",
    "**/*.db-shm",
    "**/*.pid",
    "**/*.sock",
]

HARD_TRACKING_IGNORE_ROOTS = {".git", ".agent", ".codex"}

SCAN_ROOTS = [
    "GOAL.md",
    "README.md",
    "README.zh.md",
    "AGENTS.md",
    "CLAUDE.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "tsconfig.json",
    "vite.config.ts",
    "docs",
    "product",
    "app",
    "apps",
    "packages",
    "services",
    "lib",
    "src",
    "tests",
    "scripts",
    "config",
    "work",
    ".agent/tickets",
    ".agent/backlog.jsonl",
]

GOAL_DETECT_ROOTS = [
    "GOAL.md",
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "tsconfig.json",
    "vite.config.ts",
    "product",
    "app",
    "apps",
    "packages",
    "services",
    "lib",
    "src",
    "tests",
    "docs",
    "scripts",
    "config",
    "work",
]

HEAVY_SCOPE_TERMS = [
    "RBAC",
    "marketplace",
    "platform",
    "framework",
    "enterprise",
    "full",
    "complete",
    "generic",
    "multi-tenant",
    "policy DSL",
    "security gateway",
    "provider marketplace",
    "compliance",
    "audit dashboard",
    "red-team",
    "plugin marketplace",
    "ERP",
    "MES",
    "WMS",
    "enterprise resource planning",
    "manufacturing execution system",
    "supplier marketplace",
]

GOAL_EVIDENCE_BLOCK_TERMS = [
    "RBAC",
    "provider marketplace",
    "public marketplace",
    "marketplace",
    "security gateway",
    "compliance",
    "audit dashboard",
    "enterprise permission platform",
    "full platform",
    "generic plugin platform",
]

TEXT_FILE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".go",
    ".gradle",
    ".graphql",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".lock",
    ".log",
    ".md",
    ".mdx",
    ".php",
    ".properties",
    ".proto",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

BINARY_FILE_SUFFIXES = {
    ".db",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".wasm",
    ".webp",
    ".zip",
}

DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
}

NORTH_STAR_SOURCE_FILES = {"goal.md"}
PROJECT_ANCHOR_FILES = {"readme.md", "readme.zh.md", "agents.md", "claude.md"}
GENERIC_REFERENCE_NAMES = {"index", "main", "app", "test", "tests", "readme", "config", "base", "utils"}

RUNTIME_EVIDENCE_PATTERNS = [
    "work/**",
    "storage/**",
]

WEAK_PROTECT_WORDS = {
    "video",
    "generation",
    "ai",
    "artifact",
    "model",
    "adapter",
    "security",
    "permission",
    "system",
    "automatic",
}

UNCONFIRMED_NORTH_STAR = {
    "confirmed": False,
    "goal": None,
    "source": None,
    "confirmed_at": None,
    "main_path": [],
    "allowed_subgoals": [],
    "anti_goals": [],
    "backlog_domains": [],
    "protected_principles": [],
    "core_path_patterns": [],
    "candidate_goals": [],
    "requires_confirmation": True,
    "goal_definition": {
        "schema_version": "2.0",
        "quality": "MISSING",
        "precise_goal": None,
        "problem_statement": None,
        "current_state": None,
        "desired_state": None,
        "stakeholders": [],
        "source_requirements": [],
        "first_principles": [],
        "concrete_actions": [],
        "process": {
            "entry_conditions": [],
            "nodes": [],
            "completion_conditions": [],
        },
        "deliverables": [],
        "success_criteria": [],
        "final_acceptance": [],
        "constraints": [],
        "non_goals": [],
        "assumptions": [],
        "open_questions": [],
        "dialogue_summary": [],
        "missing_fields": [
            "precise_goal",
            "problem_statement",
            "current_state",
            "desired_state",
            "first_principles>=2",
            "process.nodes>=2",
            "deliverables",
            "final_acceptance",
        ],
        "tooling_is_not_product_goal": True,
    },
}

LEGACY_BUILTIN_MAIN_PATH_MARKERS = {
    "prompt input",
    "product input",
    "agent package upload",
    "market data ingestion",
}

DEFAULT_PROTECTED_PRINCIPLES = [
    "Every action taken by the model and every supervisory intervention by this plugin must produce net execution benefit.",
    "Any action that may affect other modules without constraint or become noise for the entire project must be managed.",
    "If the cost of a control by this plugin exceeds the rework it can prevent, that control must remain inactive.",
    "Prefer end-to-end product progress over local subsystem perfection.",
    "Do not let local subsystems consume the main product goal.",
    "Do not let edge cases redefine the core product.",
]

ALLOWED_LENS_KEYS = """Allowed output keys only:
- must_do_candidate
- must_not_do_candidate
- acceptance_candidate
- drift_signal_candidate
- backlog_candidate
- smaller_path

Do not write approval, reject, sign, pause, final decision, role signoff, or request another review.
"""

DEFAULT_LENSES = {
    "product.md": """# Product Lens

Only answer:
- How does this ticket move the main product goal?
- What does not serve the current main path?
- Does why_now hold?

""" + ALLOWED_LENS_KEYS,
    "engineering.md": """# Engineering Lens

Only answer:
- What is the smallest implementation path?
- What engineering work can be skipped?
- Which changes are too large?

""" + ALLOWED_LENS_KEYS,
    "architecture.md": """# Architecture Lens

Only answer:
- Is there over-abstraction?
- Is this building future architecture too early?
- Is there a smaller structure?

""" + ALLOWED_LENS_KEYS,
    "qa.md": """# QA Lens

Only answer:
- Which acceptance checks are machine-verifiable?
- Which files, commands, or behavior prove completion?
- What acceptance gaps remain?

""" + ALLOWED_LENS_KEYS,
    "scope.md": """# Scope Lens

Only answer:
- Where can this ticket become a scope-sink?
- What must go to backlog_only?
- Which drift_signals belong in the ticket?

""" + ALLOWED_LENS_KEYS,
    "cost.md": """# Cost Lens

Only answer:
- What budget is reasonable?
- Which behaviors mean over-production?
- When should work stop?

""" + ALLOWED_LENS_KEYS,
}

AGENT_README = """# Codex Goal Supervisor In This Project

Codex Goal Supervisor has two separate layers.

## Implicit Background Layer

After this project explicitly installs Codex Goal Supervisor, its repo-local hook continuously
observes bounded metadata at low cost. Ordinary reads, edits, tests, and delivery
remain silent and do not require an ACTIVE ticket, role receipt, status query,
or cleanup pass.

The observer may emit one compact warning for concrete repeated failures or
broad artifact growth. Exact project-authored anti-goal/drift incidents remain
open across unrelated success: confirmations one and two warn, while a third
confirmation blocks only the affected wrong-direction write surface. Continued
affected-path work is rechecked after 30 minutes. A scoped `deviation-correct`
repair followed by `deviation-corrected` enters seven days of active recurrence
monitoring before the count clears. Reads, tests, validation, and unrelated
aligned writes remain available. Deterministic destructive boundaries also
remain blocked. Ambiguous semantic judgments are warnings. Hook failure is
fail-open.

Large historical-code or document reads are handled by local code only. The
runtime stores a metadata-only directory index under `.agent/runtime/context/`
and never injects it into the main thread or invokes an LLM to do the task.
`status --verbose` exposes the index for on-demand loading. The execution Agent
records explicit conclusions per directory with `context-note`, publishes a
concise result after each meaningful read slice, and may choose read-only
subagents for independent directory slices. It merges their structured
summaries itself; the hook never opens agents or stores hidden reasoning.

## Explicit Optional Layer

All capabilities remain available when the AI judges that they will save more
rework than they cost:

- `goal-set` for an explicitly requested durable North Star;
- `request` for optional Custodian analysis of a meaningful goal/scope change;
- task-shaped company roles, including zero roles;
- `check` or `close` for machine-evidence audit;
- `prune-check` or `prune-plan` for MARK_ONLY artifact review;
- `compile`, `ready`, `start`, and `close` for an optional bounded contract.

These commands are not a default workflow. Empty machine acceptance can never
start or PASS, but normal work without a ticket is valid. Missing optional role
outputs do not block implementation or certification. Janitor never moves or
deletes product files.

## Privacy

Diagnostic feedback is local-only by default. Upload requires explicit project-
level consent; an endpoint alone cannot enable it. Network failure never blocks
product work.

## Supreme Rule

Every action taken by the model and every supervisory intervention by this
plugin must produce net execution benefit. Any action that may affect other
modules without constraint or become noise for the entire project must be
managed. If the cost of a control by this plugin exceeds the rework it can
prevent, that control must remain inactive.
"""

MDCP_PROTOCOL_MD = """# Multi-Dimensional Collaboration Protocol For Goal Compass

MDCP is used here as a cross-layer rule library, not as a new workflow.

Supreme rule: every action taken by the model and every supervisory intervention
by this plugin must produce net execution benefit. Any action that may affect
other modules without constraint or become noise for the entire project must be
managed. If the cost of a control by this plugin exceeds the rework it can
prevent, that control must remain inactive.

Source reference:
https://github.com/HanShengrunning/-multi-dimensional-collaboration-protocol

Goal Compass maps MDCP into three existing layers:

1. Structured expression + pass criteria:
   - precise_goal
   - reasoned first_principles
   - process_nodes with concrete outputs and exit criteria
   - final_acceptance with evidence and validation method
   - precision_level
   - scope_anchor
   - conversation_plane
   - acceptance_consumer
   - time_cost_signal
   - value_signal
   - metacognition_lock_signal
   - loop_risk
   - consumer_mismatch_risk
   - scope_sink_risk

2. Lens / company-role task generation:
   - strategy
   - business
   - product
   - engineering
   - architecture
   - qa
   - scope_cost
   - custodian
   - janitor
   - auditor
   - adaptive company subagent plan

3. Janitor / auditor checks:
   - artifact classifications
   - delete/backlog/simplify/protected candidates
   - auditor status
   - acceptance consumer mismatch
   - scope anchor violation
   - same-axis fatigue
   - precision mismatch

MDCP must not create gate chains, ledgers, role gates, reverse-signal loops, or
security governor behavior.

Company capability is adaptive and may use zero departments when delegation
would cost more than direct bounded execution:

- The main thread CEO coordinates, integrates, and owns final machine acceptance.
- The ticket selects zero to four departments automatically from task needs. Four
  is a confirmation threshold, not a default roster and not a fixed set of roles.
- Explicit read-only/status work and narrow low-risk bounded actions may remain
  with the main thread as `NO_SUBAGENT_NEEDED`.
- Task depth selects a model/effort point inside each department's declared
  minimum/recommended/maximum range. Task breadth selects department count.
- Strategy, business, product, finance, architecture, algorithm, engineering,
  QA, manufacturing, quality, and other departments keep distinct contracts.
- Every selected department must declare responsibility, decision authority,
  inputs, deliverables, acceptance criteria, consumers, forbidden scope,
  dependencies, model/effort ranges, and a stop condition.
- More than four child agents require a conservative main-thread CEO decision
  bound to the exact roster and contracts. The default decision is KEEP_CURRENT;
  EXPAND requires concrete evidence that execution gain exceeds coordination cost.
- Confirmed plans have no protocol-level department cap. Large real companies may
  map every necessary independent department, but placeholder roles are invalid.
- Large rosters run in bounded waves rather than assuming unlimited concurrency.
- Ultra is never auto-assigned to a department agent. It is only an optional
  root-CEO capability for critical work because it may coordinate agents itself.
- Child agents never create nested company modes, role gates, or chat loops.
- Each department returns one concise structured deliverable and exits.
- The ticket records the required plan, but runtime execution remains an
  external skill contract and is never falsely reported as verified.

Operational translation:

- R3 scope anchoring -> every ticket gets scope_anchor and scope checks.
- R4 precision response -> every request/ticket gets precision_level.
- R9 loop interruption -> status/check show same-axis fatigue warnings.
- R11 consumer confirmation -> acceptance_consumer is refreshed before execution.
- Parallel lanes -> require no dependency edge, disjoint writable ownership, one
  short risk-adaptive compatibility contract, and positive net time gain after
  coordination plus integration validation.
"""

MDCP_SCHEMA_DOC = {
    "name": "goal_compass_mdcp_contract",
    "description": "Goal Compass use of MDCP as a cross-layer rule library.",
    "version": MDCP_PROTOCOL_VERSION,
    "company_department_contract_fields": COMPANY_DEPARTMENT_CONTRACT_FIELDS,
    "layers": {
        "layer_1_structured_expression": [
            "north_star_goal",
            "goal_definition_quality",
            "precise_goal",
            "problem_statement",
            "first_principles",
            "process_nodes",
            "goal_deliverables",
            "goal_final_acceptance",
            "goal_anchor",
            "scope_anchor",
            "conversation_plane",
            "precision_level",
            "time_cost_signal",
            "value_signal",
            "metacognition_lock_signal",
            "loop_risk",
            "consumer_mismatch_risk",
            "acceptance_consumer",
            "scope_sink_risk",
        ],
        "layer_1_pass_criteria": [
            "north_star_confirmed",
            "goal_definition_structured",
            "goal_definition_detailed",
            "request_classified",
            "ticket_structured",
            "machine_acceptance_present",
            "acceptance_consumer_known",
            "allowed_paths_present",
            "forbidden_paths_present",
            "budget_present",
            "drift_signals_present",
            "raw_shell_acceptance_forbidden",
            "scope_anchor_present",
        ],
        "layer_2_company_roles": [
            "strategy",
            "business",
            "product",
            "engineering",
            "architecture",
            "qa",
            "scope_cost",
            "custodian",
            "janitor",
            "auditor",
        ],
        "layer_2_company_subagents": [
            "mandatory",
            "complexity_tier",
            "task_depth",
            "task_breadth",
            "required_subagents",
            "main_thread",
            "runtime_binding",
            "runtime_execution_verified",
            "ceo_confirmation",
            "department_capacity",
            "department_contract_required",
            "expansion_policy",
            "model_routing",
            "dispatch",
        ],
        "layer_3_janitor_auditor": [
            "janitor",
            "auditor",
        ],
    },
    "forbidden": ["gate chains", "role gates", "reverse-signal loops", "security governor behavior", "nested company modes", "multi-agent chat loops"],
}


DEFAULT_CATALOG = {
    "project_pytest": {
        "cmd": "{python} -m pytest",
        "description": "Run the project pytest suite.",
        "timeout_sec": 120,
    },
    "project_smoke_mvp": {
        "cmd": "{python} scripts/smoke_mvp.py",
        "description": "Run the project MVP smoke script when present.",
        "timeout_sec": 120,
    },
}

def now() -> str:
    return utc_now_iso()


def write_json(path: Path, data: Any) -> None:
    atomic_write_json(path, data)
    try:
        is_catalog = path.resolve() == VALIDATION_CATALOG.resolve()
    except OSError:
        is_catalog = False
    if is_catalog:
        invalidate_catalog(path)


def report_governance_feedback(
    kind: str,
    message: str,
    *,
    source: str,
    severity: str = "warning",
    rule_id: str | None = None,
    command: str | None = None,
    ticket: dict[str, Any] | None = None,
    status: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture a governance problem without ever blocking the caller."""
    try:
        selected = ticket if isinstance(ticket, dict) else current_ticket()
        return record_feedback(
            kind=kind,
            message=message,
            source=source,
            severity=severity,
            rule_id=rule_id,
            command=command,
            ticket_id=str(selected.get("ticket_id") or "") or None,
            status=status,
            context=context or {},
            agent_dir=AGENT,
        )
    except Exception as exc:  # telemetry must never become project downtime
        return {"captured": False, "delivery": "INTERNAL_ERROR", "error": type(exc).__name__}


def remaining_reuse_actions(ticket: dict[str, Any] | None = None) -> list[str]:
    """Collect bounded work still ahead; only evaluated on the project-level probe."""
    actions: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in actions:
            actions.append(text)

    selected = ticket if isinstance(ticket, dict) else {}
    if selected.get("status") not in TERMINAL_TICKET_STATUSES:
        add(selected.get("task_goal"))
        for value in selected.get("must_do", []):
            add(value)
    phase = program_phase()
    if phase.get("status") == "ACTIVE":
        add(phase.get("goal"))
        for value in phase.get("exit_criteria", []):
            add(value)
    if PENDING.exists():
        for path in sorted(PENDING.glob("*.json"))[:30]:
            row = load_json(path, {})
            if not isinstance(row, dict) or row.get("status") in TERMINAL_TICKET_STATUSES:
                continue
            add(row.get("task_goal"))
            for value in row.get("must_do", []):
                add(value)
    if BACKLOG.is_file() and BACKLOG.stat().st_size:
        try:
            lines = BACKLOG.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                add(row.get("text") or row.get("item") or row.get("request"))
    return actions[:40]


def refresh_reuse_discovery(ticket: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    discovery = run_reuse_probe(
        ticket,
        north_star(),
        agent_dir=AGENT,
        force=force,
        remaining_actions=remaining_reuse_actions(ticket),
    )
    ticket["reuse_discovery"] = discovery
    return attach_reuse_project_contract(ticket, AGENT)


@contextlib.contextmanager
def current_state_lock(timeout: float = STATE_LOCK_TIMEOUT_SECONDS):
    with exclusive_file_lock(
        STATE_LOCK,
        timeout=timeout,
        stale_seconds=STATE_LOCK_STALE_SECONDS,
    ):
        yield


def serialized_current_state(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        try:
            with current_state_lock():
                return func(*args, **kwargs)
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "error": str(exc), "required_action": "retry_state_operation"}, ensure_ascii=False))
            return 3

    return wrapped


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file_contents(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def read_text(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def norm(path: str) -> str:
    path = path.replace("\\", "/")
    if path.startswith("file://"):
        path = path[7:]
    if os.path.isabs(path):
        try:
            path = str(Path(path).resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            return "__outside_repo__/" + path.lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    out = os.path.normpath(path).replace("\\", "/")
    return "" if out == "." else out


def filesystem_path(path: str) -> Path:
    """Resolve a normalized path token back to the filesystem for read checks."""
    value = str(path).replace("\\", "/")
    prefix = "__outside_repo__/"
    if not value.startswith(prefix):
        return Path(value)
    outside = value[len(prefix):]
    if re.match(r"^[A-Za-z]:/", outside):
        return Path(outside)
    return Path("/" + outside.lstrip("/"))


def filesystem_matches(path: str) -> list[Path]:
    candidate = filesystem_path(path)
    if any(mark in str(candidate) for mark in ("*", "?", "[")):
        return [Path(value) for value in glob.glob(str(candidate), recursive=True)]
    return [candidate] if candidate.exists() else []


def match_path(path: str, patterns: list[str]) -> bool:
    p = norm(path)
    for raw in patterns:
        pat = norm(str(raw))
        if not pat:
            continue
        if pat.endswith("/**") and (p == pat[:-3] or p.startswith(pat[:-2])):
            return True
        if fnmatch.fnmatch(p, pat):
            return True
    return False


def is_generated(path: str) -> bool:
    return match_path(path, GENERATED)


def should_scan(path: str, include_agent_aux: bool = True) -> bool:
    p = norm(path)
    if p.startswith(".agent/"):
        if not include_agent_aux:
            return False
        if p.startswith(".agent/tickets/examples/") or p.startswith(".agent/tickets/pending/"):
            return False
        if p.startswith(".agent/tickets/done/") or p.startswith(".agent/tickets/failed/"):
            return True
        if p == ".agent/backlog.jsonl":
            return bool(read_text(Path(p), 1000).strip())
        if p.startswith(".agent/tickets/"):
            return True
        return False
    if match_path(p, SCAN_IGNORE):
        return False
    low = p.lower()
    if any(marker in low for marker in ["hmac", "signature", "board_events", "reverse_signal", "governor.rules"]):
        return False
    return True


def lower_text(text: str) -> str:
    return text.lower().replace("_", " ").replace("-", " ")


def canonical_text(text: str) -> str:
    low = lower_text(text)
    replacements = {
        "rbac": " rbac permission authorization ",
        "供应商市场": " provider marketplace ",
        "插件市场": " plugin marketplace ",
        "公共市场": " public marketplace ",
        "代理市场": " agent marketplace ",
        "技能市场": " skill marketplace ",
        "权限系统": " rbac permission platform ",
        "企业级": " enterprise ",
        "权限": " permission ",
        "商城": " marketplace ",
        "供应商": " provider ",
        "商品模型": " product modeling ",
        "商品建模": " product modeling ",
        "建模": " modeling ",
        "刀版": " dieline ",
        "贴图": " texture ",
        "质检": " quality validation ",
        "自动修复": " repair loop ",
        "前端展示": " front-end display ",
        "界面": " ui ",
        "面板": " ui panel ",
        "安全网关": " security gateway ",
        "安全": " security ",
        "合规": " compliance ",
        "红队": " red-team ",
        "屎山": " noise shit mountain ",
        "噪音": " noise ",
        "删除": " remove delete ",
        "清理": " cleanup remove ",
        "缩小": " simplify smaller ",
        "验收": " acceptance assertion validation ",
        "测试": " test validation ",
        "只读核查": " read-only inspect ",
        "只读检查": " read-only inspect ",
        "不要修改": " without changes ",
        "不改文件": " without changes ",
        "核查": " inspect ",
        "核对": " inspect ",
        "审阅": " review ",
        "查看": " inspect ",
        "修复": " fix ",
        "修改": " modify ",
        "解析器": " parser ",
        "验证行为": " validation behavior ",
        "行为": " behavior ",
        "结果": " result ",
        "输出": " output ",
        "错误": " error ",
        "状态": " status ",
        "服务": " service ",
        "数据": " data ",
        "上游": " upstream ",
        "证据": " evidence ",
        "覆盖": " coverage ",
        "回归": " regression ",
        "视频": " video ",
        "产物": " artifact ",
    }
    for src, dst in replacements.items():
        low = low.replace(src, dst)
    return low


def text_words(text: str) -> set[str]:
    normalized = canonical_text(text)
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", normalized)}
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        words.update(segment[index:index + 2] for index in range(len(segment) - 1))
    stop = {
        "the", "and", "for", "from", "with", "this", "that", "build", "system",
        "starts", "start", "create", "add", "current", "ticket", "goal", "project",
        "video", "ai", "automatic", "support", "using", "into", "only", "before",
        "after", "does", "not", "now", "product", "products", "category",
        "categories", "all", "once",
        "当前", "项目", "系统", "新增", "添加", "构建", "实现", "这个",
        "那个", "一个", "进行", "支持", "完成", "需要", "可以", "以及",
    }
    return {w for w in words if w not in stop}


def term_hits(text: str, items: list[Any]) -> list[str]:
    low = canonical_text(text)
    req_words = text_words(text)
    hits: list[str] = []
    for item in items:
        s = str(item)
        item_low = canonical_text(s)
        item_words = text_words(s)
        phrase = item_low.strip()
        if phrase and phrase in low:
            hits.append(s)
            continue
        important = {w for w in item_words if w not in {"minimal", "mock", "adapter", "permission", "model"}}
        overlap = req_words & important
        if len(important) <= 1 and overlap:
            hits.append(s)
        elif len(important) > 1 and len(overlap) >= 2:
            hits.append(s)
    return hits


def is_text_artifact(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in BINARY_FILE_SUFFIXES:
        return False
    if suffix in TEXT_FILE_SUFFIXES:
        return True
    if path.name in DEPENDENCY_FILES:
        return True
    return suffix == ""


def heavy_hits(text: str) -> list[str]:
    return term_hits(text, HEAVY_SCOPE_TERMS)


def goal_evidence_block_hits(path: str, text: str) -> list[str]:
    combined = f"{path}\n{text}"
    hits = term_hits(combined, GOAL_EVIDENCE_BLOCK_TERMS)
    path_hits = term_hits(path, GOAL_EVIDENCE_BLOCK_TERMS)
    documented_goal, _ = project_goal_line(path, text)
    if documented_goal and goal_source_weight(path) >= 1.0:
        # A user-authored GOAL/README/product goal is authoritative even when
        # the product itself is a platform, marketplace, or compliance tool.
        return []
    quant_code_signal = bool(re.search(
        r"量化|股票|行情|交易|持仓|下单|风控|回测|quant|stock|trading|trade|broker|portfolio|market data|backtest",
        combined,
        re.I,
    ))
    if quant_code_signal and norm(path).startswith(("scripts/", "apps/", "config/", "tests/")):
        # Core trading code often names rejected designs in guards, prompts, or
        # comments. Those mentions are not evidence that the file is itself a
        # governance/platform detour; only an explicit path signal is strong.
        hits = path_hits
    declared = canonical_text(json.dumps({
        "north_star": north_star(),
        "ticket": flattened_ticket_text(current_ticket()),
    }, ensure_ascii=False))
    if "marketplace" in declared:
        hits = [hit for hit in hits if canonical_text(hit).strip() not in {"marketplace"} and canonical_text(hit).strip() not in declared]
    return hits


def filter_negated_scope_hits(hits: list[str], text: str) -> list[str]:
    local_text = canonical_text(text)
    negators = [
        "do not",
        "don't",
        "must not",
        "never",
        "without",
        "avoid",
        "no ",
        "不要",
        "不得",
        "禁止",
        "避免",
    ]
    filtered: list[str] = []
    for hit in hits:
        phrase = canonical_text(hit).strip()
        if not phrase:
            continue
        starts = [match.start() for match in re.finditer(re.escape(phrase), local_text)]
        if not starts:
            filtered.append(hit)
            continue
        has_asserted_occurrence = False
        for start in starts:
            prefix = local_text[max(0, start - 120):start]
            prefix = re.split(r"[.。;；\n]", prefix)[-1]
            if not any(marker in prefix for marker in negators):
                has_asserted_occurrence = True
                break
        if has_asserted_occurrence:
            filtered.append(hit)
    return filtered


def filter_contextual_scope_hits(hits: list[str], text: str, ticket: dict[str, Any]) -> list[str]:
    if not hits:
        return []
    hits = filter_negated_scope_hits(hits, text)
    combined = f"{text}\n{flattened_ticket_text(ticket)}\n{json.dumps(north_star(), ensure_ascii=False)}"
    current_scope = canonical_text(combined)
    local_text = canonical_text(text)
    filtered: list[str] = []
    for hit in hits:
        low = canonical_text(hit)
        if "marketplace" in current_scope and low == "marketplace":
            continue
        if low in {"full", "complete", "generic", "platform", "framework"} and not any(
            marker in local_text
            for marker in [
                "rbac",
                "security gateway",
                "provider marketplace",
                "plugin marketplace",
                "public marketplace",
                "supplier marketplace",
                "enterprise permission",
                "enterprise resource planning",
                "manufacturing execution system",
                "erp",
                "mes",
                "wms",
                "compliance platform",
            ]
        ):
            continue
        if low == "compliance":
            declared = canonical_text(json.dumps({
                "ticket_anti_patterns": ticket.get("anti_patterns", []),
                "ticket_backlog": ticket.get("backlog_only", []),
                "north_anti_goals": north_star().get("anti_goals", []),
                "north_backlog": north_star().get("backlog_domains", []),
            }, ensure_ascii=False))
            if "compliance" not in declared and not any(
                marker in local_text for marker in ["compliance framework", "compliance platform", "compliance dashboard"]
            ):
                continue
        filtered.append(hit)
    return filtered


def split_goal_evidence(
    sources: list[tuple[str, str]],
    pattern: str,
    support_label: str,
) -> tuple[list[str], list[str]]:
    supporting: list[str] = []
    noise: list[str] = []
    rx = re.compile(pattern, re.I)
    ordered = sorted(sources, key=lambda row: goal_source_weight(row[0]), reverse=True)
    for path, text in ordered:
        if not rx.search(text):
            continue
        blocks = goal_evidence_block_hits(path, text)
        if blocks:
            noise.append(f"{path} matches future/noise scope: {', '.join(blocks[:3])}")
        else:
            supporting.append(f"{path} {support_label}")
    return supporting[:5], noise[:5]


def goal_source_weight(path: str) -> float:
    p = norm(path).lower()
    name = Path(p).name
    if any(marker in p for marker in ["compatibility", "local-mcp-apps", "migration-notes", "handoff", "archive"]):
        return 0.15
    if name == "goal.md":
        return 1.1
    if name in {"readme.md", "readme.zh.md"} or p.startswith("product/"):
        return 1.0
    if p.startswith(("src/", "tests/", "scripts/", "apps/")):
        return 0.9
    if name in {"package.json", "pyproject.toml", "workspace.yaml", "requirements.txt"}:
        return 0.75
    if p.startswith("docs/"):
        return 0.55
    if p.startswith("config/"):
        return 0.5
    return 0.4


def project_goal_line(path: str, text: str) -> tuple[str | None, float]:
    """Extract a user-authored goal statement without inventing a domain model."""
    p = norm(path).lower()
    name = Path(p).name
    high_value_source = name in {"goal.md", "readme.md", "readme.zh.md"} or p.startswith("product/")
    best: tuple[float, str] | None = None
    for index, raw in enumerate(text.splitlines()[:160]):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("```", "![", "[![", "<img", "<!--")):
            continue
        heading = stripped.startswith("#")
        value = re.sub(r"^#{1,6}\s*", "", stripped).strip(" -*`>\t")
        if len(value) < 8 or len(value) > 500:
            continue
        low = canonical_text(value)
        if low.strip() in {"goal", "project goal", "north star", "north star goal", "目标", "项目目标", "北极星", "北极星目标"}:
            continue
        explicit_goal = bool(re.search(r"\b(build|create|deliver|develop|objective|goal|mvp)\b|目标|本项目|构建|建设|实现|打造", value, re.I))
        if not high_value_source and not explicit_goal:
            continue
        if re.search(r"\b(?:do not|don't|must not|guardrail|non-goal)\b|不要|禁止|非目标", low, re.I):
            continue
        if any(term in low for term in ["installation", "install", "usage", "license", "contributing", "table of contents", "changelog"]):
            continue
        if any(term in low for term in ["goal compass", "goal custodian", "goal janitor", "anti shit mountain"]):
            continue
        score = goal_source_weight(path) * 3.0
        if name == "goal.md":
            score += 1.3
        if explicit_goal:
            score += 1.4
        if heading:
            score += 1.0
        if index < 8:
            score += 0.8
        if re.search(r"\b(build|create|deliver|develop|mvp|system|platform|service|tool|application|app|workflow|generator|registry|hub)\b", low):
            score += 2.0
        if re.search(r"目标|本项目|构建|建设|实现|打造|系统|平台|工具|应用|工作流|生成器|中枢", value):
            score += 2.0
        if p.startswith("tests/") or re.search(r"^(?:test|tests|example|fixture|migration|compatibility)\b", low):
            score -= 1.5
        if best is None or score > best[0]:
            best = (score, value)
    if best is None or best[0] < 4.8:
        return None, 0.0
    confidence = min(0.98, 0.62 + goal_source_weight(path) * 0.3)
    return best[1], confidence


def documented_goal_candidates(sources: list[tuple[str, str]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, text in sources:
        goal, confidence = project_goal_line(path, text)
        if not goal:
            continue
        key = canonical_text(goal).strip()
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "goal": goal,
            "confidence": confidence,
            "evidence": [f"{path} contains the user-authored project goal"],
            "supporting_evidence": [f"{path} contains the user-authored project goal"],
            "backlog_candidate_evidence": [],
            "noise_evidence": [],
            "contradicting_evidence": [],
            "source_type": "user_project_document",
        })
    return candidates


def strong_term_hits(text: str, items: list[Any]) -> list[str]:
    low = canonical_text(text)
    req_words = text_words(text) - WEAK_PROTECT_WORDS
    hits: list[str] = []
    for item in items:
        s = str(item)
        phrase = canonical_text(s).strip()
        if phrase and phrase in low:
            hits.append(s)
            continue
        strong_words = text_words(s) - WEAK_PROTECT_WORDS
        if strong_words and strong_words.issubset(req_words):
            hits.append(s)
    return hits


def north_star() -> dict[str, Any]:
    data = load_json(NORTH_STAR, UNCONFIRMED_NORTH_STAR.copy())
    for key, val in UNCONFIRMED_NORTH_STAR.items():
        data.setdefault(key, val.copy() if isinstance(val, list) else val)
    data["anti_goals"] = north_star_policies(data)
    return data


def confirmed_goal() -> str | None:
    data = north_star()
    if data.get("confirmed") and data.get("goal"):
        return str(data["goal"])
    return None


def program_phase() -> dict[str, Any]:
    return load_json(PROGRAM_PHASE, {"status": "UNSET", "phase_id": None, "goal": None, "exit_criteria": []})


def clean_goal_text_list(values: list[Any] | None) -> list[str]:
    return [str(value).strip() for value in values or [] if str(value).strip()]


def goal_item_text(value: Any, *keys: str) -> str:
    if isinstance(value, dict):
        for key in keys:
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def goal_plan_path(reference: str) -> Path | None:
    value = str(reference or "").strip()
    path = Path(value)
    if not value or path.is_absolute() or path.suffix.lower() not in {".md", ".markdown"}:
        return None
    try:
        resolved = (Path.cwd() / path).resolve()
        resolved.relative_to(Path.cwd().resolve())
    except (OSError, ValueError):
        return None
    if path.parts and path.parts[0] in {".agent", ".codex"}:
        return None
    return resolved


def goal_plan_text(reference: str) -> str:
    path = goal_plan_path(reference)
    if path is None or not path.is_file():
        return ""
    try:
        if path.stat().st_size > 2_000_000:
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def goal_mode_summary_with_reference(definition: dict[str, Any]) -> str:
    summary = str(definition.get("goal_mode_summary") or "").strip()
    reference = str(definition.get("execution_plan_ref") or "").strip()
    if reference and reference not in summary:
        summary = f"{summary}\n\n详细执行方案：{reference}".strip()
    return summary


def render_goal_mode_objective(definition: dict[str, Any]) -> str:
    """Render the executable Goal UI contract beside the concise North Star.

    Super-complex work supplies an authored 2,000-3,500 character compressed
    contract that references the full project plan. It is never truncated.
    """
    if str(definition.get("complexity_level") or "STANDARD").upper() == "SUPER_COMPLEX":
        return goal_mode_summary_with_reference(definition)

    precise_goal = str(definition.get("precise_goal") or definition.get("desired_state") or "").strip()
    lines = [f"目标：{precise_goal}"]

    lines.extend(["", "0. 目标边界与期望状态"])
    lines.append(f"- 北极星方向：{str(definition.get('north_star_goal') or precise_goal).strip()}")
    lines.append(f"- 要解决的问题：{str(definition.get('problem_statement') or '').strip()}")
    lines.append(f"- 当前状态：{str(definition.get('current_state') or '').strip()}")
    lines.append(f"- 目标状态：{str(definition.get('desired_state') or '').strip()}")
    stakeholders = clean_goal_text_list(definition.get("stakeholders") if isinstance(definition.get("stakeholders"), list) else [])
    requirements = clean_goal_text_list(definition.get("source_requirements") if isinstance(definition.get("source_requirements"), list) else [])
    lines.append("- 主要使用者与签收方：" + "；".join(stakeholders))
    lines.append("- 已确认需求：" + "；".join(requirements))

    lines.extend(["", "1. 第一性原理"])
    principles = definition.get("first_principles", []) if isinstance(definition.get("first_principles"), list) else []
    for index, row in enumerate(principles, 1):
        if not isinstance(row, dict):
            continue
        principle = str(row.get("principle") or "").strip()
        rationale = str(row.get("rationale") or "").strip()
        implications = clean_goal_text_list(row.get("implications") if isinstance(row.get("implications"), list) else [])
        lines.append(f"- 原理 {index}：{principle}")
        lines.append(f"  原因：{rationale}")
        if implications:
            lines.append("  执行含义：" + "；".join(implications))

    process = definition.get("process", {}) if isinstance(definition.get("process"), dict) else {}
    nodes = process.get("nodes", []) if isinstance(process.get("nodes"), list) else []
    lines.extend(["", "2. 大板块、具体动作与节点签收"])
    for index, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or f"N{index}").strip()
        name = str(node.get("name") or "Unnamed module").strip()
        objective = str(node.get("objective") or "").strip()
        inputs = clean_goal_text_list(node.get("inputs") if isinstance(node.get("inputs"), list) else [])
        actions = clean_goal_text_list(node.get("actions") if isinstance(node.get("actions"), list) else [])
        outputs = clean_goal_text_list(node.get("outputs") if isinstance(node.get("outputs"), list) else [])
        exits = clean_goal_text_list(node.get("exit_criteria") if isinstance(node.get("exit_criteria"), list) else [])
        dependencies = clean_goal_text_list(node.get("dependencies") if isinstance(node.get("dependencies"), list) else [])
        execution_mode = str(node.get("execution_mode") or "SERIAL").strip().upper()
        parallel_group = str(node.get("parallel_group") or "").strip()
        contribution = str(node.get("contribution_to_goal") or "").strip()
        lines.append(f"- {node_id} {name}")
        lines.append(f"  板块目标：{objective}")
        lines.append(f"  执行关系：{execution_mode}" + (f"（并行组 {parallel_group}）" if parallel_group else ""))
        lines.append("  输入：" + ("；".join(inputs) if inputs else "无前置输入"))
        lines.append("  具体动作：" + ("；".join(actions) if actions else "未定义"))
        lines.append("  节点产出：" + ("；".join(outputs) if outputs else "未定义"))
        lines.append("  签收标准：" + ("；".join(exits) if exits else "未定义"))
        lines.append("  依赖：" + ("；".join(dependencies) if dependencies else "无"))
        lines.append(f"  对总目标的助力：{contribution}")

    lines.extend(["", "3. 模块间联调与接口检验"])
    dependency_checks = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or node.get("name") or "next module")
        dependencies = clean_goal_text_list(node.get("dependencies") if isinstance(node.get("dependencies"), list) else [])
        for dependency in dependencies:
            dependency_checks.append(f"{dependency} 的节点产出必须满足签收标准，且能作为 {node_id} 的有效输入。")
    if dependency_checks:
        lines.extend(f"- {check}" for check in dependency_checks)
    else:
        lines.append("- 各板块无数据或接口依赖时独立验收；存在共享输入输出时，必须先验证格式、语义和错误契约一致。")
    completion = clean_goal_text_list(process.get("completion_conditions") if isinstance(process.get("completion_conditions"), list) else [])
    entry = clean_goal_text_list(process.get("entry_conditions") if isinstance(process.get("entry_conditions"), list) else [])
    lines.extend(f"- 开工条件：{item}" for item in entry)
    lines.extend(f"- 全部模块完成条件：{item}" for item in completion)

    lines.extend(["", "4. 最终成品交付前的全链路检验"])
    final_acceptance = definition.get("final_acceptance", []) if isinstance(definition.get("final_acceptance"), list) else []
    for index, row in enumerate(final_acceptance, 1):
        if not isinstance(row, dict):
            continue
        lines.append(f"- 验收 {index}：{str(row.get('criterion') or '').strip()}")
        lines.append(f"  证据：{str(row.get('evidence') or '').strip()}")
        lines.append(f"  检验方法：{str(row.get('validation_method') or '').strip()}")

    lines.extend(["", "5. 最终成品交付"])
    deliverables = definition.get("deliverables", []) if isinstance(definition.get("deliverables"), list) else []
    for index, row in enumerate(deliverables, 1):
        if not isinstance(row, dict):
            continue
        acceptance = clean_goal_text_list(row.get("acceptance") if isinstance(row.get("acceptance"), list) else [])
        lines.append(
            f"- 交付物 {index}：{str(row.get('name') or '').strip()} | "
            f"格式：{str(row.get('format') or '').strip()} | 消费者：{str(row.get('consumer') or '').strip()}"
        )
        lines.append(f"  说明：{str(row.get('description') or '').strip()}")
        lines.append("  交付标准：" + ("；".join(acceptance) if acceptance else "未定义"))

    lines.extend(["", "6. 约束、非目标与实施假设"])
    constraints = clean_goal_text_list(definition.get("constraints") if isinstance(definition.get("constraints"), list) else [])
    non_goals = clean_goal_text_list(definition.get("non_goals") if isinstance(definition.get("non_goals"), list) else [])
    assumptions = clean_goal_text_list(definition.get("assumptions") if isinstance(definition.get("assumptions"), list) else [])
    lines.append("- 必须遵守：" + "；".join(constraints))
    lines.append("- 本轮明确不做：" + "；".join(non_goals))
    lines.append("- 当前假设：" + ("；".join(assumptions) if assumptions else "无未声明假设"))
    return "\n".join(lines).strip()


def complex_goal_plan_errors(definition: dict[str, Any]) -> list[str]:
    if str(definition.get("complexity_level") or "STANDARD").upper() != "SUPER_COMPLEX":
        return []
    errors: list[str] = []
    reference = str(definition.get("execution_plan_ref") or "").strip()
    plan = goal_plan_text(reference)
    if goal_plan_path(reference) is None:
        errors.append("execution_plan_ref(project-relative markdown)")
    elif not plan:
        errors.append("execution_plan_ref(existing UTF-8 markdown)")
    elif len(plan) < COMPLEX_PLAN_MIN_CHARS:
        errors.append(f"execution_plan_chars>={COMPLEX_PLAN_MIN_CHARS}")
    if plan:
        low = plan.lower()
        required_sections = {
            "plan.objective": ["目标", "objective"],
            "plan.execution_steps": ["执行步骤", "实施步骤", "execution steps"],
            "plan.parallel": ["并行", "parallel"],
            "plan.serial": ["串行", "serial"],
            "plan.dependencies": ["依赖", "dependenc"],
            "plan.goal_contribution": ["对总目标", "目标贡献", "contribution to"],
            "plan.acceptance": ["验收", "acceptance"],
        }
        for field, needles in required_sections.items():
            if not any(needle in low for needle in needles):
                errors.append(field)

    research = definition.get("planning_research") if isinstance(definition.get("planning_research"), dict) else {}
    if research.get("completed") is not True:
        errors.append("planning_research.completed")
    if not str(research.get("researched_at") or "").strip():
        errors.append("planning_research.researched_at")
    try:
        tool_source_count = int(research.get("tool_sources_reviewed") or 0)
    except (TypeError, ValueError):
        tool_source_count = 0
    try:
        article_source_count = int(research.get("article_sources_reviewed") or 0)
    except (TypeError, ValueError):
        article_source_count = 0
    if tool_source_count < 1:
        errors.append("planning_research.tool_sources_reviewed>=1")
    if article_source_count < 1:
        errors.append("planning_research.article_sources_reviewed>=1")
    if research.get("reusable_candidate_found") is True:
        if not str(research.get("reusable_candidate_name") or "").strip():
            errors.append("planning_research.reusable_candidate_name")
        consultation = research.get("user_consultation") if isinstance(research.get("user_consultation"), dict) else {}
        if consultation.get("asked_in_conversation") is not True:
            errors.append("planning_research.user_consultation.asked_in_conversation")
        reuse_choice = str(consultation.get("reuse_choice") or "").strip().upper()
        if reuse_choice not in {"USE", "ADAPT", "REJECT"}:
            errors.append("planning_research.user_consultation.reuse_choice")
        if str(consultation.get("commercial_use") or "").strip().upper() not in {"COMMERCIAL", "NON_COMMERCIAL"}:
            errors.append("planning_research.user_consultation.commercial_use")
    return errors


def detailed_goal_definition_errors(definition: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["precise_goal", "problem_statement", "current_state", "desired_state"]:
        if not str(definition.get(key) or "").strip():
            errors.append(key)
    for key in ["stakeholders", "source_requirements", "constraints", "non_goals"]:
        values = definition.get(key)
        if not isinstance(values, list) or not clean_goal_text_list(values):
            errors.append(key)

    principles = definition.get("first_principles", [])
    if not isinstance(principles, list) or len(principles) < 2:
        errors.append("first_principles>=2")
    else:
        for index, row in enumerate(principles):
            if not isinstance(row, dict) or not all([
                str(row.get("principle") or "").strip(),
                str(row.get("rationale") or "").strip(),
                clean_goal_text_list(row.get("implications") if isinstance(row.get("implications"), list) else []),
            ]):
                errors.append(f"first_principles[{index}].principle/rationale/implications")

    process = definition.get("process", {}) if isinstance(definition.get("process"), dict) else {}
    for key in ["entry_conditions", "completion_conditions"]:
        values = process.get(key)
        if not isinstance(values, list) or not clean_goal_text_list(values):
            errors.append(f"process.{key}")
    nodes = process.get("nodes", [])
    if not isinstance(nodes, list) or len(nodes) < 2:
        errors.append("process.nodes>=2")
    else:
        for index, node in enumerate(nodes):
            required_text = ["node_id", "name", "objective"]
            required_lists = ["inputs", "actions", "outputs", "exit_criteria"]
            if not isinstance(node, dict) or any(not str(node.get(key) or "").strip() for key in required_text):
                errors.append(f"process.nodes[{index}].identity")
                continue
            for key in required_lists:
                values = node.get(key)
                if not isinstance(values, list) or not clean_goal_text_list(values):
                    errors.append(f"process.nodes[{index}].{key}")
            if "dependencies" not in node or not isinstance(node.get("dependencies"), list):
                errors.append(f"process.nodes[{index}].dependencies")
            if str(node.get("execution_mode") or "").strip().upper() not in {"SERIAL", "PARALLEL", "CONDITIONAL"}:
                errors.append(f"process.nodes[{index}].execution_mode")
            if not str(node.get("contribution_to_goal") or "").strip():
                errors.append(f"process.nodes[{index}].contribution_to_goal")
            if str(node.get("execution_mode") or "").strip().upper() == "PARALLEL" and not str(node.get("parallel_group") or "").strip():
                errors.append(f"process.nodes[{index}].parallel_group")

    deliverables = definition.get("deliverables", [])
    if not isinstance(deliverables, list) or not deliverables:
        errors.append("deliverables")
    else:
        for index, row in enumerate(deliverables):
            if not isinstance(row, dict) or not all([
                str(row.get("name") or "").strip(),
                str(row.get("description") or "").strip(),
                str(row.get("format") or "").strip(),
                str(row.get("consumer") or "").strip(),
                clean_goal_text_list(row.get("acceptance") if isinstance(row.get("acceptance"), list) else []),
            ]):
                errors.append(f"deliverables[{index}].name/description/format/consumer/acceptance")

    final_acceptance = definition.get("final_acceptance", [])
    if not isinstance(final_acceptance, list) or not final_acceptance:
        errors.append("final_acceptance")
    else:
        for index, row in enumerate(final_acceptance):
            if not isinstance(row, dict) or not all([
                str(row.get("criterion") or "").strip(),
                str(row.get("evidence") or "").strip(),
                str(row.get("validation_method") or "").strip(),
            ]):
                errors.append(f"final_acceptance[{index}].criterion/evidence/validation_method")
    objective = render_goal_mode_objective(definition)
    if not (GOAL_MODE_OBJECTIVE_MIN_CHARS <= len(objective) <= GOAL_MODE_OBJECTIVE_MAX_CHARS):
        errors.append(f"goal_mode_objective_chars={GOAL_MODE_OBJECTIVE_MIN_CHARS}..{GOAL_MODE_OBJECTIVE_MAX_CHARS}")
    if str(definition.get("complexity_level") or "STANDARD").upper() == "SUPER_COMPLEX":
        reference = str(definition.get("execution_plan_ref") or "").strip()
        summary_requirements = {
            "goal": ["目标", "goal"],
            "modules": ["模块", "板块", "module"],
            "serial": ["串行", "serial"],
            "parallel": ["并行", "parallel"],
            "dependencies": ["依赖", "dependenc"],
            "outputs": ["产出", "output"],
            "goal_contribution": ["对总目标", "目标贡献", "contribution"],
            "acceptance": ["验收", "签收", "acceptance"],
        }
        objective_low = objective.lower()
        for field, needles in summary_requirements.items():
            if not any(needle in objective_low for needle in needles):
                errors.append(f"goal_mode_summary.{field}")
        if reference and reference not in objective:
            errors.append("goal_mode_summary.execution_plan_ref")
    errors.extend(complex_goal_plan_errors(definition))
    return list(dict.fromkeys(errors))


def goal_definition_contract(
    goal: str,
    *,
    precise_goal: str | None = None,
    problem_statement: str | None = None,
    current_state: str | None = None,
    desired_state: str | None = None,
    stakeholders: list[Any] | None = None,
    source_requirements: list[Any] | None = None,
    first_principles: list[Any] | None = None,
    concrete_actions: list[Any] | None = None,
    process: dict[str, Any] | None = None,
    deliverables: list[Any] | None = None,
    success_criteria: list[Any] | None = None,
    final_acceptance: list[Any] | None = None,
    constraints: list[Any] | None = None,
    non_goals: list[Any] | None = None,
    assumptions: list[Any] | None = None,
    open_questions: list[Any] | None = None,
    dialogue_summary: list[Any] | None = None,
    complexity_level: str | None = None,
    goal_mode_summary: str | None = None,
    execution_plan_ref: str | None = None,
    planning_research: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_requirements = clean_goal_text_list(source_requirements)
    dialogue_summary = clean_goal_text_list(dialogue_summary)
    first_principles = list(first_principles or [])
    concrete_actions = clean_goal_text_list(concrete_actions)
    deliverables = list(deliverables or [])
    success_criteria = clean_goal_text_list(success_criteria)
    final_acceptance = list(final_acceptance or [])
    process = dict(process or {})
    process.setdefault("entry_conditions", [])
    process.setdefault("nodes", [])
    process.setdefault("completion_conditions", [])
    complexity_level = str(complexity_level or "STANDARD").strip().upper()
    if complexity_level not in {"STANDARD", "SUPER_COMPLEX"}:
        complexity_level = "STANDARD"
    if not concrete_actions:
        concrete_actions = [
            action
            for node in process.get("nodes", []) if isinstance(node, dict)
            for action in clean_goal_text_list(node.get("actions") if isinstance(node.get("actions"), list) else [])
        ]
    if not success_criteria:
        success_criteria = [
            str(row.get("criterion")).strip()
            for row in final_acceptance if isinstance(row, dict) and str(row.get("criterion") or "").strip()
        ]
    definition = {
        "schema_version": GOAL_DEFINITION_SCHEMA_VERSION,
        "quality": "TEXT_ONLY",
        "north_star_goal": str(goal).strip(),
        "precise_goal": str(precise_goal or goal).strip(),
        "problem_statement": str(problem_statement or goal).strip(),
        "current_state": str(current_state or "").strip() or None,
        "desired_state": str(desired_state or precise_goal or goal).strip(),
        "stakeholders": clean_goal_text_list(stakeholders),
        "source_requirements": source_requirements or dialogue_summary,
        "first_principles": first_principles,
        "concrete_actions": concrete_actions,
        "process": process,
        "deliverables": deliverables,
        "success_criteria": success_criteria,
        "final_acceptance": final_acceptance,
        "constraints": clean_goal_text_list(constraints),
        "non_goals": clean_goal_text_list(non_goals),
        "assumptions": clean_goal_text_list(assumptions),
        "open_questions": clean_goal_text_list(open_questions),
        "dialogue_summary": dialogue_summary,
        "complexity_level": complexity_level,
        "goal_mode_summary": str(goal_mode_summary or "").strip() or None,
        "execution_plan_ref": str(execution_plan_ref or "").strip() or None,
        "planning_research": dict(planning_research) if isinstance(planning_research, dict) else {},
        "missing_fields": [],
        "tooling_is_not_product_goal": True,
    }
    detailed_errors = detailed_goal_definition_errors(definition)
    basic_structured = bool(
        problem_statement
        and first_principles
        and concrete_actions
        and deliverables
        and (success_criteria or final_acceptance)
    )
    definition["quality"] = "STRUCTURED_DETAILED" if not detailed_errors else "STRUCTURED" if basic_structured else "TEXT_ONLY"
    definition["missing_fields"] = detailed_errors
    definition["detail_metrics"] = {
        "first_principle_count": len(first_principles),
        "process_node_count": len(process.get("nodes", [])),
        "deliverable_count": len(deliverables),
        "final_acceptance_count": len(final_acceptance),
        "goal_mode_objective_chars": len(render_goal_mode_objective(definition)),
        "execution_plan_chars": len(goal_plan_text(str(definition.get("execution_plan_ref") or ""))),
    }
    return definition


def goal_definition_from_payload(goal: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("goal_definition") if isinstance(payload.get("goal_definition"), dict) else payload
    return goal_definition_contract(
        goal,
        precise_goal=raw.get("precise_goal"),
        problem_statement=raw.get("problem_statement"),
        current_state=raw.get("current_state"),
        desired_state=raw.get("desired_state"),
        stakeholders=raw.get("stakeholders"),
        source_requirements=raw.get("source_requirements"),
        first_principles=raw.get("first_principles"),
        concrete_actions=raw.get("concrete_actions"),
        process=raw.get("process"),
        deliverables=raw.get("deliverables"),
        success_criteria=raw.get("success_criteria"),
        final_acceptance=raw.get("final_acceptance"),
        constraints=raw.get("constraints"),
        non_goals=raw.get("non_goals"),
        assumptions=raw.get("assumptions"),
        open_questions=raw.get("open_questions"),
        dialogue_summary=raw.get("dialogue_summary"),
        complexity_level=raw.get("complexity_level"),
        goal_mode_summary=raw.get("goal_mode_summary"),
        execution_plan_ref=raw.get("execution_plan_ref"),
        planning_research=raw.get("planning_research"),
    )


def structured_north_star(goal: str, source: str, definition: dict[str, Any] | None = None) -> dict[str, Any]:
    definition = definition if isinstance(definition, dict) else goal_definition_contract(goal)
    clauses = []
    for raw in re.split(r"[\n,，;；。]+", goal):
        value = raw.strip()
        if len(value) >= 4 and value not in clauses:
            clauses.append(value)
    actions = clean_goal_text_list(definition.get("concrete_actions", []))
    deliverables = [goal_item_text(value, "name", "description") for value in definition.get("deliverables", [])]
    process = definition.get("process", {}) if isinstance(definition.get("process"), dict) else {}
    process_nodes = [
        goal_item_text(value, "objective", "name")
        for value in process.get("nodes", [])
    ]
    anchors = [str(definition.get("precise_goal") or goal).strip(), *process_nodes, *actions, *deliverables, *clauses[:10]]
    anchors = [value for index, value in enumerate(anchors) if value and value not in anchors[:index]]
    principles = [goal_item_text(value, "principle") for value in definition.get("first_principles", [])]
    principles = [value for value in principles if value]
    return {
        "contract_version": NORTH_STAR_CONTRACT_VERSION,
        "confirmed": True,
        "goal": goal,
        "source": source,
        "confirmed_at": now(),
        "main_path": anchors,
        "allowed_subgoals": [],
        "anti_goals": [],
        "backlog_domains": [],
        "protected_principles": list(dict.fromkeys([*principles, *DEFAULT_PROTECTED_PRINCIPLES])),
        "core_path_patterns": ["GOAL.md"],
        "candidate_goals": [],
        "requires_confirmation": False,
        "goal_definition": definition,
        "goal_mode_objective": render_goal_mode_objective(definition),
    }


def refresh_north_star_contract(data: dict[str, Any]) -> dict[str, Any]:
    # Existing North Star content is user-owned. Runtime compatibility must be
    # provided by read-time defaults, never by silently rewriting the file.
    return dict(data)


def goal_definition_summary(data: dict[str, Any] | None = None) -> dict[str, Any]:
    source = data if isinstance(data, dict) else north_star()
    definition = source.get("goal_definition", {}) if isinstance(source.get("goal_definition"), dict) else {}
    return {
        "quality": definition.get("quality", "MISSING"),
        "precise_goal": definition.get("precise_goal"),
        "problem_statement": definition.get("problem_statement"),
        "current_state": definition.get("current_state"),
        "desired_state": definition.get("desired_state"),
        "first_principles": list(definition.get("first_principles", []))[:4],
        "process_nodes": list(definition.get("process", {}).get("nodes", []))[:6] if isinstance(definition.get("process"), dict) else [],
        "deliverables": list(definition.get("deliverables", []))[:6],
        "final_acceptance": list(definition.get("final_acceptance", []))[:6],
        "constraints": list(definition.get("constraints", []))[:8],
        "non_goals": list(definition.get("non_goals", []))[:8],
        "complexity_level": definition.get("complexity_level", "STANDARD"),
        "execution_plan_ref": definition.get("execution_plan_ref"),
        "detail_metrics": definition.get("detail_metrics", {}),
        "missing_fields": list(definition.get("missing_fields", [])),
        "goal_mode_objective": source.get("goal_mode_objective") or render_goal_mode_objective(definition),
    }


def flattened_ticket_text(ticket: dict[str, Any]) -> str:
    return json.dumps({
        "global_goal": ticket.get("global_goal"),
        "task_goal": ticket.get("task_goal"),
        "must_do": ticket.get("must_do", []),
        "acceptance": ticket.get("acceptance", {}),
        "validation_ids": ticket.get("validation_ids", []),
    }, ensure_ascii=False)


def current_ticket() -> dict[str, Any]:
    return load_json(CURRENT_TICKET, {"status": "NONE"})


def active_ticket() -> dict[str, Any]:
    ticket = current_ticket()
    return ticket if ticket.get("status") == "ACTIVE" else {}


def last_ticket() -> dict[str, Any]:
    return load_json(LAST_TICKET, {})


def save_current(ticket: dict[str, Any]) -> None:
    disk = current_ticket()
    disk_revision = int(disk.get("state_revision", 0) or 0)
    incoming_revision = int(ticket.get("state_revision", disk_revision) or 0)
    same_activation = bool(
        disk.get("ticket_id")
        and disk.get("ticket_id") == ticket.get("ticket_id")
        and disk.get("run_id") == ticket.get("run_id")
    )
    if same_activation and incoming_revision < disk_revision:
        raise RuntimeError(
            f"STATE_REVISION_CONFLICT: current revision is {disk_revision}, incoming revision is {incoming_revision}"
        )
    ticket["state_revision"] = disk_revision + 1
    write_json(CURRENT_TICKET, ticket)


def terminal_ticket_summary(ticket: dict[str, Any], archive_path: Path | None = None) -> dict[str, Any]:
    return {
        "ticket_id": ticket.get("ticket_id"),
        "title": ticket.get("title"),
        "task_goal": ticket.get("task_goal"),
        "status": ticket.get("status"),
        "closed_at": ticket.get("closed_at") or ticket.get("aborted_at"),
        "archive_path": norm(str(archive_path)) if archive_path else None,
        "acceptance_fingerprint": ticket.get("acceptance_fingerprint"),
    }


def clear_active_ticket(ticket: dict[str, Any], archive_path: Path | None = None) -> None:
    unregister_parallel_lane(ticket)
    summary = terminal_ticket_summary(ticket, archive_path)
    write_json(LAST_TICKET, summary)
    save_current({
        "status": "NONE",
        "active_ticket_id": None,
        "last_ticket_id": summary.get("ticket_id"),
        "updated_at": now(),
    })


def ticket_acceptance_payload(ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "ticket_id": ticket.get("ticket_id"),
            "run_id": ticket.get("run_id"),
            "source_ticket_path": ticket.get("source_ticket_path"),
            "source_ticket_sha256": ticket.get("source_ticket_sha256"),
        },
        "goal_contract": {
            "global_goal": ticket.get("global_goal"),
            "why_now": ticket.get("why_now"),
            "task_goal": ticket.get("task_goal"),
            "must_do": ticket.get("must_do", []),
            "must_not_do": ticket.get("must_not_do", []),
            "anti_patterns": ticket.get("anti_patterns", []),
            "drift_signals": ticket.get("drift_signals", []),
            "backlog_only": ticket.get("backlog_only", []),
        },
        "scope_contract": {
            "execution_mode": ticket.get("execution_mode"),
            "allowed_paths": ticket.get("allowed_paths", []),
            "writable_paths": ticket.get("writable_paths", []),
            "read_dependencies": ticket.get("read_dependencies", []),
            "immutable_paths": ticket.get("immutable_paths", []),
            "runtime_paths": ticket.get("runtime_paths", []),
            "volatile_paths": ticket.get("volatile_paths", []),
            "forbidden_paths": ticket.get("forbidden_paths", []),
        },
        "acceptance": ticket.get("acceptance", {}),
        "validation_ids": ticket.get("validation_ids", []),
        "validation_lifecycle": ticket.get("validation_lifecycle", {}),
        "quality_gates": ticket.get("quality_gates", []),
        "budget": ticket.get("budget", {}),
        "supervision": ticket.get("supervision", {}),
        "execution_relationship": ticket.get("execution_relationship", {}),
        "coordination_contract": ticket.get("coordination_contract", {}),
        "execution_lane": ticket.get("execution_lane", {}),
        "company_contract": {
            "requested_company_departments": ticket.get("requested_company_departments", []),
            "role_contract_fingerprints": ticket.get("mdcp", {}).get("layer_2_company_subagents", {}).get("role_contract_fingerprints", {}),
        },
        "reuse_contract_at_start": ticket.get("reuse_contract_at_start", {}),
        "baseline_identity": {
            "baseline_ref": ticket.get("budget_used", {}).get("baseline_ref"),
            "baseline_sha256": ticket.get("budget_used", {}).get("baseline_sha256"),
            "hook_nonce": ticket.get("budget_used", {}).get("hook_nonce"),
        },
    }


def acceptance_fingerprint(ticket: dict[str, Any]) -> str:
    raw = json.dumps(ticket_acceptance_payload(ticket), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def acceptance_frozen_violation(ticket: dict[str, Any]) -> str | None:
    frozen = ticket.get("acceptance_fingerprint")
    if ticket.get("status") == "ACTIVE" and frozen and frozen != acceptance_fingerprint(ticket):
        return "ACTIVE ticket execution contract changed after start; create a new DRAFT ticket instead."
    source_path = Path(str(ticket.get("source_ticket_path") or ""))
    source_hash = ticket.get("source_ticket_sha256")
    if ticket.get("status") == "ACTIVE" and source_hash and source_path.is_file():
        if sha256_file_contents(source_path) != source_hash:
            return "ACTIVE source ticket bytes changed after start; create a new DRAFT ticket instead."
    return None


def catalog() -> dict[str, Any]:
    return load_catalog(VALIDATION_CATALOG)


def validation_ids(ticket: dict[str, Any]) -> list[str]:
    acc = ticket.get("acceptance", {})
    ids = list(ticket.get("validation_ids", []))
    ids.extend(acc.get("commands_pass", []))
    lifecycle = ticket.get("validation_lifecycle", {})
    if isinstance(lifecycle, dict):
        for phase in ("setup", "healthcheck", "teardown"):
            values = lifecycle.get(phase, [])
            if isinstance(values, list):
                ids.extend(values)
    for gate in ticket.get("quality_gates", []) if isinstance(ticket.get("quality_gates"), list) else []:
        if isinstance(gate, dict) and gate.get("validation_id"):
            ids.append(str(gate["validation_id"]))
    return list(dict.fromkeys(str(x) for x in ids if str(x)))


def valid_validation_ids(ticket: dict[str, Any]) -> list[str]:
    data = catalog()
    return [vid for vid in validation_ids(ticket) if vid in data and (data[vid].get("cmd") or data[vid].get("argv"))]


def command_validation_required(ticket: dict[str, Any]) -> bool:
    return bool(validation_ids(ticket))


def looks_like_raw_command(value: str) -> bool:
    if re.search(r"\s", value):
        return True
    return bool(re.search(r"[;&|<>`$]", value))


def acceptance_contract_errors(ticket: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    acc = ticket.get("acceptance", {})
    if not isinstance(acc, dict):
        return ["acceptance must be an object"]

    data = catalog()
    for source, values in [
        ("validation_ids", ticket.get("validation_ids", [])),
        ("acceptance.commands_pass", acc.get("commands_pass", [])),
    ]:
        if not isinstance(values, list):
            errors.append(f"{source} must be a list of validation_catalog ids")
            continue
        for raw in values:
            vid = str(raw)
            if not vid:
                continue
            if vid not in data:
                if looks_like_raw_command(vid):
                    errors.append(f"{source} must reference validation_catalog ids, not raw shell commands: {vid}")
                else:
                    errors.append(f"{source} id not found in validation_catalog: {vid}")
    lifecycle = ticket.get("validation_lifecycle", {})
    if lifecycle and not isinstance(lifecycle, dict):
        errors.append("validation_lifecycle must be an object")
    elif isinstance(lifecycle, dict):
        for phase in ("setup", "healthcheck", "teardown"):
            values = lifecycle.get(phase, [])
            if values and not isinstance(values, list):
                errors.append(f"validation_lifecycle.{phase} must be a list of validation_catalog ids")
                continue
            for raw in values if isinstance(values, list) else []:
                vid = str(raw)
                if vid not in data:
                    errors.append(f"validation_lifecycle.{phase} id not found in validation_catalog: {vid}")

    for key in ["files_exist", "files_not_changed"]:
        values = acc.get(key, [])
        if values and not isinstance(values, list):
            errors.append(f"acceptance.{key} must be a list")

    contains = acc.get("contains", [])
    if contains and not isinstance(contains, list):
        errors.append("acceptance.contains must be a list")
    elif isinstance(contains, list):
        for item in contains:
            if isinstance(item, str):
                if "::" not in item:
                    errors.append("acceptance.contains string form must be 'path::required text'")
            elif isinstance(item, dict):
                if not item.get("file") or not item.get("text"):
                    errors.append("acceptance.contains dict form must include file and text")
            else:
                errors.append("acceptance.contains supports only 'path::text' strings or {file,text} objects")

    assertions = acc.get("assertions", [])
    if assertions and not isinstance(assertions, list):
        errors.append("acceptance.assertions must be a list")
    elif isinstance(assertions, list):
        for item in assertions:
            if not isinstance(item, dict):
                errors.append("acceptance.assertions supports only objects")
                continue
            typ = str(item.get("type", ""))
            if typ == "file_exists":
                if not (item.get("path") or item.get("file")):
                    errors.append("file_exists assertion must include path or file")
            elif typ == "file_contains":
                if not item.get("file") or not item.get("text"):
                    errors.append("file_contains assertion must include file and text")
            elif typ == "json_field_equals":
                if not item.get("file") or not item.get("path") or "equals" not in item:
                    errors.append("json_field_equals assertion must include file, path, and equals")
            else:
                errors.append(f"unsupported assertion type: {typ or '<missing>'}")
    quality_gates = ticket.get("quality_gates", [])
    if quality_gates and not isinstance(quality_gates, list):
        errors.append("quality_gates must be a list")
    elif isinstance(quality_gates, list):
        seen_gate_ids: set[str] = set()
        allowed_dimensions = {"technical", "artifact", "product", "market"}
        for gate in quality_gates:
            if not isinstance(gate, dict):
                errors.append("quality_gates entries must be objects")
                continue
            gate_id = str(gate.get("id") or "").strip()
            dimension = str(gate.get("dimension") or "").strip()
            if not gate_id:
                errors.append("quality gate requires id")
            elif gate_id in seen_gate_ids:
                errors.append(f"duplicate quality gate id: {gate_id}")
            seen_gate_ids.add(gate_id)
            if dimension not in allowed_dimensions:
                errors.append(f"quality gate {gate_id or '<missing>'} has unsupported dimension: {dimension or '<missing>'}")
            evidence_types = gate.get("evidence_types", [])
            if evidence_types and not isinstance(evidence_types, list):
                errors.append(f"quality gate {gate_id or '<missing>'} evidence_types must be a list")
            if not gate.get("validation_id") and not evidence_types:
                errors.append(f"quality gate {gate_id or '<missing>'} needs validation_id or evidence_types")
    return errors


def has_machine_acceptance(ticket: dict[str, Any]) -> bool:
    acc = ticket.get("acceptance", {}) if isinstance(ticket.get("acceptance"), dict) else {}
    return any([
        bool(acc.get("files_exist")),
        bool(acc.get("contains")),
        bool(acc.get("assertions")),
        bool(valid_validation_ids(ticket)),
    ])


def acceptance_quality(ticket: dict[str, Any]) -> dict[str, Any]:
    acc = ticket.get("acceptance", {}) if isinstance(ticket.get("acceptance"), dict) else {}
    behavioral = bool(validation_ids(ticket)) or any(
        isinstance(item, dict) and item.get("type") == "json_field_equals"
        for item in acc.get("assertions", [])
    )
    syntactic = bool(acc.get("files_exist") or acc.get("contains") or acc.get("assertions"))
    if behavioral:
        return {"level": "BEHAVIORAL", "warning": None}
    if syntactic:
        return {
            "level": "SYNTACTIC_ONLY",
            "warning": "Acceptance checks structure or text only; add a focused validation_catalog command when behavior matters.",
        }
    return {"level": "MISSING", "warning": MISSING_ACCEPTANCE_MESSAGE}


def validate_shape(ticket: dict[str, Any]) -> list[str]:
    required = [
        "ticket_id",
        "title",
        "global_goal",
        "why_now",
        "task_goal",
        "status",
        "acceptance_ready",
        "must_do",
        "must_not_do",
        "anti_patterns",
        "allowed_paths",
        "forbidden_paths",
        "acceptance",
        "validation_ids",
        "budget",
        "drift_signals",
        "backlog_only",
    ]
    missing = [k for k in required if k not in ticket]
    if not isinstance(ticket.get("acceptance", {}), dict):
        missing.append("acceptance must be an object")
    return missing


def fixed_pattern_prefix(pattern: str) -> str:
    value = norm(pattern)
    wildcard_at = min((value.find(mark) for mark in ("*", "?", "[") if mark in value), default=len(value))
    return value[:wildcard_at].rstrip("/")


def forbidden_covers_allowed(forbidden: str, allowed: str) -> bool:
    deny = norm(forbidden)
    permit = norm(allowed)
    if not deny or not permit:
        return False
    if deny == permit:
        return True
    permit_prefix = fixed_pattern_prefix(permit)
    if deny.endswith("/**"):
        deny_root = deny[:-3].rstrip("/")
        return permit_prefix == deny_root or permit_prefix.startswith(deny_root + "/")
    if not any(mark in permit for mark in ("*", "?", "[")):
        return match_path(permit, [deny])
    return False


def acceptance_positive_paths(ticket: dict[str, Any]) -> list[str]:
    """Return only artifacts whose presence/content is positively required."""
    acc = ticket.get("acceptance", {}) if isinstance(ticket.get("acceptance"), dict) else {}
    paths: list[str] = [norm(str(value)) for value in acc.get("files_exist", []) if str(value).strip()]
    for item in acc.get("contains", []):
        if isinstance(item, str) and "::" in item:
            paths.append(norm(item.split("::", 1)[0]))
        elif isinstance(item, dict) and item.get("file"):
            paths.append(norm(str(item["file"])))
    for item in acc.get("assertions", []):
        if not isinstance(item, dict):
            continue
        candidate = item.get("file") or item.get("path")
        if candidate:
            paths.append(norm(str(candidate)))
    for command_id in validation_ids(ticket):
        row = catalog().get(command_id, {})
        for candidate in row.get("protects_paths", []) if isinstance(row, dict) else []:
            paths.append(norm(str(candidate)))
    return list(dict.fromkeys(path for path in paths if path))


def ticket_writable_paths(ticket: dict[str, Any]) -> list[str]:
    paths = ticket.get("writable_paths")
    if isinstance(paths, list) and paths:
        return [norm(str(value)) for value in paths if str(value).strip()]
    legacy = ticket.get("allowed_paths", [])
    return [norm(str(value)) for value in legacy if str(value).strip()] if isinstance(legacy, list) else []


def validation_path_values(ticket: dict[str, Any], key: str) -> list[str]:
    values: list[str] = []
    for command_id in validation_ids(ticket):
        row = catalog().get(command_id, {})
        if not isinstance(row, dict):
            continue
        raw = row.get(key, [])
        if isinstance(raw, list):
            values.extend(norm(str(value)) for value in raw if str(value).strip())
    return values


def ticket_read_dependencies(ticket: dict[str, Any]) -> list[str]:
    values = ticket.get("read_dependencies", [])
    direct = [norm(str(value)) for value in values if str(value).strip()] if isinstance(values, list) else []
    return list(dict.fromkeys([*direct, *validation_path_values(ticket, "reads_paths"), *validation_path_values(ticket, "protects_paths")]))


def ticket_immutable_paths(ticket: dict[str, Any]) -> list[str]:
    values = ticket.get("immutable_paths", [])
    direct = [norm(str(value)) for value in values if str(value).strip()] if isinstance(values, list) else []
    acceptance = ticket.get("acceptance", {}) if isinstance(ticket.get("acceptance"), dict) else {}
    files_not_changed = [norm(str(value)) for value in acceptance.get("files_not_changed", []) if str(value).strip()]
    return list(dict.fromkeys([*direct, *files_not_changed, *validation_path_values(ticket, "protects_paths")]))


def ticket_runtime_paths(ticket: dict[str, Any]) -> list[str]:
    values = ticket.get("runtime_paths", [])
    return [norm(str(value)) for value in values if str(value).strip()] if isinstance(values, list) else []


def execution_relationship(ticket: dict[str, Any]) -> dict[str, Any]:
    value = ticket.get("execution_relationship", {})
    if not isinstance(value, dict):
        value = {}
    return {
        "mode": str(value.get("mode") or "STANDALONE").upper(),
        "depends_on": [str(item) for item in value.get("depends_on", []) if str(item)] if isinstance(value.get("depends_on", []), list) else [],
        "produces_contracts": [str(item) for item in value.get("produces_contracts", []) if str(item)] if isinstance(value.get("produces_contracts", []), list) else [],
        "consumes_contracts": [str(item) for item in value.get("consumes_contracts", []) if str(item)] if isinstance(value.get("consumes_contracts", []), list) else [],
        "rationale": str(value.get("rationale") or ""),
    }


def coordination_contract_ref(ticket: dict[str, Any]) -> dict[str, Any]:
    value = ticket.get("coordination_contract", {})
    return value if isinstance(value, dict) else {}


def coordination_contract_file(ticket: dict[str, Any]) -> Path | None:
    raw = str(coordination_contract_ref(ticket).get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        path.resolve().relative_to(COORDINATION_CONTRACTS.resolve())
    except (OSError, ValueError):
        return None
    return path


def load_coordination_contract(ticket: dict[str, Any]) -> dict[str, Any]:
    path = coordination_contract_file(ticket)
    if not path:
        return {}
    return load_json(path, {})


def nonempty_contract_section(value: Any) -> bool:
    if isinstance(value, dict):
        if str(value.get("status") or "").upper() == "NOT_APPLICABLE":
            return bool(str(value.get("reason") or "").strip())
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return bool(str(value or "").strip())


def coordination_efficiency_summary(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("efficiency_case", {}) if isinstance(payload.get("efficiency_case"), dict) else {}

    def number(key: str) -> float | None:
        value = raw.get(key)
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    serial = number("estimated_serial_minutes")
    parallel = number("estimated_parallel_minutes")
    coordination = number("coordination_minutes")
    integration = number("integration_minutes")
    valid = bool(
        serial is not None
        and parallel is not None
        and coordination is not None
        and integration is not None
        and serial > 0
        and parallel > 0
        and coordination >= 0
        and integration >= 0
    )
    if not valid:
        return {
            "status": "INVALID",
            "estimated_serial_minutes": serial,
            "estimated_parallel_minutes": parallel,
            "coordination_minutes": coordination,
            "integration_minutes": integration,
            "reason": "efficiency_case requires positive serial/parallel minutes and non-negative coordination/integration minutes",
        }
    parallel_total = float(parallel + coordination + integration)
    net_gain = float(serial - parallel_total)
    gain_ratio = net_gain / float(serial)
    required_gain = max(PARALLEL_MIN_NET_GAIN_MINUTES, float(serial) * PARALLEL_MIN_GAIN_RATIO)
    worthwhile = net_gain >= required_gain
    return {
        "status": "WORTHWHILE" if worthwhile else "SERIAL_PREFERRED",
        "estimated_serial_minutes": serial,
        "estimated_parallel_minutes": parallel,
        "coordination_minutes": coordination,
        "integration_minutes": integration,
        "estimated_parallel_total_minutes": round(parallel_total, 2),
        "net_gain_minutes": round(net_gain, 2),
        "net_gain_ratio": round(gain_ratio, 4),
        "required_net_gain_minutes": round(required_gain, 2),
    }


def coordination_contract_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    relation = execution_relationship(ticket)
    if relation["mode"] != "PARALLEL":
        return {"mode": relation["mode"], "required": False}
    ref = coordination_contract_ref(ticket)
    payload = load_coordination_contract(ticket)
    return {
        "mode": "PARALLEL",
        "required": True,
        "path": ref.get("path"),
        "contract_id": ref.get("contract_id") or payload.get("contract_id"),
        "sha256": ref.get("sha256"),
        "version": ref.get("version") or payload.get("version"),
        "applicable_sections": list(payload.get("applicable_sections", [])) if isinstance(payload.get("applicable_sections"), list) else [],
        "efficiency": coordination_efficiency_summary(payload),
    }


def coordination_contract_errors(ticket: dict[str, Any], verify_frozen: bool = False) -> list[str]:
    relation = execution_relationship(ticket)
    if relation["mode"] != "PARALLEL":
        return []
    ref = coordination_contract_ref(ticket)
    path = coordination_contract_file(ticket)
    errors: list[str] = []
    if path is None:
        return ["parallel ticket requires coordination_contract.path under .agent/contracts/**"]
    payload = load_json(path, {})
    if not payload:
        return [f"coordination contract missing or invalid: {path}"]
    contract_id = str(payload.get("contract_id") or "").strip()
    if not contract_id:
        errors.append("coordination contract requires contract_id")
    if not payload.get("version"):
        errors.append("coordination contract requires version")
    for section in PARALLEL_BASE_CONTRACT_SECTIONS:
        if not nonempty_contract_section(payload.get(section)):
            errors.append(f"coordination contract section missing or empty: {section}")

    applicable = payload.get("applicable_sections", [])
    if not isinstance(applicable, list):
        errors.append("coordination contract applicable_sections must be a list")
        applicable = []
    applicable = [str(section) for section in applicable if str(section).strip()]
    unknown_sections = sorted(set(applicable) - PARALLEL_TECHNICAL_CONTRACT_SECTIONS)
    if unknown_sections:
        errors.append("coordination contract has unknown applicable_sections: " + ", ".join(unknown_sections))
    for section in applicable:
        if section in PARALLEL_TECHNICAL_CONTRACT_SECTIONS and not nonempty_contract_section(payload.get(section)):
            errors.append(f"coordination contract applicable section missing or empty: {section}")
    independence = payload.get("independence_evidence", {}) if isinstance(payload.get("independence_evidence"), dict) else {}
    no_shared_surface = bool(independence.get("no_shared_surface")) or str(independence.get("shared_surface") or "").lower() in {
        "none", "no_shared_surface", "independent",
    }
    if not applicable and not no_shared_surface:
        errors.append("parallel contract must list shared technical dimensions in applicable_sections or prove no_shared_surface")

    efficiency = coordination_efficiency_summary(payload)
    if efficiency["status"] == "INVALID":
        errors.append(str(efficiency["reason"]))
    elif efficiency["status"] != "WORTHWHILE":
        errors.append(
            "parallel overhead exceeds expected gain; run serially "
            f"(net {efficiency['net_gain_minutes']}m, required {efficiency['required_net_gain_minutes']}m)"
        )

    quality = payload.get("quality_validation", {}) if isinstance(payload.get("quality_validation"), dict) else {}
    if quality and not any(quality.get(key) for key in ("lane_validation_ids", "per_ticket", "cross_lane_validation_ids", "checks")):
        errors.append("quality_validation must name lane or cross-lane machine checks")
    integration = payload.get("integration_plan", {}) if isinstance(payload.get("integration_plan"), dict) else {}
    if integration and not any(integration.get(key) for key in ("owner_ticket", "owner", "validation_owner", "integration_not_required_reason")):
        errors.append("integration_plan must identify the integration owner or explain why integration is not required")
    actual_hash = file_sha256(path)
    expected_id = str(ref.get("contract_id") or "").strip()
    expected_hash = str(ref.get("sha256") or "").strip()
    if expected_id and expected_id != contract_id:
        errors.append(f"coordination contract id mismatch: {expected_id} != {contract_id}")
    if verify_frozen and expected_hash and actual_hash != expected_hash:
        errors.append("coordination contract changed after ticket start")
    for schema_path in payload.get("shared_schema_paths", []) if isinstance(payload.get("shared_schema_paths"), list) else []:
        if match_path(norm(str(schema_path)), ticket_writable_paths(ticket)):
            errors.append(f"shared schema must be read-only during parallel execution: {schema_path}")
    return errors


def refresh_coordination_contract(ticket: dict[str, Any]) -> dict[str, Any]:
    relation = execution_relationship(ticket)
    ticket["execution_relationship"] = relation
    if relation["mode"] != "PARALLEL":
        return ticket
    path = coordination_contract_file(ticket)
    payload = load_json(path, {}) if path else {}
    if path and payload:
        existing = coordination_contract_ref(ticket)
        if ticket.get("status") == "ACTIVE" and existing.get("sha256"):
            # The ACTIVE ticket keeps the frozen contract fingerprint. Runtime
            # checks compare the file to this value instead of silently moving
            # the baseline.
            ticket["coordination_contract"] = dict(existing)
        else:
            ticket["coordination_contract"] = {
                **existing,
                "path": norm(str(path)),
                "contract_id": payload.get("contract_id"),
                "sha256": file_sha256(path),
                "version": payload.get("version"),
                "efficiency": coordination_efficiency_summary(payload),
            }
    return ticket


def execution_relationship_errors(ticket: dict[str, Any]) -> list[str]:
    relation = execution_relationship(ticket)
    errors: list[str] = []
    if relation["mode"] not in {"STANDALONE", "SERIAL", "PARALLEL"}:
        errors.append("execution_relationship.mode must be STANDALONE, SERIAL, or PARALLEL")
    if relation["mode"] == "STANDALONE" and relation["depends_on"]:
        errors.append("ticket with depends_on must use SERIAL or PARALLEL mode")
    if relation["mode"] == "PARALLEL" and not relation["rationale"]:
        errors.append("parallel ticket requires a dependency-analysis rationale")
    ticket_id = str(ticket.get("ticket_id") or "")
    if ticket_id and ticket_id in relation["depends_on"]:
        errors.append("ticket cannot depend on itself")
    duplicate_contracts = sorted(set(relation["produces_contracts"]) & set(relation["consumes_contracts"]))
    if duplicate_contracts:
        errors.append("ticket cannot both produce and consume the same contract: " + ", ".join(duplicate_contracts))
    errors.extend(coordination_contract_errors(ticket))
    return errors


def implicit_runtime_path(path: str, ticket: dict[str, Any]) -> bool:
    p = norm(path)
    if volatile_path(p, ticket):
        return True
    candidate = Path(p)
    if candidate.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        # Database files are service state by default. Explicit ticket path
        # contracts still take precedence in path_contract_role below.
        return True
    return False


def path_contract_role(path: str, ticket: dict[str, Any]) -> str:
    p = norm(path)
    if match_path(p, ticket_immutable_paths(ticket)):
        return "immutable"
    if match_path(p, ticket_runtime_paths(ticket)):
        return "runtime"
    if match_path(p, ticket_writable_paths(ticket)):
        return "writable"
    if match_path(p, ticket_read_dependencies(ticket)):
        return "read_dependency"
    if implicit_runtime_path(p, ticket):
        return "runtime"
    return "outside"


def ticket_path_contract_errors(ticket: dict[str, Any]) -> list[str]:
    allowed = ticket_writable_paths(ticket)
    forbidden = ticket.get("forbidden_paths", [])
    if not isinstance(allowed, list) or not isinstance(forbidden, list):
        return ["allowed_paths and forbidden_paths must be lists"]
    errors: list[str] = []
    for permit in allowed:
        for blocked_role, patterns in [
            ("immutable_paths", ticket_immutable_paths(ticket)),
            ("runtime_paths", ticket_runtime_paths(ticket)),
        ]:
            for pattern in patterns:
                if forbidden_covers_allowed(pattern, permit) or forbidden_covers_allowed(permit, pattern):
                    errors.append(f"writable path overlaps {blocked_role}: {permit} <-> {pattern}")
                    break
    for permit in allowed:
        for deny in forbidden:
            if forbidden_covers_allowed(str(deny), str(permit)):
                errors.append(f"allowed path is fully blocked by forbidden_paths: {permit} <- {deny}")
                break

    for path in acceptance_positive_paths(ticket):
        role = path_contract_role(path, ticket)
        if match_path(path, [str(value) for value in forbidden]) and role not in {"read_dependency", "immutable"}:
            errors.append(f"acceptance path is forbidden: {path}")
        elif allowed and role == "outside":
            errors.append(f"acceptance path is outside writable_paths/read_dependencies/immutable_paths: {path}")
        # Existing immutable evidence may be read by acceptance. It is only a
        # conflict when implementation is also allowed to write the same path,
        # which the writable/immutable overlap checks above already reject.

    task_text = "\n".join([
        str(ticket.get("task_goal", "")),
        *[str(item) for item in ticket.get("must_do", [])],
    ])
    referenced = {
        norm(match)
        for match in re.findall(r"(?:[A-Za-z0-9_.-]+/)+(?:[A-Za-z0-9_.*-]+)", task_text)
    }
    for path in sorted(referenced):
        if match_path(path, [str(value) for value in forbidden]) and not match_path(path, [str(value) for value in allowed]):
            errors.append(f"task requires a forbidden path that is not allowed: {path}")
    return errors


def validation_command_parts(row: dict[str, Any]) -> tuple[list[str], str | None]:
    return catalog_command_parts(row)


def preflight_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    advisories: list[str] = []
    data = catalog()
    for command_id in validation_ids(ticket):
        row = data.get(command_id)
        if not isinstance(row, dict):
            blockers.append(f"validation id not found: {command_id}")
            continue
        parts, error = validation_command_parts(row)
        if error:
            blockers.append(f"validation {command_id}: {error}")
            continue
        executable = parts[0] if parts else ""
        if executable and not Path(executable).is_file() and shutil.which(executable) is None:
            blockers.append(f"validation executable unavailable: {command_id} -> {executable}")

    dependency_patterns = list(dict.fromkeys([*ticket_read_dependencies(ticket), *ticket_immutable_paths(ticket)]))
    for raw in dependency_patterns:
        pattern = norm(str(raw))
        if not pattern or match_path(pattern, ticket.get("acceptance", {}).get("files_not_changed", [])):
            continue
        if any(mark in pattern for mark in ("*", "?", "[")):
            try:
                matches = filesystem_matches(pattern)
            except (NotImplementedError, OSError, ValueError):
                matches = []
            if not matches:
                blockers.append(f"preflight dependency pattern has no matches: {pattern}")
        elif not filesystem_path(pattern).exists():
            blockers.append(f"preflight dependency missing: {pattern}")

    path_errors = ticket_path_contract_errors(ticket)
    blockers.extend(path_errors)
    if ticket.get("acceptance_quality", acceptance_quality(ticket)).get("level") == "SYNTACTIC_ONLY":
        advisories.append("Acceptance is syntactic-only; add focused behavioral validation when runtime behavior matters.")
    return {
        "status": "BLOCKED" if blockers else "READY",
        "blockers": list(dict.fromkeys(blockers)),
        "advisories": list(dict.fromkeys(advisories)),
        "checked_at": now(),
    }


def start_errors(ticket: dict[str, Any]) -> list[str]:
    errors = validate_shape(ticket)
    errors.extend(acceptance_contract_errors(ticket))
    errors.extend(preflight_ticket(ticket)["blockers"])
    errors.extend(e for e in mdcp_layer_1_errors(ticket) if e not in errors)
    errors.extend(e for e in company_subagent_policy_errors(ticket) if e not in errors)
    errors.extend(e for e in execution_relationship_errors(ticket) if e not in errors)
    errors.extend(e for e in reuse_contract_errors(ticket) if e not in errors)
    errors.extend(e for e in batch_execution_errors(ticket) if e not in errors)
    if ticket.get("status") == "DRAFT":
        errors.append("DRAFT ticket cannot start; run `python3 .agent/goal_compass.py ready <ticket>` after filling machine acceptance.")
    if ticket.get("status") == "ACTIVE" or ticket.get("status") in TERMINAL_TICKET_STATUSES:
        errors.append(f"terminal or active ticket cannot start again: {ticket.get('status')}")
    if ticket.get("acceptance_ready") is not True:
        errors.append("acceptance_ready must be true before start")
    if not has_machine_acceptance(ticket):
        errors.append(MISSING_ACCEPTANCE_MESSAGE)
    if not ticket_writable_paths(ticket) and ticket.get("execution_mode") != "read_only":
        errors.append("writable_paths/allowed_paths must not be empty")
    if not ticket.get("forbidden_paths"):
        errors.append("forbidden_paths must not be empty")
    if not ticket.get("drift_signals"):
        errors.append("drift_signals must not be empty")
    budget = ticket.get("budget", {})
    if not budget or not any(budget.get(k) for k in ["max_minutes", "max_tool_calls", "max_changed_files", "max_diff_lines"]):
        errors.append("budget must not be empty")
    return errors


def collect_text_sources(roots_raw: list[str], include_agent_aux: bool) -> list[tuple[str, str]]:
    roots: list[Path] = []
    for raw in roots_raw:
        root = Path(raw)
        if root.is_file():
            roots.append(root)
        elif root.is_dir():
            roots.extend(p for p in root.rglob("*") if p.is_file())
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in roots:
        p = norm(str(path))
        if p in seen or not should_scan(p, include_agent_aux=include_agent_aux):
            continue
        if not is_text_artifact(path):
            continue
        seen.add(p)
        if path.exists() and path.is_file() and path.stat().st_size <= 250000:
            text = read_text(path, 30000)
            if text.strip():
                out.append((p, text))
    return out[:300]


def project_text_sources() -> list[tuple[str, str]]:
    return collect_text_sources(GOAL_DETECT_ROOTS, include_agent_aux=False)


def is_goal_compass_tool_repo() -> bool:
    return (
        Path(".codex-plugin/plugin.json").exists()
        or Path("assets/governor-harness/.agent/goal_compass.py").exists()
        or Path("verification/tests").exists()
    )


def infer_project_goals() -> list[dict[str, Any]]:
    sources = project_text_sources()
    sources_for_goal = [(p, t) for p, t in sources if not p.startswith(".agent/")]
    joined = "\n".join(text for _, text in sources_for_goal).lower()
    candidates: list[dict[str, Any]] = documented_goal_candidates(sources_for_goal)
    has_goal_compass_self = any(w in joined for w in ["goal compass", "goal_compass", "north star", "scope-sink", "anti-shit-mountain"])

    for candidate in candidates:
        goal_words = text_words(str(candidate.get("goal") or ""))
        support = list(candidate.get("supporting_evidence", []))
        for path, source_text in sorted(sources_for_goal, key=lambda row: goal_source_weight(row[0]), reverse=True):
            if any(path in item for item in support):
                continue
            overlap = goal_words & text_words(source_text)
            if len(overlap) < 2 or goal_source_weight(path) < 0.75 or goal_evidence_block_hits(path, source_text):
                continue
            support.append(f"{path} supports the documented project goal")
            if len(support) >= 5:
                break
        candidate["evidence"] = support
        candidate["supporting_evidence"] = support

    if has_goal_compass_self and not candidates and is_goal_compass_tool_repo():
        evidence, noise = split_goal_evidence(
            sources_for_goal,
            r"Goal Compass|goal_compass|scope-sink|North Star|anti-shit",
            "mentions Goal Compass or goal drift control",
        )
        candidates.append({
            "goal": "Build a Codex Goal Compass that keeps long Codex goals aligned, bounded, and free of noise.",
            "confidence": 0.86,
            "evidence": evidence,
            "supporting_evidence": evidence,
            "backlog_candidate_evidence": [],
            "noise_evidence": noise,
            "contradicting_evidence": noise,
        })

    scope_noise: list[str] = []
    for source_path, source_text in sources_for_goal:
        blocked = goal_evidence_block_hits(source_path, source_text)
        if blocked:
            scope_noise.append(f"{source_path} matches future/noise scope: {', '.join(blocked[:3])}")
    scope_noise = list(dict.fromkeys(scope_noise))[:12]
    if scope_noise:
        for candidate in candidates:
            if candidate.get("source_type") != "user_project_document":
                continue
            candidate["backlog_candidate_evidence"] = scope_noise
            candidate["noise_evidence"] = scope_noise
            candidate["contradicting_evidence"] = scope_noise
    if not candidates:
        candidates.append({
            "goal": "Unknown project goal.",
            "confidence": 0.0,
            "evidence": ["No strong product goal evidence found."],
            "supporting_evidence": [],
            "backlog_candidate_evidence": [],
            "noise_evidence": [],
            "contradicting_evidence": [],
        })
    return sorted(candidates, key=lambda row: float(row.get("confidence", 0.0)), reverse=True)


def goal_match(text: str, north: dict[str, Any] | None = None) -> dict[str, Any]:
    north = north or north_star()
    if not north.get("confirmed") or not north.get("goal"):
        return {
            "status": "UNKNOWN",
            "alignment_score": 0.0,
            "supporting_evidence": [],
            "contradicting_evidence": [],
            "required_action": "confirm_north_star",
        }
    if canonical_text(text).strip() == canonical_text(str(north.get("goal"))).strip():
        return {
            "status": "ALIGNED",
            "alignment_score": 1.0,
            "supporting_evidence": ["Exact confirmed North Star match"],
            "contradicting_evidence": [],
            "required_action": "continue",
        }
    anti = term_hits(text, north.get("anti_goals", []))
    backlog = term_hits(text, north.get("backlog_domains", []))
    allowed = term_hits(text, north.get("allowed_subgoals", []))
    main_path = term_hits(text, north.get("main_path", []))
    heavy = filter_contextual_scope_hits(heavy_hits(text), text, current_ticket())
    if anti:
        return {
            "status": "MISMATCH",
            "alignment_score": 0.0,
            "supporting_evidence": [],
            "contradicting_evidence": anti,
            "required_action": "reject_or_rewrite",
        }
    if backlog or (heavy and not allowed and not main_path):
        return {
            "status": "PARTIAL",
            "alignment_score": 0.35,
            "supporting_evidence": backlog,
            "contradicting_evidence": heavy,
            "required_action": "backlog_or_split",
        }
    if allowed and main_path:
        return {
            "status": "ALIGNED",
            "alignment_score": 0.9,
            "supporting_evidence": allowed + main_path,
            "contradicting_evidence": [],
            "required_action": "continue",
        }
    if allowed or main_path:
        return {
            "status": "PARTIAL",
            "alignment_score": 0.65,
            "supporting_evidence": allowed + main_path,
            "contradicting_evidence": [],
            "required_action": "continue",
        }
    n_words = text_words(str(north.get("goal")))
    t_words = text_words(text)
    overlap = sorted(n_words & t_words)
    score = len(overlap) / max(1, min(len(n_words), len(t_words)))
    if score < 0.18:
        generic = {
            "build", "create", "deliver", "develop", "system", "project", "product",
            "tool", "application", "app", "automatic", "current", "goal", "using",
        }
        north_signature = {
            word.lower()
            for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", canonical_text(str(north.get("goal"))))
            if word.lower() not in generic
        }
        text_signature = {
            word.lower()
            for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", canonical_text(text))
            if word.lower() not in generic
        }
        signature_overlap = sorted(north_signature & text_signature)
        signature_score = len(signature_overlap) / max(1, min(len(north_signature), len(text_signature)))
        if len(signature_overlap) >= 2 and signature_score > score:
            overlap = signature_overlap
            score = signature_score
    if score >= 0.45:
        status = "ALIGNED"
    elif score >= 0.18 and not heavy:
        status = "PARTIAL"
    else:
        status = "MISMATCH"
    return {
        "status": status,
        "intervention": "SILENT" if status in {"CLEAN", "NOT_APPLICABLE", "NOT_REQUIRED"} else "STRONG_WARNING",
        "binding": False,
        "capability": "JANITOR_MARK_ONLY",
        "alignment_score": round(score, 2),
        "supporting_evidence": overlap,
        "contradicting_evidence": [] if status != "MISMATCH" else [MISMATCH_MESSAGE],
        "required_action": "continue" if status in {"ALIGNED", "PARTIAL"} else "force_override_required",
    }


def compare_goals(north_goal: str | None, user_goal: str | None) -> dict[str, Any]:
    if not north_goal or not user_goal:
        return goal_match(user_goal or "", {"confirmed": False})
    data = north_star()
    if data.get("goal") == north_goal:
        return goal_match(user_goal, data)
    return goal_match(user_goal, structured_north_star(north_goal, "inferred"))


def goal_report(candidate_goals: list[dict[str, Any]], status: str = "UNKNOWN", user_goal: str | None = None) -> dict[str, Any]:
    north = north_star()
    detected = candidate_goals[0]["goal"] if candidate_goals else None
    contradictions: list[str] = []
    if north.get("confirmed") and detected and detected != "Unknown project goal.":
        check = goal_match(detected, north)
        contradictions = check["contradicting_evidence"]
        if check["status"] == "MISMATCH" and MISMATCH_MESSAGE not in contradictions:
            contradictions.append(MISMATCH_MESSAGE)
    return {
        "detected_candidate_goals": candidate_goals,
        "confirmed_north_star_goal": north.get("goal"),
        "project_detected_goal": detected,
        "user_goal": user_goal,
        "alignment_status": status,
        "contradictions": contradictions,
        "requires_user_confirmation": not bool(north.get("confirmed")),
        "required_action": "confirm_north_star" if not north.get("confirmed") else "continue",
    }


def write_goal_report(report: dict[str, Any]) -> None:
    write_json(GOAL_REPORT_JSON, report)
    lines = [
        "# Goal Alignment Report",
        "",
        "## Confirmed North Star Goal",
        "",
        str(report.get("confirmed_north_star_goal") or "Not confirmed."),
        "",
        "## Detected Project Goal",
        "",
        str(report.get("project_detected_goal") or "Unknown."),
        "",
        "## Alignment Status",
        "",
        str(report.get("alignment_status") or "UNKNOWN"),
        "",
        "## Noise Inventory",
        "",
    ]
    for item in report.get("noise_inventory", [])[:60]:
        lines.append(f"- `{item.get('artifact')}`: {item.get('classification')} - {item.get('reason')}")
    if not report.get("noise_inventory"):
        lines.append("- None.")
    lines.extend(["", "## Supporting Evidence", ""])
    for cand in report.get("detected_candidate_goals", [])[:3]:
        for item in cand.get("evidence", [])[:5]:
            lines.append(f"- {item}")
    lines.extend(["", "## Contradicting Evidence", ""])
    contradictions = report.get("contradictions", [])
    lines.extend(f"- {item}" for item in contradictions) if contradictions else lines.append("- None detected.")
    lines.extend(["", "## Required Action", "", str(report.get("required_action") or "continue")])
    if report.get("alignment_status") == "MISMATCH":
        lines.extend(["", MISMATCH_MESSAGE])
    elif report.get("alignment_status") == "UNKNOWN":
        lines.extend(["", NORTH_STAR_CONFIRMATION_MESSAGE])
    GOAL_REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def goal_allows_ticket(ticket: dict[str, Any]) -> tuple[bool, str]:
    north = north_star()
    if not north.get("confirmed"):
        return False, "项目原始目标未确认，不能启动 active ticket。"
    text = f"{ticket.get('global_goal', '')}\n{ticket.get('task_goal', '')}\n{ticket.get('why_now', '')}"
    check = goal_match(text, north)
    if check["status"] == "MISMATCH":
        return False, MISMATCH_MESSAGE
    return True, "ok"


def run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                except (OSError, subprocess.SubprocessError):
                    proc.kill()
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    proc.kill()
            stdout, stderr = proc.communicate()
            return subprocess.CompletedProcess(cmd, 124, stdout or "", (stderr or "") + "\ntimeout")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def is_git_repo() -> bool:
    """Detect a normal Git worktree without spawning Git on every status path."""
    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        if (parent / ".git").exists():
            return True
    if os.environ.get("GIT_DIR"):
        return run(["git", "rev-parse", "--is-inside-work-tree"], timeout=2).returncode == 0
    return False


def git_resolved_path(*args: str) -> Path | None:
    proc = run(["git", "rev-parse", *args])
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    path = Path(proc.stdout.strip())
    return (path if path.is_absolute() else Path.cwd() / path).resolve()


def git_worktree_root() -> Path | None:
    return git_resolved_path("--show-toplevel")


def parallel_registry_path() -> Path | None:
    common = git_resolved_path("--git-common-dir")
    return common / PARALLEL_REGISTRY_DIR / PARALLEL_REGISTRY_FILE if common else None


@contextlib.contextmanager
def parallel_registry_lock(path: Path, timeout_seconds: float = 3.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout_seconds
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > 15
            except OSError:
                stale = False
            if stale:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("parallel ticket registry is busy")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def load_parallel_registry(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["schema_version"] = 2
    if not isinstance(data.get("active_tickets"), dict):
        data["active_tickets"] = {}
    if not isinstance(data.get("completed_tickets"), dict):
        data["completed_tickets"] = {}
    return data


def active_ticket_at(worktree: Path) -> dict[str, Any]:
    path = worktree / CURRENT_TICKET
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) and data.get("status") == "ACTIVE" else {}


def prune_parallel_registry(registry: dict[str, Any]) -> bool:
    rows = registry.setdefault("active_tickets", {})
    changed = False
    for lane_id, row in list(rows.items()):
        if not isinstance(row, dict) or not row.get("worktree_root"):
            rows.pop(lane_id, None)
            changed = True
            continue
        active = active_ticket_at(Path(str(row["worktree_root"])))
        reserved_at = str(row.get("reserved_at") or "")
        reservation_is_fresh = False
        if reserved_at:
            try:
                reservation_is_fresh = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(reserved_at)).total_seconds() < 30
            except (TypeError, ValueError):
                reservation_is_fresh = False
        if not active and reservation_is_fresh:
            # start reserves the shared lane immediately before writing the
            # worktree-local current ticket. Keep that tiny race window alive.
            continue
        if (
            not active
            or active.get("ticket_id") != row.get("ticket_id")
            or active.get("run_id") != row.get("run_id")
        ):
            rows.pop(lane_id, None)
            changed = True
    return changed


def write_parallel_registry(path: Path, registry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at"] = now()
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def static_pattern_prefix(pattern: str) -> str:
    value = norm(pattern)
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    parts: list[str] = []
    for part in value.split("/"):
        if any(char in part for char in "*?["):
            break
        if part:
            parts.append(part)
    return "/".join(parts)


def writable_scopes_overlap(left: list[str], right: list[str]) -> list[tuple[str, str]]:
    overlaps: list[tuple[str, str]] = []
    for first in left:
        first_prefix = static_pattern_prefix(str(first))
        for second in right:
            second_prefix = static_pattern_prefix(str(second))
            if (
                not first_prefix
                or not second_prefix
                or first_prefix == second_prefix
                or first_prefix.startswith(second_prefix + "/")
                or second_prefix.startswith(first_prefix + "/")
            ):
                overlaps.append((str(first), str(second)))
    return overlaps


def parallel_lane_entry(ticket: dict[str, Any]) -> dict[str, Any] | None:
    root = git_worktree_root()
    if not root:
        return None
    relation = execution_relationship(ticket)
    contract = coordination_contract_summary(ticket)
    return {
        "lane_id": str(ticket.get("run_id") or ""),
        "ticket_id": ticket.get("ticket_id"),
        "run_id": ticket.get("run_id"),
        "worktree_root": str(root),
        "writable_paths": ticket_writable_paths(ticket),
        "relationship_mode": relation["mode"],
        "depends_on": relation["depends_on"],
        "produces_contracts": relation["produces_contracts"],
        "consumes_contracts": relation["consumes_contracts"],
        "coordination_contract_id": contract.get("contract_id"),
        "coordination_contract_sha256": contract.get("sha256"),
        "efficiency": contract.get("efficiency"),
        "task_goal": str(ticket.get("task_goal") or "")[:300],
        "started_at": ticket.get("budget_used", {}).get("started_at") or now(),
        "reserved_at": now(),
    }


def reserve_parallel_lane(ticket: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    path = parallel_registry_path()
    entry = parallel_lane_entry(ticket)
    if path is None or entry is None:
        return True, [], {"mode": "single_workspace", "active_ticket_count": 1}
    try:
        with parallel_registry_lock(path):
            registry = load_parallel_registry(path)
            prune_parallel_registry(registry)
            conflicts: list[str] = []
            active_rows = list(registry["active_tickets"].values())
            completed = registry.get("completed_tickets", {})
            active_by_ticket = {str(row.get("ticket_id")): row for row in active_rows}

            for dependency in entry["depends_on"]:
                if dependency in active_by_ticket:
                    conflicts.append(f"ticket depends on ACTIVE ticket {dependency}; dependent tickets must run serially")
                    continue
                completion = completed.get(dependency, {}) if isinstance(completed, dict) else {}
                if not isinstance(completion, dict) or completion.get("status") != "PASS":
                    conflicts.append(f"dependency {dependency} has no recorded PASS; run this ticket after its predecessor closes")

            for row in active_rows:
                row_ticket_id = str(row.get("ticket_id") or "")
                if row.get("worktree_root") == entry["worktree_root"]:
                    conflicts.append(f"worktree already owns ACTIVE ticket {row_ticket_id}")
                if row_ticket_id == entry["ticket_id"]:
                    conflicts.append(f"ticket_id already ACTIVE in {row.get('worktree_root')}")
                if entry["relationship_mode"] != "PARALLEL" or str(row.get("relationship_mode")) != "PARALLEL":
                    conflicts.append(f"ACTIVE ticket {row_ticket_id} is not in the same declared parallel wave; run serially")
                if entry.get("coordination_contract_id") != row.get("coordination_contract_id"):
                    conflicts.append(f"coordination contract differs from ACTIVE ticket {row_ticket_id}")
                if entry.get("coordination_contract_sha256") != row.get("coordination_contract_sha256"):
                    conflicts.append(f"coordination contract version differs from ACTIVE ticket {row_ticket_id}")
                if row_ticket_id in entry["depends_on"] or entry["ticket_id"] in list(row.get("depends_on", [])):
                    conflicts.append(f"dependency edge exists between {entry['ticket_id']} and {row_ticket_id}; run serially")

                current_produces = set(entry["produces_contracts"])
                current_consumes = set(entry["consumes_contracts"])
                row_produces = set(row.get("produces_contracts", []))
                row_consumes = set(row.get("consumes_contracts", []))
                if current_consumes & row_produces or current_produces & row_consumes:
                    conflicts.append(f"producer/consumer dependency exists between {entry['ticket_id']} and {row_ticket_id}; run serially")
                duplicate_producers = sorted(current_produces & row_produces)
                if duplicate_producers:
                    conflicts.append(f"tickets share contract ownership with {row_ticket_id}: {', '.join(duplicate_producers)}")

                overlap = writable_scopes_overlap(entry["writable_paths"], list(row.get("writable_paths", [])))
                if overlap:
                    preview = ", ".join(f"{a} <-> {b}" for a, b in overlap[:4])
                    conflicts.append(f"writable scope overlaps ACTIVE ticket {row_ticket_id}: {preview}")
            if conflicts:
                write_parallel_registry(path, registry)
                return False, list(dict.fromkeys(conflicts)), {
                    "mode": "git_worktree_lanes",
                    "active_ticket_count": len(registry["active_tickets"]),
                    "relationship_mode": entry["relationship_mode"],
                    "coordination_contract_id": entry.get("coordination_contract_id"),
                }
            registry["active_tickets"][entry["lane_id"]] = entry
            write_parallel_registry(path, registry)
            return True, [], {
                "mode": "git_worktree_lanes",
                "active_ticket_count": len(registry["active_tickets"]),
                "lane_id": entry["lane_id"],
                "worktree_root": entry["worktree_root"],
                "relationship_mode": entry["relationship_mode"],
                "coordination_contract_id": entry.get("coordination_contract_id"),
                "efficiency": entry.get("efficiency"),
            }
    except RuntimeError as exc:
        return False, [str(exc)], {"mode": "git_worktree_lanes", "active_ticket_count": None}


def unregister_parallel_lane(ticket: dict[str, Any]) -> None:
    path = parallel_registry_path()
    if path is None or not path.exists():
        return
    try:
        with parallel_registry_lock(path):
            registry = load_parallel_registry(path)
            rows = registry["active_tickets"]
            removed = False
            for lane_id, row in list(rows.items()):
                if (
                    row.get("run_id") == ticket.get("run_id")
                    or (
                        row.get("ticket_id") == ticket.get("ticket_id")
                        and row.get("worktree_root") == str(git_worktree_root() or "")
                    )
                ):
                    rows.pop(lane_id, None)
                    removed = True
            if removed and ticket.get("ticket_id") and ticket.get("status") in TERMINAL_TICKET_STATUSES:
                completed = registry.setdefault("completed_tickets", {})
                completed[str(ticket["ticket_id"])] = {
                    "ticket_id": ticket.get("ticket_id"),
                    "status": ticket.get("status"),
                    "closed_at": ticket.get("closed_at") or ticket.get("aborted_at") or now(),
                    "produces_contracts": execution_relationship(ticket)["produces_contracts"],
                    "coordination_contract_id": coordination_contract_ref(ticket).get("contract_id"),
                }
                if len(completed) > 256:
                    ordered = sorted(completed.items(), key=lambda item: str(item[1].get("closed_at") or ""))
                    for old_ticket_id, _ in ordered[:-256]:
                        completed.pop(old_ticket_id, None)
            prune_parallel_registry(registry)
            write_parallel_registry(path, registry)
    except RuntimeError:
        return


def parallel_execution_summary(ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    path = parallel_registry_path()
    root = git_worktree_root()
    if path is None or root is None:
        return {
            "mode": "single_workspace",
            "active_ticket_count": 1 if ticket and ticket.get("status") == "ACTIVE" else 0,
            "rule": "one ACTIVE ticket per workspace",
        }
    try:
        with parallel_registry_lock(path):
            registry = load_parallel_registry(path)
            changed = prune_parallel_registry(registry)
            if changed:
                write_parallel_registry(path, registry)
            rows = list(registry["active_tickets"].values())
    except RuntimeError:
        rows = []
    return {
        "mode": "git_worktree_lanes",
        "active_ticket_count": len(rows),
        "current_worktree": str(root),
        "current_ticket_id": ticket.get("ticket_id") if ticket else None,
        "relationship": execution_relationship(ticket) if ticket else None,
        "coordination_contract": coordination_contract_summary(ticket) if ticket else None,
        "other_lanes": [
            {
                "ticket_id": row.get("ticket_id"),
                "worktree_root": row.get("worktree_root"),
                "writable_paths": row.get("writable_paths", []),
                "relationship_mode": row.get("relationship_mode"),
                "coordination_contract_id": row.get("coordination_contract_id"),
            }
            for row in rows
            if row.get("worktree_root") != str(root)
        ],
        "rule": "parallel tickets require positive net gain, no dependency edge, one frozen coordination contract, separate worktrees, and disjoint writable paths",
    }


def git_changed_files() -> list[str]:
    proc = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if proc.returncode != 0:
        return []
    out: list[str] = []
    parts = [p for p in proc.stdout.split("\0") if p]
    i = 0
    while i < len(parts):
        item = parts[i]
        status = item[:2]
        path = item[3:] if len(item) > 3 else item
        out.append(norm(path))
        if status.strip().startswith(("R", "C")):
            i += 1
        i += 1
    return sorted({p for p in out if p and not is_generated(p)})


def git_diff_lines(files: list[str] | None = None) -> int:
    total = 0
    included = {norm(path) for path in files} if files is not None else None
    for args in (["git", "diff", "--numstat"], ["git", "diff", "--cached", "--numstat"]):
        proc = run(args)
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            add, delete, path = cols[0], cols[1], norm(cols[2])
            if is_generated(path) or (included is not None and path not in included):
                continue
            if add.isdigit():
                total += int(add)
            if delete.isdigit():
                total += int(delete)
    for path in git_changed_files():
        if is_generated(path) or (included is not None and path not in included):
            continue
        if Path(path).exists() and run(["git", "ls-files", "--error-unmatch", path]).returncode != 0:
            if is_text_artifact(Path(path)):
                total += min(line_count(Path(path)), 1000)
    return total


def line_count(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def explicit_tracking_patterns(ticket: dict[str, Any]) -> list[str]:
    acc = ticket.get("acceptance", {}) if isinstance(ticket.get("acceptance"), dict) else {}
    patterns = [
        *ticket_writable_paths(ticket),
        *ticket_read_dependencies(ticket),
        *ticket_immutable_paths(ticket),
        *ticket_runtime_paths(ticket),
        *acc.get("files_exist", []),
    ]
    for item in acc.get("contains", []):
        if isinstance(item, str) and "::" in item:
            patterns.append(item.split("::", 1)[0])
        elif isinstance(item, dict) and item.get("file"):
            patterns.append(str(item["file"]))
    for item in acc.get("assertions", []):
        if isinstance(item, dict) and (item.get("file") or item.get("path")):
            patterns.append(str(item.get("file") or item.get("path")))
    return [norm(str(pattern)) for pattern in patterns if str(pattern)]


def tracking_path_explicit(path: str, ticket: dict[str, Any]) -> bool:
    return match_path(path, explicit_tracking_patterns(ticket))


def matched_tracking_ignore_roots(path: str) -> set[str]:
    p = norm(path)
    path_parts = Path(p).parts
    roots: set[str] = set()
    for raw in TRACKING_IGNORE_PATTERNS:
        pattern = norm(raw)
        if not match_path(p, [pattern]):
            continue
        root_parts: list[str] = []
        for index, part in enumerate(Path(pattern).parts):
            if part == "**" or index >= len(path_parts):
                break
            root_parts.append(path_parts[index])
        if root_parts:
            roots.add(norm(str(Path(*root_parts))))
    return roots


def explicit_tracking_roots(ticket: dict[str, Any]) -> set[str]:
    roots: set[str] = set()
    for pattern in explicit_tracking_patterns(ticket):
        roots.update(matched_tracking_ignore_roots(pattern))
    return roots - HARD_TRACKING_IGNORE_ROOTS


def sparse_tracking_ignore_hit(ignored_roots: set[str]) -> bool:
    return bool(ignored_roots & SPARSE_EXPLICIT_TRACKING_ROOTS)


def tracking_top_roots(ticket: dict[str, Any]) -> set[str]:
    roots: set[str] = set()
    for pattern in explicit_tracking_patterns(ticket):
        parts = Path(norm(pattern)).parts
        if parts:
            roots.add(parts[0])
    return roots


def current_top_level_entries() -> list[str]:
    try:
        return sorted(path.name for path in Path(".").iterdir() if not is_generated(path.name))
    except OSError:
        return []


def outside_non_git_tracking_surface(path: str, ticket: dict[str, Any]) -> bool:
    usage = ticket.get("budget_used", {}) if isinstance(ticket.get("budget_used"), dict) else {}
    baseline_roots = set(str(value) for value in usage.get("baseline_top_level_entries", []))
    if not baseline_roots:
        return False
    parts = Path(norm(path)).parts
    if not parts:
        return False
    top = parts[0]
    return top not in tracking_top_roots(ticket) and top in baseline_roots


def tracking_path_ignored(path: str, ticket: dict[str, Any]) -> bool:
    p = norm(path)
    if is_generated(p):
        return True
    if outside_non_git_tracking_surface(p, ticket):
        return True
    if any(part in RUNTIME_CACHE_DIR_NAMES for part in Path(p).parts):
        return True
    if volatile_path(p, ticket):
        return True
    ignored_roots = matched_tracking_ignore_roots(p)
    if not ignored_roots:
        return False
    if ignored_roots & HARD_TRACKING_IGNORE_ROOTS:
        return True
    if sparse_tracking_ignore_hit(ignored_roots):
        return not tracking_path_explicit(p, ticket)
    return not bool(ignored_roots & explicit_tracking_roots(ticket))


def pattern_reaches_directory(pattern: str, directory: str) -> bool:
    pat = norm(pattern)
    directory = norm(directory)
    if not pat or pat.startswith("__outside_repo__/"):
        return False
    wildcard_at = min((pat.find(mark) for mark in ("*", "?", "[") if mark in pat), default=len(pat))
    fixed = pat[:wildcard_at].rstrip("/")
    if not fixed:
        return True
    return fixed == directory or fixed.startswith(directory + "/") or directory.startswith(fixed + "/")


def tracking_directory_ignored(directory: str, ticket: dict[str, Any]) -> bool:
    if Path(norm(directory)).name in RUNTIME_CACHE_DIR_NAMES:
        return True
    if outside_non_git_tracking_surface(directory, ticket):
        return True
    ignored_roots = matched_tracking_ignore_roots(directory)
    if not ignored_roots:
        return False
    if ignored_roots & HARD_TRACKING_IGNORE_ROOTS:
        return True
    if sparse_tracking_ignore_hit(ignored_roots):
        return not any(
            pattern_reaches_directory(pattern, directory)
            for pattern in explicit_tracking_patterns(ticket)
        )
    return not bool(ignored_roots & explicit_tracking_roots(ticket))


def volatile_patterns(ticket: dict[str, Any]) -> list[str]:
    custom = ticket.get("volatile_paths", []) if isinstance(ticket.get("volatile_paths"), list) else []
    return [*DEFAULT_VOLATILE_PATTERNS, *[str(value) for value in custom if str(value).strip()]]


def volatile_path(path: str, ticket: dict[str, Any]) -> bool:
    return match_path(path, volatile_patterns(ticket))


def file_snapshot_meta(path: Path) -> dict[str, Any]:
    st = path.stat()
    size = st.st_size
    if size <= 16 * 1024 * 1024:
        meta: dict[str, Any] = {"size": size, "sha256": sha256_file_contents(path)}
        if size <= 2 * 1024 * 1024:
            try:
                raw = path.read_bytes()
                if b"\0" not in raw[:65536]:
                    lines = raw.splitlines(keepends=True)
                    meta["line_count"] = len(lines)
                    if len(lines) <= 20000:
                        meta["line_hashes"] = [hashlib.sha256(line).hexdigest()[:16] for line in lines]
            except OSError:
                pass
        return meta
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(128 * 1024))
        if size > 128 * 1024:
            handle.seek(max(0, size - 128 * 1024))
            digest.update(handle.read(128 * 1024))
    return {"size": size, "sample_sha256": digest.hexdigest()}


def snapshot(ticket: dict[str, Any], *, only_volatile: bool = False) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for root, dirs, files in os.walk("."):
        kept_dirs = []
        for directory in dirs:
            candidate = norm(str(Path(root) / directory))
            if is_generated(candidate) or Path(candidate).name in RUNTIME_CACHE_DIR_NAMES:
                continue
            if only_volatile and directory in SCAN_SKIP_DIR_NAMES:
                continue
            if not only_volatile and tracking_directory_ignored(candidate, ticket):
                continue
            kept_dirs.append(directory)
        dirs[:] = kept_dirs
        for name in files:
            path = Path(root) / name
            p = norm(str(path))
            is_volatile = volatile_path(p, ticket)
            if only_volatile and not is_volatile:
                continue
            if not only_volatile and tracking_path_ignored(p, ticket):
                continue
            try:
                result[p] = file_snapshot_meta(path)
            except OSError:
                continue
    return result


def baseline_ref_path(ticket: dict[str, Any]) -> Path | None:
    raw = ticket.get("budget_used", {}).get("baseline_ref")
    return Path(str(raw)) if raw else None


def persist_baseline(ticket: dict[str, Any], baseline: dict[str, dict[str, Any]]) -> None:
    usage = ticket.setdefault("budget_used", {})
    run_id = str(ticket.get("run_id") or uuid.uuid4().hex)
    ticket["run_id"] = run_id
    target = BASELINES / f"{run_id}.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(baseline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(target, "wb", compresslevel=6) as handle:
        handle.write(raw)
    usage["baseline_ref"] = norm(str(target))
    usage["baseline_sha256"] = sha256_bytes(raw)
    usage["baseline_entry_count"] = len(baseline)


def load_baseline(ticket: dict[str, Any]) -> dict[str, dict[str, Any]]:
    usage = ticket.setdefault("budget_used", {})
    legacy = usage.get("baseline_snapshot")
    if isinstance(legacy, dict):
        return legacy
    path = baseline_ref_path(ticket)
    if not path or not path.is_file():
        return {}
    try:
        with gzip.open(path, "rb") as handle:
            raw = handle.read()
        expected = usage.get("baseline_sha256")
        if expected and sha256_bytes(raw) != expected:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def cleanup_baseline(ticket: dict[str, Any]) -> None:
    path = baseline_ref_path(ticket)
    if path and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def nongit_changed_files(ticket: dict[str, Any]) -> list[str]:
    usage = ticket.setdefault("budget_used", {})
    raw_base = load_baseline(ticket)
    base = {path: meta for path, meta in raw_base.items() if not tracking_path_ignored(path, ticket)}
    current = snapshot(ticket)
    changed = []
    for path, meta in current.items():
        if path not in base or base[path] != meta:
            changed.append(path)
    for path in base:
        if path not in current:
            changed.append(path)
    return sorted({p for p in changed if p and not is_generated(p)})


def changed_files(ticket: dict[str, Any]) -> list[str]:
    return git_changed_files() if is_git_repo() else nongit_changed_files(ticket)


def diff_lines(ticket: dict[str, Any], files: list[str]) -> int:
    if is_git_repo():
        return git_diff_lines(files)
    baseline = load_baseline(ticket)
    current = snapshot(ticket)
    total = 0
    for path in files:
        if is_generated(path):
            continue
        before = baseline.get(path)
        after = current.get(path)
        if before is None and after is not None:
            if "line_count" in after:
                total += min(int(after.get("line_count", 0) or 0), 1000)
            continue
        if after is None and before is not None:
            if "line_count" in before:
                total += min(int(before.get("line_count", 0) or 0), 1000)
            continue
        if not before or not after or before == after:
            continue
        old_lines = before.get("line_hashes")
        new_lines = after.get("line_hashes")
        if isinstance(old_lines, list) and isinstance(new_lines, list):
            matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
            for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
                if tag != "equal":
                    total += (old_end - old_start) + (new_end - new_start)
            continue
        if "line_count" in before or "line_count" in after:
            size_delta = abs(int(after.get("size", 0) or 0) - int(before.get("size", 0) or 0))
            total += min(1000, max(1, (size_delta + 79) // 80))
    return total


def update_usage(ticket: dict[str, Any]) -> dict[str, Any]:
    usage = ticket.setdefault("budget_used", {})
    hook_summary = cached_hook_event_summary(ticket)
    if not hook_summary.get("complete"):
        hook_summary = hook_event_summary(ticket)
    if hook_summary["post_events"]:
        usage["tool_calls"] = hook_summary["post_events"]
        usage["tool_calls_by_type"] = hook_summary["tool_calls_by_type"]
        usage["budget_enforcement"] = "CONNECTED_VERIFIED"
        usage["hook_heartbeat_at"] = hook_summary.get("last_heartbeat_at")
    observed = changed_files(ticket)
    roles = {path: path_contract_role(path, ticket) for path in observed}
    product_files = sorted(path for path, role in roles.items() if role in {"writable", "outside"})
    immutable_changes = sorted(path for path, role in roles.items() if role == "immutable")
    runtime_changes = sorted(path for path, role in roles.items() if role == "runtime")
    upstream_changes = sorted(path for path, role in roles.items() if role == "read_dependency")
    usage["observed_changes"] = observed
    usage["changed_files"] = product_files
    usage["immutable_changes"] = immutable_changes
    usage["runtime_changes"] = runtime_changes
    usage["upstream_evidence_changes"] = upstream_changes
    usage["diff_lines"] = diff_lines(ticket, product_files)
    usage["changed_files_count"] = len(product_files)
    binary_files = []
    binary_bytes = 0
    for path in product_files:
        candidate = Path(path)
        try:
            if candidate.is_file() and not is_text_artifact(candidate):
                binary_files.append(path)
                binary_bytes += candidate.stat().st_size
        except OSError:
            continue
    usage["binary_artifacts_changed"] = sorted(binary_files)
    usage["binary_bytes_observed"] = binary_bytes
    if not is_git_repo():
        baseline_volatile = usage.get("baseline_volatile_snapshot", {})
        current_volatile = snapshot(ticket, only_volatile=True)
        volatile_changes = sorted({
            *[path for path, meta in current_volatile.items() if baseline_volatile.get(path) != meta],
            *[path for path in baseline_volatile if path not in current_volatile],
        })
        usage["volatile_runtime_changes"] = volatile_changes
    now_dt = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    previous_marker = usage.get("last_metered_at")
    if previous_marker:
        try:
            previous_dt = dt.datetime.fromisoformat(previous_marker)
            delta_minutes = max(0.0, (now_dt - previous_dt).total_seconds() / 60)
            counted_minutes = min(delta_minutes, ACTIVE_BUDGET_IDLE_GAP_MINUTES)
            usage["elapsed_minutes"] = round(float(usage.get("elapsed_minutes", 0)) + counted_minutes, 2)
        except (TypeError, ValueError):
            usage["elapsed_minutes"] = round(float(usage.get("elapsed_minutes", 0) or 0), 2)
    else:
        # Legacy tickets may resume hours after a system pause. Start metering
        # from this interaction instead of preserving a wall-clock value that
        # may already include overnight/system-error idle time.
        existing_minutes = max(0.0, float(usage.get("elapsed_minutes", 0) or 0))
        usage["elapsed_minutes"] = round(min(existing_minutes, ACTIVE_BUDGET_IDLE_GAP_MINUTES), 2)
    usage["last_metered_at"] = now_dt.isoformat()
    started = usage.get("started_at")
    if started:
        try:
            started_dt = dt.datetime.fromisoformat(str(started))
            usage["wall_clock_minutes"] = round(max(0.0, (now_dt - started_dt).total_seconds() / 60), 2)
        except (TypeError, ValueError):
            usage["wall_clock_minutes"] = None
    return ticket


def path_required_by_validation(path: str, ticket: dict[str, Any]) -> bool:
    p = norm(path)
    return p in acceptance_positive_paths(ticket)


def path_maps_to_acceptance(path: str, ticket: dict[str, Any]) -> bool:
    p = norm(path)
    if path_required_by_validation(p, ticket):
        return True
    return False


def artifact_body(path: str) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file() or p.stat().st_size > 160000:
        return ""
    if not is_text_artifact(p):
        return ""
    return read_text(p, 30000)


def artifact_text(path: str) -> str:
    return path + "\n" + artifact_body(path)


def maps_to_main_path(text: str, north: dict[str, Any]) -> bool:
    return bool(strong_term_hits(text, north.get("main_path", [])))


def strong_current_scope_mapping(text: str, north: dict[str, Any]) -> bool:
    low = canonical_text(text)
    hits = strong_term_hits(text, north.get("main_path", []))
    exact_hits = [
        item
        for item in north.get("main_path", [])
        if canonical_text(str(item)).strip() and canonical_text(str(item)).strip() in low
    ]
    return bool(exact_hits) or len(hits) >= 2


def maps_to_north_star(text: str, north_context: dict[str, Any] | None = None) -> bool:
    north = north_context if isinstance(north_context, dict) else north_star()
    if not north.get("confirmed"):
        return False
    if maps_to_main_path(text, north):
        return True
    if strong_term_hits(text, north.get("allowed_subgoals", [])):
        return True
    return False


def maps_to_ticket_must_do(text: str, ticket: dict[str, Any]) -> bool:
    # Protection needs the whole task anchor, not two coincidental domain words.
    # Loose overlap is useful for request routing but unsafe for Janitor KEEP.
    return bool(strong_term_hits(text, ticket.get("must_do", [])))


def maps_to_ticket_acceptance_text(text: str, ticket: dict[str, Any]) -> bool:
    acc = ticket.get("acceptance", {})
    acceptance_text = json.dumps({
        "files_exist": acc.get("files_exist", []),
        "commands_pass": acc.get("commands_pass", []),
        "contains": acc.get("contains", []),
        "assertions": acc.get("assertions", []),
    }, ensure_ascii=False)
    words = text_words(acceptance_text) - WEAK_PROTECT_WORDS
    req_words = text_words(text) - WEAK_PROTECT_WORDS
    return len(words & req_words) >= 2


def core_path_match(path: str, north: dict[str, Any]) -> bool:
    return match_path(path, [str(p) for p in north.get("core_path_patterns", [])])


def existing_core_flow(
    path: str,
    text: str,
    ticket: dict[str, Any],
    north_context: dict[str, Any] | None = None,
) -> bool:
    p = norm(path)
    allowed = match_path(p, ticket_writable_paths(ticket))
    if not allowed:
        return False
    if path_required_by_validation(p, ticket):
        return True
    north = north_context if isinstance(north_context, dict) else north_star()
    if core_path_match(p, north):
        return True
    return maps_to_ticket_must_do(text, ticket) or maps_to_north_star(text, north)


def anti_pattern_hits(
    text: str,
    ticket: dict[str, Any],
    north_context: dict[str, Any] | None = None,
) -> list[str]:
    north = north_context if isinstance(north_context, dict) else north_star()
    hits = term_hits(text, [*ticket.get("anti_patterns", []), *north.get("anti_goals", [])])
    return filter_negated_scope_hits(hits, text)


def future_scope_hits(
    text: str,
    ticket: dict[str, Any],
    north_context: dict[str, Any] | None = None,
) -> list[str]:
    north = north_context if isinstance(north_context, dict) else north_star()
    hits = term_hits(text, ticket.get("backlog_only", [])) + term_hits(text, north.get("backlog_domains", []))
    return filter_contextual_scope_hits(hits, text, ticket)


def dependency_file(path: str) -> bool:
    return Path(norm(path)).name in DEPENDENCY_FILES


def runtime_evidence_file(path: str, ticket: dict[str, Any]) -> bool:
    p = norm(path)
    return match_path(p, RUNTIME_EVIDENCE_PATTERNS) and (
        match_path(p, ticket_writable_paths(ticket)) or path_required_by_validation(p, ticket)
    )


def kind_for_path(path: str) -> str:
    p = norm(path)
    if "/tests/" in f"/{p}" or p.startswith("tests/") or p.endswith(".test.ts") or p.endswith("_test.py"):
        return "test"
    if p.endswith((".md", ".mdx", ".txt")):
        return "doc_section"
    if p.endswith((".json", ".toml", ".yaml", ".yml", ".ini", ".env")):
        return "config"
    if p.endswith((".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs")):
        return "file"
    return "file"


def cache_artifact_path(path: str) -> bool:
    p = norm(path).lower()
    parts = Path(p).parts
    return any(part in {".cache", "cache", "render_cache", "forecast_cache"} or part.endswith("_cache") for part in parts)


def obvious_generated_noise(path: str, body: str) -> bool:
    low = canonical_text(body)
    if "autogenerated" in low and any(marker in low for marker in ["stale", "goal.md parsed: false", "validation manifest parsed: false", "low confidence"]):
        return True
    keyword_markers = ["关键词堆积", "keyword recall experiment", "keyword experiment"]
    provenance_gaps = ["no author", "没有作者", "未被任何", "not referenced", "unreferenced"]
    return any(marker in low for marker in keyword_markers) and any(marker in low for marker in provenance_gaps)


def uncertain_retention_artifact(path: str, body: str) -> bool:
    p = norm(path).lower()
    low = canonical_text(body)
    if p.startswith("imports/") or "/imports/" in f"/{p}":
        return True
    if p.endswith(".log") and any(part in Path(p).parts for part in ["failed", "staging", "review", "incidents", "audit_staging", "review_queue"]):
        return True
    return any(marker in low for marker in ["pending owner", "retention decision", "possible evidence", "ownership unknown"])


def future_backlog_document(path: str, body: str, backlog_hits: list[str]) -> bool:
    return kind_for_path(path) == "doc_section" and bool(backlog_hits)


def mixed_scope_document(path: str, body: str, maps_main: bool, backlog_scope: bool) -> bool:
    return kind_for_path(path) == "doc_section" and maps_main and backlog_scope


def classify_artifact(
    path: str,
    ticket: dict[str, Any],
    reference_counts: dict[str, int] | None = None,
    project_context: dict[str, Any] | None = None,
    north_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    north = north_context if isinstance(north_context, dict) else north_star()
    p_norm = norm(path)
    body = artifact_body(path)
    text = f"{path}\n{body}"
    combined = text
    required = path_required_by_validation(path, ticket)
    maps_main = maps_to_main_path(combined, north)
    maps_north = maps_to_north_star(combined, north)
    maps_must = maps_to_ticket_must_do(combined, ticket)
    maps_accept = path_maps_to_acceptance(path, ticket)
    core = existing_core_flow(path, combined, ticket, north)
    anti = anti_pattern_hits(combined, ticket, north)
    backlog = future_scope_hits(combined, ticket, north)
    heavy = filter_contextual_scope_hits(heavy_hits(combined), combined, ticket)
    changed_set = {
        norm(str(value))
        for value in [
            *ticket.get("budget_used", {}).get("changed_files", []),
            *ticket.get("budget_used", {}).get("immutable_changes", []),
        ]
    }
    files_not_changed_hit = (
        ticket.get("status") == "ACTIVE"
        and p_norm in changed_set
        and match_path(path, [str(p) for p in ticket.get("acceptance", {}).get("files_not_changed", [])])
    )
    core_path = core_path_match(path, north) and not anti and not backlog and not heavy
    reference_count = int((reference_counts or {}).get(norm(path), 0))
    project_context = project_context or {}
    manifest_path = p_norm in project_context.get("validation_manifests", set())
    manifest_required = p_norm in project_context.get("validation_required_paths", set())
    duplicate_shadow = p_norm in project_context.get("duplicate_shadow_paths", set())
    negative_candidates = list(dict.fromkeys([*anti, *backlog, *heavy]))
    path_negative = set(term_hits(path, negative_candidates))
    body_negative = set(term_hits(body, negative_candidates))
    negative_terms = {canonical_text(str(value)).strip() for value in [*anti, *backlog, *heavy] if str(value).strip()}
    strong_negative = len(negative_terms) >= 2 or bool(path_negative and body_negative)
    low = canonical_text(combined)

    base = {
        "artifact": p_norm,
        "kind": kind_for_path(path),
        "signals": [],
        "confidence": 0.5,
        "delete_safe": False,
        "goal_mapping": None,
        "north_star_mapping": None,
        "current_ticket_mapping": None,
        "evidence": [],
        "evidence_tier": "AMBIGUOUS",
        "janitor_action_limit": JANITOR_CAPABILITY_LEVEL,
    }
    if p_norm.lower() in NORTH_STAR_SOURCE_FILES and north.get("confirmed"):
        return {**base, "classification": "PROTECTED", "suggested_classification": "PROTECTED", "confidence": 1.0, "reason": "Confirmed North Star source file is always protected.", "goal_mapping": "north_star_source", "north_star_mapping": north.get("goal"), "evidence": ["confirmed_north_star_source"], "evidence_tier": "EXACT_CONTRACT"}
    if required or maps_accept:
        return {**base, "classification": "PROTECTED", "suggested_classification": "PROTECTED", "confidence": 0.97, "reason": "Validation or acceptance requires this artifact.", "current_ticket_mapping": "acceptance", "evidence": ["required_by_validation_or_acceptance"], "evidence_tier": "EXACT_CONTRACT"}
    if manifest_path:
        return {**base, "classification": "PROTECTED", "suggested_classification": "PROTECTED", "confidence": 0.98, "reason": "Structured validation manifest defines exact required paths and post-cleanup checks.", "current_ticket_mapping": "validation_manifest", "evidence": ["structured_validation_manifest"], "evidence_tier": "EXACT_VALIDATION_GRAPH"}
    if manifest_required:
        if protected_boundary_path(p_norm):
            return {**base, "classification": "PROTECTED", "suggested_classification": "PROTECTED", "confidence": 0.96, "reason": "Validation manifest requires this explicit policy, constraint, or safety boundary.", "current_ticket_mapping": "validation_boundary", "evidence": ["validation_required_path", "protected_boundary"], "evidence_tier": "EXACT_VALIDATION_GRAPH"}
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.94, "reason": "Validation manifest requires this exact path.", "current_ticket_mapping": "validation_required_path", "evidence": ["validation_required_path"], "evidence_tier": "EXACT_VALIDATION_GRAPH"}
    if files_not_changed_hit:
        return {**base, "classification": "QUARANTINE_CANDIDATE", "suggested_classification": "QUARANTINE_CANDIDATE", "signals": ["files_not_changed_violation"], "confidence": 0.8, "reason": "Changed artifact matches files_not_changed; mark for quarantine review, never delete.", "north_star_mapping": None, "evidence": ["files_not_changed"], "evidence_tier": "EXACT_CONTRACT_VIOLATION"}
    if duplicate_shadow:
        return {**base, "classification": "QUARANTINE_CANDIDATE", "suggested_classification": "QUARANTINE_CANDIDATE", "signals": ["unreferenced_duplicate"], "confidence": 0.92, "reason": "Artifact duplicates an exact validation/reference path but has no trusted consumer of its own.", "north_star_mapping": None, "evidence": ["same_hash_as_required_or_referenced_path", "no_trusted_reference"], "evidence_tier": "TWO_SOURCE_NEGATIVE"}
    if obvious_generated_noise(p_norm, body):
        return {**base, "classification": "QUARANTINE_CANDIDATE", "suggested_classification": "QUARANTINE_CANDIDATE", "signals": ["generated_noise"], "confidence": 0.9, "reason": "Artifact self-identifies as stale detector output or an unreferenced keyword experiment.", "north_star_mapping": None, "evidence": ["generated_or_keyword_noise", "no_authoritative_consumer"], "evidence_tier": "TWO_SOURCE_NEGATIVE"}
    if cache_artifact_path(p_norm) and reference_count == 0:
        return {**base, "classification": "QUARANTINE_CANDIDATE", "suggested_classification": "QUARANTINE_CANDIDATE", "signals": ["cache_artifact"], "confidence": 0.88, "reason": "Unreferenced cache artifact is rebuildable and may be marked for reversible quarantine.", "north_star_mapping": None, "evidence": ["cache_path", "no_trusted_reference"], "evidence_tier": "TWO_SOURCE_NEGATIVE"}
    if uncertain_retention_artifact(p_norm, body) and reference_count == 0:
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["retention_or_owner_unknown"], "confidence": 0.4, "reason": "Imported or failed-run evidence has no confirmed owner or retention policy.", "north_star_mapping": None, "evidence": ["unknown_owner_or_retention"], "evidence_tier": "CONFLICTING_EVIDENCE"}
    if p_norm.lower() in PROJECT_ANCHOR_FILES and (anti or backlog or heavy):
        evidence = [*anti, *backlog, *heavy][:6]
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["project_anchor_with_negative_language"], "confidence": 0.48, "reason": "Project anchor mixes current product evidence with non-goal language; preserve and review.", "north_star_mapping": north.get("goal") if north.get("confirmed") else None, "evidence": evidence, "evidence_tier": "CONFLICTING_EVIDENCE"}
    if mixed_scope_document(p_norm, body, strong_current_scope_mapping(body, north), bool(backlog)):
        return {**base, "classification": "SIMPLIFY", "suggested_classification": "SIMPLIFY_CANDIDATE", "signals": ["mixed_current_and_future_scope"], "confidence": 0.86, "reason": "Document mixes useful current-flow material with unapproved future expansion; simplify without deleting the current evidence.", "north_star_mapping": north.get("goal") if north.get("confirmed") else None, "evidence": ["current_flow_content", "future_scope_content"], "evidence_tier": "CONFLICTING_EVIDENCE"}
    if future_backlog_document(p_norm, body, backlog):
        return {**base, "classification": "BACKLOG_CANDIDATE", "suggested_classification": "BACKLOG_CANDIDATE", "signals": ["explicit_future_scope"], "confidence": 0.88, "reason": "Document explicitly describes a later or unapproved phase that should remain outside the current delivery.", "north_star_mapping": "future_or_rejected_scope", "evidence": backlog[:4] or ["explicit_future_scope"], "evidence_tier": "EXPLICIT_PHASE_BOUNDARY"}
    if backlog and reference_count == 0:
        return {**base, "classification": "BACKLOG_CANDIDATE", "suggested_classification": "BACKLOG_CANDIDATE", "signals": ["future_scope_implementation"], "confidence": 0.72, "reason": "Artifact maps to an explicit later-phase domain and has no trusted current consumer.", "north_star_mapping": "future_or_rejected_scope", "evidence": backlog[:4], "evidence_tier": "EXPLICIT_PHASE_BOUNDARY"}
    if path_negative and not body_negative and reference_count and match_path(p_norm, ticket_writable_paths(ticket)) and maps_to_north_star(body):
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "signals": ["suspicious_name_but_positive_body", "referenced_by_project"], "confidence": 0.76, "reason": "Suspicious naming is outweighed by a strong North Star body mapping, current allowed scope, and a live project reference.", "goal_mapping": "north_star_with_reference", "north_star_mapping": north.get("goal") if north.get("confirmed") else None, "current_ticket_mapping": "existing_reference", "evidence": [*sorted(path_negative)[:3], f"referenced_by_project:{reference_count}"], "evidence_tier": "THREE_SOURCE_POSITIVE"}
    if (anti or backlog or heavy) and reference_count:
        evidence = [*anti, *backlog, *heavy][:4]
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["negative_scope", "referenced_by_project"], "confidence": 0.45, "reason": "Negative-scope terms conflict with live project references; human review is required.", "north_star_mapping": None, "evidence": [*evidence, f"referenced_by_project:{reference_count}"], "evidence_tier": "CONFLICTING_EVIDENCE"}
    if (anti or backlog or heavy) and strong_negative:
        evidence = [*anti, *backlog, *heavy][:6]
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["negative_scope_without_disposability_evidence"], "confidence": 0.46, "reason": "Negative-scope language alone does not prove that an artifact is disposable; review is required.", "north_star_mapping": None, "evidence": evidence, "evidence_tier": "CONFLICTING_EVIDENCE"}
    if anti or backlog or heavy:
        evidence = [*anti, *backlog, *heavy][:4]
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["single_negative_signal"], "confidence": 0.5, "reason": "A single negative-scope signal is insufficient for quarantine.", "north_star_mapping": None, "evidence": evidence, "evidence_tier": "SINGLE_SIGNAL"}
    if dependency_file(path) and match_path(path, ticket_writable_paths(ticket)):
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.7, "reason": "Dependency manifest is inside the current allowed project surface.", "current_ticket_mapping": "dependency_manifest", "evidence": ["dependency_file"]}
    if runtime_evidence_file(path, ticket):
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.66, "reason": "Runtime artifact is allowed evidence/storage for the current ticket.", "current_ticket_mapping": "runtime_evidence", "evidence": ["allowed_runtime_evidence"]}
    if maps_must or core:
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.78, "reason": "Artifact serves current ticket must_do or existing core flow.", "current_ticket_mapping": "must_do_or_core_flow", "evidence": ["maps_to_current_ticket"]}
    if core_path:
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.74, "reason": "Artifact is on a North Star core path and does not hit anti-patterns.", "goal_mapping": "core_path", "north_star_mapping": north.get("goal") if north.get("confirmed") else None, "evidence": ["core_path_patterns"]}
    if p_norm.lower() in PROJECT_ANCHOR_FILES and (maps_main or maps_north):
        return {**base, "classification": "PROTECTED", "suggested_classification": "PROTECTED", "confidence": 0.9, "reason": "Project anchor document maps to the confirmed North Star without negative-scope evidence.", "goal_mapping": "project_anchor", "north_star_mapping": north.get("goal") if north.get("confirmed") else None, "evidence": ["project_anchor_exact_name"], "evidence_tier": "PROJECT_ANCHOR"}
    if reference_count == 0 and not (maps_main or maps_north) and any(w in low for w in ["abstract", "framework", "factory", "registry", "base class", "generic"]):
        return {**base, "classification": "SIMPLIFY", "suggested_classification": "SIMPLIFY_CANDIDATE", "signals": ["premature_abstraction"], "confidence": 0.68, "reason": "Possible premature abstraction with no current acceptance mapping.", "evidence": ["abstraction_term"]}
    if maps_main or maps_north:
        if reference_count:
            return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.72, "reason": "Artifact maps to the North Star and is referenced by the existing project graph.", "goal_mapping": "north_star_with_reference", "north_star_mapping": north.get("goal") if north.get("confirmed") else None, "evidence": ["maps_to_north_star", f"referenced_by_project:{reference_count}"], "evidence_tier": "TWO_SOURCE_POSITIVE"}
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "confidence": 0.45, "reason": "Content-only North Star wording is not strong enough to protect an artifact without a contract, core path, or project reference.", "goal_mapping": "content_claim_only", "north_star_mapping": None, "signals": ["content_only_north_star_claim"], "evidence": ["maps_to_north_star_text_only"], "evidence_tier": "SINGLE_SIGNAL"}
    if reference_count:
        return {**base, "classification": "KEEP", "suggested_classification": "KEEP", "confidence": 0.7, "reason": "Artifact is referenced by the existing project graph.", "current_ticket_mapping": "existing_reference", "evidence": [f"referenced_by_project:{reference_count}"]}
    if kind_for_path(path) == "doc_section" and not maps_must and not maps_north:
        return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["unmapped_document"], "confidence": 0.35, "reason": "Unmapped documentation is ambiguous and cannot be classified as backlog automatically.", "north_star_mapping": None, "evidence": ["doc_without_mapping"]}
    return {**base, "classification": "REVIEW_REQUIRED", "suggested_classification": "REVIEW_REQUIRED", "signals": ["unmapped_artifact"], "confidence": 0.3, "reason": "No strong positive or negative mapping; human review is required.", "north_star_mapping": None, "evidence": ["no_strong_mapping"]}


def boundary_reasons(ticket: dict[str, Any]) -> tuple[str, list[str], str]:
    freeze = acceptance_frozen_violation(ticket)
    if freeze:
        return "FAIL", [freeze], "abort"

    ticket = update_usage(ticket)
    usage = ticket.get("budget_used", {})
    files = usage.get("changed_files", [])
    immutable_changes = usage.get("immutable_changes", [])
    runtime_changes = usage.get("runtime_changes", [])
    upstream_changes = usage.get("upstream_evidence_changes", [])
    budget = ticket.get("budget", {})
    acceptance = ticket.get("acceptance", {})
    allowed = ticket_writable_paths(ticket)
    forbidden = ticket.get("forbidden_paths", [])

    reasons: list[str] = []
    scope_advisories: list[str] = []
    change_budget_reasons: list[str] = []
    hard_budget_reasons: list[str] = []

    if upstream_changes:
        return (
            "UPSTREAM_EVIDENCE_INVALID",
            ["read dependency changed after ticket start: " + ", ".join(upstream_changes[:8])],
            "supersede_or_rebaseline_upstream",
        )
    forbidden_hits = [p for p in [*files, *immutable_changes] if match_path(p, forbidden)]
    outside = [p for p in files if path_contract_role(p, ticket) == "outside"]
    if forbidden_hits:
        reasons.append("forbidden_paths changed: " + ", ".join(forbidden_hits[:8]))
    if immutable_changes:
        reasons.append("immutable_paths changed: " + ", ".join(immutable_changes[:8]))
    if outside:
        scope_advisories.append("outside optional ticket writable_paths changed: " + ", ".join(outside[:8]))

    max_files = budget.get("max_changed_files", acceptance.get("max_changed_files"))
    max_lines = budget.get("max_diff_lines", acceptance.get("max_diff_lines"))
    max_calls = budget.get("max_tool_calls")
    max_minutes = budget.get("max_minutes")
    if max_files is not None and usage.get("changed_files_count", 0) > max_files:
        change_budget_reasons.append(f"changed_files {usage.get('changed_files_count')} > {max_files}")
    if max_lines is not None and usage.get("diff_lines", 0) > max_lines:
        change_budget_reasons.append(f"diff_lines {usage.get('diff_lines')} > {max_lines}")
    if (
        max_calls is not None
        and usage.get("budget_enforcement") == "CONNECTED_VERIFIED"
        and usage.get("tool_calls", 0) > max_calls
    ):
        hard_budget_reasons.append(f"tool_calls {usage.get('tool_calls')} > {max_calls}")
    if max_minutes is not None and usage.get("elapsed_minutes", 0) > max_minutes:
        hard_budget_reasons.append(f"elapsed_minutes {usage.get('elapsed_minutes')} > {max_minutes}")

    if reasons:
        return "DRIFT", reasons + scope_advisories, "repair_explicit_boundary"
    if hard_budget_reasons or change_budget_reasons or scope_advisories:
        return "BUDGET_PRESSURE", hard_budget_reasons + change_budget_reasons + scope_advisories, "finish_atomic_step_then_reassess"
    if runtime_changes:
        return (
            "ENVIRONMENT_DIRTY",
            ["runtime paths changed outside the implementation diff: " + ", ".join(runtime_changes[:8])],
            "continue_with_runtime_evidence",
        )
    return "ON_TRACK", [], "continue"


def run_validation(command_id: str) -> tuple[bool, str]:
    row = catalog().get(command_id)
    if not row:
        return False, f"validation id not found: {command_id}"
    parts, error = validation_command_parts(row if isinstance(row, dict) else {})
    if error:
        return False, f"{command_id}: {error}"
    proc = run(parts, timeout=int(row.get("timeout_sec", 600)))
    if proc.returncode == 0:
        return True, f"{command_id}: pass"
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
    return False, f"{command_id}: exit {proc.returncode}; " + " | ".join(tail)


def validation_input_paths(ticket: dict[str, Any]) -> list[str]:
    patterns = [
        *ticket_read_dependencies(ticket),
        *acceptance_positive_paths(ticket),
        *ticket.get("budget_used", {}).get("changed_files", []),
    ]
    for command_id in validation_ids(ticket):
        row = catalog().get(command_id, {})
        if not isinstance(row, dict):
            continue
        for key in ("inputs", "reads_paths", "protects_paths"):
            values = row.get(key, [])
            if isinstance(values, list):
                patterns.extend(str(value) for value in values if str(value).strip())
    files: list[str] = []
    missing: list[str] = []
    for raw in patterns:
        pattern = norm(str(raw))
        if not pattern:
            continue
        if any(mark in pattern for mark in ("*", "?", "[")):
            matches = [norm(str(path)) for path in filesystem_matches(pattern) if path.is_file()]
            files.extend(matches[:2000])
            if not matches:
                missing.append("__missing_pattern__:" + pattern)
        elif filesystem_path(pattern).is_file():
            files.append(pattern)
        else:
            missing.append("__missing_path__:" + pattern)
    return list(dict.fromkeys([*sorted(files), *sorted(missing)]))


def validation_input_fingerprint(ticket: dict[str, Any]) -> str:
    ids = validation_ids(ticket)
    data = catalog()
    file_rows: list[dict[str, Any]] = []
    for path in validation_input_paths(ticket):
        if path.startswith("__missing_"):
            file_rows.append({"path": path, "sha256": None})
            continue
        candidate = filesystem_path(path)
        file_rows.append({
            "path": path,
            "size": candidate.stat().st_size if candidate.is_file() else None,
            "sha256": sha256_file_contents(candidate) if candidate.is_file() else None,
        })
    payload = {
        "acceptance_fingerprint": acceptance_fingerprint(ticket),
        "validation_ids": ids,
        "catalog": {command_id: data.get(command_id) for command_id in ids},
        "inputs": file_rows,
    }
    return sha256_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def validation_cache_reusable(ticket: dict[str, Any], fingerprint: str) -> bool:
    lifecycle = ticket.get("validation_lifecycle", {})
    if isinstance(lifecycle, dict) and any(lifecycle.get(key) for key in ("setup", "healthcheck", "teardown")):
        return False
    cache = ticket.get("validation_cache", {})
    return bool(
        isinstance(cache, dict)
        and cache.get("status") == "PASS"
        and cache.get("input_fingerprint") == fingerprint
    )


def check_contains(item: Any) -> str | None:
    if isinstance(item, str) and "::" in item:
        file, text = item.split("::", 1)
    elif isinstance(item, dict):
        file = str(item.get("file", ""))
        text = str(item.get("text", ""))
    else:
        return f"unsupported contains assertion: {item}"
    if not file or not text:
        return f"invalid contains assertion: {item}"
    if text not in read_text(Path(file), 500000):
        return f"missing text in {file}: {text[:80]}"
    return None


def check_assertion(item: Any) -> str | None:
    if not isinstance(item, dict):
        return f"unsupported assertion: {item}"
    typ = str(item.get("type", ""))
    if typ == "file_exists":
        path = str(item.get("path") or item.get("file") or "")
        return None if path and Path(path).exists() else f"assertion file_exists failed: {path}"
    if typ == "file_contains":
        return check_contains({"file": item.get("file"), "text": item.get("text")})
    if typ == "json_field_equals":
        file = Path(str(item.get("file", "")))
        dotted = str(item.get("path", ""))
        expected = item.get("equals")
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            cur: Any = data
            for part in dotted.split("."):
                cur = cur[part]
        except Exception as exc:  # noqa: BLE001
            return f"assertion json_field_equals failed: {file} {dotted}: {exc}"
        return None if cur == expected else f"assertion json_field_equals failed: {file} {dotted} != {expected}"
    return f"unsupported assertion type: {typ}"


def acceptance_result(ticket: dict[str, Any], run_commands: bool) -> tuple[bool, list[str]]:
    if not has_machine_acceptance(ticket):
        return False, [MISSING_ACCEPTANCE_MESSAGE]
    acceptance = ticket.get("acceptance", {})
    usage = ticket.get("budget_used", {})
    files = [*usage.get("changed_files", []), *usage.get("immutable_changes", [])]
    reasons: list[str] = []

    for path in acceptance.get("files_exist", []):
        if not Path(path).exists():
            reasons.append(f"missing required file: {path}")
    for item in acceptance.get("contains", []):
        err = check_contains(item)
        if err:
            reasons.append(err)
    for item in acceptance.get("assertions", []):
        err = check_assertion(item)
        if err:
            reasons.append(err)
    for pattern in acceptance.get("files_not_changed", []):
        hits = [p for p in files if match_path(p, [pattern])]
        if hits:
            reasons.append(f"files_not_changed violated for {pattern}: {', '.join(hits[:6])}")

    max_files = acceptance.get("max_changed_files")
    max_lines = acceptance.get("max_diff_lines")
    soft_change_budget = ticket.get("budget", {}).get("change_enforcement") == "soft"
    if not soft_change_budget and max_files is not None and usage.get("changed_files_count", 0) > max_files:
        reasons.append(f"acceptance max_changed_files exceeded: {usage.get('changed_files_count')} > {max_files}")
    if not soft_change_budget and max_lines is not None and usage.get("diff_lines", 0) > max_lines:
        reasons.append(f"acceptance max_diff_lines exceeded: {usage.get('diff_lines')} > {max_lines}")

    command_ids = list(dict.fromkeys([
        *[str(value) for value in ticket.get("validation_ids", []) if str(value)],
        *[str(value) for value in acceptance.get("commands_pass", []) if str(value)],
    ]))
    if run_commands:
        input_fingerprint = validation_input_fingerprint(ticket)
        if command_ids and validation_cache_reusable(ticket, input_fingerprint):
            ticket["validation_run"] = {
                "status": "PASS",
                "cache_hit": True,
                "input_fingerprint": input_fingerprint,
                "validated_at": ticket.get("validation_cache", {}).get("validated_at"),
                "reused_at": now(),
                "root_cause": None,
                "executed_ids": [],
                "skipped_ids": [],
                "suppressed_cascade_count": 0,
            }
            return not reasons, reasons
        lifecycle = ticket.get("validation_lifecycle", {}) if isinstance(ticket.get("validation_lifecycle"), dict) else {}
        setup_ids = [str(value) for value in lifecycle.get("setup", [])]
        health_ids = [str(value) for value in lifecycle.get("healthcheck", [])]
        teardown_ids = [str(value) for value in lifecycle.get("teardown", [])]
        command_reasons: list[str] = []
        executed_ids: list[str] = []
        skipped_ids: list[str] = []
        cleanup_warnings: list[str] = []
        root_cause: dict[str, Any] | None = None
        steps = [
            *[("setup", command_id) for command_id in setup_ids],
            *[("healthcheck", command_id) for command_id in health_ids],
            *[("validation", command_id) for command_id in command_ids],
        ]
        try:
            for index, (stage, command_id) in enumerate(steps):
                ok, msg = run_validation(command_id)
                executed_ids.append(command_id)
                if not ok:
                    prefixed = f"{stage}: {msg}" if stage != "validation" else msg
                    command_reasons.append(prefixed)
                    root_cause = {"stage": stage, "command_id": command_id, "message": prefixed}
                    skipped_ids = [candidate for _, candidate in steps[index + 1:]]
                    break
        finally:
            for command_id in teardown_ids:
                ok, msg = run_validation(command_id)
                executed_ids.append(command_id)
                if not ok:
                    warning = "teardown: " + msg
                    if root_cause is None:
                        command_reasons.append(warning)
                        root_cause = {"stage": "teardown", "command_id": command_id, "message": warning}
                    else:
                        cleanup_warnings.append(warning)
        reasons.extend(command_reasons)
        cache_status = "PASS" if not command_reasons else "FAIL"
        if cache_status == "PASS":
            input_fingerprint = validation_input_fingerprint(ticket)
        ticket["validation_cache"] = {
            "status": cache_status,
            "input_fingerprint": input_fingerprint,
            "validation_ids": command_ids,
            "validated_at": now(),
            "reusable": cache_status == "PASS" and not any([setup_ids, health_ids, teardown_ids]),
        }
        ticket["validation_run"] = {
            "status": cache_status,
            "cache_hit": False,
            "input_fingerprint": input_fingerprint,
            "validated_at": ticket["validation_cache"]["validated_at"],
            "root_cause": root_cause,
            "executed_ids": executed_ids,
            "skipped_ids": skipped_ids,
            "suppressed_cascade_count": len(skipped_ids) + len(cleanup_warnings),
            "cleanup_warnings": cleanup_warnings,
        }
    return not reasons, reasons


def quality_gate_result(ticket: dict[str, Any]) -> dict[str, Any]:
    gates = ticket.get("quality_gates", []) if isinstance(ticket.get("quality_gates"), list) else []
    dimensions: dict[str, bool | str] = {
        "technical_pass": "NOT_REQUIRED",
        "artifact_quality_pass": "NOT_REQUIRED",
        "product_pass": "NOT_REQUIRED",
        "market_pass": "NOT_REQUIRED",
    }
    dimension_keys = {
        "technical": "technical_pass",
        "artifact": "artifact_quality_pass",
        "product": "product_pass",
        "market": "market_pass",
    }
    evidence = ticket.get("evidence", []) if isinstance(ticket.get("evidence"), list) else []
    validation_passed = ticket.get("validation_run", {}).get("status") == "PASS"
    results: list[dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        gate_id = str(gate.get("id") or "")
        expected_types = {str(value) for value in gate.get("evidence_types", []) if str(value)}
        matching_evidence = [
            row for row in evidence
            if isinstance(row, dict)
            and row.get("status") == "PASS"
            and row.get("acceptance_id") == gate_id
            and (not expected_types or str(row.get("type")) in expected_types)
        ]
        validation_ok = not gate.get("validation_id") or validation_passed
        evidence_ok = not expected_types or bool(matching_evidence)
        passed = validation_ok and evidence_ok
        results.append({
            "id": gate_id,
            "dimension": gate.get("dimension"),
            "required": gate.get("required", True) is not False,
            "status": "PASS" if passed else "FAIL",
            "validation_id": gate.get("validation_id"),
            "evidence_count": len(matching_evidence),
        })
    for dimension, key in dimension_keys.items():
        rows = [row for row in results if row.get("dimension") == dimension and row.get("required")]
        if rows:
            dimensions[key] = all(row.get("status") == "PASS" for row in rows)
    blockers = [f"quality gate not satisfied: {row.get('id')} ({row.get('dimension')})" for row in results if row.get("required") and row.get("status") != "PASS"]
    return {
        "status": "PASS" if not blockers else "FAIL",
        **dimensions,
        "gates": results,
        "blockers": blockers,
    }


def evaluate(ticket: dict[str, Any], run_commands: bool = False) -> dict[str, Any]:
    if not has_machine_acceptance(ticket):
        return {
            "status": "FAIL",
            "failure_class": "ACCEPTANCE_INCOMPLETE",
            "reasons": [MISSING_ACCEPTANCE_MESSAGE],
            "suggested_action": "return_to_draft",
        }
    contract_errors = coordination_contract_errors(ticket, verify_frozen=ticket.get("status") == "ACTIVE")
    if contract_errors:
        return {
            "status": "DRIFT",
            "failure_class": "GOAL_DRIFT",
            "reasons": contract_errors,
            "suggested_action": "restore_coordination_contract_or_run_serially",
        }
    ticket = update_usage(ticket)
    status, reasons, action = boundary_reasons(ticket)
    environment_dirty = status == "ENVIRONMENT_DIRTY"
    environment_reasons = list(reasons) if environment_dirty else []
    budget_pressure = status == "BUDGET_PRESSURE"
    budget_advisories = list(reasons) if budget_pressure else []
    if status in {"ON_TRACK", "ENVIRONMENT_DIRTY", "BUDGET_PRESSURE"}:
        if environment_dirty:
            status, reasons, action = "ON_TRACK", [], "continue"
        elif budget_pressure:
            status, reasons, action = "ON_TRACK", [], "continue"
        ok, acceptance_reasons = acceptance_result(ticket, run_commands=run_commands)
        if ok:
            if command_validation_required(ticket) and not run_commands:
                status, action = "NEEDS_VALIDATION", "run close or check --run-validation"
                reasons.append("validation requirements not run: " + ", ".join(validation_ids(ticket)))
            else:
                quality = quality_gate_result(ticket)
                if quality["status"] != "PASS":
                    status, action = "NEEDS_QUALITY_EVIDENCE", "add_quality_evidence_or_fix"
                    reasons.extend(quality["blockers"])
                else:
                    status, action = (
                        ("IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY", "close_with_environment_note")
                        if environment_dirty
                        else ("PASS_READY", "close")
                    )
        else:
            reasons.extend(acceptance_reasons)
            if run_commands:
                if ticket.get("validation_run", {}).get("status") == "FAIL":
                    status, action = "VALIDATION_FAILED", "repair_and_retry_validation"
                else:
                    status, action = "ACCEPTANCE_INCOMPLETE", "complete_acceptance"
            elif command_validation_required(ticket):
                status, action = "NEEDS_VALIDATION", "run close or check --run-validation"
    result = {"status": status, "reasons": reasons, "suggested_action": action}
    failure_classes = {
        "DRIFT": "GOAL_DRIFT",
        "UPSTREAM_EVIDENCE_INVALID": "BLOCKED_BY_UPSTREAM",
        "VALIDATION_FAILED": "EXECUTION_FAILED",
        "ACCEPTANCE_INCOMPLETE": "ACCEPTANCE_INCOMPLETE",
        "ARTIFACT_SPRAWL": "ARTIFACT_SPRAWL",
        "BUDGET_EXCEEDED": "BUDGET_EXCEEDED",
        "DIFF_BUDGET_EXCEEDED_CLEAN": "BUDGET_EXCEEDED",
    }
    if status in failure_classes:
        result["failure_class"] = failure_classes[status]
    if ticket.get("validation_run"):
        result["validation"] = dict(ticket["validation_run"])
        root_cause = ticket["validation_run"].get("root_cause")
        if status == "VALIDATION_FAILED" and isinstance(root_cause, dict):
            result["retry_from"] = root_cause.get("command_id")
    result["quality"] = quality_gate_result(ticket)
    if environment_reasons:
        result["environment_status"] = "DIRTY_RUNTIME_ONLY"
        result["environment_advisories"] = environment_reasons
    if budget_advisories:
        result["budget_status"] = "SOFT_CHANGE_PRESSURE"
        result["budget_advisories"] = budget_advisories
    return result


def slug_id(text: str, fallback: str) -> str:
    seed = fallback or text[:80] or "TICKET"
    seed = re.sub(r"[^A-Za-z0-9]+", "-", seed).strip("-").upper()
    return seed[:48] or "TICKET"


def task_summary(text: str) -> str:
    values: list[str] = []
    for raw in text.splitlines():
        value = re.sub(r"^#{1,6}\s*", "", raw).strip(" -*`>\t")
        if len(value) >= 8:
            values.append(value)
        if len(values) >= 3:
            break
    return ". ".join(values)[:500] if values else "the supplied rough task"


def salient_task_terms(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", canonical_text(text))
    stop = text_words("build create deliver current ticket task goal system project using with from into only")
    counts: dict[str, int] = {}
    for word in words:
        low = word.lower()
        if low in stop or low in {"build", "create", "current", "ticket", "project", "system"} | AXIS_COMMON_TERMS:
            continue
        counts[low] = counts.get(low, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def lens_notes_for_rough_task(text: str) -> dict[str, Any]:
    heavy = filter_contextual_scope_hits(heavy_hits(text), text, {})
    lower = canonical_text(text)
    summary = task_summary(text)
    terms = salient_task_terms(text, 5)
    acceptance_candidates: list[str] = [
        f"Add one machine-checkable outcome for: {summary}",
    ]
    paths = list(dict.fromkeys(re.findall(r"(?:[A-Za-z0-9_.-]+/)+(?:[A-Za-z0-9_.*-]+)", text)))
    if paths:
        acceptance_candidates.append("Assert the required path exists or contains the expected result: " + paths[0])
    if "test" in lower or "validation" in lower or "验收" in lower:
        acceptance_candidates.append("Bind one focused validation_catalog id to the stated outcome.")
    why_now = f"Advance the confirmed North Star through one bounded result: {summary}"
    smallest = f"Implement the shortest acceptance-linked path for {summary}; avoid unrelated files and future architecture."
    scope_risks = [
        "Expands beyond the supplied task summary",
        "Adds files or abstractions without an acceptance consumer",
        "Continues on the same local axis after acceptance is satisfied",
    ]
    if heavy:
        scope_risks.insert(0, "Heavy-scope terms in the rough task: " + ", ".join(heavy[:4]))
    shit_risks = [
        "Unreferenced abstractions or documents that do not map to acceptance",
        "Future-stage implementation presented as current scope",
    ]
    if terms:
        shit_risks.append("Repeated output around local terms: " + ", ".join(terms))
    return {
        "product": {
            "why_now": why_now,
            "non_goal_warning": heavy[:4],
        },
        "engineering": {
            "smallest_path": smallest,
            "avoid": ["unrelated rewrite", "future-stage implementation", "acceptance-free abstraction"],
        },
        "qa": {
            "machine_acceptance_candidates": acceptance_candidates,
        },
        "scope": {
            "drift_signals": scope_risks,
            "backlog_only": heavy[:4] or ["Any idea outside the stated bounded result"],
        },
        "janitor": {
            "likely_shit_mountain": shit_risks,
        },
        "custodian": {
            "request_risks": [
                "Treat later user input as change_request, not a new active task.",
                "Route reasonable but non-current ideas to backlog.",
            ],
        },
    }


def compile_budget_for_text(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select a bounded budget from task shape instead of one fixed line cap."""
    lower = canonical_text(text)
    paths = list(dict.fromkeys(re.findall(r"(?:[A-Za-z0-9_.-]+/)+(?:[A-Za-z0-9_.*-]+)", text)))
    micro_terms = [
        term for term in ["single file", "one file", "rename", "typo", "copy edit", "one assertion", "small fix"]
        if term in lower
    ]
    if re.search(r"\b(?:one|single)\b.{0,32}\b(?:assertion|variable|literal|test)\b", lower):
        micro_terms.append("one bounded assertion/variable/test change")
    integration_terms = [
        term for term in [
            "adapter", "pipeline", "integration", "interface", "schema", "migration",
            "endpoint", "frontend", "workflow", "end-to-end", "end to end",
        ]
        if term in lower
    ]
    broad_terms = [
        term for term in ["cross-module", "cross module", "multiple modules", "whole project", "entire system"]
        if term in lower
    ]

    batch_terms = [
        term for term in [
            "batch annotation", "bulk annotation", "dataset labeling", "batch labeling",
            "批量标注", "批量标签", "训练数据标注", "llr训练", "llr 训练",
        ]
        if term in lower
    ]
    count_match = re.search(
        r"\b(\d{1,7})\s*(?:items?|files?|samples?|records?|images?)\b|"
        r"(\d{1,7})\s*(?:条|个|份|张|文件|样本)",
        lower,
    )
    batch_count = int(next((group for group in count_match.groups() if group), "0")) if count_match else 0

    if batch_terms and batch_count:
        tier = "BATCH_VOLUME"
        output_allowance = batch_count + max(5, (batch_count + 19) // 20)
        selected = {
            "max_minutes": max(60, min(1440, batch_count * 2)),
            "max_tool_calls": max(80, batch_count * 2),
            "max_changed_files": output_allowance,
            "max_diff_lines": max(1500, batch_count * 50),
        }
        recommended = {
            "min_diff_lines": batch_count,
            "max_diff_lines": max(1500, batch_count * 60),
        }
    elif len(text) >= 2400 or len(paths) >= 6 or len(broad_terms) >= 2:
        tier = "BROAD_BOUNDED"
        selected = {"max_minutes": 60, "max_tool_calls": 80, "max_changed_files": 12, "max_diff_lines": 1500}
        recommended = {"min_diff_lines": 800, "max_diff_lines": 2000}
    elif integration_terms or len(text) >= 900 or len(paths) >= 3:
        tier = "INTEGRATION_BOUNDED"
        selected = {"max_minutes": 45, "max_tool_calls": 60, "max_changed_files": 8, "max_diff_lines": 800}
        recommended = {"min_diff_lines": 500, "max_diff_lines": 1200}
    elif micro_terms and len(text) < 600 and len(paths) <= 1:
        tier = "MICRO_BOUNDED"
        selected = {"max_minutes": 20, "max_tool_calls": 25, "max_changed_files": 3, "max_diff_lines": 180}
        recommended = {"min_diff_lines": 80, "max_diff_lines": 300}
    else:
        tier = "STANDARD_BOUNDED"
        selected = {"max_minutes": 40, "max_tool_calls": 50, "max_changed_files": 6, "max_diff_lines": 500}
        recommended = {"min_diff_lines": 300, "max_diff_lines": 800}

    signals = []
    if micro_terms:
        signals.append("micro terms: " + ", ".join(micro_terms[:4]))
    if integration_terms:
        signals.append("integration terms: " + ", ".join(integration_terms[:6]))
    if broad_terms:
        signals.append("broad terms: " + ", ".join(broad_terms[:4]))
    if paths:
        signals.append(f"explicit path count: {len(paths)}")
    if batch_terms:
        signals.append("batch volume: " + ", ".join(batch_terms[:3]))
    if batch_count:
        signals.append(f"declared batch items: {batch_count}")
    signals.append(f"rough task characters: {len(text)}")
    selected["change_enforcement"] = "soft"
    return selected, {
        "policy": "dynamic_bounded_budget",
        "tier": tier,
        "signals": signals,
        "recommended_range": recommended,
        "selected": dict(selected),
        "adjustment_rule": "Review and edit the DRAFT budget before ready; do not silently expand an ACTIVE ticket.",
    }


def compile_batch_execution_for_text(text: str) -> dict[str, Any]:
    lower = canonical_text(text)
    batch_terms = [
        "batch annotation", "bulk annotation", "dataset labeling", "batch labeling",
        "批量标注", "批量标签", "训练数据标注", "llr训练", "llr 训练",
    ]
    if not any(term in lower for term in batch_terms):
        return {}
    count_match = re.search(
        r"\b(\d{1,7})\s*(?:items?|files?|samples?|records?|images?)\b|"
        r"(\d{1,7})\s*(?:条|个|份|张|文件|样本)",
        lower,
    )
    item_count = int(next((group for group in count_match.groups() if group), "0")) if count_match else 0
    return {
        "enabled": True,
        "kind": "independent_annotation",
        "item_count": item_count,
        "requested_workers": item_count,
        "expected_output_files": item_count,
        "output_paths": [],
        "validation_ids": [],
        "independence_evidence": "",
        "merge_strategy": "",
        "ceo_confirmation": {},
    }


def ticket_axis(ticket: dict[str, Any]) -> str:
    roots: list[str] = []
    for pattern in ticket_writable_paths(ticket):
        prefix = fixed_pattern_prefix(str(pattern))
        parts = [part for part in Path(prefix).parts if part not in {".", "*", "**"}]
        if parts:
            roots.append("/".join(parts[:2]))
    if roots:
        counts = {root: roots.count(root) for root in set(roots)}
        root = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
        root_words = set(re.findall(r"[a-z0-9]+", root.lower()))
        terms = [term for term in salient_task_terms(
            f"{ticket.get('title', '')}\n{ticket.get('task_goal', '')}",
            5,
        ) if term not in root_words and term not in AXIS_COMMON_TERMS]
        return f"{root}:{terms[0]}" if terms else root
    terms = salient_task_terms(flattened_ticket_text(ticket), 3)
    return ":".join(terms) if terms else "general"


def ticket_concepts(ticket: dict[str, Any]) -> list[str]:
    primary = "\n".join([str(ticket.get("title") or ""), str(ticket.get("task_goal") or "")])
    root_words: set[str] = set()
    for pattern in ticket_writable_paths(ticket):
        prefix = fixed_pattern_prefix(str(pattern))
        for part in Path(prefix).parts:
            for word in re.findall(r"[a-z0-9]+", part.lower()):
                if word not in {"src", "tests", "test", "app", "apps", "lib"}:
                    root_words.add(word)
    north = north_star()
    north_words = text_words(json.dumps(
        {
            "goal": north.get("goal"),
            "main_path": north.get("main_path", []),
            "allowed_subgoals": north.get("allowed_subgoals", []),
        },
        ensure_ascii=False,
    ))
    excluded = AXIS_COMMON_TERMS | root_words | north_words
    terms = [term for term in salient_task_terms(primary, 12) if term not in excluded][:8]
    primary_words = text_words(primary)
    tokens = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", canonical_text(primary))
        if word.lower() in primary_words
        and word.lower() not in {"build", "create", "current", "ticket", "project", "system"} | excluded
    ]
    phrases = [f"{left} {right}" for left, right in zip(tokens, tokens[1:])]
    concepts = list(dict.fromkeys([*phrases, *terms]))
    return concepts[:10]


def recent_done_tickets(limit: int = 8) -> list[dict[str, Any]]:
    if not DONE.exists():
        return []
    paths = sorted(
        DONE.glob("*.json"),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    )[:limit]
    rows = []
    for path in paths:
        data = load_json(path, {})
        if data:
            data["_path"] = str(path)
            rows.append(data)
    return rows


def axis_advisory(current: dict[str, Any] | None = None) -> dict[str, Any]:
    tickets = recent_done_tickets()
    if current and current.get("ticket_id"):
        tickets = [current] + tickets
    if len(tickets) < 3:
        return {"status": "OK", "reason": "not enough recent tickets for axis-fatigue signal"}

    axes = [ticket_axis(t) for t in tickets[:8]]
    axis_counts = {axis: axes.count(axis) for axis in set(axes)}
    dominant_axis, dominant_count = max(axis_counts.items(), key=lambda item: item[1])

    concept_counts: dict[str, int] = {}
    for ticket in tickets[:8]:
        for concept in ticket_concepts(ticket):
            concept_counts[concept] = concept_counts.get(concept, 0) + 1
    repeated_concepts = [
        concept
        for concept, count in sorted(concept_counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 3
    ]

    concept_cluster = dominant_count >= 2 and bool(repeated_concepts)
    if dominant_count >= 4 or concept_cluster:
        return {
            "status": "AXIS_FATIGUE_WARNING",
            "dominant_axis": dominant_axis,
            "recent_axis_count": dominant_count,
            "repeated_concepts": repeated_concepts[:5],
            "reason": "Recent tickets cluster on one local axis. Finish the current ticket, then switch axis or backlog follow-ups instead of propagating another small field.",
            "suggested_action": "finish_current_then_switch_axis_or_backlog_followups",
        }
    return {"status": "OK", "dominant_axis": dominant_axis, "recent_axis_count": dominant_count}


def infer_mdcp_precision(text: str) -> str:
    low = canonical_text(text)
    if any(w in low for w in ["only", "must", "do not", "refusing", "fix", "p0", "验收", "必须", "只能", "不要", "直接", "修复", "停止"]):
        return "high"
    if any(w in low for w in ["explore", "research", "rough", "brainstorm", "maybe", "看看", "研究", "想法", "可能", "先了解"]):
        return "low"
    return "medium"


def infer_mdcp_plane(text: str) -> str:
    low = canonical_text(text)
    if any(w in low for w in ["不对", "错误", "返工", "修复", "failure", "failed", "regression", "bug"]):
        return "correction"
    if any(w in low for w in ["goal compass", "mdcp", "plugin", "harness", "目标模式", "插件", "协议"]):
        return "meta"
    if any(w in low for w in ["architecture", "pipeline", "route", "adapter", "架构", "分层", "链路", "路线"]):
        return "architecture"
    if any(w in low for w in ["ticket", "acceptance", "schema", "validation", "assertion", "验收", "测试", "规范"]):
        return "spec"
    return "execution"


def mdcp_scope_anchor(ticket: dict[str, Any] | None = None) -> list[str]:
    ticket = ticket or current_ticket()
    anchors: list[str] = []
    task = str(ticket.get("task_goal") or "").strip()
    if task:
        anchors.append(task[:180])
    anchors.extend(str(x) for x in ticket_writable_paths(ticket)[:4])
    if ticket.get("acceptance"):
        anchors.append("current acceptance only")
    return anchors[:6] or ["bounded current ticket"]


def mdcp_acceptance_consumer(ticket: dict[str, Any] | None = None) -> str:
    ticket = ticket or current_ticket()
    acc = ticket.get("acceptance", {}) if isinstance(ticket.get("acceptance"), dict) else {}
    if validation_ids(ticket):
        return "validation_catalog"
    if acc.get("files_exist") or acc.get("contains") or acc.get("assertions"):
        return "machine file/assertion check"
    if ticket.get("status") in {None, "", "DRAFT", "NONE"}:
        return "draft ticket reviewer"
    return "human confirmation required"


def mdcp_time_cost_signal(text: str, ticket: dict[str, Any] | None = None) -> str:
    ticket = ticket or {}
    budget = ticket.get("budget", {}) if isinstance(ticket.get("budget"), dict) else {}
    if filter_contextual_scope_hits(heavy_hits(text), text, ticket) or int(budget.get("max_minutes") or 0) > 60 or int(budget.get("max_tool_calls") or 0) > 80:
        return "high"
    if int(budget.get("max_minutes") or 0) <= 30 and int(budget.get("max_tool_calls") or 0) <= 40:
        return "medium"
    return "low"


def mdcp_value_signal(text: str, ticket: dict[str, Any] | None = None) -> str:
    ticket = ticket or {}
    joined = f"{text}\n{flattened_ticket_text(ticket)}"
    if maps_to_north_star(joined):
        return "high"
    if confirmed_goal() and goal_match(joined, north_star())["status"] in {"ALIGNED", "PARTIAL"}:
        return "medium"
    return "low" if confirmed_goal() else "medium"


def mdcp_scope_sink_risk(text: str, ticket: dict[str, Any] | None = None) -> str:
    ticket = ticket or {}
    joined = f"{text}\n{flattened_ticket_text(ticket)}"
    if term_hits(joined, ticket.get("anti_patterns", [])) or filter_contextual_scope_hits(heavy_hits(joined), joined, ticket):
        return "strong"
    if term_hits(joined, ticket.get("backlog_only", [])) or term_hits(joined, north_star().get("backlog_domains", [])):
        return "weak"
    return "none"


def mdcp_loop_risk(ticket: dict[str, Any] | None = None) -> str:
    axis = axis_advisory(ticket if ticket and ticket.get("status") == "ACTIVE" else None)
    if axis.get("status") == "AXIS_FATIGUE_WARNING":
        return "strong" if int(axis.get("recent_axis_count") or 0) >= 5 else "weak"
    return "none"


def mdcp_consumer_mismatch_risk(ticket: dict[str, Any] | None = None) -> str:
    ticket = ticket or {}
    consumer = mdcp_acceptance_consumer(ticket)
    if consumer == "human confirmation required":
        return "strong"
    if consumer == "draft ticket reviewer":
        return "weak"
    return "none"


def mdcp_layer_1_structured_expression(text: str, ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    ticket = ticket or {}
    north = north_star()
    definition = goal_definition_summary(north)
    loop = mdcp_loop_risk(ticket)
    return {
        "north_star_goal": north.get("goal") if north.get("confirmed") else ticket.get("global_goal"),
        "goal_definition_quality": definition.get("quality"),
        "precise_goal": definition.get("precise_goal"),
        "problem_statement": definition.get("problem_statement"),
        "first_principles": definition.get("first_principles", []),
        "process_nodes": definition.get("process_nodes", []),
        "goal_deliverables": definition.get("deliverables", []),
        "goal_final_acceptance": definition.get("final_acceptance", []),
        "goal_anchor": ticket.get("task_goal") or text[:240],
        "scope_anchor": mdcp_scope_anchor(ticket),
        "conversation_plane": infer_mdcp_plane(text),
        "precision_level": infer_mdcp_precision(text),
        "time_cost_signal": mdcp_time_cost_signal(text, ticket),
        "value_signal": mdcp_value_signal(text, ticket),
        "metacognition_lock_signal": "strong" if loop == "strong" else "weak" if loop == "weak" else "none",
        "loop_risk": loop,
        "consumer_mismatch_risk": mdcp_consumer_mismatch_risk(ticket),
        "acceptance_consumer": mdcp_acceptance_consumer(ticket),
        "scope_sink_risk": mdcp_scope_sink_risk(text, ticket),
    }


def mdcp_layer_1_pass_criteria(ticket: dict[str, Any]) -> dict[str, bool]:
    contract_errors = acceptance_contract_errors(ticket)
    raw_shell_ok = not any("raw shell" in err for err in contract_errors)
    definition = goal_definition_summary()
    return {
        "north_star_confirmed": bool(confirmed_goal()),
        "goal_definition_structured": str(definition.get("quality") or "").startswith("STRUCTURED"),
        "goal_definition_detailed": definition.get("quality") == "STRUCTURED_DETAILED",
        "request_classified": True,
        "ticket_structured": not bool(validate_shape(ticket)),
        "machine_acceptance_present": has_machine_acceptance(ticket),
        "acceptance_consumer_known": mdcp_acceptance_consumer(ticket) != "human confirmation required",
        "allowed_paths_present": bool(ticket_writable_paths(ticket)) or ticket.get("execution_mode") == "read_only",
        "forbidden_paths_present": bool(ticket.get("forbidden_paths")),
        "budget_present": bool(ticket.get("budget")),
        "drift_signals_present": bool(ticket.get("drift_signals")),
        "raw_shell_acceptance_forbidden": raw_shell_ok,
        "scope_anchor_present": bool(mdcp_scope_anchor(ticket)),
    }


def mdcp_layer_1_errors(ticket: dict[str, Any]) -> list[str]:
    criteria = mdcp_layer_1_pass_criteria(ticket)
    required = [
        "north_star_confirmed",
        "ticket_structured",
        "machine_acceptance_present",
        "acceptance_consumer_known",
        "allowed_paths_present",
        "forbidden_paths_present",
        "budget_present",
        "drift_signals_present",
        "raw_shell_acceptance_forbidden",
        "scope_anchor_present",
    ]
    return [f"MDCP layer_1_pass_criteria failed: {key}" for key in required if not criteria.get(key)]


def company_policy_core_text(ticket: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ["title", "why_now", "task_goal"]:
        value = ticket.get(key)
        if value:
            values.append(str(value))
    values.extend(str(value) for value in ticket.get("must_do", []) if value)
    values.extend(acceptance_positive_paths(ticket))
    return "\n".join(values)


def company_complexity(ticket: dict[str, Any]) -> dict[str, Any]:
    text = company_policy_core_text(ticket).lower()
    strategy_terms = [
        "strategy",
        "strategic",
        "roadmap",
        "architecture",
        "architectural",
        "tradeoff",
        "choose between",
        "cross-repo",
        "cross repo",
        "cross-system",
        "cross system",
        "路线",
        "战略",
        "架构",
        "权衡",
        "跨仓",
        "跨系统",
    ]
    consequence_terms = [
        "irreversible",
        "production migration",
        "data migration",
        "public api",
        "permission boundary",
        "security boundary",
        "destructive operation",
        "airworthiness release",
        "flight release authorization",
        "clinical dosing",
        "medication eligibility",
        "patient safety",
        "water quality interlock",
        "public warning authorization",
        "emergency dispatch authorization",
        "life safety",
        "safety-critical",
        "food-contact migration",
        "sterile barrier",
        "seal integrity",
        "dangerous goods packaging",
        "pharmaceutical serialization",
        "retort seam integrity",
        "double-seam integrity",
        "aerosol burst pressure",
        "burst pressure release",
        "glass thermal shock",
        "thermal shock release",
        "food-contact resin migration",
        "mineral-oil migration",
        "seam rupture",
        "flammable propellant",
        "oxygen-barrier loss",
        "tamper-evident bridge failure",
        "internal coating pinholes",
        "sudden leakage",
        "vial breakage",
        "microbial carryover",
        "seal-channel leakage",
        "sterilization-dose nonuniformity",
        "allergen label accuracy",
        "ispm-15 phytosanitary",
        "child-resistant closure",
        "packaging contamination control",
        "pressure vessel leakage",
        "can rupture",
        "glass delamination",
        "injectable medicine contamination",
        "azo-dye residue",
        "allergen cross-contact",
        "admit pathogens",
        "pathogen ingress",
        "microbial contamination",
        "sterility breach",
        "glass fragments",
        "food-contact contamination",
        "不可逆",
        "生产迁移",
        "数据迁移",
        "公共 api",
        "权限边界",
        "安全边界",
        "破坏性操作",
        "适航放行",
        "临床给药",
        "患者安全",
        "水质联锁",
        "公共告警授权",
        "应急调度授权",
        "生命安全",
        "安全关键",
        "食品接触迁移",
        "无菌屏障",
        "封口完整性",
        "危险品包装",
        "药品追溯码",
        "二重卷封完整性",
        "爆破压力放行",
        "玻璃热冲击",
        "热冲击放行",
        "食品接触树脂迁移",
        "矿物油迁移",
        "接缝破裂",
        "易燃推进剂",
        "阻氧层失效",
        "防拆桥失效",
        "内涂层针孔",
        "突然泄漏",
        "西林瓶破裂",
        "微生物残留",
        "封口通道泄漏",
        "灭菌剂量不均",
        "过敏原标签准确性",
        "植检放行",
        "儿童安全锁盖",
        "包装污染控制",
        "压力容器泄漏",
        "罐体破裂",
        "玻璃脱片",
        "注射剂污染",
        "偶氮染料残留",
        "过敏原交叉接触",
        "病原体侵入",
        "微生物污染",
        "无菌失效",
        "玻璃碎片",
        "食品接触污染",
    ]
    evidence_text = json.dumps(
        {
            "company_escalation_evidence": ticket.get("company_escalation_evidence", []),
            "prior_failures": ticket.get("prior_failures", []),
            "axis_advisory": ticket.get("axis_advisory", {}),
        },
        ensure_ascii=False,
    ).lower()
    insufficiency_terms = [
        "xhigh insufficient",
        "max insufficient",
        "two prior failures",
        "repeated failure",
        "authoritative evidence conflict",
        "unresolved north star conflict",
        "xhigh 不足",
        "连续两次失败",
        "权威证据冲突",
        "目标冲突未解决",
    ]
    strategy_hits = [term for term in strategy_terms if term in text]
    consequence_hits = [term for term in consequence_terms if term in text]
    insufficiency_hits = [term for term in insufficiency_terms if term in evidence_text or term in text]
    budget = ticket.get("budget", {}) if isinstance(ticket.get("budget"), dict) else {}
    max_minutes = int(budget.get("max_minutes", 0) or 0)
    max_changed_files = int(budget.get("max_changed_files", 0) or 0)
    max_diff_lines = int(budget.get("max_diff_lines", 0) or 0)
    allowed_count = len(ticket_writable_paths(ticket))
    high_budget = max_minutes > 90 or max_changed_files > 12 or max_diff_lines > 1500 or allowed_count > 8
    mechanical = (
        not strategy_hits
        and not consequence_hits
        and max_minutes <= 20
        and max_changed_files <= 3
        and max_diff_lines <= 180
        and allowed_count <= 2
    )

    signals = [
        *[f"strategy:{value}" for value in strategy_hits[:4]],
        *[f"high_consequence:{value}" for value in consequence_hits[:4]],
        *[f"insufficiency:{value}" for value in insufficiency_hits[:3]],
    ]
    if high_budget:
        signals.append("large_bounded_ticket")

    if consequence_hits and insufficiency_hits:
        tier, score, depth = "T3_CRITICAL", 4, "D3_CRITICAL"
    elif strategy_hits or consequence_hits or high_budget:
        tier, score, depth = "T2_STRATEGIC", 3, "D2_COMPLEX"
    elif mechanical:
        tier, score, depth = "T0_EXECUTION", 1, "D0_ROUTINE"
        signals = ["small_mechanical_ticket"]
    else:
        tier, score, depth = "T1_STANDARD", 2, "D1_STANDARD"
        signals = signals or ["bounded_product_ticket"]

    breadth_groups = {
        "strategy": ["strategy", "roadmap", "tradeoff", "战略", "路线", "权衡"],
        "business": ["business", "revenue", "pricing", "commercial", "market", "业务", "营收", "定价", "商业"],
        "product": ["product", "user", "customer", "workflow", "产品", "用户", "客户", "流程"],
        "architecture": ["architecture", "cross-system", "migration", "架构", "跨系统", "迁移"],
        "algorithm": ["algorithm", "model training", "inference", "optimization", "算法", "模型训练", "推理", "优化"],
        "design": ["design", "ui", "ux", "interaction", "设计", "界面", "交互"],
        "engineering": ["implement", "code", "api", "parser", "src/", "实现", "代码", "接口"],
        "quality": ["test", "validation", "acceptance", "quality", "测试", "验证", "验收", "质量"],
        "operations": ["operations", "deployment", "handoff", "manufacturing", "运营", "部署", "交付", "制造"],
        "risk": ["security", "legal", "compliance", "safety", "安全", "法务", "合规"],
    }
    breadth_hits = [name for name, terms in breadth_groups.items() if any(term in text for term in terms)]
    breadth_count = max(1, len(breadth_hits))
    if breadth_count == 1:
        breadth = "B1_FOCUSED"
    elif breadth_count <= 3:
        breadth = "B2_MULTI_FUNCTION"
    elif breadth_count <= 6:
        breadth = "B3_CROSS_FUNCTION"
    else:
        breadth = "B4_ENTERPRISE"
    return {
        "tier": tier,
        "score": score,
        "depth": depth,
        "breadth": breadth,
        "breadth_signals": breadth_hits,
        "signals": signals,
        "root_ultra_eligible": tier == "T3_CRITICAL",
    }


def supervision_decision(ticket: dict[str, Any]) -> dict[str, Any]:
    """Select only the controls whose expected rework reduction exceeds their cost."""
    text = company_policy_core_text(ticket).lower()
    complexity = company_complexity(ticket)
    budget = ticket.get("budget", {}) if isinstance(ticket.get("budget"), dict) else {}
    execution_mode = str(ticket.get("execution_mode") or "product_edit").lower()
    read_only = execution_mode in {"read_only", "status", "analysis_only"} or any(term in text for term in [
        "status only", "read-only inspection", "inspect status", "report current status",
        "summarize existing", "no product edits", "仅查看状态", "只读检查", "不修改产品",
    ])
    budget_basis = ticket.get("budget_basis", {}) if isinstance(ticket.get("budget_basis"), dict) else {}
    quality_gates = [gate for gate in ticket.get("quality_gates", []) if isinstance(gate, dict)]
    relationship = ticket.get("execution_relationship", {}) if isinstance(ticket.get("execution_relationship"), dict) else {}
    return decide_supervision({
        "text": text,
        "complexity": complexity,
        "max_minutes": budget.get("max_minutes"),
        "max_tool_calls": budget.get("max_tool_calls"),
        "max_changed_files": budget.get("max_changed_files"),
        "max_diff_lines": budget.get("max_diff_lines"),
        "allowed_count": len(ticket_writable_paths(ticket)),
        "read_only": read_only,
        "budget_tier": budget_basis.get("tier"),
        "requested_departments": ticket.get("requested_company_departments", []),
        "quality_dimensions": [gate.get("dimension") for gate in quality_gates],
        "relationship_mode": relationship.get("mode"),
        "janitor_required": ticket.get("janitor_required"),
    })


def refresh_supervision_decision(ticket: dict[str, Any]) -> dict[str, Any]:
    ticket["supervision"] = supervision_decision(ticket)
    return ticket


def company_department_specs() -> dict[str, dict[str, Any]]:
    return {
        "strategy": {
            "responsibility": "Resolve direction and irreversible tradeoffs into one smaller North-Star path.",
            "decision_authority": "Choose the strategic path and explicitly defer alternatives; may not widen frozen acceptance.",
            "deliverables": ["one direction statement", "tradeoff record", "smaller path"],
            "acceptance_criteria": ["one direction is chosen", "tradeoffs cite the current ticket", "deferred options go to backlog"],
            "consumers": ["main_thread_ceo", "business", "product", "architecture"],
            "forbidden_scope": ["implementation detail ownership", "unrequested portfolio expansion"],
            "dependencies": ["confirmed North Star", "current ticket"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "business": {
            "responsibility": "Translate the ticket into the smallest measurable business outcome and operating constraint.",
            "decision_authority": "Define business value, operating fit, and commercial boundaries; may not replace product acceptance.",
            "deliverables": ["business outcome", "operating constraint", "deferred business ideas"],
            "acceptance_criteria": ["outcome is measurable", "outcome serves the North Star", "future value is separated"],
            "consumers": ["main_thread_ceo", "product", "finance"],
            "forbidden_scope": ["feature design", "technical architecture", "generic market expansion"],
            "dependencies": ["North Star", "user or operator context"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "product": {
            "responsibility": "Compress the ticket to the smallest user-value path and define explicit non-goals.",
            "decision_authority": "Choose product behavior inside the ticket; may not add future-stage capabilities.",
            "deliverables": ["user-value path", "behavior boundary", "non-goal list"],
            "acceptance_criteria": ["behavior maps to acceptance", "non-goals are explicit", "scope is smaller than the global goal"],
            "consumers": ["main_thread_ceo", "design", "engineering", "qa"],
            "forbidden_scope": ["business strategy ownership", "implementation architecture", "future roadmap implementation"],
            "dependencies": ["business outcome", "current acceptance"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "finance": {
            "responsibility": "Quantify cost, return, exposure, and budget assumptions that materially affect this ticket.",
            "decision_authority": "Set financial constraints and expose unsupported economics; may not decide product behavior.",
            "deliverables": ["cost and value assumptions", "financial constraints", "sensitivity risks"],
            "acceptance_criteria": ["assumptions are explicit", "numbers are traceable", "uncertainty is bounded"],
            "consumers": ["main_thread_ceo", "strategy", "business"],
            "forbidden_scope": ["feature specification", "technical implementation", "unrelated financial modeling"],
            "dependencies": ["business outcome", "budget evidence"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "architecture": {
            "responsibility": "Choose the smallest maintainable boundary and prevent premature generalization.",
            "decision_authority": "Define component boundaries and interfaces needed by current acceptance only.",
            "deliverables": ["minimal component boundary", "interface constraints", "abstractions to defer"],
            "acceptance_criteria": ["one-use abstractions are challenged", "dependencies are explicit", "current acceptance remains implementable"],
            "consumers": ["main_thread_ceo", "engineering", "algorithm"],
            "forbidden_scope": ["generic platform design", "future provider ecosystem", "implementation ownership"],
            "dependencies": ["product behavior", "existing code boundaries"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "algorithm": {
            "responsibility": "Select the smallest algorithmic method, data contract, and measurable quality target.",
            "decision_authority": "Choose method and evaluation metric inside the ticket; may not expand into a research program.",
            "deliverables": ["algorithm choice", "input/output contract", "evaluation metric"],
            "acceptance_criteria": ["baseline is stated", "metric is machine-checkable", "method fits ticket budget"],
            "consumers": ["main_thread_ceo", "engineering", "qa", "data"],
            "forbidden_scope": ["open-ended research", "unbounded model exploration", "unrequested data platform"],
            "dependencies": ["product behavior", "available evidence"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "data": {
            "responsibility": "Define the minimum trustworthy data inputs, transformations, and evidence quality checks.",
            "decision_authority": "Set data-quality thresholds and lineage requirements for the ticket.",
            "deliverables": ["data contract", "quality checks", "lineage notes"],
            "acceptance_criteria": ["required fields are explicit", "quality failures are testable", "lineage is traceable"],
            "consumers": ["algorithm", "engineering", "qa"],
            "forbidden_scope": ["enterprise data lake", "unrelated analytics", "future data collection"],
            "dependencies": ["algorithm input contract", "available data"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "design": {
            "responsibility": "Define the smallest usable interaction and visual state set required by product behavior.",
            "decision_authority": "Choose interaction hierarchy and states inside the bounded flow.",
            "deliverables": ["interaction flow", "required states", "visual constraints"],
            "acceptance_criteria": ["all acceptance states are represented", "flow is operable", "no decorative feature expansion"],
            "consumers": ["product", "engineering", "qa"],
            "forbidden_scope": ["marketing site expansion", "unrequested design system", "future screens"],
            "dependencies": ["product behavior", "target user context"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "engineering": {
            "responsibility": "Implement the bounded ticket and machine acceptance inside allowed paths.",
            "decision_authority": "Choose local implementation details that preserve frozen behavior and architecture constraints.",
            "deliverables": ["minimal product patch", "machine-checkable evidence", "implementation notes only when needed"],
            "acceptance_criteria": ["validation passes", "allowed paths are respected", "no acceptance-free abstraction is added"],
            "consumers": ["main_thread_ceo", "qa", "auditor"],
            "forbidden_scope": ["acceptance changes", "unrelated refactors", "future-stage implementation"],
            "dependencies": ["frozen ticket", "relevant role constraints"],
            "workspace_access": "allowed_paths_writer",
            "phase": "execution",
        },
        "qa": {
            "responsibility": "Define or verify machine acceptance without widening the product contract.",
            "decision_authority": "Select verification evidence and identify acceptance gaps; may not redefine desired behavior.",
            "deliverables": ["validation evidence", "acceptance gaps", "failure reproduction"],
            "acceptance_criteria": ["evidence is machine-checkable", "failure is reproducible", "coverage maps to frozen acceptance"],
            "consumers": ["main_thread_ceo", "engineering", "auditor"],
            "forbidden_scope": ["new product requirements", "broad regression programs unrelated to the ticket"],
            "dependencies": ["frozen acceptance", "implementation artifact"],
            "workspace_access": "read_only",
            "phase": "verification",
        },
        "operations": {
            "responsibility": "Define the smallest deployable or operable handoff for the bounded result.",
            "decision_authority": "Set runbook and operational readiness requirements inside the ticket.",
            "deliverables": ["operational handoff", "failure response", "run instructions"],
            "acceptance_criteria": ["handoff is runnable", "failure path is explicit", "no platform expansion is introduced"],
            "consumers": ["main_thread_ceo", "engineering", "qa"],
            "forbidden_scope": ["enterprise operations platform", "unrequested deployment topology"],
            "dependencies": ["implementation artifact", "target runtime"],
            "workspace_access": "read_only",
            "phase": "verification",
        },
        "manufacturing": {
            "responsibility": "Translate product requirements into one executable manufacturing process slice.",
            "decision_authority": "Set process steps and controllable parameters for the bounded production outcome.",
            "deliverables": ["process flow", "critical process parameters", "operator handoff"],
            "acceptance_criteria": ["process is executable", "parameters are measurable", "lot outcome is traceable"],
            "consumers": ["main_thread_ceo", "quality", "operations", "engineering"],
            "forbidden_scope": ["factory-wide transformation", "unrequested equipment program", "future product families"],
            "dependencies": ["product requirement", "material and equipment constraints"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "materials": {
            "responsibility": "Define only the material properties and compatibility evidence required by the ticket.",
            "decision_authority": "Set material constraints and required evidence for the bounded use case.",
            "deliverables": ["material requirement", "compatibility evidence", "material risks"],
            "acceptance_criteria": ["properties are measurable", "evidence fits the use case", "alternatives are bounded"],
            "consumers": ["manufacturing", "quality", "engineering"],
            "forbidden_scope": ["open-ended material research", "unrequested supplier qualification"],
            "dependencies": ["product use conditions", "process constraints"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "quality": {
            "responsibility": "Define measurable quality evidence and release boundaries for the current production slice.",
            "decision_authority": "Set quality checks and release evidence; may not expand into a company-wide quality system.",
            "deliverables": ["quality checks", "release evidence", "nonconformance boundary"],
            "acceptance_criteria": ["checks are measurable", "evidence is traceable", "release decision inputs are complete"],
            "consumers": ["main_thread_ceo", "manufacturing", "operations", "auditor"],
            "forbidden_scope": ["generic compliance framework", "unrequested certification program"],
            "dependencies": ["process flow", "acceptance and validation evidence"],
            "workspace_access": "read_only",
            "phase": "verification",
        },
        "security_legal": {
            "responsibility": "Identify only material legal, permission, privacy, or safety constraints that can block this ticket.",
            "decision_authority": "State hard constraints and a minimal compliant path; may not build a security or compliance platform.",
            "deliverables": ["material constraints", "minimal mitigation", "unresolved blocker"],
            "acceptance_criteria": ["constraint is relevant to the ticket", "mitigation is minimal", "future programs go to backlog"],
            "consumers": ["main_thread_ceo", "product", "engineering", "auditor"],
            "forbidden_scope": ["full RBAC", "policy DSL", "generic security gateway", "compliance framework"],
            "dependencies": ["ticket risk surface", "authoritative requirements"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "scope_cost": {
            "responsibility": "Check scope anchor, coordination cost, same-axis fatigue, and smaller alternatives.",
            "decision_authority": "Recommend compression, split, or backlog routing; may not add departments by itself.",
            "deliverables": ["scope risks", "cost signal", "smaller path"],
            "acceptance_criteria": ["risks cite ticket evidence", "smaller path is actionable", "new ideas are routed"],
            "consumers": ["main_thread_ceo", "custodian"],
            "forbidden_scope": ["new workstreams", "role proliferation", "acceptance changes"],
            "dependencies": ["ticket contract", "current execution state"],
            "workspace_access": "read_only",
            "phase": "verification",
        },
        "custodian": {
            "responsibility": "Route new requests to current scope, simplified intent, backlog, split, or rejection.",
            "decision_authority": "Classify requests against North Star and frozen acceptance; may not activate backlog work.",
            "deliverables": ["request classification", "minimal accepted intent", "backlog items"],
            "acceptance_criteria": ["verdict cites scope evidence", "heavy scope is not accepted whole", "current acceptance stays frozen"],
            "consumers": ["main_thread_ceo", "product", "scope_cost"],
            "forbidden_scope": ["implementation", "acceptance mutation", "department expansion"],
            "dependencies": ["North Star", "current ticket", "incoming request"],
            "workspace_access": "read_only",
            "phase": "planning",
        },
        "janitor": {
            "responsibility": "Classify noise and quarantine candidates without deleting product files.",
            "decision_authority": "Mark keep, simplify, backlog, or quarantine candidates; has no delete authority.",
            "deliverables": ["artifact classifications", "quarantine candidates", "simplify candidates"],
            "acceptance_criteria": ["protected evidence wins", "negative evidence is explicit", "no file is deleted"],
            "consumers": ["main_thread_ceo", "auditor"],
            "forbidden_scope": ["file deletion", "North Star redefinition", "automatic cleanup execution"],
            "dependencies": ["North Star", "ticket acceptance", "repository inventory"],
            "workspace_access": "read_only",
            "phase": "verification",
        },
        "auditor": {
            "responsibility": "Independently verify acceptance, validation, drift, budget, and cleanup evidence before close.",
            "decision_authority": "Report blocking evidence and required action; may not rewrite the ticket or implement fixes.",
            "deliverables": ["audit findings", "blocking evidence", "required next action"],
            "acceptance_criteria": ["findings are evidence-linked", "validation state is accurate", "scope violations are surfaced"],
            "consumers": ["main_thread_ceo"],
            "forbidden_scope": ["implementation", "acceptance mutation", "role signoff chains"],
            "dependencies": ["validation results", "janitor output", "ticket budget state"],
            "workspace_access": "read_only",
            "phase": "verification",
        },
    }


def company_model_profiles() -> dict[str, dict[str, dict[str, str]]]:
    sol_reasoning = {
        "minimum": {"model": "gpt-5.6-terra", "effort": "high"},
        "recommended": {"model": "gpt-5.6-sol", "effort": "max"},
        "maximum": {"model": "gpt-5.6-sol", "effort": "max"},
    }
    terra_execution = {
        "minimum": {"model": "gpt-5.6-terra", "effort": "high"},
        "recommended": {"model": "gpt-5.6-terra", "effort": "max"},
        "maximum": {"model": "gpt-5.6-terra", "effort": "max"},
    }
    luna_evidence = {
        "minimum": {"model": "gpt-5.6-luna", "effort": "high"},
        "recommended": {"model": "gpt-5.6-luna", "effort": "max"},
        "maximum": {"model": "gpt-5.6-terra", "effort": "max"},
    }
    profiles = {role: dict(sol_reasoning) for role in company_department_specs()}
    for role in ["engineering", "algorithm", "data", "manufacturing", "materials"]:
        profiles[role] = dict(terra_execution)
    for role in ["qa", "operations", "quality", "auditor"]:
        profiles[role] = dict(luna_evidence)
    profiles["strategy"] = {
        "minimum": {"model": "gpt-5.6-sol", "effort": "max"},
        "recommended": {"model": "gpt-5.6-sol", "effort": "max"},
        "maximum": {"model": "gpt-5.6-sol", "effort": "max"},
    }
    profiles["architecture"] = dict(profiles["strategy"])
    profiles["janitor"] = {
        "minimum": {"model": "gpt-5.6-terra", "effort": "high"},
        "recommended": {"model": "gpt-5.6-sol", "effort": "max"},
        "maximum": {"model": "gpt-5.6-sol", "effort": "max"},
    }
    return profiles


def company_model_route(department: str, depth: str, supplied: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = supplied or company_model_profiles().get(department) or {
        "minimum": {"model": "gpt-5.6-terra", "effort": "high"},
        "recommended": {"model": "gpt-5.6-terra", "effort": "max"},
        "maximum": {"model": "gpt-5.6-sol", "effort": "max"},
    }
    selected_key = "minimum" if depth == "D0_ROUTINE" else "recommended" if depth == "D1_STANDARD" else "maximum"
    selected = dict(profile.get(selected_key) or profile.get("recommended") or profile.get("minimum") or {})
    return {
        "model_range": profile,
        "effort_range": {
            key: str((profile.get(key) or {}).get("effort") or "max")
            for key in ["minimum", "recommended", "maximum"]
        },
        "selected_for_depth": selected_key,
        "preferred_model": str(selected.get("model") or "gpt-5.6-terra"),
        "reasoning_effort": str(selected.get("effort") or "max").lower(),
    }


def batch_execution_fingerprint(config: dict[str, Any]) -> str:
    payload = {
        "kind": config.get("kind"),
        "item_count": config.get("item_count"),
        "requested_workers": config.get("requested_workers"),
        "expected_output_files": config.get("expected_output_files"),
        "output_paths": config.get("output_paths"),
        "validation_ids": config.get("validation_ids"),
        "independence_evidence": config.get("independence_evidence"),
        "merge_strategy": config.get("merge_strategy"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def batch_execution_policy(ticket: dict[str, Any]) -> dict[str, Any]:
    raw = ticket.get("batch_execution", {})
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return {
            "enabled": False,
            "status": "NOT_REQUESTED",
            "no_protocol_worker_cap": True,
        }
    config = dict(raw)
    kind = str(config.get("kind") or "").strip()
    item_count = int(config.get("item_count", 0) or 0)
    requested_workers = int(config.get("requested_workers", 0) or 0)
    expected_output_files = int(config.get("expected_output_files", item_count) or 0)
    output_paths = [str(value).strip() for value in config.get("output_paths", []) if str(value).strip()]
    validation_ids = [str(value).strip() for value in config.get("validation_ids", []) if str(value).strip()]
    independence = str(config.get("independence_evidence") or "").strip()
    merge_strategy = str(config.get("merge_strategy") or "").strip()
    errors: list[str] = []
    if kind not in BATCH_EXECUTION_KINDS:
        errors.append("batch_execution.kind must declare an independent annotation/labeling workload")
    if item_count <= 0:
        errors.append("batch_execution.item_count must be greater than zero")
    if requested_workers <= 0:
        errors.append("batch_execution.requested_workers must be greater than zero")
    if item_count > 0 and requested_workers > item_count:
        errors.append("batch_execution.requested_workers cannot exceed item_count")
    if expected_output_files <= 0:
        errors.append("batch_execution.expected_output_files must be greater than zero")
    if not output_paths:
        errors.append("batch_execution.output_paths must name the isolated output scope")
    if not validation_ids:
        errors.append("batch_execution.validation_ids must name the merge/quality validation")
    if not independence:
        errors.append("batch_execution.independence_evidence must explain why items are independently writable")
    if not merge_strategy:
        errors.append("batch_execution.merge_strategy must define deterministic merge and conflict handling")
    fingerprint = batch_execution_fingerprint(config)
    confirmation = config.get("ceo_confirmation", {}) if isinstance(config.get("ceo_confirmation"), dict) else {}
    confirmed = (
        not errors
        and confirmation.get("status") == "CONFIRMED"
        and confirmation.get("decision") == "EXPAND_BATCH_WORKFORCE"
        and confirmation.get("confirmed_by") == "main_thread_ceo"
        and confirmation.get("worker_count") == requested_workers
        and confirmation.get("contract_fingerprint") == fingerprint
        and company_confirmation_text_is_concrete(confirmation.get("reason"))
    )
    return {
        "enabled": True,
        "status": "CONFIRMED" if confirmed else "INVALID" if errors else "CEO_CONFIRMATION_REQUIRED",
        "errors": errors,
        "kind": kind,
        "item_count": item_count,
        "worker_count": requested_workers,
        "expected_output_files": expected_output_files,
        "output_paths": output_paths,
        "validation_ids": validation_ids,
        "worker_profile": {"model": "gpt-5.6-luna", "effort": "high"},
        "worker_fallback_profile": {"model": "gpt-5.6-terra", "effort": "high"},
        "shared_worker_contract": True,
        "per_worker_company_receipt_required": False,
        "no_protocol_worker_cap": True,
        "parallelism_policy": "use all runtime capacity up to the CEO-confirmed worker count",
        "contract_fingerprint": fingerprint,
        "ceo_confirmation": {
            "required": True,
            "status": "CONFIRMED" if confirmed else "PENDING",
            "confirmation_template": None if confirmed else {
                "status": "CONFIRMED",
                "decision": "EXPAND_BATCH_WORKFORCE",
                "confirmed_by": "main_thread_ceo",
                "worker_count": requested_workers,
                "reason": "",
                "contract_fingerprint": fingerprint,
            },
        },
    }


def refresh_batch_execution(ticket: dict[str, Any]) -> dict[str, Any]:
    policy = batch_execution_policy(ticket)
    if not policy.get("enabled"):
        return ticket
    config = ticket.setdefault("batch_execution", {})
    config["policy"] = policy
    expected_files = int(policy.get("expected_output_files", 0) or 0)
    if expected_files > 0:
        file_allowance = expected_files + max(5, (expected_files + 19) // 20)
        budget = ticket.setdefault("budget", {})
        acceptance = ticket.setdefault("acceptance", {})
        budget["max_changed_files"] = max(int(budget.get("max_changed_files", 0) or 0), file_allowance)
        acceptance["max_changed_files"] = max(int(acceptance.get("max_changed_files", 0) or 0), file_allowance)
        budget_basis = ticket.setdefault("budget_basis", {})
        budget_basis["tier"] = "BATCH_VOLUME"
        budget_basis["batch_expected_output_files"] = expected_files
        budget_basis["adjustment_rule"] = "Derived from the explicit batch output volume before acceptance freeze."
    return ticket


def batch_execution_errors(ticket: dict[str, Any]) -> list[str]:
    policy = batch_execution_policy(ticket)
    if not policy.get("enabled"):
        return []
    errors = list(policy.get("errors", []))
    if policy.get("status") != "CONFIRMED":
        errors.append("batch workforce requires main_thread_ceo confirmation for the exact worker count and contract")
    validation_ids = set(str(value) for value in ticket.get("validation_ids", []))
    missing_validation = [value for value in policy.get("validation_ids", []) if value not in validation_ids]
    if missing_validation:
        errors.append("batch validation ids must also be present in ticket.validation_ids: " + ", ".join(missing_validation))
    return errors


def company_delegation_need(ticket: dict[str, Any], complexity: dict[str, Any]) -> dict[str, Any]:
    text = company_policy_core_text(ticket).lower()
    budget = ticket.get("budget", {}) if isinstance(ticket.get("budget"), dict) else {}
    max_minutes = int(budget.get("max_minutes", 0) or 0)
    max_tool_calls = int(budget.get("max_tool_calls", 0) or 0)
    max_changed_files = int(budget.get("max_changed_files", 0) or 0)
    max_diff_lines = int(budget.get("max_diff_lines", 0) or 0)
    allowed_count = len(ticket_writable_paths(ticket))
    read_only_terms = [
        "status only", "read-only inspection", "inspect status", "report current status",
        "summarize existing", "no product edits", "goal check only", "request classification only",
        "仅查看状态", "只读检查", "仅汇总现状", "不修改产品", "无需修改", "只做状态检查",
    ]
    specialist_terms = [
        "strategy", "tradeoff", "architecture", "commercial", "revenue", "pricing",
        "algorithm", "model training", "security", "permission", "legal", "compliance",
        "manufacturing", "packaging", "quality release", "战略", "权衡", "架构", "商业",
        "营收", "定价", "算法", "安全", "权限", "法务", "合规", "制造", "包装", "质量放行",
    ]
    supervision = ticket.get("supervision") if isinstance(ticket.get("supervision"), dict) else supervision_decision(ticket)
    explicit_main_thread_only = any(term in text for term in read_only_terms)
    specialized_judgment = any(term in text for term in specialist_terms)
    narrow_action_terms = [
        "one ", "single ", "rename", "literal", "assertion", "existing module", "existing file",
        "一个", "单个", "重命名", "字面量", "断言", "现有模块", "现有文件",
    ]
    narrow_direct_action = (
        not specialized_judgment
        and 0 < max_minutes <= 30
        and 0 < max_tool_calls <= 40
        and 0 < max_changed_files <= 5
        and 0 < max_diff_lines <= 300
        and allowed_count <= 3
        and (
            complexity.get("depth") == "D0_ROUTINE"
            or any(term in text for term in narrow_action_terms)
        )
    )
    if explicit_main_thread_only:
        return {
            "required": False,
            "reason": "The ticket is an explicit read-only or status-only main-thread action with no independent department deliverable.",
            "signal": "MAIN_THREAD_ONLY_READ_ONLY",
        }
    if supervision.get("level") in {"NONE", "LIGHT"} or narrow_direct_action:
        return {
            "required": False,
            "reason": "The ticket is a narrow low-risk action whose company coordination cost is unlikely to produce net execution benefit.",
            "signal": "MAIN_THREAD_ONLY_BOUNDED_ACTION",
        }
    if supervision.get("level") == "STANDARD" and complexity.get("breadth") == "B1_FOCUSED" and not specialized_judgment:
        return {
            "required": False,
            "reason": "The ticket is focused and has no independent specialist deliverable beyond the main executor.",
            "signal": "MAIN_THREAD_ONLY_FOCUSED_ACTION",
        }
    return {
        "required": True,
        "reason": "The ticket has an executable or specialist deliverable that benefits from at least one accountable department.",
        "signal": "DEPARTMENT_WORK_REQUIRED",
    }


def company_auto_departments(
    ticket: dict[str, Any],
    complexity: dict[str, Any],
) -> tuple[list[str], dict[str, list[str]], dict[str, Any]]:
    text = company_policy_core_text(ticket).lower()
    selected: list[str] = []
    evidence: dict[str, list[str]] = {}
    delegation = company_delegation_need(ticket, complexity)
    if not delegation["required"]:
        return [], {"main_thread_only": [delegation["reason"]]}, delegation

    def has(*terms: str) -> bool:
        return any(term in text for term in terms)

    def add(role: str, reason: str) -> None:
        evidence.setdefault(role, []).append(reason)
        if role not in selected and len(selected) < COMPANY_AUTO_DEPARTMENT_LIMIT:
            selected.append(role)

    commercial = has("commercial", "business opportunity", "revenue", "pricing", "market entry", "商业机会", "业务机会", "营收", "定价", "市场进入")
    algorithmic = has("algorithm", "model training", "inference", "optimization", "ranking", "算法", "模型训练", "推理", "排序", "优化")
    manufacturing = has("manufacturing", "packaging", "production line", "material", "lot release", "制造", "包装", "产线", "材料", "批次放行")
    cleanup = has("prune", "cleanup", "noise", "dead code", "quarantine", "清理", "噪音", "隔离", "屎山")
    visual = has(" ui ", "ux", "interface design", "interaction", "dashboard", "网页", "界面", "交互", "设计")
    strategic = has("strategy", "roadmap", "tradeoff", "choose between", "战略", "路线", "权衡")
    architectural = has("architecture", "cross-system", "cross system", "migration", "boundary", "架构", "跨系统", "迁移", "边界")
    risky = has("security", "permission", "privacy", "legal", "compliance", "safety-critical", "安全", "权限", "隐私", "法务", "合规")
    validation = has("test", "assertion", "validation", "acceptance", "verify", "测试", "断言", "验证", "验收")

    if commercial:
        for role in ["strategy", "business", "product", "finance"]:
            add(role, "commercial outcome requires an independent department deliverable")
    elif algorithmic:
        for role in ["product", "algorithm", "engineering", "qa"]:
            add(role, "algorithmic work requires product, method, implementation, and evidence boundaries")
    elif manufacturing:
        for role in ["manufacturing", "quality", "engineering", "operations"]:
            add(role, "manufacturing work requires executable process, quality evidence, implementation, and handoff")
    elif cleanup:
        for role in ["janitor", "auditor", "custodian"]:
            add(role, "cleanup work needs classification, independent evidence, and scope routing")
    else:
        if strategic:
            add("strategy", "ticket contains a real direction or tradeoff choice")
        if architectural:
            add("architecture", "ticket changes a system boundary or architecture")
        if has("business", "operator", "workflow", "业务", "经营", "运营流程"):
            add("business", "ticket depends on an operating or business outcome")
        if has("product", "user", "customer", "feature", "产品", "用户", "客户", "功能"):
            add("product", "ticket defines user-visible product behavior")
        if visual:
            add("design", "ticket contains an interaction or visual deliverable")
        if risky:
            add("security_legal", "ticket contains a material permission, legal, privacy, or safety constraint")
        if has("implement", "build", "code", "api", "parser", "src/", "实现", "开发", "代码", "接口") or not selected:
            add("engineering", "ticket requires a bounded implementation deliverable")
        if validation and len(selected) < COMPANY_AUTO_DEPARTMENT_LIMIT:
            add("qa", "ticket explicitly requires machine verification")

    if not selected:
        add("engineering", "fallback owner for one bounded executable result")
    return selected, evidence, delegation


def company_department_contract_errors(row: dict[str, Any]) -> list[str]:
    role = str(row.get("role") or "<unknown>")
    errors: list[str] = []
    for field in COMPANY_DEPARTMENT_CONTRACT_FIELDS:
        value = row.get(field)
        if field in {"required_inputs", "deliverables", "acceptance_criteria", "consumers", "forbidden_scope", "dependencies"}:
            if not isinstance(value, list) or not value or not all(str(item).strip() for item in value):
                errors.append(f"department {role} requires non-empty {field}")
        elif field in {"model_range", "effort_range"}:
            if not isinstance(value, dict) or not all(key in value for key in ["minimum", "recommended", "maximum"]):
                errors.append(f"department {role} requires complete {field}")
        elif not str(value or "").strip():
            errors.append(f"department {role} requires {field}")
    if not str(row.get("join_reason") or "").strip():
        errors.append(f"department {role} requires join_reason")
    if row.get("workspace_access") not in {"read_only", "allowed_paths_writer"}:
        errors.append(f"department {role} has unsupported workspace_access")
    if row.get("phase") not in {"planning", "execution", "verification"}:
        errors.append(f"department {role} has unsupported phase")
    preferred_model = str(row.get("preferred_model") or "")
    reasoning_effort = str(row.get("reasoning_effort") or "").lower()
    if reasoning_effort not in COMPANY_ALLOWED_MODEL_EFFORTS.get(preferred_model, set()):
        errors.append(f"department {role} must use Sol Max, Terra High/Max, or Luna High/Max")
    model_range = row.get("model_range", {})
    if isinstance(model_range, dict):
        for level in ["minimum", "recommended", "maximum"]:
            endpoint = model_range.get(level)
            if not isinstance(endpoint, dict) or not endpoint.get("model") or not endpoint.get("effort"):
                errors.append(f"department {role} model_range.{level} requires model and effort")
            elif str(endpoint.get("effort")).lower() not in COMPANY_ALLOWED_MODEL_EFFORTS.get(str(endpoint.get("model")), set()):
                errors.append(f"department {role} model_range may contain only Sol Max, Terra High/Max, or Luna High/Max")
    effort_range = row.get("effort_range", {})
    if isinstance(effort_range, dict) and any(str(value).lower() not in {"high", "max"} for value in effort_range.values()):
        errors.append(f"department {role} effort_range may contain only High or Max")
    return errors


def company_role_contract_fingerprint(row: dict[str, Any]) -> str:
    payload = {
        key: row.get(key)
        for key in [
            "role", "responsibility", "decision_authority", "required_inputs", "deliverables",
            "acceptance_criteria", "consumers", "forbidden_scope", "dependencies", "stop_condition",
            "join_reason", "preferred_model", "reasoning_effort", "model_range", "effort_range",
            "phase", "workspace_access",
        ]
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def company_subagent_role(
    role: str,
    spec: dict[str, Any],
    ticket: dict[str, Any],
    complexity: dict[str, Any],
    join_reason: str,
) -> dict[str, Any]:
    route = company_model_route(role, complexity["depth"], spec.get("model_range"))
    effort = route["reasoning_effort"]
    task_goal = str(ticket.get("task_goal") or ticket.get("title") or "current bounded ticket")
    row = {
        "role": role,
        "required": True,
        "objective": f"{spec['responsibility']} Ticket: {task_goal}",
        "responsibility": spec["responsibility"],
        "decision_authority": spec["decision_authority"],
        "required_inputs": list(spec.get("required_inputs") or ["confirmed North Star", "current task_goal", "frozen acceptance"]),
        "deliverables": list(spec["deliverables"]),
        "acceptance_criteria": list(spec["acceptance_criteria"]),
        "consumers": list(spec["consumers"]),
        "forbidden_scope": list(spec["forbidden_scope"]),
        "dependencies": list(spec["dependencies"]),
        "stop_condition": str(spec.get("stop_condition") or "Return the structured deliverables once, hand off to listed consumers, then exit."),
        "join_reason": join_reason,
        "coordination_value": "Owns a distinct deliverable that would otherwise be uncovered; must reduce rework more than it adds coordination.",
        "preferred_model": route["preferred_model"],
        "reasoning_effort": effort,
        "ui_effort_label": effort.title(),
        "model_range": route["model_range"],
        "effort_range": route["effort_range"],
        "selected_for_depth": route["selected_for_depth"],
        "workspace_access": spec["workspace_access"],
        "phase": spec["phase"],
        "fallback": {
            "model": "current_parent_model",
            "reasoning_effort": effort,
        },
        "fallback_models": [
            route["model_range"][level]
            for level in ("recommended", "minimum", "maximum")
            if isinstance(route["model_range"].get(level), dict)
        ],
        "spawn_contract": {
            "fork_context": False,
            "close_after_deliverable": True,
            "one_structured_result": True,
        },
        "output_policy": "concise_structured_deliverable_only",
        "release_after_deliverable": True,
    }
    return row


def company_role_from_department_spec(
    value: Any,
    ticket: dict[str, Any],
    complexity: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    auto_evidence: dict[str, list[str]],
) -> tuple[dict[str, Any] | None, list[str]]:
    name = str(value.get("name") or value.get("role") or "").strip() if isinstance(value, dict) else str(value or "").strip()
    if not name:
        return None, ["department name is empty"]
    canonical = name.lower()
    supplied = value if isinstance(value, dict) else {}
    if canonical in catalog:
        spec = dict(catalog[canonical])
        for field in COMPANY_DEPARTMENT_CONTRACT_FIELDS + ["workspace_access", "phase"]:
            if field in supplied:
                spec[field] = supplied[field]
        task_goal = str(ticket.get("task_goal") or ticket.get("title") or "the current bounded ticket")
        generated_reason = (
            f"{canonical} owns {spec['deliverables'][0]} for {task_goal}; "
            f"the result is consumed by {spec['consumers'][0]}."
        )
        join_reason = str(supplied.get("join_reason") or "; ".join(auto_evidence.get(canonical, [])) or generated_reason)
        row = company_subagent_role(canonical, spec, ticket, complexity, join_reason)
        if supplied.get("preferred_model"):
            row["preferred_model"] = str(supplied["preferred_model"])
        if supplied.get("reasoning_effort"):
            effort = str(supplied["reasoning_effort"]).lower()
            if effort not in {"high", "max"}:
                return None, [f"department {name} has unsupported reasoning_effort: {effort}; company departments require High or Max"]
            row["reasoning_effort"] = effort
            row["ui_effort_label"] = effort.title()
    else:
        if not isinstance(value, dict):
            return None, [f"custom department {name} requires a structured department contract"]
        missing = [field for field in COMPANY_DEPARTMENT_CONTRACT_FIELDS + ["workspace_access", "phase", "join_reason"] if not value.get(field)]
        if missing:
            return None, [f"custom department {name} is missing contract fields: {', '.join(missing)}"]
        spec = {field: value[field] for field in COMPANY_DEPARTMENT_CONTRACT_FIELDS + ["workspace_access", "phase"]}
        row = company_subagent_role(name, spec, ticket, complexity, str(value["join_reason"]))
        if value.get("preferred_model"):
            row["preferred_model"] = str(value["preferred_model"])
        if value.get("reasoning_effort"):
            effort = str(value["reasoning_effort"]).lower()
            if effort not in {"high", "max"}:
                return None, [f"department {name} has unsupported reasoning_effort: {effort}; company departments require High or Max"]
            row["reasoning_effort"] = effort
            row["ui_effort_label"] = effort.title()
    return row, company_department_contract_errors(row)


def company_confirmation_text_is_concrete(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) >= 12 and not text.lower().startswith(("explain ", "describe ", "todo", "tbd"))


def company_subagent_policy(ticket: dict[str, Any]) -> dict[str, Any]:
    complexity = company_complexity(ticket)
    catalog = company_department_specs()
    auto_departments, auto_evidence, delegation = company_auto_departments(ticket, complexity)
    requested_raw = ticket.get("requested_company_departments", [])
    requested = requested_raw if isinstance(requested_raw, list) else []
    if requested:
        delegation = {
            "required": True,
            "reason": "The DRAFT explicitly requests a department roster; every requested department must satisfy its contract.",
            "signal": "EXPLICIT_DEPARTMENT_ROSTER",
        }
    selected_specs: list[Any] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for value in requested or auto_departments:
        name = str(value.get("name") or value.get("role") or "").strip() if isinstance(value, dict) else str(value or "").strip()
        if not name:
            invalid.append("department name is empty")
            continue
        key = name.casefold()
        if key not in seen:
            selected_specs.append(value)
            seen.add(key)

    roles: list[dict[str, Any]] = []
    for value in selected_specs:
        row, errors = company_role_from_department_spec(value, ticket, complexity, catalog, auto_evidence)
        invalid.extend(errors)
        if row and not errors:
            row["contract_fingerprint"] = company_role_contract_fingerprint(row)
            roles.append(row)

    roster_payload = [
        {
            key: row.get(key)
            for key in [
                "role", "responsibility", "decision_authority", "required_inputs", "deliverables",
                "acceptance_criteria", "consumers", "forbidden_scope", "dependencies", "stop_condition",
                "join_reason", "preferred_model", "reasoning_effort", "model_range", "effort_range",
                "phase", "workspace_access",
            ]
        }
        for row in roles
    ]
    roster_fingerprint = hashlib.sha256(
        json.dumps(roster_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    confirmation_input = ticket.get("company_ceo_confirmation", {})
    if not isinstance(confirmation_input, dict):
        confirmation_input = {}
    confirmation_required = len(selected_specs) > COMPANY_AUTO_DEPARTMENT_LIMIT
    added_departments = [row["role"] for row in roles[COMPANY_AUTO_DEPARTMENT_LIMIT:]]
    confirmation_valid = (
        confirmation_required
        and not invalid
        and confirmation_input.get("status") == "CONFIRMED"
        and confirmation_input.get("decision") == "EXPAND"
        and confirmation_input.get("confirmed_by") == "main_thread_ceo"
        and company_confirmation_text_is_concrete(confirmation_input.get("reason"))
        and company_confirmation_text_is_concrete(confirmation_input.get("why_current_team_is_insufficient"))
        and company_confirmation_text_is_concrete(confirmation_input.get("expected_execution_gain"))
        and company_confirmation_text_is_concrete(confirmation_input.get("coordination_cost_control"))
        and confirmation_input.get("added_departments") == added_departments
        and confirmation_input.get("expansion_batch_size") == len(added_departments)
        and confirmation_input.get("roster_fingerprint") == roster_fingerprint
    )
    if not confirmation_required:
        confirmation_status = "NOT_REQUIRED"
    elif confirmation_valid:
        confirmation_status = "CONFIRMED"
    elif confirmation_input.get("decision") == "EXPAND":
        confirmation_status = "PENDING"
    else:
        confirmation_status = "KEEP_CURRENT"
    subagents_recommended = bool(selected_specs)
    parallel = min(COMPANY_AUTO_DEPARTMENT_LIMIT, len(roles)) if roles else 0
    if invalid:
        plan_status = "CONTRACT_INCOMPLETE"
    elif confirmation_required and not confirmation_valid:
        plan_status = "CEO_CONFIRMATION_REQUIRED"
    elif not subagents_recommended:
        plan_status = "NO_SUBAGENT_NEEDED"
    else:
        plan_status = "SUBAGENT_RECOMMENDED"

    batch_workforce = batch_execution_policy(ticket)
    return {
        "policy_version": COMPANY_SUBAGENT_POLICY_VERSION,
        "mandatory": False,
        "recommended": subagents_recommended,
        "invocation": "AI_OPTIONAL",
        "plan_status": plan_status,
        "delegation_decision": delegation,
        "complexity": complexity,
        "complexity_tier": complexity["tier"],
        "task_depth": complexity["depth"],
        "task_breadth": complexity["breadth"],
        "required_subagents": roles,
        "role_contract_fingerprints": {
            str(row["role"]): str(row["contract_fingerprint"])
            for row in roles
        },
        "available_departments": sorted(catalog),
        "activated_departments": [row["role"] for row in roles],
        "automatic_department_limit": COMPANY_AUTO_DEPARTMENT_LIMIT,
        "department_selection_source": "ticket_override" if requested else "task_driven_auto",
        "selection_evidence": auto_evidence if not requested else {},
        "invalid_requested_departments": invalid,
        "requested_department_count": len(selected_specs),
        "min_subagents": len(roles),
        "max_subagents": len(roles),
        "planned_subagents": len(roles),
        "department_capacity": "unbounded_by_protocol",
        "department_contract_required": True,
        "department_contract_fields": COMPANY_DEPARTMENT_CONTRACT_FIELDS,
        "dispatch": {
            "total_subagents": len(roles),
            "max_parallel_per_wave": parallel,
            "wave_count": (len(roles) + parallel - 1) // parallel if roles else 0,
            "all_selected_departments_must_run": False,
            "structured_outputs_only": True,
            "release_after_deliverable": True,
            "child_agents_may_expand_roster": False,
            "reserve_qa_capacity": any(row.get("role") == "qa" for row in roles),
            "explicit_model_spawn_uses_fork_context": False,
            "batch_worker_count": batch_workforce.get("worker_count", 0) if batch_workforce.get("status") == "CONFIRMED" else 0,
            "batch_max_parallel_per_wave": batch_workforce.get("worker_count", 0) if batch_workforce.get("status") == "CONFIRMED" else 0,
        },
        "batch_workforce": batch_workforce,
        "expansion_policy": {
            "default_decision": "KEEP_CURRENT",
            "automatic_range": "0-4 task-driven departments",
            "confirmation_required_above": COMPANY_AUTO_DEPARTMENT_LIMIT,
            "no_protocol_department_cap": True,
            "preferred_increment": "add one or two independent departments at a time",
            "bulk_expansion_condition": "independent workstreams and complete department contracts already exist",
            "new_department_test": "must improve execution or reduce rework more than its coordination cost",
        },
        "ceo_confirmation": {
            "required": confirmation_required,
            "required_above_subagent_count": COMPANY_AUTO_DEPARTMENT_LIMIT,
            "status": confirmation_status,
            "decision": "EXPAND" if confirmation_valid else "KEEP_CURRENT",
            "confirmed_by": confirmation_input.get("confirmed_by") if confirmation_valid else None,
            "reason": confirmation_input.get("reason") if confirmation_valid else None,
            "confirmed_at": confirmation_input.get("confirmed_at") if confirmation_valid else None,
            "roster_fingerprint": roster_fingerprint,
            "confirmation_template": {
                "status": "PENDING",
                "decision": "KEEP_CURRENT",
                "confirmed_by": "main_thread_ceo",
                "reason": "",
                "why_current_team_is_insufficient": "",
                "expected_execution_gain": "",
                "coordination_cost_control": "",
                "added_departments": added_departments,
                "expansion_batch_size": len(added_departments),
                "roster_fingerprint": roster_fingerprint,
            } if confirmation_required and not confirmation_valid else None,
        },
        "main_thread": {
            "role": "ceo_coordinator_integrator",
            "product_edit_policy": "normal_execution_with_optional_specialists",
            "responsibilities": [
                "freeze the bounded ticket",
                "select only departments with distinct contracts",
                "integrate concise non-overlapping deliverables",
                "run Goal Compass checks and machine acceptance",
                "release departments after their deliverables",
            ],
        },
        "model_routing": {
            "depth_controls_model_effort": True,
            "breadth_controls_department_count": True,
            "department_routes_are_ranges_not_fixed_levels": True,
            "allowed_department_profiles": ["Sol Max", "Terra High", "Terra Max", "Luna High", "Luna Max"],
            "repetitive_batch_profile": "Luna High with Terra High fallback under a confirmed batch_execution contract",
            "ultra": {
                "department_auto_assignment": False,
                "root_ceo_only": True,
                "eligible": bool(complexity.get("root_ultra_eligible")),
                "reason": "Ultra may coordinate its own agents; avoid nesting it inside an already expanded company roster.",
            },
        },
        "runtime_binding": "optional_external_runtime" if subagents_recommended else "not_required",
        "runtime_execution_verified": not subagents_recommended,
        "subagent_spawn_required_before_product_edits": False,
        "company_mode_scope": "root_ticket_only",
        "nested_company_mode": False,
        "max_collaboration_rounds": 1,
        "max_calls_per_role": 1,
        "max_inter_role_chat_rounds": 0,
        "no_inter_role_chat_loop": True,
        "no_role_gate_or_signoff": True,
    }


def company_subagent_policy_errors(ticket: dict[str, Any]) -> list[str]:
    policy = ticket.get("mdcp", {}).get("layer_2_company_subagents", {})
    if not isinstance(policy, dict):
        return ["MDCP company subagent plan is missing"]
    errors: list[str] = []
    errors.extend(batch_execution_errors(ticket))
    roles = policy.get("required_subagents", [])
    subagents_recommended = policy.get("recommended") is True
    if subagents_recommended:
        if policy.get("runtime_binding") != "optional_external_runtime":
            errors.append("recommended MDCP company roles must use optional_external_runtime")
        if not isinstance(roles, list) or not roles:
            errors.append("recommended MDCP company plan must contain at least one department")
    else:
        if policy.get("plan_status") != "NO_SUBAGENT_NEEDED":
            errors.append("zero-department plan must explicitly report NO_SUBAGENT_NEEDED")
        if policy.get("runtime_binding") != "not_required":
            errors.append("zero-department plan must report runtime_binding=not_required")
        if roles:
            errors.append("zero-department plan cannot contain required_subagents")
    if isinstance(roles, list):
        for row in roles:
            if not isinstance(row, dict):
                errors.append("MDCP company subagent role is incomplete")
                break
            errors.extend(company_department_contract_errors(row))
    if subagents_recommended and int(policy.get("min_subagents", 0) or 0) < 1:
        errors.append("recommended MDCP company plan must contain at least one subagent")
    if not subagents_recommended and int(policy.get("min_subagents", 0) or 0) != 0:
        errors.append("zero-department plan must set min_subagents to zero")
    if policy.get("department_selection_source") == "task_driven_auto" and len(roles) > COMPANY_AUTO_DEPARTMENT_LIMIT:
        errors.append("automatic company selection may not exceed four departments")
    if policy.get("department_capacity") != "unbounded_by_protocol":
        errors.append("MDCP company subagent department capacity must remain unbounded_by_protocol")
    if policy.get("invalid_requested_departments"):
        errors.append("MDCP company subagent plan contains invalid departments: " + ", ".join(policy["invalid_requested_departments"]))
    ceo = policy.get("ceo_confirmation", {})
    if isinstance(ceo, dict) and ceo.get("required") and ceo.get("status") != "CONFIRMED":
        errors.append(
            "CEO expansion confirmation required above four departments; default is KEEP_CURRENT and EXPAND requires a concrete necessity, execution gain, and coordination-cost control"
        )
    return list(dict.fromkeys(errors))


def company_subagent_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    policy = ticket.get("mdcp", {}).get("layer_2_company_subagents", {})
    roles = policy.get("required_subagents", []) if isinstance(policy, dict) else []
    strategy = next((row for row in roles if isinstance(row, dict) and row.get("role") == "strategy"), None)
    runtime = ticket.get("company_runtime", {}) if isinstance(ticket.get("company_runtime"), dict) else {}
    receipts = runtime.get("receipts", []) if isinstance(runtime.get("receipts"), list) else []
    required_roles = [str(row.get("role")) for row in roles if isinstance(row, dict) and row.get("role")]
    role_fingerprints = {
        str(row.get("role")): str(row.get("contract_fingerprint") or company_role_contract_fingerprint(row))
        for row in roles if isinstance(row, dict) and row.get("role")
    }
    expected_fingerprint = policy.get("ceo_confirmation", {}).get("roster_fingerprint") if isinstance(policy, dict) else None
    legacy_roster_match = runtime.get("roster_fingerprint") == expected_fingerprint
    role_status: dict[str, dict[str, Any]] = {}
    for role in required_roles:
        role_receipts = [row for row in receipts if isinstance(row, dict) and row.get("role") == role]
        valid_receipts = [
            row for row in role_receipts
            if row.get("role_contract_fingerprint") == role_fingerprints.get(role)
            or (not row.get("role_contract_fingerprint") and legacy_roster_match)
        ]
        invalid_receipts = [row for row in role_receipts if row not in valid_receipts]
        completed = [row for row in valid_receipts if row.get("status") == "COMPLETED" and row.get("result_hash")]
        failed = [row for row in valid_receipts if row.get("status") == "FAILED"]
        started = [row for row in valid_receipts if row.get("status") == "STARTED"]
        latest_failure_class = str(failed[-1].get("failure_class") or "REVIEW_INCOMPLETE") if failed else None
        failure_actions = {
            "PRODUCT_BLOCKER": "return_blocker_to_ticket",
            "REVIEW_INCOMPLETE": "complete_role_review",
            "RUNTIME_FAILURE": "retry_role_runtime",
            "SUPERSEDED": "do_not_retry_superseded_role",
        }
        role_status[role] = {
            "status": "COMPLETED" if completed else latest_failure_class if failed else "STARTED" if started else "NOT_STARTED",
            "attempts": len({str(row.get("agent_id")) for row in valid_receipts if row.get("agent_id")}),
            "completed_agent_id": completed[-1].get("agent_id") if completed else None,
            "result_hash": completed[-1].get("result_hash") if completed else None,
            "failed_attempts": len(failed),
            "latest_failure_class": latest_failure_class,
            "recommended_action": failure_actions.get(latest_failure_class),
            "invalidated_receipts": len(invalid_receipts),
            "contract_fingerprint": role_fingerprints.get(role),
        }
    roster_matches = not required_roles or runtime.get("roster_fingerprint") == expected_fingerprint
    runtime_verified = all(role_status[role]["status"] == "COMPLETED" for role in required_roles)
    preserved_roles = [role for role in required_roles if role_status[role]["status"] == "COMPLETED"]
    invalidated_roles = sorted({
        *[str(role) for role in runtime.get("invalidated_roles", []) if str(role) in required_roles],
        *[role for role in required_roles if role_status[role]["invalidated_receipts"] > 0],
    } - set(preserved_roles))
    return {
        "required": False,
        "recommended": bool(policy.get("recommended")) if isinstance(policy, dict) else False,
        "invocation": policy.get("invocation") if isinstance(policy, dict) else None,
        "plan_status": policy.get("plan_status") if isinstance(policy, dict) else None,
        "delegation_decision": policy.get("delegation_decision") if isinstance(policy, dict) else {},
        "complexity_tier": policy.get("complexity_tier") if isinstance(policy, dict) else None,
        "task_depth": policy.get("task_depth") if isinstance(policy, dict) else None,
        "task_breadth": policy.get("task_breadth") if isinstance(policy, dict) else None,
        "required_roles": required_roles,
        "department_count": len(roles),
        "automatic_department_limit": policy.get("automatic_department_limit") if isinstance(policy, dict) else COMPANY_AUTO_DEPARTMENT_LIMIT,
        "min_subagents": policy.get("min_subagents") if isinstance(policy, dict) else 0,
        "max_subagents": policy.get("max_subagents") if isinstance(policy, dict) else 0,
        "department_capacity": policy.get("department_capacity") if isinstance(policy, dict) else None,
        "department_selection_source": policy.get("department_selection_source") if isinstance(policy, dict) else None,
        "dispatch": policy.get("dispatch") if isinstance(policy, dict) else {},
        "ceo_confirmation": policy.get("ceo_confirmation") if isinstance(policy, dict) else {},
        "model_routes": [
            {
                "role": row.get("role"),
                "preferred_model": row.get("preferred_model"),
                "reasoning_effort": row.get("reasoning_effort"),
                "model_range": row.get("model_range"),
            }
            for row in roles if isinstance(row, dict)
        ],
        "strategy_model": strategy.get("preferred_model") if strategy else None,
        "strategy_effort": strategy.get("reasoning_effort") if strategy else None,
        "runtime_binding": policy.get("runtime_binding") if isinstance(policy, dict) else None,
        "subagent_spawn_required_before_product_edits": bool(policy.get("subagent_spawn_required_before_product_edits")) if isinstance(policy, dict) else False,
        "runtime_execution_verified": runtime_verified,
        "runtime_verification_basis": "completed_role_receipts_with_result_hashes" if runtime_verified else "incomplete_role_receipts",
        "runtime_roster_matches": roster_matches,
        "missing_roles": [role for role in required_roles if role_status[role]["status"] != "COMPLETED"],
        "preserved_roles": preserved_roles,
        "invalidated_roles": invalidated_roles,
        "role_status": role_status,
        "receipt_count": len(receipts),
    }


def compact_company_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    summary = company_subagent_summary(ticket)
    return {
        "required": summary.get("required", False),
        "recommended": summary.get("recommended", False),
        "invocation": summary.get("invocation"),
        "plan_status": summary.get("plan_status"),
        "complexity_tier": summary.get("complexity_tier"),
        "required_roles": summary.get("required_roles", []),
        "min_subagents": summary.get("min_subagents", 0),
        "runtime_binding": summary.get("runtime_binding"),
        "missing_roles": summary.get("missing_roles", []),
        "runtime_execution_verified": summary.get("runtime_execution_verified", True),
        "receipt_count": summary.get("receipt_count", 0),
        "preserved_roles": summary.get("preserved_roles", []),
        "invalidated_roles": summary.get("invalidated_roles", []),
    }


def initialize_company_runtime(ticket: dict[str, Any]) -> None:
    policy = ticket.get("mdcp", {}).get("layer_2_company_subagents", {})
    roles = policy.get("required_subagents", []) if isinstance(policy, dict) else []
    required_roles = [str(row.get("role")) for row in roles if isinstance(row, dict) and row.get("role")]
    roster_fingerprint = policy.get("ceo_confirmation", {}).get("roster_fingerprint") if isinstance(policy, dict) else None
    existing = ticket.get("company_runtime", {}) if isinstance(ticket.get("company_runtime"), dict) else {}
    role_fingerprints = {
        str(row.get("role")): str(row.get("contract_fingerprint") or company_role_contract_fingerprint(row))
        for row in roles if isinstance(row, dict) and row.get("role")
    }
    preserve_legacy = existing.get("roster_fingerprint") == roster_fingerprint
    receipts = []
    invalidated_roles: set[str] = {
        str(role) for role in existing.get("invalidated_roles", []) if str(role) in required_roles
    }
    for receipt in existing.get("receipts", []) if isinstance(existing.get("receipts"), list) else []:
        if not isinstance(receipt, dict):
            continue
        role = str(receipt.get("role") or "")
        current_fingerprint = role_fingerprints.get(role)
        receipt_fingerprint = receipt.get("role_contract_fingerprint")
        if current_fingerprint and (receipt_fingerprint == current_fingerprint or (not receipt_fingerprint and preserve_legacy)):
            receipt = dict(receipt)
            receipt["role_contract_fingerprint"] = current_fingerprint
            receipts.append(receipt)
        elif role:
            invalidated_roles.add(role)
    completed_roles = {
        str(receipt.get("role"))
        for receipt in receipts
        if receipt.get("status") == "COMPLETED" and receipt.get("result_hash")
    }
    invalidated_roles -= completed_roles
    ticket["company_runtime"] = {
        "schema_version": 1,
        "run_id": ticket.get("run_id"),
        "roster_fingerprint": roster_fingerprint,
        "required_roles": required_roles,
        "started_at": existing.get("started_at") or now(),
        "activated_at": now(),
        "receipts": receipts,
        "role_contract_fingerprints": role_fingerprints,
        "invalidated_roles": sorted(invalidated_roles),
    }


@serialized_current_state
def cmd_company_record(args: argparse.Namespace) -> int:
    source_path = Path(args.ticket) if args.ticket else None
    ticket = load_json(source_path, {}) if source_path else active_ticket()
    if not ticket:
        print(json.dumps({"ok": False, "error": "no ACTIVE ticket and no valid --ticket path"}, ensure_ascii=False))
        return 1
    prestart = ticket.get("status") in {"DRAFT", "PENDING"}
    if prestart:
        try:
            source_path.resolve().relative_to(PENDING.resolve())
        except (AttributeError, OSError, ValueError):
            print(json.dumps({"ok": False, "error": "pre-start company receipts may only be stored on .agent/tickets/pending/**"}, ensure_ascii=False))
            return 2
    elif ticket.get("status") != "ACTIVE":
        print(json.dumps({"ok": False, "error": f"company receipt cannot be recorded for {ticket.get('status')} ticket"}, ensure_ascii=False))
        return 2
    ticket = refresh_coordination_contract(ticket)
    ticket = refresh_mdcp_contract(ticket)
    initialize_company_runtime(ticket)
    summary = company_subagent_summary(ticket)
    role = str(args.role).strip()
    if role not in summary.get("required_roles", []):
        print(json.dumps({"ok": False, "error": f"role is not required by the frozen company roster: {role}", "required_roles": summary.get("required_roles", [])}, ensure_ascii=False))
        return 2
    status = str(args.status).upper()
    if status == "FAILED" and not args.failure_class:
        args.failure_class = "REVIEW_INCOMPLETE"
    if status != "FAILED" and args.failure_class:
        print(json.dumps({"ok": False, "error": "--failure-class is only valid with --status FAILED"}, ensure_ascii=False))
        return 2
    result_path = norm(str(args.result_path)) if args.result_path else None
    computed_hash = sha256_file_contents(Path(result_path)) if result_path and Path(result_path).is_file() else None
    summary_hash = sha256_bytes(str(args.summary).encode("utf-8")) if args.summary else None
    result_hash = str(args.result_hash or computed_hash or summary_hash or "").strip()
    if args.result_hash and computed_hash and str(args.result_hash) != computed_hash:
        print(json.dumps({"ok": False, "error": "--result-hash does not match --result-path contents"}, ensure_ascii=False))
        return 2
    if status == "COMPLETED" and not result_hash:
        print(json.dumps({"ok": False, "error": "COMPLETED receipt requires --summary, --result-hash, or an existing --result-path"}, ensure_ascii=False))
        return 2
    runtime = ticket.setdefault("company_runtime", {})
    policy = ticket.get("mdcp", {}).get("layer_2_company_subagents", {})
    roster_fingerprint = policy.get("ceo_confirmation", {}).get("roster_fingerprint") if isinstance(policy, dict) else None
    role_fingerprint = policy.get("role_contract_fingerprints", {}).get(role)
    receipts = runtime.setdefault("receipts", [])
    prior_started = any(
        isinstance(row, dict)
        and row.get("role") == role
        and str(row.get("agent_id")) == str(args.agent_id)
        and row.get("status") == "STARTED"
        for row in receipts
    )
    auto_started = None
    if status == "COMPLETED" and not prior_started:
        auto_started = {
            "receipt_id": uuid.uuid4().hex,
            "ts": now(),
            "role": role,
            "agent_id": str(args.agent_id),
            "status": "STARTED",
            "model": args.model,
            "effort": args.effort,
            "result_path": None,
            "result_hash": None,
            "summary": "Automatically inferred from the completed role result.",
            "auto_recorded": True,
            "role_contract_fingerprint": role_fingerprint,
        }
        receipts.append(auto_started)
    receipt = {
        "receipt_id": uuid.uuid4().hex,
        "ts": now(),
        "role": role,
        "agent_id": str(args.agent_id),
        "status": status,
        "model": args.model,
        "effort": args.effort,
        "result_path": result_path,
        "result_hash": result_hash or None,
        "summary": args.summary,
        "role_contract_fingerprint": role_fingerprint,
        "failure_class": args.failure_class if status == "FAILED" else None,
    }
    receipts.append(receipt)
    if status in {"COMPLETED", "FAILED"}:
        receipt["released"] = True
    if prestart and source_path:
        write_json(source_path, ticket)
    else:
        save_current(ticket)
    print(json.dumps({
        "ok": True,
        "receipt": receipt,
        "auto_started_receipt": auto_started,
        "stored_on": norm(str(source_path)) if prestart and source_path else norm(str(CURRENT_TICKET)),
        "company_subagents": compact_company_summary(ticket),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_company_status(_: argparse.Namespace) -> int:
    ticket = active_ticket()
    if not ticket:
        print(json.dumps({"active": False, "company_subagents": {}}, ensure_ascii=False))
        return 0
    print(json.dumps({"active": True, "ticket_id": ticket.get("ticket_id"), "company_subagents": company_subagent_summary(ticket)}, ensure_ascii=False, indent=2))
    return 0


def mdcp_company_roles_for_text(
    text: str,
    ticket: dict[str, Any] | None = None,
    custodian: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticket = ticket or {}
    combined = text + "\n" + flattened_ticket_text(ticket)
    heavy = filter_contextual_scope_hits(heavy_hits(combined), combined, ticket)
    backlog = term_hits(text, ticket.get("backlog_only", [])) or heavy[:4]
    lenses = lens_notes_for_rough_task(text)
    why_now = str(lenses.get("product", {}).get("why_now") or "Advance the confirmed North Star through one bounded result.")
    smallest = str(lenses.get("engineering", {}).get("smallest_path") or "Implement only the narrowest acceptance-linked path.")
    qa_candidates = list(lenses.get("qa", {}).get("machine_acceptance_candidates", []))
    scope_risks = list(lenses.get("scope", {}).get("drift_signals", []))
    shit_risks = list(lenses.get("janitor", {}).get("likely_shit_mountain", []))
    custodian_row = {
        "request_verdict": (custodian or {}).get("verdict", "UNKNOWN"),
        "accepted_intent": (custodian or {}).get("accepted_intent") or "",
        "minimal_action": (custodian or {}).get("minimal_action") or "",
        "rejected_scope": (custodian or {}).get("rejected_scope", []),
        "backlog_items": (custodian or {}).get("backlog_items", []),
    }
    return {
        "strategy": {
            "north_star_tradeoffs": scope_risks[:4],
            "highest_order_risks": shit_risks[:4],
            "smaller_path": smallest,
        },
        "business": {
            "business_outcome": why_now,
            "value_path": task_summary(text),
            "defer_to_backlog": backlog[:4],
        },
        "product": {
            "why_now": why_now,
            "north_star_fit": "serve current North Star through the current ticket acceptance",
            "non_goal_warning": backlog[:4],
            "user_value_signal": mdcp_value_signal(text, ticket),
        },
        "engineering": {
            "smallest_path": smallest,
            "avoid_overengineering": ["unrelated rewrite", "future-stage implementation", "acceptance-free abstraction"],
            "expected_files": ticket_writable_paths(ticket)[:5],
            "unnecessary_files": list(ticket.get("forbidden_paths", []))[:5],
        },
        "architecture": {
            "premature_abstraction_risks": shit_risks[:4],
            "simpler_structure": smallest,
            "do_not_generalize_yet": scope_risks[:4],
        },
        "qa": {
            "acceptance_consumer": mdcp_acceptance_consumer(ticket),
            "machine_acceptance_candidates": qa_candidates[:5],
            "missing_acceptance_risks": ["no validation id", "no files_exist/contains/assertions"],
            "unsupported_acceptance_shapes": ["raw shell command in commands_pass", "contains without file/text"],
        },
        "scope_cost": {
            "scope_anchor": mdcp_scope_anchor(ticket),
            "scope_sink_risks": scope_risks[:5],
            "backlog_only": list(ticket.get("backlog_only", []))[:5] or backlog[:5],
            "budget_recommendation": ticket.get("budget", {"max_minutes": 30, "max_tool_calls": 40, "max_changed_files": 5, "max_diff_lines": 300}),
        },
        "custodian": custodian_row,
        "janitor": {
            "likely_shit_mountain": shit_risks[:5],
            "delete_or_backlog_candidates": backlog[:5],
            "simplify_candidates": ["single-use abstraction", "duplicate explanation", "unused compatibility layer"],
        },
        "auditor": {
            "acceptance_risks": ["missing machine acceptance"] if not has_machine_acceptance(ticket) else [],
            "validation_risks": ["validation requirement not run yet"] if command_validation_required(ticket) else [],
            "consumer_mismatch_risks": [mdcp_consumer_mismatch_risk(ticket)],
            "axis_fatigue_risks": [mdcp_loop_risk(ticket)],
        },
    }


def contains_mdcp_gate_language(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    patterns = [
        r"\bapprove\b",
        r"\bsign\b",
        r"role approval",
        r"board passed",
        r"decision approved",
        r"review passed",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def mdcp_layer_2_pass_criteria(roles: dict[str, Any]) -> dict[str, bool]:
    required = ["strategy", "business", "product", "engineering", "architecture", "qa", "scope_cost", "custodian", "janitor", "auditor"]
    return {
        "required_roles_present": all(role in roles for role in required),
        "role_outputs_structured": all(isinstance(roles.get(role), dict) for role in required),
        "no_approval_language": not contains_mdcp_gate_language(roles),
        "product_why_now_present": bool(roles.get("product", {}).get("why_now")),
        "engineering_smallest_path_present": bool(roles.get("engineering", {}).get("smallest_path")),
        "qa_acceptance_candidates_present": bool(roles.get("qa", {}).get("machine_acceptance_candidates")),
        "scope_sink_risks_present": bool(roles.get("scope_cost", {}).get("scope_sink_risks")),
        "custodian_verdict_present": bool(roles.get("custodian", {}).get("request_verdict")),
        "janitor_noise_candidates_present": bool(roles.get("janitor", {}).get("likely_shit_mountain")),
        "conflicts_resolved_into_ticket_or_backlog": True,
    }


def mdcp_contract_for_text(text: str, ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    ticket = ticket or {}
    layer_1 = mdcp_layer_1_structured_expression(text, ticket)
    layer_2 = mdcp_company_roles_for_text(text, ticket)
    layer_1_pass = mdcp_layer_1_pass_criteria(ticket)
    layer_2_pass = mdcp_layer_2_pass_criteria(layer_2)
    subagent_policy = company_subagent_policy(ticket)
    return {
        "protocol": "MDCP",
        "protocol_version": MDCP_PROTOCOL_VERSION,
        "role": "cross_layer_rule_library",
        "source": "https://github.com/HanShengrunning/-multi-dimensional-collaboration-protocol",
        "layer_1_structured_expression": layer_1,
        "layer_1_fields": layer_1,
        "layer_1_pass_criteria": layer_1_pass,
        "layer_2_company_roles": layer_2,
        "layer_2_company_subagents": subagent_policy,
        "layer_2_role_constraints": {
            "no_gate_language": True,
            "structured_objections_only": True,
            "must_mark_scope_anchor": True,
            "must_mark_consumer": True,
            "must_mark_smaller_path": True,
            "allowed_outputs": [
                "must_do_candidate",
                "must_not_do_candidate",
                "acceptance_candidate",
                "drift_signal_candidate",
                "backlog_candidate",
                "smaller_path",
                "shit_mountain_risk",
                "request_risk",
            ],
            "forbidden_output_policy": "no gate language",
        },
        "layer_2_pass_criteria": layer_2_pass,
        "layer_3_audit_checks": {
            "same_axis_loop_check": True,
            "acceptance_consumer_check": True,
            "scope_anchor_violation_check": True,
            "precision_mismatch_check": True,
        },
        "pass_criteria": {
            "layer_1_structured_expression_pass": [
                "precision_level is explicit",
                "scope_anchor is non-empty",
                "conversation_plane is explicit",
                "acceptance_consumer is explicit",
            ],
            "layer_2_lens_generation_pass": [
                "lens output stays in allowed output keys",
                "objections identify smaller_path or backlog_candidate",
                "no gate language is used",
            ],
            "layer_3_janitor_auditor_pass": [
                "same-axis fatigue is surfaced",
                "outside writable_paths is drift",
                "heavy future scope cannot become protected only by weak words",
                "machine acceptance is required before PASS",
            ],
        },
    }


def ensure_mdcp_contract(ticket: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ticket, dict):
        return ticket
    contract = ticket.get("mdcp")
    base = mdcp_contract_for_text(flattened_ticket_text(ticket), ticket)
    if not isinstance(contract, dict):
        ticket["mdcp"] = base
        return ticket
    for key, value in base.items():
        contract.setdefault(key, value)
    for layer_key in ["layer_1_structured_expression", "layer_1_fields"]:
        for key, value in base["layer_1_structured_expression"].items():
            contract.setdefault(layer_key, {}).setdefault(key, value)
    for key, value in base["layer_1_pass_criteria"].items():
        contract.setdefault("layer_1_pass_criteria", {}).setdefault(key, value)
    for role, row in base["layer_2_company_roles"].items():
        contract.setdefault("layer_2_company_roles", {}).setdefault(role, row)
    contract.setdefault("layer_2_company_subagents", base["layer_2_company_subagents"])
    for key, value in base["layer_2_role_constraints"].items():
        contract.setdefault("layer_2_role_constraints", {}).setdefault(key, value)
    for key, value in base["layer_2_pass_criteria"].items():
        contract.setdefault("layer_2_pass_criteria", {}).setdefault(key, value)
    for key, value in base["layer_3_audit_checks"].items():
        contract.setdefault("layer_3_audit_checks", {}).setdefault(key, value)
    contract.setdefault("pass_criteria", base["pass_criteria"])
    ticket["mdcp"] = contract
    return ticket


def refresh_mdcp_contract(ticket: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ticket, dict):
        return ticket
    base = mdcp_contract_for_text(flattened_ticket_text(ticket), ticket)
    contract = ticket.get("mdcp")
    if not isinstance(contract, dict):
        ticket["mdcp"] = base
        return ticket

    for key in ["protocol", "role", "source"]:
        contract.setdefault(key, base[key])
    contract.setdefault("protocol_version", MDCP_PROTOCOL_VERSION)

    layer_1 = contract.setdefault("layer_1_structured_expression", {})
    for key, value in base["layer_1_structured_expression"].items():
        layer_1[key] = value
    contract["layer_1_fields"] = dict(layer_1)
    contract["layer_1_pass_criteria"] = mdcp_layer_1_pass_criteria(ticket)

    roles = contract.setdefault("layer_2_company_roles", {})
    for role, row in base["layer_2_company_roles"].items():
        roles.setdefault(role, row)
    for key, value in base["layer_2_role_constraints"].items():
        contract.setdefault("layer_2_role_constraints", {}).setdefault(key, value)
    contract["layer_2_company_subagents"] = base["layer_2_company_subagents"]
    contract["layer_2_pass_criteria"] = mdcp_layer_2_pass_criteria(roles)
    for key, value in base["layer_3_audit_checks"].items():
        contract.setdefault("layer_3_audit_checks", {}).setdefault(key, value)
    contract.setdefault("pass_criteria", base["pass_criteria"])
    ticket["mdcp"] = contract
    return ticket


def mdcp_request_signal(
    text: str,
    ticket: dict[str, Any] | None = None,
    custodian: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticket = ticket or active_ticket()
    fields = mdcp_layer_1_structured_expression(text, ticket)
    roles = mdcp_company_roles_for_text(text, ticket, custodian=custodian)
    return {
        "protocol_version": MDCP_PROTOCOL_VERSION,
        "layer_1_structured_expression": fields,
        "layer_2_company_roles": {
            "custodian": roles["custodian"],
        },
        "precision_level": fields["precision_level"],
        "conversation_plane": fields["conversation_plane"],
        "scope_anchor": fields["scope_anchor"],
        "time_cost_signal": fields["time_cost_signal"],
        "value_signal": fields["value_signal"],
        "consumer_mismatch_risk": fields["consumer_mismatch_risk"],
        "scope_sink_risk": fields["scope_sink_risk"],
    }


def mdcp_axis_fatigue_check(ticket: dict[str, Any]) -> str:
    axis = axis_advisory(ticket if ticket.get("status") == "ACTIVE" else None)
    if axis.get("status") != "AXIS_FATIGUE_WARNING":
        return "none"
    return "strong" if int(axis.get("recent_axis_count") or 0) >= 5 else "warning"


def mdcp_scope_anchor_check(evaluation: dict[str, Any] | None) -> str:
    reasons = list((evaluation or {}).get("reasons", []))
    if any("forbidden_paths" in r or "outside writable_paths" in r or "immutable_paths" in r for r in reasons):
        return "fail"
    return "pass" if evaluation else "unknown"


def mdcp_acceptance_consumer_check(ticket: dict[str, Any]) -> str:
    if not has_machine_acceptance(ticket):
        return "fail"
    return "fail" if mdcp_acceptance_consumer(ticket) == "human confirmation required" else "pass"


def mdcp_precision_mismatch_check(ticket: dict[str, Any], evaluation: dict[str, Any] | None) -> str:
    fields = refresh_mdcp_contract(ticket).get("mdcp", {}).get("layer_1_structured_expression", {})
    status = (evaluation or {}).get("status")
    if fields.get("precision_level") == "high" and status in {"DRIFT", "BUDGET_EXCEEDED", "DIFF_BUDGET_EXCEEDED_CLEAN"}:
        return "warning"
    return "none"


def mdcp_required_action(evaluation: dict[str, Any] | None, axis_check: str = "none") -> str:
    status = (evaluation or {}).get("status")
    action = str((evaluation or {}).get("suggested_action") or "")
    if action == "complete_or_retry_required_departments":
        return "complete_or_retry_required_departments"
    if action == "prune_plan":
        return "prune_plan"
    if status == "NEEDS_VALIDATION":
        return "run_validation"
    if status == "VALIDATION_FAILED":
        return "fix_validation"
    if status == "ACCEPTANCE_INCOMPLETE":
        return "complete_acceptance"
    if status == "NEEDS_QUALITY_EVIDENCE":
        return "add_quality_evidence_or_fix"
    if status == "UPSTREAM_EVIDENCE_INVALID":
        return "supersede_or_rebaseline_upstream"
    if status == "DIFF_BUDGET_EXCEEDED_CLEAN":
        return "compress_or_split"
    if status in {"DRIFT"}:
        return "prune_plan" if action == "prune_plan" else "backlog"
    if status == "ARTIFACT_SPRAWL":
        return "review_quarantine_plan"
    if status == "ACTIVE_UNCHECKED" or action == "check":
        return "check"
    if status in {"BUDGET_EXCEEDED", "FAIL"}:
        return "abort"
    if status in {"PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY"}:
        return "close_then_switch_axis" if axis_check == "strong" else "close"
    if axis_check == "strong":
        return "finish_current_then_switch_axis"
    return "continue"


def mdcp_janitor_from_prune(prune: dict[str, Any] | None, artifact_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    prune = prune or {"status": "CLEAN", "reasons": []}
    artifact_items = artifact_items or []
    return {
        "status": prune.get("status", "CLEAN"),
        "artifact_classifications": [
            {
                "artifact": item.get("target") or item.get("artifact"),
                "classification": item.get("classification"),
                "reason": item.get("reason"),
            }
            for item in artifact_items[:40]
        ],
        "quarantine_candidates": [item.get("target") for item in artifact_items if item.get("classification") == "QUARANTINE_CANDIDATE"][:20],
        "backlog_candidates": [item.get("target") for item in artifact_items if item.get("classification") == "BACKLOG_CANDIDATE"][:20],
        "simplify_candidates": [item.get("target") for item in artifact_items if item.get("classification") in {"SIMPLIFY", "SIMPLIFY_CANDIDATE"}][:20],
        "protected_artifacts": [item.get("target") for item in artifact_items if item.get("classification") == "PROTECTED"][:20],
    }


def mdcp_auditor_from_evaluation(
    ticket: dict[str, Any],
    evaluation: dict[str, Any] | None,
    prune: dict[str, Any] | None = None,
) -> dict[str, Any]:
    axis_check = mdcp_axis_fatigue_check(ticket)
    status = (evaluation or {}).get("status", "ON_TRACK")
    blocking = list((evaluation or {}).get("reasons", []))
    advisories: list[str] = [
        *list((evaluation or {}).get("environment_advisories", [])),
        *list((evaluation or {}).get("budget_advisories", [])),
    ]
    if prune and prune.get("status") in {"REVIEW_REQUIRED", "NOISE_RISK", "ARTIFACT_SPRAWL"}:
        advisories.extend(prune.get("reasons", []))
        advisories.extend(prune.get("advisories", []))
    if axis_check in {"warning", "strong"}:
        advisories.append("Recent tickets cluster on one local axis.")
    return {
        "status": status,
        "blocking_reasons": blocking,
        "advisories": advisories,
        "required_action": mdcp_required_action(evaluation, axis_check),
        "acceptance_consumer_check": mdcp_acceptance_consumer_check(ticket),
        "scope_anchor_check": mdcp_scope_anchor_check(evaluation),
        "axis_fatigue_check": axis_check,
        "precision_mismatch_check": mdcp_precision_mismatch_check(ticket, evaluation),
    }


def mdcp_layer_3_pass_criteria(
    ticket: dict[str, Any],
    evaluation: dict[str, Any] | None,
    prune: dict[str, Any] | None,
) -> dict[str, bool]:
    auditor = mdcp_auditor_from_evaluation(ticket, evaluation, prune)
    status = auditor["status"]
    pass_ready_statuses = {"PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY"}
    axis_resolved_for_close = auditor["axis_fatigue_check"] != "strong" or (
        status in pass_ready_statuses and auditor["required_action"] == "close_then_switch_axis"
    )
    return {
        "janitor_checked": prune is not None,
        "auditor_checked": evaluation is not None,
        "no_artifact_sprawl_blocker": not prune or prune.get("status") != "ARTIFACT_SPRAWL",
        "validation_not_failed": status not in {"VALIDATION_FAILED", "ACCEPTANCE_INCOMPLETE", "NEEDS_QUALITY_EVIDENCE", "FAIL"},
        "acceptance_consumer_known": auditor["acceptance_consumer_check"] == "pass",
        "scope_anchor_not_violated": auditor["scope_anchor_check"] != "fail",
        "unresolved_axis_fatigue_not_strong": axis_resolved_for_close,
        "close_requires_validation_pass": status in pass_ready_statuses if evaluation else False,
        "company_runtime_complete": company_subagent_summary(ticket).get("runtime_execution_verified", True),
    }


def mdcp_layer_3_janitor_auditor(
    ticket: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    prune: dict[str, Any] | None = None,
    artifact_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "janitor": mdcp_janitor_from_prune(prune, artifact_items=artifact_items),
        "auditor": mdcp_auditor_from_evaluation(ticket, evaluation, prune),
    }


def compact_mdcp_status(
    ticket: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    prune: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = refresh_mdcp_contract(ticket).get("mdcp", {}).get("layer_1_structured_expression", {})
    auditor = mdcp_auditor_from_evaluation(ticket, evaluation, prune) if evaluation else {}
    return {
        "protocol_version": MDCP_PROTOCOL_VERSION,
        "acceptance_consumer": fields.get("acceptance_consumer"),
        "scope_anchor": fields.get("scope_anchor", []),
        "company_subagents": compact_company_summary(ticket),
        "layer_3_janitor_auditor": {"auditor": auditor} if auditor else {},
        "layer_3_pass_criteria": mdcp_layer_3_pass_criteria(ticket, evaluation, prune) if evaluation else {},
    }


def mdcp_artifact_reason(path: str, classified: dict[str, Any], ticket: dict[str, Any]) -> dict[str, Any]:
    anti = anti_pattern_hits(f"{path}\n{artifact_text(path)}", ticket)
    backlog = future_scope_hits(f"{path}\n{artifact_text(path)}", ticket)
    return {
        "north_star_mapping": classified.get("north_star_mapping"),
        "scope_anchor_violation": path_contract_role(path, ticket) == "outside" if ticket_writable_paths(ticket) else False,
        "anti_pattern_hit": bool(anti),
        "backlog_domain_hit": bool(backlog),
        "acceptance_mapping": classified.get("current_ticket_mapping"),
        "consumer_relevance": "required" if path_required_by_validation(path, ticket) else "none",
    }


def mdcp_audit(
    ticket: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    run_commands: bool = False,
    require_validation_run: bool = False,
) -> dict[str, Any]:
    ticket = refresh_coordination_contract(ticket)
    ticket = refresh_mdcp_contract(ticket)
    contract = ticket.get("mdcp", {})
    fields = contract.get("layer_1_fields", {})
    checks: dict[str, Any] = {}
    warnings: list[str] = []
    blocks: list[str] = []

    axis = axis_advisory(ticket if ticket.get("status") == "ACTIVE" else None)
    checks["same_axis_loop_check"] = axis
    if axis.get("status") == "AXIS_FATIGUE_WARNING":
        warnings.append(axis.get("reason", "same-axis fatigue"))

    if not has_machine_acceptance(ticket):
        blocks.append("acceptance has no machine consumer")
        checks["acceptance_consumer_check"] = {"status": "BLOCK", "consumer": None}
    elif command_validation_required(ticket) and not run_commands and require_validation_run:
        warnings.append("command validation exists but has not run in this check")
        checks["acceptance_consumer_check"] = {
            "status": "WARNING",
            "consumer": fields.get("acceptance_consumer", mdcp_acceptance_consumer(ticket)),
            "required_validation_ids": validation_ids(ticket),
        }
    else:
        checks["acceptance_consumer_check"] = {
            "status": "OK",
            "consumer": fields.get("acceptance_consumer", mdcp_acceptance_consumer(ticket)),
            "pending_validation_ids": validation_ids(ticket) if command_validation_required(ticket) and not run_commands else [],
        }

    eval_status = (evaluation or {}).get("status")
    eval_reasons = list((evaluation or {}).get("reasons", []))
    scope_reasons = [
        r for r in eval_reasons
        if "forbidden_paths" in r or "outside writable_paths" in r or "immutable_paths" in r
    ]
    if scope_reasons:
        blocks.extend(scope_reasons)
        checks["scope_anchor_violation_check"] = {"status": "BLOCK", "reasons": scope_reasons[:5]}
    else:
        checks["scope_anchor_violation_check"] = {"status": "OK", "scope_anchor": fields.get("scope_anchor", [])}

    precision = fields.get("precision_level", "medium")
    if precision == "high" and eval_status in {"DRIFT", "BUDGET_EXCEEDED", "DIFF_BUDGET_EXCEEDED_CLEAN"}:
        warnings.append("high-precision ticket hit drift or budget pressure")
        checks["precision_mismatch_check"] = {"status": "WARNING", "precision_level": precision, "evaluation_status": eval_status}
    else:
        checks["precision_mismatch_check"] = {"status": "OK", "precision_level": precision}

    status = "BLOCK" if blocks else "WARNING" if warnings else "OK"
    return {
        "status": status,
        "role": "cross_layer_audit",
        "checks": checks,
        "warnings": warnings,
        "blocking_reasons": blocks,
        "layer_3_janitor_auditor": mdcp_layer_3_janitor_auditor(ticket, evaluation=evaluation),
        "layer_3_pass_criteria": mdcp_layer_3_pass_criteria(ticket, evaluation, prune=None),
    }


def compile_ticket(rough_path: Path, out_path: Path) -> dict[str, Any]:
    text = rough_path.read_text(encoding="utf-8")
    lines = [line.strip("# \t") for line in text.splitlines() if line.strip()]
    title = lines[0] if lines else out_path.stem
    ticket_id = slug_id(out_path.stem, out_path.stem)
    lenses = lens_notes_for_rough_task(text)
    budget, budget_basis = compile_budget_for_text(text)
    confirmed = confirmed_goal()
    north_definition = north_star().get("goal_definition", {})
    principles = north_definition.get("first_principles", []) if isinstance(north_definition, dict) else []
    invariants = [
        {"id": f"NS-P{index}", "principle": goal_item_text(value, "principle")}
        for index, value in enumerate(principles, 1)
        if goal_item_text(value, "principle")
    ]
    writable_paths = ["src/**", "tests/**", "docs/**"]
    phase = program_phase()
    ticket = {
        "ticket_id": ticket_id,
        "title": title,
        "global_goal": confirmed or "Confirm the North Star Goal before readying this ticket.",
        "why_now": lenses["product"]["why_now"],
        "task_goal": text.strip() or "Fill this with one bounded task goal.",
        "north_star_invariants": invariants,
        "program_phase_id": phase.get("phase_id") if phase.get("status") == "ACTIVE" else None,
        "phase_completion": {"complete_on_pass": False},
        "execution_mode": "product_edit",
        "status": "DRAFT",
        "acceptance_ready": False,
        "must_do": [f"Produce the bounded result described by: {task_summary(text)}"],
        "must_not_do": ["Do not expand beyond this ticket.", "Put new ideas into backlog_only."],
        "anti_patterns": ["Acceptance-free abstraction", "Future-stage implementation outside the task goal"],
        "allowed_paths": writable_paths,
        "writable_paths": writable_paths,
        "read_dependencies": [],
        "immutable_paths": [],
        "runtime_paths": [],
        "execution_relationship": {
            "mode": "STANDALONE",
            "depends_on": [],
            "produces_contracts": [],
            "consumes_contracts": [],
            "rationale": "No independent sibling ticket was supplied; default to serial-safe standalone execution.",
        },
        "coordination_contract": {},
        "forbidden_paths": [".env", ".agent/**", ".codex/**", ".git/**"],
        "acceptance": {
            "commands_pass": [],
            "files_exist": [],
            "contains": [],
            "assertions": [],
            "files_not_changed": [],
            "max_changed_files": budget["max_changed_files"],
            "max_diff_lines": budget["max_diff_lines"],
        },
        "validation_ids": [],
        "quality_gates": [],
        "budget": budget,
        "budget_basis": budget_basis,
        "drift_signals": [
            "Starts solving a larger architecture than this ticket",
            "Starts adding unrelated features",
            "Keeps rewriting the same area without meeting acceptance",
        ],
        "backlog_only": ["Future architecture", "Nice-to-have polish", "Unrelated cleanup"],
        "requested_company_departments": [],
        "company_ceo_confirmation": {},
        "batch_execution": compile_batch_execution_for_text(text),
        "reuse_discovery": {},
        "reuse_decision": {},
        "reuse_integration": {},
        "reuse_update_decision": {},
        "reuse_update_decision": {},
        "lens_notes_status": "TASK_SPECIFIC",
        "lens_notes": lenses,
    }
    ticket = refresh_supervision_decision(ticket)
    ticket["mdcp"] = mdcp_contract_for_text(text, ticket)
    return ticket


def hooks_json() -> dict[str, Any]:
    windows_launcher = (Path.cwd() / ".agent" / "goal_compass_runtime" / "windows_hook.py").resolve()
    windows_command = subprocess.list2cmdline([sys.executable, "-X", "utf8", str(windows_launcher)])
    entry = {
        "type": "command",
        "command": "/bin/sh -c 'd=\"$PWD\"; while [ \"$d\" != / ]; do if [ -f \"$d/.agent/goal_compass.py\" ]; then cd \"$d\" || exit 0; if [ -f .agent/goal_compass_runtime/project_hook.py ]; then exec python3 .agent/goal_compass_runtime/project_hook.py; else exec python3 .agent/goal_compass.py hook; fi; fi; d=${d%/*}; [ -n \"$d\" ] || d=/; done; exit 0'",
        "commandWindows": windows_command,
        "timeout": 15,
        "statusMessage": "Codex Goal Supervisor observer",
    }
    context_entry = {**entry, "additionalContextLimit": 800}
    return {
        "hooks": {
            "PreToolUse": [{"matcher": ".*", "hooks": [entry]}],
            "PostToolUse": [{"matcher": ".*", "hooks": [entry]}],
            "PreCompact": [{"matcher": "manual|auto", "hooks": [entry]}],
            "PostCompact": [{"matcher": "manual|auto", "hooks": [entry]}],
            "SessionStart": [{"matcher": ".*", "hooks": [context_entry]}],
            "SubagentStart": [{"matcher": ".*", "hooks": [context_entry]}],
            "UserPromptSubmit": [{"matcher": ".*", "hooks": [context_entry]}],
            "Stop": [{"matcher": ".*", "hooks": [entry]}],
        }
    }


def is_goal_compass_hook(handler: Any) -> bool:
    if not isinstance(handler, dict):
        return False
    command = str(handler.get("command", ""))
    return (
        "goal_compass.py" in command
        or "project_hook.py" in command
        or str(handler.get("statusMessage", "")).startswith("Goal Compass")
    )


def merge_hooks_json(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(existing)) if isinstance(existing, dict) else {}
    result_hooks = result.setdefault("hooks", {})
    generated_hooks = generated.get("hooks", {})
    for event in (
        "PreToolUse", "PostToolUse", "PreCompact", "PostCompact",
        "SessionStart", "SubagentStart", "UserPromptSubmit", "Stop",
    ):
        preserved = []
        for group in result_hooks.get(event, []):
            if not isinstance(group, dict):
                preserved.append(group)
                continue
            handlers = [handler for handler in group.get("hooks", []) if not is_goal_compass_hook(handler)]
            if handlers:
                updated = dict(group)
                updated["hooks"] = handlers
                preserved.append(updated)
        preserved.extend(generated_hooks.get(event, []))
        if preserved:
            result_hooks[event] = preserved
        else:
            result_hooks.pop(event, None)
    return result


def cmd_goal_set(args: argparse.Namespace) -> int:
    existing = load_json(NORTH_STAR, {}) if NORTH_STAR.exists() else {}
    if existing.get("confirmed") and not args.replace_existing:
        print(json.dumps({
            "ok": False,
            "status": "EXISTING_GOAL_PRESERVED",
            "error": "A confirmed North Star already exists. Automatic goal setup never rewrites it.",
            "north_star_goal": existing.get("goal"),
            "required_action": "reuse_existing_goal",
        }, ensure_ascii=False, indent=2))
        return 2
    if args.definition_file:
        definition_path = Path(args.definition_file)
        raw_definition = load_json(definition_path, {})
        if not raw_definition:
            print(json.dumps({
                "ok": False,
                "error": f"goal definition file is missing or invalid JSON: {definition_path}",
            }, ensure_ascii=False))
            return 2
        definition = goal_definition_from_payload(args.text, raw_definition)
    else:
        definition = goal_definition_contract(
            args.text,
            problem_statement=args.problem,
            first_principles=args.first_principle,
            concrete_actions=args.action,
            deliverables=args.deliverable,
            success_criteria=args.success_criterion,
            constraints=args.constraint,
            non_goals=args.non_goal,
            dialogue_summary=args.dialogue_summary,
        )
    if args.require_detailed and definition.get("quality") != "STRUCTURED_DETAILED":
        missing_fields = list(definition.get("missing_fields", []))
        consultation_missing = any("planning_research.user_consultation" in field for field in missing_fields)
        research = definition.get("planning_research") if isinstance(definition.get("planning_research"), dict) else {}
        candidate = str(research.get("reusable_candidate_name") or "the reusable candidate").strip()
        print(json.dumps({
            "ok": False,
            "status": "GOAL_DEFINITION_INCOMPLETE",
            "error": (
                "Detailed Goal mode requires a 2,000-3,500 character executable contract with first principles, "
                "module execution relationships, dependencies, goal contributions, outputs, and final acceptance. "
                "Super-complex work also requires a referenced project plan over 4,000 characters, written after "
                "market research and any visible reuse/commercial-use consultation."
            ),
            "missing_fields": missing_fields,
            "detail_metrics": definition.get("detail_metrics", {}),
            "required_action": (
                "ask_user_about_reuse_and_commercial_use"
                if consultation_missing
                else "complete_goal_contract_before_goal_mode"
            ),
            **({
                "user_question": f"A reusable candidate was found: {candidate}. Should we use/adapt it, and is this project commercial or non-commercial?",
            } if consultation_missing else {}),
        }, ensure_ascii=False, indent=2))
        return 2
    source = "user_explicit_replacement" if existing.get("confirmed") else "user_confirmed"
    data = structured_north_star(args.text, source, definition)
    write_json(NORTH_STAR, data)
    refresh_convergence_projection()
    onboarding_probe = refresh_reuse_discovery({
        "ticket_id": "PROJECT-ONBOARDING",
        "status": "PROJECT_ONBOARDING",
        "task_goal": str(definition.get("precise_goal") or args.text),
        "must_do": list(definition.get("source_requirements", [])),
        "execution_mode": "project_onboarding",
    })
    payload = {
        "ok": True,
        "north_star_goal": args.text,
        "structured": str(definition.get("quality") or "").startswith("STRUCTURED"),
        "detailed": definition.get("quality") == "STRUCTURED_DETAILED",
        "goal_definition": goal_definition_summary(data),
        "goal_mode_objective": data.get("goal_mode_objective"),
        "goal_mode_objective_chars": len(str(data.get("goal_mode_objective") or "")),
        "execution_plan_ref": definition.get("execution_plan_ref"),
        "reuse": reuse_compact_status(onboarding_probe, AGENT),
    }
    if definition.get("quality") != "STRUCTURED_DETAILED":
        payload["warning"] = "Goal is not a detailed execution blueprint; process nodes, node outputs, or final acceptance are incomplete."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_phase_set(args: argparse.Namespace) -> int:
    existing = program_phase()
    if existing.get("status") == "ACTIVE":
        print(json.dumps({
            "ok": False,
            "error": "an ACTIVE program phase already exists",
            "phase_id": existing.get("phase_id"),
            "required_action": "phase-complete or phase-advance",
        }, ensure_ascii=False, indent=2))
        return 2
    payload = {
        "status": "ACTIVE",
        "phase_id": args.id,
        "goal": args.goal,
        "exit_criteria": list(args.exit_criterion or []),
        "source": "user_confirmed",
        "confirmed_at": now(),
        "north_star_hash": sha256_bytes(json.dumps(north_star(), ensure_ascii=False, sort_keys=True).encode("utf-8")),
    }
    write_json(PROGRAM_PHASE, payload)
    refresh_convergence_projection()
    print(json.dumps({"ok": True, "program_phase": payload}, ensure_ascii=False, indent=2))
    return 0


def complete_program_phase(reason: str, ticket_id: str | None = None) -> dict[str, Any]:
    phase = program_phase()
    if phase.get("status") != "ACTIVE":
        return phase
    phase = dict(phase)
    phase["status"] = "COMPLETED"
    phase["completed_at"] = now()
    phase["completion_reason"] = reason
    phase["completed_by_ticket_id"] = ticket_id
    write_json(PROGRAM_PHASE, phase)
    refresh_convergence_projection(current_action="", expected_evidence="")
    return phase


def cmd_phase_complete(args: argparse.Namespace) -> int:
    phase = program_phase()
    if phase.get("status") != "ACTIVE":
        print(json.dumps({"ok": False, "error": "no ACTIVE program phase"}, ensure_ascii=False))
        return 1
    completed = complete_program_phase(args.reason)
    print(json.dumps({"ok": True, "program_phase": completed}, ensure_ascii=False, indent=2))
    return 0


def cmd_phase_advance(args: argparse.Namespace) -> int:
    previous = program_phase()
    if previous.get("status") == "ACTIVE":
        previous = complete_program_phase(args.reason)
    payload = {
        "status": "ACTIVE",
        "phase_id": args.id,
        "goal": args.goal,
        "exit_criteria": list(args.exit_criterion or []),
        "source": "user_confirmed",
        "confirmed_at": now(),
        "north_star_hash": sha256_bytes(json.dumps(north_star(), ensure_ascii=False, sort_keys=True).encode("utf-8")),
        "previous_phase_id": previous.get("phase_id"),
    }
    write_json(PROGRAM_PHASE, payload)
    refresh_convergence_projection()
    print(json.dumps({"ok": True, "previous_phase": previous, "program_phase": payload}, ensure_ascii=False, indent=2))
    return 0


def cmd_goal_detect(_: argparse.Namespace) -> int:
    candidates = infer_project_goals()
    status = "UNKNOWN"
    north = north_star()
    if north.get("confirmed"):
        status = goal_match(candidates[0]["goal"], north)["status"]
    else:
        north["candidate_goals"] = candidates
        north["requires_confirmation"] = True
        write_json(NORTH_STAR, north)
    report = goal_report(candidates, status=status)
    report["status"] = "NEEDS_CONFIRMATION" if status == "UNKNOWN" else status
    write_goal_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def goal_check_result(user_goal: str) -> dict[str, Any]:
    candidates = infer_project_goals()
    north = north_star()
    result = goal_match(user_goal, north)
    return {
        "status": result["status"],
        "north_star_goal": north.get("goal") if north.get("confirmed") else None,
        "user_goal": user_goal,
        "project_detected_goal": candidates[0]["goal"],
        "alignment_score": result["alignment_score"],
        "supporting_evidence": result["supporting_evidence"],
        "contradicting_evidence": result["contradicting_evidence"],
        "required_action": result["required_action"],
    }


def cmd_goal_check(args: argparse.Namespace) -> int:
    result = goal_check_result(args.user_goal)
    if result["status"] == "MISMATCH" and MISMATCH_MESSAGE not in result["contradicting_evidence"]:
        result["contradicting_evidence"].append(MISMATCH_MESSAGE)
    if result["status"] == "UNKNOWN":
        result["alignment_status"] = "UNKNOWN"
        result["status"] = "NEEDS_CONFIRMATION"
        result["message"] = NORTH_STAR_CONFIRMATION_MESSAGE
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ALIGNED", "PARTIAL", "NEEDS_CONFIRMATION"} else 1


def cleanup_request(text: str) -> bool:
    low = canonical_text(text)
    return any(w in low for w in ["remove", "delete", "cleanup", "unused", "over abstract", "over-abstract", "simplify", "prune", "noise", "shit mountain"])


def acceptance_improvement(text: str, ticket: dict[str, Any]) -> bool:
    low = canonical_text(text)
    if filter_contextual_scope_hits(heavy_hits(text), text, ticket):
        return False
    maps_to_acceptance = maps_to_ticket_acceptance_text(text, ticket)
    contract_mapping = request_current_mapping(text, ticket)
    has_acceptance_language = any(w in low for w in ["assertion", "acceptance", "validation", "test", "artifact path", "files_exist"])
    return maps_to_acceptance or (has_acceptance_language and bool(contract_mapping))


def request_policy_hits(text: str, items: list[Any]) -> list[str]:
    low = canonical_text(text)
    request_words = text_words(text)
    hits: list[str] = []
    for item in items:
        value = str(item)
        item_low = canonical_text(value).strip()
        if item_low and item_low in low:
            hits.append(value)
            continue
        item_words = text_words(value) - WEAK_PROTECT_WORDS
        overlap = request_words & item_words
        if item_words and len(overlap) >= 2 and len(overlap) / len(item_words) >= 0.6:
            hits.append(value)
    return hits


def request_current_mapping(text: str, ticket: dict[str, Any]) -> list[str]:
    request_words = text_words(text)
    current_contract = json.dumps(
        {
            "task_goal": ticket.get("task_goal"),
            "must_do": ticket.get("must_do", []),
            "acceptance": ticket.get("acceptance", {}),
        },
        ensure_ascii=False,
    )
    current_words = text_words(current_contract)
    weak = WEAK_PROTECT_WORDS | {"full", "complete", "generic", "platform", "framework", "enterprise"}
    mapping = sorted((request_words & current_words) - weak)
    request_low = canonical_text(text)
    contract_low = canonical_text(current_contract)
    permission_request = any(value in request_low for value in ["rbac", "permission", "authorization"])
    minimal_permission_contract = (
        any(value in contract_low for value in ["permission", "authorization"])
        and any(value in contract_low for value in ["guard", "check", "boundary"])
    )
    if permission_request and minimal_permission_contract:
        return ["minimal_permission_intent"]
    return mapping if len(mapping) >= 2 else []


def simplified_current_action(ticket: dict[str, Any]) -> str:
    task = str(ticket.get("task_goal") or "the current ticket acceptance").strip()
    return f"Implement only the smallest portion mapped to the current ticket: {task[:220]}"


def safe_heavy_simplification(current_mapping: list[str]) -> bool:
    """Only simplify heavy scope when the bounded intent is explicit and known."""
    return current_mapping == ["minimal_permission_intent"]


def drift_request_route(text: str) -> str:
    low = canonical_text(text)
    if any(term in low for term in ["rewrite the whole", "replace the entire", "replace the north star", "unrelated architecture", "推翻整个", "替换北极星", "重写全部", "无关架构"]):
        return "REJECT"
    action_words = len(re.findall(r"\b(?:add|build|create|replace|rewrite|migrate|implement)\b", low))
    if action_words >= 2 and (" and " in low or "同时" in low or "并且" in low):
        return "SPLIT"
    return "BACKLOG"


def affirmative_request_action(text: str) -> bool:
    """Detect intended mutation without treating forbidden actions as intent."""
    low = canonical_text(text)
    negated_suffixes = (
        "do not", "don't", "must not", "without", "never", "not", "no",
        "cannot", "can't", "avoid", "不得", "不要", "不再", "无需", "无须",
        "禁止", "避免", "不能", "不可", "绝不", "不会", "不需要", "不",
    )

    def affirmative_at(start: int) -> bool:
        prefix = low[max(0, start - 18):start].rstrip()
        return not any(prefix.endswith(marker) for marker in negated_suffixes)

    english = re.compile(r"\b(?:add|build|create|replace|rewrite|migrate|implement|fix|modify|delete|remove|patch|refactor|develop|write)\b")
    if any(affirmative_at(match.start()) for match in english.finditer(low)):
        return True
    chinese_terms = (
        "修复", "修改", "新增", "增加", "添加", "构建", "实现", "创建", "新建",
        "替换", "重写", "迁移", "删除", "移除", "修正", "补齐", "补充", "编写",
        "开发", "接入", "改造", "重构",
    )
    for term in chinese_terms:
        start = 0
        while True:
            position = low.find(term, start)
            if position < 0:
                break
            if affirmative_at(position):
                return True
            start = position + len(term)
    return False


def explicit_correction_request(text: str) -> bool:
    low = canonical_text(text).strip()
    if re.match(r"^(?:stop|pause|cancel|halt)\b", low):
        return True
    if re.match(r"^(?:停止|暂停|终止|取消)(?:当前|这个|该|本)?(?:任务|票据|项目|发布|范围|工作)", low):
        return True
    return any(term in low for term in [
        "do not continue", "remove from scope", "narrow scope", "stop that scope",
        "不要继续", "不要做", "不再做", "移出范围", "缩小范围",
    ])


def request_operation_class(text: str) -> str:
    low = canonical_text(text)
    # Main product actions outrank boundary clauses such as "read the registry
    # only" or "do not stop production".
    if affirmative_request_action(text):
        return "product_edit"
    if explicit_correction_request(text):
        return "correction"
    explicit_read_only = any(term in low for term in [
        "read only", "status only", "without changes", "do not modify", "no edits",
        "只读", "仅查看", "只查看", "仅检查", "只检查", "仅分析", "只分析", "仅审计", "只审计",
    ])
    if explicit_read_only:
        return "read_only"
    if re.search(r"\b(?:audit|inspect|review|analyze|summarize|compare)\b", low) or any(
        term in low for term in ["审计", "检查", "分析", "查看", "诊断", "汇总", "比较"]
    ):
        return "read_only"
    if re.search(r"\b(?:plan|design|specify|draft|roadmap)\b", low) or any(term in low for term in ["规划一下", "制定方案", "设计一下", "架构方案"]):
        return "planning"
    return "product_edit"


def stop_or_scope_reduction_request(text: str) -> bool:
    return explicit_correction_request(text)


def request_program_phase_mapping(text: str) -> list[str]:
    phase = program_phase()
    if phase.get("status") != "ACTIVE":
        return []
    mapping = request_current_mapping(text, {
        "task_goal": phase.get("goal"),
        "must_do": phase.get("exit_criteria", []),
        "acceptance": {},
    })
    return [f"program_phase:{phase.get('phase_id')}", *mapping] if mapping else []


def request_failed_ticket_recovery_mapping(text: str) -> list[str]:
    previous = last_ticket()
    if not previous or str(previous.get("status")) == "PASS":
        return []
    low = canonical_text(text)
    recovery_intent = any(term in low for term in [
        "fix", "repair", "recover", "regression", "修复", "修正", "恢复", "回归",
    ])
    if not recovery_intent:
        return []
    mapping = request_current_mapping(text, previous)
    return [f"failed_ticket:{previous.get('ticket_id')}", *mapping] if mapping else []


def request_decision(text: str) -> dict[str, Any]:
    north = north_star()
    ticket = active_ticket()
    low = canonical_text(text)
    if "FORCE_OVERRIDE_NORTH_STAR" in text:
        override_goal = text.split("FORCE_OVERRIDE_NORTH_STAR", 1)[1].strip(" :-\n\t") or text
        data = structured_north_star(override_goal, "user_force_override")
        data["notes"] = [{"override_reason": text}]
        write_json(NORTH_STAR, data)
        return {
            "request": text,
            "verdict": "ACCEPT_AS_IS",
            "north_star_mapping": "FORCE_OVERRIDE_NORTH_STAR",
            "goal_mapping": [],
            "accepted_intent": override_goal,
            "minimal_action": "North Star overridden only because FORCE_OVERRIDE_NORTH_STAR was explicit.",
            "rejected_scope": [],
            "backlog_items": [],
            "reason": "Explicit force override marker present; override_reason recorded.",
            "allowed_current_change": False,
        }
    if not north.get("confirmed"):
        report = goal_report(infer_project_goals(), status="UNKNOWN", user_goal=text)
        write_goal_report(report)
        return {
            "request": text,
            "verdict": "UNKNOWN",
            "north_star_mapping": None,
            "goal_mapping": [],
            "accepted_intent": None,
            "minimal_action": None,
            "rejected_scope": [],
            "backlog_items": [],
            "reason": MISMATCH_MESSAGE,
            "allowed_current_change": False,
        }

    operation = request_operation_class(text)
    north_check = goal_match(text, north)
    if operation == "correction" and stop_or_scope_reduction_request(text):
        return {
            "request": text,
            "verdict": "ACCEPT_AS_IS",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": ["scope_reduction"],
            "accepted_intent": text,
            "minimal_action": "Stop, pause, or remove only the named scope; do not replace it with another task.",
            "rejected_scope": [],
            "backlog_items": [],
            "reason": "Explicit correction and scope-reduction intent takes precedence over lexical matches.",
            "allowed_current_change": False,
        }
    if operation == "read_only":
        return {
            "request": text,
            "verdict": "ACCEPT_READ_ONLY",
            "north_star_mapping": str(north.get("goal")) if north_check["status"] in {"ALIGNED", "PARTIAL"} else None,
            "goal_mapping": north_check.get("supporting_evidence", []),
            "accepted_intent": text,
            "minimal_action": "Perform the requested read-only analysis. Normal product work may continue without a ticket; use an optional bounded ticket only when its contract adds value.",
            "rejected_scope": [],
            "backlog_items": [],
            "reason": "Read-only inspection cannot mutate the North Star or current acceptance, so it is allowed even when lexical goal mapping is weak.",
            "allowed_current_change": False,
        }

    anti = request_policy_hits(text, north.get("anti_goals", []))
    must_not = request_policy_hits(text, ticket.get("must_not_do", [])) + request_policy_hits(text, ticket.get("anti_patterns", []))
    ticket_backlog = request_policy_hits(text, ticket.get("backlog_only", []))
    north_backlog = request_policy_hits(text, north.get("backlog_domains", []))
    heavy = filter_contextual_scope_hits(heavy_hits(text), text, ticket)
    drift = request_policy_hits(text, ticket.get("drift_signals", []))
    allowed = term_hits(text, north.get("allowed_subgoals", []))
    must_do = term_hits(text, ticket.get("must_do", []))
    acceptance_hit = acceptance_improvement(text, ticket)

    current_mapping = request_current_mapping(text, ticket)
    phase_mapping = request_program_phase_mapping(text)
    recovery_mapping = request_failed_ticket_recovery_mapping(text)

    if cleanup_request(text):
        return {
            "request": text,
            "verdict": "ACCEPT_AS_IS",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": ["reduces noise"],
            "accepted_intent": text,
            "minimal_action": "Mark or simplify only the named noise; Goal Janitor does not move or delete project files.",
            "rejected_scope": [],
            "backlog_items": [],
            "reason": "Request reduces artifact-sprawl risk and does not expand acceptance.",
            "allowed_current_change": True,
        }
    if anti:
        if safe_heavy_simplification(current_mapping) and heavy:
            return {
                "request": text,
                "verdict": "ACCEPT_SIMPLIFIED",
                "north_star_mapping": str(north.get("goal")),
                "goal_mapping": current_mapping,
                "accepted_intent": text,
                "minimal_action": simplified_current_action(ticket),
                "rejected_scope": list(dict.fromkeys([*anti, *heavy])),
                "backlog_items": [text],
                "reason": "The underlying intent maps to the current ticket, but anti-goal scope is rejected. " + EDGE_CASE_MESSAGE,
                "allowed_current_change": True,
            }
        if ticket_backlog or north_backlog or any("marketplace" in canonical_text(item) for item in anti):
            return {
                "request": text,
                "verdict": "BACKLOG",
                "north_star_mapping": None,
                "goal_mapping": [],
                "accepted_intent": None,
                "minimal_action": None,
                "rejected_scope": anti,
                "backlog_items": [text],
                "reason": "Request belongs to an explicit anti-goal or future domain and does not serve current acceptance.",
                "allowed_current_change": False,
            }
        return {
            "request": text,
            "verdict": "REJECT",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": [],
            "accepted_intent": None,
            "minimal_action": None,
            "rejected_scope": anti,
            "backlog_items": [],
            "reason": "Request hits North Star anti_goals and does not serve current ticket acceptance. " + EDGE_CASE_MESSAGE,
            "allowed_current_change": False,
        }
    if must_not:
        if safe_heavy_simplification(current_mapping) and heavy:
            return {
                "request": text,
                "verdict": "ACCEPT_SIMPLIFIED",
                "north_star_mapping": str(north.get("goal")),
                "goal_mapping": current_mapping,
                "accepted_intent": text,
                "minimal_action": simplified_current_action(ticket),
                "rejected_scope": list(dict.fromkeys([*must_not, *heavy])),
                "backlog_items": [text],
                "reason": "The underlying intent maps to current scope, but the larger forbidden design is rejected. " + EDGE_CASE_MESSAGE,
                "allowed_current_change": True,
            }
        if ticket_backlog or north_backlog or any("marketplace" in canonical_text(item) for item in [*must_not, *heavy]):
            return {
                "request": text,
                "verdict": "BACKLOG",
                "north_star_mapping": str(north.get("goal")),
                "goal_mapping": [],
                "accepted_intent": None,
                "minimal_action": None,
                "rejected_scope": must_not,
                "backlog_items": [text],
                "reason": "Request is explicitly outside the current ticket and belongs to future scope.",
                "allowed_current_change": False,
            }
        return {
            "request": text,
            "verdict": "REJECT",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": [],
            "accepted_intent": None,
            "minimal_action": None,
            "rejected_scope": must_not,
            "backlog_items": [],
            "reason": "Request hits current_ticket.must_not_do or anti_patterns.",
            "allowed_current_change": False,
        }
    if ticket_backlog or north_backlog:
        return {
            "request": text,
            "verdict": "BACKLOG",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": allowed,
            "accepted_intent": None,
            "minimal_action": None,
            "rejected_scope": [],
            "backlog_items": [text],
            "reason": "Request belongs to backlog/future domain and does not serve current acceptance.",
            "allowed_current_change": False,
        }
    if not ticket and (north_check["status"] in {"ALIGNED", "PARTIAL"} or phase_mapping or recovery_mapping):
        contextual_mapping = list(dict.fromkeys([
            *north_check.get("supporting_evidence", []),
            *phase_mapping,
            *recovery_mapping,
        ]))
        return {
            "request": text,
            "verdict": "PROPOSE_NEW_TICKET",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": contextual_mapping,
            "accepted_intent": text,
            "minimal_action": "Continue normally, or create an optional bounded ticket when machine certification or isolated scope would reduce rework.",
            "rejected_scope": [],
            "backlog_items": [],
            "reason": (
                "The request maps to the confirmed North Star, active Program Phase, or latest failed-ticket recovery. "
                "No ACTIVE ticket exists, which is valid in advisory mode."
            ),
            "allowed_current_change": False,
            "ticket_optional": True,
        }
    if heavy:
        if safe_heavy_simplification(current_mapping):
            return {
                "request": text,
                "verdict": "ACCEPT_SIMPLIFIED",
                "north_star_mapping": str(north.get("goal")),
                "goal_mapping": current_mapping,
                "accepted_intent": text,
                "minimal_action": simplified_current_action(ticket),
                "rejected_scope": heavy,
                "backlog_items": [text],
                "reason": "Heavy-scope request simplified to the smallest current-ticket intent.",
                "allowed_current_change": True,
            }
        if any("marketplace" in h.lower() for h in heavy):
            return {
                "request": text,
                "verdict": "BACKLOG",
                "north_star_mapping": None,
                "goal_mapping": [],
                "accepted_intent": None,
                "minimal_action": None,
                "rejected_scope": heavy,
                "backlog_items": [text],
                "reason": "Marketplace scope does not map to current acceptance and is routed to backlog.",
                "allowed_current_change": False,
            }
        return {
            "request": text,
            "verdict": "REJECT",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": [],
            "accepted_intent": None,
            "minimal_action": "If there is a valid need, create a smaller ticket that directly serves acceptance.",
            "rejected_scope": heavy,
            "backlog_items": [],
            "reason": "Heavy scope terms are not allowed to ACCEPT_AS_IS.",
            "allowed_current_change": False,
        }
    if drift:
        route = drift_request_route(text)
        return {
            "request": text,
            "verdict": route,
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": [],
            "accepted_intent": None,
            "minimal_action": None,
            "rejected_scope": drift,
            "backlog_items": [text] if route in {"BACKLOG", "SPLIT"} else [],
            "reason": f"Request matches current ticket drift_signals; route={route} based on rewrite severity and independent action count.",
            "allowed_current_change": False,
        }
    if acceptance_hit or must_do:
        return {
            "request": text,
            "verdict": "ACCEPT_AS_IS",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": ["current_ticket.acceptance" if acceptance_hit else "current_ticket.must_do"],
            "accepted_intent": text,
            "minimal_action": "Apply only the smallest change that directly serves current acceptance.",
            "rejected_scope": [],
            "backlog_items": [],
            "reason": "Request directly advances current acceptance or must_do without expanding scope.",
            "allowed_current_change": True,
        }
    check = goal_match(text, north)
    if check["status"] == "PARTIAL":
        return {
            "request": text,
            "verdict": "SPLIT",
            "north_star_mapping": str(north.get("goal")),
            "goal_mapping": check["supporting_evidence"],
            "accepted_intent": None,
            "minimal_action": "Create a future bounded ticket if this still matters.",
            "rejected_scope": [],
            "backlog_items": [text],
            "reason": "Request may serve the North Star but does not map to current acceptance.",
            "allowed_current_change": False,
        }
    return {
        "request": text,
        "verdict": "REJECT",
        "north_star_mapping": str(north.get("goal")),
        "goal_mapping": [],
        "accepted_intent": None,
        "minimal_action": None,
        "rejected_scope": [text],
        "backlog_items": [],
        "reason": "Request does not clearly serve the active goal or current acceptance.",
        "allowed_current_change": False,
    }


def cmd_request(args: argparse.Namespace) -> int:
    decision = request_decision(args.text)
    active = active_ticket()
    operation = request_operation_class(args.text)
    north = north_star()
    phase = program_phase()
    north_alignment = goal_match(args.text, north).get("status") if north.get("confirmed") else "UNKNOWN"
    decision["operation_class"] = operation
    decision["north_star_alignment"] = north_alignment
    decision["program_phase_alignment"] = (
        "MAPPED"
        if any(str(value).startswith("program_phase:") for value in decision.get("goal_mapping", []))
        else "NOT_MAPPED"
    )
    decision["active_ticket"] = {
        "ticket_id": active.get("ticket_id"),
        "status": active.get("status"),
        "acceptance_fingerprint": active.get("acceptance_fingerprint"),
    } if active else None
    decision["ticket_scope_alignment"] = "MAPPED" if decision.get("allowed_current_change") else "NOT_APPLICABLE" if not active else "OUTSIDE_CURRENT_TICKET"
    decision["read_only_allowed"] = decision.get("verdict") == "ACCEPT_READ_ONLY" or operation == "read_only"
    decision["planning_allowed"] = operation in {"read_only", "planning", "correction"} and not str(decision.get("verdict", "")).startswith("REJECT")
    decision["product_edit_allowed"] = bool(decision.get("allowed_current_change"))
    decision["requires_new_ticket"] = False
    decision["ticket_recommended"] = decision.get("verdict") in {"PROPOSE_NEW_TICKET", "SPLIT"}
    verdict = str(decision.get("verdict") or "")
    decision["custodian"] = {
        "invocation": "AI_OPTIONAL",
        "role": "incoming_goal_or_scope_change",
        "binding": False,
    }
    decision["intervention"] = (
        "SILENT"
        if verdict in {"ACCEPT_AS_IS", "ACCEPT_READ_ONLY"}
        else "STRONG_WARNING"
    )
    decision["execution_policy"] = "advisory_only_unless_an_explicit_user_authored_anti_goal_is_hit"
    decision["program_phase"] = phase if phase.get("status") == "ACTIVE" else None
    decision["mdcp"] = mdcp_request_signal(args.text, ticket=active, custodian=decision)
    decision["ts"] = now()
    north_payload = json.dumps(north, ensure_ascii=False, sort_keys=True).encode("utf-8")
    state_payload = json.dumps({
        "request": args.text,
        "ts": decision["ts"],
        "north_star_hash": sha256_bytes(north_payload),
        "active_ticket_id": active.get("ticket_id") if active else None,
        "active_ticket_status": active.get("status") if active else None,
        "acceptance_fingerprint": active.get("acceptance_fingerprint") if active else None,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    decision["provenance"] = {
        "decision_id": sha256_bytes(state_payload)[:20],
        "router_version": REQUEST_ROUTER_VERSION,
        "runtime_sha256": sha256_file_contents(Path(__file__)),
        "north_star_hash": sha256_bytes(north_payload),
        "north_star_contract_version": north.get("contract_version"),
        "program_phase_id": phase.get("phase_id") if phase.get("status") == "ACTIVE" else None,
        "active_ticket_id": active.get("ticket_id") if active else None,
        "active_ticket_status": active.get("status") if active else None,
        "acceptance_fingerprint": active.get("acceptance_fingerprint") if active else None,
    }
    append_jsonl(REQUEST_DECISIONS, decision)
    if decision.get("verdict") in {"BACKLOG", "SPLIT"}:
        for item in decision.get("backlog_items", []) or [args.text]:
            append_jsonl(BACKLOG, {"ts": now(), "text": item, "source": "request"})
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


def janitor_ticket_context(
    ticket: dict[str, Any] | None = None,
    *,
    refresh_usage: bool = True,
) -> dict[str, Any]:
    ticket = ticket or current_ticket()
    if ticket.get("status") == "ACTIVE":
        return update_usage(ticket) if refresh_usage else ticket
    return {
        "status": "NONE",
        "must_do": [],
        "must_not_do": [],
        "anti_patterns": [],
        "allowed_paths": [],
        "forbidden_paths": [],
        "acceptance": {},
        "validation_ids": [],
        "backlog_only": [],
        "budget_used": {"changed_files": []},
    }


def janitor_changed_paths(ticket: dict[str, Any]) -> list[str]:
    usage = ticket.get("budget_used", {}) if isinstance(ticket.get("budget_used"), dict) else {}
    return sorted(set([
        *usage.get("changed_files", []),
        *usage.get("immutable_changes", []),
    ]))


def changed_paths_need_janitor(ticket: dict[str, Any], files: list[str]) -> bool:
    """Cheap trigger for bounded Janitor work; never scans the repository."""
    if len(files) > 8:
        return True
    suspicious_path_terms = {
        "stale", "unused", "deprecated", "duplicate", "scaffold", "marketplace",
        "rbac", "security-gateway", "security_gateway", "tmp", "noise", "archive",
    }
    for path in files[:40]:
        role = path_contract_role(path, ticket)
        if role in {"immutable", "outside"} or match_path(path, ticket.get("forbidden_paths", [])):
            return True
        path_words = set(re.findall(r"[a-z0-9_-]+", canonical_text(path)))
        if path_words & suspicious_path_terms:
            return True
        text = artifact_text(path)
        if anti_pattern_hits(text, ticket) or future_scope_hits(text, ticket):
            return True
        if filter_contextual_scope_hits(heavy_hits(text), text, ticket):
            return True
    return False


def bounded_janitor_context(
    ticket: dict[str, Any],
    files: list[str],
) -> tuple[dict[str, int], dict[str, Any]]:
    """Build reference evidence from the frozen ticket surface, not the whole repo."""
    candidates = list(dict.fromkeys([
        *files,
        *acceptance_positive_paths(ticket),
        *ticket_read_dependencies(ticket),
        *ticket_immutable_paths(ticket),
    ]))
    for command_id in validation_ids(ticket):
        row = catalog().get(command_id, {})
        if not isinstance(row, dict):
            continue
        for key in ("inputs", "reads_paths", "protects_paths"):
            values = row.get(key, [])
            if isinstance(values, list):
                candidates.extend(str(value) for value in values if str(value).strip())

    bounded_paths: list[str] = []
    for raw in dict.fromkeys(candidates):
        path = norm(str(raw))
        if not path or any(mark in path for mark in ("*", "?", "[")):
            continue
        if Path(path).is_file():
            bounded_paths.append(path)
        if len(bounded_paths) >= 500:
            break
    items = [{"artifact": path, "kind": kind_for_path(path)} for path in bounded_paths]
    references = project_reference_counts(items) if items else {}
    context = project_scan_context(items, references) if items else {
        "validation_manifests": set(),
        "validation_required_paths": set(),
        "duplicate_shadow_paths": set(),
    }
    return references, context


def prune_check_result(
    files: list[str] | None = None,
    reference_counts: dict[str, int] | None = None,
    project_context: dict[str, Any] | None = None,
    scope: str = "current-ticket",
    explicit_path: str | None = None,
    explicit_request: bool = False,
) -> dict[str, Any]:
    ticket = janitor_ticket_context()
    supervision = ticket.get("supervision") if isinstance(ticket.get("supervision"), dict) else supervision_decision(ticket)
    if (
        files is None
        and (not explicit_request or supervision.get("level") == "NONE")
        and not explicit_path
        and scope == "current-ticket"
        and ticket.get("status") == "ACTIVE"
        and supervision.get("janitor_mode") == "not_required"
    ):
        changed = janitor_changed_paths(ticket)
        if not changed_paths_need_janitor(ticket, changed):
            return {
                "status": "NOT_REQUIRED",
                "intervention": "SILENT",
                "binding": False,
                "capability": "JANITOR_MARK_ONLY",
                "scope": "current-ticket",
                "files_scanned": 0,
                "noise_score": 0.0,
                "reasons": [],
                "advisories": [],
                "required_action": "continue",
                "ticket_noise_status": "NOT_REQUIRED",
                "repository_hygiene_status": "NOT_SCANNED",
                "supervision_level": supervision.get("level"),
                "net_benefit_reason": "No changed-path signal justified a bounded Janitor scan.",
            }
        files = changed
    if files is None:
        if explicit_path:
            files = [norm(explicit_path)]
            scope = "path"
        elif scope == "full-repo":
            files = [row["artifact"] for row in scan_artifacts()]
        elif ticket.get("status") == "ACTIVE":
            files = janitor_changed_paths(ticket)
        else:
            return {
                "status": "NOT_APPLICABLE",
                "intervention": "SILENT",
                "binding": False,
                "capability": "JANITOR_MARK_ONLY",
                "scope": "current-ticket",
                "files_scanned": 0,
                "noise_score": 0.0,
                "reasons": ["no ACTIVE ticket; use --scope full-repo for repository hygiene"],
                "advisories": [],
                "required_action": "prepare_ticket_or_run_full_repo_scan",
                "ticket_noise_status": "NOT_APPLICABLE",
                "repository_hygiene_status": "UNKNOWN",
            }
    scan_north = north_star()
    if reference_counts is None:
        if scope == "full-repo":
            scanned = scan_artifacts()
            reference_counts = project_reference_counts(scanned, scan_north)
            project_context = project_scan_context(scanned, reference_counts)
        else:
            reference_counts, project_context = bounded_janitor_context(ticket, files)
    elif project_context is None:
        project_context = {}
    reasons: list[str] = []
    review_reasons: list[str] = []
    score = 0.0
    for path in files:
        c = classify_artifact(path, ticket, reference_counts, project_context, scan_north)
        if c["classification"] == "QUARANTINE_CANDIDATE":
            score += 0.6
            reasons.append(f"{path}: {c['reason']}")
        elif c["classification"] in {"BACKLOG_CANDIDATE", "SIMPLIFY"}:
            score += 0.25
            reasons.append(f"{path}: {c['classification']} - {c['reason']}")
        elif c["classification"] == "REVIEW_REQUIRED":
            review_reasons.append(f"{path}: {c['reason']}")
    status = "CLEAN"
    action = "continue"
    if score >= 0.75:
        status, action = "ARTIFACT_SPRAWL", "review_quarantine_plan"
    elif score > 0:
        status, action = "NOISE_RISK", "prune_plan"
    elif review_reasons:
        status, action = "REVIEW_REQUIRED", "review_marks"
    return {
        "status": status,
        "scope": scope,
        "files_scanned": len(files),
        "noise_score": round(min(score, 1.0), 2),
        "reasons": reasons,
        "advisories": review_reasons,
        "required_action": action,
        "ticket_noise_status": status if scope in {"current-ticket", "path"} else "NOT_APPLICABLE",
        "repository_hygiene_status": status if scope == "full-repo" else "NOT_SCANNED",
    }


def cmd_prune_check(args: argparse.Namespace) -> int:
    result = prune_check_result(scope=args.scope, explicit_path=args.path, explicit_request=True)
    result["janitor"] = {
        "implicit_mode": "background_sprawl_signal",
        "explicit_mode": "AI_OPTIONAL_PRUNE_CHECK",
        "capability_level": JANITOR_CAPABILITY_LEVEL,
        "binding": False,
        "moves_files": False,
        "deletes_files": False,
    }
    ticket = current_ticket()
    result["mdcp"] = {
        "protocol_version": MDCP_PROTOCOL_VERSION,
        "layer_3_janitor_auditor": mdcp_layer_3_janitor_auditor(ticket, prune=result),
        "layer_3_pass_criteria": mdcp_layer_3_pass_criteria(ticket, evaluation=None, prune=result),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def prune_plan_result(scope: str = "current-ticket", explicit_path: str | None = None) -> dict[str, Any]:
    ticket = janitor_ticket_context()
    if explicit_path:
        files = [norm(explicit_path)]
        scope = "path"
    elif scope == "full-repo":
        scanned = scan_artifacts()
        files = [row["artifact"] for row in scanned]
    elif ticket.get("status") == "ACTIVE":
        files = janitor_changed_paths(ticket)
    else:
        return {
            "generated_at": now(),
            "status": "NOT_APPLICABLE",
            "scope": "current-ticket",
            "items": [],
            "reason": "no ACTIVE ticket; use --scope full-repo for repository hygiene",
            "janitor_policy": {"capability_level": JANITOR_CAPABILITY_LEVEL, "moves_files": False, "deletes_files": False},
        }
    scan_north = north_star()
    if scope == "full-repo":
        references = project_reference_counts(scanned, scan_north)
        project_context = project_scan_context(scanned, references)
    else:
        references, project_context = bounded_janitor_context(ticket, files)
    entries = []
    for path in files:
        c = classify_artifact(path, ticket, references, project_context, scan_north)
        classification = c["classification"]
        action = {
            "QUARANTINE_CANDIDATE": "mark_quarantine",
            "BACKLOG_CANDIDATE": "backlog",
            "SIMPLIFY": "manual_simplify",
            "REVIEW_REQUIRED": "manual_review",
        }.get(classification, "keep")
        negative_scope = classification in {"QUARANTINE_CANDIDATE", "BACKLOG_CANDIDATE"} or c.get("suggested_classification") in {
            "QUARANTINE_CANDIDATE",
            "BACKLOG_CANDIDATE",
            "NOISE_RISK",
        }
        maps_ns = False if negative_scope else maps_to_north_star(artifact_text(path), scan_north)
        entry = {
            "target": path,
            "classification": classification,
            "confidence": c.get("confidence", 0.0),
            "delete_safe": False,
            "goal_mapping": c.get("goal_mapping"),
            "north_star_mapping": c.get("north_star_mapping"),
            "current_ticket_mapping": c.get("current_ticket_mapping"),
            "signals": c.get("signals", []),
            "evidence_tier": c.get("evidence_tier", "AMBIGUOUS"),
            "janitor_action_limit": JANITOR_CAPABILITY_LEVEL,
            "required_by_validation": path_required_by_validation(path, ticket),
            "maps_to_north_star": maps_ns,
            "required_by_existing_core_flow": existing_core_flow(path, artifact_text(path), ticket, scan_north),
            "reason": c["reason"],
            "action": action,
        }
        entry["mdcp_reason"] = mdcp_artifact_reason(path, c, ticket)
        entries.append(entry)
    prune = prune_check_result(files, references, project_context, scope=scope)
    return {
        "generated_at": now(),
        "status": prune["status"],
        "scope": scope,
        "ticket_noise_status": prune.get("ticket_noise_status"),
        "repository_hygiene_status": prune.get("repository_hygiene_status"),
        "janitor_policy": {"capability_level": JANITOR_CAPABILITY_LEVEL, "moves_files": False, "deletes_files": False},
        "items": entries,
        "mdcp": {
            "protocol_version": MDCP_PROTOCOL_VERSION,
            "layer_3_janitor_auditor": mdcp_layer_3_janitor_auditor(ticket, prune=prune, artifact_items=entries),
            "layer_3_pass_criteria": mdcp_layer_3_pass_criteria(ticket, evaluation=None, prune=prune),
        },
    }


def cmd_prune_plan(args: argparse.Namespace) -> int:
    plan = prune_plan_result(scope=args.scope, explicit_path=args.path)
    write_json(PRUNE_PLAN, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def mark_quarantine_candidate(item: dict[str, Any]) -> dict[str, Any]:
    target = norm(str(item.get("target", "")))
    path = Path(target)
    record = {
        "ts": now(),
        "target": target,
        "original_path": target,
        "sha256": file_sha256(path),
        "size": path.stat().st_size if path.is_file() else None,
        "classification": "QUARANTINE_CANDIDATE",
        "confidence": item.get("confidence", 0.0),
        "reason": item.get("reason"),
        "signals": item.get("signals", []),
        "evidence_tier": item.get("evidence_tier", "AMBIGUOUS"),
        "janitor_capability_level": JANITOR_CAPABILITY_LEVEL,
        "status": "MARKED_ONLY",
        "file_moved": False,
        "file_deleted": False,
        "recoverable_at_original_path": path.exists(),
    }
    append_jsonl(QUARANTINE_MANIFEST, record)
    return record


def cmd_prune_apply(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(json.dumps({"ok": False, "error": "prune-apply requires --confirm"}, ensure_ascii=False))
        return 2
    if args.delete:
        print(json.dumps({"ok": False, "error": "Goal Janitor has no delete permission. Use the quarantine manifest for reversible review."}, ensure_ascii=False))
        return 2
    if not confirmed_goal():
        print(json.dumps({"ok": False, "error": "项目原始目标未确认，不能标记隔离候选。"}, ensure_ascii=False))
        return 2
    report = load_json(GOAL_REPORT_JSON, {})
    if report.get("alignment_status") == "MISMATCH":
        print(json.dumps({"ok": False, "error": MISMATCH_MESSAGE}, ensure_ascii=False))
        return 2
    plan = load_json(PRUNE_PLAN, {"items": []})
    actions = []
    for item in plan.get("items", []):
        target = norm(str(item.get("target", "")))
        classification = item.get("classification")
        if classification in {"PROTECTED", "KEEP"}:
            actions.append({"target": target, "action": "skip", "reason": "PROTECTED"})
        elif classification == "BACKLOG_CANDIDATE":
            append_jsonl(BACKLOG, {"ts": now(), "text": target, "source": "prune-plan"})
            actions.append({"target": target, "action": "backlog"})
        elif classification == "QUARANTINE_CANDIDATE":
            record = mark_quarantine_candidate(item)
            actions.append({"target": target, "action": "marked_quarantine", "sha256": record.get("sha256"), "file_moved": False, "file_deleted": False})
        elif classification in {"SIMPLIFY", "SIMPLIFY_CANDIDATE"}:
            actions.append({"target": target, "action": "manual_patch_needed", "reason": "SIMPLIFY"})
        else:
            actions.append({"target": target, "action": "manual_review", "reason": str(classification)})
    print(json.dumps({"ok": True, "capability_level": JANITOR_CAPABILITY_LEVEL, "deleted": False, "moved": False, "quarantine_manifest": str(QUARANTINE_MANIFEST), "actions": actions, "next": "review marks, then run validation or close"}, ensure_ascii=False, indent=2))
    return 0


def scan_artifacts() -> list[dict[str, Any]]:
    global LAST_SCAN_SUMMARY
    paths: list[Path] = []
    metadata_paths: list[Path] = []
    seen_paths: set[str] = set()
    eligible_count = 0
    skipped_non_text = 0
    skipped_large = 0
    truncated_roots: set[str] = set()

    def add_file(path: Path) -> bool:
        nonlocal eligible_count, skipped_non_text, skipped_large
        p = norm(str(path))
        if not p or p in seen_paths or not path.is_file():
            return False
        seen_paths.add(p)
        if not should_scan(p, include_agent_aux=True):
            return False
        if not is_text_artifact(path):
            skipped_non_text += 1
            if len(metadata_paths) < 800:
                metadata_paths.append(path)
            return False
        try:
            if path.stat().st_size > 500000:
                skipped_large += 1
                if len(metadata_paths) < 800:
                    metadata_paths.append(path)
                return False
        except OSError:
            return False
        eligible_count += 1
        if len(paths) >= 1600:
            truncated_roots.add(p.split("/", 1)[0])
            return False
        paths.append(path)
        return True

    def add_priority_root(raw: str, limit: int) -> None:
        root = Path(raw)
        if root.is_file():
            add_file(root)
            return
        if not root.is_dir():
            return
        added = 0
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(
                directory
                for directory in dirs
                if directory not in SCAN_SKIP_DIR_NAMES and not directory.startswith(".venv")
            )
            for name in sorted(files):
                if add_file(Path(current) / name):
                    added += 1
                    if added >= limit:
                        return

    priority_limits = {
        "docs": 60,
        "product": 60,
        "app": 90,
        "apps": 90,
        "packages": 90,
        "services": 90,
        "lib": 60,
        "src": 180,
        "tests": 140,
        "scripts": 90,
        "config": 60,
        "work": 40,
        ".agent/tickets": 30,
    }
    for raw in SCAN_ROOTS:
        add_priority_root(raw, priority_limits.get(raw, 1))

    # Fill remaining capacity from arbitrary project directories only after the
    # declared product roots are represented. A large examples/cache tree must
    # not crowd src/tests/config out of the onboard inventory.
    for root, dirs, files in os.walk("."):
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in SCAN_SKIP_DIR_NAMES and not directory.startswith(".venv")
        )
        for name in sorted(files):
            add_file(Path(root) / name)
    for aux in (DONE, FAILED):
        if aux.is_dir():
            for path in sorted(path for path in aux.rglob("*") if path.is_file()):
                add_file(path)
    if BACKLOG.is_file() and read_text(BACKLOG, 1000).strip():
        add_file(BACKLOG)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in [*paths, *metadata_paths]:
        p = norm(str(path))
        if p in seen or not should_scan(p):
            continue
        seen.add(p)
        out.append({"artifact": p, "kind": kind_for_path(p)})
    LAST_SCAN_SUMMARY = {
        "discovered_files": len(seen_paths),
        "eligible_text_artifacts": eligible_count,
        "selected_text_artifacts": len(paths),
        "metadata_only_artifacts": len(metadata_paths),
        "selected_artifacts": len(out),
        "incomplete": eligible_count > len(paths) or (skipped_non_text + skipped_large) > len(metadata_paths),
        "metadata_inventory_incomplete": (skipped_non_text + skipped_large) > len(metadata_paths),
        "truncated_roots": sorted(truncated_roots),
        "skipped_non_text": skipped_non_text,
        "skipped_large": skipped_large,
    }
    return out


def project_reference_counts(
    items: list[dict[str, Any]],
    north_context: dict[str, Any] | None = None,
) -> dict[str, int]:
    key_targets: dict[str, set[str]] = {}
    for item in items:
        target = norm(str(item["artifact"]))
        path = Path(target)
        keys = {target.lower(), path.name.lower()}
        stem = path.stem.lower()
        if len(stem) >= 5 and stem not in GENERIC_REFERENCE_NAMES:
            keys.add(stem)
        for key in keys:
            key_targets.setdefault(key, set()).add(target)

    counts = {norm(str(item["artifact"])): 0 for item in items}
    for item in items:
        source = norm(str(item["artifact"]))
        source_path = Path(source)
        try:
            if not is_text_artifact(source_path) or source_path.stat().st_size > 500000:
                continue
        except OSError:
            continue
        text = read_text(source_path, 30000).lower()
        if not text:
            continue
        if untrusted_reference_source(source, text, north_context):
            continue
        tokens = set(re.findall(r"[a-z0-9_./-]{4,}", text))
        expanded = set(tokens)
        for token in tokens:
            expanded.add(Path(token).name)
            stem = Path(token).stem
            if len(stem) >= 5 and stem not in GENERIC_REFERENCE_NAMES:
                expanded.add(stem)
            if "." in token and "/" not in token:
                dotted_path = token.replace(".", "/")
                expanded.add(dotted_path)
                expanded.add(dotted_path.rsplit("/", 1)[-1])
        referenced_targets: set[str] = set()
        for token in expanded:
            targets = key_targets.get(token, set())
            if len(targets) == 1:
                referenced_targets.update(targets)
        for target in referenced_targets:
            if target != source:
                counts[target] += 1
    return counts


def untrusted_reference_source(
    path: str,
    body: str = "",
    north_context: dict[str, Any] | None = None,
) -> bool:
    p = norm(path).lower()
    # Structured validation contracts are evaluated separately with authority
    # and command-path checks. Their claims must not become generic references
    # before that trust decision is made.
    if validation_manifest_payload(p) is not None:
        return True
    segments = set(Path(p).parts)
    if segments & {"archive", ".cache", "cache", "debug", "failed", "staging", "imports"}:
        return True
    if p.startswith(("_noise_archive/", "external_research/", ".agent/tickets/failed/", "meta/", "brainstorm/", "scratch/")):
        return True
    if obvious_generated_noise(p, body):
        return True
    north = north_context if isinstance(north_context, dict) else north_star()
    if p.startswith("docs/") and term_hits(body, north.get("backlog_domains", [])) and not maps_to_main_path(body, north):
        return True
    return False


def validation_manifest_payload(path: str) -> dict[str, Any] | None:
    p = Path(path)
    try:
        invalid = p.suffix.lower() != ".json" or not p.is_file() or p.stat().st_size > 500000
    except OSError:
        return None
    if invalid:
        return None
    try:
        data = json.loads(read_text(p, 500000))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("required_paths"), list):
        return None
    if not any(key in data for key in ["command", "validation_order", "required_regressions", "golden_hashes", "sha256"]):
        return None
    return data


def manifest_required_paths(payload: dict[str, Any]) -> set[str]:
    paths = {norm(str(value)) for value in payload.get("required_paths", []) if str(value).strip()}
    paths.update(norm(str(value)) for value in payload.get("required_regressions", []) if str(value).strip())
    for key in ("golden_hashes", "sha256"):
        values = payload.get(key, {})
        if isinstance(values, dict):
            paths.update(norm(str(value)) for value in values if str(value).strip())
    return {path for path in paths if path and not path.startswith("__outside_repo__/")}


def trusted_validation_manifest(
    path: str,
    payload: dict[str, Any],
    project_paths: set[str],
    references: dict[str, int],
) -> bool:
    required = manifest_required_paths(payload)
    if not required or not required.issubset(project_paths):
        return False
    command = str(payload.get("command") or "")
    try:
        command_tokens = shlex.split(command)
    except ValueError:
        return False
    command_paths = {
        norm(token)
        for token in command_tokens
        if "/" in token or Path(token).suffix.lower() in TEXT_FILE_SUFFIXES
    }
    command_exists = any(candidate in project_paths for candidate in command_paths)
    authority = norm(str(payload.get("authority") or ""))
    authority_exists = authority == "GOAL.md" and authority in project_paths
    return references.get(path, 0) > 0 or (authority_exists and command_exists)


def protected_boundary_path(path: str) -> bool:
    low = canonical_text(path)
    markers = [
        "hard limit", "constraint", "interlock", "guardrail", "asset rights", "budget policy",
        "intended use", "authority precedence", "acl semantics", "invariant", "model lock",
        "locked rule", "fail safe", "decision bound", "appeal and hold", "operator only",
        "chain of custody",
    ]
    return any(marker in low for marker in markers)


def project_scan_context(items: list[dict[str, Any]], references: dict[str, int]) -> dict[str, Any]:
    paths = {norm(str(item["artifact"])) for item in items}
    manifests: set[str] = set()
    required: set[str] = set()
    for path in paths:
        payload = validation_manifest_payload(path)
        if payload is None or not trusted_validation_manifest(path, payload, paths, references):
            continue
        manifests.add(path)
        required.update(candidate for candidate in manifest_required_paths(payload) if candidate in paths)

    size_groups: dict[int, list[str]] = {}
    for path in paths:
        file_path = Path(path)
        try:
            if not file_path.is_file() or file_path.stat().st_size > 5_000_000:
                continue
            size_groups.setdefault(file_path.stat().st_size, []).append(path)
        except OSError:
            continue

    hashes: dict[str, list[str]] = {}
    for group in size_groups.values():
        if len(group) < 2:
            continue
        for path in group:
            digest = file_sha256(Path(path))
            if digest:
                hashes.setdefault(digest, []).append(path)

    duplicate_shadows: set[str] = set()
    for group in hashes.values():
        if len(group) < 2:
            continue
        anchors = {path for path in group if path in required or references.get(path, 0) > 0}
        if not anchors:
            continue
        duplicate_shadows.update(path for path in group if path not in anchors and references.get(path, 0) == 0)
    return {
        "validation_manifests": manifests,
        "validation_required_paths": required,
        "duplicate_shadow_paths": duplicate_shadows,
    }


def internal_light_scan(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    scan_north = north_star()
    items = scan_artifacts()
    references = project_reference_counts(items, scan_north)
    project_context = project_scan_context(items, references)
    rows = []
    for item in items:
        c = classify_artifact(item["artifact"], ticket, references, project_context, scan_north)
        rows.append({
            "artifact": item["artifact"],
            "kind": item["kind"],
            "signals": c.get("signals", []),
            "confidence": c.get("confidence", 0.0),
            "suggested_classification": c.get("suggested_classification", c.get("classification")),
            "delete_safe": False,
            "evidence": c.get("evidence", []),
            "evidence_tier": c.get("evidence_tier", "AMBIGUOUS"),
            "janitor_action_limit": JANITOR_CAPABILITY_LEVEL,
            "reason": c.get("reason"),
            "north_star_mapping": c.get("north_star_mapping"),
            "current_ticket_mapping": c.get("current_ticket_mapping"),
            "reference_count": references.get(item["artifact"], 0),
        })
    return rows


def optional_tool_scan(_: str, __: dict[str, Any]) -> list[dict[str, Any]]:
    return []


def scanner_adapter_results(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    rows = internal_light_scan(ticket)
    for name in ["semgrep", "tree-sitter", "knip", "vulture"]:
        if shutil.which(name):
            rows.extend(optional_tool_scan(name, ticket))
    return rows


def onboard_inventory(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    north_confirmed = bool(confirmed_goal())
    rows = []
    for row in scanner_adapter_results(ticket):
        classification = row["suggested_classification"]
        if classification == "SIMPLIFY":
            classification = "SIMPLIFY_CANDIDATE"
        if classification == "KEEP":
            classification = "KEEP"
        if classification == "PROTECTED":
            classification = "PROTECTED"
        negative_scope = classification in {"QUARANTINE_CANDIDATE", "BACKLOG_CANDIDATE", "NOISE_RISK"}
        signals = row.get("signals", [])
        north_mapping = row.get("north_star_mapping")
        if negative_scope and any(s in signals for s in ["anti_pattern", "future_scope", "files_not_changed_violation"]):
            north_mapping = "future_or_rejected_scope"
        elif negative_scope:
            north_mapping = None
        elif not north_mapping:
            north_mapping = confirmed_goal() if maps_to_north_star(artifact_text(row["artifact"])) else None
        rows.append({
            "artifact": row["artifact"],
            "classification": classification,
            "reason": row.get("reason") or "scanner candidate",
            "confidence": 0.0 if not north_confirmed and classification in {"QUARANTINE_CANDIDATE", "BACKLOG_CANDIDATE"} else row.get("confidence", 0.0),
            "delete_safe": False,
            "north_star_mapping": north_mapping,
            "current_ticket_mapping": row.get("current_ticket_mapping"),
            "signals": signals,
            "evidence": row.get("evidence", []),
            "evidence_tier": row.get("evidence_tier", "AMBIGUOUS"),
            "janitor_action_limit": JANITOR_CAPABILITY_LEVEL,
            "reference_count": row.get("reference_count", 0),
        })
    return rows


def cmd_onboard_scan(args: argparse.Namespace) -> int:
    candidates = infer_project_goals()
    north = north_star()
    if not north.get("confirmed"):
        status = "UNKNOWN"
        contradictions = [MISMATCH_MESSAGE]
        required = "confirm_north_star"
    else:
        check = goal_match(candidates[0]["goal"], north)
        status = check["status"]
        contradictions = check["contradicting_evidence"]
        required = check["required_action"]
    # Onboarding already performs an explicit full-repository scan. Recomputing
    # ticket budget baselines here duplicates work and made the scan pay for an
    # unrelated status calculation.
    scan_ticket = janitor_ticket_context(refresh_usage=False)
    inventory = onboard_inventory(scan_ticket)
    report = goal_report(candidates, status=status)
    report["contradictions"] = contradictions
    report["required_action"] = required
    report["goal_alignment"] = status
    report["detected_project_goal"] = report.get("project_detected_goal")
    report["confirmed_north_star_goal"] = north.get("goal")
    report["supporting_evidence"] = candidates[0].get("evidence", []) if candidates else []
    report["contradicting_evidence"] = contradictions
    report["inventory"] = inventory
    report["noise_inventory"] = inventory
    report["scan_summary"] = dict(LAST_SCAN_SUMMARY)
    report["mdcp"] = {
        "protocol_version": MDCP_PROTOCOL_VERSION,
        "layer_1_structured_expression": mdcp_layer_1_structured_expression(
            report.get("project_detected_goal") or "",
            scan_ticket,
        ),
        "goal_alignment": status,
        "noise_evidence": candidates[0].get("noise_evidence", []) if candidates else [],
        "backlog_candidate_evidence": candidates[0].get("backlog_candidate_evidence", []) if candidates else [],
        "scope_sink_candidates": [item for item in inventory if item.get("classification") in {"BACKLOG_CANDIDATE", "NOISE_RISK"}][:20],
        "shit_mountain_candidates": [item for item in inventory if item.get("classification") == "QUARANTINE_CANDIDATE"][:20],
        "janitor_policy": {"capability_level": JANITOR_CAPABILITY_LEVEL, "moves_files": False, "deletes_files": False},
    }
    report["status"] = "NEEDS_CONFIRMATION" if status == "UNKNOWN" else status
    write_goal_report(report)
    if args.verbose:
        output = report
    else:
        classifications: dict[str, int] = {}
        for item in inventory:
            key = str(item.get("classification") or "UNKNOWN")
            classifications[key] = classifications.get(key, 0) + 1
        output = {
            "status": report["status"],
            "alignment_status": status,
            "goal_alignment": status,
            "detected_project_goal": report.get("project_detected_goal"),
            "confirmed_north_star_goal": north.get("goal"),
            "requires_user_confirmation": not bool(north.get("confirmed")),
            "required_action": required,
            "evidence_summary": {
                "supporting": len(report.get("supporting_evidence") or []),
                "contradicting": len(contradictions),
                "noise": len(candidates[0].get("noise_evidence", [])) if candidates else 0,
                "backlog_candidates": len(candidates[0].get("backlog_candidate_evidence", [])) if candidates else 0,
            },
            "inventory_summary": {
                "total": len(inventory),
                "classifications": dict(sorted(classifications.items())),
            },
            "scan_summary": dict(LAST_SCAN_SUMMARY),
            "report_paths": {
                "json": str(GOAL_REPORT_JSON),
                "markdown": str(GOAL_REPORT_MD),
            },
            "mdcp": {
                "goal_alignment": status,
                "scope_sink_candidate_count": sum(
                    1 for item in inventory if item.get("classification") in {"BACKLOG_CANDIDATE", "NOISE_RISK"}
                ),
                "quarantine_candidate_count": sum(
                    1 for item in inventory if item.get("classification") == "QUARANTINE_CANDIDATE"
                ),
                "janitor_action_limit": JANITOR_CAPABILITY_LEVEL,
            },
        }
        if status == "UNKNOWN":
            output["message"] = NORTH_STAR_CONFIRMATION_MESSAGE
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.verbose else None))
    return 0 if status in {"ALIGNED", "PARTIAL", "UNKNOWN"} else 1


def cmd_init(_: argparse.Namespace) -> int:
    for path in (AGENT, CODEX, LENSES, PENDING, DONE, FAILED, AGENT_DOCS, SELFTEST, PROTOCOLS, COORDINATION_CONTRACTS, RUNTIME, BASELINES):
        path.mkdir(parents=True, exist_ok=True)
    if not NORTH_STAR.exists():
        write_json(NORTH_STAR, UNCONFIRMED_NORTH_STAR)
    # Existing North Star content belongs to the project. Init and reinstall
    # must never normalize, enrich, or replace it automatically.
    if not CURRENT_TICKET.exists():
        write_json(CURRENT_TICKET, {"status": "NONE", "active_ticket_id": None, "last_ticket_id": None})
    else:
        existing_ticket = current_ticket()
        if existing_ticket.get("status") != "ACTIVE":
            if existing_ticket.get("ticket_id") and existing_ticket.get("status") in TERMINAL_TICKET_STATUSES:
                folder = DONE if existing_ticket.get("status") == "PASS" else FAILED
                target = folder / f"{existing_ticket.get('ticket_id')}.json"
                if not target.exists():
                    write_json_exclusive(target, compact_terminal_ticket(existing_ticket))
                cleanup_baseline(existing_ticket)
                clear_active_ticket(existing_ticket, target)
            else:
                save_current({
                    "status": "NONE",
                    "active_ticket_id": None,
                    "last_ticket_id": last_ticket().get("ticket_id"),
                    "updated_at": now(),
                })
    if not LAST_TICKET.exists():
        write_json(LAST_TICKET, {})
    if not PROGRAM_PHASE.exists():
        write_json(PROGRAM_PHASE, {"status": "UNSET", "phase_id": None, "goal": None, "exit_criteria": []})
    if not TOOL_MODE.exists():
        write_json(TOOL_MODE, {
            "version": "2.0",
            "enabled": True,
            "mode": "BACKGROUND_ADVISORY",
            "visible_ticket_required": False,
            "ticket_mode": "optional_explicit_contract",
            "intervention_policy": {
                "ordinary_action": "SILENT",
                "semantic_risk": "STRONG_WARNING",
                "confirmed_north_star_deviation": "WARN_WARN_TARGETED_RAIL",
                "deviation_recheck_minutes": 30,
                "deviation_clear_after_corrected_days": 7,
                "deterministic_irreversible_boundary": "BLOCK_ACTION",
            },
            "capabilities": {
                "company_roles": "on_demand",
                "custodian": "on_goal_or_scope_change",
                "auditor": "on_delivery_or_failed_validation",
                "janitor": "on_artifact_sprawl_mark_only",
            },
        })
    else:
        mode = tool_mode_config()
        policy = dict(mode.get("intervention_policy") or {})
        policy.update({
            "confirmed_north_star_deviation": "WARN_WARN_TARGETED_RAIL",
            "deviation_recheck_minutes": 30,
            "deviation_clear_after_corrected_days": 7,
        })
        mode["intervention_policy"] = policy
        write_json(TOOL_MODE, mode)
    if not OBSERVER_STATE.exists():
        write_json(OBSERVER_STATE, empty_observer_state())
    if not CONVERGENCE_STATE.exists():
        write_json(CONVERGENCE_STATE, empty_convergence_state())
    BACKLOG.touch(exist_ok=True)
    REQUEST_DECISIONS.touch(exist_ok=True)
    QUARANTINE_MANIFEST.touch(exist_ok=True)
    if not PRUNE_PLAN.exists():
        write_json(PRUNE_PLAN, {"items": []})
    if not VALIDATION_CATALOG.exists():
        write_json(VALIDATION_CATALOG, DEFAULT_CATALOG)
    else:
        existing_catalog = catalog()
        merged_catalog = dict(DEFAULT_CATALOG)
        merged_catalog.update(existing_catalog)
        if merged_catalog != existing_catalog:
            write_json(VALIDATION_CATALOG, merged_catalog)
    ensure_feedback_config(AGENT)
    ensure_reuse_probe_config(AGENT)
    for name, content in DEFAULT_LENSES.items():
        target = LENSES / name
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    doc = AGENT_DOCS / "README_GOAL_COMPASS.md"
    if not doc.exists():
        doc.write_text(AGENT_README, encoding="utf-8")
    protocol_doc = PROTOCOLS / "mdcp.md"
    if not protocol_doc.exists():
        protocol_doc.write_text(MDCP_PROTOCOL_MD, encoding="utf-8")
    schema_doc = PROTOCOLS / "mdcp.schema.json"
    if not schema_doc.exists():
        write_json(schema_doc, MDCP_SCHEMA_DOC)
    ensure_llm_judge_schema(LLM_JUDGE_SCHEMA_PATH)
    # Init also serves as an in-place runtime upgrade. Re-project an existing
    # confirmed goal so a newly introduced convergence state cannot disagree
    # with the preserved North Star until another command happens to refresh it.
    refresh_convergence_projection(persist=True)
    write_json(HOOKS, merge_hooks_json(load_json(HOOKS, {}), hooks_json()))
    print(json.dumps({
        "ok": True,
        "message": "Codex Goal Supervisor initialized in background advisory mode",
        "tool_mode": tool_mode_config(),
    }, ensure_ascii=False))
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    ticket = compile_ticket(Path(args.rough_task), Path(args.out))
    missing = validate_shape(ticket)
    if missing:
        print(json.dumps({"ok": False, "errors": missing}, ensure_ascii=False))
        return 2
    write_json(Path(args.out), ticket)
    print(json.dumps({
        "ok": True,
        "out": args.out,
        "ticket_id": ticket["ticket_id"],
        "status": "DRAFT",
        "acceptance_ready": False,
        "supervision": ticket.get("supervision"),
    }, ensure_ascii=False))
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    path = Path(args.ticket)
    ticket = load_json(path, {})
    if not ticket:
        print(json.dumps({"ok": False, "errors": [f"ticket not found or invalid JSON: {path}"]}, ensure_ascii=False))
        return 2
    if ticket.get("status") == "ACTIVE" or ticket.get("status") in TERMINAL_TICKET_STATUSES:
        print(json.dumps({"ok": False, "errors": [f"ready only applies before execution, not {ticket.get('status')} tickets"]}, ensure_ascii=False))
        return 2
    ticket["status"] = "PENDING"
    ticket["acceptance_ready"] = True
    ticket = refresh_batch_execution(ticket)
    ticket = refresh_coordination_contract(ticket)
    ticket = refresh_supervision_decision(ticket)
    ticket = refresh_mdcp_contract(ticket)
    ticket = refresh_reuse_discovery(ticket)
    if isinstance(ticket.get("company_runtime"), dict):
        initialize_company_runtime(ticket)
    ticket["acceptance_quality"] = acceptance_quality(ticket)
    ticket["preflight"] = preflight_ticket(ticket)
    errors = start_errors(ticket)
    if errors:
        feedback = report_governance_feedback(
            "ticket_preflight_block",
            "; ".join(errors[:8]),
            source="ready",
            rule_id="READY_CONTRACT",
            command="ready",
            ticket=ticket,
            status="BLOCKED",
            context={"errors": errors[:20], "reuse": reuse_compact_status(ticket)},
        )
        print(json.dumps({
            "ok": False,
            "ticket": str(path),
            "errors": errors,
            "supported_acceptance": {
                "commands_pass": "validation_catalog ids only, not raw shell commands",
                "contains": ["path::required text", {"file": "path", "text": "required text"}],
                "assertions": [
                    {"type": "file_exists", "path": "path"},
                    {"type": "file_contains", "file": "path", "text": "required text"},
                    {"type": "json_field_equals", "file": "path", "path": "a.b", "equals": "value"},
                ],
            },
            "company_subagents": company_subagent_summary(ticket),
            "preflight": ticket["preflight"],
            "reuse": reuse_compact_status(ticket),
            "direct_reuse_candidates": ticket.get("reuse_discovery", {}).get("direct_reuse_candidates", []),
            "feedback_event_id": feedback.get("event_id"),
        }, ensure_ascii=False, indent=2))
        return 2
    write_json(path, ticket)
    output = {
        "ok": True,
        "ticket": str(path),
        "ticket_id": ticket.get("ticket_id"),
        "status": "PENDING",
        "acceptance_ready": True,
        "acceptance_quality": ticket["acceptance_quality"],
        "preflight": ticket["preflight"],
        "company_subagents": company_subagent_summary(ticket),
        "axis_advisory": axis_advisory(ticket),
        "supervision": ticket.get("supervision"),
        "reuse": reuse_compact_status(ticket),
    }
    if args.verbose:
        output["mdcp_audit"] = mdcp_audit(ticket)
        output["mdcp_contract"] = ticket.get("mdcp")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


@serialized_current_state
def cmd_start(args: argparse.Namespace) -> int:
    current = current_ticket()
    if current.get("status") == "ACTIVE":
        feedback = report_governance_feedback(
            "ticket_start_block",
            "ACTIVE ticket already exists in this worktree",
            source="start",
            rule_id="ONE_ACTIVE_TICKET_PER_WORKTREE",
            command="start",
            ticket=current,
            status="BLOCKED",
        )
        print(json.dumps({"ok": False, "error": "ACTIVE ticket already exists", "ticket_id": current.get("ticket_id"), "feedback_event_id": feedback.get("event_id")}, ensure_ascii=False))
        return 2
    source_path = Path(args.ticket)
    ticket = load_json(source_path, {})
    ticket = refresh_coordination_contract(ticket)
    ticket = refresh_supervision_decision(ticket)
    ticket = refresh_mdcp_contract(ticket)
    ticket = refresh_reuse_discovery(ticket)
    ticket["preflight"] = preflight_ticket(ticket)
    errors = start_errors(ticket)
    if errors:
        feedback = report_governance_feedback(
            "ticket_start_block",
            "; ".join(errors[:8]),
            source="start",
            rule_id="START_CONTRACT",
            command="start",
            ticket=ticket,
            status="BLOCKED",
            context={"errors": errors[:20], "reuse": reuse_compact_status(ticket)},
        )
        print(json.dumps({
            "ok": False,
            "errors": errors,
            "preflight": ticket.get("preflight"),
            "company_subagents": company_subagent_summary(ticket),
            "reuse": reuse_compact_status(ticket),
            "direct_reuse_candidates": ticket.get("reuse_discovery", {}).get("direct_reuse_candidates", []),
            "feedback_event_id": feedback.get("event_id"),
        }, ensure_ascii=False))
        return 2
    ok, reason = goal_allows_ticket(ticket)
    if not ok:
        report_governance_feedback(
            "goal_alignment_block",
            reason,
            source="start",
            rule_id="NORTH_STAR_ALIGNMENT",
            command="start",
            ticket=ticket,
            status="BLOCKED",
        )
        print(json.dumps({"ok": False, "error": reason, "required_action": "confirm_north_star"}, ensure_ascii=False))
        return 2
    conflicts = ticket_id_conflicts(str(ticket.get("ticket_id") or ""), source_path)
    if conflicts:
        report_governance_feedback(
            "ticket_identity_conflict",
            "ticket_id already exists in active or terminal history",
            source="start",
            rule_id="TICKET_ID_UNIQUE",
            command="start",
            ticket=ticket,
            status="BLOCKED",
            context={"conflicts": conflicts[:20]},
        )
        print(json.dumps({
            "ok": False,
            "error": "ticket_id already exists in active or terminal history",
            "ticket_id": ticket.get("ticket_id"),
            "conflicts": conflicts,
            "required_action": "create_new_ticket_id_and_set_retry_of_or_supersedes",
        }, ensure_ascii=False, indent=2))
        return 2
    ticket["status"] = "ACTIVE"
    ticket["run_id"] = uuid.uuid4().hex
    ticket.pop("last_evaluation", None)
    ticket["source_ticket_path"] = norm(str(source_path))
    ticket["source_ticket_sha256"] = sha256_file_contents(source_path)
    ticket["acceptance_quality"] = acceptance_quality(ticket)
    ticket["budget_used"] = {
        "tool_calls": 0,
        "tool_calls_by_type": {"read": 0, "write": 0, "validation": 0, "agent": 0, "external": 0, "failed": 0},
        "budget_enforcement": "ADVISORY_UNVERIFIED",
        "changed_files": [],
        "diff_lines": 0,
        "started_at": now(),
        "last_metered_at": now(),
        "elapsed_minutes": 0,
        "wall_clock_minutes": 0,
        "hook_nonce": uuid.uuid4().hex,
    }
    lane_ok, lane_conflicts, lane_summary = reserve_parallel_lane(ticket)
    if not lane_ok:
        print(json.dumps({
            "ok": False,
            "error": "PARALLEL_TICKET_CONFLICT",
            "conflicts": lane_conflicts,
            "parallel_execution": lane_summary,
            "required_action": "use_separate_non_overlapping_worktree_or_run_serially",
        }, ensure_ascii=False, indent=2))
        return 2
    ticket["execution_lane"] = lane_summary
    if not is_git_repo():
        ticket["budget_used"]["baseline_top_level_entries"] = current_top_level_entries()
        persist_baseline(ticket, snapshot(ticket))
        ticket["budget_used"]["baseline_volatile_snapshot"] = snapshot(ticket, only_volatile=True)
    write_json(HOOK_STATE, {
        "ticket_id": ticket.get("ticket_id"),
        "run_id": ticket.get("run_id"),
        "hook_nonce": ticket["budget_used"]["hook_nonce"],
        "started_at": now(),
        "pre_events": 0,
        "post_events": 0,
        "tool_calls_by_type": {"read": 0, "write": 0, "validation": 0, "agent": 0, "external": 0, "failed": 0},
        "event_log_size": 0,
        "recent_event_ids": [],
        "status": "AWAITING_HEARTBEAT",
    })
    initialize_company_runtime(ticket)
    ticket["reuse_contract_at_start"] = {
        "context_fingerprint": ticket.get("reuse_discovery", {}).get("context_fingerprint"),
        "checked_at": ticket.get("reuse_discovery", {}).get("checked_at"),
        "decision": ticket.get("reuse_decision", {}),
        "integration": ticket.get("reuse_integration", {}),
    }
    ticket["acceptance_fingerprint"] = acceptance_fingerprint(ticket)
    save_current(ticket)
    refresh_convergence_projection(
        current_action=str(ticket.get("task_goal") or "") or None,
        expected_evidence="validation_catalog" if validation_ids(ticket) else "machine acceptance evidence",
    )
    output = {
        "ok": True,
        "status": "ACTIVE",
        "ticket_id": ticket["ticket_id"],
        "company_subagents": company_subagent_summary(ticket),
        "axis_advisory": axis_advisory(ticket),
        "parallel_execution": lane_summary,
        "supervision": ticket.get("supervision"),
        "reuse": reuse_compact_status(ticket),
    }
    if args.verbose:
        output["mdcp_audit"] = mdcp_audit(ticket)
        output["mdcp_contract"] = ticket.get("mdcp")
    print(json.dumps(output, ensure_ascii=False))
    return 0


def backlog_count() -> int:
    if not BACKLOG.exists():
        return 0
    try:
        return sum(1 for line in BACKLOG.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def budget_status_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    usage = ticket.get("budget_used", {}) if isinstance(ticket, dict) else {}
    changed = list(usage.get("changed_files", []))
    events = cached_hook_event_summary(ticket) if ticket else {"post_events": 0, "tool_calls_by_type": {}}
    connected_calls = int(events.get("post_events", 0) or 0)
    enforcement = (
        "CONNECTED_VERIFIED"
        if connected_calls and events.get("complete")
        else usage.get("budget_enforcement", "ADVISORY_UNVERIFIED")
    )
    return {
        "tool_calls": connected_calls if enforcement == "CONNECTED_VERIFIED" else None,
        "recorded_tool_calls": connected_calls or int(usage.get("tool_calls", 0) or 0),
        "tool_calls_by_type": events.get("tool_calls_by_type") if connected_calls else usage.get("tool_calls_by_type", {}),
        "budget_enforcement": enforcement,
        "active_metered_minutes": float(usage.get("elapsed_minutes", 0) or 0),
        "wall_clock_minutes": usage.get("wall_clock_minutes"),
        "changed_files_count": int(usage.get("changed_files_count", len(changed)) or 0),
        "changed_files_preview": changed[:12],
        "changed_files_omitted": max(0, len(changed) - 12),
        "diff_lines": int(usage.get("diff_lines", 0) or 0),
        "started_at": usage.get("started_at"),
        "last_metered_at": usage.get("last_metered_at"),
    }
def cmd_status(args: argparse.Namespace) -> int:
    loaded = current_ticket()
    active = loaded.get("status") == "ACTIVE"
    ticket = loaded if active else {}
    mdcp_fields = ticket.get("mdcp", {}).get("layer_1_structured_expression", {}) if isinstance(ticket, dict) else {}
    cached = ticket.get("last_evaluation") if active and isinstance(ticket.get("last_evaluation"), dict) else None
    evaluation = cached if cached else ({
        "status": "ACTIVE_UNCHECKED",
        "reasons": [],
        "suggested_action": "check",
    } if active else None)
    auditor = mdcp_auditor_from_evaluation(ticket, evaluation) if active else {}
    inactive_action = "continue_normal_execution"
    company = company_subagent_summary(ticket) if active and args.verbose else compact_company_summary(ticket) if active else {}
    runtime_action = (
        "complete_or_retry_required_departments"
        if company.get("required") and not company.get("runtime_execution_verified")
        else auditor.get("required_action", "continue") if active else inactive_action
    )
    north = north_star()
    definition = goal_definition_summary(north)
    observer = observer_summary()
    convergence = compact_convergence_status(convergence_state())
    convergence_stack = convergence.get("goal_stack", {}) if isinstance(convergence.get("goal_stack"), dict) else {}
    convergence_compact = {
        "goal_stack": {
            key: convergence_stack.get(key)
            for key in (
                "l0_final_goal", "l2_current_stage", "l3_current_action",
                "l3_expected_evidence", "goal_contract",
            )
        },
        "success_criteria_count": len(convergence_stack.get("l1_success_criteria") or []),
        "progress": convergence.get("progress", {}),
        "recovery": {
            key: (convergence.get("recovery") or {}).get(key)
            for key in ("blocked_reason", "recommended_action")
        },
    }
    collaboration = convergence.get("collaboration") if isinstance(convergence.get("collaboration"), dict) else {}
    if args.verbose or collaboration.get("status") not in {None, "IDLE"}:
        convergence_compact["collaboration"] = collaboration
    completion = convergence.get("goal_completion", {}) if isinstance(convergence.get("goal_completion"), dict) else {}
    completion_certified = completion.get("status") == "CERTIFIED_COMPLETE"
    if not active or completion.get("status") != "NOT_CERTIFIED":
        convergence_compact["goal_completion"] = completion.get("status", "NOT_CERTIFIED")
    axis = axis_advisory(ticket if active else None)
    last_check = {
        "status": evaluation.get("status"),
        "failure_class": evaluation.get("failure_class"),
        "suggested_action": evaluation.get("suggested_action"),
        "checked_at": evaluation.get("checked_at"),
        "retry_from": evaluation.get("retry_from"),
    } if evaluation else None
    truth_status = evaluation.get("status") if active and evaluation else (
        "GOAL_CERTIFIED_COMPLETE" if completion_certified else
        "IDLE" if north.get("confirmed") else "NEEDS_CONFIRMATION"
    )
    reasons = list(evaluation.get("reasons") or []) if evaluation else []
    reason = reasons[0] if reasons else (
        "Current ticket has not been checked." if active else
        "Final North Star regression passed." if completion_certified else
        "No ACTIVE ticket; normal execution may continue." if north.get("confirmed") else
        NORTH_STAR_CONFIRMATION_MESSAGE
    )
    if completion_certified and not active:
        runtime_action = "deliver_verified_result"
    observer_compact = {
        key: observer.get(key)
        for key in (
            "mode", "enabled", "events", "writes", "tracked_path_count", "last_event_at",
            "fallback_pending_events", "fallback_events_recovered", "fallback_overflow_detected",
            "deviations",
        )
        if key in observer
    }
    payload: dict[str, Any] = {
        "status": truth_status,
        "active": active,
        "reason": reason,
        "required_action": runtime_action,
        "observer": observer_compact,
        "convergence": convergence_compact,
        "current_ticket": {
            "ticket_id": ticket.get("ticket_id"),
            "status": ticket.get("status"),
            "task_goal": str(ticket.get("task_goal") or "")[:320],
            "last_check": last_check,
        } if active else None,
        "north_star": {
            "confirmed": bool(north.get("confirmed")),
            "goal": north.get("goal"),
            "definition_quality": definition.get("quality"),
        },
        "axis_advisory": axis,
        "mdcp": {
            "acceptance_consumer": mdcp_fields.get("acceptance_consumer"),
            "scope_anchor": list(mdcp_fields.get("scope_anchor") or [])[:6],
            "axis_fatigue_check": auditor.get("axis_fatigue_check", "none"),
            "current_required_action": runtime_action,
        },
        "backlog": {
            "count": backlog_count(),
        },
    }
    if args.verbose:
        recent = recent_done_tickets(limit=5)
        previous = last_ticket()
        payload.update({
            "convergence": convergence,
            "tool_mode": tool_mode_config(),
            "observer": observer,
            "current_ticket": {
                "ticket_id": ticket.get("ticket_id"),
                "status": ticket.get("status"),
                "task_goal": ticket.get("task_goal"),
                "budget_used": budget_status_summary(ticket),
                "budget_limits": ticket.get("budget", {}),
                "budget_basis": ticket.get("budget_basis", {}),
                "runtime_checkpoint": ticket.get("runtime_checkpoint"),
                "state_revision": ticket.get("state_revision"),
                "last_check": last_check,
            } if active else None,
            "north_star": {
                "confirmed": bool(north.get("confirmed")),
                "goal": north.get("goal"),
                "definition": definition,
                "preservation_policy": "existing_goal_is_read_only_unless_user_explicitly_replaces_it",
            },
            "last_ticket": ({
                key: previous.get(key)
                for key in ("ticket_id", "title", "status", "closed_at", "archive_path")
            } if previous else None),
            "recent_done_tickets": [
                {
                    "ticket_id": row.get("ticket_id"),
                    "title": row.get("title"),
                    "axis": ticket_axis(row),
                    "closed_at": row.get("closed_at"),
                }
                for row in recent
            ],
            "hook": hook_health(ticket if active else None),
            "program_phase": program_phase(),
            "parallel_execution": parallel_execution_summary(ticket if active else None),
            "supervision": ticket.get("supervision") if active else None,
            "capability_pool": {
                "company_roles": "AI_OPTIONAL",
                "custodian": "implicit_lightweight_plus_AI_OPTIONAL_explicit",
                "auditor": "implicit_signal_plus_AI_OPTIONAL_check",
                "janitor": "implicit_sprawl_signal_plus_AI_OPTIONAL_mark_only",
            },
            "reuse": reuse_compact_status(ticket if active else None),
            "feedback": feedback_status(AGENT),
            "context_continuity": context_continuity_status(
                Path.cwd(), CONTEXT_CONTINUITY_STATE, CONTEXT_CAPSULE,
            ),
            "goal_return": goal_return_status(GOAL_RETURN_STATE),
            "mdcp": {
                "precision_level": mdcp_fields.get("precision_level"),
                "conversation_plane": mdcp_fields.get("conversation_plane"),
                "scope_anchor": mdcp_fields.get("scope_anchor"),
                "acceptance_consumer": mdcp_fields.get("acceptance_consumer"),
                "axis_fatigue_check": auditor.get("axis_fatigue_check", "none"),
                "current_required_action": runtime_action,
                "company_subagents": company,
                "layer_3_janitor_auditor": {"auditor": auditor} if auditor else {},
            },
            "backlog": {"path": str(BACKLOG), "count": backlog_count()},
        })
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.verbose else None))
    return 0


def cmd_context_note(args: argparse.Namespace) -> int:
    """Store an explicit local conclusion checkpoint for one project directory."""
    try:
        result = record_semantic_checkpoint(
            Path.cwd(),
            CONTEXT_CONTINUITY_STATE,
            RUNTIME / "context_continuity.lock",
            CONTEXT_CAPSULE,
            directory=args.directory,
            confirmed_facts=list(args.fact or []),
            key_interfaces=list(args.interface or []),
            dependencies=list(args.dependency or []),
            open_questions=list(args.open_question or []),
            next_action=args.next_action,
            evidence_paths=list(args.evidence or []),
        )
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


@serialized_current_state
def cmd_check(args: argparse.Namespace) -> int:
    ticket = current_ticket()
    if ticket.get("status") != "ACTIVE":
        print(json.dumps({"status": "FAIL", "reasons": ["no ACTIVE ticket"], "suggested_action": "abort"}, ensure_ascii=False))
        return 1
    ticket = refresh_coordination_contract(ticket)
    ticket = refresh_mdcp_contract(ticket)
    result = evaluate(ticket, run_commands=bool(args.run_validation))
    prune = prune_check_result()
    result["prune_check"] = prune
    primary_failure = result["status"] in {
        "VALIDATION_FAILED", "ACCEPTANCE_INCOMPLETE", "UPSTREAM_EVIDENCE_INVALID",
        "BUDGET_EXCEEDED", "DIFF_BUDGET_EXCEEDED_CLEAN", "FAIL", "DRIFT",
    }
    if prune["status"] == "ARTIFACT_SPRAWL":
        result.setdefault("secondary_advisories", []).extend(prune["reasons"])
        result["suppressed_secondary_findings"] = len(prune["reasons"]) if primary_failure else 0
        result["cleanup_advisory"] = "review_quarantine_plan"
    elif prune["status"] == "NOISE_RISK" and result["status"] in {"ON_TRACK", "NEEDS_VALIDATION", "PASS_READY"}:
        result.setdefault("secondary_advisories", []).extend(prune["reasons"])
        result["cleanup_advisory"] = "prune_plan"
    company = company_subagent_summary(ticket)
    pass_ready_statuses = {"PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY"}
    if result["status"] in pass_ready_statuses and company.get("required") and not company.get("runtime_execution_verified"):
        result["suggested_action"] = "complete_or_retry_required_departments"
    result["close_readiness"] = {
        "machine_acceptance_ready": result["status"] in pass_ready_statuses,
        "company_runtime_complete": company.get("runtime_execution_verified", True),
        "company_runtime_required": False,
        "janitor_clear": True,
        "janitor_advisory": prune["status"] in {"NOISE_RISK", "ARTIFACT_SPRAWL"},
    }
    if args.verbose:
        result["mdcp_audit"] = mdcp_audit(
            ticket,
            evaluation=result,
            run_commands=bool(args.run_validation),
            require_validation_run=True,
        )
        result["mdcp_contract"] = refresh_mdcp_contract(ticket)["mdcp"]
    result["mdcp"] = compact_mdcp_status(ticket, result, prune)
    result["axis_advisory"] = axis_advisory(ticket)
    result["supervision"] = ticket.get("supervision")
    result["auditor"] = {
        "implicit_mode": "background_signal_only",
        "explicit_mode": "AI_OPTIONAL_CHECK",
        "binding": False,
        "intervention": (
            "SILENT"
            if result.get("status") in {"ON_TRACK", "PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY"}
            else "STRONG_WARNING"
        ),
        "certification_authority": "close_only",
    }
    if result.get("status") not in {"ON_TRACK", "PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY", "NEEDS_VALIDATION"}:
        feedback = report_governance_feedback(
            "governance_check_advisory",
            "; ".join(str(value) for value in result.get("reasons", [])[:8]) or str(result.get("status")),
            source="check",
            rule_id=str(result.get("failure_class") or result.get("status") or "CHECK_BLOCK"),
            command="check --run-validation" if args.run_validation else "check",
            ticket=ticket,
            status=str(result.get("status") or "FAIL"),
            context={"suggested_action": result.get("suggested_action"), "retry_from": result.get("retry_from")},
        )
        result["feedback_event_id"] = feedback.get("event_id")
    ticket["last_evaluation"] = {
        "status": result.get("status"),
        "failure_class": result.get("failure_class"),
        "reasons": list(result.get("reasons", []))[:5],
        "suggested_action": result.get("suggested_action"),
        "retry_from": result.get("retry_from"),
        "checked_at": now(),
        "validation": result.get("validation"),
        "prune_status": prune.get("status"),
    }
    save_current(ticket)
    if args.run_validation and result.get("status") in {"PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY"}:
        state = refresh_convergence_projection(persist=False)
        validation_digest = hashlib.sha256(
            json.dumps(result.get("validation", {}), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        state = record_convergence_evidence(
            state,
            evidence_id=f"ticket-validation:{ticket.get('ticket_id')}:{validation_digest}",
            kind="ticket_validation",
            summary="The active ticket validation completed successfully.",
            observed_at=now(),
        )
        write_json(CONVERGENCE_STATE, state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_backlog(args: argparse.Namespace) -> int:
    append_jsonl(BACKLOG, {"ts": now(), "text": args.text})
    print(json.dumps({"ok": True, "backlog": str(BACKLOG)}, ensure_ascii=False))
    return 0


@serialized_current_state
def cmd_evidence_add(args: argparse.Namespace) -> int:
    ticket = active_ticket()
    if not ticket:
        print(json.dumps({"ok": False, "error": "no ACTIVE ticket"}, ensure_ascii=False))
        return 1
    artifact_path = norm(str(args.path)) if args.path else None
    path_hash = None
    if artifact_path and Path(artifact_path).is_file():
        try:
            path_hash = sha256_file_contents(Path(artifact_path))
        except OSError:
            path_hash = None
    entry = {
        "evidence_id": uuid.uuid4().hex,
        "ts": now(),
        "type": args.type,
        "source": args.source,
        "status": args.status,
        "summary": args.summary,
        "path": artifact_path,
        "sha256": path_hash,
        "acceptance_id": args.acceptance_id,
    }
    ticket.setdefault("evidence", []).append(entry)
    if args.type == "runtime":
        checkpoint = {
            "owner": args.owner or str(ticket.get("ticket_id")),
            "pid": args.pid,
            "port": args.port,
            "checkpoint_id": args.checkpoint_id,
            "resume_command": args.resume_command,
            "resources": list(args.resource or []),
            "evidence_id": entry["evidence_id"],
            "path": artifact_path,
            "sha256": path_hash,
            "updated_at": now(),
            "authority": "evidence_only_no_cross_task_kill_authority",
        }
        ticket["runtime_checkpoint"] = checkpoint
    save_current(ticket)
    state = refresh_convergence_projection(persist=False)
    state = record_convergence_evidence(
        state,
        evidence_id=str(entry["evidence_id"]),
        kind=str(entry["type"]),
        summary=str(entry["summary"]),
        observed_at=str(entry["ts"]),
    )
    write_json(CONVERGENCE_STATE, state)
    print(json.dumps({
        "ok": True,
        "evidence": entry,
        "evidence_count": len(ticket["evidence"]),
        "runtime_checkpoint": ticket.get("runtime_checkpoint") if args.type == "runtime" else None,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_evidence_list(_: argparse.Namespace) -> int:
    ticket = active_ticket()
    entries = ticket.get("evidence", []) if ticket else []
    print(json.dumps({"active": bool(ticket), "ticket_id": ticket.get("ticket_id") if ticket else None, "evidence": entries}, ensure_ascii=False, indent=2))
    return 0


def ticket_id_conflicts(ticket_id: str, source_path: Path | None = None) -> list[str]:
    conflicts: list[str] = []
    for folder in [DONE, FAILED]:
        direct = folder / f"{ticket_id}.json"
        if direct.exists():
            conflicts.append(norm(str(direct)))
    active = active_ticket()
    if active.get("ticket_id") == ticket_id:
        conflicts.append(str(CURRENT_TICKET))
    for path in PENDING.glob("*.json") if PENDING.exists() else []:
        if source_path and path.resolve() == source_path.resolve():
            continue
        try:
            if load_json(path, {}).get("ticket_id") == ticket_id:
                conflicts.append(norm(str(path)))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(set(conflicts))


def compact_terminal_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    compacted = json.loads(json.dumps(ticket, ensure_ascii=False))
    usage = compacted.get("budget_used", {})
    if isinstance(usage, dict):
        for key in ("baseline_snapshot", "baseline_volatile_snapshot", "baseline_ref", "baseline_sha256", "baseline_entry_count", "baseline_top_level_entries"):
            usage.pop(key, None)
    return compacted


def remove_pending_source(ticket: dict[str, Any]) -> None:
    source = ticket.get("source_ticket_path")
    if not source:
        return
    path = Path(str(source))
    try:
        path.resolve().relative_to(PENDING.resolve())
    except (OSError, ValueError):
        return
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def move_finished(ticket: dict[str, Any], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{ticket.get('ticket_id', 'ticket')}.json"
    if target.exists():
        raise FileExistsError(f"terminal ticket history already exists and cannot be overwritten: {target}")
    write_json_exclusive(target, compact_terminal_ticket(ticket))
    return target


def finalize_ticket(ticket: dict[str, Any], folder: Path) -> Path:
    path = move_finished(ticket, folder)
    remove_pending_source(ticket)
    cleanup_baseline(ticket)
    clear_active_ticket(ticket, path)
    return path


@serialized_current_state
def cmd_close(args: argparse.Namespace) -> int:
    ticket = current_ticket()
    if ticket.get("status") != "ACTIVE":
        print(json.dumps({"ok": False, "error": "no ACTIVE ticket"}, ensure_ascii=False))
        return 1
    ticket = refresh_coordination_contract(ticket)
    ticket = refresh_mdcp_contract(ticket)
    if not has_machine_acceptance(ticket):
        feedback = report_governance_feedback(
            "close_block",
            NO_PASS_ACCEPTANCE_MESSAGE,
            source="close",
            rule_id="MACHINE_ACCEPTANCE_REQUIRED",
            command="close",
            ticket=ticket,
            status="FAIL",
        )
        fail_result = {"status": "FAIL", "reasons": [NO_PASS_ACCEPTANCE_MESSAGE], "suggested_action": "abort"}
        fail_result["mdcp"] = compact_mdcp_status(ticket, fail_result, None)
        ticket["last_evaluation"] = {**fail_result, "checked_at": now()}
        save_current(ticket)
        print(json.dumps({
            "status": "NOT_CERTIFIED",
            "ticket_status": "ACTIVE",
            "reasons": [NO_PASS_ACCEPTANCE_MESSAGE],
            "mdcp": fail_result["mdcp"],
            "feedback_event_id": feedback.get("event_id"),
        }, ensure_ascii=False, indent=2))
        return 1
    company = company_subagent_summary(ticket)
    company_advisories = []
    if company.get("recommended") and not company.get("runtime_execution_verified"):
        company_advisories.append(
            "Optional company role results are incomplete: " + ", ".join(company.get("missing_roles", []))
        )
    prune = prune_check_result()
    result = evaluate(ticket, run_commands=True)
    result["prune_check"] = prune
    if args.verbose:
        result["mdcp_audit"] = mdcp_audit(ticket, evaluation=result, run_commands=True, require_validation_run=True)
        result["mdcp_contract"] = refresh_mdcp_contract(ticket)["mdcp"]
    result["mdcp"] = compact_mdcp_status(ticket, result, prune)
    if prune["status"] == "ARTIFACT_SPRAWL":
        result.setdefault("secondary_advisories", []).extend(prune["reasons"])
        result["cleanup_advisory"] = "review_quarantine_plan"
        result["mdcp"] = compact_mdcp_status(ticket, result, prune)
    truth_criteria = {
        key: value
        for key, value in result["mdcp"]["layer_3_pass_criteria"].items()
        if key in {
            "validation_not_failed", "acceptance_consumer_known",
            "scope_anchor_not_violated", "close_requires_validation_pass",
        }
    }
    layer_3_ok = all(truth_criteria.values())
    if result["status"] in {"PASS_READY", "IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY"}:
        if not layer_3_ok:
            result["status"] = "FAIL"
            result["reasons"].append("MDCP layer_3_pass_criteria failed")
            result["suggested_action"] = "fix_validation_or_contract"
            result["mdcp"] = compact_mdcp_status(ticket, result, prune)
        else:
            ticket = mark_reuse_integration_verified(ticket, AGENT)
            ticket["status"] = "PASS"
            ticket["closed_at"] = now()
            ticket["close_result"] = result
            phase_result = None
            phase = program_phase()
            phase_policy = ticket.get("phase_completion", {}) if isinstance(ticket.get("phase_completion"), dict) else {}
            if (
                phase_policy.get("complete_on_pass") is True
                and phase.get("status") == "ACTIVE"
                and ticket.get("program_phase_id") == phase.get("phase_id")
            ):
                phase_result = complete_program_phase(
                    f"Ticket {ticket.get('ticket_id')} passed its frozen acceptance.",
                    str(ticket.get("ticket_id")),
                )
            try:
                path = finalize_ticket(ticket, DONE)
            except FileExistsError as exc:
                print(json.dumps({"status": "FAIL", "reasons": [str(exc)], "suggested_action": "create_new_ticket_id"}, ensure_ascii=False))
                return 1
            refresh_convergence_projection(
                current_action="Select the next highest-value stage action.",
                expected_evidence=None,
            )
            output = {
                "status": "PASS",
                "ticket": str(path),
                "validation": result.get("validation"),
                "quality": result.get("quality"),
                "environment_status": result.get("environment_status"),
                "program_phase": phase_result,
                "mdcp": result["mdcp"],
                "supervision": ticket.get("supervision"),
                "reuse": reuse_compact_status(ticket, AGENT),
                "advisories": [*company_advisories, *result.get("secondary_advisories", [])],
            }
            if args.verbose:
                output["details"] = result
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
    terminal_status = result["status"] if result["status"] in {
        "UPSTREAM_EVIDENCE_INVALID", "DRIFT", "ENVIRONMENT_DIRTY", "ARTIFACT_SPRAWL",
    } else "FAIL"
    feedback = report_governance_feedback(
        "close_failure",
        "; ".join(str(value) for value in result.get("reasons", [])[:8]) or terminal_status,
        source="close",
        rule_id=str(result.get("failure_class") or terminal_status),
        command="close",
        ticket=ticket,
        status=terminal_status,
        context={"suggested_action": result.get("suggested_action"), "retry_from": result.get("retry_from")},
    )
    ticket["last_evaluation"] = {
        "status": result.get("status"),
        "failure_class": result.get("failure_class"),
        "reasons": list(result.get("reasons", []))[:5],
        "suggested_action": result.get("suggested_action"),
        "retry_from": result.get("retry_from"),
        "checked_at": now(),
        "validation": result.get("validation"),
        "prune_status": prune.get("status"),
    }
    save_current(ticket)
    output = {
        "status": "NOT_CERTIFIED",
        "evaluation_status": terminal_status,
        "ticket_status": "ACTIVE",
        "reasons": result["reasons"],
        "suggested_action": result.get("suggested_action"),
        "failure_class": result.get("failure_class"),
        "retry_from": result.get("retry_from"),
        "validation": result.get("validation"),
        "quality": result.get("quality"),
        "prune_check": {"status": prune.get("status"), "reasons": prune.get("reasons", [])},
        "mdcp": result["mdcp"],
        "supervision": ticket.get("supervision"),
        "feedback_event_id": feedback.get("event_id"),
        "advisories": [*company_advisories, *result.get("secondary_advisories", [])],
    }
    if args.verbose:
        output["details"] = result
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1


def git_diff_paths() -> list[str]:
    return git_changed_files() if is_git_repo() else []


@serialized_current_state
def cmd_abort(args: argparse.Namespace) -> int:
    ticket = current_ticket()
    if ticket.get("status") != "ACTIVE":
        print(json.dumps({"ok": False, "error": "no ACTIVE ticket"}, ensure_ascii=False))
        return 1
    ticket = update_usage(ticket)
    classification = str(args.classification or "DRIFT").upper()
    ticket["status"] = classification
    ticket["aborted_at"] = now()
    ticket["abort_reason"] = args.reason
    try:
        path = finalize_ticket(ticket, FAILED)
    except FileExistsError as exc:
        print(json.dumps({"status": "FAIL", "reasons": [str(exc)], "suggested_action": "create_new_ticket_id"}, ensure_ascii=False))
        return 1
    refresh_convergence_projection(
        current_action="Repair the recorded blocker or restore the latest evidence checkpoint.",
        expected_evidence="new machine evidence for the failed condition",
    )
    print(json.dumps({"status": classification, "ticket": str(path), "git_diff_paths": git_diff_paths()}, ensure_ascii=False, indent=2))
    return 1


def extract_patch_paths(patch: str) -> list[str]:
    out = []
    for line in patch.splitlines():
        m = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)", line)
        if m:
            out.append(norm(m.group(1).strip()))
            continue
        m = re.match(r"\*\*\* Move to: (.+)", line)
        if m:
            out.append(norm(m.group(1).strip()))
    return out


def extract_generic_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, val in value.items():
            low = key.lower()
            if low in {"path", "paths", "file", "files", "filename", "target", "dest", "dst", "to"}:
                if isinstance(val, list):
                    paths.extend(norm(str(v)) for v in val)
                else:
                    paths.append(norm(str(val)))
            else:
                paths.extend(extract_generic_paths(val))
    elif isinstance(value, list):
        for item in value:
            paths.extend(extract_generic_paths(item))
    return [p for p in paths if p]


def bash_write_paths(command: str) -> list[str]:
    return [norm(path) for path in shell_write_targets(command) if path]


def opaque_inline_writer(command: str) -> bool:
    low = command.lower()
    interpreter = bool(re.search(r"\b(?:python(?:3)?|py|node|deno|perl|ruby|php|powershell|pwsh|cmd)\b", low))
    inline = bool(re.search(r"(?:\s-c\b|\s-e\b|<<|\b-command\b|\s/c\b)", low))
    write_verb = bool(re.search(r"write_text|write_bytes|writefile|unlink|remove\(|rmtree|open\([^)]*['\"](?:w|a|x)", low))
    return interpreter and inline and write_verb


def goal_compass_command(command: str) -> str | None:
    if re.search(r"(?:&&|\|\||[;|<>\n])", command):
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for i, part in enumerate(parts):
        if part.endswith("goal_compass.py") and i + 1 < len(parts):
            return parts[i + 1]
    return None


def mutating_git(command: str) -> str | None:
    destructive = destructive_git_command(command)
    if destructive:
        return destructive
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0] == "git" and parts[1] in {"commit", "reset", "clean", "stash"}:
        return parts[1]
    return None


def tool_mode_config() -> dict[str, Any]:
    return load_json(TOOL_MODE, {
        "version": "2.0",
        "enabled": False,
        "mode": "BACKGROUND_ADVISORY",
        "visible_ticket_required": False,
    })


def observer_enabled() -> bool:
    config = tool_mode_config()
    return config.get("enabled") is True and config.get("mode") == "BACKGROUND_ADVISORY"


def hook_write_paths(event: dict[str, Any]) -> list[str]:
    tool = str(event.get("tool_name") or event.get("toolName") or "").lower()
    tool_input = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "") if isinstance(tool_input, dict) else ""
    if "apply_patch" in tool or tool == "apply_patch":
        patch = (tool_input.get("command") or tool_input.get("patch") or "") if isinstance(tool_input, dict) else ""
        return sorted(set(extract_patch_paths(str(patch))))
    if tool in {"bash", "shell", "exec_command", "terminal"}:
        return sorted(set(bash_write_paths(command)))
    if any(word in tool for word in ["write", "edit", "patch", "delete", "remove", "move", "create", "update"]):
        return sorted(set(extract_generic_paths(tool_input)))
    return []


def record_observer_event(event: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    """Record bounded metadata even when no visible ticket is active."""
    if not observer_enabled():
        return []
    category = hook_tool_category(event)
    paths = hook_write_paths(event)
    failed = hook_event_failed(event) if phase == "PostToolUse" else False
    observed_at = now()
    event_identifier = hook_event_id(event) or uuid.uuid4().hex
    row = make_observer_event(
        event_id=f"{phase}:{event_identifier}",
        phase=phase,
        category=category,
        paths=paths,
        failed=failed,
        observed_at=observed_at,
    )
    if phase == "PreToolUse" and paths:
        north = north_star()
        stored_north = load_json(NORTH_STAR, {})
        ticket = current_ticket()
        # north_star() exposes a compatibility union of North Star anti-goals
        # and Goal non-goals. The stored document retains their true sources.
        policy_sources = alignment_policy_sources(stored_north)
        if ticket.get("status") == "ACTIVE":
            for key in ("must_not_do", "anti_patterns", "drift_signals"):
                for value in ticket.get(key, []):
                    policy = str(value).strip()
                    if policy:
                        policy_sources.setdefault(policy, GOAL_CONTRACT_ALIGNMENT)
        tool_input = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
        context = build_deviation_context(
            north_star_goal=str(north.get("goal") or "") if north.get("confirmed") else "",
            policies=list(policy_sources),
            tool_input=tool_input if isinstance(tool_input, dict) else {},
            paths=paths,
            policy_sources=policy_sources,
        )
        if context:
            row["deviation_context"] = context
    signals: list[dict[str, Any]] = []
    try:
        with exclusive_file_lock(OBSERVER_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
            previous = load_json(OBSERVER_STATE, empty_observer_state())
            state, pending_signals, processed = apply_pending_observer_events(previous, OBSERVER_PENDING)
            state, current_signals = apply_observer_observation(state, row)
            signals = [*current_signals, *pending_signals]
            write_json(OBSERVER_STATE, state)
            persist_observer_events(OBSERVER_EVENTS, state)
            finalize_pending_observer_events(processed, OBSERVER_PENDING)
    except (OSError, RuntimeError, json.JSONDecodeError):
        queue_pending_observer_event(OBSERVER_PENDING, row)
        return []
    try:
        with exclusive_file_lock(CONVERGENCE_STATE_LOCK, timeout=0.2, stale_seconds=30.0):
            convergence = refresh_convergence_state(
                load_json(CONVERGENCE_STATE, empty_convergence_state()),
                north_star=north_star(),
                phase=program_phase(),
                ticket=current_ticket(),
                updated_at=observed_at,
            )
            convergence = apply_convergence_observation(convergence, row)
            write_json(CONVERGENCE_STATE, convergence)
    except (OSError, RuntimeError, json.JSONDecodeError):
        # Convergence metadata must never delay or block product work.
        pass
    return signals


def observer_notice_once(signal: str) -> bool:
    """Return True once per observer session for a low-frequency reminder."""
    if not observer_enabled():
        return False
    try:
        with exclusive_file_lock(OBSERVER_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
            state = load_json(OBSERVER_STATE, empty_observer_state())
            emitted = set(str(value) for value in state.get("emitted_signals", []))
            if signal in emitted:
                return False
            emitted.add(signal)
            state["emitted_signals"] = sorted(emitted)
            write_json(OBSERVER_STATE, state)
            return True
    except RuntimeError:
        return False


def observer_summary() -> dict[str, Any]:
    summary = compact_observer_summary(load_json(OBSERVER_STATE, empty_observer_state()))
    pending = pending_event_summary(OBSERVER_PENDING)
    summary["events"]["pre"] += int(pending["pre"])
    summary["events"]["post"] += int(pending["post"])
    summary["events"]["failed"] += int(pending["failed"])
    summary["fallback_pending_events"] = int(pending["total"])
    summary["fallback_overflow_detected"] = bool(
        summary.get("fallback_overflow_detected") or pending.get("overflow")
    )
    config = tool_mode_config()
    summary["enabled"] = config.get("enabled") is True
    summary["intervention_policy"] = config.get("intervention_policy", {})
    return summary


def convergence_state() -> dict[str, Any]:
    return load_json(CONVERGENCE_STATE, empty_convergence_state())


def refresh_convergence_projection(
    *,
    current_action: str | None = None,
    expected_evidence: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    state = refresh_convergence_state(
        convergence_state(),
        north_star=north_star(),
        phase=program_phase(),
        ticket=current_ticket(),
        updated_at=now(),
        current_action=current_action,
        expected_evidence=expected_evidence,
    )
    if persist:
        try:
            with exclusive_file_lock(CONVERGENCE_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
                write_json(CONVERGENCE_STATE, state)
        except RuntimeError:
            pass
    return state


def convergence_judge_packet(
    state: dict[str, Any],
    *,
    trigger: str,
    consequence: str,
    appeal: str | None = None,
    policy_boundary: str | None = None,
    alignment_layer: str | None = None,
    affected_paths: list[str] | None = None,
) -> dict[str, Any]:
    stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
    evidence = [item for item in state.get("evidence", []) if isinstance(item, dict)]
    return {
        "trigger": trigger,
        "north_star_goal": stack.get("l0_final_goal"),
        "goal_contract": stack.get("goal_contract"),
        "alignment_layer": alignment_layer,
        "success_criteria": [
            row.get("criterion") for row in stack.get("l1_success_criteria", [])
            if isinstance(row, dict) and row.get("criterion") is not None
        ],
        "current_stage": stack.get("l2_current_stage"),
        "current_action": stack.get("l3_current_action"),
        "expected_evidence": stack.get("l3_expected_evidence"),
        "observed_evidence": [
            {"kind": row.get("kind"), "summary": row.get("summary")}
            for row in evidence[-12:]
        ],
        "policy_boundary": policy_boundary,
        "affected_paths": list(affected_paths or [])[:16],
        "consequence": consequence,
        "appeal": appeal,
    }


def review_semantic_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Confirm a semantic targeted rail with the sparse Judge before denial."""
    if os.environ.get("GOAL_SUPERVISOR_DISABLE_LLM_JUDGE") == "1":
        return signal
    if signal.get("signal") not in {"NORTH_STAR_DEVIATION", "GOAL_CONTRACT_DEVIATION"}:
        return signal
    status = str(signal.get("status") or "")
    strike = int(signal.get("strike_count", 0) or 0)
    if status not in {"CORRECTION_REQUIRED", "RAIL_ENFORCED"} or strike < 2:
        return signal
    state = refresh_convergence_projection(
        current_action="Write under " + ", ".join(signal.get("affected_path_roots", [])[:6]),
        persist=False,
    )
    packet = convergence_judge_packet(
        state,
        trigger="pending_targeted_rail",
        consequence=(
            "A false rail delays aligned work; a missed rail permits repeated explicit Goal-contract deviation."
            if signal.get("signal") == "GOAL_CONTRACT_DEVIATION"
            else "A false rail delays aligned work; a missed rail permits repeated North Star deviation."
        ),
        policy_boundary=str(signal.get("policy") or ""),
        alignment_layer=str(signal.get("alignment_layer") or ""),
        affected_paths=list(signal.get("affected_path_roots") or []),
    )
    result = invoke_llm_judge(
        packet,
        schema_path=LLM_JUDGE_SCHEMA_PATH,
        cache_path=LLM_JUDGE_CACHE,
    )
    state.setdefault("judge", {})["last_result"] = {
        key: result.get(key)
        for key in (
            "status", "verdict", "confidence", "rationale", "recommended_action",
            "evidence_needed", "fingerprint",
        )
    }
    state["judge"]["pending"] = None
    write_json(CONVERGENCE_STATE, state)
    reviewed = dict(signal)
    confirmed = result.get("verdict") == "CONFIRM_TARGETED_RAIL" and result.get("confidence") == "high"
    if status == "RAIL_ENFORCED" and not confirmed:
        reviewed["deny"] = False
        reviewed["intervention"] = "STRONG_WARNING"
        reviewed["recommended_action"] = result.get("recommended_action") or "return_to_alignment_target_or_add_evidence"
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " LLM Judge did not confirm a targeted rail at high confidence; execution remains available. "
            + str(result.get("rationale") or "")
        ).strip()
    elif status == "RAIL_ENFORCED":
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " Sparse LLM Judge confirmed the scoped rail at high confidence. "
            + str(result.get("rationale") or "")
        ).strip()
    else:
        reviewed["reason"] = (
            str(reviewed.get("reason") or "")
            + " Sparse LLM Judge: "
            + str(result.get("verdict") or "INSUFFICIENT_EVIDENCE")
            + ". "
            + str(result.get("rationale") or "")
        ).strip()
    reviewed["llm_judge"] = {
        "status": result.get("status"),
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "fingerprint": result.get("fingerprint"),
    }
    return reviewed


def hook_out(
    event: str,
    deny: str | None = None,
    context: str | None = None,
    advisory: str | None = None,
) -> None:
    if deny and event == "PreToolUse":
        report_governance_feedback(
            "policy_block",
            deny,
            source="hook",
            rule_id="PRE_TOOL_BOUNDARY",
            command="hook",
            status="BLOCKED",
        )
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": deny}}, ensure_ascii=False))
    elif advisory and event == "PreToolUse":
        report_governance_feedback(
            "policy_advisory",
            advisory,
            source="hook",
            rule_id="SUPERVISOR_ADVISORY",
            command="hook",
            status="WARNING",
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": "Codex Goal Supervisor reminder: " + advisory,
        }}, ensure_ascii=False))
    elif context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}, ensure_ascii=False))


def hook_event_id(event: dict[str, Any]) -> str | None:
    for key in ("tool_use_id", "toolUseId", "event_id", "eventId", "call_id", "callId"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def hook_tool_category(event: dict[str, Any]) -> str:
    tool = str(event.get("tool_name") or event.get("toolName") or "").lower()
    tool_input = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "") if isinstance(tool_input, dict) else ""
    low_command = command.lower()
    if any(term in tool for term in ("agent", "subagent", "spawn")):
        return "agent"
    if any(term in tool for term in ("mcp", "browser", "web", "chrome", "computer")):
        return "external"
    if any(term in low_command for term in ("pytest", "unittest", "npm test", "pnpm test", "cargo test", "go test")):
        return "validation"
    if "goal_compass.py" in low_command and any(term in low_command for term in (" check", " close", "prune-check")):
        return "validation"
    if "apply_patch" in tool or any(term in tool for term in ("write", "edit", "patch", "delete", "remove", "move", "create", "update")):
        return "write"
    if tool in {"bash", "shell", "exec_command", "terminal"} and bash_write_paths(command):
        return "write"
    return "read"


def hook_event_failed(event: dict[str, Any]) -> bool:
    return tool_failed(event)


def hook_events_path(ticket: dict[str, Any]) -> Path | None:
    run_id = re.sub(r"[^A-Za-z0-9._-]", "", str(ticket.get("run_id") or ""))
    return HOOK_EVENTS / f"{run_id}.jsonl" if run_id else None


def empty_hook_categories() -> dict[str, int]:
    return {"read": 0, "write": 0, "validation": 0, "agent": 0, "external": 0, "failed": 0}


def cached_hook_event_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    state = load_json(HOOK_STATE, {})
    path = hook_events_path(ticket)
    if state.get("run_id") != ticket.get("run_id") or state.get("ticket_id") != ticket.get("ticket_id"):
        return {
            "pre_events": 0,
            "post_events": 0,
            "tool_calls_by_type": empty_hook_categories(),
            "last_heartbeat_at": None,
            "complete": not bool(path and path.exists()),
        }
    try:
        actual_size = path.stat().st_size if path and path.is_file() else 0
    except OSError:
        actual_size = 0
    return {
        "pre_events": int(state.get("pre_events", 0) or 0),
        "post_events": int(state.get("post_events", 0) or 0),
        "tool_calls_by_type": dict(state.get("tool_calls_by_type") or empty_hook_categories()),
        "last_heartbeat_at": state.get("last_heartbeat_at"),
        "complete": int(state.get("event_log_size", 0) or 0) == actual_size,
    }


def hook_event_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    path = hook_events_path(ticket)
    categories = empty_hook_categories()
    if not path or not path.is_file():
        return {
            "pre_events": 0,
            "post_events": 0,
            "tool_calls_by_type": categories,
            "last_heartbeat_at": None,
        }
    seen: set[str] = set()
    pre_events = 0
    post_events = 0
    last_heartbeat = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                key = str(row.get("dedup_key") or row.get("event_row_id") or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                phase = row.get("phase")
                if phase == "PreToolUse":
                    pre_events += 1
                elif phase == "PostToolUse":
                    post_events += 1
                    category = str(row.get("category") or "read")
                    if category not in categories:
                        category = "read"
                    categories[category] += 1
                    if row.get("failed"):
                        categories["failed"] += 1
                last_heartbeat = row.get("ts") or last_heartbeat
    except OSError:
        pass
    return {
        "pre_events": pre_events,
        "post_events": post_events,
        "tool_calls_by_type": categories,
        "last_heartbeat_at": last_heartbeat,
    }


def hook_health(ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    ticket = ticket or active_ticket()
    state = load_json(HOOK_STATE, {})
    if not ticket:
        return {
            "status": "BACKGROUND_OBSERVING" if observer_enabled() else "NO_ACTIVE_TICKET",
            "budget_enforcement": "NOT_APPLICABLE",
            "last_heartbeat_at": state.get("last_heartbeat_at"),
        }
    events = cached_hook_event_summary(ticket)
    post_events = int(events.get("post_events", 0) or 0)
    pre_events = int(events.get("pre_events", 0) or 0)
    if post_events > 0 and events.get("complete"):
        status = "CONNECTED_VERIFIED"
    elif post_events > 0:
        status = "CONNECTED_UNFOLDED"
    elif pre_events > 0:
        status = "ADVISORY_UNVERIFIED"
    else:
        status = "DISCONNECTED"
    return {
        "status": status,
        "budget_enforcement": "CONNECTED_VERIFIED" if post_events > 0 and events.get("complete") else "ADVISORY_UNVERIFIED",
        "pre_events": pre_events,
        "post_events": post_events,
        "last_heartbeat_at": events.get("last_heartbeat_at") or state.get("last_heartbeat_at"),
        "tool_calls_by_type": events.get("tool_calls_by_type", {}),
        "counter_complete": bool(events.get("complete")),
    }


def record_hook_event(event: dict[str, Any], phase: str) -> bool:
    ticket = active_ticket()
    if not ticket:
        return False
    path = hook_events_path(ticket)
    if path is None:
        return False
    event_id = hook_event_id(event)
    row = {
        "event_row_id": uuid.uuid4().hex,
        "dedup_key": f"{phase}:{event_id}" if event_id else None,
        "ts": now(),
        "ticket_id": ticket.get("ticket_id"),
        "run_id": ticket.get("run_id"),
        "phase": phase,
        "category": hook_tool_category(event) if phase == "PostToolUse" else None,
        "failed": hook_event_failed(event) if phase == "PostToolUse" else False,
    }
    append_jsonl(path, row)
    try:
        with exclusive_file_lock(HOOK_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
            state = load_json(HOOK_STATE, {})
            if state.get("run_id") != ticket.get("run_id") or state.get("ticket_id") != ticket.get("ticket_id"):
                state = {
                    "ticket_id": ticket.get("ticket_id"),
                    "run_id": ticket.get("run_id"),
                    "pre_events": 0,
                    "post_events": 0,
                    "tool_calls_by_type": empty_hook_categories(),
                    "recent_event_ids": [],
                }
            dedup_key = row.get("dedup_key")
            recent = list(state.get("recent_event_ids", []))
            duplicate = bool(dedup_key and dedup_key in recent)
            if not duplicate:
                if phase == "PreToolUse":
                    state["pre_events"] = int(state.get("pre_events", 0) or 0) + 1
                elif phase == "PostToolUse":
                    state["post_events"] = int(state.get("post_events", 0) or 0) + 1
                    categories = dict(state.get("tool_calls_by_type") or empty_hook_categories())
                    category = str(row.get("category") or "read")
                    categories[category] = int(categories.get(category, 0) or 0) + 1
                    if row.get("failed"):
                        categories["failed"] = int(categories.get("failed", 0) or 0) + 1
                    state["tool_calls_by_type"] = categories
                if dedup_key:
                    recent.append(dedup_key)
                    state["recent_event_ids"] = recent[-256:]
            state["last_heartbeat_at"] = row["ts"]
            try:
                state["event_log_size"] = path.stat().st_size
            except OSError:
                state["event_log_size"] = 0
            state["status"] = "CONNECTED_VERIFIED" if int(state.get("post_events", 0) or 0) else "ADVISORY_UNVERIFIED"
            write_json(HOOK_STATE, state)
    except RuntimeError:
        # The append-only log remains recoverable by the next explicit check.
        pass
    return True


def hook_pre(event: dict[str, Any]) -> int:
    tool = str(event.get("tool_name") or event.get("toolName") or "")
    tool_input = event.get("tool_input") or event.get("toolInput") or event.get("input") or {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "") if isinstance(tool_input, dict) else ""
    subcmd = goal_compass_command(command) if command else None
    allowed_goal_commands = {
        "init", "compile", "ready", "start", "status", "check", "backlog", "close", "abort",
        "goal-set", "phase-set", "phase-complete", "phase-advance", "goal-detect", "goal-check", "request", "onboard-scan",
        "prune-check", "prune-plan", "prune-apply", "doctor", "company-record", "company-status",
        "evidence-add", "evidence-list", "feedback", "feedback-config", "reuse-check",
        "deviation-correct", "deviation-corrected", "convergence",
    }
    if subcmd in allowed_goal_commands:
        return 0

    observer_signals = record_observer_event(event, "PreToolUse")
    ticket = current_ticket()
    if command:
        bad_git = mutating_git(command)
        if bad_git:
            if bad_git in {"reset", "clean"}:
                hook_out("PreToolUse", deny=f"Codex Goal Supervisor blocks destructive git {bad_git}; use an explicit reviewed recovery action.")
            else:
                hook_out("PreToolUse", advisory=f"git {bad_git} changes repository state. Confirm that the current atomic result is ready before proceeding.")
            return 0

    active = ticket.get("status") == "ACTIVE"
    if active:
        record_hook_event(event, "PreToolUse")

    lower_tool = tool.lower()
    paths = hook_write_paths(event)
    write_like = bool(paths) or any(word in lower_tool for word in ["write", "edit", "patch", "delete", "remove", "move", "create", "update"])

    if not write_like:
        if observer_signals:
            hook_out("PreToolUse", advisory=observer_signals[0]["reason"])
        return 0

    if command and opaque_inline_writer(command) and not paths:
        hook_out("PreToolUse", advisory="The inline writer hides its target paths. Prefer an explicit-path edit so the background observer can classify the change.")
        return 0

    bad_control = [p for p in paths if match_path(p, PROTECTED_CONTROL_PATTERNS)]
    if bad_control:
        hook_out("PreToolUse", deny="Codex Goal Supervisor control state can only be changed through its CLI: " + ", ".join(bad_control[:8]))
        return 0

    semantic_decisions = [
        review_semantic_signal(item) for item in observer_signals
        if item.get("signal") in {"NORTH_STAR_DEVIATION", "GOAL_CONTRACT_DEVIATION"}
    ]
    semantic_denial = next((item for item in semantic_decisions if item.get("deny")), None)
    semantic_advisory = next(
        (str(item.get("reason")) for item in semantic_decisions if item.get("reason") and not item.get("deny")),
        None,
    )
    if semantic_denial:
        hook_out("PreToolUse", deny=str(semantic_denial.get("reason") or "This wrong-direction write is blocked."))
        return 0

    if not active:
        if semantic_advisory:
            hook_out("PreToolUse", advisory=semantic_advisory)
        elif observer_signals:
            hook_out("PreToolUse", advisory=observer_signals[0]["reason"])
        return 0

    advisories: list[str] = []
    if semantic_advisory:
        advisories.append(semantic_advisory)
    if reuse_probe_due(ticket.get("reuse_discovery")) and observer_notice_once("REUSE_REFRESH_DUE"):
        advisories.append("Reusable-software reconnaissance is missing or older than five days. Refresh it after the current atomic edit; this does not block execution.")

    freeze = acceptance_frozen_violation(ticket)
    if freeze:
        advisories.append(freeze)
    cached_evaluation = ticket.get("last_evaluation", {}) if isinstance(ticket.get("last_evaluation"), dict) else {}
    cached_status = str(cached_evaluation.get("status") or "")
    if cached_status in {"GOAL_DRIFT", "DRIFT", "UPSTREAM_EVIDENCE_INVALID", "BUDGET_EXCEEDED"}:
        signal_key = f"CACHED_{cached_status}"
        if observer_notice_once(signal_key):
            advisories.append(
                f"The last explicit check reported {cached_status}: "
                + "; ".join(str(value) for value in cached_evaluation.get("reasons", [])[:3])
            )
    hook_summary = cached_hook_event_summary(ticket)
    max_calls = ticket.get("budget", {}).get("max_tool_calls")
    if (
        max_calls is not None
        and hook_summary.get("complete")
        and int(hook_summary.get("post_events", 0) or 0) >= int(max_calls)
    ):
        if observer_notice_once("TOOL_BUDGET_PRESSURE"):
            ratio = float(hook_summary.get("post_events", 0) or 0) / float(max_calls or 1)
            advisories.append(f"Tool-call usage is {ratio:.1f}x the planning estimate. Finish the current atomic step, then reassess scope.")

    forbidden = ticket.get("forbidden_paths", [])
    bad_forbidden = [p for p in paths if match_path(p, forbidden)]
    bad_immutable = [p for p in paths if path_contract_role(p, ticket) == "immutable"]
    bad_read_dependencies = [p for p in paths if path_contract_role(p, ticket) == "read_dependency"]
    bad_runtime = [p for p in paths if path_contract_role(p, ticket) == "runtime"]
    bad_outside = [p for p in paths if path_contract_role(p, ticket) == "outside"]
    if bad_forbidden:
        hook_out("PreToolUse", deny="The explicit ticket contract forbids editing: " + ", ".join(bad_forbidden[:8]))
        return 0
    elif bad_immutable:
        hook_out("PreToolUse", deny="Immutable evidence inputs cannot be edited: " + ", ".join(bad_immutable[:8]))
        return 0
    elif bad_read_dependencies:
        advisories.append("The edit targets declared read dependencies: " + ", ".join(bad_read_dependencies[:8]))
    elif bad_runtime:
        advisories.append("The edit targets runtime-owned paths: " + ", ".join(bad_runtime[:8]))
    elif bad_outside:
        advisories.append("The edit is outside the optional ticket's writable paths: " + ", ".join(bad_outside[:8]))
    else:
        company = company_subagent_summary(ticket)
        not_started = [role for role, row in company.get("role_status", {}).items() if row.get("status") == "NOT_STARTED"]
        if company.get("recommended") and not_started and observer_notice_once("COMPANY_CAPABILITY_AVAILABLE"):
            advisories.append("Optional specialist perspectives are available for this decision: " + ", ".join(not_started))
    if advisories:
        hook_out("PreToolUse", advisory=advisories[0] + (f" (+{len(advisories) - 1} background advisories)" if len(advisories) > 1 else ""))
    return 0


def hook_post(event: dict[str, Any]) -> int:
    observer_signals = record_observer_event(event, "PostToolUse")
    ticket = current_ticket()
    if ticket.get("status") != "ACTIVE":
        if observer_signals:
            hook_out("PostToolUse", context="Codex Goal Supervisor reminder: " + observer_signals[0]["reason"])
        return 0
    recorded = record_hook_event(event, "PostToolUse")
    if not recorded:
        return 0
    hook_summary = cached_hook_event_summary(ticket)
    max_calls = ticket.get("budget", {}).get("max_tool_calls")
    if (
        max_calls is not None
        and hook_summary.get("complete")
        and int(hook_summary.get("post_events", 0) or 0) >= int(max_calls)
    ):
        if observer_notice_once("POST_TOOL_BUDGET_PRESSURE"):
            ratio = float(hook_summary.get("post_events", 0) or 0) / float(max_calls or 1)
            hook_out("PostToolUse", context=f"Codex Goal Supervisor reminder: tool-call usage is {ratio:.1f}x the planning estimate. Finish the current atomic step before widening scope.")
        return 0
    cached = ticket.get("last_evaluation", {}) if isinstance(ticket.get("last_evaluation"), dict) else {}
    if cached.get("status") == "PASS_READY":
        if observer_notice_once("PASS_READY"):
            hook_out("PostToolUse", context="Codex Goal Supervisor reminder: acceptance appears satisfied. Deliver the result instead of continuing optional optimization.")
    elif observer_signals:
        hook_out("PostToolUse", context="Codex Goal Supervisor reminder: " + observer_signals[0]["reason"])
    return 0


def cmd_deviation_correct(args: argparse.Namespace) -> int:
    """Open a short, path-scoped repair lane for one enforced deviation."""
    try:
        with exclusive_file_lock(OBSERVER_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
            state = load_json(OBSERVER_STATE, empty_observer_state())
            state, incident = open_deviation_correction(
                state,
                identifier=args.incident,
                reason=args.reason,
                allowed_paths=list(args.allow_path or []),
                observed_at=now(),
            )
            write_json(OBSERVER_STATE, state)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "ok": True,
        "incident_id": incident.get("incident_id"),
        "status": incident.get("status"),
        "strike_count": incident.get("strike_count"),
        "correction": incident.get("correction"),
        "message": "Only the scoped correction lane was opened; aligned project work remains available.",
    }, ensure_ascii=False))
    return 0


def cmd_deviation_corrected(args: argparse.Namespace) -> int:
    """Mark a repaired deviation for seven days of active recurrence monitoring."""
    try:
        with exclusive_file_lock(OBSERVER_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
            state = load_json(OBSERVER_STATE, empty_observer_state())
            state, incident = mark_deviation_corrected(
                state,
                identifier=args.incident,
                evidence=args.evidence,
                observed_at=now(),
            )
            write_json(OBSERVER_STATE, state)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "ok": True,
        "incident_id": incident.get("incident_id"),
        "status": incident.get("status"),
        "strike_count": incident.get("strike_count"),
        "clean_window": incident.get("clean_window"),
        "message": "The strike remains active during the seven-day monitoring window.",
    }, ensure_ascii=False))
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    if not args.kind or not args.message:
        print(json.dumps({"ok": False, "error": "--kind and --message are required"}, ensure_ascii=False))
        return 2
    result = report_governance_feedback(
        args.kind,
        args.message,
        source="ai_reported_plugin_judgment",
        severity=args.severity,
        rule_id=args.rule_id,
        command=args.command,
        status="REPORTED",
        context={"expected_behavior": args.expected_behavior},
    )
    ok = bool(result.get("captured"))
    print(json.dumps({"ok": ok, **result}, ensure_ascii=False, indent=2))
    return 0 if ok else 2


@serialized_current_state
def cmd_reuse_check(args: argparse.Namespace) -> int:
    path = Path(args.ticket) if args.ticket else None
    direct_action = bool(args.task and not path)
    ticket = (
        {
            "ticket_id": "DIRECT-" + hashlib.sha256(args.task.encode("utf-8")).hexdigest()[:12],
            "status": "DIRECT_ACTION",
            "task_goal": args.task,
            "must_do": [args.task],
            "execution_mode": "direct_action",
        }
        if direct_action
        else load_json(path, {}) if path else current_ticket()
    )
    if not isinstance(ticket, dict) or not ticket or ticket.get("status") == "NONE":
        print(json.dumps({"ok": False, "error": "no ticket available for reuse reconnaissance"}, ensure_ascii=False))
        return 2
    ticket = refresh_reuse_discovery(ticket, force=bool(args.force))
    if args.decision:
        if not args.rationale:
            print(json.dumps({"ok": False, "error": "--rationale is required with --decision"}, ensure_ascii=False))
            return 2
        existing_decision = ticket.get("reuse_decision", {}) if isinstance(ticket.get("reuse_decision"), dict) else {}
        existing_status = str(existing_decision.get("status") or "").upper()
        requested_status = str(args.decision).upper()
        if direct_action and requested_status in {"ADOPT_EXISTING", "EXTEND_EXISTING"}:
            print(json.dumps({
                "ok": False,
                "error": "a suitable reusable tool must be integrated through a bounded ticket with machine validation",
                "required_action": "create_light_integration_ticket",
            }, ensure_ascii=False))
            return 2
        if requested_status in {"ADOPT_EXISTING", "EXTEND_EXISTING"}:
            missing_integration = []
            if not args.candidate:
                missing_integration.append("--candidate")
            if len(str(args.integration_plan or "").strip()) < 20:
                missing_integration.append("--integration-plan (20+ characters)")
            if not args.integration_validation_id:
                missing_integration.append("--integration-validation-id")
            if missing_integration:
                print(json.dumps({
                    "ok": False,
                    "error": "adopt/extend requires an executable project integration contract",
                    "missing": missing_integration,
                }, ensure_ascii=False))
                return 2
        if ticket.get("status") == "ACTIVE" and existing_status in {"ADOPT_EXISTING", "EXTEND_EXISTING", "REJECT_WITH_EVIDENCE"}:
            candidate_changed = (
                existing_status in {"ADOPT_EXISTING", "EXTEND_EXISTING"}
                and str(existing_decision.get("candidate") or "") != str(args.candidate or "")
            )
            if requested_status != existing_status or candidate_changed:
                print(json.dumps({
                    "ok": False,
                    "error": "ACTIVE ticket reuse disposition is frozen; create a new DRAFT ticket to replace it",
                }, ensure_ascii=False))
                return 2
        try:
            ticket = apply_reuse_decision(
                ticket,
                decision=args.decision,
                rationale=args.rationale,
                candidate=args.candidate,
                update_decision=args.update_decision,
                integration_plan=args.integration_plan,
                integration_validation_ids=args.integration_validation_id,
                agent_dir=AGENT,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
    elif args.update_decision:
        print(json.dumps({"ok": False, "error": "--update-decision must accompany --decision"}, ensure_ascii=False))
        return 2
    if path:
        write_json(path, ticket)
    elif ticket.get("status") == "ACTIVE":
        save_current(ticket)
    elif direct_action:
        pass
    else:
        print(json.dumps({"ok": False, "error": "--ticket is required for a non-ACTIVE ticket"}, ensure_ascii=False))
        return 2
    errors = reuse_contract_errors(ticket)
    if errors:
        report_governance_feedback(
            "reuse_gate_block",
            "; ".join(errors),
            source="reuse-check",
            rule_id="REUSE_BEFORE_BUILD",
            command="reuse-check",
            ticket=ticket,
            status="BLOCKED",
            context={"reuse": reuse_compact_status(ticket)},
        )
    print(json.dumps({
        "ok": not errors,
        "reuse": reuse_compact_status(ticket),
        "errors": errors,
        "candidates": ticket.get("reuse_discovery", {}).get("candidates", []),
        "updates": ticket.get("reuse_discovery", {}).get("updates", []),
        "decision": ticket.get("reuse_decision", {}),
        "integration": ticket.get("reuse_integration", {}),
        "query_terms": ticket.get("reuse_discovery", {}).get("query_terms", []),
        "remaining_actions": ticket.get("reuse_discovery", {}).get("remaining_actions", []),
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


def cmd_convergence(args: argparse.Namespace) -> int:
    state = refresh_convergence_projection(
        current_action=args.current_action,
        expected_evidence=args.expected_evidence,
        persist=False,
    )
    completion_result = None
    completion_exit_code = 0
    if args.certify_goal:
        north = north_star()
        goal_stack = state.get("goal_stack") if isinstance(state.get("goal_stack"), dict) else {}
        global_success_criteria = [
            row for row in (goal_stack.get("l1_success_criteria") or [])
            if isinstance(row, dict) and str(row.get("criterion") or "").strip()
        ]
        requested_ids = list(dict.fromkeys(
            str(value).strip() for value in (args.final_validation_id or []) if str(value).strip()
        ))
        completion_status = "NOT_CERTIFIED"
        completion_reasons: list[str] = []
        validation_run: dict[str, Any] = {}
        phase_result = None
        if not north.get("confirmed"):
            completion_status = "NEEDS_CONFIRMATION"
            completion_reasons.append(NORTH_STAR_CONFIRMATION_MESSAGE)
        elif active_ticket():
            completion_status = "ACTIVE_WORK_REMAINS"
            completion_reasons.append("Close or abort the ACTIVE ticket before final North Star certification.")
        elif not global_success_criteria:
            completion_status = "INCOMPLETE_GOAL_CONTRACT"
            completion_reasons.append(
                "The project-level Goal has no success criteria. A local validation cannot certify the entire North Star."
            )
        elif not requested_ids:
            completion_status = "NEEDS_FINAL_REGRESSION"
            completion_reasons.append("At least one validation_catalog id is required for final North Star regression.")
        else:
            unknown_ids = [command_id for command_id in requested_ids if command_id not in catalog()]
            if unknown_ids:
                completion_status = "INVALID_FINAL_REGRESSION"
                completion_reasons.append(
                    "Unknown validation_catalog ids: " + ", ".join(unknown_ids)
                )
            else:
                final_ticket = {
                    "ticket_id": "NORTH-STAR-FINAL-REGRESSION",
                    "status": "ACTIVE",
                    "acceptance_ready": True,
                    "acceptance": {
                        "commands_pass": requested_ids,
                        "files_exist": [],
                        "contains": [],
                        "assertions": [],
                        "files_not_changed": [],
                    },
                    "validation_ids": requested_ids,
                    "validation_lifecycle": {},
                    "read_dependencies": [],
                    "writable_paths": [],
                    "allowed_paths": [],
                    "forbidden_paths": [],
                    "budget": {},
                    "budget_used": {"changed_files": [], "immutable_changes": []},
                }
                validation_ok, completion_reasons = acceptance_result(final_ticket, run_commands=True)
                validation_run = final_ticket.get("validation_run", {})
                completion_status = "CERTIFIED_COMPLETE" if validation_ok else "FINAL_REGRESSION_FAILED"
        north_hash = (
            sha256_bytes(json.dumps(north, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            if north.get("confirmed") else None
        )
        completion = {
            "status": completion_status,
            "north_star_hash": north_hash,
            "validation_ids": requested_ids,
            "validated_at": now() if completion_status in {"CERTIFIED_COMPLETE", "FINAL_REGRESSION_FAILED"} else None,
            "input_fingerprint": validation_run.get("input_fingerprint"),
            "summary": str(args.completion_summary or "").strip() or None,
            "failure_reasons": completion_reasons[:8],
            "validation": {
                key: validation_run.get(key)
                for key in (
                    "status", "cache_hit", "validated_at", "root_cause",
                    "executed_ids", "skipped_ids", "suppressed_cascade_count",
                )
                if key in validation_run
            },
        }
        if completion_status == "CERTIFIED_COMPLETE":
            phase = program_phase()
            if phase.get("status") == "ACTIVE":
                phase_result = complete_program_phase(
                    "Final North Star regression passed: " + ", ".join(requested_ids)
                )
            state = refresh_convergence_projection(persist=False)
            evidence_id = "north-star-final-regression:" + str(validation_run.get("input_fingerprint") or north_hash or "pass")[:24]
            state = record_convergence_evidence(
                state,
                evidence_id=evidence_id,
                kind="north_star_final_regression",
                summary="Final North Star regression passed.",
                observed_at=completion["validated_at"],
            )
        state["goal_completion"] = completion
        state["updated_at"] = now()
        completion_result = {
            "status": completion_status,
            "north_star_goal": north.get("goal") if north.get("confirmed") else None,
            "validation_ids": requested_ids,
            "failure_reasons": completion_reasons[:8],
            "validation": completion["validation"],
            "program_phase": phase_result,
            "required_action": (
                "deliver_verified_result"
                if completion_status == "CERTIFIED_COMPLETE"
                else "fix_final_regression_and_retry"
                if completion_status == "FINAL_REGRESSION_FAILED"
                else "close_active_ticket"
                if completion_status == "ACTIVE_WORK_REMAINS"
                else "configure_final_regression"
                if completion_status in {"NEEDS_FINAL_REGRESSION", "INVALID_FINAL_REGRESSION"}
                else "repair_goal_contract"
                if completion_status == "INCOMPLETE_GOAL_CONTRACT"
                else "confirm_north_star"
            ),
        }
        completion_exit_code = 0 if completion_status == "CERTIFIED_COMPLETE" else 2
    if args.record_iteration:
        required = {
            "--hypothesis": args.hypothesis,
            "--change": args.change,
            "--expected-result": args.expected_result,
            "--validation": args.validation,
            "--result": args.result,
            "--decision": args.decision,
        }
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            print(json.dumps({
                "ok": False,
                "error": "iteration record is incomplete",
                "missing": missing,
            }, ensure_ascii=False))
            return 2
        state = record_convergence_iteration(
            state,
            hypothesis=args.hypothesis,
            change=args.change,
            expected_result=args.expected_result,
            validation=args.validation,
            result=args.result,
            decision=args.decision,
            evidence_ids=list(args.evidence_id or []),
            completed_criteria=list(args.completed_criterion or []),
            observed_at=now(),
        )

    if args.record_collaboration:
        required = {
            "--source-thread": args.source_thread,
            "--target-thread": args.target_thread,
            "--claim": args.claim,
        }
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            print(json.dumps({
                "ok": False,
                "error": "collaboration record is incomplete",
                "missing": missing,
            }, ensure_ascii=False))
            return 2
        if str(args.source_thread).strip() == str(args.target_thread).strip():
            print(json.dumps({
                "ok": False,
                "error": "collaboration source and target must be different",
            }, ensure_ascii=False))
            return 2
        state = record_convergence_collaboration_round(
            state,
            source=args.source_thread,
            target=args.target_thread,
            claim=args.claim,
            evidence_ids=list(args.evidence_id or []),
            artifact_refs=[
                str(value).strip()
                for value in (args.artifact_ref or [])
                if str(value).strip() and Path(str(value).strip()).exists()
            ],
            state_transition=args.state_transition,
            observed_at=now(),
        )

    judge_result = None
    trigger = convergence_judge_trigger(
        state,
        pending_targeted_rail=bool(args.pending_targeted_rail),
        high_cost_ambiguous_action=bool(args.high_cost_ambiguous_action),
        appeal_with_new_evidence=bool(args.appeal_with_new_evidence),
        explicit_request=bool(args.judge),
        novelty=not bool(args.same_evidence),
    )
    automatic_iteration_review = bool(
        args.record_iteration
        and trigger["eligible"]
        and os.environ.get("GOAL_SUPERVISOR_DISABLE_LLM_JUDGE") != "1"
    )
    if args.judge or ((args.auto_judge or automatic_iteration_review) and trigger["eligible"]):
        packet = convergence_judge_packet(
            state,
            trigger=", ".join(trigger["reasons"]) or str(args.reason or "explicit semantic review"),
            consequence=str(args.consequence or "A wrong decision could create expensive rework."),
            appeal=args.appeal,
            policy_boundary=args.policy_boundary,
            affected_paths=list(args.affected_path or []),
        )
        judge_result = invoke_llm_judge(
            packet,
            schema_path=LLM_JUDGE_SCHEMA_PATH,
            cache_path=LLM_JUDGE_CACHE,
            timeout_seconds=float(args.judge_timeout),
            force=bool(args.force_judge),
        )
        state.setdefault("judge", {})["last_result"] = {
            key: judge_result.get(key)
            for key in (
                "status", "verdict", "confidence", "rationale", "recommended_action",
                "evidence_needed", "fingerprint",
            )
        }
        state["judge"]["pending"] = None
        state["updated_at"] = now()
    elif (args.auto_judge or args.record_iteration) and not trigger["eligible"]:
        state.setdefault("judge", {})["pending"] = {
            "eligible": False,
            "reasons": trigger["reasons"],
            "policy": trigger["policy"],
        }

    try:
        with exclusive_file_lock(CONVERGENCE_STATE_LOCK, timeout=1.0, stale_seconds=30.0):
            write_json(CONVERGENCE_STATE, state)
    except RuntimeError:
        print(json.dumps({"ok": False, "status": "STATE_BUSY"}, ensure_ascii=False))
        return 2
    payload = {
        "ok": completion_exit_code == 0,
        "convergence": compact_convergence_status(state),
        "judge_trigger": trigger,
        "judge_result": judge_result,
        "goal_completion": completion_result,
        "ordinary_execution_blocked": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.verbose else None))
    return completion_exit_code


def cmd_doctor(_: argparse.Namespace) -> int:
    health = hook_health()
    payload = {
        "goal_compass": "READY",
        "active_ticket": active_ticket().get("ticket_id") if active_ticket() else None,
        "hook": health,
        "generated_state_ignored_by_product_budget": True,
        "feedback": feedback_status(AGENT),
        "reuse": reuse_compact_status(active_ticket()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if health["status"] in {"CONNECTED_VERIFIED", "NO_ACTIVE_TICKET", "BACKGROUND_OBSERVING"} else 1


def cmd_hook(_: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    event_name = event.get("hook_event_name") or event.get("hookEventName") or ""
    if event_name == "PreToolUse":
        return hook_pre(event)
    if event_name == "PostToolUse":
        return hook_post(event)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Goal Compass")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    p = sub.add_parser("goal-set")
    p.add_argument("--text", required=True)
    p.add_argument("--definition-file")
    p.add_argument("--require-detailed", action="store_true")
    p.add_argument("--problem")
    p.add_argument("--first-principle", action="append", default=[])
    p.add_argument("--action", action="append", default=[])
    p.add_argument("--deliverable", action="append", default=[])
    p.add_argument("--success-criterion", action="append", default=[])
    p.add_argument("--constraint", action="append", default=[])
    p.add_argument("--non-goal", action="append", default=[])
    p.add_argument("--dialogue-summary", action="append", default=[])
    p.add_argument("--replace-existing", action="store_true")
    p.set_defaults(func=cmd_goal_set)
    p = sub.add_parser("phase-set")
    p.add_argument("--id", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--exit-criterion", action="append", default=[])
    p.set_defaults(func=cmd_phase_set)
    p = sub.add_parser("phase-complete")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_phase_complete)
    p = sub.add_parser("phase-advance")
    p.add_argument("--id", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--exit-criterion", action="append", default=[])
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_phase_advance)
    sub.add_parser("goal-detect").set_defaults(func=cmd_goal_detect)
    p = sub.add_parser("goal-check")
    p.add_argument("--user-goal", required=True)
    p.set_defaults(func=cmd_goal_check)
    p = sub.add_parser("request")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_request)
    p = sub.add_parser("onboard-scan")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_onboard_scan)
    p = sub.add_parser("prune-check")
    p.add_argument("--scope", choices=["current-ticket", "full-repo"], default="current-ticket")
    p.add_argument("--path")
    p.set_defaults(func=cmd_prune_check)
    p = sub.add_parser("prune-plan")
    p.add_argument("--scope", choices=["current-ticket", "full-repo"], default="current-ticket")
    p.add_argument("--path")
    p.set_defaults(func=cmd_prune_plan)
    p = sub.add_parser("prune-apply")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--delete", action="store_true")
    p.set_defaults(func=cmd_prune_apply)
    p = sub.add_parser("compile")
    p.add_argument("rough_task")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_compile)
    p = sub.add_parser("ready")
    p.add_argument("ticket")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_ready)
    p = sub.add_parser("start")
    p.add_argument("ticket")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_start)
    p = sub.add_parser("status")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("context-note")
    p.add_argument("--directory", required=True)
    p.add_argument("--fact", action="append", default=[])
    p.add_argument("--interface", action="append", default=[])
    p.add_argument("--dependency", action="append", default=[])
    p.add_argument("--open-question", action="append", default=[])
    p.add_argument("--next-action")
    p.add_argument("--evidence", action="append", default=[])
    p.set_defaults(func=cmd_context_note)
    p = sub.add_parser("deviation-correct")
    p.add_argument("--incident", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--allow-path", action="append", default=[])
    p.set_defaults(func=cmd_deviation_correct)
    p = sub.add_parser("deviation-corrected")
    p.add_argument("--incident", required=True)
    p.add_argument("--evidence", required=True)
    p.set_defaults(func=cmd_deviation_corrected)
    p = sub.add_parser("feedback")
    p.add_argument("--kind", choices=["false_positive", "false_negative", "wrong_status", "plugin_runtime_error", "workflow_friction", "other"])
    p.add_argument("--message")
    p.add_argument("--expected-behavior")
    p.add_argument("--rule-id")
    p.add_argument("--command")
    p.add_argument("--severity", choices=["info", "warning", "error", "critical"], default="warning")
    p.set_defaults(func=cmd_feedback)
    p = sub.add_parser("reuse-check")
    reuse_target = p.add_mutually_exclusive_group()
    reuse_target.add_argument("--ticket")
    reuse_target.add_argument("--task", help="direct-action description when no ticket is needed")
    p.add_argument("--force", action="store_true")
    p.add_argument("--decision", choices=sorted(REUSE_DECISIONS))
    p.add_argument("--candidate")
    p.add_argument("--rationale")
    p.add_argument("--integration-plan")
    p.add_argument("--integration-validation-id", action="append", default=[])
    p.add_argument("--update-decision", choices=sorted(REUSE_UPDATE_DECISIONS))
    p.set_defaults(func=cmd_reuse_check)
    p = sub.add_parser("convergence")
    p.add_argument("--certify-goal", action="store_true")
    p.add_argument("--final-validation-id", action="append", default=[])
    p.add_argument("--completion-summary")
    p.add_argument("--record-iteration", action="store_true")
    p.add_argument("--record-collaboration", action="store_true")
    p.add_argument("--hypothesis")
    p.add_argument("--change")
    p.add_argument("--expected-result")
    p.add_argument("--validation")
    p.add_argument("--result")
    p.add_argument("--decision")
    p.add_argument("--evidence-id", action="append", default=[])
    p.add_argument("--artifact-ref", action="append", default=[])
    p.add_argument("--completed-criterion", action="append", default=[])
    p.add_argument("--source-thread")
    p.add_argument("--target-thread")
    p.add_argument("--claim")
    p.add_argument(
        "--state-transition",
        choices=[
            "BLOCKED_WITH_EVIDENCE",
            "DELIVERED",
            "IMPLEMENTED",
            "REVERTED_WITH_EVIDENCE",
            "VALIDATED",
        ],
    )
    p.add_argument("--current-action")
    p.add_argument("--expected-evidence")
    p.add_argument("--judge", action="store_true")
    p.add_argument("--auto-judge", action="store_true")
    p.add_argument("--pending-targeted-rail", action="store_true")
    p.add_argument("--high-cost-ambiguous-action", action="store_true")
    p.add_argument("--appeal-with-new-evidence", action="store_true")
    p.add_argument("--same-evidence", action="store_true")
    p.add_argument("--reason")
    p.add_argument("--consequence")
    p.add_argument("--appeal")
    p.add_argument("--policy-boundary")
    p.add_argument("--affected-path", action="append", default=[])
    p.add_argument("--judge-timeout", type=float, default=45.0)
    p.add_argument("--force-judge", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_convergence)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    p = sub.add_parser("company-record")
    p.add_argument("--ticket", help="pending ticket path for planning-role receipts before start")
    p.add_argument("--role", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--status", required=True, choices=["STARTED", "COMPLETED", "FAILED"])
    p.add_argument(
        "--failure-class",
        choices=["PRODUCT_BLOCKER", "REVIEW_INCOMPLETE", "RUNTIME_FAILURE", "SUPERSEDED"],
    )
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--result-hash")
    p.add_argument("--result-path")
    p.add_argument("--summary")
    p.set_defaults(func=cmd_company_record)
    sub.add_parser("company-status").set_defaults(func=cmd_company_status)
    p = sub.add_parser("check")
    p.add_argument("--run-validation", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("backlog")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_backlog)
    p = sub.add_parser("evidence-add")
    p.add_argument("--type", required=True, choices=["browser", "manual", "qa", "validation", "artifact", "runtime"])
    p.add_argument("--source", required=True)
    p.add_argument("--status", default="PASS", choices=["PASS", "FAIL", "INFO"])
    p.add_argument("--summary", required=True)
    p.add_argument("--path")
    p.add_argument("--acceptance-id")
    p.add_argument("--owner")
    p.add_argument("--pid", type=int)
    p.add_argument("--port", type=int)
    p.add_argument("--checkpoint-id")
    p.add_argument("--resume-command")
    p.add_argument("--resource", action="append", default=[])
    p.set_defaults(func=cmd_evidence_add)
    sub.add_parser("evidence-list").set_defaults(func=cmd_evidence_list)
    p = sub.add_parser("close")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_close)
    p = sub.add_parser("abort")
    p.add_argument("--reason", required=True)
    p.add_argument("--classification", choices=sorted(ABORT_CLASSIFICATIONS), default="DRIFT")
    p.set_defaults(func=cmd_abort)
    sub.add_parser("hook").set_defaults(func=cmd_hook)
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        report = report_governance_feedback(
            "plugin_runtime_error",
            f"{type(exc).__name__}: {exc}",
            source="cli_runtime",
            severity="error",
            rule_id="UNHANDLED_RUNTIME_EXCEPTION",
            command=str(getattr(args, "cmd", "unknown")),
            status="RUNTIME_FAILURE",
            context={"exception_type": type(exc).__name__},
        )
        print(json.dumps({
            "ok": False,
            "status": "PLUGIN_RUNTIME_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "feedback_event_id": report.get("event_id"),
        }, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
