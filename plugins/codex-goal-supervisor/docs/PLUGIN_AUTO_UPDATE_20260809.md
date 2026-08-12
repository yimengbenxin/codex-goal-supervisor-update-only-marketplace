# Codex Goal Supervisor Auto Update

## Decision

Use Codex's native Git marketplace as the distribution and installation layer.
The plugin adds only a thin device-level scheduler around these official CLI
operations:

```text
codex plugin marketplace upgrade goal-supervisor
codex plugin list --json
codex plugin add codex-goal-supervisor@goal-supervisor --json
```

There is no custom ZIP downloader and no in-place replacement of a loaded
plugin. Codex installs each release in a versioned cache. Existing sessions keep
their loaded version; new sessions use the installed release.

Operating-system scheduled runs use `--scheduled`, so the daily cadence is not
suppressed by a recent manual or installation-time check. Ad-hoc checks still
retain the configured interval guard.

## External Basis

- Codex's plugin CLI supports Git marketplace URLs, marketplace refresh, JSON
  inventory, and versioned plugin installation.
- Apple LaunchAgents support per-user property-list jobs and calendar triggers.
- Windows Task Scheduler supports daily per-user executable tasks.
- The default channels are public read-only GitHub repositories. The `full` and
  `update-only` editions use different marketplace repositories and declared
  marketplace names. Edition verification refuses cross-channel installation.

## Client Contract

- One-time configuration only; no project hooks invoke the updater.
- HTTPS marketplace URLs only.
- One low-priority daily scheduled trigger; ad-hoc invocations retain the
  configured successful-check interval guard.
- One process at a time through an exclusive lock.
- Every Codex/Git operation has a hard timeout and consumes stdout/stderr.
- A lower or unrecognized remote version is never installed automatically.
- The returned cache path and plugin manifest must match the expected name and
  version before success is recorded.
- The plugin is installed through Codex's versioned cache rather than modified
  in place. A downgrade is never applied automatically.
- Update failure keeps the current version and never blocks project execution.

## Distribution Contract

Every completed local version must be published through the verified release
command before it is considered delivered:

```bash
python3 scripts/publish_verified_release.py
```

The command refuses an uncommitted checkout, runs source and extracted-package
verification, builds all three ZIP editions, pushes the canonical source and
the separate full/update-only marketplaces, creates the GitHub Release with a
SHA-256 manifest, and clones both remote marketplaces again to verify the
published version. Use `--dry-run` to exercise the complete build and
verification path without network writes. Do not publish on every file save:
only a committed, fully verified release may reach client update channels.

The default sources are:

```text
https://github.com/yimengbenxin/codex-goal-supervisor-marketplace.git
https://github.com/yimengbenxin/codex-goal-supervisor-update-only-marketplace.git
```

This edition uses its GitHub marketplace channel only. It contains no private feedback-server or asset-server endpoint.

Build clean edition-specific marketplace trees before publishing:Build clean edition-specific marketplace trees before publishing:

```bash
python3 scripts/build_plugin_release.py \
  --output /tmp/goal-supervisor-marketplace \
  --marketplace-edition full \
  --force

python3 scripts/build_plugin_release.py \
  --output /tmp/goal-supervisor-update-only-marketplace \
  --marketplace-edition update-only \
  --force
```

The builder excludes source-checkout `.agent/.codex` state, Python bytecode,
temporary files, and caches.

It produces three separate artifacts:

- a compact marketplace tree containing the complete Goal Supervisor runtime;
- a complete offline plugin ZIP containing runtime, docs, verification, server
  sources, and the bundled specialist role library;
- an immutable specialist-role ZIP used only when a marketplace-installed user
  explicitly invokes `agency_role_pack.py`.

The optional role archive is verified against the descriptor's exact byte size
and SHA-256 before bounded, traversal-safe extraction into the user-level Codex
cache. It is never fetched by background hooks or copied into a project.

## Verification

Unit tests cover version ordering, update/no-update/downgrade behavior,
intervals, concurrency locks, process-group timeouts, HTTPS enforcement, macOS
LaunchAgent data, Windows Task Scheduler commands, and marketplace metadata.
Release verification also uses an isolated `CODEX_HOME` and a real Git
marketplace to prove refresh and installation behavior.
