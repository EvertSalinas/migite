# Internal LangGraph scripts (called by migite)

These are invoked by `migite` via `spawn_langgraph()`. They can also be tested standalone.

<a id="internal-migite-plan"></a>
### `migite-plan`

```bash
migite-plan \
  --intake              path/to/intake.md \
  --plan-output         path/to/plan.md \
  --critic-output       path/to/architecture-critic.md \
  --testing-plan-output path/to/testing-plan.md \
  --repo-root           /path/to/repo \
  --sentinel            path/to/.plan.done \
  [--task-type    feature] \
  [--knowledge    path/to/knowledge.md] \
  [--audit        path/to/audit.md] \
  [--blueprint    path/to/blueprint.md] \
  [--task-file    path/to/task.md] \
  [--jira-context path/to/jira-context.md] \
  [--base-branch  master]
```

`--task-file` injects supplementary details from [migite's intake mode](./migite.md#intake-mode)'s "add a separate task.md" prompt into `synthesize_plan` as authoritative context alongside the intake — it's optional and only ever set when that prompt produced a file.

`--jira-context` is a pre-fetched Jira ticket summary (title, type, priority, status, description, acceptance criteria) — fetched in `plan.sh` via the Atlassian MCP whenever `--jira` is used, cached to the scratchpad, and injected into both `synthesize_plan` (as authoritative scope/acceptance-criteria context) and the 7 explorers' keyword extraction. `migite-plan` itself never calls the MCP — it only reads whatever file this flag points to, same as `--audit`/`--blueprint`/`--task-file`.

`--base-branch` sets the branch explorers diff against. Omitted, it auto-detects from `origin/HEAD`, then falls back to `main` / `master` / `develop`. `migite` passes its own detected value so both agree.

`--blueprint` injects the blueprint content into both `synthesize_plan` and `run_architecture_critic` as pre-decided architecture. The planner and critic treat blueprint decisions as settled constraints rather than re-deriving them from the codebase.

Reads `prompts/plan.md` (plan format + type routing, injected into `synthesize_plan`) and
`prompts/architecture_critic.md` (the critic checklist) from the directory the script really
lives in (`Path(__file__).resolve().parent`, so the `~/.local/bin` symlink is followed). Either
file missing is a hard exit-1 — never a silent empty prompt.

Exits 0 and touches `--sentinel` on success. Exits 1 on failure (no sentinel written).

<a id="internal-migite-review"></a>
### `migite-review`

```bash
migite-review \
  --plan           path/to/plan.md \
  --implementation path/to/implementation.md \
  --rubocop-log    path/to/rubocop.txt \
  --rspec-log      path/to/rspec.txt \
  --repo-root      /path/to/repo \
  --review-output  path/to/review.md \
  --sentinel       path/to/.review.done \
  [--base-branch   master] \
  [--testing-plan  path/to/testing-plan.md]
```

`--base-branch` defaults to auto-detect (`origin/HEAD`, then `main` / `master` / `develop`) when omitted, same as `migite-plan`. `--testing-plan` is optional — when given, its full content (not the truncated plan excerpt every other dimension sees) is what the `testing_plan` reviewer dimension grades.

**Models used:** `claude-sonnet-5` for the 4 parallel review dimensions, `claude-opus-5` for `synthesize_verdict`.

`synthesize_verdict` is given `prompts/review.md` as the output format (resolved the same way as
`migite-plan`'s prompts; hard error if missing). That format puts `## Verdict: READY TO COMMIT` or
`## Verdict: NEEDS FIXES` on one line directly under the title, which is what `review_verdict()`
in `helpers.sh` parses for the commit-gate banner.

Exits 0 and touches `--sentinel` on success. Exits 1 on failure.
