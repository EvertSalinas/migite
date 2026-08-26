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
  [--base-branch  master]
```

`--task-file` injects supplementary details from [migite's intake mode](./migite.md#intake-mode)'s "add a separate task.md" prompt into `synthesize_plan` as authoritative context alongside the intake — it's optional and only ever set when that prompt produced a file.

`--base-branch` sets the branch explorers diff against. Omitted, it auto-detects from `origin/HEAD`, then falls back to `main` / `master` / `develop`. `migite` passes its own detected value so both agree.

`--blueprint` injects the blueprint content into both `synthesize_plan` and `run_architecture_critic` as pre-decided architecture. The planner and critic treat blueprint decisions as settled constraints rather than re-deriving them from the codebase.

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

Exits 0 and touches `--sentinel` on success. Exits 1 on failure.
