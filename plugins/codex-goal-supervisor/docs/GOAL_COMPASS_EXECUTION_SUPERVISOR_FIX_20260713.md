# Goal Compass Execution Supervisor Fix

Date: 2026-07-13

## Why this pass existed

Four long-running project reviews agreed on the same failure pattern: Goal
Compass prevented scope drift, but its own process cost could dominate product
work. The root problem was not one bad threshold. Several unrelated concepts
were collapsed into the same fields and statuses:

- writable product scope, read-only evidence, and service runtime files all used
  `allowed_paths`;
- a passing validation had no reusable identity, so `close` ran it again;
- upstream evidence changes, product drift, and runtime churn all became DRIFT or
  BUDGET_EXCEEDED;
- role participation required ceremonial STARTED and COMPLETED calls;
- non-Git line budgets counted whole files instead of actual line changes;
- file existence could be treated as product or artifact quality;
- default output repeated large MDCP structures;
- phase state had no normal complete/advance transition.

This pass changes those mechanics without adding an approval system.

## Changes

### 1. Four path responsibilities

Tickets now distinguish:

- `writable_paths`: the only product paths Codex may edit;
- `read_dependencies`: upstream inputs that may be read but not edited;
- `immutable_paths`: frozen evidence and validation-protected paths;
- `runtime_paths`: service-owned databases, logs, checkpoints, and process state.

Legacy `allowed_paths` remains a writable fallback. Validation catalog
`reads_paths` feeds read dependencies; `protects_paths` feeds immutable evidence
and never expands writable scope.

Why: a dependency can be required by validation without being editable. A
running SQLite database can change without being a code patch. Keeping those
facts separate prevents contradictory “required and forbidden” reports.

### 2. Environment and upstream state semantics

Runtime-only changes produce `ENVIRONMENT_DIRTY`. If machine acceptance passes,
the result is `IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY`. A changed read dependency
produces `UPSTREAM_EVIDENCE_INVALID`, not DRIFT. `abort --classification` also
supports `SUPERSEDED_BY_RECOVERY`.

Why: long-run statistics are useless when premise failure, system activity, and
goal drift share one label.

### 3. Real non-Git line deltas

Small text baselines store compact per-line hashes. A one-line replacement in a
large file is now approximately two changed lines instead of the whole file.
Large/binary files use a bounded size-delta estimate. Existing unrelated
top-level directories are not scanned unless the ticket names them; new roots
and the ticket's product roots remain visible.

Why: whole-file line counts encouraged monoliths and punished local fixes.

### 4. Validation cache and preflight

Passing validation stores an input fingerprint covering frozen acceptance,
catalog entries, and actual input hashes. Unchanged `close` reuses that pass.
Failed results are never reused. Setup/healthcheck/teardown lifecycle validation
always reruns. The successful cache fingerprint is recorded after validation,
so a validation-produced acceptance artifact does not force an immediate rerun.

`ready` now performs one read-only preflight and returns all known missing
executables, dependencies, immutable inputs, and path-contract conflicts.

Why: validation should prove a state once, and early diagnosis should expose a
blocker chain once instead of manufacturing a sequence of recovery tickets.

### 5. One role result instead of receipt ceremony

`company-record --status COMPLETED` automatically records a missing STARTED
event and releases the role. Planning roles can write their result directly to a
pending ticket using `--ticket`. Start preserves those receipts only when the
frozen company roster fingerprint is unchanged.

Why: a real structured result is evidence. Requiring two manual calls for the
same attempt added no independent information.

### 6. Soft change-size pressure

Newly compiled tickets keep hard scope, tool-call, and active-time limits.
File-count and diff-line limits become soft pressure: the check reports
`SOFT_CHANGE_PRESSURE`, but a scope-clean patch with passing acceptance may still
close. Legacy tickets without the policy retain hard behavior.

Why: change-size limits should trigger compression or a split, not force sound
architecture into one file.

### 7. Artifact and product quality evidence

Optional `quality_gates` support four dimensions:

- `technical_pass`;
- `artifact_quality_pass`;
- `product_pass`;
- `market_pass`.

A declared gate must name a validation id, evidence type, or both. An existing
MP4, GLB, page, or JSON file does not satisfy an explicitly declared quality
gate. Existing tickets with no quality gates remain compatible.

Why: technical existence and actual usefulness are different claims.

### 8. Compact default output

`status`, `check`, `close`, `ready`, and `start` no longer repeat full MDCP
contracts by default. Compact output retains the actual status, next action,
acceptance consumer, scope anchor, company completion, quality, and validation
cache state. `--verbose` restores the full diagnostic contract.

Why: supervision state should be easier to see than the protocol that produced
it.

### 9. Program phase lifecycle

`phase-set` refuses to overwrite an ACTIVE phase. `phase-complete` closes it;
`phase-advance` closes the current phase and opens the next. A ticket may opt in
to automatic phase closure with a matching `program_phase_id` and
`phase_completion.complete_on_pass=true`.

Why: phase, ticket, and goal should not silently disagree after a successful
close.

### 10. Bilingual request operation routing

Request operation classification now detects mutation before generic review
language and normalizes common Chinese read-only/mutation phrases. “Review and
fix” and “检查并修复” are edits; “inspect without changes” and “只读核查，不要修改”
are read-only.

Why: language choice must not change the operation class.

### 11. Long-running process evidence

Existing `evidence-add --type runtime` can store owner, PID, port, checkpoint id,
resume command, and resource names. Validation subprocess timeouts terminate the
whole process group and drain output pipes.

Why: a long task needs a recoverable handoff, and a timed-out validation must not
leave child processes behind.

## Intentionally not claimed as solved

### Retrospective writer attribution

Git state cannot reliably identify which PID wrote a file after the event. Real
writer attribution requires a persistent file watcher, OS audit source, or
service cooperation. This version classifies explicit runtime paths and records
runtime ownership evidence, but does not invent PID attribution.

### Cross-thread resource locks

Reliable GPU, port, process, and directory leases require a shared registry or
daemon visible to every Codex thread. Runtime checkpoints are evidence only and
grant no authority to kill another task's process.

### Interrupting a child agent inside one tool call

Hooks enforce budgets between tool calls. They cannot guarantee interruption in
the middle of an opaque long-running tool call. Validation commands now have
real process-group timeouts; arbitrary agent tools still depend on the host
runtime.

### Mutating frozen acceptance

Acceptance remains immutable after start. A stale or invalid premise must end as
`UPSTREAM_EVIDENCE_INVALID` or `SUPERSEDED_BY_RECOVERY`, followed by a new ticket.
Silently changing acceptance would fix ceremony by reintroducing goal drift.

## Compatibility

- `allowed_paths` still works as writable scope.
- Tickets without `quality_gates` are unchanged.
- Tickets without `budget.change_enforcement=soft` keep hard change limits.
- Full MDCP data remains available with `--verbose`.
- Goal Janitor remains `MARK_ONLY`; no move or delete authority was added.
- No HMAC, board, signature, reverse signal, or approval chain was introduced.

## Verification

The regression suite includes explicit coverage for validation reuse, one-call
company receipts, non-Git line deltas, runtime SQLite isolation, upstream
evidence invalidation, soft change budgets, quality evidence, bilingual routing,
phase closure, compact output, runtime checkpoints, and aggregate preflight
errors. Core historical acceptance, request, Janitor, onboard-scan, install,
MDCP, hook, cross-domain, long-run, and packaging manufacturing tests remain.
