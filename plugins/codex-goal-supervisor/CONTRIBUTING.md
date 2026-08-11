# Contributing

Codex Goal Supervisor is designed around one constraint: an intervention must
save more rework than the process cost it creates.

## Development

Use Python 3.9 or newer. The core runtime intentionally uses the standard
library so a plugin install does not add dependencies to the user's project.

Run the focused verification suite before opening a pull request:

```bash
python3 -m py_compile assets/governor-harness/.agent/goal_compass.py scripts/install_governor.py scripts/goal_hook.py verification/tests/*.py
python3 -m unittest -q verification.tests.test_goal_compass
python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
```

Please include a regression test for behavior changes. Keep project-local
runtime state, feedback exports, credentials, and machine-specific paths out of
commits.

## Scope

Good contributions improve long-task alignment, convergence, recovery, or
signal accuracy while keeping ordinary work quiet. New gates, roles, reports,
or commands need evidence that they reduce total execution cost.

