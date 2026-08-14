# Codex Goal Supervisor In This Project

Codex Goal Supervisor runs as a low-cost background observer after this project explicitly installs it. Explicit activation requires a structured project North Star and the matching Codex Goal mode; normal reads, edits, tests, and delivery do not require a ticket.

The North Star and Goal-mode objective are different layers. The North Star is the concise durable direction. Goal mode contains a 2,000-3,500 character executable contract with modules, concrete actions, serial/parallel relationships, dependencies, outputs, goal contribution, and acceptance. Super-complex work also uses a project-relative plan over 4,000 characters; Goal mode keeps a compressed contract and references the full plan rather than replacing its content with a path.

Each implementation action must have verification proportional to its risk. Use focused evidence for local changes instead of repeatedly running the full suite. Before claiming the entire North Star complete, run `convergence --certify-goal --final-validation-id <catalog-id>` with project-level end-to-end regression ids. Missing or failed regression cannot certify completion; only `CERTIFIED_COMPLETE` can.

Background behavior remains quiet: product writes create bounded verification debt, successful observed validation clears it, and only an explicit completion claim at `Stop` exposes an open debt. A validation start without a `PostToolUse` success remains unverified. Goal Compass state under `.agent/**` and `.codex/**` is excluded from this debt.

Alignment uses both layers: North Star checks durable direction, while the Goal contract checks the concrete module, dependency, output, exit criterion, and acceptance being advanced. Explicit anti-goals and Goal non-goals are tracked separately. Missing an obvious positive module mapping is not a hard failure; it remains an advisory because prerequisites and bounded exploration can still be legitimate.

Before the Agent writes or materially rewrites a super-complex plan, it researches available tools/projects and technical articles. The plan contains the resulting route, not the research log. If a reusable tool is found, the Agent asks in conversation whether to reuse it and whether the project is commercial; only after the answer does it finalize the plan and Goal-mode contract.

For `goal-set --require-detailed`, each process node includes inputs, actions, outputs, dependencies, exit criteria, `execution_mode` (`SERIAL`, `PARALLEL`, or `CONDITIONAL`), `contribution_to_goal`, and an hour-level target; downstream consumers are derived from dependencies or may be explicit. Parallel nodes also include `parallel_group`. Optional `affected_paths`, `affected_modules`, and `subnodes` support user-requested detail without making decomposition ceremony mandatory. A super-complex definition additionally records `complexity_level: SUPER_COMPLEX`, `execution_plan_ref`, an authored `goal_mode_summary`, and lightweight `planning_research` state. That state records only that tool/article research and visible user consultation happened; the final plan should not contain the research log.

Every newly authored or materially rewritten detailed Goal therefore has a complete high-level technical route before implementation. The `roadmap` command serves a read-only visualization on `127.0.0.1`; `roadmap --snapshot` returns its JSON projection and `roadmap --stop` closes it. The page refreshes from North Star and convergence state and does not create another project truth source. Ordinary tasks without an explicitly activated detailed Goal do not start it.

## Implicit Layer

- Silent for ordinary execution.
- One compact warning for repeated failures or a broad write surface.
- Exact project-authored anti-goal/drift incidents stay open: confirmations one and two warn; before confirmation three can enforce a scoped rail, a sparse read-only LLM Judge must confirm it at high confidence.
- Hard deny also remains for destructive `git reset`/`git clean`, direct Goal Supervisor control-state edits, and exact active-ticket forbidden/immutable paths.
- Hook failure is fail-open.

Observer state is compact metadata in `.agent/runtime/observer_state.json`; source contents are not stored there. Lock contention falls back to a recoverable bounded queue, and only the latest 128 diagnostic events (up to 64 KiB) are retained.

Large historical-code or document reads use local context continuity without
LLM assistance or proactive prompt injection. `.agent/runtime/context/index.json`
contains only totals and directory pointers; each relevant project directory is
split into `by-directory/<project-path>/_context.json` with file fingerprints.
The execution Agent may inspect `status --verbose` and load only the needed
directory. When independent reading slices justify it, the Agent may use
read-only subagents and merge their structured summaries itself. During a
large read, the main thread should publish a concise conclusion after each
meaningful directory/evidence slice and persist reusable facts with
`context-note`; source text and hidden reasoning are never stored.
When already available, Serena may supply symbol/reference reads and FastCtx
may supply paged reads; Goal Supervisor never installs either dependency. The
fallback is bounded ranges through normal client tools. Source file size alone
does not trigger the large-read path, tiny searches do not accumulate as broad
reads, and subagents are recommended only when at least two independent slices
exist. A saved directory conclusion becomes `STALE` when its evidence changes.

The same deviation is rechecked after 30 minutes of continued affected-path work. Unrelated success does not clear it. Judge results are structured and cached; unavailable, malformed, or timed-out judgment fails open. `deviation-correct` opens a scoped repair lane; `deviation-corrected` starts seven days of active recurrence monitoring. A recurrence restores the rail immediately. Counts clear only after explicit correction, seven clean days, and real project activity.

## Explicit Optional Layer

Call only when the expected saved rework is concrete:

- `request --text ...`: optional Custodian analysis.
- company roles: optional specialist deliverables; zero roles is valid.
- `check`: optional Auditor snapshot.
- `prune-check` / `prune-plan`: optional MARK_ONLY Janitor review; no move or delete.
- `compile / ready / start / close`: optional bounded ticket and machine certification.
- `convergence`: explicit L0-L3 goal stack, evidence progress, high-cost iteration records, recovery checkpoint, and optional semantic judgment.

Empty acceptance cannot start or PASS. Failed `close` returns `NOT_CERTIFIED` and leaves the ACTIVE ticket available for repair. Missing optional company-role results do not block ordinary work or certification.

`status` and `onboard-scan` are summaries by default. Use `status --verbose` to see the local context-continuity index; use other verbose output only when full diagnostic or inventory detail is needed. `onboard-scan` always writes the full report files. An unconfirmed North Star returns `NEEDS_CONFIRMATION` with `confirm_north_star`, not a failed command.

## Privacy

Feedback is local-only by default. Upload requires explicit project-level consent; an endpoint alone cannot enable it. Network failure never blocks product work.

## Supreme Rule

Every action taken by the model and every supervisory intervention by this plugin must produce net execution benefit. Any action that may affect other modules without constraint or become noise for the entire project must be managed. If the cost of a control by this plugin exceeds the rework it can prevent, that control must remain inactive.
