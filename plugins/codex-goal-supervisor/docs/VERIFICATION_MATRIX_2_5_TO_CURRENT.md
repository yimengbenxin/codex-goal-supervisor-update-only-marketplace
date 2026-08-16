# Business Verification Matrix: 2.5 To Current Candidate

Candidate under test: `2.8.6+codex.20260816111230`

Release publication is frozen. A row is complete only when its required
deterministic, distribution, and real-thread evidence has passed. Existing test
counts and earlier black-box runs are context, not proof for this campaign.

## Evidence Levels

- `deterministic`: source or extracted-package command verifies a bounded rule.
- `distribution`: the built edition physically contains or omits the claimed
  capability; a disabled flag is not sufficient.
- `real-thread`: the fixed Luna Max task actually loads the candidate Skill and
  uses the capability in a fresh isolated product scenario.

## Capability Matrix

| Version | Capability | Required evidence | Real business scenario | Status |
|---|---|---|---|---|
| 2.5 | Verified publisher ordering | deterministic + distribution | Dirty trees, missing or stale black-box attestations, and archive failures must stop before any network write; a valid `--dry-run` must build and verify without publishing. | PASS |
| 2.5 | Three physical editions | distribution | Offline contains no updater, feedback transport, or publisher; update-only contains updater but no feedback transport/publisher; full contains all optional maintainer capabilities with upload still opt-in. | PASS |
| 2.5 | Durable North Star change confirmation | deterministic + real-thread | A question mentioning a possible North Star change must not count as confirmation; an explicit durable change must survive continuation and compaction. | PASS |
| 2.6 | Goal-authored reuse research | deterministic + real-thread | A detailed Goal records actual tool/article research, rejects unsuitable reuse, asks the user when a direct candidate needs reuse/commercial confirmation, and places accepted reuse into the execution route. | PASS |
| 2.6 | 24-hour reuse refresh | deterministic + real-thread | Continuations inside the window do not repeat research; after 24 hours the probe uses the North Star, current Goal, and remaining actions and updates the route only when useful. | PASS |
| 2.6 | Real segment deadlines | deterministic + real-thread | The first product write starts one unambiguous dependency-ready segment, creates an absolute deadline, keeps short segments quiet, and emits only the bounded due reminder. | PASS |
| 2.6 | Optional heavy tools remain optional | deterministic + real-thread | Ordinary work does not require tickets, company roles, Custodian, Auditor, or Janitor; an explicit call still works. | PASS |
| 2.6.1 | External-blocker recovery | deterministic + real-thread | A device/login/manual prerequisite selects a dependency-ready independent module and requires a product action; a genuine global blocker stops without inventing work. | PASS |
| 2.7 | Live technical route | deterministic + real-thread | Detailed Goal creation starts a loopback-only read-only dashboard; live node state, dependencies, inputs, outputs, consumers, optional detail, snapshot, and stop all work. | PASS |
| 2.7 | Roadmap fail-open boundary | deterministic + real-thread | Dashboard failure never blocks product work and ordinary one-off tasks do not start a route server. | PASS |
| 2.8 | Project procedure memory | deterministic + real-thread | A successful local service creates an idempotent project Skill/runner; other command sequences require two independent successful tasks. | PASS |
| 2.8 | Procedure noise/privacy boundary | deterministic + distribution + real-thread | Reads, failed commands, sensitive values, temporary paths, arbitrary shell and destructive operations are not persisted or injected; feedback remains local-only. | PASS |
| 2.8.1 | Canonical native Goal synchronization | deterministic + real-thread | Research and detailed Goal finalize first; `create_goal` receives the exact `goal_mode_objective`; `get_goal` matches its length, bytes and SHA-256. An early short Goal is rejected as unsynchronized. | PASS |
| 2.8.2 | Black-box-before-release contract | deterministic + distribution | A real Luna Max attestation is bound to the exact clean commit/version and independent product pass; missing, stale, mismatched, early-Goal, or no-research evidence prevents publication. | PASS |
| 2.8.3 | Structured phased Goal runtime | deterministic + real-thread | A concise North Star anchors a shallow program outline; only the dependency-ready 2-24 hour phase is detailed and projected into native Goal mode; phase completion validates before advance. | SOURCE PASS / REAL-THREAD PENDING |
| 2.8.4 | Directly usable phase input contract | deterministic + real-thread | The installed Skill and project README expose one canonical input shape plus bounded aliases, so an execution agent can author and submit a phase without reading verification tests or schema-guess retries. | SOURCE PASS / REAL-THREAD PENDING |
| 2.8.5 | Full definition and native Goal projection separation | deterministic + real-thread | A complete structured phase may provide a separately authored 2,000-3,500 character native Goal projection; the full definition is never truncated and the projection cannot conceal missing structure. | SOURCE PASS / REAL-THREAD PENDING |
| 2.8.5 | Roadmap token argument stability | deterministic | A generated URL-safe shutdown token beginning with `-` still starts the loopback dashboard and cannot be parsed as a new CLI option. | PASS |
| 2.8.6 | Installed lightweight phase telemetry | deterministic + real-thread | A structured phase without an ACTIVE ticket records the first real product write and successful validation through the installed lightweight project hook; successful `phase-complete` also records its authoritative catalog evidence without fabricating a product action. | SOURCE PASS / REAL-THREAD PENDING |
| 2.8.6 | Completed program-phase dependency projection | deterministic + real-thread | A current phase node may depend on a completed program phase ID; completed phase dependencies are satisfied while unknown non-node dependencies remain blocked. | SOURCE PASS / REAL-THREAD PENDING |
| 2.8.6 | Plugin template isolation under an observed source checkout | deterministic | Events inside `assets/governor-harness` never fall back to the observed plugin repository parent or receive project reminders. | PASS |
| current | Existing core regressions | deterministic + distribution | Goal detection, advisory mode, deviation recurrence, validation states, Janitor mark-only behavior, feedback local default, updater boundary, concurrency and status performance remain green. | PASS |

## Deterministic Commands

```bash
python3 -m py_compile assets/governor-harness/.agent/goal_compass.py scripts/install_governor.py scripts/goal_hook.py scripts/build_plugin_release.py scripts/publish_verified_release.py verification/tests/*.py
python3 -m unittest -q verification.tests.test_goal_compass
python3 -m unittest discover -s verification/tests -v
python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
```

The full edition must then be extracted into a temporary directory and the same
module suite, discovery suite, and selftest must run from that extracted tree.

## Distribution Commands

```bash
python3 scripts/build_plugin_release.py --all-editions-dir <temporary-output>
python3 scripts/publish_verified_release.py --dry-run
```

Edition inspection must check physical files and private-server markers, not
runtime feature flags alone. The dry run must perform no network write.

## Fixed Real-Thread Contract

- Task ID: `01a00666-8821-7461-a9ab-113205b3bdd0`
- Use the task's Luna Max configuration; do not rename or replace the task.
- Each scenario uses a fresh isolated project and the exact current candidate.
- The task must load the candidate Skill, establish the detailed Goal before
  native Goal creation, perform real online research where required, and execute
  product commands rather than inspect `verification/tests`.
- It must not modify Goal Supervisor source, publish a release, or upload
  feedback.
- The main task independently inspects artifacts and reruns acceptance before a
  row changes to `PASS`.

## Exit Rule

All rows must be `PASS`, every confirmed defect must have a regression test, and
the final source/extracted/edition/real-thread rerun must be green before the
candidate can be called stable. Publication remains a later, separate decision.

## Signed Evidence

- Current 2.8.5 source candidate: module `530 tests / 90.182s`, discovery
  `530 tests / 93.651s`, selftest `OK`; `py_compile` also passed.
- The complete discovery suite exposed an intermittent roadmap startup failure.
  Root cause was a generated shutdown token beginning with `-` being passed as a
  separate argparse value. The fix uses `--token=<value>` and a deterministic
  leading-hyphen regression test.
- Source suite after the final hook repairs: `517 tests`, `92.535s`, `OK`.
- Extracted full-edition suite before the two isolated hook repairs: module
  `515 tests / 90.416s`, discovery `515 tests / 88.944s`, selftest `OK`.
  The final extracted rerun remains a release-exit check after the phased Goal
  implementation is complete.
- Three physical edition SHA-256 values from the pre-phase build:
  - offline: `c9456856acd3d260896afebcec1e962b99b6454e3dc9506386660a6dc4042ce8`
  - update-only: `6dba450d117b09fd95a21bc5fd0c219b913e59767d1404efc1e23c02d439d534`
  - full: `9778592edbef56013a82315e9ff7c8b549948dbe9985678a6fed684e77338391`
- Publisher dry-run in an isolated clean repository returned
  `VERIFIED_NOT_PUBLISHED` after source, extracted archive, selftest, edition,
  and checksum validation. No network write occurred.
- Fixed Luna Max task `01a00666-8821-7461-a9ab-113205b3bdd0` completed the
  packaging product black box and independently passed the repaired durable
  direction hook plus generated procedure lifecycle.
- Main-task read-back of `audit/hook-sequence-repaired.json` confirmed four
  zero-exit hook events, one confirmation prompt, duplicate suppression,
  explicit resolution, and unchanged North Star SHA-256
  `faa33b74f8e3b365c3718ed00708769e6977d901addf328251d052082d327b4d`.
