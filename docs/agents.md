# Agent backends

Migite drives an agent CLI; it is not tied to one. `agent.backend` in the config picks which:

| Backend | `agent.backend` | Executable | Status |
|---------|-----------------|------------|--------|
| Claude Code | `claude` (default) | `claude` | Reference backend. Every capability, verified live. |
| Cursor CLI | `cursor` | `cursor-agent` (alias `agent`) | Headless and interactive verified against `cursor-agent --help` and the Cursor docs; no live run yet in this checkout (needs `cursor-agent login`). |
| OpenCode | `opencode` | `opencode` | Built from the documented `run --format json` event shapes; not installed here, so untested live. |

```yaml
# ~/.config/migite/config.yml or <repo>/.migite.yml
agent:
  backend: cursor            # or: MIGITE_AGENT=cursor migite ...
  # command: /opt/cursor/bin/agent   # override the executable
```

## What migite needs from a backend, and what each one has

| Capability | Used for | Claude Code | Cursor | OpenCode |
|---|---|---|---|---|
| Headless call, text out | planner, reviewers, knowledge, amendments, standalone tools | `claude --print --output-format json`, prompt on stdin | `cursor-agent -p --output-format json --trust "prompt"` | `opencode run --format json "message"` |
| Interactive session with an initial prompt | implement, gate fixes, PR description | `claude -- "prompt"` | `cursor-agent "prompt"` | `opencode --prompt "prompt"` |
| Usage and cost in the ledger | `usage.json`, gate banner | tokens, cache, cost | none (zeros recorded) | cost and tokens summed from `step_finish` events |
| Structured output (schema-validated verdict) | `review.json` verdict | `--json-schema` | no → reviewer parses markdown | no → reviewer parses markdown |
| `--effort` | `models.effort` | yes | dropped | dropped |
| Scoped tool allowlist | the Jira fetch | `--allowedTools` | no → fetch skipped with a warning | no → fetch skipped with a warning |
| Permission modes | `permissions.*` | `--permission-mode` | `bypassPermissions`/`acceptEdits` → `--force`; `plan` → `--mode plan`; `--trust` always in headless | `bypassPermissions`/`acceptEdits` → `--auto` |
| Prompt transport | large plan/knowledge prompts | stdin | argument; over 100 KB a pointer to a temp file is passed instead | argument; same pointer rule |

Everything degrades explicitly, never silently: the reviewer announces it is using text
synthesis, the planner warns that the Jira fetch was skipped, the ledger records the call with
zero cost. `migite doctor` prints the active backend and checks its executable resolves.

## Models per backend

Model ids are backend-specific, so the three tiers have per-backend defaults:

| Tier | Claude Code | Cursor | OpenCode |
|---|---|---|---|
| `fast` | `claude-haiku-4-5-20251001` | unset | unset |
| `standard` | `claude-sonnet-5` | unset | unset |
| `strong` | `claude-opus-5-5` | unset | unset |

"Unset" means no `--model` flag is passed and the CLI uses its own configured default. Cursor
and OpenCode ids could not be verified without a login in this checkout, so nothing is guessed.
Pin them once you know them:

```yaml
agent:
  backend: cursor
models:
  fast: sonnet-4            # cursor-agent --list-models
  standard: sonnet-4-thinking
  strong: gpt-5
```

```yaml
agent:
  backend: opencode
models:
  standard: anthropic/claude-sonnet-4-5     # opencode models  → provider/model
  strong: anthropic/claude-opus-4-5
```

Roles (`think`, `critic`, `review_security`, ...) and `models.roles` pins work the same on every
backend. `models.effort` is honoured only on Claude Code.

## Switching for one run

```bash
MIGITE_AGENT=cursor migite --jira BB-1234
MIGITE_AGENT=opencode migite-audit --focus jobs
```

Env beats files, so this never touches your config. The usage ledger records the model each
backend reported, or the requested one, so runs on different backends can be compared in
`usage.json`.

## How it is built

`migite_agent.py` holds one class per backend with three methods: `headless_argv` (flags and
whether the prompt travels on stdin or as an argument), `parse` (that CLI's output into one
normalised result: text, structured output, ok, error, tokens, cost), and `interactive_argv`.
`migite_claude.call_claude` and the bash `claude_print` helper (a historical name; it is not
Claude-specific) pick the backend from the config and never see a flag. `run_phase` asks the
adapter for a shell-quoted interactive command line and runs it, in a tmux pane or inline.

Adding a backend is one class in `migite_agent.py`, one row in `BACKEND_MODEL_DEFAULTS` in
`migite_config.py`, a fake CLI under `tests/`, and a row in the tables above.

## Known limits

- Cursor and OpenCode have not been exercised live from this checkout. The adapters follow the
  documented flags and output shapes and are covered by unit tests with fake CLIs; the first
  real run on each should be watched, and `usage.json` will show whether usage parsed.
- Without structured output, the verdict comes from the markdown parser. It is anchored on the
  Verdict heading and tested against every past review, but a typed enum is stronger.
- Cursor headless mode without `--force` only proposes changes; migite maps
  `bypassPermissions` and `acceptEdits` to `--force`, so the heal loop needs one of those.
- The `implement.md` prompt still says `/exit` to end the session; on Cursor and OpenCode use
  that CLI's own exit command.
