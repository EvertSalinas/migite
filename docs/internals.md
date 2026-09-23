# Internals

Repository layout, the two agent scripts `migite` calls directly, and the machine-readable
envelopes. For the phase-by-phase behaviour see [migite.md](./migite.md).

## Contents

- [Repository layout](#layout)
- [How the bash spine and the agents talk](#spine)
- [`migite-plan`](#internal-migite-plan)
- [`migite-review`](#internal-migite-review)
- [Machine-readable envelopes and the usage ledger](#machine-readable)

<a id="layout"></a>
## Repository layout

```
migite/                       ← wherever you clone this repo
├── migite                    ← entrypoint (bash): config load, arg parsing, phase sequencing, EXIT trap
├── migite.d/                 ← phase fragments sourced by migite, sharing its variables
│   ├── helpers.sh            ← output, agent_ask/agent_think/run_phase, changed-file helpers, stack profiles, sync, gate banner
│   ├── config.sh             ← load_migite_config, load_agent_info, cfg / prompt_path, use_tmux, `migite config`
│   ├── doctor.sh             ← `migite doctor`
│   ├── amend.sh              ← --amend mode
│   ├── plan.sh               ← Phase 1 (+ Jira fetch, plan gate) and Phase 1.5
│   ├── implement.sh          ← Phase 2 (single or --staged) and the Phase 2.5 heal loop
│   ├── review.sh             ← Phase 3, the commit gate, strict-policy blockers
│   └── deliver.sh            ← Phases 3.5, 4, 4.5
├── migite-plan               ← LangGraph planner (Python), called by Phase 1
├── migite-review             ← LangGraph reviewer (Python), called by Phase 3
├── migite-explore(.py)       ← standalone: initiative feasibility (bash wrapper + agent)
├── migite-blueprint(.py)     ← standalone: new-project definition
├── migite-audit(.py)         ← standalone: codebase audit
├── migite-pr-review(.py)     ← standalone: review a branch
├── agents/                   ← one adapter per agent CLI: the ONLY place a CLI's flags, output format, or model ids appear
│   ├── base.py               ← the interface: AgentInfo, AskRequest, SessionRequest, Launch, AskResult, permission words, scopes
│   ├── claude.py             ← Claude Code
│   ├── cursor.py             ← Cursor CLI
│   └── opencode.py           ← OpenCode
├── migite_call.py            ← the gateway: role → model and effort, capability fallbacks, prompt pointer, process, AgentError, usage ledger
├── migite_agent.py           ← bash's door to the gateway: ask, session, info, check
├── migite_claude.py          ← compatibility shim: the old name of migite_call.py
├── migite_config.py          ← layered config resolver; role → tier → the agent's model
├── migite_paths.py           ← vault path resolver: org detection, base branch, slugify, run-dir lookup
├── prompts/                  ← plan.md, implement.md, review.md, architecture_critic.md (overridable via prompts.dir)
├── templates/                ← intake templates per --type, and commit.md (the PR-description prompt)
├── tests/                    ← run.sh (bash suite), test_*.py (unittest, incl. the adapter contract), fake CLIs per agent, fixtures/
├── docs/                     ← this directory
├── .github/workflows/ci.yml  ← Python tests, bash suite, shellcheck
└── migite-improvements.md    ← the self-improvement log, appended by Phase 4.5
```

`~/.local/bin/` holds symlinks to every executable and to the three `migite_*.py` modules (the
agents import them from the directory they are launched from). `migite.d/`, `prompts/`, and
`templates/` are not symlinked: `migite` resolves its real path through the symlink and finds
them beside itself.

<a id="spine"></a>
## How the bash spine and the agents talk

- **Arguments in, files out.** `migite` passes paths (intake, outputs, sentinel, logs) and a few
  scalars (base branch, stack, task type) to each agent on the command line. The agent writes its
  documents and its JSON envelope, then touches the sentinel. Bash treats a missing sentinel as
  failure rather than trusting exit codes through a tmux pane.
- **Config is loaded on both sides.** Bash evals `migite_config.py env`; each agent calls
  `migite_config.load(repo_root)` itself. Both see the same layered result, so the agents behave
  identically when run by hand.
- **One model-call path.** Bash's `agent_ask` and the agents' `call_agent` wrappers both end
  in `migite_call.py`, which asks the configured agent's adapter for machine-readable output and appends to the usage ledger
  named by `$MIGITE_USAGE_LEDGER`. tmux panes inherit the tmux server's environment, so the
  wrapper scripts re-export that variable.
- **Gemfile one level down.** `migite` always `cd`s to the repo root, but Bundler only searches
  upward for a `Gemfile`. `detect_stack` finds an app directory one level down and every
  `bundle_exec` runs from it, with `strip_app_prefix` rewriting the repo-relative paths `git diff`
  produces. `resolve_path` absolutizes user-supplied file arguments before the `cd`.

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
lives in (`Path(__file__).resolve().parent`, so the `~/.local/bin` symlink is followed), unless
`prompts.dir` in the config overrides one. Either file missing is a hard exit-1 — never a silent
empty prompt. Models come from the config roles `explore` / `think` / `critic`
([docs/configuration.md](./configuration.md#models)).

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

**Models used (defaults):** one config role per dimension — `review_correctness` and
`review_security` on the strong tier (`claude-opus-5-5`), `review_test_coverage` and
`review_testing_plan` on the standard tier (`claude-sonnet-5`) — and `verdict` (strong) for
`synthesize_verdict`. See [docs/configuration.md](./configuration.md#models).

`synthesize_verdict` is given `prompts/review.md` as the output format (resolved the same way as
`migite-plan`'s prompts; hard error if missing). It runs as a **schema-validated structured
call** (`claude --json-schema`, see `REVIEW_SCHEMA`): the model returns `{verdict, reason,
findings[], document}`, `document` becomes `review.md`, and the rest becomes `review.json`. If
the structured call fails or returns something malformed, it falls back to a plain text call and
derives the verdict from the document with the same anchored rule `helpers.sh`'s
`review_verdict()` uses (`source: "markdown"` in the envelope).

<a id="machine-readable"></a>
## Machine-readable envelopes and the usage ledger

Every headless model call goes through **`call_agent` in `migite_call.py`** (imported by
`migite-plan`, `migite-review`, and the standalone tools; reached from bash via `agent_ask` in
`helpers.sh`, which pipes the prompt through `migite_agent.py ask`). It asks the configured
agent's adapter in `agents/` for a command line and parses that CLI's output into one shape. On Claude Code that is
the `--output-format json` envelope: `result`, `usage`, `total_cost_usd`, `duration_ms`, and with
`--json-schema` a validated `structured_output`. Cursor and OpenCode report what they can; see
[agents.md](./agents.md). Each call appends one line to the run's ledger
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

A `plan.json` from a run whose critic found one warning and whose refine went through cleanly:

```json
{
  "schema_version": 1,
  "tool": "migite-plan",
  "generated_at": "2026-09-22T14:21:33+00:00",
  "base_branch": "main",
  "stack": "rails",
  "task_type": "feature",
  "critic": { "clean": false, "critical": 0, "warning": 1, "note": 1 },
  "open_questions": 2,
  "plan_headings": ["## Summary", "## Scope", "## Approach", "## Test plan", "## Performance considerations",
                    "## cURL examples", "## Risks", "## Out of scope", "## Open questions"],
  "synth_retries": 0,
  "refine_status": "applied",
  "explorers": { "count": 7, "failed": [] },
  "outputs": { "plan": ".../scratchpad/bb-1234/plan.md", "critic": ".../architecture-critic.md", "testing_plan": ".../testing-plan.md" },
  "usage": { "calls": 11, "failed": 0, "cost_usd": 2.31, "duration_ms": 254100,
             "input_tokens": 1820, "output_tokens": 12904, "cache_read_input_tokens": 236240, "cache_creation_input_tokens": 23624 }
}
```

Reading one field from bash and from Python:

```bash
json_field scratchpad/bb-1234/review.json verdict            # → needs_fixes
json_field scratchpad/bb-1234/review.json counts.critical    # → 1
json_field scratchpad/bb-1234/plan.json critic.clean         # → false
```

```python
import json
review = json.load(open("scratchpad/bb-1234/review.json"))
blocking = [f for f in review["findings"] if f["severity"] == "critical"]
```

A `review.json` example, including `findings[]`, is in
[getting-started.md](./getting-started.md#outputs).

**`usage.json`** is written by `print_usage_summary` (from `migite`'s EXIT trap, so aborted runs
report too) and summarises the ledger by model and by tool. Interactive sessions (`run_phase`:
implement, gate fixes, PR description) are not metered — the CLI only emits usage in `--print` mode.

Exits 0 and touches `--sentinel` on success. Exits 1 on failure.
