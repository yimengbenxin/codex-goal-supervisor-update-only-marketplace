# Codex Goal Supervisor Execution Convergence and Sparse LLM Judge

## Why this change exists

Goal Supervisor must help a long task converge without turning every action into a managed workflow. Scripted observers are cheap and reliable for deterministic facts, but they cannot safely resolve every semantic ambiguity. Calling an LLM on every event would create the same process tax the tool is intended to remove.

This release therefore separates mandatory activation from optional intervention:

- Explicit plugin activation for a substantive task requires one structured project North Star and the matching Codex client Goal mode.
- After activation, ordinary reads, edits, tests, and delivery remain unblocked and are observed silently.
- Tickets, company roles, Custodian, Auditor, Janitor, convergence records, and semantic judgment remain explicit or event-driven tools.

## What changed

### Four-level goal stack

Project-scoped convergence state now records:

- L0: final project goal.
- L1: evidence-bearing success criteria.
- L2: current program phase or active ticket stage.
- L3: current action and the evidence expected from it.

Initialization also re-projects an existing confirmed North Star. This prevents a runtime upgrade from showing a confirmed goal in `north_star_goal.json` while reporting a null L0 in status.

### Activity is not progress

Observer events, writes, validation calls, and failures are activity. Progress requires evidence or an explicitly completed criterion. Iteration evidence IDs are persisted in the bounded evidence set instead of merely toggling a progress flag.

### Iteration convergence

Important iterations can record:

1. hypothesis;
2. change;
3. expected result;
4. validation;
5. observed result;
6. accept, retry, or revert decision;
7. evidence and completed criteria.

Two completed iterations without new evidence produce a strategy-review signal. This is an iteration-level condition, not a counter for every command or failed event.

### Drift recovery

The convergence projection keeps the latest evidence checkpoint, current blocker, and one recommended recovery action. It favors repair, strategy change, or restoration of the latest evidence checkpoint over repeated blind execution.

### Sparse Codex CLI Judge

The Judge is invoked only for consequential semantic ambiguity:

- a third targeted North Star rail is pending;
- a high-cost action has an unclear relationship to the current stage;
- an Agent appeals a supervisor judgment with new evidence;
- two completed iterations produced no new evidence;
- a user or Agent explicitly requests judgment.

The Judge receives bounded project metadata only. It runs from a neutral temporary directory with user config and repository rules ignored, a read-only sandbox, a strict JSON schema, a process timeout, and process-group termination. Results are cached by a policy-and-packet fingerprint. Missing CLI, timeout, command failure, or malformed output fails open to scripted advisory behavior.

The Judge cannot edit files, rewrite the North Star, approve work, or block ordinary execution by itself. A third semantic rail requires a high-confidence `CONFIRM_TARGETED_RAIL`; all unavailable or uncertain outcomes remain warnings.

## User-visible behavior

- `status` remains a compact cached summary and now includes L0, L2, L3, evidence progress, and recovery action.
- `convergence` is an optional explicit interface for status, iteration records, and semantic judgment.
- Full details remain behind `--verbose`.
- V2 remains local-only for feedback unless a user explicitly enables upload.

## Verification coverage

The verification suite covers initialization and migration, four-level projection, activity/progress separation, evidence persistence, stagnation triggers, sparse trigger policy, read-only neutral CLI execution, cache reuse, timeout/malformed fail-open behavior, targeted-rail confirmation, mandatory North Star plus client Goal-mode skill instructions, installer non-interactive stability, and the existing V2 regression surface.
