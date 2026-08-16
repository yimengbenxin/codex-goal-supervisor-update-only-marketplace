# Program Outline And Phased Goals

This document defines the implemented structured phased-Goal contract. It is
for super-complex projects whose full delivery cannot fit one useful native
Codex Goal. Small work continues directly and does not acquire phase ceremony.

## Problem

A large project should not be forced into one permanent 2,000-3,500 character
Goal. The durable North Star may remain stable while delivery moves through a
small number of independently useful, independently verifiable phases. One
oversized Goal makes the active acceptance too broad and encourages shallow or
repetitive execution.

## Implemented Model

1. Keep one concise, durable project North Star.
2. Before implementation, research current open-source tools and proven routes,
   then write a shallow project program outline.
3. The outline records a small set of phase IDs, boundaries, dependencies,
   shared contracts, outputs, consumers, project contribution, and final
   acceptance. It does not fully design every phase in advance.
4. Before each phase begins, repeat online reuse research against that phase's
   current scope and remaining project work. A suitable tool must be integrated
   and validated, not merely mentioned.
5. Write the current phase's full technical route in a durable local phase
   document. Generate the native Codex Goal from a 2,000-3,500 character
   executable projection of that phase and link the phase document.
6. Complete and validate the current phase before replacing the native Goal
   with the next dependency-ready phase Goal. Every action must align with both
   the project North Star and the current phase Goal.

The project outline follows the useful part of GitHub Spec Kit's documented
"spec of specs" approach: a shallow roadmap first, then one self-contained
specification per independently testable slice. Goal Supervisor will adapt the
pattern without requiring Spec Kit, LangGraph, or another workflow runtime.

References:

- https://github.com/github/spec-kit/blob/main/docs/concepts/spec-of-specs.md
- https://github.com/github/spec-kit/blob/main/README.md

## Goal Granularity

- A phase Goal should normally represent 2-24 hours of execution.
- Work estimated below two hours remains an action inside the current phase; it
  does not create a new Goal.
- Work estimated above one day must be split at a coherent business capability,
  integration boundary, or independently verifiable outcome.
- Do not split by file count, minor patch, or administrative step.
- Keep the phase count small. Add a phase only when it owns a distinct outcome,
  acceptance contract, dependencies, and downstream consumer.
- Estimates are initial predictions, not hard failure budgets. Validation truth
  and delivered business value remain authoritative.

## Progressive Detail

The program outline stays shallow so it does not become a large stale plan. Only
the current phase receives full implementation detail. The next phase may keep
a short boundary note until the current phase produces evidence that can change
its design.

Each detailed phase route must include:

- goal and business outcome;
- inputs, outputs, consumers, and shared contracts;
- actions and dependency order;
- safe parallel opportunities;
- reuse decision and integration point;
- expected duration and absolute start-time deadline;
- machine acceptance and final phase regression;
- contribution to the project North Star and program acceptance.

## Local Estimation Evidence

Goal Supervisor should record bounded project-local telemetry for each phase:

- predicted duration and actual duration;
- first product action, first valid evidence, and completion timestamps;
- acceptance progress, retries, and rework;
- interruption or stall reasons;
- whether decomposition reduced or increased total execution cost.

This telemetry stays local by default. It may join sanitized feedback only after
the project's existing explicit upload consent. Later releases can use the
evidence to improve phase-size recommendations without treating estimates as
blocking truth.

## Runtime Contract

The outline and current phase are project-owned JSON inputs. The plugin keeps
the concise confirmed North Star unchanged and projects only the current
phase's 2,000-3,500 character detailed objective into native Goal mode.

```bash
python3 .agent/goal_compass.py phase-set \
  --outline-file program-outline.json \
  --definition-file phase-01.json

python3 .agent/goal_compass.py phase-complete \
  --reason "Phase acceptance passed"

python3 .agent/goal_compass.py phase-advance \
  --definition-file phase-02.json \
  --reason "Phase 01 validated"
```

After `phase-set` or `phase-advance`, the execution Agent must create the native
Codex Goal from the exact returned `goal_mode_objective`, then compare the
native objective length and SHA-256 with `native_goal_sync`. The CLI cannot
silently rewrite an already-active native Goal. The current native Goal must be
completed before the next phase objective is created.

`phase-complete` runs the phase's validation-catalog IDs. A failure leaves the
phase `ACTIVE`; only a passing phase can become a dependency for the next
phase. `status` reads a compact phase summary. Prediction, deadline, first
product action, first valid evidence, attempts, completion, and actual duration
remain project-local.

## Acceptance

- Large projects produce a program outline before the first phase Goal.
- Program-outline research and current-phase research are distinct recorded
  probes.
- Exactly one phase Goal is active in a Codex task at a time.
- The native Goal exactly matches the current phase projection, not the entire
  project plan.
- Phase completion requires its own acceptance and affected project regression.
- The next phase is derived from the durable outline plus newly verified facts.
- Small work is not inflated into ceremonial Goals.
- No telemetry or feedback leaves the device without explicit consent.
