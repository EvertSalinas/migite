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
- [Phase 3.8: Plan update](#phase-3-8-plan-update)
- [Phase 4 — PR description](#phase-4-pr-description)
- [Phase 4.2: Run summary](#phase-4-2-run-summary)
- [Phase 4.5 — Self-improvement](#phase-4-5-self-improvement)
- [Active memory injection](#active-memory-injection)
- [tmux integration](#tmux-integration)

---


<a id="phase-1-plan"></a>
### Phase 1 — Plan (LangGraph)

Before planning, migite switches to the branch the intake names in its `**Branch base:**` (or plain
`Branch:`) line, if any. An existing local branch is checked out as-is, one that only exists on
`origin` is checked out tracking it, and otherwise the branch is created from the base branch.
Naming the base branch itself, or an invalid branch name, only prints a warning and the run stays
where it is. If local changes block the checkout, the run stops and asks you to commit or stash
them first.

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
    generate_testing_plan   (Sonnet 5, standalone QA/dev verification doc; skipped when `plan.testing_plan_when: review`)
         │
    write_outputs   → plan.md + architecture-critic.md + testing-plan.md (not with `review` timing) + sentinel
```

An eighth explorer, `views_frontend` (views, components, helpers, Stimulus controllers, the
importmap), joins the seven when the task may touch the frontend. See
[Frontend: views, Turbo and Stimulus](#frontend) for how that's decided.

Explorers use Haiku 4.5 for fast file analysis. Each reads changed files first (from `git diff <base branch>` — empty on a fresh branch, populated when resuming or amending), then ranks the rest by intake-keyword hits in path and content, weighted toward path matches. Plan synthesis and refinement use the strong tier (Opus 5.5 by default, role `think`) — the plan is the highest-leverage text in the run, and the refiner must not be weaker than the critic whose findings it applies. The testing plan is a checklist document written from the finished plan, so `generate_testing_plan` runs on the standard tier (Sonnet 5 by default, role `testing_plan`), which also gives it the standard tier's 600s timeout (see [timeouts](./configuration.md#models)). The architecture critic also uses the strong tier — it is the single highest-stakes call in the planner, where a missed finding propagates into implementation — and runs read-only (`Read`, `Grep`, `Glob`) so it can verify the plan's claims against the repo. All of this is configurable per role, see [docs/configuration.md](./configuration.md#models). `generate_testing_plan` writes `testing-plan.md` as its own file rather than a section of the plan — see [Testing Plan requirement](./outputs.md#testing-plan-requirement) for why.

**Jira ticket fetching.** When `--jira` was used, `plan.sh` asks `migite-ticket` for the actual ticket (title, type, priority, status, description, acceptance criteria) before `migite-plan` runs. The source follows `tracker.provider`: Atlassian's `acli` when it is installed and logged in (no model call, no token), else one agent call through the Atlassian MCP tools inside the `jira.read` scope (see [tickets.md](./tickets.md)). The result is cached to `jira-context.md` in the scratchpad (mirrored to the vault, reused on redos so it isn't re-fetched every time) and fed into `synthesize_plan`, the explorers' keyword extraction, and the choice of [knowledge.md entries](#active-memory-injection) the prompts get. If no source can run or the fetch fails (no `acli` login, wrong key, no access), planning proceeds without it, same as a missing `knowledge.md`/audit/blueprint; the ticket key still works for slugging and vault naming regardless.

After the agent finishes, the architecture critic findings are printed above the plan gate as a checklist. If the critic returns no usable findings (leaked tool-call syntax or an empty reply), `migite-plan` retries once and then writes a 🟡 warning marking the plan un-critiqued rather than aborting. The gate then opens:

**The refiner can say no.** `refine_plan` applies the critic's findings as exact edits, but it is no longer
forced to apply every one. For each finding it either edits the plan or rejects it, and it may reject only
when the plan itself or the explorer reports (which it is given as context) show the finding is wrong or
already handled, for example the plan already specifies the index or lock the critic asks for. A rejection
must quote that evidence verbatim, and migite checks the quote is really in the plan or the explorer reports.
Rejections that check out are listed under `## Rejected by the plan refiner` at the end of
`architecture-critic.md`, so they print right below the critic's findings at the gate, each with its
reason. One whose quote cannot be found is not trusted: it is listed under `## Not applied, reason not
verified` and stays open. If you disagree with a rejection, `f` asks for the change. The refiner has no
tools, so when it cannot show from that text that a finding is wrong it must address it. Both lists are
in `plan.json` (`refine_rejected[]`, `refine_unverified[]`). The full-rewrite fallback still addresses every finding.

```
Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve the plan and continue |
| `f` | Give feedback - migite prompts for text, refines the plan in place with one headless call (the `plan_refine` role, standard tier) that returns exact edits rather than a rewritten plan (`migite/doc_edits.py`; a full rewrite is the fallback when no usable edit comes back), shows a colored diff of what changed, then re-opens the gate |
| `e` | Edit — opens `plan.md` directly in `$EDITOR` (default: vim) with zero latency |
| `n` | Reject — re-runs the full `migite-plan` agent from scratch (fresh exploration + synthesis + critic), shows a colored diff after |
| `q` | Abort the workflow. `run.json` records the gate as pending: running the same command again comes straight back here |

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

Each finished stage is recorded in `run.json` (`phases.implement.stage_num`), so a staged run that stops resumes after the last stage it finished; a `q` at a checkpoint re-opens that checkpoint.

**Known limitation in the current implementation:** the extra instructions typed under `e` are saved to a log file but nothing feeds them back into the next stage's prompt yet. It is an open bug, not intentional behavior.

No gate after Phase 2 — migite moves directly to the heal loop. The heal loop is its own phase in `run.json`, so a run interrupted while healing resumes there without replaying the implement session.

<a id="phase-2-5-heal"></a>
### Phase 2.5 — Auto-heal loop

After implementation, migite runs rubocop and rspec automatically. If failures exist:

1. The agent fixes them non-interactively (a headless call on the `heal` role with `permissions.heal`, `auto` by default)
2. Checks re-run
3. Repeats up to `MAX_HEAL_ATTEMPTS` (default 3)

If a heal call itself fails (a CLI error, say), the loop stops there and the run moves on to review
with the failures still present, as if the attempts were used up.

Phase 3 always runs its own authoritative rubocop + rspec pass regardless — the heal loop delivers clean inputs to the reviewer, it doesn't skip the review.

When the diff touches views or JavaScript, the loop also autofixes them with erb_lint / eslint
(when the repo configures them) and hands the agent only what autofix left. Spec failures that
`tooling_failed` puts down to the toolchain (no browser for system specs, DB down) are never sent
to the agent: changing application code can't fix a missing Chrome.

**On a [stack profile](./configuration.md#stacks)** (`stacks.<name>`) the loop is the same, with
the profile's commands in place of rubocop and rspec (`run_stack_heal_loop`):

1. `autofix`, then `lint`, run over the changed files the `source` globs select.
2. `test` runs over the files the `specs` globs select.
3. Exit codes decide pass or fail.

Only a lint or test that failed goes to the agent, each log capped by
`heal.prompt_log_max_bytes`. A command that could not start (exit 126 or 127: not installed, not
executable) is never sent; Phase 3 reports it as a tooling error. A profile has no `tooling_failed`
patterns and no frontend linters.

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

Phase 3 also runs a separate, different check: any changed file (tracked or untracked) whose basename doesn't appear in any run folder's `implementation.md` (the original build's and every amendment's) gets flagged as a warning before the review runs, so undocumented changes get caught before the reviewer sees them.

Tooling failures — Ruby version unset, a git-sourced gem not checked out, rspec producing
`0 examples` because of a DB connection or load error — are detected by one shared
`tooling_failed <log>` helper wherever a rubocop/rspec log is inspected (Phase 3, the commit-gate
re-checks, and the banner). When it fires, `TOOLING_ERROR` carries the message into the banner.

**Specs aren't run twice on the same code.** Auto-heal records a fingerprint of the tree each time
it runs rspec (`tree_fingerprint`: the diff plus every untracked file, minus `scratchpad/`). When
Phase 3's rubocop sweep changes nothing and the fingerprint still matches, Phase 3 reuses heal's
rspec results instead of running the same specs again.

**Phase 3's full-suite fallback is configurable.** By default (`heal.full_suite_fallback: true`)
Phase 3 and the commit-gate re-checks still run the whole rspec suite when no spec files changed —
the pre-config behaviour. Set it to `false` in `.migite.yml` to make Phase 3 consistent with Phase
2.5 (skip rspec and say so); the full suite needs a live DB and verifies nothing about the diff.

**A stack profile picks its files with globs.** On a [`stacks.<name>`](./configuration.md#stacks)
profile, `changed_stack_files` filters `changed_all_files` by the profile's `source` globs (for
autofix and lint) and `specs` globs (for test). Only the globs come from the profile; the list
underneath is the same tracked-plus-untracked, no-deletes, no-`scratchpad/` list. A command whose
list is empty doesn't run, and says so (`Skipped: ...`), in every phase alike: a profile has no
full-suite fallback, because a test command without `{files}` already is the whole suite.

<a id="phase-3-review"></a>
### Phase 3 — Review (LangGraph)

`migite-review` runs after the authoritative rubocop and rspec pass (on a
[stack profile](./configuration.md#stacks), the profile's autofix, lint and test, run by
`run_stack_review_checks` and decided by exit code; the reviewers see them as the lint and test
results, and the gate banner shows `Lint:` and `Tests:` lines). With `plan.testing_plan_when: review`, Phase 3 first writes `testing-plan.md` from `plan.md` and the diff (`ensure_testing_plan`), so the browser check and the `testing_plan` reviewer below read a testing plan that describes what was built; an existing one is kept, and a failed call only warns. See [configuration](./configuration.md#plan).


```
load_inputs  (reads plan, implementation notes, rubocop/rspec logs, git diff, testing-plan.md)
    │
    ├── review: correctness      (logic vs plan, scope creep, acceptance criteria)
    ├── review: security         (auth, N+1, SQL injection, raw params, scopes)
    ├── review: test_coverage    (unit + request specs, factories, context wording)
    ├── review: testing_plan     (testing-plan.md completeness — see below; `review.dimensions.testing_plan: off` skips it)
    └── review: frontend         (only when the diff touches views or JavaScript - see Frontend)
         │
    verify_findings      → a second agent tries to disprove each Critical (see below)
         │
    synthesize_verdict   → de-duplicates findings → READY TO COMMIT | NEEDS FIXES
         │
    write_review   → review.md + sentinel
```

All reviewers (four, or five with frontend) use Sonnet 5 and run in parallel; `synthesize_verdict` uses Opus 5. The commit gate then opens with a context banner showing the verdict, spec failures, and rubocop offense count.

What each reviewer checks comes from the stack's [checklist](./configuration.md#checklists):
`prompts/checklists/rails.md` on rails (the criteria in parentheses above), `generic.md` on generic
and on a stack profile without a checklist of its own, with any `prompts.dir` override's sections on
top. The refuter below takes its expertise and "how to work" block from the same file.

**Findings are checked before they decide the verdict.** A reviewer can be confidently wrong about code
it did read, and a wrong Critical turns into a wrong `NEEDS FIXES` and a wrong `f` fix round. So
`verify_findings` (`migite/verify.py`, shared with `migite-pr-review`) sits between the reviewers and
the synthesis:

- **Evidence.** The correctness, security and test-coverage reviewers must quote, on an `**Evidence:**`
  line, the exact line of code each Critical and Warning rests on. migite checks the quote exists near the
  cited line (no model call). A Warning whose evidence is missing or does not match is sent to the refuter.
- **Refuter.** Every Critical goes to a fresh agent (role `refute`, strong tier, read-only tools) that
  sees the claim and the cited location but not the reviewer's reasoning or fix, and is asked to disprove
  it by tracing the code the way Ruby and Rails resolve it (constant lookup through enclosing modules,
  inheritance, concerns, default scopes). `CONFIRMED` keeps the finding and adds a `**Verified:**` line.
  `REFUTED` removes it from the findings and lists it under `## Refuted by verification` at the end of
  `review.md` (and in `review.json` as `refuted[]`), so you can audit the call. `UNVERIFIABLE` demotes it
  to a Note. If the refuter fails or times out the finding stays as reported, marked as not checked.
- The `testing_plan` and `frontend` reviewers are not refuted: their findings rest on a document or a
  browser report, not on code. At most 12 findings are checked per run, Criticals first.

To decorrelate the refuter's mistakes from the reviewers', pin it to another model or backend with
`models.roles.refute` (see [configuration](./configuration.md#models)).

```
Proceed? [y/f/e/n/q] (y=commit, f=Claude fixes, e=edit directly, n=fix it yourself, q=abort):
```

| Key | Action |
|-----|--------|
| `y` | Approve — continue to Phase 3.5 |
| `f` | Claude fixes: opens an interactive session with the full review findings + the current plan (and any amendment it doesn't reflect yet) as context. Claude addresses every Critical and Warning. On `/exit`, migite re-runs rubocop + rspec + `migite-review` automatically and shows the gate again |
| `e` | Edit — opens `review.md` directly in `$EDITOR` so you can annotate, dismiss, or restructure findings before deciding |
| `n` | Manual fix — migite pauses and waits for you to press Enter when ready, then re-runs checks and re-review |
| `q` | Abort the workflow. `run.json` records the gate as pending: running the same command again re-runs lint and specs and re-opens the gate, reusing `review.md` when the code is unchanged since it was written |

Use `f` when the review found something real and the fix is straightforward enough for Claude to handle. Use `e` when you want to read and annotate the review before acting. Use `n` when the fix involves a judgment call, a schema change, or something that needs your direct decision. Both `f` and `n` re-review afterwards through the same helper, and there's no cap on how many times you can loop through this.

A re-review runs only what the change could affect: correctness always, every dimension whose last result had findings (or whose reviewer failed), and the testing-plan dimension when the testing plan changed since. The clean dimensions carry their previous result into the verdict, marked as not re-run (`review-dimensions.json` beside `review.json`). The first review of a run always runs every active dimension: the four core ones (three with `review.dimensions.testing_plan: off`, see [configuration](./configuration.md#review)), plus frontend when the diff touches views or JavaScript. After an `f`, the testing plan is updated with exact edits (`edit_document`), with a full regeneration only when no usable edit comes back.

The reviewers run with read-only tools (`Read`, `Grep`, `Glob`), no MCP servers or plugins, and a per-call cost cap (`budget.review_call_max_usd`); see [`permissions.headless_tools`](./configuration.md#permissions).

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
repo has a `.erb_lint.yml` (or `.erb-lint.yml`); eslint runs on changed JavaScript when the repo has
an eslint config (`eslint.config.*` or `.eslintrc*`).
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

<a id="phase-3-8-plan-update"></a>
### Phase 3.8: Plan update

`plan.md` is a living document: at the end of every run it is brought up to date with what the run
decided and built, so the next run, reviewer, or person reads the design as it stands rather than
as first planned plus a pile of amendments. The plan as approved at the gate is kept, unchanged, in
`00-build/plan.md`.

One headless call (the `plan_fold` role, standard tier, `migite/plan_fold.py`) reads this run's
amendment (for an `--amend`), implementation notes, fix rounds and final review, and proposes exact
edits: each one a passage copied from the plan and its corrected text. The model never rewrites
the plan. An edit lands only when its passage appears exactly once in the plan; the rest are
dropped and listed. You see the diff, then:

| Key | Action |
|---|---|
| `y` | Apply the edits (the previous `plan.md` goes to `.plan-history/`) |
| `e` | Apply them, then open `plan.md` in `$EDITOR` |
| `n` | Keep the plan as it is |

Every applied fold, even one that needed no edits (applied without asking), adds one line to a
`## Revision history` section at the bottom of `plan.md`:

```text
- 2026-09-30 `01-amend-reduce-the-delay-before-chat-recordmessa`: Enqueue delay is 15 seconds, not 1 minute.
```

That section is also how migite knows which runs the plan reflects. The amend, implement, review,
fix and PR-description prompts get the current plan plus only the amendments it doesn't reflect
yet: normally none, or the current run's before its own fold. So prompt size stays flat however
many times a task is amended. A declined or failed fold leaves `plan.md` alone, and that run's
amendment keeps going into later prompts beside it. A task from before the living plan has no
revision history, so every one of its amendments is still included, as before.

**Testing plan, on `--amend` runs.** The amendment gate edits `testing-plan.md` from what the
amendment says will change, before any code exists. So after the plan update, one more headless
call (the `testing_plan` role, through `edit_document`) reads the amendment, the implementation
notes, the run's fix rounds and the final diff, and proposes exact edits wherever the code as built
differs from the testing plan. You see the diff, then answer the TESTING PLAN UPDATE prompt the same
way: `y` apply, `e` apply then edit, `n` keep it as it is. When nothing needs to change it says so
without asking. A failed call, or one with no usable edits, leaves the gate-time version in place;
there is no full rewrite at this point. Build runs skip this step: their testing plan was written
from the finished plan, and any fix rounds already edited it.

<a id="phase-4-pr-description"></a>
### Phase 4 — PR description (interactive)

The agent generates a PR description from the plan and review, following the prompt/template in
this repo's [`templates/commit.md`](../templates/commit.md) (`deliver.sh` reads it via
`$MIGITE_HOME`). Output goes to `pr-description.md` — ready to paste into GitHub.

<a id="phase-4-2-run-summary"></a>
### Phase 4.2: Run summary

A headless pass (the `summary` role, fast tier) writes `summary.md` in the run's folder: a record
for people of what this run did and why. It reads only this run's own files: the plan (or, for an
`--amend`, the amendment), the implementation notes, every fix round, the final review, any
commit-gate overrides, the note you typed at Phase 3.5, and `git diff --stat`. From those it writes:

- a one-line `Summary:`, which `index.md` shows in the run's row
- **What changed**, **Why**, **Decisions made during the run**, **Deviations from the plan**,
  **Fix rounds** and **Follow-ups**

The title, the date, and a **Run facts** section (review verdict, fix rounds, commit-gate
re-reviews, auto-heal attempts, plan-gate rounds, plan update result, testing-plan update result
on amend runs, headless model cost) are written by bash, not the
model, so they can't be misreported. The only prompt that reads `summary.md` back is a later
`--amend`: for a run that `plan.md` already reflects, it gets that run's summary in place of its
full implementation notes, since the design is already in the plan. If the call fails or comes back empty, the run
goes on without one.

<a id="phase-4-5-self-improvement"></a>
### Phase 4.5 — Self-improvement

A background headless pass (the `improve` role, standard tier) reviews the full run and appends 0–3 actionable observations to `docs/improvements.md` in this repo. Observations must be grounded in what happened during the run - no generic suggestions. It gets a function index of the migite script (file, line and name of every function), never the full source, and the rubocop and rspec logs capped at 8 KB each. The full source used to go in on every run with a gate rejection, commit-gate loop or heal attempt, which was most runs: about 180 KB of prompt for 0–3 bullets. Phase 3.5's knowledge extraction is pinned to the standard tier as well.

---

<a id="active-memory-injection"></a>
## Active memory injection

Before Phase 1 (Plan), Phase 1.5 (TDD specs), and Phase 2 (Implement), migite reads `knowledge.md` from the vault and injects it into the prompt as:

```
## Repository conventions and past lessons
<the knowledge.md entries closest to the task, up to knowledge.inject_max_bytes, newest first>
(N other entries not shown; all of them are in knowledge.md)
```

The entries are picked by [`knowledge.select`](./configuration.md#knowledge):

- `relevant` (the default) ranks every entry by how many of the task's words its lessons use and fills [`knowledge.inject_max_bytes`](./configuration.md#knowledge) (default 8000) in that order.
  - Ties go to the newer entry. An entry too big for the room left is skipped so a smaller one can use it.
  - The task's words come from `intake.md` and, when `--jira` was used, `jira-context.md`.
  - Template scaffolding doesn't count: front matter, `<!-- -->` hints, links, headings, the `**Type:**` line, `**Label:**` markers and empty labels. A `--jira` intake is usually the bare template, so the ticket supplies the words.
  - Nor do an entry's dated `##` heading or its `[[wikilinks]]`.
  - When no entry shares a word with the task (a bare intake with no ticket, say), the selection falls back to `recent`.
- `recent` takes the newest entries first. The last line then reads "N older entries".

Either way the picked entries print newest first, and the last line appears only when entries were left out. A two-year-old lesson about the code a ticket touches can reach the prompt, while the latest lessons still fill whatever room is left. Claude sees past N+1 pitfalls, auth patterns, business logic constraints, and architectural decisions before touching anything.

The amend and resume paths pick the same way from the build's `intake.md` and `jira-context.md`. Inside `migite-plan`, synthesis gets the same selection, and each explorer gets one at 800 bytes (`EXPLORER_KNOWLEDGE_BYTES`).

---

<a id="tmux-integration"></a>
## tmux integration

If migite is running inside a tmux session (`$TMUX` is set), every interactive phase and every LangGraph agent opens in a **split pane below the current pane** (`split-window -v`) and signals back to the orchestrator via `tmux wait-for` when done. You get a macOS notification and focus returns to the migite pane automatically.

If you close a pane before the phase completes, migite detects this and aborts with an error rather than hanging indefinitely.

Outside tmux, all phases run inline in the current terminal.

---

