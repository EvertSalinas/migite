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
`migite-plan`'s prompts; hard error if missing). It runs as a **schema-validated structured
call** (`claude --json-schema`, see `REVIEW_SCHEMA`): the model returns `{verdict, reason,
findings[], document}`, `document` becomes `review.md`, and the rest becomes `review.json`. If
the structured call fails or returns something malformed, it falls back to a plain text call and
derives the verdict from the document with the same anchored rule `helpers.sh`'s
`review_verdict()` uses (`source: "markdown"` in the envelope).

<a id="machine-readable"></a>
## Machine-readable envelopes and the usage ledger

Every headless model call goes through **`migite_claude.py`** (imported by `migite-plan` and
`migite-review`; reached from bash via `claude_print` in `helpers.sh`, which pipes the CLI's JSON
through `migite_claude.py extract`). It always runs `claude --print --output-format json`, so
each call yields the CLI envelope — `result`, `usage`, `total_cost_usd`, `duration_ms`, and with
`--json-schema` a validated `structured_output` — and appends one line to the run's ledger
(`$MIGITE_USAGE_LEDGER`, default `~/.dev-workflow/logs/<ts>-usage.jsonl`):

```json
{"ts": "...", "tool": "migite-plan", "label": "explore:models", "model": "claude-haiku-4-5-20251001",
 "input_tokens": 10, "output_tokens": 39, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 23624,
 "cost_usd": 0.0475, "duration_ms": 1407, "ok": true}
```

If the CLI doesn't return the envelope (older version, plain-text error) the wrapper passes stdout
through untouched with zero usage, so nothing downstream changes. Note the
`cache_creation_input_tokens`: every headless call re-sends Claude Code's own system context
(~23k tokens in testing), which is a large share of a run's cost and is why the summary prints it.

**`plan.json`** (beside `plan.md`, written by `migite-plan`):

| Field | Meaning |
|---|---|
| `critic.clean`, `critic.critical/warning/note` | Whether the architecture critic returned the clean signal, and its 🔴/🟡/🟢 counts |
| `open_questions` | Number of `### N.` entries under `## Open questions` |
| `plan_headings` | The plan's `## ` headings, in order |
| `synth_retries` | 0 or 1 — whether synthesis needed the stub retry |
| `refine_status` | `no_concerns` / `applied` / `applied_after_retry` / `kept_draft` |
| `explorers.count`, `explorers.failed[]` | How many explorers ran and which failed |
| `usage` | This tool's calls from the ledger, summed |

**`review.json`** (beside `review.md`, written by `migite-review`):

| Field | Meaning |
|---|---|
| `verdict` | `needs_fixes` / `ready` / `unknown` — **what the commit gate reads** (`review_verdict()` prefers this over parsing the markdown) |
| `verdict_label`, `reason` | The model's exact verdict string and its one-line justification |
| `findings[]` | `{severity, dimension, file, line, problem, fix}` from the structured call (empty in the markdown fallback) |
| `counts` | Per-severity totals (from `findings[]`, or 🔴/🟡/🟢 counts in the fallback) |
| `dimensions` | Per-reviewer 🔴/🟡/🟢 counts and a `failed` flag |
| `source` | `structured` or `markdown` |
| `usage` | This tool's calls from the ledger, summed |

Both envelopes carry `schema_version`, `tool`, `generated_at`, `base_branch`, and `outputs` paths.
Bash reads them with `json_field <file> <dotted.key>`; mirror them to the vault with `sync_json`
(never `sync_artifact`, whose frontmatter stamp would corrupt JSON).

**`usage.json`** is written by `print_usage_summary` (from `migite`'s EXIT trap, so aborted runs
report too) and summarises the ledger by model and by tool. Interactive sessions (`run_phase`:
implement, gate fixes, PR description) are not metered — the CLI only emits usage in `--print` mode.

Exits 0 and touches `--sentinel` on success. Exits 1 on failure.
