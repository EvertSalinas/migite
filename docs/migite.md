# `migite` — workflow orchestrator

The command-line reference for `migite` and its run modes. The reference is split in three:

| Doc | Covers |
|-----|--------|
| **this file** | command line, `--type` and `--stack`, amend / intake / blueprint / audit modes, design principles, resuming a run |
| [phases.md](./phases.md) | what each phase does and calls, every gate key, which files get linted and tested, memory injection, tmux |
| [outputs.md](./outputs.md) | every file a run writes, the commit-gate banner, the testing-plan requirement |

New here? Read [getting-started.md](./getting-started.md) first — it walks one complete run with
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
migite --amend ["feedback"] | --amend-file <file> [--jira KEY]   # scope a delta against a built task
migite doctor [--repo <path>]               # read-only health check
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
ONE Sonnet call ── reads plan.md + implementation.md + review.md
                   + git diff <base> + knowledge.md + your feedback
      │
amendment-NN.md → gate [y/f/e/q]
      │ (on y)
regenerate testing-plan.md in full — one more Sonnet call
      │
Phase 2 implement → 2.5 heal → 3 review → commit gate → 3.5 knowledge → 4 PR description
```

The gate itself:

```
Proceed with amendment? [y/f/e/q] (y=approve, f=feedback refine, e=edit directly, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve — regenerates `testing-plan.md` in full, then continues to implementation |
| `f` | Feedback - one more headless call revises the amendment document in place |
| `e` | Edit — opens `amendment-NN.md` directly in `$EDITOR` |
| `q` | Abort the workflow |

Key properties:

- **`plan.md` is never overwritten.** Amendments accumulate as `amendment-01.md`, `amendment-02.md` beside the original plan, so the record of what changed and why lives with the work.
- **`testing-plan.md` is overwritten in full on approval**, unlike `plan.md` — it reflects current, post-amendment behaviour, not history. See [Testing Plan requirement](./outputs.md#testing-plan-requirement).
- **Grounded in the diff, not a re-exploration.** The amendment prompt sees the actual built code, so it can say "this already handles that, only the retry path changes" instead of re-planning greenfield.
- **Implementation is scoped to the amendment.** The implement prompt marks the original plan as already built and enforces the amendment's `Out of scope` section.
- **The PR description absorbs every amendment**, so it reflects the final delivered scope rather than only the original plan.
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

- **Narrow core, capability at the edges.** Prefer editing a prompt/template over adding new bash logic to `migite.d/*.sh` when the same result is reachable that way. Growing `migite.d/*.sh` for something a prompt template already covers adds permanent core surface for a one-off need.

- **Doc-drift checklist.** Before calling a rename/move done, grep `README.md` and `docs/*.md` for the old name. Nothing in this repo enforces docs and implementation moving together, so it has to be a manual habit.

---

<a id="resuming-a-run"></a>
## Resuming a run

| State | Behaviour |
|-------|-----------|
| `scratchpad/<ticket>/intake.md` exists | Reused — no template copy |
| `scratchpad/<ticket>/plan.md` missing but vault has one | `resume_from_vault()` copies it into the scratchpad before the plan gate runs |
| `plan.md` exists (scratchpad, after the above) | Offers `[u]se existing` or `[r]edo` |
| `.plan.done` sentinel missing after agent | Hard error on the initial plan generation; only a warning (gate still opens) if it's missing after an `n`-redo from the plan gate |
| `.review.done` sentinel missing after agent | Warning — review output may be incomplete |
| `--amend` targeting a task whose scratchpad no longer exists | `resume_from_vault()` recovers `plan.md`/`implementation.md`/`review.md`/`testing-plan.md`/`intake.md` from the vault mirror before amend mode checks for an existing plan |
