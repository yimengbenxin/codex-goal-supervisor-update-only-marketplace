# Codex Goal Supervisor - Update-only Marketplace

This repository is the official `update-only` Codex marketplace channel for [Codex Goal Supervisor](https://github.com/yimengbenxin/codex-goal-supervisor), an execution-convergence tool for long-running Codex work.

## Why This Project Exists

Coding Agents are entering the Loop Era: they can work for hours, traverse large repositories, delegate work, survive context compaction, and keep iterating beyond one conversation turn. The new bottleneck is not merely producing a good next action. It is keeping thousands of locally reasonable actions converging on one valuable outcome without turning supervision into a second source of delay.

Codex Goal Supervisor is built for that convergence problem. Its mission is to help long-running Agents preserve intent, distinguish activity from evidence-backed progress, recover from drift, and finish with verifiable results while keeping ordinary work free of mandatory ceremony.

Codex Goal Supervisor preserves a project North Star, maintains a separate executable Goal contract, distinguishes activity from evidence-backed progress, restores the active Goal after temporary requests or compaction, and offers optional Custodian, company-role, Auditor, Janitor, convergence, and bounded-ticket capabilities.

It is an advisory-first administrator, not a project decision maker. Ordinary work does not require tickets or role receipts. Janitor is MARK_ONLY and never deletes product files. Project use remains explicit opt-in.

## Edition Boundary

- Automatic updates: included and pinned to the `update-only` channel.
- Feedback: Remote feedback client, credential, upload, fetch, and server code are physically absent.
- Cross-edition replacement: refused by the updater.
- Project activation: never performed by an update check.

## Install

```bash
codex plugin marketplace add yimengbenxin/codex-goal-supervisor-update-only-marketplace --ref main
codex plugin add codex-goal-supervisor@goal-supervisor-update-only
```

For the complete Codex lifecycle map and adaptation boundaries, read the [architecture document](https://github.com/yimengbenxin/codex-goal-supervisor/blob/main/docs/ARCHITECTURE.md). For capabilities, project activation, privacy boundaries, verification evidence, and release ZIPs, use the [canonical repository](https://github.com/yimengbenxin/codex-goal-supervisor) and [latest release](https://github.com/yimengbenxin/codex-goal-supervisor/releases/latest).
