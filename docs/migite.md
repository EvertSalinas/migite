# `migite` — workflow orchestrator

The command-line reference for `migite` and its run modes. The reference is split in three:

| Doc | Covers |
|-----|--------|
| **this file** | command line, `--type` and `--stack`, amend / intake / blueprint / audit modes, design principles, resuming a run |
| [phases.md](./phases.md) | what each phase does and calls, every gate key, which files get linted and tested, memory injection, tmux |
| [outputs.md](./outputs.md) | every file a run writes, the commit-gate banner, the testing-plan requirement |

For task-by-task tutorials with examples, see [workflows/](./workflows/README.md). New here? Read [getting-started.md](./getting-started.md) first: it walks one complete run with
every prompt and output file. Configuration keys are in [configuration.md](./configuration.md).

## Contents

- [Command-line reference](#cli)
  - [`--type` values](#type-values)
  - [`--stack` values](#stack-values)
- [Amend mode](#amend-mode)
- [Intake mode](#intake-mode)
- [Blueprint mode](#blueprint-mode)
- [Audit mode](#audit-mode)
- [Design principles](#design-principles)
- [Resuming a run](#resuming-a-run)

---

<a id="cli"></a>
### Command-line reference

```bash
migite "<task description>"                 # plain text task; type picked interactively
migite --jira BB-1234 [--type feature]      # Jira key or full Atlassian URL; ticket fetched for planning
migite ./intake-01-foundation.md            # an existing .md positional arg is an intake (same as --intake)
migite --intake <file> [--blueprint <file>] # pre-written intake, optional blueprint as settled architecture
migite --audit <report.md> [--jira KEY]     # remediation task generated from a migite-audit report
migite --attach <file> ...                  # fold reference material into the planner's context (repeatable)
migite --staged                             # one implement session per Scope sub-section, checkpoint between
migite --stack rails|generic                # override stack detection
migite --resume [run.json]                  # continue an interrupted run; a plain re-run of the same command does too
migite --amend ["feedback"] | --amend-file <file> [--jira KEY]   # scope a delta against a built task
migite doctor [--repo <path>]               # read-only health check
migite migrate-vault [--dry-run]            # move pre-run-folder task folders into run folders
migite config [--edit [--user] | --init [--user] [--force] | --validate | --path [--user]]   # layered configuration
migite --help                               # all of the above; every subcommand and tool also takes --help
```

An option migite doesn't recognise is an error that points at `--help`, not a task description.

<a id="type-values"></a>
**`--type` values**

| Value | When to use | Template |
|-------|-------------|----------|
| `feature` | New behaviour, new endpoint, new model | `templates/feature.md` |
| `bug` | Fix a regression or reported defect | `templates/bug.md` |
| `refactor` | Internal restructure, no behaviour change | `templates/refactor.md` |
| `spike` | Investigation or proof of concept; the plan is a recommendation, not a dev plan | `templates/spike.md` |
| `config` | Infrastructure, environment, or gem changes | `templates/config.md` |

`plan.sh` copies the template into the scratchpad and opens `$EDITOR` on it. `templates.dir` in
the config overrides any of them per project.

<a id="stack-values"></a>
**`--stack` values**

| Value | When it's used |
|-------|----------------|
| `rails` | Auto-detected when a `Gemfile` exists at the repo root or one level down. Runs rubocop/rspec via `bundle_exec` from the app's directory. |
| `generic` | Everything else. Skips rubocop/rspec/`bundle`; plan and review still run against the diff, with language-agnostic explore globs instead of Rails' MVC split. |

Precedence: `--stack` on the command line, then `stack:` in the config, then detection.

<a id="amend-mode"></a>
### Amend mode (`--amend`)

For feedback that arrives **after** implementation — PR comments, QA bugs, scope changes. A fresh
`--type bug` run would spend ~10 model calls re-planning from scratch and land in a new vault
directory, disconnected from the original work. `--amend` costs **one call** and stays in place.

```bash
# Inline feedback
migite --amend "reviewer says the translator must be idempotent on retry"

# From a file (QA notes, pasted PR comments)
migite --amend-file ./qa-notes.md

# No feedback given — opens $EDITOR
migite --amend

# Target a specific ticket instead of auto-detecting
migite --amend "fix the retry path" --jira <jira-ticket-id>
```

**How the task is found**, in order:

| Step | Behaviour |
|------|-----------|
| `--jira <jira-ticket-id>` | Uses that ticket's vault directory |
| Branch name | Extracts a ticket key from the branch (`feature/<jira-ticket-id>-add-pdf` → the lowercased slug) |
| Picker | Lists the 10 most recently modified task directories for this repo |

**What it does differently from a normal run:**

```
skip intake        (reuses the original)
skip 7 explorers   (the changed files are already in your diff)
skip synthesis / architecture critic / refine
      │
ONE Sonnet call ── reads plan.md + each run's implementation.md
                   (summary.md for runs plan.md already reflects)
                   + unfolded amendments + latest review.md
                   + git diff --stat + diff capped at ui.prompt_diff_max_bytes
                   + recent knowledge.md entries + your feedback
      │
NN-amend-<slug>/amendment.md → gate [y/f/e/q]
      │ (on y)
update testing-plan.md with exact edits - one more Sonnet call
(regenerated in full only when no usable edit comes back)
      │
Phase 2 implement → 2.5 heal → 3 review → commit gate → 3.5 knowledge
      → 3.8 plan update → 4 PR description → 4.2 run summary
```

The gate itself:

```
Proceed with amendment? [y/f/e/q] (y=approve, f=feedback refine, e=edit directly, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve - updates `testing-plan.md` with exact edits, then continues to implementation |
| `f` | Feedback - one more headless call revises the amendment document in place |
| `e` | Edit: opens `amendment.md` directly in `$EDITOR` |
| `q` | Abort the workflow |

Key properties:

- **Each amendment gets its own run folder**, `01-amend-<slug>/`, `02-amend-<slug>/`, so the record of what changed and why lives with the work. See [Run folders](./vault-structure.md#run-folders).
- **`plan.md` stays current.** At the end of the run, [Phase 3.8](./phases.md#phase-3-8-plan-update) proposes exact edits that fold the amendment into it, shows you the diff, and adds a revision line. The approved original stays in `00-build/plan.md`.
- **`testing-plan.md` is edited on approval**, unlike `plan.md`, which waits for the end-of-run plan update. Exact edits rewrite or drop the steps the amendment invalidates; it is regenerated in full only when no usable edit comes back. Once the amendment is built, [Phase 3.8](./phases.md#phase-3-8-plan-update) proposes a second round of edits from the final diff, which you approve the same way as the plan update. It reflects current, post-amendment behaviour, not history. See [Testing Plan requirement](./outputs.md#testing-plan-requirement).
- **Grounded in the diff, not a re-exploration.** The amendment prompt sees the actual built code, so it can say "this already handles that, only the retry path changes" instead of re-planning greenfield.
- **Implementation is scoped to the amendment.** The implement prompt marks the current plan as already built and enforces the amendment's `Out of scope` section.
- **The PR description covers every amendment**: through the updated plan, plus any amendment the plan doesn't reflect yet, so it describes the final delivered scope.
- If the feedback turns out to be already satisfied by the current code, the amendment says so and leaves `Scope` empty rather than inventing work.

Note that migite never runs `git commit` itself — the commit gate is an approval step. Each amendment is therefore naturally its own commit, made by you after the gate.

<a id="intake-mode"></a>
### Intake mode (`--intake`)

`migite-explore --intakes` and `migite-blueprint` both emit ready-made intake files. Pass one to
`migite` and it skips the type picker and the template editor entirely:

```bash
migite --intake ./exploration-.../intake-01-extract-provider-adapter.md
migite ./intake-01-foundation.md      # positional .md file is auto-detected
```

| Derived from the file | Behaviour |
|---|---|
| `Title:` line | Determines the vault/scratchpad slug |
| `Type:` line | Sets the task type — no picker prompt |
| Missing `Title:` | Warns, falls back to the filename for the slug |
| Invalid `Type:` | Warns, falls back to the interactive picker |

An explicit `--type` always wins over the file's `Type:` line. After loading, migite shows the
first 30 lines and opens a `[y/e/q]` confirm-or-edit prompt before planning starts.

A positional argument is only treated as an intake when it is an **existing** file ending in `.md`.
A plain description like `"fix N+1 on district index"` is unaffected, and so is a non-existent
path like `notes.md`, which stays a task description.

**Adding details the file doesn't cover.** Right after the confirm-or-edit gate, migite always asks:

```
Add supplementary details in a separate task.md before planning? [y/N]:
```

Answering `y` opens `$EDITOR` on a blank scratch file; whatever you write is saved as `task.md`
next to `intake.md` in the scratchpad (mirrored to the vault). It's deliberately a **separate file, never merged into the
passed intake** — the same reasoning as amendments staying beside `plan.md` instead of rewriting
it: the original `--intake` file stays exactly what `migite-explore`/`migite-blueprint` produced
(or whatever you were handed), and `task.md` is clearly your own addition on top of it.
`migite-plan` reads both and treats `task.md` as authoritative context for anything it adds.
Leaving the editor empty (or answering anything but `y`) skips it — no `task.md` is created,
unless `--attach` was also given (see below), in which case `task.md` is still written from the
attachments alone.

**Attaching reference material (`--attach <file>`, repeatable).** `migite-plan`'s agent calls are
headless, with no permission flag passed by default (`permissions.headless: none` - see
[Permission failures](./troubleshooting.md#troubleshooting-permissions)), so a file path merely
mentioned in the intake (a data map, a spec doc, a design mock) can never be opened by the model
itself. `--attach` reads the file's raw content and folds it into `task.md` as a `## Attachment:
<name>` block, so it reaches `migite-plan` as plain prompt text instead. Works regardless of
`--intake` mode — if you also answer `y` to the supplementary-details prompt above, the attachment
content is pre-filled into the editor buffer so you can add more around it, in the same file. Each
attachment is capped at 50,000 chars (truncated with a warning beyond that) since it's repeated in
full on every explore/synthesis/critic/refine call for this task, not read once.

<a id="blueprint-mode"></a>
### Blueprint mode (`--blueprint`)

When you have a `migite-blueprint` output, pass it to `migite` with `--blueprint` to give the planner pre-decided architectural context:

```bash
migite --jira <jira-ticket-id> --blueprint ~/dev-log/Personal/my-project/blueprint/blueprint.md
```

The blueprint is injected into Phase 1 planning as settled decisions — the 7 parallel explorers still run, but `synthesize_plan` and the architecture critic treat the blueprint's domain model, API surface, and tech decisions as constraints rather than open questions.

<a id="audit-mode"></a>
### Audit mode (`--audit`)

When `--audit <path>` is the primary input (no task description or Jira ticket), migite skips the type selector and intake editor entirely. It auto-generates an intake from the audit report and shows a quick `[y/e/q]` confirm-or-edit prompt before planning starts.

The audit content is injected into both `synthesize_plan` and `run_architecture_critic`, so the planner and critic see the raw findings alongside the intake. The task type defaults to `refactor`.

Critical findings (🔴) and warnings (🟡) are always preserved in full when the audit report is injected — they are extracted first before any character-limit trimming, so a large audit never silently drops the most important lines.

```
migite --audit <path>
  → auto-sets task = "audit-remediation"
  → auto-sets type = refactor
  → generates intake from audit findings
  → [y/e/q] — proceed / edit / abort
  → planning starts immediately
```

<a id="design-principles"></a>
### Design principles

Two principles that already govern how migite has evolved, written down as a citable reference:

- **Narrow core, capability at the edges.** Prefer editing a prompt/template over adding new bash logic to `lib/**/*.sh` when the same result is reachable that way. Growing `lib/**/*.sh` for something a prompt template already covers adds permanent core surface for a one-off need.

- **Doc-drift checklist.** Before calling a rename/move done, grep `README.md` and `docs/*.md` for the old name. Nothing in this repo enforces docs and implementation moving together, so it has to be a manual habit.

---

<a id="resuming-a-run"></a>
## Resuming a run

Every run keeps a run manifest, `run.json` in its run folder (`scratchpad/<task>/00-build/run.json`,
mirrored to the vault), written at every phase boundary: the run's arguments, branch and folders,
and the status of each phase (`plan`, `tdd`, `implement`, `heal`, `review`, `deliver`). Run the same
command again and migite continues from the recorded position instead of starting over:

| Recorded in `run.json` | On the next run |
|---|---|
| a phase `done` (or `tdd` / `skipped`) | Skipped, with a line saying so. A declined TDD question isn't asked again |
| a phase `running` | It was interrupted (a crash, a closed terminal, an error): that phase runs again from its start |
| plan `pending_gate` (`q` at the plan gate) | Straight back to the plan gate, without the use-or-redo question |
| implement `pending_gate` / `running` with `--staged` | Starts after the last stage that finished (`stage_num`); a `q` at a checkpoint re-opens that checkpoint |
| review `pending_gate` (`q` at the commit gate) | Lint and specs run again, then the gate re-opens. `review.md` is reused when the code is the same as the code it reviewed; any change, including edits you made after `n`, gets a new review |
| deliver `running` | Phase 3.5 to 4.5 run again as one: the knowledge question and the PR session come back |
| `status: complete` | Prints where the run's files are and exits 0. Use `migite --amend` for a follow-up |

The run is found by its arguments, so a task whose intake `Title:` renamed its folder is still
found from the original command: the newest manifest whose Jira key, intake file, or task text
matches wins, in the scratchpad or, when the scratchpad copy is gone, the vault. `migite --resume`
picks the newest unfinished run in the repo without any other arguments, and
`migite --resume <run.json>` names one. A resumed run checks out the branch it was on, keeps its
base branch and usage ledger (so the cost total spans the interruption), and pulls its documents
back from the vault when the scratchpad lost them.

`--amend` always scopes a new amendment. An amend run that stopped is picked up with
`migite --resume <its run.json>` (`scratchpad/<task>/NN-amend-<slug>/run.json`); one that stopped at
its amendment gate re-opens the same amendment.

`run.json` records where the workflow got to, not the working tree: resuming assumes the code is
as you left it. To build a task again from scratch, delete its `run.json` in both the scratchpad
and the vault (the vault copy alone would be found and resumed). Design and schema:
[run-manifest-and-resume.md](./run-manifest-and-resume.md) and [outputs.md](./outputs.md#run-json).

Underneath, and for a task from before run manifests:

| State | Behaviour |
|-------|-----------|
| Task still uses the flat, pre-run-folder layout | Moved into run folders first (scratchpad and vault), see [Run folders](./vault-structure.md#run-folders) |
| `scratchpad/<ticket>/00-build/intake.md` exists | Reused, no template copy |
| `scratchpad/<ticket>/plan.md` missing but vault has one | `resume_from_vault()` copies it into the scratchpad before the plan gate runs |
| `plan.md` exists (scratchpad, after the above) | Offers `[u]se existing` or `[r]edo` |
| `.plan.done` sentinel missing after agent | Hard error on the initial plan generation; only a warning (gate still opens) if it's missing after an `n`-redo from the plan gate |
| `.review.done` sentinel missing after agent | Warning — review output may be incomplete |
| `--amend` targeting a task whose scratchpad no longer exists | `resume_from_vault()` recovers `plan.md`/`implementation.md`/`review.md`/`testing-plan.md`/`intake.md` from the vault mirror before amend mode checks for an existing plan |
