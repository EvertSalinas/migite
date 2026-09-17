# Learnings from Hermes Agent (NousResearch)

> One-time research artifact, not an ongoing log. Source: https://github.com/NousResearch/hermes-agent
> (reviewed 2026-09-07: README.md, AGENTS.md, docs/ADR.md, docs/state-db-recovery.md,
> docs/cron-doctor-spec.md, a sample `optional-skills/*/SKILL.md`, and the repo's file layout).
> This is a different order of project — a multi-platform personal AI agent product with a
> gateway, cron scheduler, desktop app, and ~39k tests — so most of it doesn't transfer directly.
> What follows is the subset of design decisions that do apply to migite, each tied to a
> concrete file in one repo or the other. Distinct from `migite-improvements.md` (which is an
> append-only log of per-run production findings); this is a one-time comparative review.

---

## 1. Migite has no automated tests of its own logic — and its bug history says it needs some

Hermes's `AGENTS.md` states this as a hard rule: *"E2E validation, not just green unit mocks.
Anything touching resolution chains, config propagation, security boundaries, remote backends,
or file/network I/O must exercise the real path... Mocks hide integration bugs."*

Read against `migite-improvements.md`, that's a description of migite's entire bug history:
`Bundler::GitError` silently read as success, `0 examples + ConnectionBad` read as passing specs,
untracked files invisible to `git diff --name-only`, `git diff main` breaking on a `master`
default branch, `$HOME`-hardcoded paths breaking on a different machine. Every one of these is
exactly the category Hermes's rule targets (file I/O, resolution chains, env-dependent config),
and every one was caught in production, on a real ticket, instead of by a test — the file is a
log of the same handful of failure *shapes* recurring across a year of runs.

**Concrete suggestion:** a small `tests/` directory with fixture git repos (a repo with an
untracked `.rb` file, one whose default branch is `master`, one with a `Bundler::GitError`-style
broken `bundle exec` output canned as fixture text) exercising `helpers.sh`'s detection functions
(`changed_files()`, `tooling_failed()`, `gate_has_blockers()`) and `migite_paths.py`'s resolution
functions directly, without needing a live Claude call or a real Rails app. This wouldn't replace
the reactive `migite-improvements.md` loop, but it would stop the *same* failure shape from
needing to be rediscovered on a different repo months later.

## 2. `hermes cron doctor` — a read-only diagnostic subcommand — maps directly onto a `migite doctor`

`docs/cron-doctor-spec.md` specs a `hermes cron doctor` command in a shape worth copying
verbatim: **read-only** (does not mutate jobs or auto-repair state), **exits non-zero** when it
finds actionable issues, and reports them **grouped by job**, not as a wall of text.

Migite accumulates exactly the kind of drift this pattern exists to catch, with no single place
to check it: scratchpad (`$SCRATCHPAD_DIR`) vs. vault (`$TASK_DIR`) mirrors falling out of sync
if a run is interrupted between `sync_artifact` calls, orphaned `.plan.done`/`.review.done`
sentinels left behind by an aborted run, `.tool-versions` drift from what `bundle_exec` actually
resolves, and `MIGITE_PYTHON` (see #3 below) silently pointing at a Python that no longer exists.
Right now each of these is discovered mid-run, as a cryptic failure, on whatever task happens to
trip it.

**Concrete suggestion:** a `migite doctor [--repo <path>]` command with the same acceptance-
criteria shape as the spec above — checks scratchpad/vault sync drift, orphaned sentinels, stale
`.tool-versions` vs. `bundle_exec`'s actual resolution, and `MIGITE_PYTHON`/command-path
existence; prints grouped issues; exits non-zero if any are found; touches nothing.

## 3. Single-source path resolution — migite already has the right pattern in one place, not everywhere

Hermes's rule: *"Never hardcode `~/.hermes`. `get_hermes_home()` for code paths... Hardcoding
breaks profiles (5 bugs in PR #3575)."* Migite already converged on this independently once —
`migite-improvements.md`'s 2026-08-20 entry is exactly this bug (`IMPROVEMENTS_FILE` hardcoded to
`$HOME/.local/bin/migite-improvements.md`, fixed by a self-locating `MIGITE_HOME`) — and
`migite-plan`'s `PLAN_CMD_PATH`/`CRITIC_CMD_PATH` constants (`migite-plan:35-36`) are the pattern
applied correctly: one named constant, resolved once, imported everywhere that needs it.

Applying that same lens to the rest of the script surfaced two places where the fix from that
2026-08-20 entry didn't get generalized:

- **`migite:29`** — `MIGITE_PYTHON="${MIGITE_PYTHON:-$HOME/.asdf/installs/python/3.13.5/bin/python3}"`
  hardcodes a specific asdf Python *patch version*. An asdf upgrade (which this repo's own
  `migite-improvements.md` 2026-08-05 entry shows is a normal event — the gem-upgrade advisory
  entry) silently breaks this the same way the `IMPROVEMENTS_FILE` bug did, just for Python
  instead of the notes file.
- **`$HOME/.claude/commands/implement.md`** is hardcoded three separate times
  (`migite.d/implement.sh:16`, `migite.d/implement.sh:29`, `migite.d/plan.sh:490`) instead of a
  single `IMPLEMENT_CMD_PATH` constant next to `PLAN_CMD_PATH`/`CRITIC_CMD_PATH` — the exact
  "one resolver, every call site" shape that already exists for the other two command files just
  wasn't extended to this one.

**Concrete suggestion:** resolve `MIGITE_PYTHON` via `command -v python3` (or the asdf shim)
instead of a pinned version path, and collapse the three `$HOME/.claude/commands/implement.md`
references into one constant.

## 4. Knowledge as an append-only file will eventually need what Hermes built a whole subsystem for

Hermes's memory isn't a flat file — `hermes_state_fts.py` gives it full-text search, and
`docs/state-db-recovery.md` gives it an explicit repair/recovery story for when the index or the
file itself gets corrupted. That's a large subsystem for a large problem, but the problem it
solves is one migite already has in miniature: `knowledge.md` (`migite.d/deliver.sh:14`) is a
single markdown file that only ever grows, and `migite-improvements.md`'s own "2026-08-18 pass"
entry notes duplicate entries had to be found and merged *by hand* across runs ("Bundler::GitError
appeared twice, untracked-file detection three times").

This doesn't call for FTS5 — `knowledge.md` is nowhere near that scale — but the underlying
lesson (an append-only memory file needs a periodic consolidation pass, not just discipline)
already cost manual effort once and will again as more repos accumulate entries.

**Concrete suggestion:** nothing urgent, but worth a follow-up: an occasional (manually
triggered, or run every N tasks) dedup pass over `knowledge.md` — same shape as the
self-improvement notes consolidation that already happened once by hand.

## 5. "Keep the core narrow; capability lives at the edges" — a principle worth stating explicitly

Hermes's Footprint Ladder ranks solutions by how much permanent core surface they add, cheapest
first: extend existing code → CLI command + skill → service-gated tool → plugin → MCP server →
new core tool, "only when fundamental... and unreachable via terminal + file or an MCP server."
The core is described as "a narrow waist; capability lives at the edges," extended primarily
through skills, not by growing the core.

This matches something migite already did without naming it: the "Decision Points" document
inconsistency fixed earlier in this repo's history was solved by editing `plan.md`'s prompt
template (an edge — a skill file), not by adding new branching logic to `migite-plan`'s LangGraph
graph (the core).

The testing-plan-goes-stale fix is a partial counterexample worth being honest about, not a
clean second instance: `review.sh`'s fix-loop regeneration follows the same *shape* as
`amend.sh`'s `_regen_testing_plan` (full regeneration to a tmp file, only overwriting on
non-empty output, then `sync_artifact`), but it's a separately written, duplicated block, not a
call to that function — `_regen_testing_plan` is a local closure defined inside `amend.sh`'s
`run_amend_mode` and isn't callable from `review.sh` as-is. Reusing the pattern was still the
right call (a duplicated ten-line block is cheaper than extracting a shared helper for two call
sites), but "extended an existing mechanism" overstated it as literal code reuse. Caught by
migite's own architecture critic on a dogfood run of this exact task — a good demonstration of
why the critic step exists.

**Concrete suggestion:** no code change — just a principle worth writing down (in `docs/migite.md`
or `AGENTS.md`-equivalent for this repo) so future additions default to "can this be a prompt/
template change?" before "does this need new bash in `migite.d/`?"

## 6. Doc drift after a rename/move is a named discipline in Hermes, and migite has already been bitten by it

*"Moving a symbol means fixing its docs in the same PR: grep `website/docs`, `docs/`, `skills/`,
and every `AGENTS.md` for the old path + symbol (23 doc files went stale after the refactor)."*

Migite's own history has the same failure: `README.md` documented `--amend`/`--amend-file`
before they were ever implemented in the arg parser (`migite-improvements.md`, 2026-08-20 pass) —
the inverse of Hermes's case (docs ahead of code instead of behind it), but the same root cause:
nothing forces docs and implementation to move together.

**Concrete suggestion:** when changing a flag, phase name, or file path in `migite`/`migite.d/*.sh`,
grep `README.md` and `docs/*.md` for the old name as a matter of habit before considering the
change done — there's no CI here to enforce it (unlike Hermes's `scripts/check_compat_pointers.py`),
so it has to be a manual checklist item for now.

## 7. Validation, not new ideas: migite's facade + siblings split already matches Hermes's own pattern

Worth naming because it confirms the current structure is on the right track, not because
anything needs to change: Hermes's "Facade + siblings" rule (a thin entry point plus
topic-owning sibling files, e.g. `hermes_state.py` + `hermes_state_fts.py`,
`hermes_state_repair.py`, etc.) is exactly the shape of `migite` (facade) +
`migite.d/{plan,implement,review,deliver,amend}.sh` (siblings). The one file worth watching as
it grows is `migite.d/helpers.sh` — it's already the general-purpose catch-all for shared
detection logic; Hermes's stated threshold ("~2,000 lines or a 300-line function is the signal
to split by topic") is a reasonable trigger to reuse here if it keeps absorbing new checks.
