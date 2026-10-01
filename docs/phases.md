# Phases

What each phase of a `migite` run does, which model calls it makes, how files are selected for
lint and test, and how repo memory and tmux fit in. For the command-line modes (amend, intake,
audit, staged) see [migite.md](./migite.md); for what each phase writes and the commit-gate
banner see [outputs.md](./outputs.md); for a complete example run see
[getting-started.md](./getting-started.md#first-run).

## Contents

- [Phase 1 — Plan](#phase-1-plan)
- [Phase 1.5 — TDD specs](#phase-1-5-tdd)
- [Phase 2 — Implement](#phase-2-implement)
- [Phase 2.5 — Auto-heal loop](#phase-2-5-heal)
- [Which files get linted and tested](#lint-test-selection)
- [Phase 3 — Review](#phase-3-review)
- [Frontend: views, Turbo and Stimulus](#frontend)
- [Phase 3.5 — Knowledge capture](#phase-3-5-knowledge)
- [Phase 4 — PR description](#phase-4-pr-description)
- [Phase 4.5 — Self-improvement](#phase-4-5-self-improvement)
- [Active memory injection](#active-memory-injection)
- [tmux integration](#tmux-integration)

---


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

An eighth explorer, `views_frontend` (views, components, helpers, Stimulus controllers, the
importmap), joins the seven when the task may touch the frontend. See
[Frontend: views, Turbo and Stimulus](#frontend) for how that's decided.

Explorers use Haiku 4.5 for fast file analysis. Each reads changed files first (from `git diff <base branch>` — empty on a fresh branch, populated when resuming or amending), then ranks the rest by intake-keyword hits in path and content, weighted toward path matches. Plan synthesis, refinement, and the testing plan use the strong tier (Opus 5.5 by default, role `think`) — the plan is the highest-leverage text in the run, and the refiner must not be weaker than the critic whose findings it applies. The architecture critic also uses the strong tier — it is the single highest-stakes call in the planner, where a missed finding propagates into implementation. All of this is configurable per role, see [docs/configuration.md](./configuration.md#models). `generate_testing_plan` writes `testing-plan.md` as its own file rather than a section of the plan — see [Testing Plan requirement](./outputs.md#testing-plan-requirement) for why.

**Jira ticket fetching.** When `--jira` was used, `plan.sh` asks `migite-ticket` for the actual ticket (title, type, priority, status, description, acceptance criteria) before `migite-plan` runs. The source follows `tracker.provider`: Atlassian's `acli` when it is installed and logged in (no model call, no token), else one agent call through the Atlassian MCP tools inside the `jira.read` scope (see [tickets.md](./tickets.md)). The result is cached to `jira-context.md` in the scratchpad (mirrored to the vault, reused on redos so it isn't re-fetched every time) and fed into both `synthesize_plan` and the explorers' keyword extraction. If no source can run or the fetch fails (no `acli` login, wrong key, no access), planning proceeds without it, same as a missing `knowledge.md`/audit/blueprint; the ticket key still works for slugging and vault naming regardless.

After the agent finishes, the architecture critic findings are printed above the plan gate as a checklist. The gate then opens:

```
Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve the plan and continue |
| `f` | Give feedback - migite prompts for text, refines the plan in place with one headless call (the `plan_refine` role, standard tier), shows a colored diff of what changed, then re-opens the gate |
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

1. The agent fixes them non-interactively (a headless call on the `heal` role with `permissions.heal`, `auto` by default)
2. Checks re-run
3. Repeats up to `MAX_HEAL_ATTEMPTS` (default 3)

Phase 3 always runs its own authoritative rubocop + rspec pass regardless — the heal loop delivers clean inputs to the reviewer, it doesn't skip the review.

When the diff touches views or JavaScript, the loop also autofixes them with erb_lint / eslint
(when the repo configures them) and hands the agent only what autofix left. Spec failures that
`tooling_failed` puts down to the toolchain (no browser for system specs, DB down) are never sent
to the agent: changing application code can't fix a missing Chrome.

<a id="lint-test-selection"></a>
### Which files get linted and tested

Every phase gets its changed-file list from one set of shared helpers in `lib/stack.sh` —
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
| `bundle exec` runs from the app's actual root, not necessarily the repo root | Bundler only searches upward from cwd for a Gemfile. When the Ruby app lives one level down (e.g. a `rails-app/` subdirectory alongside other tooling), `detect_stack` (`lib/stack.sh`) finds it via the `rails` stack profile's `stack_rails_app_root` and every `bundle_exec` call `cd`s there first — otherwise `git diff`'s repo-root-relative paths get re-resolved against the wrong directory and rubocop reports files missing |

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
    ├── review: testing_plan     (testing-plan.md completeness — see below)
    └── review: frontend         (only when the diff touches views or JavaScript - see Frontend)
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

<a id="frontend"></a>
### Frontend: views, Turbo and Stimulus

Migite works out whether a task touches the frontend twice, from two different sources.

| When | Decided from | What it adds |
|------|--------------|--------------|
| Planning (Phase 1) | The intake's `**Frontend:**` line (`yes` / `no`), else the repo: an `app/javascript` dir, `config/importmap.rb`, or `turbo-rails` / `stimulus-rails` / `view_component` in the Gemfile. `app/views` alone doesn't count, since API-only apps still have mailer templates | The `views_frontend` explorer, frontend planning guidance, and testing-plan UI steps written so a person or an agent in a browser can follow them literally |
| Checks and review (Phases 2.5 and 3) | The actual diff: `changed_frontend_files` (`lib/stack.sh`) lists changed `.erb`, `.js`, `.mjs`, `.ts`, `.jsx` and `.tsx` files under `app/`, minus `app/assets/builds/`. A root `eslint.config.js` or `tailwind.config.js` is tooling, not frontend | erb_lint / eslint, the `frontend` reviewer, and the optional browser check |

The intake line is a hint for planning, not a requirement. Leaving it blank or `unknown` lets the
repo decide. Setting `no` in a Hotwire repo skips the eighth explorer, and the planner is told to
raise any UI need under Open questions instead of planning it blind. Setting `yes` explores the
frontend even where nothing is detected. Checks and review always go by the diff, so a
backend-only diff never runs the frontend linters or reviewer, whatever the intake said.

**Linting.** erb_lint runs on changed `.erb` files when `erb_lint` is in `Gemfile.lock` and the
repo has a `.erb_lint.yml`; eslint runs on changed JavaScript when the repo has an eslint config.
An eslint config without `node_modules/.bin/eslint` is a tooling error, not a pass. Remaining
problems appear as `FE lint:` in the commit banner and count as a lint blocker under
`gates.commit.policy: strict`. `frontend.lint: off` turns both off.

**Reviewing.** `security` and `test_coverage` always check the Hotwire items that are really
security or coverage: no user content through `html_safe` / `raw`, broadcasts scoped to who may
see them, request specs asserting `turbo_stream` responses. The fifth reviewer, `frontend` (role
`review_frontend`, standard tier), checks the mechanics that break in the browser, not in a spec:
422 on failed form submits, 303 after successful non-GET redirects, frame and stream targets
that exist, Stimulus names that match their controllers. Its system-spec rule depends on the
repo. Where `spec/system` exists, a new interactive flow without a system spec is a warning;
where it doesn't, it's only a note, so an unrelated change never has to set up a browser driver.

**System specs** run like any other spec when they change. If the browser never starts (Cuprite
without Chrome, a Selenium driver mismatch, Playwright without downloaded browsers),
`tooling_failed` reports a tooling error instead of N failures. On a machine with no browser, set
`frontend.system_specs: off` to drop `spec/system` from both the changed-spec run and the
full-suite fallback.

**Browser check (Phase 3.1, opt-in).** With `frontend.browser_check: ask` or `on`, and a diff
that touches the frontend, an interactive session runs before the review. The agent follows the
testing plan's browser steps with whatever browser tool it has (a Playwright MCP server, a Chrome
extension), writes `browser-check.md` with PASS / FAIL / SKIPPED per step, and changes no code.
The `frontend` reviewer reads that report, and a FAIL caused by the code is Critical. An agent
with no browser tool writes `SKIPPED`. The report is reused on resume; delete it to run the
check again. It isn't re-run after commit-gate fix rounds.

<a id="phase-3-5-knowledge"></a>
### Phase 3.5 — Knowledge capture

A background headless pass (the `knowledge` role) extracts 1–3 reusable bullets from the completed run (plan + implementation + review) and appends them to `knowledge.md`. Entries link back to the review via Obsidian wikilinks (harmless plain text if you're not using Obsidian).

Only domain-level insights are captured: business logic clarifications, non-obvious constraints, architectural decisions. Rails conventions and testing patterns are excluded.

<a id="phase-4-pr-description"></a>
### Phase 4 — PR description (interactive)

The agent generates a PR description from the plan and review, following the prompt/template in
this repo's [`templates/commit.md`](../templates/commit.md) (`deliver.sh` reads it via
`$MIGITE_HOME`). Output goes to `pr-description.md` — ready to paste into GitHub.

<a id="phase-4-5-self-improvement"></a>
### Phase 4.5 — Self-improvement

A background headless pass (the `improve` role, standard tier) reviews the full run and appends 0–3 actionable observations to `docs/improvements.md` in this repo. Observations must be grounded in what happened during the run - no generic suggestions. The full migite source (~110 KB) is included only on *eventful* runs - a plan rejected at the gate, any commit-gate loop, or any auto-heal attempt - since that's when there's a script behaviour to point at; a quiet run gets a function index instead. Phase 3.5's knowledge extraction is pinned to the standard tier as well.

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

