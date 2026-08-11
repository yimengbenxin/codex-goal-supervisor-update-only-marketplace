# Goal Compass Adaptive Company Subagents

> Historical design note. The 2026-07-12 runtime adds per-role STARTED/COMPLETED/FAILED receipts and a close gate; statements below that runtime execution is always unverified describe the earlier build only.

Date: 2026-07-11

## Purpose

Company subagents improve execution quality, delivery speed, and North-Star
alignment. They are not an opinion panel and are not an approval chain. Adding a
department is justified only when it owns an independent deliverable that the
current roster cannot cover more efficiently.

## Dynamic Scaling

Goal Compass automatically selects zero to four task-relevant departments. Four
is the automatic-selection ceiling, not a default size and not a fixed roster.

Examples:

- status/read-only work or a tiny low-risk action may use zero departments;
- one mechanical implementation may use engineering only;
- a validation-heavy patch may use engineering and QA;
- a commercial opportunity may use strategy, business, product, and finance;
- an algorithm system may use product, algorithm, engineering, and QA;
- a packaging production slice may use manufacturing, quality, engineering,
  and operations.

Task depth controls model and effort within each department's declared range.
Task breadth controls department count and role mix. These controls are
independent: a narrow difficult problem can use one strong department, while a
broad routine problem can use several lower-cost departments.

## Department Contract

Every selected department must have:

- responsibility and decision authority;
- required inputs;
- concrete deliverables;
- acceptance criteria;
- downstream consumers;
- forbidden scope;
- dependencies and handoffs;
- minimum, recommended, and maximum model/effort routing;
- one stop condition and a reason for joining.

Canonical departments receive complete built-in contracts. A custom department
must be a structured object with the same fields; a bare custom string is
invalid. Each department returns one concise structured deliverable and exits.

## CEO Expansion

More than four departments requires an explicit main-thread CEO decision. The
default is `KEEP_CURRENT`, and copying the generated template does not unlock
expansion. A valid `EXPAND` decision must state:

- why the current zero-to-four roster is insufficient;
- the expected execution or rework reduction;
- how coordination cost will be contained;
- the exact roster-contract fingerprint.

There is no protocol-level department cap. If a real project has hundreds of
independent departments with complete contracts, the CEO may confirm them. The
recommended action is still to add one or two at a time; bulk expansion is for
already-separated workstreams with explicit handoffs.

Any change to authority, inputs, deliverables, acceptance, model, effort,
workspace access, or roster invalidates the confirmation.

## Model Routing

- Strategy and architecture use stronger Sol ranges.
- Business and product use Sol ranges appropriate to product and commercial
  reasoning; they remain separate roles.
- Engineering, algorithm, data, manufacturing, QA, and operations favor
  Terra/Luna-to-Terra ranges according to task depth.
- `xhigh` is shown as `Extra High` in the Codex UI.
- `ultra` is never auto-assigned to a department. It is only an optional root
  CEO capability for critical work because it may coordinate agents itself.

## Runtime Boundary

`goal_compass.py` writes and validates the company contract but does not falsely
claim that Codex spawned the agents:

```text
runtime_binding = external_runtime_required
runtime_execution_verified = false
```

Child agents cannot expand the roster, start nested companies, change frozen
acceptance, or chat in loops. The main thread integrates outputs and owns machine
acceptance.

## Verification

Tests cover dynamic zero-to-four selection, distinct business/product roles,
commercial and algorithm rosters, model/effort ranges, complete department
contracts, conservative CEO expansion, stale-fingerprint invalidation,
unbounded confirmed capacity, root-only Ultra eligibility, cross-industry long
runs, and packaging-manufacturing runs.
