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
- [Documentation](#documentation)
- [FAQ](#faq)
- [Roadmap](#roadmap)

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

Full phase-by-phase reference: [docs/migite.md](./docs/migite.md).

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

Full reference for all four: [docs/standalone-tools.md](./docs/standalone-tools.md).

---

<a id="repository-layout"></a>
## Repository layout

```
migite/                   ← wherever you clone this repo
├── migite                ← workflow orchestrator entrypoint (bash) — config, arg parsing, phase sequencing
├── migite.d/              ← phase fragments sourced by migite, in run order
│   ├── helpers.sh         ← output/prompt helpers + shared DRY helpers (sync_artifact, run_rubocop_check, detect_stack + stack profiles, bundle_exec, resolve_path, ...)
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
├── templates/            ← intake templates + the PR-description prompt, read by migite.d/plan.sh and deliver.sh
│   ├── feature.md, bug.md, refactor.md, spike.md, config.md  ← intake templates ($TASK_TYPE.md)
│   └── commit.md         ← PR-description generation prompt (Phase 4)
├── docs/                 ← deep-dive reference docs (phases, standalone tools, vault, troubleshooting)
└── README.md
```

`migite.d/` holds the orchestrator's own logic, split by phase so no single file runs past a few hundred lines. `migite` sources each fragment on startup and calls its phase function (`run_plan`, `run_implement`, ...) — they all share `migite`'s variables (`TASK_DIR`, `PLAN_FILE`, `REVIEW_FILE`, ...) directly rather than taking them as arguments, so `migite.d/*.sh` only makes sense read alongside `migite` itself, not standalone. It's not a standalone tool like `migite-plan`/`migite-review`, so it isn't symlinked or listed in [docs/standalone-tools.md](./docs/standalone-tools.md).

`~/.local/bin/` holds symlinks to every top-level file above except README.md, docs/, and migite-improvements.md. `migite.d/` is not symlinked separately — `migite` resolves its own real path (through the symlink) to find `migite.d/` alongside it, so the directory just needs to stay next to `migite` in this repo.

**Gemfile-subdirectory detection.** `migite` always `cd`s to `$REPO_ROOT` right after computing it, so `git diff` and every other command run from a consistent directory regardless of where `migite` was invoked from — but Bundler only searches upward from cwd for a `Gemfile`, never into subdirectories. When the actual Ruby app lives one level down (e.g. a `rails-app/` folder alongside other tooling in the same repo), `detect_stack()` finds it (via the `rails` stack profile's `stack_rails_app_root`) and sets `$APP_ROOT`; every `bundle_exec` call then `cd`s there first, and `strip_app_prefix()` rewrites the repo-root-relative paths `git diff` produces into `$APP_ROOT`-relative ones before handing them to rubocop/rspec. `resolve_path()` absolutizes user-supplied file paths (`--audit`, `--blueprint`, `--intake`, `--amend-file`, `--attach`) before that `cd`, so they still resolve correctly afterward even if given relative to wherever you ran `migite` from. `rails` and `generic` are the two registered stack profiles today — see [`--stack` values](#-stack-values) above and `docs/hermes-agent-improvements-plan.md` for the plan to add more.

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
- Intake templates and the PR-description prompt ship in this repo's [`templates/`](./templates) directory — `migite.d/plan.sh` and `migite.d/deliver.sh` read them via `$MIGITE_HOME`, no `~/.claude/` setup required for these.
- Remaining phase prompts: `~/.claude/commands/{plan,implement,review,architecture_critic}.md`

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
| `MIGITE_PROMPT_INLINE_MAX` | `100000` | Interactive-phase prompts larger than this many bytes are passed to `claude` as a pointer to the prompt file instead of inline on the command line (Linux caps one argv string at 128 KB; the implement prompt includes all of `knowledge.md`) |
| `MIGITE_PERMISSION_MODE` | unset | Permission mode passed to `claude --print` — but only inside three standalone tools: `migite-explore`, `migite-audit`, `migite-pr-review`. Omitted (fail closed) when unset. `migite` itself, `migite-plan`, `migite-review`, and `migite-blueprint` don't read this variable at all — see [Permission failures](./docs/troubleshooting.md#troubleshooting-permissions) |
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
migite "https://yourcompany.atlassian.net/browse/<jira-ticket-id>"

# Jira key directly
migite --jira <jira-ticket-id>

# Pre-specify type to skip the interactive prompt
migite --jira <jira-ticket-id> --type feature
migite "fix N+1 on district index" --type bug

# Audit mode — feed a migite-audit report directly into planning
migite --audit ~/dev-log/Work/my-repo/audit-2026-08-11.md

# Audit mode + Jira
migite --audit ./audit.md --jira <jira-ticket-id>

# Pre-written intake from migite-explore or migite-blueprint
migite --intake ./exploration-agent-agnostic-2026-08-18/intake-01-extract-provider-adapter.md

# Same thing — a positional .md file is auto-detected as an intake
migite ./intake-01-foundation.md

# Blueprint context — inject a migite-blueprint output into planning
migite --jira <jira-ticket-id> --blueprint ./output/blueprint.md

# Staged implementation — checkpoint gate between plan scope layers
migite "add billing API" --staged
migite --jira <jira-ticket-id> --staged

# Amend an already-implemented task with post-implementation feedback
migite --amend "reviewer says the service must be idempotent on retry"
migite --amend-file ./qa-notes.md

# Attach reference material the plan should ground itself in (repeatable) —
# folded into task.md as plain text, since migite-plan's Claude calls are
# headless and can't open a path mentioned in the intake themselves
migite --jira <jira-ticket-id> --type spike --attach ~/Downloads/data-map.csv

# Non-Rails / no-Gemfile repo — auto-detects as the generic stack (no
# rubocop/rspec, plan+review still run). --stack forces it explicitly, e.g.
# on a monorepo where auto-detection would otherwise pick rails.
migite "add a health-check endpoint" --stack generic

# Health check — read-only, mutates nothing. Tool resolution, scratchpad/vault
# sync drift, orphaned sentinels. Exits non-zero if anything's found.
migite doctor
migite doctor --repo ~/Code/some-other-repo
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

### `--stack` values

| Value | When it's used |
|-------|----------------|
| `rails` | Auto-detected when a `Gemfile` exists at the repo root or one level down. Runs rubocop/rspec via `bundle_exec`. |
| `generic` | Auto-detected fallback for any repo with no recognized stack. Skips rubocop/rspec/`bundle` entirely; plan and review still run against the diff using general engineering judgment, with generic (language-agnostic) explore-area globs instead of Rails' MVC split. |

Pass `--stack <value>` to override auto-detection. See `docs/hermes-agent-improvements-plan.md`
("stack profiles") for the plan to add more stacks beyond these two.

For amend mode, intake mode, blueprint mode, audit mode, the full phase-by-phase breakdown, output files, the commit gate, the Testing Plan requirement, and resuming a run, see **[docs/migite.md](./docs/migite.md)**.

---

<a id="documentation"></a>
## Documentation

| Doc | Covers |
|-----|--------|
| [docs/migite.md](./docs/migite.md) | `migite` usage modes (amend/intake/blueprint/audit), all phases, active memory injection, tmux integration, output files, the commit gate banner, the Testing Plan requirement, resuming a run |
| [docs/standalone-tools.md](./docs/standalone-tools.md) | `migite-blueprint`, `migite-explore`, `migite-audit`, `migite-pr-review` |
| [docs/internals.md](./docs/internals.md) | The internal `migite-plan` / `migite-review` LangGraph scripts `migite` calls directly |
| [docs/vault-structure.md](./docs/vault-structure.md) | Full `~/dev-log/` directory tree and org resolution |
| [docs/troubleshooting.md](./docs/troubleshooting.md) | Permission failures, tooling preflight, review loops |

---

<a id="faq"></a>
## FAQ

**Does `--jira` always open the intake editor?**
No. It opens `$EDITOR` on the template only on a first run with no existing intake. It silently reuses an existing `intake.md` for that ticket's slug when resuming, and skips the editor entirely in `--intake`/`--audit` mode (just a `[y/e/q]` confirm prompt instead).

**Does `--jira` fetch the ticket's content automatically?**
Yes, for planning. `plan.sh` fetches the ticket (title, type, priority, status, description, acceptance criteria) via the Atlassian MCP before `migite-plan` runs, caches it to `jira-context.md`, and feeds it into plan synthesis and the explorers — the one call in the pipeline granted tool access, scoped to just the two read-only Jira-lookup tools. It still doesn't get injected into the intake template's own `Title`/`Jira` fields (those are still just slugging/labeling, filled with the ticket key/link as before) — the fetched content flows straight into planning instead. If the fetch fails (MCP not configured/authenticated, wrong key, no access), planning proceeds without it, same as a missing knowledge.md/audit/blueprint.

**Why do the planning explorers only read a handful of files instead of the whole codebase?**
Cost and context budget — each of the 7 parallel Haiku explorers is capped at 14 files / 14,000 chars. They're not meant to be exhaustive; they exist to ground the plan in real file/method names before Phase 2 (an interactive session with full Read/Grep/Edit access) does the actual deep exploration during implementation.

**Why does an explorer check `git diff <base_branch>` before any code has been written?**
On a fresh branch it's empty and changes nothing. It matters when resuming a plan, running `--amend`, or if you'd already hand-edited files before invoking `migite` — in those cases, already-changed files are the strongest relevance signal and are always read first.

**How are the remaining files ranked when there's no diff to fall back to?**
By intake-keyword hits — weighted 5x for a match in the file's path (deliberate naming is a stronger signal) plus 1x per keyword occurrence found in the file's content, so relevant files with generic names still surface.

**Does migite commit for me?**
No — it never runs `git commit` at any point, including at the commit gate's `y` (approve). That just advances to Phase 3.5; the actual commit is always yours to make. See [The commit gate banner](./docs/migite.md#commit-gate-banner).

**Do I need a separate Anthropic API key?**
No. Every phase shells out to the `claude` CLI, which shares your existing Claude Code auth — see [Requirements](#requirements).

**Does migite work on non-Ruby/Rails projects?**
Not yet. `migite` itself hardcodes Ruby/Rails tooling (`bundle`, rubocop, rspec, `Gemfile` detection) throughout `migite.d/*.sh` — see [Roadmap](#roadmap). `migite-blueprint` is the exception; it already infers/accepts any stack for new-project definition.

**Can I resume a run if I close the terminal or a phase fails partway through?**
Yes — just re-run the same `migite --jira <ticket>` (or same task/intake) command. It picks up from whatever already exists: an existing `intake.md` is reused with no editor, an existing `plan.md` offers `[u]se existing` or `[r]edo`. See [Resuming a run](./docs/migite.md#resuming-a-run).

**Does a brand-new file I haven't `git add`ed get linted and tested?**
Yes. Every phase gets its file list from the shared `changed_*_files` helpers, which union `git diff <base branch>` with `git ls-files --others --exclude-standard`, so untracked new files are included (gitignored ones are not). The one gap: the reviewer's diff still comes from plain `git diff`, so a new file's content reaches `migite-review` only once staged. See [Which files get linted and tested](./docs/migite.md#lint-test-selection).

**Why does Phase 3 re-run rubocop/rspec when the Phase 2.5 auto-heal loop already got them passing?**
The heal loop's job is to deliver clean input to the reviewer, not to replace the review — Phase 3 always runs its own authoritative pass regardless of heal-loop outcome, so a stale or partial heal never silently reaches the commit gate.

---

<a id="roadmap"></a>
## Roadmap

Not yet built, roughly in the order they're likely to land:

- **Per-run usage summary.** Print a token/cost summary (and wall-clock time) at the end of each `migite` run — today there's no visibility into what a run actually cost across its ~15-20 model calls.
- **Stack-agnostic beyond `migite-blueprint`.** `migite-blueprint` already infers/accepts any stack (see [docs/standalone-tools.md](./docs/standalone-tools.md)); `migite` itself still hardcodes Ruby/Rails tooling (`bundle`, rubocop, rspec, `Gemfile` detection) throughout `migite.d/*.sh`. Generalizing this means detecting and running the equivalent toolchain per stack (`npm run lint`/`jest`, `ruff`/`pytest`, `go vet`/`go test`, etc.) instead of one hardcoded path.
- **Support AI agents other than Claude.** Right now every phase shells out to `claude`. Making the agent backend pluggable (e.g. Codex, opencode) would decouple the orchestration logic (phases, gates, vault, resolvers) from any one CLI.
- **One-line installer.** Replace the manual clone-and-symlink dance in [Installation](#installation) with a script that does it in one command.
- **CI on this repo.** No GitHub Actions yet — at minimum, run `migite_paths.py --self-test` on push/PR so the path-resolver logic (org/ticket detection) can't silently regress.
- **Config file support.** Configuration today is env-var only (`DEV_LOG_BASE`, `MIGITE_ORG`, `MIGITE_PYTHON`, ...). An optional `.migiterc`/`migite.yml` would let settings live with a project instead of only in shell profiles.
