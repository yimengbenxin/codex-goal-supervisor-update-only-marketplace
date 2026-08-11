# Agency Agents Specialist Role Library

This directory is a pinned, optional expert-reference pack for Codex Goal
Supervisor. It is not a decision authority and is never installed into a user
project.

- Upstream: <https://github.com/msitarzewski/agency-agents>
- Pinned commit: `ebe9c99acb5c96f9468de368d8bead775387d1a7`
- License: MIT; see `LICENSE`
- Integrity and role metadata: `manifest.json`
- Exact upstream prompts: `roles/**`

The raw role prompts are stored without truncation or semantic rewriting. The
main execution thread decides whether to use a profile unchanged, combine it
with a Goal Supervisor department contract, or ignore it. Selecting a profile
does not change the North Star, Goal, ticket acceptance, paths, or department
roster.

Use `python3 scripts/agency_role_pack.py` from the plugin root to list, search,
read, or verify the pack. Updating the snapshot is an explicit maintenance
operation through `scripts/build_agency_role_pack.py`; runtime use never pulls
unreviewed upstream changes.
