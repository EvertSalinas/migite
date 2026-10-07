# FAQ

**Does migite commit for me?**
No. It never runs `git commit`, including at the commit gate's `y`. That key only ends the review
loop and moves on to knowledge capture and the PR description. The commit is always yours. See
[the commit gate](./outputs.md#commit-gate-banner).

**Do I need an Anthropic API key?**
No. Every model call shells out to the configured agent CLI (Claude Code by default, Cursor CLI, Kimi Code,
or OpenCode via `agent.backend`) and uses that CLI's own login.

**What does a run cost?**
It is printed at the end of every run and written to `usage.json`; the commit-gate banner shows
the running total. Only headless calls are metered (planner, reviewers, knowledge, amendments);
the interactive implement and PR-description sessions are not, because the CLI reports usage
only in `--print` mode. Expect roughly $3 to $5 in headless calls for a feature on the default
tiering, less with `think` pinned to Sonnet. Set `budget.max_usd_per_run` for a soft cap.

**Why does every headless call show about 23k cache-creation tokens?**
That is Claude Code's own system context being sent with each headless call. It is a
cache hit after the first call in a run, so the per-call cost drops sharply from the second call
on. The usage summary prints the total so you can see it.

**Can a wrong review finding block me?**
Less often than it used to. Reviewers must quote the line each Critical and Warning rests on, and
a second agent (the refuter) then tries to disprove every Critical with read-only tools before it
can decide the verdict. A finding it disproves is dropped and listed at the end of `review.md`
under "Refuted by verification". It can still be wrong in both directions, which is why
[calibrating it](./workflows/calibrate-the-refuter.md) against findings you labeled is a workflow of
its own. See [troubleshooting](./troubleshooting.md#troubleshooting-refuted).

**What does the refuter cost?**
One strong-tier call per Critical, roughly $0.2 to $0.6 each on Fable 5.1 (it varies about 30% between
runs of the same finding), and none when a review has no Critical. At most 12 findings are checked
per run. Warnings are checked only when their evidence did not check out.

**Does `--jira` fetch the ticket's content?**
Yes, for planning. The ticket's title, type, priority, status, description, and acceptance
criteria are fetched before the planner runs, cached to `jira-context.md`, and given to synthesis
and the explorers. With Atlassian's `acli` installed and logged in (a one-time browser login),
migite reads it through `acli`: no model call, no token, same on every agent. Without it, migite
falls back to one agent call through the Atlassian MCP tools, restricted to the `jira.read` scope
(Claude Code only). If neither can run
or the fetch fails, planning continues without it and the key is still used for naming. See
[tickets.md](./tickets.md).

**Does `--jira` always open the intake editor?**
Only on the first run for that ticket. An existing `intake.md` is reused silently, and
`--intake` / `--audit` mode shows a `[y/e/q]` confirm prompt instead of an editor.

**Can I resume a run I aborted or that crashed?**
Yes. Re-run the same command. `run.json` in the run's folder records which phases finished, so
those are skipped, a gate you quit at (`q`) re-opens, and an interrupted phase runs again. A
scratchpad that was deleted is restored from the vault mirror. `migite --resume` alone picks up
the newest unfinished run. See [resuming a run](./migite.md#resuming-a-run).

**How do I build a task again from scratch, not resume it?**
Delete `scratchpad/<task>/00-build/run.json` (and its vault copy), then run the command again.
For a change to a task that's already built, use `migite --amend` instead.

**Why do the explorers read only a handful of files?**
Cost and context. Each of the seven parallel explorers (eight when the task may touch the
frontend) is capped at 14 files and 14k characters.
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
Yes, with a [stack profile](./configuration.md#stacks). A `stacks.<name>` block in `.migite.yml`
names the repo's detect files, its lint, autofix and test commands, and the globs that pick each
command's changed files; heal and review then run them, and exit codes decide. Without a profile,
a repo with no `Gemfile` is the `generic` stack: plan, implement, review, knowledge, and PR
description all run; lint, tests, and the heal loop are skipped. Either way, explorers use
language-agnostic globs. `migite-explore` and `migite-blueprint` are already stack-agnostic;
`migite-audit`, `migite-pr-review` and Phase 3's reviewers check what the stack's
[checklist](./configuration.md#checklists) says: Rails' own on rails, a language-neutral one
everywhere else, and your `checklists/<name>.md` when you write one.

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
