# Goal Compass Runtime Status

Date: 2026-07-13

## Build

- Source: `<plugin-root>`
- Version: use `.codex-plugin/plugin.json` as the current build identifier.
- Installed cache: the cachebuster-matched path reported by `codex plugin add`.
- Runtime SHA-256: `85dce52be6d233c17d98f62e49f9b988156a1462678c755b462a4b4386bb0a43`
- Skill SHA-256: `debe11ebbe641cc35e6f114a857577658226bf2f5a04bfd389489f0348e84347`
- Source and installed cache hashes match.

## Observed State Before This Pass

The four supplied long-run reviews converged on these runtime symptoms:

- validation was repeated during close;
- runtime database churn and upstream evidence invalidation were reported as
  product drift or budget failure;
- planning and execution role receipts required redundant manual calls;
- non-Git diff usage counted whole files;
- phase status could remain ACTIVE after the intended exit;
- default MDCP output obscured the actual status;
- syntactic artifact existence could be mistaken for product quality;
- request operation routing could differ between Chinese and English.

## Runtime Behavior After This Pass

| Scenario | Current result |
|---|---|
| `check --run-validation` passes, inputs unchanged, then `close` | close reuses the passing validation cache |
| validation lifecycle contains setup/health/teardown | lifecycle reruns; cache reuse disabled |
| service-owned SQLite/runtime path changes | `ENVIRONMENT_DIRTY` or `IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY` |
| read dependency changes after start | `UPSTREAM_EVIDENCE_INVALID` |
| one line changes in a large non-Git text file | real line delta, not whole-file line count |
| compiled ticket exceeds only change-size guidance | soft budget advisory; scope/tool/time remain hard |
| declared artifact quality gate lacks evidence | `NEEDS_QUALITY_EVIDENCE` |
| planning child returns one completed result before start | auto STARTED receipt, preserved if roster hash matches |
| ticket declares phase completion on pass | matching active phase becomes COMPLETED |
| default status/check/close | compact operational output |
| explicit `--verbose` | full MDCP and diagnostic output |
| runtime evidence includes PID/port/checkpoint | checkpoint appears in status; no kill authority is inferred |

## Verification Results

- Python compile: PASS
- `python3 -m unittest -q verification.tests.test_goal_compass`: 219 tests,
  44.512 seconds, PASS
- `python3 -m unittest discover -s verification/tests -v`: 219 tests,
  43.760 seconds, PASS
- `python3 assets/governor-harness/.agent/selftest/test_goal_compass.py`:
  0.354 seconds, PASS
- Skill schema validation: PASS
- Plugin schema validation: PASS

## Explicit Limits

- The plugin does not claim retrospective PID-level file-writer attribution.
- Runtime checkpoints do not implement cross-thread resource locking.
- Hooks enforce time/tool budgets between tool calls; arbitrary opaque tool calls
  cannot be forcibly interrupted from inside Goal Compass.
- Acceptance remains frozen after start; invalid premises use a replacement
  ticket and an accurate terminal classification.
- Goal Janitor remains `MARK_ONLY` and cannot move or delete project files.
