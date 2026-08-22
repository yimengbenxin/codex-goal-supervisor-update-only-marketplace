# Codex Goal Supervisor In This Project

Codex Goal Supervisor runs as a low-cost background observer after this project explicitly installs it. General Profile activation does not require a North Star or native Goal. Selecting Goal Profile requires a structured project North Star and the matching Codex Goal mode; normal reads, edits, tests, and delivery do not require a ticket.

General Profile always enables two low-noise instruction-hygiene rules after project activation. A completed temporary request is tombstoned, and compaction restores the primary task without repeating the temporary request text. A confirmed rejected element may appear in the correction response once, but later residue in titles, comments, narrative files, or completion summaries is blocked until rewritten as the positive canonical result. Normal product code remains available, and an explicit user instruction can reopen the element. The bounded local state is redacted.

The North Star and Goal-mode objective are different layers. The North Star is the concise durable direction. Goal mode contains a 2,000-3,500 character executable contract with modules, concrete actions, serial/parallel relationships, dependencies, outputs, goal contribution, and acceptance. Finalize that contract first, then run `goal-set --require-detailed --validate-only`. This returns the exact rendered objective, character count, SHA-256, and missing fields without changing project/native Goal state, starting the roadmap, or probing reuse again. Review it, then remove `--validate-only` for the single state-changing call. Standard structured Goals are rendered from their structured fields; a hand-authored `goal_mode_objective` does not override that projection. Inside a Codex task the state-changing command uses official app-server `thread/goal/set`, immediately reads `thread/goal/get`, and commits project state only after exact objective length and SHA-256 verification. An explicitly confirmed durable direction change uses `--replace-existing --replacement-reason <reason>` and records the old objective, acceptance, status, usage, and restorable local snapshot as superseded rather than falsely complete. Super-complex work also uses a project-relative plan over 4,000 characters; Goal mode keeps a compressed contract and references the full plan rather than replacing its content with a path.

For a project too large for one useful Goal, keep the confirmed North Star and
use a shallow program outline plus one detailed current phase. Each phase is a
2-24 hour independently useful outcome with distinct reuse research,
dependencies, outputs, consumers, and validation-catalog IDs. Start it with
`phase-set --outline-file <outline.json> --definition-file <phase.json>`, use
the exact returned objective as the native Goal, and verify its hash. A failed
`phase-complete` leaves the phase active; only a passing phase can be followed
by `phase-advance --definition-file <next.json>`. The CLI reports required
native Goal synchronization and performs it automatically when invoked inside
the target Codex task. A failed native sync leaves the current phase unchanged.

## Structured Phased Goal Input

`phase-set` consumes two JSON objects. The outline uses these canonical fields:

```json
{
  "north_star_goal": "exact confirmed North Star",
  "planning_research": {
    "completed": true,
    "queries": ["current reusable route"],
    "sources": ["https://primary.example/source"],
    "reuse_decision": "reuse or bounded-build decision"
  },
  "phases": [{
    "phase_id": "P1",
    "title": "independently useful outcome",
    "outcome": "observable business result",
    "dependencies": [],
    "outputs": ["validated output"],
    "consumers": ["named consumer"],
    "contribution_to_goal": "why this advances the North Star",
    "estimated_hours": 4
  }],
  "shared_contracts": ["cross-phase contract"],
  "final_acceptance": ["program-level acceptance"]
}
```

The phase file uses these canonical top-level fields:

```json
{
  "phase_id": "P1",
  "estimated_hours": 4,
  "dependencies": [],
  "validation_ids": ["catalog_validation_id"],
  "goal_mode_objective": "authored 2,000-3,500 character current-phase contract",
  "planning_research": {
    "completed": true,
    "researched_at": "ISO-8601 timestamp",
    "queries": ["phase-specific reusable route"],
    "sources": ["https://primary.example/phase-source"],
    "tool_sources_reviewed": 1,
    "article_sources_reviewed": 1,
    "refresh_interval_hours": 24,
    "reusable_candidate_found": false,
    "no_suitable_reuse_reason": "bounded reason"
  },
  "goal_definition": {
    "precise_goal": "current phase result",
    "problem_statement": "current gap",
    "current_state": "verified starting state",
    "desired_state": "verified target state",
    "stakeholders": ["consumer"],
    "source_requirements": ["confirmed requirement"],
    "first_principles": [{
      "principle": "principle",
      "rationale": "reason",
      "implications": ["implementation consequence"]
    }],
    "process": {
      "entry_conditions": ["entry evidence"],
      "nodes": [{
        "node_id": "N1",
        "name": "module",
        "objective": "module result",
        "execution_mode": "SERIAL",
        "inputs": ["input"],
        "actions": ["action"],
        "outputs": ["output"],
        "exit_criteria": ["machine check"],
        "dependencies": [],
        "contribution_to_goal": "phase contribution",
        "timebox_hours": 2,
        "reminder_interval_hours": 0
      }],
      "completion_conditions": ["phase acceptance passes"]
    },
    "deliverables": [{
      "name": "deliverable",
      "description": "what is delivered",
      "format": "file or service",
      "consumer": "named consumer",
      "acceptance": ["acceptance evidence"]
    }],
    "final_acceptance": [{
      "criterion": "business criterion",
      "evidence": "evidence location",
      "validation_method": "catalog command"
    }],
    "constraints": ["constraint"],
    "non_goals": ["non-goal"]
  }
}
```

Use at least two first principles and two process nodes. Supply an authored
2,000-3,500 character `goal_mode_objective` when the complete structured
definition would render beyond the native Goal limit. The plugin validates the
complete definition separately and never truncates it. A long objective cannot
compensate for missing structured fields. `phase-set` does not require a prior
detailed `goal-set`; it validates this file, stores the phase projection, and
returns the exact native Goal objective. Compatibility aliases such as
`detailed_goal_definition`, `validation_catalog_ids`, `id`, `timebox_hours`,
and `depends_on` are normalized, but new definitions should use the canonical
shape above. Contract errors point back to this section.

## Hierarchical Goal Workstreams

Use `goal-workstreams` only when a detailed parent Goal has multiple independent
workstreams and parallel execution has positive net benefit. The parent Codex
task remains the sole integration owner. The CLI returns validated launch
payloads; the parent uses Codex `create_thread` to create each independent task.
Company subagents and child Codex tasks are separate mechanisms.

The parent plan is a project-relative JSON object:

```json
{
  "parent_north_star_goal": "exact confirmed North Star",
  "fanout_reason": "why independent tasks reduce completion time",
  "integration_owner": "parent Codex task",
  "expected_net_benefit": {
    "serial_hours": 12,
    "parallel_hours": 5,
    "coordination_hours": 1,
    "integration_hours": 2
  },
  "shared_contracts": [{
    "contract_id": "shared-result-contract",
    "subject": "cross-workstream result",
    "rule": "one versioned schema and error model",
    "consumers": ["workstream-a", "workstream-b", "integration"]
  }],
  "workstreams": [{
    "workstream_id": "workstream-a",
    "title": "Independent module A",
    "responsibility": "bounded implementation responsibility",
    "parent_contribution": "how its output advances final integration",
    "execution_mode": "PARALLEL",
    "parallel_group": "foundation",
    "estimated_hours": 4,
    "dependencies": [],
    "inputs": ["shared-result-contract"],
    "outputs": ["validated module output"],
    "consumers": ["integration"],
    "writable_paths": ["src/module_a/**", "tests/module_a/**"],
    "read_dependencies": ["contracts/result.json"],
    "immutable_paths": ["contracts/result.json"],
    "validation_ids": ["module_a_validation"],
    "shared_contract_ids": ["shared-result-contract"]
  }],
  "final_integration": {
    "inputs": ["validated module output"],
    "validation_ids": ["project_integration_validation"],
    "acceptance": "all declared workstream outputs integrate through the shared contract"
  }
}
```

At least two initially dependency-ready parallel workstreams are required.
Dependencies inside one parallel group, dependency cycles, overlapping writable
paths, unknown shared contracts, missing consumers, or non-positive time savings
are rejected before any child task starts.

```bash
python3 .agent/goal_compass.py goal-workstreams --plan-file workstreams.json --validate-only
python3 .agent/goal_compass.py goal-workstreams --plan-file workstreams.json
python3 .agent/goal_compass.py goal-workstreams
```

Each returned `thread_launches` entry contains the assignment, scope, contracts,
validation IDs, and one launch prompt. In that child Codex task, author a normal
detailed Goal JSON and run:

```bash
python3 .agent/goal_compass.py goal-workstreams --set-goal workstream-a --definition-file child-goal.json
python3 .agent/goal_compass.py goal-workstreams --complete workstream-a --evidence-id evidence-ref --summary "validated output returned to parent"
```

`--set-goal` installs and verifies the child task's native Goal without changing
the project North Star. Completion runs the assigned validation IDs and only
then unlocks dependency-ready workstreams. Session start and post-compaction
restore the child assignment and parent alignment. If the parent North Star or
detailed Goal changes, only further child product writes pause for parent
reconciliation; reads and evidence return stay available.

Each implementation action must have verification proportional to its risk. Use focused evidence for local changes instead of repeatedly running the full suite. Before claiming the entire North Star complete, run `convergence --certify-goal --final-validation-id <catalog-id>` with project-level end-to-end regression ids. Missing or failed regression cannot certify completion; only `CERTIFIED_COMPLETE` can.

Background behavior remains quiet: product writes create bounded verification debt, successful observed validation clears it, and only an explicit completion claim at `Stop` exposes an open debt. A validation start without a `PostToolUse` success remains unverified. Goal Compass state under `.agent/**` and `.codex/**` is excluded from this debt.

Alignment uses both layers: North Star checks durable direction, while the Goal contract checks the concrete module, dependency, output, exit criterion, and acceptance being advanced. Explicit anti-goals and Goal non-goals are tracked separately. Missing an obvious positive module mapping is not a hard failure; it remains an advisory because prerequisites and bounded exploration can still be legitimate.

Before the Agent writes or materially rewrites a super-complex plan, it researches available tools/projects and technical articles. The plan contains the resulting route, not the research log. If a reusable tool is found, the Agent asks in conversation whether to reuse it and whether the project is commercial; only after the answer does it finalize the plan and Goal-mode contract.

For `goal-set --require-detailed`, each process node includes inputs, actions, outputs, dependencies, exit criteria, `execution_mode` (`SERIAL`, `PARALLEL`, or `CONDITIONAL`), `contribution_to_goal`, and an hour-level target; downstream consumers are derived from dependencies or may be explicit. Parallel nodes also include `parallel_group`. Optional `affected_paths`, `affected_modules`, and `subnodes` support user-requested detail without making decomposition ceremony mandatory. A super-complex definition additionally records `complexity_level: SUPER_COMPLEX`, `execution_plan_ref`, an authored `goal_mode_summary`, and lightweight `planning_research` state. That state records only that tool/article research and visible user consultation happened; the final plan should not contain the research log.

Every newly authored or materially rewritten detailed Goal therefore has a complete high-level technical route before implementation. The `roadmap` command serves a read-only visualization on `127.0.0.1`; `roadmap --snapshot` returns its JSON projection and `roadmap --stop` closes it. The page refreshes from North Star and convergence state and does not create another project truth source. Ordinary tasks without an explicitly activated detailed Goal do not start it.

## Implicit Layer

- Silent for ordinary execution.
- One compact warning for repeated failures or a broad write surface.
- Exact project-authored anti-goal/drift incidents stay open: confirmations one and two warn; before confirmation three can enforce a scoped rail, a sparse read-only LLM Judge must confirm it at high confidence.
- Hard deny also remains for destructive `git reset`/`git clean`, direct Goal Supervisor control-state edits, and exact active-ticket forbidden/immutable paths.
- Hook failure is fail-open.

Observer state is compact metadata in `.agent/runtime/observer_state.json`; source contents are not stored there. Lock contention falls back to a recoverable bounded queue, and only the latest 128 diagnostic events (up to 64 KiB) are retained.

Large historical-code or document reads use local context continuity without
LLM assistance or proactive prompt injection. `.agent/runtime/context/index.json`
contains only totals and directory pointers; each relevant project directory is
split into `by-directory/<project-path>/_context.json` with file fingerprints.
The execution Agent may inspect `status --verbose` and load only the needed
directory. When independent reading slices justify it, the Agent may use
read-only subagents and merge their structured summaries itself. During a
large read, the main thread should publish a concise conclusion after each
meaningful directory/evidence slice and persist reusable facts with
`context-note`; source text and hidden reasoning are never stored.
When already available, Serena may supply symbol/reference reads and FastCtx
may supply paged reads; Goal Supervisor never installs either dependency. The
fallback is bounded ranges through normal client tools. Source file size alone
does not trigger the large-read path, tiny searches do not accumulate as broad
reads, and subagents are recommended only when at least two independent slices
exist. A saved directory conclusion becomes `STALE` when its evidence changes.

Repeated operational knowledge is kept separately from source-reading
continuity. The hook records successful deterministic commands and one bounded,
redacted task outcome. A recognized local-service launch generates a
project-local Skill and start/status/stop runner after its first observed
success. Other fixed command sequences require matching success in two
independent tasks. Use `procedure` to inspect the compact index and load only a
matching `.agent/procedures/<id>/SKILL.md`. Commands containing credentials,
arbitrary shell composition, destructive operations, transient paths, or
ordinary reads are never converted into executable procedures.

The same deviation is rechecked after 30 minutes of continued affected-path work. Unrelated success does not clear it. Judge results are structured and cached; unavailable, malformed, or timed-out judgment fails open. `deviation-correct` opens a scoped repair lane; `deviation-corrected` starts seven days of active recurrence monitoring. A recurrence restores the rail immediately. Counts clear only after explicit correction, seven clean days, and real project activity.

## Explicit Optional Layer

Call only when the expected saved rework is concrete:

- `request --text ...`: optional Custodian analysis.
- company roles: optional specialist deliverables; zero roles is valid.
- `check`: optional Auditor snapshot.
- `prune-check` / `prune-plan`: optional MARK_ONLY Janitor review; no move or delete.
- `compile / ready / start / close`: optional bounded ticket and machine certification.
- `convergence`: explicit L0-L3 goal stack, evidence progress, high-cost iteration records, recovery checkpoint, and optional semantic judgment.

Empty acceptance cannot start or PASS. Failed `close` returns `NOT_CERTIFIED` and leaves the ACTIVE ticket available for repair. Missing optional company-role results do not block ordinary work or certification.

`status` and `onboard-scan` are summaries by default. Use `status --verbose` to see the local context-continuity index; use other verbose output only when full diagnostic or inventory detail is needed. `onboard-scan` always writes the full report files. An unconfirmed North Star returns `NEEDS_CONFIRMATION` with `confirm_north_star`, not a failed command.

## Privacy

Feedback is local-only by default. Upload requires explicit project-level consent; an endpoint alone cannot enable it. Network failure never blocks product work.

## Supreme Rule

Every action taken by the model and every supervisory intervention by this plugin must produce net execution benefit. Any action that may affect other modules without constraint or become noise for the entire project must be managed. If the cost of a control by this plugin exceeds the rework it can prevent, that control must remain inactive.
