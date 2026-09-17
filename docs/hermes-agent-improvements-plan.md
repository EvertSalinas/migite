# Implementation plan: Hermes Agent learnings → migite

> Companion to `docs/hermes-agent-learnings.md`. That file is the research; this is the
> execution order. Phases are sequenced by risk/effort, not by the learnings doc's numbering —
> cheap, zero-risk fixes first, then the stack-profiles work (the flagship item — "run migite on
> any project"), staged so each step is independently low-risk.

---

## Phase 0 — Hardcoded-path fixes (learnings #3) [DONE — 2026-09-07]

No behavior change for the common case; fixes two latent breakages.

1. **`migite:29`** — `MIGITE_PYTHON` is pinned to `$HOME/.asdf/installs/python/3.13.5/bin/python3`.
   Resolve it dynamically instead: try `command -v python3` first, fall back to the current
   hardcoded path only if that's unset (keeps existing explicit-override behavior via the
   `${MIGITE_PYTHON:-...}` pattern already there — this only changes what the *default*
   resolves to, not the override mechanism).
2. **`$HOME/.claude/commands/implement.md`** hardcoded 3x (`migite.d/implement.sh:16,29`,
   `migite.d/plan.sh:490`) — collapse into one `IMPLEMENT_CMD_PATH` constant defined once near
   `MIGITE_HOME` at the top of `migite`, referenced everywhere else. Mirrors the
   `PLAN_CMD_PATH`/`CRITIC_CMD_PATH` pattern already in `migite-plan` — just extending it to the
   bash side, where it hasn't been applied yet.

**Effort:** ~30 min. **Risk:** none — same resolved value on any machine that already works today.

---

## Phase 1 — Stack profiles: run on any project (learnings #8, found while answering "does migite work on migite")

Flagship item. Rails-specificity runs deeper than the `Gemfile` gate alone: `helpers.sh`
(`detect_app_root`/`bundle_exec`/`run_rubocop_check`/`run_rspec_check`), `migite-plan`'s
hardcoded Rails-MVC `EXPLORE_AREAS`, and — most of all — the actual judgment content of
`~/.claude/commands/plan.md` (layer-by-layer Rails instructions per task type) and
`architecture_critic.md` (N+1/ActiveRecord/Sidekiq checklist) are all Rails-shaped. A fully
first-class second stack is realistically its own mini-project per stack (the Rails checklist
is hard-won from real runs — a Node/Python profile earns the same quality the same way, not by
translating the Rails one). A **generic "runs on any project, degraded" mode** is the
achievable near-term win and should happen regardless of how far stack-specific profiles go
later.

**Design: stack profiles as a table, not branching logic** — each stack is a
`stack_<name>_detect` / `stack_<name>_app_root` (and later `_lint`/`_test`/`_explore_areas`/
plan-critic overlay) function set, registered in `STACK_PROFILES`; `detect_stack()` dispatches
to the first match instead of `if`/`elif` chains scattered through the codebase (the same
"table over branching" shape the Hermes learnings doc calls out, learnings #5).

### Stage 1 — Refactor Rails into the profile shape [DONE — 2026-09-07]

Pure extraction, verified zero behavior change (all four original `detect_app_root` cases —
Gemfile at root, one level down, multiple Gemfiles, no Gemfile — checked to produce byte-
identical output/errors via `detect_stack`):

- `helpers.sh`: `detect_app_root()` → `stack_rails_detect()` (boolean predicate) +
  `stack_rails_app_root()` (the original resolution logic, unchanged) + `detect_stack()`
  (dispatch loop over `STACK_PROFILES=(rails)`, falls through to `stack_rails_app_root`'s
  original error when nothing matches — no `generic` profile registered yet).
  `bundle_exec`/`run_rubocop_check`/`run_rspec_check` intentionally left untouched — generalizing
  those now, with only one stack existing, would be speculative infrastructure with no second
  consumer yet (Stage 3 is where that pays off).
- `migite:187` — call site updated to `detect_stack`; `README.md` and `docs/migite.md`
  references to `detect_app_root` updated to match (learnings #6 doc-drift discipline, applied
  to itself).

### Stage 2 — Add the `generic` profile [DONE — 2026-09-07]

The actual "runs on any project" unlock. `STACK_PROFILES=(rails generic)`, with
`stack_generic_detect` always matching (last in the list) and `stack_generic_app_root` setting
`APP_ROOT=$REPO_ROOT` unconditionally. `review.sh` skips rubocop/rspec entirely when
`$STACK == generic` (still runs the LangGraph review on the diff + plan, just without tooling
logs — writes a stub message to `$RUBOCOP_LOG`/`$RSPEC_LOG` instead of leaving them absent, in
both the initial run and the commit-gate `_rerun_checks_and_review` loop); `require_cmd bundle`
in `migite` moved to after `detect_stack` and gated on `[[ "$STACK" == "rails" ]]`.
`migite-plan`'s `route_to_explorers` picks `GENERIC_EXPLORE_AREAS` (one broad
language-extension glob covering ~15 common languages) instead of the Rails-MVC `EXPLORE_AREAS`
when `$STACK == generic`, with vendor/build directories (`node_modules`, `vendor`, `.venv`,
`dist`, `build`, etc.) filtered out of every glob's results — the same filter applies to the
Rails patterns too, harmlessly, since they're already scoped to `app/`/`spec/`/`db/`/`config/`.
Added `--stack <name>` override (same shape as the existing `--type` flag) on both `migite` and
`migite-plan`, validated against `STACK_PROFILES` with a clear error on an unknown value.

Verified: `detect_stack` on a repo with no Gemfile now resolves to `generic` (previously hard
error) both via auto-detection and via explicit `--stack generic` override; an unknown
`--stack` value errors clearly instead of silently misbehaving.

**Found and fixed a live bug in Phase 0 while testing this stage:** `command -v python3` returns
success for an asdf shim even when no Python version is selected for the current directory (the
shim binary always exists on PATH; it only fails *when invoked*) — so Phase 0's `MIGITE_PYTHON`
resolution would have silently picked a broken shim on exactly the kind of repo this stage exists
to support (confirmed against this repo itself, which has no Python pinned in `.tool-versions`).
Fixed by verifying `python3 --version` actually succeeds before trusting `command -v`'s result,
falling back to the hardcoded path otherwise — same fallback behavior as before, now reachable
under the condition that actually needs it.

### Stage 3 — Add a second real stack (only when there's an actual task to run against it)

Don't design this in the abstract — let its `architecture_critic.md`/plan-template overlay and
`explore_areas` earn themselves from real findings the way Rails' did (see
`migite-improvements.md`'s history). This is also when generalizing `bundle_exec`/
`run_rubocop_check`/`run_rspec_check` into stack-dispatched `stack_<name>_lint`/`_test`
actually pays for itself, instead of being abstraction with one caller.

---

## Phase 2 — `migite doctor` (learnings #2) [DONE — 2026-09-07]

A new, read-only, non-mutating subcommand, mirroring `hermes cron doctor`'s spec shape exactly:
prints grouped issues, exits non-zero if any are found, touches nothing.

### Dispatch

Special-case `"${1:-}" == "doctor"` at the top of arg parsing in `migite` (same shape as the
existing `--amend`/`--jira` special-casing), consuming the rest of argv as doctor's own flags
(`--repo <path>`, default `$PWD`), and branch out to `run_doctor` before any of `run_plan` /
`run_implement` / `run_review` are reached — skip `detect_app_root`/`bundle`/`require_cmd bundle`
entirely for this path, since doctor must work even against a repo with no Gemfile.

### Checks (v1 — pick the highest-value 3, extend later)

1. **Scratchpad/vault sync drift** — for every `$REPO_ROOT/scratchpad/<slug>/*.md`, confirm a
   same-named, same-or-newer file exists under the corresponding `dev-log/<org>/<repo>/<slug>/`.
   Flag mismatches (missing vault copy, or vault copy older than scratchpad — a `sync_artifact`
   call that didn't happen).
2. **Orphaned sentinels** — `.plan.done` / `.review.done` present in a scratchpad dir whose
   `plan.md`/`review.md` is missing or empty (a crashed LangGraph run that touched the sentinel
   file before actually finishing — check `write_outputs` in `migite-plan` for whether sentinel
   touch is truly last; if not, that's a bug this check would be built to catch).
3. **Tool resolution** — `MIGITE_PYTHON` / `python3` / `claude` / `bundle` all resolve to an
   existing executable (catches exactly the Phase 0 class of bug recurring in the future).

### Output shape

```
migite doctor

✔ Tool resolution: all commands found
⚠ Scratchpad/vault drift (2):
  - scratchpad/bb-3136/review.md is newer than dev-log/.../bb-3136/review.md
  - scratchpad/old-task/plan.md has no vault counterpart
✔ Sentinels: none orphaned

2 issue(s) found.
```

Implemented in `migite.d/doctor.sh` (`run_doctor`), dispatched from `migite` before Setup, exactly as scoped above — all three v1 checks, `--repo <path>` override, non-mutating, exits 1 iff issues found.

**Caught a real bug on its first real run.** `run_deliver` (`migite.d/deliver.sh`) appends a `> Knowledge: [[...]]` wikilink footer to `$REVIEW_FILE` after knowledge capture, but never called `sync_artifact` afterward — so the vault mirror of `review.md` has permanently diverged from the scratchpad copy at that point, on every run, since knowledge capture was added. `migite doctor` flagged it immediately (`add-a-design-principles-section-to-docs-migite-md/review.md — scratchpad is newer than vault`) on the very first invocation, against real historical data from this session's own dogfood run. Fixed: added the missing `sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"` call.

Also surfaced pre-existing drift on two older tasks (`improve-auto-heal`, `line-whenever-jira-is-absent-and-mv-s-both-the-vau`) predating this session — left as-is per doctor's read-only contract; worth investigating separately rather than guessing at a retroactive fix for data whose original sync context isn't known.

Exit 0 if clean, 1 if any issues found — same contract as `hermes cron doctor`.

**Effort:** ~half a day for the 3 checks above. **Risk:** low — new, isolated code path; cannot
affect the existing plan/implement/review pipeline since it exits before touching it.

---

## Phase 3 — Minimal test coverage for migite's own detection logic (learnings #1) [DONE — 2026-09-08]

The biggest-effort item, and the one with the most direct payoff given `migite-improvements.md`'s
bug history is almost entirely regressions in this exact class of logic.

**Constraint carried over from CLAUDE.md: no new dependency (bats-core, pytest-for-bash, etc.)
without flagging it first** — flagging it now: a dependency-free approach is possible and is
what's proposed below, but bats-core would give better assertions/output if you're open to adding
a dev-only dependency. Recommendation is to start dependency-free and revisit if the manual
harness gets unwieldy.

### Shape

`tests/run.sh` — sources `migite.d/helpers.sh` directly (no `set -e` side effects from the main
script, since helpers.sh is meant to be sourced standalone), builds small fixture git repos under
a tmp dir per test, calls the function under test, asserts on output, tracks pass/fail count,
exits non-zero on any failure. One test file per function group to start:

- `tests/detect_base_branch_test.sh` — repo with `origin/HEAD` set, repo with only `master`,
  repo with neither (should fall back sanely, not silently return empty — this was a real
  2026-08-05 bug per `migite-improvements.md`).
- `tests/changed_files_test.sh` — tracked-only changes vs. untracked `.rb`/`_spec.rb` files
  present (the untracked-file blind spot recurring across at least 4 separate
  `migite-improvements.md` entries — 2026-07-23, 2026-08-05, 2026-08-11, 2026-08-12, 2026-08-17).
- `tests/tooling_failed_test.sh` — canned log text for `Bundler::GitError`, `No version is set
  for command`, `0 examples` + `ConnectionBad`, `0 examples` + `LoadError` — assert each is
  detected as a failure, and a clean rubocop/rspec log is not.

### Migite_paths.py

Already Python — add `tests/test_migite_paths.py`, plain `unittest` (stdlib, no new dependency),
covering `detect_base_branch`/`detect-org` against fixture repos the same way.

**Effort:** ~1-2 days for the four fixture suites above (the highest-recurrence bug classes
first; extend later). **Risk:** none — purely additive, doesn't touch runtime behavior.

Implemented as scoped above, dependency-free: `tests/run.sh` (discovers `tests/*_test.sh`,
sources `migite.d/helpers.sh` standalone, tracks pass/fail, exits non-zero on any failure) plus
`tests/detect_base_branch_test.sh`, `tests/changed_files_test.sh` (covers `changed_ruby_files`/
`changed_spec_files`, including the working-tree-delete exclusion from the same bug history), and
`tests/tooling_failed_test.sh` — 18 checks, all passing. `tests/test_migite_paths.py` adds
`unittest`-based coverage for `detect_base_branch`/`detect_org` against fixture repos,
independent of the existing `--self-test` CLI flag (which already covers `resolve_run_dir`/
`slugify`/`extract_ticket_key` and is left as-is, not duplicated) — 7 tests, all passing.

---

## Phase 4 — Documentation-only items (learnings #5, #6) [DONE — 2026-09-07]

Add a short "Design principles" section near the top of `docs/migite.md`:

- **Narrow core, capability at the edges** (learnings #5): before adding new bash logic to
  `migite.d/*.sh`, check whether the same result is reachable by editing a prompt/template
  (`~/.claude/commands/*.md`) instead. Cite the plan-template Open-Questions fix and the
  testing-plan regeneration fix as the two concrete precedents.
- **Doc-drift checklist** (learnings #6): when renaming a flag, phase, or file path, grep
  `README.md` and `docs/*.md` for the old name before calling the change done. Cite the
  `--amend`/`--amend-file` documented-before-implemented bug as the reason this is a checklist
  item and not just a nice-to-have.

**Effort:** ~20 min. **Risk:** none.

Implemented as `### Design principles` in `docs/migite.md`, placed after Audit mode (a `###`
sibling of the four mode sections) rather than "near the top" as scoped above — that placement
makes it a real outline sibling instead of nesting the mode sections under it. Both principles
are stated as scoped, each with its cited precedent.

---

## Phase 5 — Knowledge consolidation (learnings #4) and facade-size watch (learnings #7) [DONE — 2026-09-08]

Both explicitly marked "nothing urgent" in the learnings doc. Not scheduled as standalone work;
folded into Phase 2's `migite doctor` as two more checks once the v1 checks above are in and
proven useful:

- `knowledge.md` duplicate-entry heuristic (e.g. flag entries whose first line normalized-matches
  another entry's first line) — surfaced as a doctor warning, not an automatic dedup.
- `helpers.sh` line count vs. the ~2,000-line/~300-line-function threshold Hermes uses as its own
  split trigger — a single `wc -l` check, informational only.

**Effort:** ~1 hour once Phase 2 exists. **Risk:** none.

Implemented in `migite.d/doctor.sh`. The duplicate-entry check normalizes each `knowledge.md`
bullet (wikilink stripped, lowercased, punctuation stripped) and flags any that repeat an earlier
entry's normalized text — verified against a fixture with two near-identical bullets (differing
only in punctuation and their trailing review wikilink), correctly flagged as one duplicate while
two genuinely distinct bullets were left alone. The size watch is a plain `wc -l` against
`migite.d/helpers.sh` (currently 566 lines, well under the ~2,000-line threshold) — printed with
an `ℹ` marker and never added to `$issues`, so it can't fail doctor, only inform it.

---

## Suggested order

Phase 0 (done) → Phase 1 Stage 1 (done) → Phase 1 Stage 2 (done) → Phase 2 (done) → Phase 3 (done)
→ Phase 4 (done) → Phase 5 (done). All phases in this plan's suggested order are now complete.
Migite can now run on itself (and any non-Rails repo) via the `generic` stack — worth an actual
end-to-end dogfood run before moving on, since Stage 2 was verified function-by-function but not
yet exercised through a full `migite` invocation. Phase 1 Stage 3 (a second real stack) stays
deferred until there's an actual task to run against it, per its own section above — the only item
in this document left open.

### Dogfood run [DONE — 2026-09-07] — Stage 2 confirmed working, one pre-existing bug found and fixed

Ran `migite --intake ... --type refactor --stack generic` against this repo (task: add a Design
Principles section to `docs/migite.md`, itself Phase 4 below). Confirmed working exactly as
designed: `detect_stack` → `generic`, no `bundle` requirement, `migite-plan` fanned out
`GENERIC_EXPLORE_AREAS` (`Fanning out 1 explorers in parallel (stack=generic)`) instead of the
Rails split, and `generate_testing_plan` correctly recognized there's no Rails app and proposed
testing the stack-profile detection itself with fixture repos instead of a Rails-console script.

Also surfaced a real, reproducible, **pre-existing** bug unrelated to the stack-profiles work:
`migite-plan`'s `synthesize_plan` and `refine_plan` nodes could return a one-line stub
confirmation ("Plan written to Obsidian...", "Revised plan written... all findings addressed...")
instead of the actual plan document, and the only existing safety net (`refine_plan`'s
heading-overlap check) had a blind spot — `if draft_headings and ...` silently no-ops whenever
the pre-refine draft has zero `#`-headings, which is indistinguishable from a stub without
inspecting the *output* directly. Hit on 2 of 2 real attempts in this run. **Fixed**: added
`looks_like_stub()` (checks the output directly — heading count and length, no blind spot) and
applied it to both `synthesize_plan` (previously had zero validation — retries once, then
hard-fails the run rather than propagating a broken draft to the critic/refine/testing-plan
nodes downstream) and `refine_plan` (now checked unconditionally alongside the existing overlap
ratio, closing the blind spot). Verified against both real stub outputs from this run plus a
realistic full plan document.

The architecture critic also caught a real accuracy issue in `docs/hermes-agent-learnings.md`
(this doc's sibling) during the same run — see that file's `#5` for the correction. Not a bug in
migite; a documentation overstatement from an earlier session, caught by dogfooding migite's own
critic on a task about that very documentation.
