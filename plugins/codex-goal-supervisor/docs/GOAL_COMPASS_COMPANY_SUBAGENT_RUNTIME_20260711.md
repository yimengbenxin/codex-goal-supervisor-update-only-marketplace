# Goal Compass Company Subagent Runtime Observation

> Historical design note. Superseded on 2026-07-12 by company runtime receipts, fallback-attempt history, Hook start enforcement, and `NEEDS_COMPANY_RESULTS` close behavior.

Date: 2026-07-11

## Observation Run

The original observation used three independent child agents during design
validation:

| Role | Model | Effort | Result used |
| --- | --- | --- | --- |
| strategy | `gpt-5.6-sol` | `xhigh` | strategy should be conditional and reserved for real direction choices |
| business/product | `gpt-5.6-sol` | `medium` | main thread must remain the only ticket writer and reducer |
| technical | `gpt-5.6-terra` | `high` | plan generation is enforceable in Python, but runtime spawning cannot be honestly self-verified |

The child agents were advisory. They did not write plugin files, confirm a
roster, expand acceptance, or act as a gate. The main thread implemented and
verified the final change.

## Product Decision After Observation

The initial fixed-four roster and numeric cap were rejected as too rigid. The
current policy uses:

- task-driven automatic selection of zero to four agents;
- independent task-depth model routing and task-breadth department routing;
- an exact department override with no protocol-level numeric cap;
- conservative CEO confirmation above four, defaulting to `KEEP_CURRENT`;
- complete responsibility/input/deliverable/acceptance/consumer contracts;
- roster-contract fingerprint invalidation when any authority or route changes;
- root-only optional Ultra eligibility, never automatic department Ultra;
- no nested companies and no inter-role chat loops.

Large confirmed rosters run in bounded waves. Wave size limits simultaneous
runtime pressure but does not reduce the promised one-agent-per-department total.

## Known Boundary

The Goal Compass file contract reports the required runtime roster but keeps
`runtime_execution_verified=false`. Actual spawn evidence belongs to Codex
runtime/tooling and is not fabricated by the harness.

## Verification Result

The command block below was the historical result of the first observation and
is not the current release result. Current release results are produced by the
verification suite during packaging.

```text
python3 -m unittest -q verification.tests.test_goal_compass
Ran 146 tests in 17.958s
OK

python3 -m unittest discover -s verification/tests -v
Ran 146 tests in 17.695s
OK

python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
Goal Compass selftest OK
```
