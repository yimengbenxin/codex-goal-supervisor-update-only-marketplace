# Codex Goal Supervisor Feature Inventory And Layering

Status: factual audit of `2.8.10+codex.20260817054749` at commit `4234b43`.

This document is an inventory and migration-policy draft, not a V3 implementation. It separates:

- **Current reality:** what the present source, Skill, commands, hooks, and tests implement.
- **Initial migration state:** which profile may activate the capability during the first safe migration. This is not permanent ownership.
- **Migration condition:** what must be decoupled before a capability can honestly be called cross-mode.

There is no current product capability formally named "context compaction endurance". The current source has three distinct mechanisms that must not be conflated:

1. Goal Return seals and restores a confirmed Goal and temporary-branch state around compaction.
2. Context Continuity keeps a bounded, directory-level metadata ledger for genuinely large reads.
3. SessionStart restores bounded project state pointers after a new or compacted session.

## Architecture Correction: One Capability Core, Inherited Profiles

Capabilities are not physically divided into a general copy and a Goal copy.
There is one implementation and one capability registry. Runtime profiles only
decide how that capability is exposed and enforced.

```text
Shared runtime foundation
└── Universal capability core (superset)
    ├── General baseline profile
    └── Goal profile = General baseline + Goal-specific promotions
```

The universal capability core is the largest set. Goal mode always remains
inside that set and calls the same implementations. A capability may initially
remain `GOAL_ONLY_COMPATIBILITY` while it is still coupled to native Goal state;
that label is a migration state, not a claim that ordinary modes can never use
it.

Every capability profile has independent policy dimensions:

```json
{
  "availability": "available | compatibility_only | disabled",
  "obligation": "optional | conditional | required",
  "invocation": "explicit | background | either",
  "enforcement": "none | advisory | targeted_block",
  "preconditions": []
}
```

Profile inheritance is monotonic for obligation and enforcement:

1. A capability required by the General baseline is also required in Goal mode.
2. A capability optional in General may remain optional or be promoted to
   conditional/required by Goal policy.
3. A General targeted block cannot be weakened by Goal mode.
4. Goal mode may add Goal-specific triggers, but it calls the same capability
   implementation and writes compatible state.
5. `availability` and `obligation` are separate. A capability may be available
   everywhere while remaining optional until its preconditions are present.
6. Missing preconditions return an explicit unavailable/not-applicable result;
   they never fabricate a North Star, Goal, phase, ticket, or evidence record.

This avoids two bad extremes: making every general capability mandatory, or
forking the current implementation into two versions that later drift apart.

## Current 2.8.10 Goal Profile Baseline

This section records current behavior before V3 changes. It is the compatibility
source for the future Goal profile and must not be reconstructed from memory.

### Required after explicit Goal Supervisor activation

These requirements apply only after the user explicitly activates Goal
Supervisor for a substantive project task:

1. Establish or exactly reuse one confirmed project-owned North Star.
2. Build a real 2,000-3,500 character detailed Goal contract; a rough request,
   one-line North Star, path-only reference, or truncated plan is insufficient.
3. Research current reusable tools/routes before authoring or materially
   rewriting the detailed Goal. If a viable reuse candidate exists, visibly ask
   the user about adoption and commercial use before finalizing the route.
4. Include first principles, modules, actions, serial/parallel relationships,
   dependencies, inputs, outputs, consumers, project contribution, exit
   criteria, final acceptance, per-segment hour targets, and reuse decisions.
5. Synchronize the exact detailed objective into native Codex Goal mode and
   verify byte equality, length, and SHA-256 before committing project state.
6. Preserve an existing confirmed North Star and native Goal unless the user
   explicitly confirms replacement. Replacement records supersession and
   `objective_achieved=false`; it never fakes completion.
7. For a super-complex plan, preserve the full project-relative plan over 4,000
   characters and keep a real 2,000-3,500 character executable Goal projection.
8. For work too large for one useful Goal, use a small phased program. Each
   active phase is one independently useful 2-24 hour outcome with distinct
   current research and machine validation; a phase must pass before a
   dependency-ready successor is projected.
9. Verify every claimed implementation result proportionally to its risk.
10. Run project-level final regression before claiming the entire North Star
    complete. Only a current `CERTIFIED_COMPLETE` permits that claim.

The route dashboard is started or recovered after a successful detailed Goal
setup and opened once when the in-app Browser is available. Dashboard failure
is reported compactly and is never allowed to block otherwise valid product
work.

### Automatic only when evidence preconditions are present

- The project observer records bounded metadata and remains silent for ordinary
  aligned work.
- Product writes open verification debt; a later observed successful validation
  clears it. Only an unsupported completion claim at `Stop` surfaces a reminder.
- A clearly durable direction change outside both North Star and detailed Goal
  asks once only after high-confidence sparse judgment. Temporary requests and
  contained subgoals stay silent.
- A confirmed Goal receives Goal Return temporary-branch bookkeeping on
  `UserPromptSubmit`, `Stop`, compaction, and session recovery.
- Exact North Star deviation uses two warnings, 30-minute rechecks, and a third
  high-confidence targeted rail. It does not block aligned paths, reads, tests,
  validation, or correction.
- Repeated same-route failures trigger route reassessment only after the current
  thresholds. A materially different route remains available.
- If one external prerequisite would prematurely stop an unfinished Goal, the
  runtime selects a dependency-ready module when one exists. If all remaining
  paths are transitively blocked, it reports one exact human action and stops.
- One unambiguous first product write starts its Goal segment clock. Longer
  segments use the Goal-authored reminder cadence; short segments normally do
  not produce pre-deadline reminders.
- Goal reuse research refreshes after each 24-hour continued-work window, not on
  every continuation.
- A high-confidence industry match is used during new detailed Goal authoring;
  a low-confidence match asks once and no match stays silent.
- Genuinely large, independently partitionable reads may receive directory
  capsules and read-only subagent guidance. Small or tightly coupled reads do
  not.
- Verified deterministic local-service commands may become project procedures;
  sensitive, destructive, transient, read-only, or unevidenced commands never do.

### Explicit optional capabilities

The following are not required merely because Goal mode is active:

- Custodian `request` analysis;
- Company roles and receipts, including zero-role execution;
- specialist-role lookup outside required Goal authoring;
- Auditor `check` and ticket certification;
- Janitor `prune-check` / `prune-plan` artifact review;
- bounded tickets and parallel worktree tickets;
- MDCP/lens verbose projections;
- explicit iteration, collaboration, backlog, evidence, context-note, and
  procedure inspection commands;
- remote feedback upload;
- device-level automatic update;
- optional roadmap subnode expansion.

If one of these tools is explicitly activated, its internal truth conditions
still apply. For example, an optional ticket cannot start or pass with empty
machine acceptance, and optional Company expansion above four roles requires a
cautious main-thread decision. Optional invocation does not mean optional
truthfulness.

### Advisory-only signals

- three consecutive tool failures;
- broad write surfaces and declared batch-work confirmation;
- ordinary budget or diff pressure;
- ambiguous semantic scope judgments;
- axis fatigue and cleanup candidates;
- unsupported completion claims with verification debt.

These signals do not by themselves stop ordinary aligned execution.

### Current hard or targeted boundaries

- destructive `git reset` / `git clean`;
- direct edits to Supervisor control state;
- exact active-ticket `forbidden_paths` and immutable-evidence paths;
- explicit Janitor delete requests (`prune-apply` is manifest-only);
- a third high-confidence confirmation of the same exact North Star deviation,
  limited to the wrong-direction write surface;
- invalid detailed Goal/native synchronization cannot be committed as active;
- failed phase validation cannot complete or advance that phase;
- an activated ticket with empty/invalid machine acceptance cannot start or
  PASS;
- failed validation cannot be reported as PASS or North Star completion;
- custom building behind a confirmed direct-reuse candidate cannot proceed
  until the candidate receives an explicit reuse or evidence-backed rejection
  disposition.

Hook failure, Judge timeout, malformed semantic output, feedback delivery
failure, missing dashboard, or missing optional tool state fails open for
ordinary product work and cannot become product evidence.

## Classification Key

- **GENERAL:** first migration can safely expose the capability through the General baseline.
- **GOAL:** first migration keeps the capability behind Goal compatibility policy because the current implementation requires confirmed Goal state. It may be generalized later.
- **SHARED:** infrastructure used by both layers and not a user-facing decision system.
- **OPTIONAL:** explicitly callable project tool; never a mandatory background ceremony.
- **SPLIT:** the present implementation combines a general mechanism with Goal-specific policy and must be separated before V3 claims cross-mode support.
- **FREEZE:** retain compatibility and tests, but do not expand the concept.

## A. Activation, Intent, And Goal Authoring

| # | Current capability | Actual entry/evidence | Current dependency | Initial migration state | Decision |
|---|---|---|---|---|---|
| 1 | Explicit project opt-in | installer, passive plugin hooks, Skill activation boundary | selected project | SHARED | Keep. Never auto-enrol unrelated projects. |
| 2 | Concise confirmed North Star | `goal-set`, `.agent/north_star_goal.json` | confirmed user direction | GOAL | Register universally; initially optional in General and required by Goal profile. It is not a generic task summary. |
| 3 | Detailed 2,000-3,500 character Goal contract | `goal-set --require-detailed` | North Star, structured modules and acceptance | GOAL | Register universally; initially optional in General and required by Goal profile. |
| 4 | Native Codex Goal synchronization and byte/hash verification | `native_goal_bridge.py`, `thread/goal/set/get` | Codex task and native Goal API | GOAL | Universally callable when native Goal context exists; required by Goal profile. |
| 5 | Unfinished Goal replacement with supersession history | `goal-set --replace-existing` | existing native/project Goal | GOAL | Universally callable with Goal preconditions; Goal profile records `SUPERSEDED_BY_USER_DIRECTION_CHANGE`, never fake completion. |
| 6 | Durable direction-change confirmation | UserPrompt hook plus sparse Judge | long-running confirmed Goal | GOAL | Universally registered; initially automatic only in Goal profile and rare. |
| 7 | Project goal detection and alignment | `goal-detect`, `goal-check` | project evidence and North Star | GOAL | Universally callable; automatic alignment remains Goal policy. |
| 8 | Super-complex plan plus compressed Goal objective | detailed Goal schema and plan reference | detailed Goal authoring | GOAL | Universally callable; required only when the active profile and task complexity demand it. |
| 9 | Phased 2-24 hour program Goals | `phase-set`, `phase-complete`, `phase-advance` | program outline, phase DAG, native Goal replacement | GOAL | Universally callable with phase context; Goal policy may require it for oversized work. Do not inflate small work. |
| 10 | L0-L3 Goal stack | `convergence.goal_contract_projection` | North Star and Goal definition | GOAL | Universally callable with Goal context; a generic task-stack adapter may be added later. |
| 11 | Expert-assisted Goal authoring | `agency_role_pack.py goal-brief` | new identifiable-industry detailed Goal | GOAL | Universally callable; high-confidence automatic routing remains a Goal-authoring promotion. |
| 12 | Reuse research embedded in Goal and refreshed every 24 hours | `reuse_probe.py`, Goal contract | confirmed detailed Goal and remaining Goal actions | GOAL | Universally callable; the 24-hour automatic policy initially remains Goal-specific. |
| 13 | Goal route dashboard | `roadmap`, loopback HTML | detailed Goal nodes and convergence state | GOAL | Universally callable with a route contract; automatically started only by Goal policy. |
| 14 | Goal segment deadlines and reminders | `convergence --start/complete-segment` | Goal route nodes and real deadlines | GOAL | Universally callable with segment context; required scheduling initially remains Goal policy. |
| 15 | Final North Star regression certification | `convergence --certify-goal` | project-level Goal success criteria and validation id | GOAL | Universally callable with Goal success criteria; required before Goal-level completion claims. |

## B. Background Execution And Convergence

| # | Current capability | Actual entry/evidence | Current dependency | Initial migration state | Decision |
|---|---|---|---|---|---|
| 16 | Low-noise metadata observer | `observer.py`, project hooks | explicit project installation | GENERAL | Move to general runtime unchanged in spirit: silent normal path. |
| 17 | Tool success/failure normalization | `hook_rules.py`, `project_hook.py` | hook event shapes | SHARED | General shared adapter. |
| 18 | Product-write verification debt | observer plus `Stop` completion-claim check | product write and observed validation | GENERAL | General runtime. It does not require Goal mode. |
| 19 | Activity-versus-evidence separation | `convergence.py` | evidence IDs and iterations | SPLIT | General core; Goal projection must become an adapter. |
| 20 | Iteration stagnation detection | `record_iteration`, `judge_trigger` | completed iterations and evidence changes | GENERAL | General runtime after removing mandatory North Star fields. |
| 21 | Sparse read-only LLM Judge | `llm_judge.py` | structured packet, timeout, cache | SHARED | Shared service. Policies decide when to call it. |
| 22 | Exact North Star deviation incidents | `deviation_incidents.py` | North Star anti-goals and Goal non-goals | GOAL | Universally registered with North Star preconditions; automatic three-confirmation rail remains Goal policy initially. |
| 23 | Technical-route repeated-failure incidents | `route_incidents.py` | currently redacted Goal route/cause family | SPLIT | General same-action failure detector; Goal-specific first-principle and acceptance check stays in Goal adapter. |
| 24 | External-prerequisite alternate-path continuation | Goal Return/Convergence Stop review | unfinished Goal module DAG | SPLIT | General blocker classification is reusable; automatic dependency-ready selection is Goal-only. |
| 25 | Goal Return temporary branches | `goal_return.py` | confirmed North Star, detailed Goal, Goal generation | GOAL | Universally registered with Goal preconditions; automatic use initially remains Goal policy. Do not rename it as generic compaction continuity. |
| 26 | Compaction lifecycle sealing for Goal Return | `PreCompact`, `PostCompact`, `SessionStart` | Goal Return state | GOAL | Shared events stay universal; Goal Return sealing initially remains a Goal-policy subscriber. |
| 27 | Large-read context ledger and directory capsules | `context_continuity.py`, `context-note` | project paths and explicit conclusions, not Goal | GENERAL | General runtime. Preserve metadata-only, on-demand loading, and no auto-injection. |
| 28 | Conditional read-only subagent guidance for large reads | Context Continuity plus `SubagentStart` | independently partitionable large-read slices | GENERAL | General advisory; never force subagents for small/coupled reads. |
| 29 | Project procedure memory and generated service runners | `procedure_memory.py`, `procedure` | verified deterministic commands | GENERAL | General runtime, opt-in consumption, local-only. |
| 30 | Evidence-bearing collaboration liveness | `record_collaboration_round` | cross-thread evidence IDs | GENERAL | General runtime; stop praise/review loops after two evidence-free rounds. |
| 31 | Runtime/generated/binary artifact tracking | snapshot/diff/runtime contracts | project tracking contract | GENERAL | General runtime foundation. |
| 32 | Compact cached status | `status`, observer/convergence compact projections | persisted metadata | SPLIT | General status shell plus Goal/ticket optional sections. No full scan on default status. |

## C. Explicit Optional Project Tools

| # | Current capability | Actual entry/evidence | Current dependency | Initial migration state | Decision |
|---|---|---|---|---|---|
| 33 | Custodian request routing | `request` | currently North Star/current ticket | OPTIONAL + SPLIT | Keep optional. General task-change core needs an adapter; Goal scope policy stays Goal-specific. |
| 34 | Company role selection and model routing | company runtime/functions | ticket/task complexity and role contracts | OPTIONAL | General explicit tool. Zero roles remains valid. |
| 35 | Pinned specialist role library | `agency_role_pack.py search/show` | explicit lookup or Goal authoring | OPTIONAL | General library; high-confidence automatic use remains only in Goal authoring. |
| 36 | Auditor machine evidence | `check`, `close` | current ticket and validation catalog | OPTIONAL | General explicit certification tool, but current implementation is ticket-bound. |
| 37 | Janitor artifact classification | `prune-check`, `prune-plan` | project/North Star/ticket evidence | OPTIONAL + SPLIT | Keep MARK_ONLY. General artifact evidence core needs an adapter; Goal mapping stays Goal-specific. |
| 38 | Quarantine manifest | `prune-apply` | reviewed Janitor candidates | OPTIONAL | Keep mark-only record. No move/delete authority. |
| 39 | Existing-project inventory and alignment scan | `onboard-scan` | project inventory and optional North Star | OPTIONAL + SPLIT | Inventory is general; Goal alignment view is Goal-specific. |
| 40 | Bounded ticket lifecycle | `compile`, `ready`, `start`, `check`, `close`, `abort` | ticket contract | OPTIONAL | General explicit tool. Never mandatory for ordinary work. |
| 41 | Hard machine acceptance and validation catalog | ticket acceptance, `validation_catalog.py` | catalog IDs and acceptance shapes | SHARED/OPTIONAL | Shared validation service; ticket certification remains optional. |
| 42 | Ticket scope, budget, read/write/immutable paths | ticket contract and runtime tracking | active ticket | OPTIONAL | Keep advisory budgets and deterministic exact boundaries. |
| 43 | Independent worktree ticket parallelism | ticket dependencies and coordination contract | separate worktrees and disjoint scopes | OPTIONAL | General explicit scheduling tool. |
| 44 | MDCP structured lenses and company-role projections | compile/check/status MDCP fields | ticket and Goal structures | FREEZE/OPTIONAL | Keep as internal schema; do not expose it as another workflow or approval layer. |
| 45 | Backlog and evidence ledger | `backlog`, `evidence-add/list` | project/ticket state | OPTIONAL | General lightweight utilities. |

## D. Shared Distribution, Privacy, And Reliability

| # | Current capability | Actual entry/evidence | Current dependency | Initial migration state | Decision |
|---|---|---|---|---|---|
| 46 | Local diagnostic feedback with explicit remote consent | `feedback.py`, `feedback-config`, receiver | project consent; full edition only | SHARED | Keep local-only default and fail-open delivery. |
| 47 | Offline, update-only, and full physical editions | release builder | release packaging | SHARED | Keep physical code separation. |
| 48 | Device-level automatic update | updater scripts/schedulers | explicit device setup | SHARED | Keep outside project hooks. |
| 49 | Verified release publishing and real black-box attestation | publisher/release tests | exact clean commit and test evidence | SHARED | Keep release-only. |
| 50 | Non-polluting installer and project-local hook dispatch | installer and hook scripts | explicitly selected project | SHARED | Keep. |
| 51 | Atomic/locked JSON state and bounded event queues | `state_store.py`, concurrency tests | local runtime state | SHARED | Required foundation for both layers. |
| 52 | Cross-platform hook normalization | shell dispatcher, Windows hook | Codex hook events | SHARED | Required foundation; surface support must be verified per Codex client. |
| 53 | Doctor/health diagnostics | `doctor` | installed runtime | SHARED | Keep read-only and bounded. |
| 54 | Validation cache and fail-fast validation chain | evaluation/validation runtime | input fingerprints and catalog | SHARED | Reuse unchanged passes; stop downstream validation after first blocker. |
| 55 | Resolved correction canonicalization | `instruction_hygiene.py`, prompt/stop/tool hooks | explicitly activated project and confirmed subtraction | GENERAL | Required low-noise background rule; block only repeated narrative/publication residue and allow explicit reopening. |
| 56 | General temporary-request return after compaction | `instruction_hygiene.py`, compact lifecycle hooks | explicitly activated project and bounded primary-task anchor | GENERAL | Required background rule for explicit temporary requests and short side questions; never re-inject closed request text. |
| 57 | Hierarchical parent-child Goal workstreams | `goal-workstreams` | detailed parent Goal, independent workstream DAG, shared contracts | GOAL | Universally callable but optional in General. In Goal mode use only when dependency-ready independent work saves more time than coordination and integration consume. |

## Duplicate Entry Points That Are Not Separate Capabilities

1. `status`, `check`, and `convergence` currently overlap in reporting. V3 should keep one general compact runtime status, one optional ticket certification view, and one Goal convergence view.
2. `goal-detect`, `goal-check`, and the alignment portion of `onboard-scan` share Goal evidence classification. They should use one Goal alignment engine with different views.
3. `prune-check`, `prune-plan`, and `prune-apply` share one artifact classification engine. `prune-apply` must remain manifest-only.
4. MDCP, lens notes, company-role projections, and auditor/janitor fields overlap structurally. MDCP should remain an internal schema, not a fourth visible process.

## Complete CLI Command Map

All 36 commands registered by the current parser are accounted for below.

| Commands | Owning capability |
|---|---|
| `init`, `hook`, `doctor` | shared installation/runtime/health foundation |
| `goal-set`, `goal-detect`, `goal-check` | North Star, detailed Goal, and Goal alignment |
| `phase-set`, `phase-complete`, `phase-advance` | phased Goal program |
| `goal-workstreams` | hierarchical parent-child Goal workstreams |
| `roadmap` | Goal technical-route dashboard |
| `reuse-check` | Goal-authored reuse route and refresh |
| `convergence` | Goal stack, segments, evidence, iterations, collaboration, and final certification; general evidence core is a split candidate |
| `deviation-correct`, `deviation-corrected` | Goal-specific North Star incident correction lifecycle |
| `request` | optional Custodian |
| `onboard-scan` | optional general inventory plus Goal-specific alignment view |
| `prune-check`, `prune-plan`, `prune-apply` | optional mark-only Janitor and quarantine manifest |
| `compile`, `ready`, `start`, `check`, `close`, `abort` | optional bounded-ticket lifecycle and Auditor certification |
| `status` | compact mixed projection; split into general shell and optional Goal/ticket sections |
| `procedure` | general verified project procedure memory |
| `context-note` | general explicit large-read directory conclusion |
| `feedback-config`, `feedback` | shared local feedback and explicitly consented delivery |
| `company-record`, `company-status` | optional company-role runtime |
| `backlog` | optional project backlog utility |
| `evidence-add`, `evidence-list` | optional evidence ledger |

## Initial Profile Policy

The migration starts conservatively without hard-coding the final policy. The
first version proves that one capability implementation can be called through
both profiles; later evidence may promote more capabilities into the General
baseline.

### General baseline, initially active or required

- Explicit project activation remains required before any project-local
  observation begins.
- Deterministic irreversible boundaries remain required once activated:
  destructive Git cleanup, direct Supervisor control-state writes, and exact
  immutable paths.
- Tool-result normalization, bounded state persistence, runtime/generated-file
  exclusion, privacy defaults, and fail-open runtime behavior are required
  infrastructure.
- Ordinary aligned work remains silent.

### General baseline, initially available but not mandatory

- North Star and detailed Goal authoring;
- native Goal synchronization when a native Goal is requested;
- phased Goals, route dashboard, segment deadlines, reuse research, expert Goal
  input, and final certification;
- Custodian, Company, role library, Auditor, Janitor, bounded tickets, backlog,
  evidence, and MDCP projections;
- convergence records, large-read context capsules, procedure memory,
  collaboration liveness, and sparse Judge review.

This list is deliberately not permanent. A capability may later become a
General conditional or required default when black-box evidence shows that its
expected saved rework consistently exceeds its process cost.

### Goal profile promotions during initial migration

Goal mode inherits the General baseline and initially promotes these existing
behaviors:

- confirmed North Star and detailed native Goal equality are required;
- detailed Goal research, route, dependencies, outputs, consumers, timeboxes,
  and acceptance are required before substantive Goal execution;
- Goal Return, durable direction-change handling, Goal route incidents, segment
  deadlines, and North Star deviation monitoring run automatically when their
  evidence preconditions are present;
- project-level final Goal certification is required before claiming the whole
  North Star complete.

Tickets, Company roles, Custodian, Auditor, Janitor, and MDCP remain optional or
conditional even in Goal mode unless a separate evidence-backed Goal policy
promotes them. Their existence alone never makes them mandatory.

## General Instruction-Hygiene Promotions

The two previously proposed General-baseline candidates are implemented and
validated as required background capabilities after explicit project activation:

1. **Temporary-request compaction return (`instruction.compaction_return`).**
   General Profile stores a bounded primary-task anchor, tombstones completed
   temporary branches, and restores only the primary task plus tombstone state.
   Raw closed-request text is never re-injected during repeated compaction.
2. **Resolved-correction canonicalization (`instruction.correction_canonicalization`).**
   Explicit subtraction is confirmed immediately; a rhetorical subtraction is
   confirmed only by the Agent's correction response. After that response, the
   rejected variant is blocked only from publication/narrative surfaces and later
   completion summaries. Product code stays available, and explicit user reopening
   supersedes the stored correction.

Both use one mode-neutral implementation in `instruction_hygiene.py`. Goal mode
inherits the General requirements while Goal Return retains its richer confirmed-
Goal checkpoint and replay lifecycle.

## Initial Extraction Boundary

### General-callable runtime candidates

The first extraction candidate is a small, mode-neutral runtime containing:

- low-noise event observation and event normalization;
- verification debt and truthful completion reminders;
- activity-versus-evidence and bounded stagnation detection;
- repeated same-action failure detection;
- large-read context ledger and optional read-only partition guidance;
- verified project procedure memory;
- evidence-bearing collaboration liveness;
- runtime/generated/binary tracking;
- compact cached status;
- sparse Judge as a shared service, invoked only by a policy with concrete expected benefit.

These capabilities cannot read a native Goal as a mandatory input. If a Goal exists, a Goal adapter may enrich their packet.

### Goal compatibility policy during migration

Keep the existing Goal-coupled behavior stable behind the Goal profile while
its implementations are registered in the universal capability catalog:

- confirmed North Star and Goal alignment;
- detailed Goal contract and native synchronization;
- Goal replacement and phase transitions;
- long-term direction-change confirmation;
- Goal Return temporary branches around user interruptions and compaction;
- Goal technical route, roadmap, segments, deadlines, reuse route, and expert-assisted Goal authoring;
- North Star deviation incidents;
- project-level final Goal regression certification.

### Shared infrastructure

State storage, hook transport, validation catalog/cache, privacy/feedback, updater, release packaging, installer, and diagnostics belong below both layers. They are not product goals and must never become another decision workflow.

## Immediate Non-Goals

- Do not rename Goal Return or Context Continuity into an invented umbrella feature.
- Do not claim existing Goal-coupled code already works in every Codex mode.
- Do not claim North Star rails, native Goal synchronization, phased Goals, or Goal certification are already mode-neutral. They remain callable catalog capabilities with Goal-coupled preconditions until separately generalized.
- Do not turn Custodian, Company, Auditor, Janitor, MDCP, or tickets into mandatory background steps.
- Do not add board, signature, HMAC, reverse-signal, approval, or resident multi-agent chat flows.
- Do not start V3 implementation until this inventory and boundary are accepted.
