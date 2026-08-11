# Goal Compass Cross-Industry Long-Run Stress Report

> Historical test report. Its `runtime_execution_verified=false` limitation was addressed on 2026-07-12 with explicit per-role runtime receipts; the original observations remain unchanged below.

Date: 2026-07-11

## Verdict

The current build passed a long-run, cross-industry bounded-ticket stress run and the complete verification suite.

- Final benchmark: 16 industries, 128 ticket lifecycles, 42.6681 seconds.
- Default company: 4 roles on every normal ticket.
- Expanded company: 7 roles required CEO confirmation in every industry.
- Same-axis fatigue: detected in every industry after repeated local tickets.
- Heavy marketplace request: routed to `BACKLOG` in every industry.
- Validation failure: blocked in every industry.
- Clean diff-budget overrun: blocked in every industry.
- Recovery ticket after failure: completed in every industry.
- Janitor: produced marks/plans only and did not move or delete product files.
- North Star: preserved in every industry.

The plugin is suitable for supervised long-run trials made of many bounded tickets. It still cannot prove from Python alone that Codex actually spawned every planned child agent; the runtime contract continues to report `runtime_execution_verified=false`.

## Industries

The final run covered:

1. Aviation maintenance
2. Clinical trial operations
3. Municipal water operations
4. Emergency response
5. Insurance claims
6. Hotel revenue operations
7. Public transit control
8. Museum digital collections
9. Semiconductor fab operations
10. Telecom network assurance
11. Construction BIM coordination
12. Port logistics
13. Mine operations
14. Municipal waste operations
15. Fisheries management
16. Property asset operations

Each industry executed eight lifecycle scenarios: four normal tickets, one CEO-expanded company ticket, one deterministic validation failure, one recovery ticket, and one clean diff-budget failure/abort. The run also exercised request routing, axis fatigue, onboard scan, prune plan, mark-only prune apply, and final goal detection.

Raw final result: `docs/GOAL_COMPASS_LONG_RUN_CROSS_INDUSTRY_20260711.json`.

## Independent Pressure Runs

Four actual subagents independently tested different surfaces before the final benchmark:

- Strategy: 4 industries, 40 ticket lifecycles; North Star hashes remained unchanged after tickets 5 and 10.
- Product: 4 industries, 12 tickets, 48 change requests; exposed request/backlog cross-talk and domain-word false mapping.
- Engineering: 4 industries, 176 real CLI calls in 28.16 seconds; exercised 5/8/12 department companies, budget failures, validation failures, and recovery.
- QA: checked empty acceptance, validation state, Janitor behavior, 4/5/128 department plans, roster invalidation, and same-axis close semantics.

These subagents tested the plugin. They do not constitute machine evidence that every simulated product ticket spawned its planned company. That distinction remains explicit.

## Problems Found And Fixed

### 1. Same-axis fatigue contradicted close

Before: `check` returned `PASS_READY`, but a strong axis warning made `close` fail.

Now: the current valid ticket can close with `close_then_switch_axis`. Validation failure, drift, and budget failure still take priority over axis advice.

Why: fatigue should stop the next same-axis expansion, not invalidate work that already meets frozen acceptance.

### 2. Future backlog text rejected current business work

Before: a future item such as catastrophe assignment after claim intake could reject a current claimant-photo request because two generic words overlapped.

Now: request policy matching requires an exact phrase or a substantial item-specific overlap. The original user request is preserved when routed to backlog.

Why: future work often names its current prerequisite; that dependency wording must not absorb the current ticket.

### 3. Industry identity falsely justified heavy scope

Before: `real_estate` became `real + estate`, so an enterprise marketplace request looked like two current-ticket mappings and became `ACCEPT_SIMPLIFIED`.

Now: North Star domain words and allowed-path words cannot by themselves justify heavy-scope simplification. All 16 final marketplace probes route to `BACKLOG`.

Why: sharing an industry label is not evidence that a request advances current acceptance.

### 4. Validation vocabulary was over-rewarded

Before: mentioning validation/test language could be accepted without a real mapping to current acceptance.

Now: validation language must map to the current acceptance contract; unrelated validation work is not automatically accepted.

Why: technical wording must not outrank business relevance.

### 5. SPLIT did not persist its backlog item

Before: `SPLIT` returned a backlog item but did not write it.

Now: both `BACKLOG` and `SPLIT` preserve the original request in `.agent/backlog.jsonl`.

Why: a routing result without durable state disappears between tickets.

### 6. CEO confirmation did not cover role authority

Before: the roster fingerprint covered role/model/effort/phase, but not objective or workspace access. A custom department could become a writer without invalidating old confirmation.

Now: the fingerprint also covers objective, workspace access, and fallback policy.

Why: changing what a department may do is a material roster change.

### 7. Exact North Star was reported as partial

Before: an identical confirmed goal could return `PARTIAL`.

Now: canonical exact equality returns `ALIGNED` with score `1.0`.

Why: exact source-of-truth equality is stronger than fuzzy overlap.

### 8. Industry safety language did not trigger consequence routing

Before: terms such as airworthiness release, clinical dosing, water-quality interlock, and public-warning authorization did not count as high consequence unless rewritten as software-security language.

Now: domain high-consequence terms provide the first T3 key. `ultra` still requires the second key: concrete evidence that prior xhigh reasoning was insufficient.

Why: model routing should understand real operational consequences without making every strategic ticket ultra.

### 9. Generated state appeared in an ineffective acceptance clause

Before: compile emitted `.agent/**` in `files_not_changed`, although generated state is excluded from product diff metering.

Now: compile leaves `files_not_changed` empty until the ticket author adds real product paths. Hook protection of control files is unchanged.

Why: acceptance fields should describe behavior the evaluator can actually measure.

## Company Gate Results

- Four roles are the default: strategy, product, engineering, QA.
- Four roles require no CEO confirmation.
- Seven-role plans were blocked at `ready` until the main-thread CEO supplied the exact fingerprint.
- Changing role membership, objective, or workspace authority invalidates the confirmation.
- Domain high-consequence plus prior insufficiency makes the root CEO eligible for `ultra`; department agents remain capped at their declared range and are never auto-routed to `ultra`.
- Same-axis fatigue appears as a close-and-switch advisory after the current acceptance passes.

## Known Limits

1. Company execution evidence remains external. The ticket records the exact required plan, but Goal Compass cannot authenticate Codex child-agent calls by itself.
2. Axis fatigue is an advisory for selecting the next ticket, not a hard next-ticket lock. This avoids trapping a valid current ticket but still relies on the main runtime to switch axes.
3. Goal Janitor remains `MARK_ONLY`; accuracy improvements do not grant delete or move authority.

## Verification

```text
python3 -m py_compile ...
PASS, 0.16s

python3 -m unittest -q verification.tests.test_goal_compass
Ran 158 tests in 24.671s
OK (24.75s wall)

python3 -m unittest discover -s verification/tests -v
Ran 158 tests in 24.315s
OK (24.40s wall)

python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
Goal Compass selftest OK (0.34s wall)
```

The full 16-industry benchmark is intentionally kept as a standalone long-run command. The normal verification suite runs two representative lifecycle cases so daily regression remains below 60 seconds.
