# Goal Supervisor Functional Backtest - 2026-08-03

## Scope

This backtest used the final distributable archive as an installer input, a fresh non-Git user project, the real local Codex CLI, deterministic failure fixtures, a large-workspace status fixture, focused capability tests, and the complete verification suite.

## Results

| Capability | Result | Evidence |
| --- | --- | --- |
| Clean ZIP installation | PASS | Fresh project install completed in 0.432s and preserved the user's root README. No root AGENTS file was added. |
| Feedback privacy default | PASS | Installed configuration remained `upload_enabled=false` and `local_outbox_only`; a reported false positive was stored locally. No live upload was attempted. |
| Structured North Star | PASS | `goal-set --require-detailed` accepted the detailed contract and `status` reported the confirmed goal. |
| Codex client Goal mode | CONTRACT PASS, NOT SCRIPT-OBSERVABLE | The skill requires the Agent to start client Goal mode from the same North Star and has a regression test. The project runtime cannot independently inspect Codex UI Goal-mode state. |
| L0-L3 goal stack | PASS | Fresh-project status exposed the final goal, current stage, current action, and expected evidence. |
| Silent ordinary execution | PASS | An ordinary source edit produced no hook output and was recorded by the observer. |
| Deterministic control boundary | PASS | Direct modification of `.agent/current_ticket.json` was denied while ordinary work remained unblocked. |
| Activity/progress separation | PASS | Activity events did not complete success criteria. Explicit evidence created progress. |
| Iteration stagnation | PASS | Two completed iterations without new evidence made semantic review eligible; individual commands and event counters did not. |
| Evidence recovery | PASS | A subsequent evidence-bearing iteration reset no-progress count and persisted the evidence ID. |
| Phase completion semantics | PASS | Completing the phase cleared L2, L3 action, and expected evidence while preserving historical checkpoint data. |
| Real Codex CLI Judge | PASS | Fresh install returned schema-valid `ALLOW_SCOPED_ACTION` with high confidence in 21.197s. Ordinary execution remained unblocked. |
| Judge cache | PASS | Repeating the same packet returned `CACHED` in 0.192s with the same fingerprint. |
| Judge missing executable | FIXED, PASS | Backtest initially reproduced an uncaught `FileNotFoundError`. Runtime now returns `UNAVAILABLE`, `INSUFFICIENT_EVIDENCE`, and scripted advisory continuation. |
| Judge timeout/malformed output | PASS | Deterministic tests confirm both paths fail open without semantic blocking. |
| Third semantic deviation rail | PASS | Tests confirm a targeted rail requires high-confidence Judge confirmation; an allow verdict keeps execution advisory-only. |
| Persistent deviation lifecycle | PASS | Tests cover 30-minute recheck, unrelated success not clearing the incident, explicit correction, seven active clean days, and immediate recurrence. |
| Large-workspace status | PASS | Non-Git fixture with 11,561 files and a 130KB validation catalog returned in 0.1501s with 1,624 bytes of output and no scan. |
| Hard acceptance and validation DAG | PASS | Existing acceptance and validation suites retain draft/start/close rules, fail-fast validation, and failure status priority. |
| Request, Janitor, onboard scan | PASS | Existing suites retain heavy-scope routing, strong-evidence protection, mark-only cleanup, and clean-repository inventory scanning. |
| Parallel tickets | PASS | Existing tests retain disjoint-lane parallelism, dependency serialization, writable-path overlap detection, and coordination-contract checks. |
| Feedback receiver and reuse discovery | PASS | Full suite covers local feedback delivery state, receiver contracts, first-use/five-day reuse policy, and explicit integration decisions. Live upload was intentionally not exercised because upload is disabled by default. |
| Concurrency and Windows hooks | PASS | Full suite covers concurrent state updates, process-group timeouts, nested project dispatch, fixed Windows launcher paths, and non-interactive installer behavior. |

## Test totals

- Focused capability matrix: 228 tests in 35.864s, all passed.
- Full module suite: 334 tests in 52.378s, all passed.
- Full discovery suite: 334 tests in 51.981s, all passed.
- Selftest: 0.32s, passed.
- `py_compile`: passed.

## Confirmed limitation

The project runtime can prove the North Star contract, convergence projection, and hook state. It cannot independently query whether the Codex desktop Goal-mode UI is currently active. That activation is enforced by the plugin skill's execution instructions and the Codex client tool call, not by pretending a project-local JSON field proves UI state.
