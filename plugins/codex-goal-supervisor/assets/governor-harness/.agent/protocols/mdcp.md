# Multi-Dimensional Collaboration Protocol For Goal Compass

MDCP is used here as a cross-layer rule library, not as a new workflow.

Supreme rule: every protocol step must create net execution benefit. Skip,
simplify, or serialize a step when its coordination cost exceeds the rework or
risk it prevents.

Source reference:
https://github.com/HanShengrunning/-multi-dimensional-collaboration-protocol

Goal Compass maps MDCP into three existing layers:

1. Structured expression + pass criteria:
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

## Adaptive Company Subagents

Company subagents are adaptive. The main thread coordinates and integrates;
zero to four task-relevant departments may be selected automatically. Zero is
correct when delegation would cost more than the bounded direct action. Task
depth chooses model effort while task breadth chooses the actual departments.

Company execution rules:

- The main thread is the only ticket writer and final integrator.
- Child agents receive one bounded role brief and return once.
- Read-only planning roles run before product edits; writers stay inside
  `allowed_paths`; QA/auditor roles consume validation results.
- A child agent cannot start another company mode.
- Every department must own a distinct responsibility, inputs, deliverable,
  consumer, acceptance criteria, forbidden scope, and stop condition.
- More than four child agents require explicit main-thread CEO confirmation.
  Confirmation records the reason and exact roster fingerprint, so adding or
  changing a department invalidates the old confirmation.
- `requested_company_departments` may activate any number of canonical or
  custom departments. The protocol has no hard department capacity.
- Large rosters are dispatched in bounded waves. Every confirmed department
  still receives its own agent; wave sizing limits concurrency, not total count.
- The company plan records what runtime must do. It does not falsely claim that
  child agents already ran; `runtime_execution_verified` remains false because
  execution belongs to the Codex skill/runtime.
- Company work never replaces hard acceptance, validation, Janitor, or Auditor.

Optional specialist role packs may enrich a selected department, but they do
not select departments or become decision authorities. The main thread chooses
whether to inspect and use a profile. A role pack must preserve source
provenance and may provide the complete raw prompt without truncation. Its
personality, workflows, and default responsibilities remain expert input;
North Star, Goal, frozen acceptance, project facts, and the department contract
remain the execution authority. Role packs stay in the plugin and are never
copied into the user project by the installer.

## Parallel Ticket Lanes

Parallelism is allowed only when it produces net execution benefit. Every lane
uses its own Git worktree and ACTIVE ticket. The shared registry rejects causal
dependencies, producer/consumer edges, overlapping writable paths, and contract
version mismatches.

Parallel lanes freeze one short risk-adaptive coordination contract containing:

- independence evidence;
- estimated serial, parallel, coordination, and integration minutes;
- minimum lane and cross-lane validation;
- an integration owner;
- only the technical dimensions actually shared, such as language/runtime,
  architecture boundaries, interfaces, data, naming, serialization/units/time,
  errors, compatibility, toolchain, observability, and resources.

If parallel elapsed time plus contract and integration cost does not clearly
beat serial execution, the tickets run serially.

Operational translation:

- R3 scope anchoring -> every ticket gets scope_anchor and scope checks.
- R4 precision response -> every request/ticket gets precision_level.
- R9 loop interruption -> status/check show same-axis fatigue warnings.
- R11 consumer confirmation -> acceptance_consumer is refreshed before execution.
- Parallel lane check -> independence, frozen compatibility, and positive net
  time gain are required before simultaneous start.
