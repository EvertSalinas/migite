# Troubleshooting

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
ready.
