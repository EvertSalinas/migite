# Troubleshooting

Symptoms first; each entry names the check that proves the cause.

| Symptom | Jump to |
|---------|---------|
| `✘ migite config error: ...` before anything runs | [Config errors](#config-errors) |
| `... exists but PyYAML is not installed` | [PyYAML](#pyyaml) |
| `No version is set for command python3`, or `Python not found at ...` | [Python](#python) |
| `Prompt file missing: .../prompts/plan.md` | [Prompt files](#prompts) |
| rubocop / rspec never run, or a phase asks for permissions and hangs | [Permission failures](#troubleshooting-permissions) |
| a headless phase exits immediately when launched from inside Claude Code | [Nested sessions](#nested) |
| the commit gate keeps saying NEEDS FIXES | [Review loops](#troubleshooting-needs-fixes) |
| `migite doctor` reports scratchpad/vault drift | [Drift](#drift) |
| `Not logged in` from Cursor, `No API key configured` from OpenCode, or `refusing a tool-enabled call` | [Other backends](#backends) |

<a id="config-errors"></a>
### Config errors

An invalid value in a config file aborts the run on purpose: running on defaults after you wrote
a config would be worse than stopping. The message names the key, the value, and the file:

```text
✘ migite config error: gates.commit.policy must be one of lenient, strict (got 'strcit' from /Users/you/repo/.migite.yml)
✘ Fix the configuration above (or unset MIGITE_CONFIG) and re-run
```

`migite config --validate` reproduces it without starting a run. Unknown keys are a warning, not
an error, so a typo in a key name shows up as `⚠ config: ...: unknown key 'gatse.commit' (ignored)`
and the run continues on the default for that key.

<a id="pyyaml"></a>
### PyYAML

Only needed when a `.yml` / `.yaml` config file exists. `pip3 install pyyaml` into the Python
`MIGITE_PYTHON` points at, or rename the file to `.json`. With no config files at all, migite
runs without PyYAML.

<a id="python"></a>
### Python

Migite uses `MIGITE_PYTHON` if set, else `python3` if it actually runs, else an asdf fallback
path. `No version is set for command python3` means an asdf shim is on `PATH` with no version
selected for this directory; migite's own check skips it, but the four standalone wrappers and
`tests/run.sh` resolve Python the same way only when `MIGITE_PYTHON` is unset. The robust fix is
one line in your shell profile:

```bash
export MIGITE_PYTHON="$HOME/.asdf/installs/python/3.13.5/bin/python3"
"$MIGITE_PYTHON" -c "import langgraph" || "$MIGITE_PYTHON" -m pip install langgraph
```

<a id="prompts"></a>
### Prompt files

`migite`, `migite-plan`, and `migite-review` fail loudly if any of `prompts/{plan,implement,review,architecture_critic}.md`
is missing, rather than running with an empty prompt. Causes: an incomplete checkout, or
`prompts.dir` pointing at a directory that exists but lacks the file (that case falls back to
the repo copy, so it is almost always the checkout). `migite doctor` lists each prompt's presence.

<a id="troubleshooting-permissions"></a>
### Permission failures running bundler / rubocop / rspec / brakeman

Permission modes are set in one place — the `permissions` block of the config
([docs/configuration.md](./configuration.md#permissions)) — and apply uniformly:

| Key | Default | Applies to |
|---|---|---|
| `permissions.interactive` | `bypassPermissions` | `run_phase` sessions: implement, gate fixes, PR description |
| `permissions.heal` | `bypassPermissions` | the auto-heal loop's headless fixes |
| `permissions.headless` | `none` (no flag) | every other headless call in every tool: plan, review, knowledge, amendments, explore, audit, blueprint, pr-review |

`MIGITE_PERMISSION_MODE` still works and maps onto `permissions.headless` (env beats files). The
Jira fetch always passes its own explicit mode plus a two-tool allowlist, regardless of these.

```bash
export MIGITE_PERMISSION_MODE=acceptEdits          # or, in .migite.yml:  permissions: { headless: acceptEdits }
```

Omitting `--permission-mode` in `--print` mode means any tool use in that call is refused — the
agent still produces output, just without being able to read/run anything, and with no visible
error. This matters most for `migite-plan`/`migite-review`, which lean on file reads throughout.

**If failures persist, the cause is almost certainly managed settings.** An enterprise policy can
forbid `bypassPermissions`, in which case Claude Code silently falls back to prompting — which
nothing can answer in a non-interactive `--print` call, or in `run_phase`'s hardcoded
`bypassPermissions` sessions. Check with:

```bash
claude config list                      # look for a pinned/managed permission policy
echo '1' | claude --print --permission-mode bypassPermissions 'run: echo ok'
```

If the second command can't run the tool, set modes your policy does allow in `.migite.yml`:

```yaml
permissions:
  interactive: acceptEdits
  heal: acceptEdits
  headless: acceptEdits
```

<a id="nested"></a>
### Running migite from inside a Claude Code session

Claude Code sets `CLAUDECODE` in its own terminal sessions, and a nested `claude` refuses to
start while it's set. Every migite call site strips it — the Python agents drop it from the
subprocess environment, and all bash launches go through `claude_cmd()` (`helpers.sh`), which
is `env -u CLAUDECODE claude "$@"`. If you add a new `claude` invocation, use `claude_cmd`, not
`claude` directly.

Note that **brakeman is not part of migite**. If you're seeing brakeman runs, they come from your
`~/.claude/CLAUDE.md`, a project CLAUDE.md, or a skill — migite never invokes it.

<a id="troubleshooting-preflight"></a>
### Tooling preflight

There's no `bundle check`/rubocop/rspec startup preflight — a broken Ruby toolchain surfaces only
when a phase actually tries to run it (look for `No version is set for command` or a bundler error
in the relevant log). The one real preflight that exists is narrower: before spawning any LangGraph
agent, `spawn_langgraph()` (`helpers.sh`) verifies `import langgraph` succeeds in
`$MIGITE_PYTHON` and fails loudly if it doesn't — that's a Python-dependency check, not a
Ruby-tooling one.

<a id="troubleshooting-needs-fixes"></a>
### Review keeps saying NEEDS FIXES

There's no bounded auto-fix loop or attempt cap — at the COMMIT GATE, `f` (Claude fixes it in an
interactive session) and `n` (you fix it, then press Enter) both re-run rubocop + rspec + the full
`migite-review` pass and reopen the gate with a fresh verdict. You can loop through either as many
times as you need; nothing stops `y` from being pressed on the first pass regardless of the
verdict shown.

If the same finding keeps coming back, it's usually something Claude can't fix blind — a schema
decision, a missing factory, or a genuinely wrong review finding. Use `e` at the gate to open
`review.md` directly and strike the bad finding, or `n` to fix it yourself and re-run checks when
ready. With `gates.commit.policy: strict`, a capital `Y` approves over the remaining blockers and
records them in `gate-overrides.md`.

<a id="backends"></a>
### Other backends (Cursor, OpenCode)

`agent.backend` picks the CLI. Each has its own login: `cursor-agent login` (or `CURSOR_API_KEY`),
`opencode auth login`. An `is_error` result from Cursor or an `error` event from OpenCode fails the
call with that CLI's message. Two things are by design, not bugs:

- `refusing a tool-enabled call` — only Claude Code can run a headless call with a scoped MCP tool
  allowlist; on other backends the Jira fetch is skipped and planning proceeds without the ticket
  body (the key still names the task).
- The reviewer prints `backend has no structured output — text synthesis + markdown verdict` and
  `review.json` says `"source": "markdown"`; the gate still reads a verdict, from the anchored
  markdown parser.

Model tiers are unset for these backends until you pin them; see [agents.md](./agents.md#models-per-backend).

<a id="drift"></a>
### `migite doctor` reports scratchpad/vault drift

```text
⚠ Scratchpad/vault drift (2):
  - bb-1234/plan.md — scratchpad is newer than vault (sync_artifact may not have run)
  - bb-1234/notes.md — no vault counterpart
```

The first line means a phase wrote the scratchpad copy and crashed before mirroring it; re-running
the task re-syncs on the next write. The second means a file was added to the scratchpad by hand;
migite only mirrors what it writes. Neither blocks a run. Drift from old runs of migite on its
own repo is normal and safe to ignore or delete.
