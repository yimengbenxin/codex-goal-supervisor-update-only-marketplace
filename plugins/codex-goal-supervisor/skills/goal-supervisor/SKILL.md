---
name: goal-supervisor
description: Use when the user explicitly asks to install or use Codex Goal Supervisor, Goal Compass, North Star monitoring, optional bounded tickets, company subagents, Custodian, Auditor, or Janitor. The plugin is advisory-first and must not auto-install into unrelated projects.
---

# Codex Goal Supervisor

Codex Goal Supervisor is a project tool, not a mandatory harness. Its supreme rule is:

> Every action taken by the model and every supervisory intervention by this plugin must produce net execution benefit. Any action that may affect other modules without constraint or become noise for the entire project must be managed. If the cost of a control by this plugin exceeds the rework it can prevent, that control must remain inactive.

Treat its capabilities as a collection of goal-mode problem-solving skills, not
as a worker that takes over the product task. It may teach the execution agent
how to recover, structure, validate, delegate, or clean up; the execution agent
still owns the work and final judgment.

## Activation Boundary

- This plugin is explicit opt-in at the project boundary.
- Do not auto-activate it for a large, long, or multi-step task.
- Install only after the user explicitly selects Goal Supervisor/Goal Compass for this project.
- Never activate it from task size alone and never inspect neighboring projects.
- After explicit activation for a substantive task, establishing the project North Star and starting the Codex client Goal mode are mandatory setup, not optional background capabilities. Keep the layers distinct: the North Star is the concise durable direction; Goal mode contains the detailed executable contract derived from the structured definition.
- A confirmed `.agent/north_star_goal.json` is project-owned. Do not rewrite it unless the user explicitly asks.
- If `.agent/north_star_goal.json` already contains a confirmed goal, reuse it exactly. Do not call `goal-set` unless the user explicitly asks to replace or create that project goal.
- If a confirmed North Star already exists, preserve it exactly and reuse its stored Goal-mode contract. If no confirmed North Star exists, build the detailed contract, run `goal-set --require-detailed`, and then start Goal mode. Never claim activation is complete when only one of these two states exists.
- During a long-running confirmed Goal, do not let the North Star become stale when the user clearly introduces a different durable product direction. A temporary request, question, implementation detail, sequencing change, or subgoal already contained by the current North Star stays silent. For a non-explicit direction change, ask only after the sparse Judge confirms at high confidence that it is durable and outside both the North Star and detailed Goal. Ask once per Goal generation across session changes or compaction, never auto-rewrite, and keep execution available. If the user confirms, rebuild the concise North Star and detailed Goal-mode contract together with `goal-set --replace-existing --require-detailed`; never replace only the one-line North Star.
- Plugin auto-update is a separate device-level capability. It must never activate a project, alter a North Star, or run from project hooks. When the user explicitly asks to enable or repair automatic updates, run `scripts/configure_plugin_auto_update.py`; otherwise leave device scheduling unchanged.
- A maintainer-created local version is not complete until `scripts/publish_verified_release.py` reports `PUBLISHED_AND_VERIFIED`. The command must run only after the release commit is clean; it verifies source and extracted ZIPs, publishes the canonical repository plus the full and update-only marketplaces, uploads all three edition ZIPs and their SHA-256 manifest, and reads the remote channels back. Never stream uncommitted file saves to clients.

## Two Layers

### 1. Implicit Background Observer

After project installation, the repo-local hook observes bounded metadata cheaply. It does not require a ticket, role receipt, check, or cleanup pass.

Normal behavior is silent. It may emit one compact strong warning when it sees:

- three consecutive tool failures;
- a broad write surface that may be intentional batch work;
- an exact user-authored anti-goal or an active ticket drift signal;
- validation, scope, or budget pressure already established by explicit evidence.

It may block deterministic irreversible boundaries:

- destructive `git reset` or `git clean`;
- direct edits to Goal Supervisor control state;
- an explicit active-ticket `forbidden_paths` match;
- an explicit immutable-evidence path.

An exact project-authored North Star deviation has persistent incident semantics, not a one-shot notice. The first two confirmations are strong warnings. Continued affected-path work is rechecked every 30 minutes; unrelated success never clears the incident. The third confirmation blocks only the wrong-direction write surface while aligned work, reads, tests, and validation continue. Use `deviation-correct --incident ... --reason ...` to open a 30-minute scoped repair lane, then `deviation-corrected --incident ... --evidence ...` to enter a seven-day active monitoring window. Clear the strike only after explicit correction, seven days, real project activity, and no recurrence. A recurrence during that window immediately restores the targeted rail.

Ambiguous semantic judgments are warnings, never hidden approvals or denials. Only exact project-authored anti-goals or drift boundaries may enter this three-confirmation rail. Hook failure is fail-open and must not become product evidence.

For a confirmed Goal, `UserPromptSubmit` also creates a bounded Goal-return branch unless the user explicitly replaces the Goal, promotes a persistent constraint, or simply asks to continue. `Stop` closes a completed branch. After compaction, `SessionStart` restores the current Goal checkpoint and treats closed branches as tombstoned history. A first exact-path replay is silent context, a second is a warning, and a third may reach the sparse Judge. Do not expose this bookkeeping as a ticket or ask the user to manage it.

If a response tries to stop an unfinished confirmed Goal only because of one
manual, physical-device, login, or other external prerequisite, `Stop` forces
one bounded Goal-wide scope check. Defer the local prerequisite, inspect every
unfinished Goal module or acceptance path, and continue the highest-value
independent work. If all remaining paths are transitively blocked, report one
exact human action and stop normally; `stop_hook_active` prevents a loop.

### 2. Explicit Optional Capabilities

After the mandatory North Star and Goal-mode setup, the AI may call these when their expected benefit is concrete:

- **Custodian** via `request` for a meaningful goal/scope change.
- **Company roles** for independent specialist deliverables; zero roles is valid.
- **Auditor** via `check` or `close` for machine evidence.
- **Janitor** via `prune-check` or `prune-plan` for artifact-sprawl review. Janitor is MARK_ONLY and never moves or deletes product files.
- **Bounded ticket** via `compile/ready/start/close` when isolation, machine certification, or parallel ownership will save rework.

These capabilities are never required merely because they exist. Do not create ceremonial receipts, reviews, or tickets.

## Verification And Completion

- Never report an implementation action as complete without the smallest relevant verification evidence. Match the check to the risk: a focused test or read-back is enough for a local change; cross-cutting work needs the affected regression set.
- A temporary implementation branch with product writes exits inside the current Goal turn after two successful validations with no intervening write. Return to the stored Goal action at that point; do not keep rerunning or broadening the completed branch until the eventual `Stop` event.
- The background observer records product-write verification debt without injecting a workflow. Only an explicit completion claim at `Stop` surfaces a reminder when no later successful validation was observed. Missing `PostToolUse` evidence remains unverified; never infer a pass from a validation command merely starting. Tool state under `.agent/**` and `.codex/**` is excluded.
- Claim the entire confirmed North Star complete only after `convergence --certify-goal --final-validation-id <catalog-id>` produces `CERTIFIED_COMPLETE`.
- Final certification also requires non-empty project-level Goal success criteria. A passing local validation id can certify its own change, but can never certify an empty or missing global Goal contract.
- Do not run the whole project suite after every micro-edit. Verification must reduce expected rework rather than become a repeated ceremony.
- "Implementation finished" and "North Star complete" are different claims. Before the final North Star delivery, run the project-level end-to-end regression through `convergence --certify-goal --final-validation-id <catalog-id>`.
- An implementation may be finished while final certification remains `NEEDS_FINAL_REGRESSION` or `FINAL_REGRESSION_FAILED`; neither is completion. Only `CERTIFIED_COMPLETE` permits a claim that the North Star is complete.
- Final regression uses `validation_catalog` ids, records the executed evidence and input fingerprint, and invalidates its certificate if the confirmed North Star changes.

## Context Continuity For Large Reads

Ordinary file reading remains silent. When a turn accumulates a genuinely broad
read of historical code, documents, or prior project material, the project hook
keeps a bounded metadata-only read ledger and hierarchical context capsules
under `.agent/runtime/context/`; it never copies source text into supervisor
state. `index.json` contains only totals and directory pointers, while
`by-directory/<project-path>/_context.json` contains that directory's local
fingerprints and optional semantic checkpoint.

- Read in bounded slices by project directory or evidence domain. After each
  meaningful slice, the main thread emits a concise progress update containing
  confirmed conclusions, unresolved questions, and the next slice, then stores
  the reusable result with `context-note`. Do not wait for the whole archive to
  be read and do not emit a narration for every file.
- For code-heavy discovery, prefer symbol/reference retrieval when Serena is
  already available. Otherwise use bounded line ranges or an already-installed
  paged reader such as FastCtx. Never install either tool automatically, and do
  not treat its presence as proof that its output is correct. The fallback is
  the client's normal read/search tools with explicit ranges and small output.
- When the local ledger reports `CHECKPOINT_DUE` and the remaining material can
  be separated by module, archive, or evidence domain, the main thread should
  autonomously start read-only subagents for those independent slices. Each
  returns confirmed facts, key interfaces, dependencies, relevance, open
  questions, and evidence paths. The main thread merges their results, records
  directory checkpoints, and remains the sole implementation decision maker.
- Do not force subagents for a small read or for tightly coupled material that
  needs one reasoning context. A large read alone is insufficient: there must
  be at least two independently reviewable slices and the expected saved
  context must exceed coordination cost.
- `PreCompact` seals the local directory capsules. The plugin does not inject
  them into the main thread; the execution agent may inspect `status --verbose`
  and load only the directories needed for the current question.
- This is continuity support, not cross-project memory, and never blocks normal
  work when state is missing or unavailable.
- Directory conclusions are evidence-bound. When a referenced source changes,
  the capsule becomes `STALE`; load and revalidate that slice instead of
  trusting it or rereading the whole project.

Record conclusions, not private chain-of-thought:

```bash
python3 .agent/goal_compass.py context-note \
  --directory src/api \
  --fact "The request schema is validated before persistence." \
  --interface "create_job(request) returns a persisted job id." \
  --dependency "src/api depends on src/storage for durable writes." \
  --open-question "Retry ownership is not yet confirmed." \
  --next-action "Inspect src/workers retry handling." \
  --evidence src/api/schema.py
```

This command writes only explicit conclusions and evidence fingerprints. It
does not store source bodies, hidden reasoning, or automatically inject the
checkpoint into the main thread.

## Local Diagnostic Records

This edition keeps redacted plugin diagnostics in the project-local outbox only. It contains no network transport, device registration, remote endpoint, credential handling, or upload command.

## Optional Ticket Semantics

- Normal work may proceed with no ACTIVE ticket.
- `compile` creates `DRAFT`; `ready` validates an explicitly chosen contract; `start` freezes acceptance.
- Empty machine acceptance can never start or PASS.
- Budget and ordinary out-of-scope changes are advisories. Explicit forbidden and immutable paths remain hard boundaries.
- `check` reports truth but is non-binding; failed validation remains `VALIDATION_FAILED`.
- `close` is the only PASS certification authority. Failed close returns `NOT_CERTIFIED` and leaves the ticket ACTIVE for repair.
- One worktree still has one ACTIVE ticket. Independent worktrees may run disjoint tickets in parallel.

## Company Roles

Company mode is optional and task-shaped. Select only roles with distinct inputs, authority, deliverables, acceptance criteria, consumers, and stop conditions. Zero to four roles may be chosen automatically. More than four requires a cautious main-thread CEO expansion decision. Missing optional role output never blocks ordinary implementation or PASS certification.

### Optional Specialist Role Library

The plugin provides a pinned, MIT-licensed snapshot of the Agency Agents role
library as an expert reference. Complete release ZIPs and edition-specific
marketplaces bundle it. It is a library for the main thread, not a new decision
layer and not an automatic company roster.

- The upstream role prompts are preserved in full, including personality,
  workflows, default responsibilities, examples, deliverables, and metrics.
- Never inject all profiles. For a new detailed Goal, a high-confidence match
  must be loaded and used as expert input without asking the user. A
  low-confidence match asks the user once to choose an eligible role or skip.
  No relevant match stays silent. Outside Goal authoring, ordinary searches
  return candidates and do not silently add company roles.
- A selected profile supplies specialist perspective. It cannot silently alter
  the North Star, Goal-mode contract, ticket acceptance, allowed paths, company
  roster, or main-thread authority.
- Do not copy the role pack into the user project. It remains a plugin asset and
  is read only when confidence routing or an explicit specialist lookup needs it.
- The source commit, exact prompt hash, and MIT notice remain available in the
  manifest. Do not silently rewrite an upstream prompt. A demonstrable upstream
  error must be represented as a separate, visible local override.

From the plugin root:

```bash
python3 scripts/agency_role_pack.py status
python3 scripts/agency_role_pack.py list --division healthcare
python3 scripts/agency_role_pack.py search --query "China manufacturing supplier procurement" --limit 10
python3 scripts/agency_role_pack.py goal-brief --query "corrugated packaging manufacturing QA" --auto-select
python3 scripts/agency_role_pack.py show --role specialized/supply-chain-strategist
python3 scripts/agency_role_pack.py verify
```

`search` returns candidates only. `show` returns the exact untruncated upstream
prompt. The execution thread remains responsible for choosing the role and for
checking time-sensitive professional claims against current project evidence.

When authoring a new detailed Goal for an identifiable industry, the main thread
must run one local `goal-brief --auto-select` match. A high-confidence result is
used directly: load the exact selected prompt with `show` and incorporate its
task-specific input before finalizing the Goal. A low-confidence result asks the
user once to choose one eligible candidate or skip expert input. A result with
no relevant expert stays silent and continues without one. Explicit user or
main-thread role selection remains valid. The selected expert returns only
task-specific domain modules, dependencies, acceptance evidence, failure modes,
reusable tools, user-facing commercial/compliance questions, and forbidden
assumptions. The main thread remains the sole Goal author and preserves the
user's words and confirmed North Star.

## Evidence-Bearing Collaboration

Cross-thread or Codex-plus-GPT collaboration uses asymmetric ownership: one
main thread owns the Goal and final synthesis, one executor produces artifacts,
and an optional falsifier challenges evidence. Agreement, praise, restatement,
or another review request is activity, not progress. Only a new evidence id,
artifact reference, or accepted state transition counts.

Record a meaningful handoff with `convergence --record-collaboration`. A first
round without evidence returns `NO_EVIDENCE_WARNING`. Two consecutive rounds
without evidence return `CONSENSUS_WITHOUT_PROGRESS` and require the threads to
stop mutual review and execute, validate, or surface one concrete blocker.
Any evidence-bearing round resets this collaboration counter. Do not create an
unbounded model-to-model conversation or call the LLM Judge for ordinary notes.

## Goal Contract

When the user explicitly asks to create a new North Star, keep three artifacts separate:

1. **North Star:** the user's concise, durable project direction, normally one sentence. Preserve it verbatim after confirmation.
2. **Goal-mode objective:** a 2,000-3,500 character executable contract. It must remain actual content in the Codex Goal UI, not a slogan or a path-only placeholder.
3. **Super-complex project plan:** when the full plan exceeds Goal-mode capacity, write a project-relative Markdown/README plan longer than 4,000 characters. Goal mode still contains the 2,000-3,500 character compressed contract and references that plan. Never truncate the full plan to manufacture the compressed contract.

The Goal-mode contract must state:

1. `precise_goal` and reasoned `first_principles`;
2. current and desired state;
3. concrete modules and actions;
4. serial, parallel, or conditional execution relationships;
5. module inputs, dependencies, outputs, and contribution to the overall goal;
6. node exit criteria, deliverables, consumers, and machine or human acceptance evidence;
7. cross-module integration checks;
8. final end-to-end validation and evidence-backed delivery;
9. one positive `timebox_hours` for every capability segment, plus a thread-selected reminder interval for segments longer than two hours;
10. the open-source reuse decisions that belong to each module, and the persistent rule to refresh them every 24 hours of continued work.

Runtime alignment uses both artifacts without merging them. The North Star is
the durable direction check. The Goal-mode contract is the specific module,
dependency, output, exit-criteria, and acceptance check. Explicit anti-goals
and explicit Goal non-goals may enter the persistent correction mechanism, but
failure to find an obvious positive module match is advisory only; it may be a
legitimate prerequisite or bounded exploration. Never reduce this comparison
to keyword overlap against the full Goal prose.

Before authoring any detailed Goal-mode contract, or materially rewriting it:

1. Research current tools, open-source projects, product documentation, and relevant technical articles against the North Star and remaining work.
2. Use the findings to choose the route, but do not paste the research log or candidate list into the final technical plan.
3. When a reusable candidate is found, ask the user visibly in conversation whether to use/adapt it and whether the project is commercial. Do not infer either answer and do not hide the question in a JSON contract.
4. Only after the user answers, write the final plan, integrate the accepted tool into the relevant module, state how that integration will be validated, generate the Goal-mode contract, and run `goal-set --require-detailed`.

Research happens when the detailed Goal is first authored or materially rewritten, not on every continuation. If the same long task continues, refresh it after each 24-hour window against the North Star, detailed Goal, and remaining actions. Put the resulting reuse route in Goal mode; never paste the raw research log there. A suitable tool must be used or adapted and validated, not merely mentioned.

Every capability segment has an hour-level target in Goal mode. The first real product write silently starts the clock when exactly one dependency-ready segment is eligible, or when the current action names one segment unambiguously. Ambiguous parallel work is never guessed: use `convergence --start-segment <node_id>` when selecting it. The project stores an absolute wall-clock deadline. Segments of two hours or less normally have no pre-deadline reminder. Longer segments use the Goal-authored cadence. The hook emits at most one due checkpoint at that cadence until `convergence --complete-segment <node_id> --evidence-id <id>` (or a completed criterion) records the result. A missed deadline requires finish, validation, or an explicit split/replan; it is not silently extended and it does not create a ticket. Hooks can remind on subsequent Codex activity, but cannot wake a completely idle client process.

Use `goal-set --require-detailed` for this explicit operation. Never put only the North Star sentence, a path-only reference, or a truncated plan into Goal mode. Do not put Goal Supervisor, tickets, monitoring, or subagents into the product goal.

## Install

```bash
python3 <plugin-root>/scripts/install_governor.py /path/to/repo --force
```

The legacy-compatible script name installs Codex Goal Supervisor into `.agent/**` and `.codex/hooks.json` only. It must not overwrite root README, AGENTS, or project tests.

## Low-Noise Output

Default output should contain only: current truth, one reason, and one next action. Full MDCP/company/audit data is explicit `--verbose` material. `onboard-scan` prints a summary while keeping the full inventory in its report files; use `onboard-scan --verbose` only when the inventory is needed. An unconfirmed North Star means `NEEDS_CONFIRMATION`, not command failure. Never add approval, board, signature, HMAC, reverse-signal, or role-signoff workflows.
