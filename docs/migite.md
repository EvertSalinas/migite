# `migite` — workflow orchestrator

Full reference for the main `migite` command: usage modes, phases, output files, the commit gate,
the Testing Plan requirement, and resuming a run. See the [README](../README.md) for
installation, configuration, and a quickstart.

## Contents

- [`--type` values](#type-values)
- [Amend mode](#amend-mode)
- [Intake mode](#intake-mode)
- [Blueprint mode](#blueprint-mode)
- [Audit mode](#audit-mode)
- [Design principles](#design-principles)
- [Phases](#phases)
  - [Phase 1 — Plan](#phase-1-plan)
  - [Phase 1.5 — TDD specs](#phase-1-5-tdd)
  - [Phase 2 — Implement](#phase-2-implement)
  - [Phase 2.5 — Auto-heal loop](#phase-2-5-heal)
  - [Which files get linted and tested](#lint-test-selection)
  - [Phase 3 — Review](#phase-3-review)
  - [Phase 3.5 — Knowledge capture](#phase-3-5-knowledge)
  - [Phase 4 — PR description](#phase-4-pr-description)
  - [Phase 4.5 — Self-improvement](#phase-4-5-self-improvement)
- [Active memory injection](#active-memory-injection)
- [tmux integration](#tmux-integration)
- [Output files](#output-files)
- [The commit gate banner](#commit-gate-banner)
- [Testing Plan requirement](#testing-plan-requirement)
- [Resuming a run](#resuming-a-run)

---

<a id="type-values"></a>
### `--type` values

| Value | When to use |
|-------|-------------|
| `feature` | New behaviour, new endpoint, new model |
| `bug` | Fix a regression or reported defect |
| `refactor` | Internal restructure, no behaviour change |
| `spike` | Investigation or proof of concept |
| `config` | Infrastructure, environment, or gem changes |

Each value maps to an intake template shipped in this repo's [`templates/`](../templates) directory
(`templates/<type>.md`) — `plan.sh` copies `$MIGITE_HOME/templates/${TASK_TYPE}.md` into the
scratchpad before opening `$EDITOR` on it (see [Phase 1 — Plan](#phase-1-plan)).

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
| `f` | Feedback — one more `claude --print` call revises the amendment document in place |
| `e` | Edit — opens `amendment-NN.md` directly in `$EDITOR` |
| `q` | Abort the workflow |

Key properties:

- **`plan.md` is never overwritten.** Amendments accumulate as `amendment-01.md`, `amendment-02.md` beside the original plan, so the record of what changed and why lives with the work.
- **`testing-plan.md` is overwritten in full on approval**, unlike `plan.md` — it reflects current, post-amendment behaviour, not history. See [Testing Plan requirement](#testing-plan-requirement).
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

**Attaching reference material (`--attach <file>`, repeatable).** `migite-plan`'s Claude calls are
headless (`claude --print`, never given `--permission-mode` — see
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

<a id="phases"></a>
## Phases

<a id="phase-1-plan"></a>
### Phase 1 — Plan (LangGraph)

`migite-plan` runs autonomously as a LangGraph graph:

```
load_context
    │
    ├── explore: models
    ├── explore: controllers
    ├── explore: services
    ├── explore: serializers        (all 7 run in parallel)
    ├── explore: specs
    ├── explore: migrations + schema
    └── explore: routes + config
         │
    synthesize_plan   (Opus 5.5 — role `think`)
         │
    architecture_critic   (Opus 5.5 — highest-stakes call, one per run)
         │
    refine_plan   (Opus 5.5, incorporates critic findings)
         │
    generate_testing_plan   (Opus 5.5, standalone QA/dev verification doc)
         │
    write_outputs   → plan.md + architecture-critic.md + testing-plan.md + sentinel
```

Explorers use Haiku 4.5 for fast file analysis. Each reads changed files first (from `git diff <base branch>` — empty on a fresh branch, populated when resuming or amending), then ranks the rest by intake-keyword hits in path and content, weighted toward path matches. Plan synthesis, refinement, and the testing plan use the strong tier (Opus 5.5 by default, role `think`) — the plan is the highest-leverage text in the run, and the refiner must not be weaker than the critic whose findings it applies. The architecture critic also uses the strong tier — it is the single highest-stakes call in the planner, where a missed finding propagates into implementation. All of this is configurable per role, see [docs/configuration.md](./configuration.md#models). `generate_testing_plan` writes `testing-plan.md` as its own file rather than a section of the plan — see [Testing Plan requirement](#testing-plan-requirement) for why.

**Jira ticket fetching.** When `--jira` was used, `plan.sh` fetches the actual ticket (title, type, priority, status, description, acceptance criteria) via the Atlassian MCP before `migite-plan` runs — the one call in the entire pipeline granted tool access, and it's scoped to just the two read-only Jira-lookup tools, never the full toolset. The result is cached to `jira-context.md` in the scratchpad (mirrored to the vault, reused on redos so it isn't re-fetched every time) and fed into both `synthesize_plan` and the explorers' keyword extraction. If the fetch fails — MCP not configured, not authenticated, wrong key, no access — planning proceeds without it, same as a missing `knowledge.md`/audit/blueprint; the ticket key still works for slugging and vault naming regardless.

After the agent finishes, the architecture critic findings are printed above the plan gate as a checklist. The gate then opens:

```
Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve the plan and continue |
| `f` | Give feedback — migite prompts for text, refines the plan in place with one `claude --print` call (Sonnet 5), shows a colored diff of what changed, then re-opens the gate |
| `e` | Edit — opens `plan.md` directly in `$EDITOR` (default: vim) with zero latency |
| `n` | Reject — re-runs the full `migite-plan` agent from scratch (fresh exploration + synthesis + critic), shows a colored diff after |
| `q` | Abort the workflow |

`f` is right when the plan needs a targeted AI correction. `e` is right when the change is surgical and you know exactly what to write. `n` is for when the exploration found the wrong files or the structure is fundamentally off. After `f` or `n`, a colored unified diff highlights what changed so you can verify the delta at a glance.

<a id="phase-1-5-tdd"></a>
### Phase 1.5 — TDD specs (opt-in, interactive)

After the plan gate, migite asks whether to write spec files before implementation. If yes:

1. Claude writes only RSpec spec files (red phase — no implementation code)
2. Migite confirms specs fail (`bundle exec rspec` on the new files)
3. Implementation proceeds with passing specs as the target

<a id="phase-2-implement"></a>
### Phase 2 — Implement (interactive)

Claude implements the approved plan in an interactive session. Knowledge from `knowledge.md` is injected into the prompt so past repo lessons are in context before any code is written.

**Staged implementation (`--staged`):** If you pass `--staged`, migite parses the `### ` sub-sections from the plan's Scope section and treats each as an implementation layer. Claude runs one interactive session per layer. Between layers, migite shows a `git diff --stat` and opens a checkpoint gate:

```
STAGE CHECKPOINT: 2/4 — controllers
Proceed? [c/r/e/q] (c=continue, r=redo this stage, e=edit next stage brief, q=abort):
```

| Key | Action |
|-----|--------|
| `c` | Continue to the next layer |
| `r` | Redo this layer |
| `e` | Type extra instructions for the *next* layer's prompt (not `$EDITOR` — a typed note, appended before the next session starts) |
| `q` | Abort — migite never runs `git commit` itself, so any uncommitted work just stays in your working tree |

Use `--staged` for large tasks where you want to verify correctness at each architectural boundary before proceeding.

**Known limitations in the current implementation:** `r` decrements the stage counter, but the underlying loop (a bash `for` over the stage list) always advances to the next array element regardless — it doesn't actually re-run the current layer's session yet. And the extra instructions typed under `e` are saved to a log file but nothing feeds them back into the next stage's prompt yet. Both are open bugs, not intentional behavior.

No gate after Phase 2 — migite moves directly to the heal loop.

<a id="phase-2-5-heal"></a>
### Phase 2.5 — Auto-heal loop

After implementation, migite runs rubocop and rspec automatically. If failures exist:

1. Claude fixes them non-interactively (`claude --print --permission-mode bypassPermissions`)
2. Checks re-run
3. Repeats up to `MAX_HEAL_ATTEMPTS` (default 3)

Phase 3 always runs its own authoritative rubocop + rspec pass regardless — the heal loop delivers clean inputs to the reviewer, it doesn't skip the review.

<a id="lint-test-selection"></a>
### Which files get linted and tested

Every phase gets its changed-file list from one set of shared helpers in `helpers.sh` —
`changed_ruby_files`, `changed_spec_files`, `changed_source_files` (Ruby minus specs, for the
heal loop's autocorrect), and `changed_all_files` (any extension, for the diff-vs-notes warning).
Each one is `git diff <base branch> --name-only --diff-filter=ACMR` **union**
`git ls-files --others --exclude-standard`, so tracked changes and never-`git add`ed new files
are both included, and working-tree deletes are excluded. The phases still differ on what
happens when no spec files changed:

| Phase | No-spec-files behaviour |
|-------|--------------------------|
| Phase 1.5 TDD red-state check (`plan.sh`) | skips the check, warns |
| Phase 2.5 auto-heal loop (`implement.sh`) | skips rspec and says so |
| Phase 3 review + commit-gate re-checks (`review.sh`) | **falls back to the full suite** |

| Rule | Why |
|------|-----|
| Base branch is auto-detected | `origin/HEAD`, then `main` / `master` / `develop`. Hardcoding `main` silently produced empty diffs on master-based repos, so rubocop was skipped for the wrong reason |
| Deleted files excluded (`ACMR`) everywhere | Stale paths caused rubocop `No such file or directory` and rspec load errors — Phase 3 didn't apply this filter until it was caught and fixed |
| `bundle exec` runs from the app's actual root, not necessarily the repo root | Bundler only searches upward from cwd for a Gemfile. When the Ruby app lives one level down (e.g. a `rails-app/` subdirectory alongside other tooling), `detect_stack` (`helpers.sh`) finds it via the `rails` stack profile's `stack_rails_app_root` and every `bundle_exec` call `cd`s there first — otherwise `git diff`'s repo-root-relative paths get re-resolved against the wrong directory and rubocop reports files missing |

Untracked files respect `.gitignore` (`--exclude-standard`), and `changed_all_files` additionally
drops anything under `scratchpad/` so migite's own artifacts never show up as "your" changes.
Note that `migite-plan`'s explorers and `migite-review`'s diff still use plain `git diff`, so a
brand-new file is linted and tested but its *content* only reaches the reviewer once staged.

Phase 3 also runs a separate, different check: any changed file (tracked or untracked) whose basename doesn't appear anywhere in `implementation.md` gets flagged as a warning before the review runs, so undocumented changes get caught before the reviewer sees them.

Tooling failures — Ruby version unset, a git-sourced gem not checked out, rspec producing
`0 examples` because of a DB connection or load error — are detected by one shared
`tooling_failed <log>` helper wherever a rubocop/rspec log is inspected (Phase 3, the commit-gate
re-checks, and the banner). When it fires, `TOOLING_ERROR` carries the message into the banner.

**Phase 3's full-suite fallback is configurable.** By default (`heal.full_suite_fallback: true`)
Phase 3 and the commit-gate re-checks still run the whole rspec suite when no spec files changed —
the pre-config behaviour. Set it to `false` in `.migite.yml` to make Phase 3 consistent with Phase
2.5 (skip rspec and say so); the full suite needs a live DB and verifies nothing about the diff.

<a id="phase-3-review"></a>
### Phase 3 — Review (LangGraph)

`migite-review` runs after the authoritative rubocop and rspec pass:

```
load_inputs  (reads plan, implementation notes, rubocop/rspec logs, git diff, testing-plan.md)
    │
    ├── review: correctness      (logic vs plan, scope creep, acceptance criteria)
    ├── review: security         (auth, N+1, SQL injection, raw params, scopes)
    ├── review: test_coverage    (unit + request specs, factories, context wording)
    └── review: testing_plan     (testing-plan.md completeness — see below)
         │
    synthesize_verdict   → de-duplicates findings → READY TO COMMIT | NEEDS FIXES
         │
    write_review   → review.md + sentinel
```

All four reviewers use Sonnet 5 and run in parallel; `synthesize_verdict` uses Opus 5. The commit gate then opens with a context banner showing the verdict, spec failures, and rubocop offense count.

```
Proceed? [y/f/e/n/q] (y=commit, f=Claude fixes, e=edit directly, n=fix it yourself, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve — continue to Phase 3.5 |
| `f` | Claude fixes — opens an interactive session with the full review findings + original plan as context. Claude addresses every Critical and Warning. On `/exit`, migite re-runs rubocop + rspec + `migite-review` automatically and shows the gate again |
| `e` | Edit — opens `review.md` directly in `$EDITOR` so you can annotate, dismiss, or restructure findings before deciding |
| `n` | Manual fix — migite pauses and waits for you to press Enter when ready, then re-runs checks and re-review |
| `q` | Abort the workflow |

Use `f` when the review found something real and the fix is straightforward enough for Claude to handle. Use `e` when you want to read and annotate the review before acting. Use `n` when the fix involves a judgment call, a schema change, or something that needs your direct decision. Both `f` and `n` re-run the full review afterwards through the same helper — there's no cap on how many times you can loop through this.

**What `y` may approve over is a config choice** (`gates.commit.policy`, see
[docs/configuration.md](./configuration.md#gates)). With the default `lenient`, `y` approves on
the first pass regardless of what the banner says. With `strict`, `y` is refused while a
`NEEDS FIXES` verdict (read from `review.json`), spec failures, a tooling error, or remaining
rubocop offenses stand — the blockers are listed — and only a capital `Y` approves anyway,
appending the blockers to `gate-overrides.md` beside the review so the override is on record.

<a id="phase-3-5-knowledge"></a>
### Phase 3.5 — Knowledge capture

A background `claude --print` pass extracts 1–3 reusable bullets from the completed run (plan + implementation + review) and appends them to `knowledge.md`. Entries link back to the review via Obsidian wikilinks (harmless plain text if you're not using Obsidian).

Only domain-level insights are captured: business logic clarifications, non-obvious constraints, architectural decisions. Rails conventions and testing patterns are excluded.

<a id="phase-4-pr-description"></a>
### Phase 4 — PR description (interactive)

Claude generates a PR description from the plan and review, following the prompt/template in
this repo's [`templates/commit.md`](../templates/commit.md) (`deliver.sh` reads it via
`$MIGITE_HOME`). Output goes to `pr-description.md` — ready to paste into GitHub.

<a id="phase-4-5-self-improvement"></a>
### Phase 4.5 — Self-improvement

A background `claude --print` pass (Sonnet 5) reviews the full run and appends 0–3 actionable observations to `migite-improvements.md` in this repo. Observations must be grounded in what happened during the run — no generic suggestions. The full migite source (~110 KB) is included only on *eventful* runs — a plan rejected at the gate, any commit-gate loop, or any auto-heal attempt — since that's when there's a script behaviour to point at; a quiet run gets a function index instead. Phase 3.5's knowledge extraction is pinned to Sonnet 5 as well.

---

<a id="active-memory-injection"></a>
## Active memory injection

Before Phase 1 (Plan), Phase 1.5 (TDD specs), and Phase 2 (Implement), migite reads `knowledge.md` from the vault and injects it into the prompt as:

```
## Repository conventions and past lessons
<contents of knowledge.md>
```

This means every new task starts with the accumulated lessons from all previous tasks in the same repo. Claude sees past N+1 pitfalls, auth patterns, business logic constraints, and architectural decisions before touching anything.

---

<a id="tmux-integration"></a>
## tmux integration

If migite is running inside a tmux session (`$TMUX` is set), every interactive phase and every LangGraph agent opens in a **split pane below the current pane** (`split-window -v`) and signals back to the orchestrator via `tmux wait-for` when done. You get a macOS notification and focus returns to the migite pane automatically.

If you close a pane before the phase completes, migite detects this and aborts with an error rather than hanging indefinitely.

Outside tmux, all phases run inline in the current terminal.

---

<a id="output-files"></a>
## Output files

### Scratchpad (`<repo-root>/scratchpad/<ticket>/`) — source of truth for the run

Every phase reads and writes here directly. `sync_artifact()` (`migite.d/helpers.sh`) mirrors
each file out to the vault immediately after every write, edit, refine, or redo, so the vault
copy never lags behind.

| File | Contents |
|------|----------|
| `intake.md` | Filled-in task intake |
| `task.md` | Optional — supplementary details added via [Intake mode](#intake-mode)'s prompt and/or `--attach`, kept separate from `intake.md` |
| `jira-context.md` | Optional — fetched Jira ticket content when `--jira` is used and the fetch succeeds |
| `plan.md` | Implementation plan |
| `amendment-NN.md` | Scoped delta from each `--amend` run — original plan stays untouched |
| `testing-plan.md` | QA/dev verification steps — seed script, curls, teardown. Regenerated in full on every `--amend`, unlike `plan.md` |
| `architecture-critic.md` | Pre-implementation risk findings |
| `implementation.md` | Notes from the implementation session |
| `implementation-stage-N.md` | Per-layer notes, `--staged` mode only |
| `review.md` | Code review verdict and findings |
| `fix-r<N>.md` | Summary of what Claude changed during a commit-gate `f` fix pass |
| `pr-description.md` | Ready to paste into GitHub |
| `plan.json` | Machine-readable envelope beside `plan.md`: critic finding counts and clean flag, open-question count, plan headings, stub retries, refine status, failed explorers, per-tool usage. Derived deterministically from the documents, so it can't disagree with them |
| `review.json` | Machine-readable envelope beside `review.md`: `verdict` (`needs_fixes` / `ready`), reason, typed `findings[]`, per-severity `counts`, per-dimension counts, `source` (`structured` from a schema-validated call, or `markdown` fallback), usage. **This is what the commit gate reads**; deleted before every review run and when you hand-edit `review.md` at the gate |
| `usage.json` | End-of-run summary of every headless model call (by model and by tool: calls, tokens, time, cost). Interactive sessions are not metered |
| `gate-overrides.md` | Only with `gates.commit.policy: strict` — one entry per capital-`Y` approval over blockers, listing what was overridden |
| `.plan.done` | Sentinel written by migite-plan on success |
| `.review.done` | Sentinel written by migite-review on success |
| `.plan-history/` | Timestamped `plan.md` snapshots, one per edit/refine/redo |

If the scratchpad copy of any of the above is missing (cleaned, fresh clone, different
machine), `resume_from_vault()` pulls it back in from the vault mirror before the phase that
needs it runs — see [Resuming a run](#resuming-a-run).

### Vault (`~/dev-log/<org>/<repo>/<ticket>/`) — read-only mirror

A synced copy of every file above (same names, same paths under `$TASK_DIR`), meant for
reading/browsing later — e.g. in Obsidian — not for resuming or working from directly. Safe to
delete the scratchpad copy after merging; the vault mirror keeps the durable record.
`knowledge.md` (one file per repo, not per-ticket) and `migite-improvements.md` (in the migite
tool's own repo) are the exceptions — they live in the vault only, with no scratchpad copy.

See [Vault structure](./vault-structure.md) for the full directory tree.

### Logs (`~/.dev-workflow/logs/`)

| File | Contents |
|------|----------|
| `<ts>-<ticket>-rubocop.txt` | Pre-review rubocop output |
| `<ts>-<ticket>-rubocop-final.txt` | Post-review rubocop output |
| `<ts>-<ticket>-rspec.txt` | Rspec output |
| `<ts>-<ticket>-heal-rubocop.txt` | Heal loop rubocop |
| `<ts>-<ticket>-heal-rspec.txt` | Heal loop rspec |
| `<ts>-<ticket>-heal-fix-N.txt` | Claude's heal output per attempt |
| `<ts>-<ticket>-critic.txt` | Architecture critic raw output |
| `<ts>-<ticket>-knowledge.txt` | Raw knowledge extraction |
| `<ts>-<ticket>-improvements.txt` | Raw self-improvement notes |
| `<ts>-prompt-<label>.txt` | Every prompt sent to interactive phases |
| `<ts>-wrapper-<label>.sh` | tmux wrapper scripts |
| `<ts>-usage.jsonl` | The run's usage ledger — one JSON line per headless `claude --print` call (tool, label, model, tokens, cost, duration, ok). Source for `usage.json` and the gate banner's running cost. Override the path with `MIGITE_USAGE_LEDGER` |

---

<a id="commit-gate-banner"></a>
## The commit gate banner

```
── Commit context ──────────────────────────
  ⚠ Ruby version error — bundle exec could not run. Fix .tool-versions before approving.
  Verdict: NEEDS FIXES
  Specs:   ⚠ 1 failure — check /path/to/rspec.txt before approving
  Rubocop: 3 offense(s) remain
────────────────────────────────────────────
```

| Signal | Source |
|--------|--------|
| Tooling error | `tooling_failed()` matched either log: `No version is set for command`, `Bundler::GitError` / `not yet checked out`, or `0 examples` alongside a DB connection or load error — the tool never ran, so all results below are untrustworthy. Re-evaluated on every commit-gate re-check |
| Verdict | Read from the `## Verdict` section of review.md by `review_verdict()` (`helpers.sh`) — `NEEDS FIXES`/`NEEDS CHANGES` → red, `READY TO COMMIT`/`READY TO MERGE`/`APPROVED` → green, anything else → "unknown". Anchored on the heading on purpose: the review format's `## Brakeman: PASS` line sits above the verdict, and a whole-file keyword grep used to match it first and show a green verdict on `NEEDS FIXES` reviews |
| Spec failures | Failure count, DB connection failure, load errors, `0 examples`, or `skipped` — "all passed" is only claimed when examples actually ran |
| Rubocop state | Offense count from the post-review re-run |
| Findings / Reason | From `review.json`: critical / warning / note counts and the one-line reason the verdict was decided. Only shown when the envelope exists (i.e. not after a hand-edit of `review.md`) |
| Cost | Running total of headless model calls from the usage ledger — interactive sessions aren't metered |

| Key | Action |
|-----|--------|
| `y` | Commit — continue to Phase 3.5, even if the banner above shows blockers. There's no confirmation step beyond the keypress itself |
| `f` | Claude fixes the findings in an interactive session, then checks + review re-run automatically |
| `e` | Open `review.md` in `$EDITOR` to read and annotate before deciding |
| `n` | Manual fix — pauses for you, then re-runs checks + review when you're ready |
| `q` | Abort |

---

<a id="testing-plan-requirement"></a>
## Testing Plan requirement

The QA/dev verification steps — seed script, curls or browser actions, teardown — live in their own `testing-plan.md`, not inside `plan.md`. This is deliberate: `plan.md` is history (amendments accumulate beside it, never overwriting it), but the testing plan describes how to verify the code **as it exists right now**. If it lived inside `plan.md`, every amendment that changed behaviour would leave it silently describing the pre-amendment version.

`migite-plan` writes `testing-plan.md` once during Phase 1, from the finished plan. Every `--amend` run regenerates it **in full** (not appended) from the current testing plan + the new amendment + the diff, so a step an amendment invalidates gets rewritten or dropped instead of lingering. `migite-review`'s `testing_plan` specialist reads this file directly (not `plan.md`) and returns `NEEDS FIXES` if it's missing, empty, or placeholder-only — and if the task has amendments, checks that the steps match current behaviour, not the original plan's.

Required shape:

````markdown
# Testing Plan

### Prerequisites — seed records (Rails console)
```ruby
district = District.find_by!(subdomain: "qa-district")
user = User.create!(email: "test.user@example.com", ...)
puts "Seeded: user=#{user.id}"
```

### Verification steps
1. Hit the endpoint: `curl -X POST https://localhost:3000/api/v1/... | jq`
2. Check the log: `grep "EventName" log/development.log | tail -5`

### Teardown
```ruby
User.find_by(email: "test.user@example.com")&.destroy
```
````

Only generic emails (`test.user@example.com`, `admin.qa@example.com`) — never real addresses.

One gap worth knowing: regeneration only happens on `--amend` (and on a full plan `n`-redo at the Phase 1 gate). The lightweight `f`/`e` plan-gate edits — feedback refine and direct `$EDITOR` edits, both pre-implementation — don't touch `testing-plan.md`, since nothing has been built yet for it to verify at that point.

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
