# CLAUDE.md

Project rules for working on migite itself. Generic preferences (commit format, code style,
security, testing) live in `~/.claude/CLAUDE.md`; this file adds only what is specific to this
repo. Docs are the source of truth for behaviour: start with `docs/internals.md` (layout, how
bash and the Python agents talk) and `docs/phases.md` (what each phase does).

## What this is

A bash spine (`bin/`, `lib/`) that drives an agent CLI through plan, gate, implement, heal,
review, gate, PR description, knowledge capture. Python (`migite/`) holds the config resolver,
the agent gateway, and the LangGraph tools. Every model call shells out to the configured agent
CLI; there is no API key and no SDK.

## Hard boundaries

- **Agent CLI flags, output formats and model ids appear only in `migite/agents/<name>.py`.**
  Bash never builds a CLI command; it calls `python -m migite.agent_cli` (`ask`, `session`,
  `info`, `check`). The gateway (`migite/gateway.py`) speaks in roles, labels, permission words
  and scopes, never in flags.
- **A capability a backend lacks degrades explicitly, never silently.** Print one notice and fall
  back (drop the schema, drop the effort, record zero usage). Refuse only when running without
  the capability would be unsafe (an unmapped tool scope).
- **Migite never runs `git commit`.** The commit gate's `y` ends the review loop; the commit is
  the user's.
- **Every config default equals the behaviour without a config file.** A new key goes in
  `DEFAULTS` in `migite/config.py`, with validation, in `docs/configuration.md`, and as a
  commented line in the sample `.migite.yml`. Model call sites are roles in `ROLE_TIERS`; never
  hardcode a model id outside `migite/agents/`.
- **Bound what you embed in a prompt.** A log, diff or document goes into a prompt through a
  capped excerpt (`truncate_log`, `prompt_diff`, `knowledge.recent`), never whole. The
  gateway's file pointer guards argv size only, not the context window.
- **A failed headless call degrades the run, it does not kill it.** `bin/migite` runs under
  `set -e`; wrap `agent_think` / `agent_ask` so a CLI error leaves a warning and the next phase
  still runs. Phase 3 always re-runs lint and tests itself, whatever the heal loop reported.

## Design principles

- **Narrow core, capability at the edges.** Prefer changing a prompt in `prompts/` or a template
  in `templates/` over adding bash to `lib/**/*.sh` when the result is reachable that way.
- **Doc-drift checklist.** Before calling a rename, move or new flag done, grep `README.md` and
  `docs/**/*.md` for the old name and update every hit. Nothing enforces this; it is a habit.
- **Read-only fan-out, single-threaded writes.** Explorers and reviewers run in parallel with
  read tools; implement stays one interactive session. Keep that split.

## Python

- Minimum 3.11. Resolve the interpreter through `MIGITE_PYTHON`; never `/usr/bin/python3`
  (3.9 on macOS, fails migite's own version check and tells you nothing).
- Locally: `~/.asdf/installs/python/3.13.5/bin/python3` has PyYAML and langgraph.
- CI installs PyYAML only, no langgraph. Anything that reaches `spawn_langgraph` must still
  import cleanly without it. To reproduce CI, use a fresh 3.11+ venv without langgraph.
- PyYAML is required whenever a `.yml` config exists; `~/.config/migite/config.yml` does, so a
  13-check failure cluster around `agent_ask` / `print_usage_summary` means the wrong Python,
  not a regression.

## Test gate

Run all of it before saying a change is done. This mirrors `.github/workflows/ci.yml`.

```bash
export MIGITE_PYTHON=~/.asdf/installs/python/3.13.5/bin/python3
bash tests/run.sh
"$MIGITE_PYTHON" -m unittest discover -s tests -p 'test_*.py'
"$MIGITE_PYTHON" -m migite.paths --self-test
shellcheck -S error -s bash bin/* lib/*.sh lib/phases/*.sh tests/run.sh tests/*_test.sh \
  tests/fake-claude tests/fake-cursor-agent tests/fake-opencode tests/fake-kimi
```

- Bash tests are `tests/*_test.sh`, discovered by `tests/run.sh`, each calling `check` per
  assertion, sourcing `lib/*.sh` standalone. A change to `lib/**/*.sh` gets a check there.
- Python tests are `tests/test_*.py` with `unittest`. A change to `migite/**` gets a test there.
  `tests/test_agents_contract.py` runs every adapter against the same contract; a new backend
  capability is added to the contract, not to one adapter's test.
- No test calls a real agent or a real tracker. Use `tests/fake-claude`, `fake-cursor-agent`,
  `fake-kimi`, `fake-opencode` and `fake-acli`; `tests/run.sh` already points `MIGITE_ACLI` at
  the fake.
- shellcheck warnings are informational in CI, errors block. Do not add disable comments
  without a reason in the comment. It is not installed on this machine by default:
  `brew install shellcheck`, or `pip install shellcheck-py` into a throwaway venv.

## Shell

- Scripts are bash 4+. Source `lib/*.sh` only inside `bash -c '...'`, never in the zsh tool
  shell: unmatched globs error under zsh and `set -e` behaves differently.
- In this user's shell `ls` is aliased to eza. List files with `find`, or `command ls`.
- Changed-file lists come from the shared helpers in `lib/stack.sh` (`changed_ruby_files`,
  `changed_spec_files`, `changed_all_files`): `git diff <base> --diff-filter=ACMR` unioned with
  untracked, non-ignored files. Do not write a new `git diff main` anywhere; the base branch is
  detected, not hardcoded.

## Live runs

A real `migite` run costs money ($4 to $6 in headless calls for a build) and needs the `claude`
CLI logged in. Run `migite doctor` first. Do not start a live run on a repo without asking;
unit tests with the fake CLIs cover the code paths. When a live run is wanted, point
`MIGITE_USAGE_LEDGER` at a file and keep the resulting `usage.json` as evidence.

## Git

- Commits are GPG-signed and the agent shell has no pinentry. Try `git commit` once; if signing
  fails, leave the tree staged as intended and hand the commit to the user. Never pass
  `-c commit.gpgsign=false`.
- Never `git stash` to compare against a baseline; use `git show HEAD:<file>` or a worktree.
- The run-manifest branch is merged; new work branches from `main`.

## Where things are decided

| Changing | Also touch |
|---|---|
| A phase's behaviour | `docs/phases.md`, and `docs/outputs.md` if it writes a file |
| A command-line flag | `bin/migite` parser and `--help`, `docs/migite.md`, `README.md` |
| A config key | `migite/config.py` DEFAULTS and validation, `docs/configuration.md`, `.migite.yml` |
| A backend capability | the adapter, `tests/test_agents_contract.py`, the matrix in `docs/agents.md` |
| `plan.json` / `review.json` / `run.json` shape | `docs/internals.md`, `docs/run-manifest-and-resume.md`, `tests/machine_readable_test.sh` |
| Lint or test selection | `lib/stack.sh` helpers, `docs/phases.md` "Which files get linted and tested" |

## Current work

The 2026-10-06 evaluation and the 13-item work plan (instrumentation, cheaper testing plan,
knowledge retrieval, stack profiles as data, `--automata` (the plan's `--yes`), resumed planning chain, native plan
strategy, session metering, refuter calibration, Workflows decision, OpenCode live) are in the
Claude doc "Migite Evaluation". Each item is one branch off `main`, one commit, the full test
gate, and for items marked live, one real run measured against the baseline `usage.json`.
