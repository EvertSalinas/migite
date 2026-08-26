# Migite

> 右手 (みぎて) — right hand. The trusted assistant that handles the groundwork so you can focus on what matters.

Migite is a personal agentic dev workflow orchestrator. It wraps Claude around every task: plan before you touch a file, gate before you implement, review before you commit, and capture knowledge so nothing gets lost. It doesn't replace your judgment — it extends your reach.

---

<a id="contents"></a>
## Contents

- [Architecture](#architecture)
  - [Workflow orchestrator](#workflow-orchestrator)
  - [Standalone tools at a glance](#standalone-tools-glance)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [`--type` values](#type-values)
  - [Amend mode](#amend-mode)
  - [Intake mode](#intake-mode)
  - [Blueprint mode](#blueprint-mode)
  - [Audit mode](#audit-mode)
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
- [Standalone tools (reference)](#standalone-tools)
  - [`migite-blueprint`](#migite-blueprint)
  - [`migite-explore`](#migite-explore)
  - [`migite-audit`](#migite-audit)
  - [`migite-pr-review`](#migite-pr-review)
- [Internal LangGraph scripts](#internal-langgraph-scripts)
  - [`migite-plan`](#internal-migite-plan)
  - [`migite-review`](#internal-migite-review)
- [Vault structure](#vault-structure)
- [Troubleshooting](#troubleshooting)
  - [Permission failures running bundler / rubocop / rspec / brakeman](#troubleshooting-permissions)
  - [Tooling preflight](#troubleshooting-preflight)
  - [Review keeps saying NEEDS FIXES](#troubleshooting-needs-fixes)

---

<a id="architecture"></a>
## Architecture

Migite is a **hybrid orchestrator**: a bash spine that coordinates human gates, file management, and tool runs, with Python LangGraph agents handling the reasoning-heavy phases.

<a id="workflow-orchestrator"></a>
### Workflow orchestrator (`migite`)

```
bash (migite, sourcing migite.d/*.sh)
├── Phase 1   ──► migite-plan (LangGraph)     — parallel codebase exploration → plan + critic       [migite.d/plan.sh]
├── Phase 1.5 ──► claude interactive          — TDD spec writing (opt-in)                           [migite.d/plan.sh]
├── Phase 2   ──► claude interactive          — implementation                                      [migite.d/implement.sh]
├── Phase 2.5 ──► claude --print loop         — auto-heal: rubocop + rspec → fix → repeat            [migite.d/implement.sh]
├── Phase 3   ──► migite-review (LangGraph)   — 4 parallel specialist reviewers → verdict            [migite.d/review.sh]
├── Phase 3.5 ──► claude --print              — knowledge extraction                                [migite.d/deliver.sh]
├── Phase 4   ──► claude interactive          — PR description                                      [migite.d/deliver.sh]
└── Phase 4.5 ──► claude --print              — self-improvement notes                              [migite.d/deliver.sh]
```

Human gates sit between Phase 1→2 and Phase 3→3.5. Everything else is autonomous. `--amend` mode (`migite.d/amend.sh`) replaces Phase 1 with a scoped-delta flow against an already-planned, already-built task; Phases 2 onward run the same either way.

<a id="standalone-tools-glance"></a>
### Standalone tools at a glance

| Command | Purpose |
|---------|---------|
| `migite-blueprint` | Start a new project — brief → 5 analysts → blueprint + milestone intakes + knowledge seed |
| `migite-explore` | Scope a large initiative on an existing repo — 6 lenses → feasibility doc + verdict, no implementation |
| `migite-audit` | Audit an existing codebase — 7 parallel specialist auditors → ranked findings |
| `migite-pr-review` | Review a teammate's PR — diff a local branch → 4 parallel reviewers → verdict |

**Which tool for which situation:**

| Situation | Tool |
|-----------|------|
| No repo yet — defining a new project | `migite-blueprint` |
| Repo exists, big initiative, unsure if it's worth doing | `migite-explore` |
| Repo exists, work is decided, ready to build | `migite` |
| Want to know what's wrong with the codebase | `migite-audit` |
| Reviewing someone else's branch | `migite-pr-review` |

---

<a id="repository-layout"></a>
## Repository layout

```
migite/                   ← wherever you clone this repo
├── migite                ← workflow orchestrator entrypoint (bash) — config, arg parsing, phase sequencing
├── migite.d/              ← phase fragments sourced by migite, in run order
│   ├── helpers.sh         ← output/prompt helpers + shared DRY helpers (sync_artifact, run_rubocop_check, detect_app_root, bundle_exec, resolve_path, ...)
│   ├── amend.sh           ← --amend mode (scope a delta against an already-built task)
│   ├── plan.sh            ← Phase 1 (plan) + Phase 1.5 (TDD red phase)
│   ├── implement.sh       ← Phase 2 (implement, staged or single-session) + Phase 2.5 auto-heal loop
│   ├── review.sh          ← Phase 3 (rubocop/rspec + migite-review) + commit gate
│   └── deliver.sh         ← Phase 3.5 (knowledge capture) + Phase 4 (PR description) + Phase 4.5 (self-improvement)
├── migite-plan           ← LangGraph planner agent (Python) — called by migite Phase 1
├── migite-review         ← LangGraph reviewer agent (Python) — called by migite Phase 3
├── migite-blueprint      ← standalone project definition tool, bash wrapper
├── migite-blueprint.py   ← LangGraph blueprint agent (Python)
├── migite-explore        ← standalone initiative feasibility tool, bash wrapper
├── migite-explore.py     ← LangGraph exploration agent (Python)
├── migite-audit          ← standalone codebase auditor, bash wrapper
├── migite-audit.py       ← LangGraph audit agent (Python)
├── migite-pr-review      ← standalone PR reviewer, bash wrapper
├── migite-pr-review.py   ← LangGraph PR review agent (Python)
├── migite_paths.py       ← shared run-directory resolver (id/name → vault folder)
├── migite-improvements.md ← self-improvement notes appended after each run
└── README.md
```

`migite.d/` holds the orchestrator's own logic, split by phase so no single file runs past a few hundred lines. `migite` sources each fragment on startup and calls its phase function (`run_plan`, `run_implement`, ...) — they all share `migite`'s variables (`TASK_DIR`, `PLAN_FILE`, `REVIEW_FILE`, ...) directly rather than taking them as arguments, so `migite.d/*.sh` only makes sense read alongside `migite` itself, not standalone. It's not a standalone tool like `migite-plan`/`migite-review`, so it isn't symlinked or listed in "Standalone tools" below.

`~/.local/bin/` holds symlinks to every top-level file above except README.md and migite-improvements.md. `migite.d/` is not symlinked separately — `migite` resolves its own real path (through the symlink) to find `migite.d/` alongside it, so the directory just needs to stay next to `migite` in this repo.

**Gemfile-subdirectory detection.** `migite` always `cd`s to `$REPO_ROOT` right after computing it, so `git diff` and every other command run from a consistent directory regardless of where `migite` was invoked from — but Bundler only searches upward from cwd for a `Gemfile`, never into subdirectories. When the actual Ruby app lives one level down (e.g. a `rails-app/` folder alongside other tooling in the same repo), `detect_app_root()` finds it and sets `$APP_ROOT`; every `bundle_exec` call then `cd`s there first, and `strip_app_prefix()` rewrites the repo-root-relative paths `git diff` produces into `$APP_ROOT`-relative ones before handing them to rubocop/rspec. `resolve_path()` absolutizes user-supplied file paths (`--audit`, `--blueprint`, `--intake`, `--amend-file`) before that `cd`, so they still resolve correctly afterward even if given relative to wherever you ran `migite` from.

---

<a id="requirements"></a>
## Requirements

### Core

| Tool | Purpose |
|------|---------|
| `claude` (Claude Code CLI) | All AI phases — auth is shared, no separate API key needed |
| `git` | Branch detection, diff scoping |
| `bundle` | Rubocop + rspec |
| Python 3.11+ | LangGraph agent scripts |
| `langgraph`, `anthropic` Python packages | Plan and review agents |

### Files expected

- Vault root: `~/dev-log/` — plain markdown files, created automatically on first run. No note-taking app required; point `DEV_LOG_BASE` at an existing Obsidian vault (or anywhere else) if you have one and want the `[[wikilinks]]` to resolve.
- Intake templates: `~/.claude/templates/{feature,bug,refactor,spike,config}.md`
- Phase prompts: `~/.claude/commands/{plan,implement,review,commit,architecture_critic}.md`

---

<a id="installation"></a>
## Installation

Clone it anywhere. Commands are exposed via symlinks in `~/.local/bin/`.

```bash
# 1. Clone to wherever you keep source
git clone <this-repo> ~/Code/migite

# 2. Symlink all scripts onto your PATH
MIGITE_SRC="$HOME/Code/migite"   # match wherever you cloned it in step 1
BIN="$HOME/.local/bin"
for f in migite migite-plan migite-review \
          migite-blueprint migite-blueprint.py \
          migite-explore migite-explore.py \
          migite-audit migite-audit.py \
          migite-pr-review migite-pr-review.py \
          migite_paths.py; do
  ln -sf "$MIGITE_SRC/$f" "$BIN/$f"
done
chmod +x "$MIGITE_SRC"/migite "$MIGITE_SRC"/migite-blueprint \
         "$MIGITE_SRC"/migite-explore \
         "$MIGITE_SRC"/migite-audit "$MIGITE_SRC"/migite-pr-review

# 3. Make sure ~/.local/bin is on your PATH (add to ~/.zshrc if needed)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# 4. Install Python dependencies (one-time)
pip3 install langgraph anthropic
```

If you use asdf for Python version management, set `MIGITE_PYTHON` to the full binary path:

```bash
export MIGITE_PYTHON="$HOME/.asdf/installs/python/3.13.5/bin/python3"
```

Add that export to `~/.zshrc` to make it permanent.

---

<a id="configuration"></a>
## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MIGITE_PYTHON` | `$HOME/.asdf/installs/python/3.13.5/bin/python3` | Python binary for LangGraph scripts |
| `DEV_LOG_BASE` | `~/dev-log` | Vault root |
| `MIGITE_ORG` | unset | Forces the vault "org" bucket a repo files under (`$DEV_LOG_BASE/<org>/<repo>/...`). Unset, it's auto-detected as the name of the directory directly containing the repo — `~/Code/Acme/foo` → `Acme` — so any org/client folder name works without configuration. Falls back to `Personal` only if the repo has no meaningful parent directory |
| `LOG_DIR` | `~/.dev-workflow/logs` | Per-run logs |
| `MAX_HEAL_ATTEMPTS` | `3` | Phase 2.5 auto-heal retry cap |
| `MIGITE_PERMISSION_MODE` | unset | Permission mode passed to `claude --print` — but only inside three standalone tools: `migite-explore`, `migite-audit`, `migite-pr-review`. Omitted (fail closed) when unset. `migite` itself, `migite-plan`, `migite-review`, and `migite-blueprint` don't read this variable at all — see [Permission failures](#troubleshooting-permissions) |
| `EDITOR` | `vim` | Opens intake template and other manual-edit prompts |

Override in your shell profile:

```bash
export EDITOR=nvim
export MIGITE_PYTHON="/opt/homebrew/bin/python3"
export DEV_LOG_BASE="$HOME/Notes/dev-log"   # or your Obsidian vault's dev-log folder
export MIGITE_ORG="Personal"                # optional: force one bucket regardless of folder name
```

---

<a id="usage"></a>
## Usage

```bash
# Plain task description
migite "add PDF export to invoices"

# Atlassian URL — ticket key extracted automatically
migite "https://yourcompany.atlassian.net/browse/BB-3347"

# Jira key directly
migite --jira BB-3347

# Pre-specify type to skip the interactive prompt
migite --jira BB-3347 --type feature
migite "fix N+1 on district index" --type bug

# Audit mode — feed a migite-audit report directly into planning
migite --audit ~/dev-log/Work/my-repo/audit-2026-08-11.md

# Audit mode + Jira
migite --audit ./audit.md --jira BB-1234

# Pre-written intake from migite-explore or migite-blueprint
migite --intake ./exploration-agent-agnostic-2026-08-18/intake-01-extract-provider-adapter.md

# Same thing — a positional .md file is auto-detected as an intake
migite ./intake-01-foundation.md

# Blueprint context — inject a migite-blueprint output into planning
migite --jira BB-3347 --blueprint ./output/blueprint.md

# Staged implementation — checkpoint gate between plan scope layers
migite "add billing API" --staged
migite --jira BB-3347 --staged

# Amend an already-implemented task with post-implementation feedback
migite --amend "reviewer says the service must be idempotent on retry"
migite --amend-file ./qa-notes.md
```

<a id="type-values"></a>
### `--type` values

| Value | When to use |
|-------|-------------|
| `feature` | New behaviour, new endpoint, new model |
| `bug` | Fix a regression or reported defect |
| `refactor` | Internal restructure, no behaviour change |
| `spike` | Investigation or proof of concept |
| `config` | Infrastructure, environment, or gem changes |

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
migite --amend "fix the retry path" --jira BB-3347
```

**How the task is found**, in order:

| Step | Behaviour |
|------|-----------|
| `--jira BB-3347` | Uses that ticket's vault directory |
| Branch name | Extracts a ticket key from the branch (`feature/BB-3347-add-pdf` → `bb-3347`) |
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
- **Grounded in the diff, not a re-exploration.** The amendment prompt sees the actual built code, so it can say "`Presend::Translator` already handles this, only the retry path changes" instead of re-planning greenfield.
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
next to `intake.md` in the vault. It's deliberately a **separate file, never merged into the
passed intake** — the same reasoning as amendments staying beside `plan.md` instead of rewriting
it: the original `--intake` file stays exactly what `migite-explore`/`migite-blueprint` produced
(or whatever you were handed), and `task.md` is clearly your own addition on top of it.
`migite-plan` reads both and treats `task.md` as authoritative context for anything it adds.
Leaving the editor empty (or answering anything but `y`) skips it — no `task.md` is created.

<a id="blueprint-mode"></a>
### Blueprint mode (`--blueprint`)

When you have a `migite-blueprint` output, pass it to `migite` with `--blueprint` to give the planner pre-decided architectural context:

```bash
migite --jira BB-3347 --blueprint ~/dev-log/Personal/my-project/blueprint/blueprint.md
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
    synthesize_plan   (Sonnet 5)
         │
    architecture_critic   (Opus 5 — highest-stakes call, one per run)
         │
    refine_plan   (Sonnet 5, incorporates critic findings)
         │
    generate_testing_plan   (Sonnet 5, standalone QA/dev verification doc)
         │
    write_outputs   → plan.md + architecture-critic.md + testing-plan.md + sentinel
```

Explorers use Haiku 4.5 for fast file analysis. Each reads changed files first (from `git diff <base branch>`), then scores existing files by keyword relevance from the intake. Plan synthesis and refinement use Sonnet 5. The architecture critic uses Opus 5 — it is the single highest-stakes call in the planner, where a missed finding propagates into implementation. `generate_testing_plan` writes `testing-plan.md` as its own file rather than a section of the plan — see [Testing Plan requirement](#testing-plan-requirement) for why.

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

Every phase computes its own changed-file list from `git diff <base branch> --name-only`, filtered by extension (`\.rb$`, `_spec\.rb$`) — there isn't one shared helper, and the phases aren't fully consistent with each other:

| Phase | Diff filter | No-spec-files behaviour |
|-------|-------------|--------------------------|
| Phase 1.5 TDD red-state check (`plan.sh`) | `--diff-filter=ACMR` (deletions excluded) | skips the check, warns |
| Phase 2.5 auto-heal loop (`implement.sh`) | `--diff-filter=ACMR` (deletions excluded) | skips rspec and says so |
| Phase 3 review + commit-gate re-checks (`review.sh`) | `--diff-filter=ACMR` (deletions excluded) | **falls back to the full suite** |

| Rule | Why |
|------|-----|
| Base branch is auto-detected | `origin/HEAD`, then `main` / `master` / `develop`. Hardcoding `main` silently produced empty diffs on master-based repos, so rubocop was skipped for the wrong reason |
| Deleted files excluded (`ACMR`) everywhere | Stale paths caused rubocop `No such file or directory` and rspec load errors — Phase 3 didn't apply this filter until it was caught and fixed |
| `bundle exec` runs from the app's actual root, not necessarily the repo root | Bundler only searches upward from cwd for a Gemfile. When the Ruby app lives one level down (e.g. a `rails-app/` subdirectory alongside other tooling), `detect_app_root` (`helpers.sh`) finds it and every `bundle_exec` call `cd`s there first — otherwise `git diff`'s repo-root-relative paths get re-resolved against the wrong directory and rubocop reports files missing |

Untracked (never-`git add`ed) files are **not** included in any of these diffs — there's no `git ls-files --others` call anywhere in migite. A brand-new file that hasn't been staged yet is invisible to rubocop/rspec/review until you `git add` it.

Phase 3 also runs a separate, different check: any file in the diff whose basename doesn't appear anywhere in `implementation.md` gets flagged as a warning before the review runs, so undocumented changes get caught before the reviewer sees them — a diff-vs-notes cross-reference, not an untracked-file check.

**Known inconsistency:** Phase 2.5 skips rspec (and says so) when no spec files changed, but Phase 3's review and its commit-gate re-checks still fall back to running the full suite in that case — the older behavior Phase 2.5 was specifically changed to avoid, for the same reason (it requires a live DB and verifies nothing relevant to the diff). Phase 3 hasn't been brought in line with that fix yet.

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

Use `f` when the review found something real and the fix is straightforward enough for Claude to handle. Use `e` when you want to read and annotate the review before acting. Use `n` when the fix involves a judgment call, a schema change, or something that needs your direct decision. Both `f` and `n` re-run the full review afterwards through the same helper — there's no cap on how many times you can loop through this, and no confirmation beyond the single `y` keypress required to approve over a `NEEDS FIXES` verdict or remaining rubocop offenses. You always get a fresh verdict before committing if you choose `f` or `n`, but nothing currently stops `y` from being pressed on the first pass regardless of what the banner says.

<a id="phase-3-5-knowledge"></a>
### Phase 3.5 — Knowledge capture

A background `claude --print` pass extracts 1–3 reusable bullets from the completed run (plan + implementation + review) and appends them to `knowledge.md`. Entries link back to the review via Obsidian wikilinks.

Only domain-level insights are captured: business logic clarifications, non-obvious constraints, architectural decisions. Rails conventions and testing patterns are excluded.

<a id="phase-4-pr-description"></a>
### Phase 4 — PR description (interactive)

Claude generates a PR description from the plan and review. Output goes to `pr-description.md` — ready to paste into GitHub.

<a id="phase-4-5-self-improvement"></a>
### Phase 4.5 — Self-improvement

A background `claude --print` pass reviews the full run (including the migite script itself) and appends 0–3 actionable observations to `migite-improvements.md` in this repo. Observations must be grounded in what happened during the run — no generic suggestions.

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

### Vault (`~/dev-log/<org>/<repo>/<ticket>/`)

| File | Contents |
|------|----------|
| `intake.md` | Filled-in task intake |
| `task.md` | Optional — supplementary details added via [Intake mode](#intake-mode)'s prompt, kept separate from `intake.md` |
| `plan.md` | Implementation plan |
| `amendment-NN.md` | Scoped delta from each `--amend` run — original plan stays untouched |
| `testing-plan.md` | QA/dev verification steps — seed script, curls, teardown. Regenerated in full on every `--amend`, unlike `plan.md` |
| `architecture-critic.md` | Pre-implementation risk findings |
| `implementation.md` | Notes from the implementation session |
| `review.md` | Code review verdict and findings |
| `fix-r<N>.md` | Summary of what Claude changed during a commit-gate `f` fix pass |
| `pr-description.md` | Ready to paste into GitHub |

### Scratchpad (`<repo-root>/scratchpad/<ticket>/`)

Live copies synced during the run. Safe to delete after merging.

| Extra files | Purpose |
|-------------|---------|
| `.plan.done` | Sentinel written by migite-plan on success |
| `.review.done` | Sentinel written by migite-review on success |

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
| Tooling error | `No version is set for command`, `Bundler::GitError`, `not yet checked out`, or a missing gem in either log — the tool never ran, so all results below are untrustworthy |
| Verdict | Extracted from review.md: `NEEDS FIXES` / `APPROVED` / `PASS` / `READY TO COMMIT` |
| Spec failures | Failure count, DB connection failure, load errors, `0 examples`, or `skipped` — "all passed" is only claimed when examples actually ran |
| Rubocop state | Offense count from the post-review re-run |

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
| `plan.md` exists in vault | Offers `[u]se existing` or `[r]edo` |
| `.plan.done` sentinel missing after agent | Hard error on the initial plan generation; only a warning (gate still opens) if it's missing after an `n`-redo from the plan gate |
| `.review.done` sentinel missing after agent | Warning — review output may be incomplete |

---

<a id="standalone-tools"></a>
## Standalone tools

<a id="migite-blueprint"></a>
### `migite-blueprint`

Defines a new project from a brief before any code exists. Runs 5 parallel specialist analysts
(domain model, user roles & flows, API surface, tech conventions, risks), synthesises a complete
blueprint with Opus 5, then extracts sequenced milestone intake files and a knowledge.md seed.

**Stack-agnostic:** a new project has no code yet for the tool to read, so it determines a
stack up front — either taken verbatim from `--stack`, or inferred from the brief (its own
Sonnet 5 call) — and threads it directly through every analyst prompt and the synthesis prompt.
None of those prompts hardcode Rails; they ask for whatever's idiomatic to the chosen stack (a
Rails model, a Prisma relation, a Django `ForeignKey`, `npm run lint` vs `bundle exec rubocop`,
etc.). Milestone/knowledge extraction don't receive the stack directly — they work from the
already-stack-aware `blueprint.md` text instead.

```bash
# One-liner brief — prompts for project name, stack inferred from the brief
migite-blueprint "build a billing API for a SaaS product"

# No args at all — fully interactive, prompts for both the brief and the project name
migite-blueprint

# Brief from file
migite-blueprint --brief path/to/brief.md --name billing-api

# State the stack explicitly instead of letting it be inferred
migite-blueprint --brief brief.md --name billing-api --stack "Next.js + TypeScript + Postgres"

# Write to a specific directory
migite-blueprint --brief brief.md --name billing-api --output ./billing-api/

# Re-extract milestone intakes AND knowledge.md from an already-generated (and edited) blueprint
migite-blueprint --from-blueprint ./output/blueprint.md
```

**What it produces** (all in the output directory):

| File | Contents |
|------|----------|
| `blueprint.md` | Domain model, user flows, API surface, tech decisions, MVP scope, risks, milestones |
| `knowledge.md` | Seed conventions and decisions — drop into `dev-log/<org>/<repo>/` before the first migite run |
| `intake-01-<slug>.md` | Migite intake for milestone 1 (Foundation) |
| `intake-02-<slug>.md` | Migite intake for milestone 2 |
| `intake-NN-<slug>.md` | … one file per milestone, ready to pass to `migite` |
| `milestones-raw.md` | Only written if the extraction output couldn't be parsed into milestone blocks — the raw text lands here instead of being silently dropped |

**Recommended workflow:**

```
1. migite-blueprint "your brief"     → blueprint.md + intake-NN-*.md
2. Edit blueprint.md to your taste
3. Create a fresh repo for your chosen stack (e.g. `rails new <project>` for Rails)
4. cp knowledge.md dev-log/<org>/<repo>/knowledge.md
5. migite --intake intake-01-foundation.md   → implement milestone 1
6. migite --intake intake-02-auth.md         → implement milestone 2
   ...
```

If you edited `blueprint.md` and want to regenerate the intakes AND `knowledge.md` from the updated version — this rewrites `blueprint.md` too (unchanged content), and defaults `--output` to the blueprint file's own directory when not given:
```bash
migite-blueprint --from-blueprint ./output/blueprint.md
```

**Models used:** `claude-sonnet-5` for stack inference (when `--stack` isn't given), the 5 parallel
analysts, milestone extraction, and knowledge-seed extraction; `claude-opus-5` for blueprint
synthesis (the most consequential call — shapes everything downstream).

**Default output:** `~/dev-log/Personal/<project-name>/blueprint/`, where
`<project-name>` is the sanitized project name (lowercased, slugified), not necessarily what you
typed verbatim.

---

<a id="migite-explore"></a>
### `migite-explore`

Scopes a large initiative against an existing codebase **without implementing anything**. Use it
when you want to know what a big change would actually take before committing to it - the output
is a decision artefact, not a plan.

Like `migite-blueprint`, `migite-explore` is **language-agnostic** - but it gets there differently.
It discovers files with `git ls-files` and ranks them by keyword relevance to your brief, so it
works on any repo - Rails, Python, a shell toolchain, anything under git - because there's an
existing codebase to read. `migite-blueprint` has no code to read yet, so it determines a stack
up front instead (see below) rather than discovering one.

```bash
# Inline brief
migite-explore "make migite agent-agnostic across claude code and opencode"

# Brief from a file, with a short slug for the output directory
migite-explore --brief ./initiative.md --name agent-agnostic

# Group this exploration under an existing ticket's vault folder
migite-explore --brief ./initiative.md --id BB-1234

# Also emit one migite intake per workstream, ready to feed back into migite
migite-explore "extract billing into a service" --intakes

# Run a single lens instead of all six
migite-explore "add multi-tenancy" --focus approaches

# Interactive - prompts for the brief
migite-explore

# Re-extract intakes from an existing (or hand-edited) exploration - 1 call, not 9
migite-explore --from-exploration ~/dev-log/Work/my-repo/exploration-billing-2026-08-18/exploration.md
```

**The graph:**

```
load_context   (git ls-files → keyword rank → shared repo context)
    │
    ├── lens: current_state       (how the repo does this today)
    ├── lens: touchpoints         (every file that would change, classified)
    ├── lens: coupling_and_risk   (what breaks, what has no test cover)
    ├── lens: existing_seams      (abstractions already there to build on)
    ├── lens: constraints         (contracts, compat, tooling, docs)
    └── lens: approaches          (2-3 distinct options, no winner picked)
         │
    synthesize_exploration   (Opus 5 — the feasibility document)
         │
    challenge_assumptions    (Opus 5 — adversarial: attacks the document)
         │
    refine_exploration       (Sonnet 5 — incorporates the challenges)
         │
    extract_workstreams      (Sonnet 5 — only with --intakes)
         │
    write_outputs   → exploration.md + challenges.md [+ intake-NN-*.md]
```

All six lenses share one repo context but apply a different question to it — an initiative cuts
across the whole repo, so splitting by directory (the `migite-plan` approach) would fragment the
analysis. Lenses are instructed to output `UNKNOWN: <what I'd need to look at>` rather than guess;
every unknown must surface in the final Open Questions section. `--focus` matches lens names by
case-insensitive substring — an unmatched value warns and falls back to running all six rather
than erroring. If a lens, the challenge pass, or the refine pass fails outright, migite-explore
degrades that step to a placeholder/unrefined-draft instead of crashing the whole run. Repo
context is capped (~22,000 chars total, ~4,000/file, top 30 ranked files; the challenge pass sees
a further-truncated 12,000-char slice) with no warning surfaced when a large repo/initiative gets
trimmed — a known limitation, not a bug, but worth knowing on a big repo.

The **challenge pass** is what separates this from a plan. Feasibility documents fail in
predictable ways — missed touchpoints, underestimated effort, hand-waved hard parts, a confident
verdict from thin evidence. A second Opus 5 pass attacks the draft on seven axes and the document
is revised against those findings.

**What it produces:**

| File | Contents |
|------|----------|
| `exploration.md` | Verdict, current state, what-would-change inventory, approach options, workstreams, open questions, decision points, risks, effort summary |
| `challenges.md` | The adversarial review — what the first draft got wrong |
| `intake-NN-<slug>.md` | One migite intake per workstream (only with `--intakes`) |
| `workstreams-raw.md` | Only written if workstream extraction couldn't be parsed into intake blocks — the raw text lands here instead of being silently dropped |

`--output`, when given, always wins over `--id`/`--name` — no resolver lookup happens at all in that
case. Reusing an existing `--name` creates a *new* `<slug>/explore-<timestamp>/` folder alongside
any older `exploration-<slug>-<date>/` folder from before this change — the two aren't merged
automatically.

**The verdict line** is one of four, and is greppable:

| Verdict | Meaning |
|---------|---------|
| `✅ PROCEED` | The path is clear enough to plan and implement |
| `🔬 SPIKE FURTHER` | Unknowns must be resolved before committing |
| `⏸️ DEFER` | Feasible, but the cost outweighs current value |
| `❌ NOT WORTH IT` | Coupling or cost makes this a bad investment |

**Effort scale:** `S` under a day · `M` 1-3 days · `L` 1-2 weeks · `XL` 2+ weeks or unbounded until a spike lands.

**Recommended workflow:**

```
1. migite-explore "the initiative"              → exploration.md + challenges.md
2. Read exploration.md, check challenges.md for what the first draft missed
3. Resolve the Open Questions and Decision Points yourself - edit exploration.md directly
4. migite-explore --from-exploration <path>     → workstream intakes from your edited doc
5. If verdict is ✅ PROCEED:
     migite --intake intake-01-<slug>.md        → plan + implement workstream 1
     migite --intake intake-02-<slug>.md        → …then the next workstream
   If verdict is 🔬 SPIKE FURTHER:
     migite "<the spike>" --type spike          → resolve the unknown, then re-explore
```

Pass `--blueprint`-style context too if the exploration should inform planning beyond the intake:

```bash
migite --intake intake-01-extract-provider-adapter.md \
       --blueprint ./exploration-agent-agnostic-2026-08-18/exploration.md
```

### Re-extracting intakes (`--from-exploration`)

A full run is ~9 model calls (10 with `--intakes`), two of them Opus 5 with extended thinking.
Re-extraction is **one Sonnet 5 call** — it reads an existing `exploration.md` and regenerates
only the workstream intakes.

Use it when you triaged without `--intakes` and now want them, or when you edited the workstreams
by hand and want the intakes to match:

```bash
# Edit the workstreams first, then re-extract against your edits
$EDITOR ~/…/exploration-billing-2026-08-18/exploration.md
migite-explore --from-exploration ~/…/exploration-billing-2026-08-18/exploration.md
```

| Behaviour | Detail |
|-----------|--------|
| Reads | `## Workstreams` section of the exploration document |
| Writes | `intake-NN-<slug>.md` only |
| Never touches | `exploration.md` (your input) and `challenges.md` (the original run's output) |
| Output dir | Defaults to the directory containing the exploration file; overridden by `--output`; if `--id` is passed instead, goes to that ticket's `explore-<timestamp>/` folder regardless of where the source file lives |
| Git repo | Not required when working directly on a file in the vault — **required if `--id` is also passed**, to resolve org/repo for the ticket folder |

Warns if the document has no `## Workstreams` section rather than silently producing nothing.

If a repo `knowledge.md` exists in the vault, it is injected into every lens automatically on full runs.

**Models used:** `claude-sonnet-5` for the 6 lenses (exploration quality is the whole product —
there is no implementation phase downstream to catch a shallow read), `claude-opus-5` for both
synthesis and the adversarial challenge, `claude-sonnet-5` for refinement and workstream extraction.

**Default output:** no `--id`/`--name` — `~/dev-log/<org>/<repo>/exploration-<slug>-<date>/`.
With `--id BB-1234` or `--name <slug>` — `~/dev-log/<org>/<repo>/<slug>/explore-<timestamp>/`,
grouped alongside any other run already using that folder (only `--id` prefix-matches an existing
sibling; `--name` is exact-match-only).

---

<a id="migite-audit"></a>
### `migite-audit`

Audits an existing codebase for architectural problems. Runs 7 parallel specialist auditors
(models, controllers, services, serializers, jobs, migrations, `schema_indexes`), synthesises
findings ranked by severity, and writes an audit report to the vault.

```bash
# Full audit — writes to ~/dev-log/<org>/<repo>/audit-<date>.md
migite-audit

# Limit to a specific layer
migite-audit --focus jobs
migite-audit --focus controllers

# Group this audit under an existing ticket's vault folder
migite-audit --id BB-1234

# Write to a specific file
migite-audit --output ./audit.md
```

`--focus` matches area names by case-insensitive substring — `--focus schema` or `--focus indexes`
both hit `schema_indexes`, but `--focus "schema indexes"` (with a space) won't. An unmatched value
warns and falls back to auditing everything rather than erroring.

After the audit, feed the report directly into migite planning:
```bash
migite --audit ~/dev-log/<org>/<repo>/audit-<date>.md
```

`--output`, when given, always wins over `--id` — no resolver lookup happens at all in that case.
When a run finds any critical finding, it prints a `migite --type refactor` suggestion alongside
the `N critical / N warnings / N notes` summary.

**Models used:** `claude-haiku-4-5-20251001` for per-area analysis, `claude-sonnet-5` for synthesis.

**Default output:** no `--id` — `~/dev-log/<org>/<repo>/audit-<date>.md`. With
`--id BB-1234` — `~/dev-log/<org>/<repo>/bb-1234/audit-<timestamp>.md`, grouped
alongside any other run already using that ticket folder.

<a id="migite-pr-review"></a>
### `migite-pr-review`

Reviews a teammate's PR using a local branch. Diffs the branch against the merge-base with a base
branch (`git diff base...branch` — only the branch's own commits, not everything on base since
divergence), runs rubocop and rspec on changed files (optional), then fans out 4 parallel
specialist reviewers (correctness, security, test coverage, conventions & migrations — the fourth
dimension's checklist is migration-heavy: missing `NOT NULL` defaults, reversibility, missing
`add_index`, non-idempotent jobs). Produces a review with a clear APPROVED / APPROVED WITH
COMMENTS / NEEDS CHANGES verdict. The branch must be checked out locally — it errors if the diff
comes back empty, which is the common failure mode when reviewing someone else's unfetched
branch. A single reviewer dimension failing doesn't crash the run; it's downgraded to a synthetic
Critical finding so the other three still complete. Diff and file-content context are truncated
(diff capped at 16,000 chars, file contents at ~12,000 total/2,500 per file, with further
per-reviewer truncation) with no warning surfaced — a known limitation on very large PRs.

```bash
# Review branch against the repo's default branch (auto-detected: origin/HEAD, then main/master/develop)
migite-pr-review --branch feature/BB-1234-add-pdf-export

# With Jira ticket for reference (key or full URL — both work)
migite-pr-review --branch feature/BB-1234 --jira BB-1234
migite-pr-review --branch feature/BB-1234 --jira https://yourcompany.atlassian.net/browse/BB-1234

# Different base branch
migite-pr-review --branch alex/fix-n-plus-one --base develop

# Skip rubocop + rspec (use when reviewing unfamiliar code or flaky test suite)
migite-pr-review --branch feature/big-refactor --skip-tests

# Write to a specific file
migite-pr-review --branch feature/BB-1234 --output ./review.md
```

**Default output:** no `--jira` — `~/dev-log/<org>/<repo>/pr-review-<branch>-<date>.md`.
With `--jira BB-1234` — `~/dev-log/<org>/<repo>/bb-1234/pr-review-<branch>-<timestamp>.md`,
grouped alongside any other run already using that ticket folder. The branch name has `/` replaced
with `-` in the filename either way. `--output`, when given, always wins over `--jira` — no resolver
lookup happens at all in that case. `--jira` only affects the output path when it parses as a
ticket key or Atlassian URL — a free-text value is still passed into the review as reference
context, just without grouping the output under a ticket folder.

**Models used:** `claude-sonnet-5` for the 4 parallel specialist reviewers, `claude-opus-5` for the final verdict. This tool is read-only — it never commits anything, there's no gate to approve.

---

<a id="internal-langgraph-scripts"></a>
## Internal LangGraph scripts (called by migite)

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
  [--base-branch  master]
```

`--task-file` injects supplementary details from [Intake mode](#intake-mode)'s "add a separate task.md" prompt into `synthesize_plan` as authoritative context alongside the intake — it's optional and only ever set when that prompt produced a file.

`--base-branch` sets the branch explorers diff against. Omitted, it auto-detects from `origin/HEAD`, then falls back to `main` / `master` / `develop`. `migite` passes its own detected value so both agree.

`--blueprint` injects the blueprint content into both `synthesize_plan` and `run_architecture_critic` as pre-decided architecture. The planner and critic treat blueprint decisions as settled constraints rather than re-deriving them from the codebase.

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

**Models used:** `claude-sonnet-5` for the 4 parallel review dimensions, `claude-opus-5` for `synthesize_verdict`.

Exits 0 and touches `--sentinel` on success. Exits 1 on failure.

---

<a id="vault-structure"></a>
## Vault structure

```
~/dev-log/
├── <org>/                                          ← name of the directory that directly contains the repo (or $MIGITE_ORG)
│   └── <repo-name>/
│       ├── knowledge.md                          ← one file per repo, all lessons
│       ├── audit-<date>.md                       ← migite-audit, no --id given
│       ├── pr-review-<branch>-<date>.md          ← migite-pr-review, no --jira given
│       ├── exploration-<slug>-<date>/            ← migite-explore, no --id/--name given
│       │   ├── exploration.md
│       │   ├── challenges.md
│       │   └── intake-NN-<slug>.md               ← only with --intakes
│       └── <ticket>/                             ← grouped by --id / --jira, shared across tools
│           ├── intake.md
│           ├── task.md                           ← optional, from the --intake supplementary prompt
│           ├── plan.md
│           ├── testing-plan.md                   ← regenerated in full on every --amend
│           ├── architecture-critic.md
│           ├── implementation.md
│           ├── review.md
│           ├── pr-description.md
│           ├── audit-<timestamp>.md              ← migite-audit --id <ticket>
│           ├── pr-review-<branch>-<timestamp>.md ← migite-pr-review --jira <ticket>
│           └── explore-<timestamp>/              ← migite-explore --id <ticket>
│               ├── exploration.md
│               ├── challenges.md
│               └── intake-NN-<slug>.md
└── Personal/
    ├── <project-name>/
    │   └── blueprint/
    │       ├── blueprint.md              ← written by migite-blueprint
    │       ├── knowledge.md              ← seed — copy to repo vault before first migite run
    │       ├── intake-01-foundation.md   ← milestone intake files
    │       └── intake-NN-<slug>.md
    └── <repo-name>/
        └── ...
```

`migite` and the standalone tools both resolve `<org>` the same way, via `migite_paths.detect_org()`: `$MIGITE_ORG` if set, otherwise the name of the directory that directly contains the repo (`~/Code/Acme/foo` → `Acme`), falling back to `Personal` only if the repo has no meaningful parent directory. No org names are hardcoded, so this works for any team, client, or personal-project layout without configuration — set `MIGITE_ORG` only if you want to force everything from a given shell into one bucket regardless of folder name.

`migite-audit`, `migite-pr-review`, and `migite-explore` each accept `--id`/`--jira` (a Jira ticket
key or Atlassian URL) to group their output under an existing ticket's folder instead of writing a
flat, disconnected file. `migite_paths.py` is the shared resolver behind this — see its module
docstring for exact match/prefix-match/ambiguous-folder rules. `--output`, when given, always wins
and skips the resolver entirely. `migite` itself does not yet use this resolver.

---

<a id="troubleshooting"></a>
## Troubleshooting

<a id="troubleshooting-permissions"></a>
### Permission failures running bundler / rubocop / rspec / brakeman

`MIGITE_PERMISSION_MODE` is **not** honored uniformly — coverage is currently partial:

| Component | Behaviour |
|---|---|
| `migite-explore`, `migite-audit`, `migite-pr-review` (Python) | Read `MIGITE_PERMISSION_MODE`; pass `--permission-mode <value>` to `claude --print` when set, omit it entirely when unset |
| `migite-plan`, `migite-review` (Python, invoked internally by `migite`) | Don't read the variable at all |
| `migite-blueprint.py` (Python) | Doesn't read the variable at all |
| `migite`'s own interactive sessions (`run_phase`) and heal-loop fixes (`heal_run`) | Hardcode `bypassPermissions`, unconditionally — the variable has no effect here |
| `migite`'s background `claude --print` calls (`thinking()` — knowledge capture, self-improvement, amendment generation/refine) | Pass no `--permission-mode` flag at all, regardless of the variable |

```bash
export MIGITE_PERMISSION_MODE=bypassPermissions   # only affects migite-explore/audit/pr-review
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

If the second command can't run the tool, set a mode your policy does allow (only affects the
three tools that read it):

```bash
export MIGITE_PERMISSION_MODE=acceptEdits
```

Note that **brakeman is not part of migite**. If you're seeing brakeman runs, they come from your
`~/.claude/CLAUDE.md`, a project CLAUDE.md, or a skill — migite never invokes it.

<a id="troubleshooting-preflight"></a>
### Tooling preflight

There's no `bundle check`/rubocop/rspec startup preflight — a broken Ruby toolchain surfaces only
when a phase actually tries to run it (look for `No version is set for command` or a bundler error
in the relevant log). The one real preflight that exists is narrower: before spawning any LangGraph
agent, `spawn_langgraph()` (`helpers.sh`) verifies `import langgraph, anthropic` succeeds in
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
