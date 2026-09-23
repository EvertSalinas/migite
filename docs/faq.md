# FAQ

**Does migite commit for me?**
No. It never runs `git commit`, including at the commit gate's `y`. That key only ends the review
loop and moves on to knowledge capture and the PR description. The commit is always yours. See
[the commit gate](./outputs.md#commit-gate-banner).

**Do I need an Anthropic API key?**
No. Every model call shells out to the `claude` CLI and shares Claude Code's login.

**What does a run cost?**
It is printed at the end of every run and written to `usage.json`; the commit-gate banner shows
the running total. Only headless calls are metered (planner, reviewers, knowledge, amendments);
the interactive implement and PR-description sessions are not, because the CLI reports usage
only in `--print` mode. Expect roughly $3 to $5 in headless calls for a feature on the default
tiering, less with `think` pinned to Sonnet. Set `budget.max_usd_per_run` for a soft cap.

**Why does every headless call show about 23k cache-creation tokens?**
That is Claude Code's own system context being sent with each `claude --print` call. It is a
cache hit after the first call in a run, so the per-call cost drops sharply from the second call
on. The usage summary prints the total so you can see it.

**Does `--jira` fetch the ticket's content?**
Yes, for planning. The ticket's title, type, priority, status, description, and acceptance
criteria are fetched through the Atlassian MCP before the planner runs, cached to
`jira-context.md`, and given to synthesis and the explorers. It is the one call in the pipeline
with tool access, scoped to two read-only lookups. If the fetch fails, planning continues
without it and the key is still used for naming.

**Does `--jira` always open the intake editor?**
Only on the first run for that ticket. An existing `intake.md` is reused silently, and
`--intake` / `--audit` mode shows a `[y/e/q]` confirm prompt instead of an editor.

**Can I resume a run I aborted or that crashed?**
Yes. Re-run the same command. An existing intake is reused; an existing `plan.md` offers
`[u]se existing` or `[r]edo`; a scratchpad that was deleted is restored from the vault mirror.
See [resuming a run](./migite.md#resuming-a-run).

**Why do the explorers read only a handful of files?**
Cost and context. Each of the seven parallel explorers is capped at 14 files and 14k characters.
They ground the plan in real file and method names; the implement session, with full read and
search access, does the deep exploration. Changed files are always read first, then files ranked
by intake-keyword hits, weighted 5x for a hit in the path.

**Does a brand-new file I have not `git add`ed get linted and tested?**
Yes. The changed-file helpers union `git diff <base>` with untracked, non-ignored files. The one
gap: the reviewer's diff comes from plain `git diff`, so a new file's content reaches the
reviewer only once staged. The implement prompt tells Claude to `git add` new files for that
reason.

**Why does Phase 3 re-run rubocop and rspec after the heal loop passed them?**
The heal loop delivers clean input to the reviewer; it does not replace the review. Phase 3 runs
its own authoritative pass so a stale or partial heal never reaches the commit gate.

**Does migite work on non-Rails projects?**
Partly. A repo with no `Gemfile` is the `generic` stack: plan, implement, review, knowledge, and
PR description all run; rubocop, rspec, and the heal loop are skipped; explorers use
language-agnostic globs. Running another stack's lint and test commands is on the roadmap.
`migite-explore` and `migite-blueprint` are already stack-agnostic; `migite-audit` and
`migite-pr-review` still use Rails checklists.

**How do I use a cheaper or a stronger model for one step?**
Pin the role in `.migite.yml`: `models: { roles: { think: claude-sonnet-5 } }`. Roles cover
every call site; tiers move them in bulk. `migite config` lists the resolved model per role.
See [configuration](./configuration.md#models).

**What is the difference between `f` and `e` at a gate?**
`f` takes one line of feedback and makes one model call to revise the document, then shows a
diff. `e` opens the document in your editor with no model call. Use `f` for a targeted
correction you would rather describe than write, `e` for a surgical edit you already know.

**Where did my slash commands go?**
The four phase prompts moved from `~/.claude/commands/` into the repo's `prompts/`. Nothing under
`~/.claude/` is required any more. To keep them as slash commands, symlink them back (getting
started, install step 6). Note they are written for the headless pipeline now: they output the
document rather than writing files.

**Why are there two copies of every file, scratchpad and vault?**
The scratchpad in the repo is the working copy every phase reads and writes. The vault is a
mirror for reading later, in Obsidian or anywhere, and survives deleting the scratchpad. Resume
reads the scratchpad first and falls back to the vault.

**Why did `migite` abort with a config error before doing anything?**
An invalid value in a config file (a typo in an enum, a non-integer, bad YAML) aborts on purpose.
Running on defaults after you wrote a config would be worse than stopping. `migite config
--validate` shows the exact key and file. Unknown keys are only a warning.
