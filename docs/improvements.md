# Migite — Improvement Notes

> One file. Each run appends observations about what could be better.
> Review periodically and apply what makes sense.
> Items marked [IMPLEMENTED] have already been applied to the script — skip them on future passes.

## Implementation notes (2026-09-28 pass)

- **An unbounded tooling log in the auto-heal prompt killed the run with "prompt too long".**
  A 606-example rspec run with 224 failures produced a 795 KB log (a backtrace per failure);
  `run_auto_heal_loop` embedded it whole, making a ~900 KB prompt the CLI rejected in 37 ms,
  and the unguarded `agent_think` call then aborted the workflow under `set -e` — no review,
  no commit gate. Fixed on both sides: `truncate_log` (lib/stack.sh) caps each log excerpt in
  the heal prompt at `heal.prompt_log_max_bytes` (default 60000, head + tail with an elision
  marker naming the full log's path), and a failed heal call now degrades to "review sees the
  remaining failures" instead of killing the run. The gateway's `fit_prompt` pointer only
  guards argv size for `prompt_via="arg"` agents — it is not a context-window guard, so prompt
  builders must bound what they embed (migite-review already slices every input).

## Implementation notes (2026-08-20 pass)

- **`--amend` / `--amend-file` were documented in README.md but never implemented.** The arg
  parser had no case for them, so `--amend "message"` fell through to the catch-all branch,
  got stored in `$TASK`, and was then silently clobbered by a subsequent `--jira` (which
  overwrites `$TASK` unconditionally). The result: the workflow ran as an ordinary resume with
  the amendment text discarded. Implemented per the README spec — task resolution by
  `--jira` → branch name → picker of the 10 most recent task dirs, one Sonnet call producing
  `amendment-NN.md` from plan + implementation + review + diff + knowledge + feedback, a
  `[y/f/e/q]` gate, then straight into the existing Phase 2 implement/heal/review/commit/
  knowledge/PR flow with `_impl_base_prompt()` and the PR prompt amendment-aware.
- **`IMPROVEMENTS_FILE` was hardcoded to `$HOME/.local/bin/migite-improvements.md`** instead of
  resolving to this repo, so every Phase 4.5 note since 2026-08-11 landed in a stray file nobody
  reviewed (merged into this file above). Added a self-locating `MIGITE_HOME` (resolves through
  the `~/.local/bin` symlink to the real script location) and pointed `IMPROVEMENTS_FILE`,
  `PLAN_SCRIPT`, `REVIEW_SCRIPT`, and the self-improvement prompt's script-cat at it, so
  `Code/Evert/migite` stays the single source of truth regardless of which path invoked it.

## Implementation notes (2026-08-18 pass)

Two items were implemented differently from the literal text, both deliberately:

- **Dedicated rubocop-fix session** (bb-3370) — the note asked for a session that opens
  *automatically* when offenses remain. Implemented instead as an `[r]` option at the commit
  gate. Auto-opening an interactive session bypasses the gate's purpose, which is to let the
  human choose the next step; `[r]` gives the same surgical fix path (rubocop-only prompt,
  re-runs rubocop, no full re-review) without seizing control.
- **Gem-upgrade advisory** (bb-3372) — the note referenced a `PLAN_PROMPT` variable in the bash
  script. Planning has since moved to the `migite-plan` LangGraph agent, so the advisory was
  added to `synthesize_plan`, gated on `task_type == "config"`.

Several notes were duplicates across runs (Bundler::GitError appeared twice, untracked-file
detection three times) and were implemented once as shared helpers: `changed_files()`,
`tooling_failed()`, and `gate_has_blockers()` in `migite`.


## 2026-05-19 — bb-3155 [IMPLEMENTED]
- [IMPLEMENTED] The Phase 3 rubocop run found 10 autocorrectable offenses; the review session then spent time documenting those same offenses and instructing a manual `rubocop -a` fix. Migite should auto-apply `rubocop -a` on changed files immediately after the initial rubocop run, then re-run rubocop to produce a clean result log for the review session. Concrete change: in Phase 3 after the first `bundle exec rubocop` call, add: `if grep -q 'autocorrectable' "$RUBOCOP_LOG"; then bundle exec rubocop -a --format progress 2>&1 >> "$RUBOCOP_LOG" || true; bundle exec rubocop --format progress 2>&1 | tee "$RUBOCOP_LOG" || true; fi` — this eliminates autocorrectable noise from the review and lets the reviewer focus on offenses that require judgment (the 4 non-autocorrectable issues in this run: `AbcSize`, `CyclomaticComplexity`, and the two `RSpec/ExampleLength` missing disable comments).

## 2026-06-01 — bb-3227 [IMPLEMENTED]
- [IMPLEMENTED] **Spec failure count not shown at commit gate**: The rspec log had 1 failure (`NameError` in `campaigns_controller_spec.rb:111`), the review correctly returned `Verdict: NEEDS FIXES`, yet the commit gate was approved on the first attempt. Before the `gate "commit"` call, grep `$RSPEC_LOG` for `'\d+ failure'` and print the count in red above the prompt — e.g., `"⚠ 1 spec failure — see review before approving"` — so the failure is impossible to miss at the gate without scrolling back through phase output.

- [IMPLEMENTED] **Review verdict not surfaced at commit gate**: `gate "commit"` prints only the generic `"Proceed with commit? [y/n/q]"` prompt with no context. Extract the `Verdict:` line from `$REVIEW_FILE` (e.g., `grep -m1 '^## Verdict' -A1 "$REVIEW_FILE"`) and display it immediately before the gate call, colored red for `NEEDS FIXES` and green for `PASS`, so the human sees the outcome of the review without having to remember what Claude wrote.

## 2026-06-01 — bb-3228 [IMPLEMENTED]
- [IMPLEMENTED] Rubocop ran on all 553 repo files and flagged `scripts/check_rsvp_slot_n1.rb`, a file unrelated to this task — the review agent had to reason through why to ignore it. Scope the rubocop command to changed files the same way rspec already is: `CHANGED_RUBY=$(git diff main --name-only | grep '\.rb$' || true)` and pass that list to `bundle exec rubocop $CHANGED_RUBY`.

- [IMPLEMENTED] The rspec run exited with `PG::ConnectionBad` and printed `0 examples, 0 failures, 1 error occurred outside of examples` — migite emitted the generic `warn "Some specs failed"` and the commit gate still passed. Detect this pattern in `$RSPEC_LOG` (grep for `"0 examples"` combined with `"connection"`) and emit a more specific warning that names the DB as the problem, so the developer knows they approved a commit with zero test coverage exercised rather than passing tests.

## 2026-07-07 — https-apptegy-atlassian-net-browse-bb-3101 [IMPLEMENTED]
- [IMPLEMENTED] Auto-extract Jira ticket key from Atlassian URLs: when `TASK` matches `https?://.*atlassian\.net/browse/([A-Z]+-[0-9]+)`, extract the ticket key and set `JIRA_TICKET` automatically before slugifying. This run's slug (`https-apptegy-atlassian-net-browse-bb-3101`) was derived from a full URL pasted as a plain argument; the result is a 50-character directory name that will make vault navigation painful. A single `grep -oE '[A-Z]+-[0-9]+'` on `TASK` at parse time covers this.

- [IMPLEMENTED] Re-run rubocop after `run_phase "Reviewing"` and surface the result in the commit gate banner: rubocop found 16 offenses, the review correctly said "NEEDS FIXES", yet the commit gate was approved on the first attempt. The script currently runs rubocop once before the review session and never again. Add a second `bundle exec rubocop` call after the review phase exits, capture pass/fail, and include the line count in the gate prompt (e.g. `Proceed with commit? [Rubocop: 16 offenses remain] [y/n/q]`) so the human cannot approve without seeing the current state.

- [IMPLEMENTED] Grep the review file for a `## Verdict` line and print it prominently in the commit gate banner: the review document contains an explicit `**NEEDS FIXES**` or `**APPROVED**` verdict, but `gate "commit"` currently shows a generic prompt with no context. A one-line `grep -m1 'NEEDS FIXES\|APPROVED'` on `$REVIEW_FILE` piped into the gate echo would make it structurally harder to approve a flagged review by accident.

## 2026-07-10 — https-apptegy-atlassian-net-browse-bb-3279 [IMPLEMENTED]
- [IMPLEMENTED] The `bundle exec` failure (no Ruby version set in `.tool-versions`) produced only a warning line in both the rubocop and rspec logs — migite treated both as successful runs. `show_commit_context` has no check for this pattern, so the gate displayed clean results when neither tool actually ran. Fix: after each `bundle exec` call, grep the log for `'No version is set for command'` and, if matched, emit a `warn` and set a flag that `show_commit_context` surfaces as a red-line error (parallel to the `0 examples` + `ConnectionBad` check that already exists).

- [IMPLEMENTED] The review verdict was `READY TO COMMIT` but `show_commit_context` greps for `APPROVED|PASS` — so the gate displayed `Verdict: unknown — check $REVIEW_FILE` even though the review approved the commit. Fix: add `READY TO COMMIT` to the grep pattern in `show_commit_context`.

## 2026-07-21 — https-apptegy-atlassian-net-browse-bb-3284 [IMPLEMENTED]
- [IMPLEMENTED] The review flagged 4 files in `git diff main` that weren't covered by the implementation notes (`app/models/building.rb`, `app/services/buildings/lister.rb`, and two spec files). Migite could detect this gap before the review phase by comparing `git diff main --name-only` against the files listed in the implementation notes and emitting a `warn` if the diff contains files not mentioned — giving the developer a chance to explain or separate them before the reviewer flags it.

- [IMPLEMENTED] The commit gate displayed **"unknown — check review.md"** instead of green because the review verdict was `READY TO COMMIT`, which matches neither `APPROVED` nor `PASS` in `show_commit_context`. Add `READY TO COMMIT` to the approved pattern in the `elif` branch: `grep -qE 'APPROVED|PASS|READY TO COMMIT'`.

## 2026-07-23 — bb-3347 [IMPLEMENTED]

- [IMPLEMENTED] **Testing plan + QA script missing from plan.md**: The plan.md generated by migite covers implementation steps and file changes but never includes a section on how to manually verify the feature in a dev or QA environment. After the implementation section, migite should append a `## Testing Plan` block that the review agent populates with: (1) a prerequisite setup script using the Rails console to seed the exact records needed (generic emails only — `test.user@example.com`, `admin.qa@example.com`, never real addresses); (2) step-by-step curl or browser actions that exercise the changed code paths; (3) what log lines to grep for to confirm success (e.g., `grep "EventName" log/development.log`); and (4) a teardown block to remove the seeded records so the environment is clean after testing. Concrete shape expected in plan.md:

  ````markdown
  ## Testing Plan

  ### Prerequisites — seed records (Rails console)
  ```ruby
  # Run in: rails console (dev or QA)
  district = District.find_by!(subdomain: "qa-district")

  user = User.create!(
    email: "test.user@example.com",
    first_name: "Test",
    last_name: "User",
    district: district,
    role: "admin"
  )

  # <any other records required by this feature>
  puts "Seeded: user=#{user.id}"
  ```

  ### Verification steps
  1. Sign in as `test.user@example.com` in the QA environment.
  2. Hit the changed endpoint: `POST /api/v1/<resource>` with body `{ ... }`.
  3. Confirm HTTP 200 and response shape matches the serializer contract.
  4. Check the log: `grep "<expected log message>" log/development.log | tail -5`.
  5. Confirm any background job enqueued: `Sidekiq::Queue.new("<queue_name>").size` should increase by 1 (or check the QA Sidekiq dashboard).

  ### Teardown
  ```ruby
  User.find_by(email: "test.user@example.com")&.destroy
  # destroy any other seeded records
  puts "Teardown complete"
  ```
  ````

  The review agent should validate that the Testing Plan section is present and non-trivial before issuing `APPROVED` — if the section is missing or contains only a placeholder, the verdict should be `NEEDS FIXES` with an explicit note.

- [IMPLEMENTED] **Outcome files named after slugified URLs instead of ticket key**: Even after bb-3101's URL-extraction fix was applied to directory names, the outcome files produced during a run (e.g., the review file, rubocop log, rspec log, plan.md) are still named using the raw slugified form of whatever `TASK` was passed — so a full Atlassian URL produces filenames like `https-apptegy-atlassian-net-browse-bb-3347-review.md`. Fix: apply the same `grep -oE '[A-Z]+-[0-9]+'` extraction that was added for directory slugging to the `SLUG` variable used for all output file names, so every outcome file is named `BB-3347-review.md`, `BB-3347-rubocop.log`, etc., regardless of how the task was passed in. If no ticket key is found in `TASK` (plain text description), fall back to the current slugify behaviour so non-Jira tasks are unaffected.

## 2026-07-23 — https-apptegy-atlassian-net-browse-bb-3348 [IMPLEMENTED]
- [IMPLEMENTED] **Run `bundle check` before tooling, auto-install if it fails.** Both rubocop and rspec failed with `Bundler::GitError` because the `apptegy_translate` git gem (landed in BB-3347) was never checked out locally. The script's error detection only looks for `'No version is set for command'` (asdf Ruby version errors), so this bundler failure fell through silently and `show_commit_context` would have reported "Specs: all passed" — actively wrong. Fix: add `bundle check &>/dev/null || bundle install` immediately before the rubocop block in Phase 3, and add a grep for `'Bundler::GitError\|not yet checked out'` alongside the existing `'No version is set for command'` check, setting `RUBY_VERSION_ERROR=true` (or a new `BUNDLE_ERROR` flag) when matched.

- [IMPLEMENTED] **Include untracked `.rb` files in `CHANGED_RUBY` and `CHANGED_SPECS`.** The review's critical finding was that three new files were completely untracked. `git diff main --name-only` only returns tracked modified files — new untracked files are invisible to it. This means rubocop and rspec both silently skip newly created source files when the developer hasn't staged them yet, giving a false "No Ruby files changed — skipping rubocop." Fix: change the `CHANGED_RUBY` and `CHANGED_SPECS` assignments to combine `git diff main --name-only` with `git ls-files --others --exclude-standard` so untracked new files are included in both checks.

## 2026-07-23 — https-apptegy-atlassian-net-browse-bb-3347 [IMPLEMENTED]
- [IMPLEMENTED] **Bundler::GitError silently falls through to "Rubocop clean"**: When `bundle install` hasn't been run after adding a git-sourced gem, both rubocop and rspec exit with `Bundler::GitError` — none of the existing log checks (`'No version is set for command'`, `'autocorrectable'`, `'offense'`) match, so the script logs `success "Rubocop clean"` and `success "all passed"` even though neither tool ran. Fix: add `grep -q 'Bundler::GitError\|bundle install'` as the first branch in both rubocop and rspec detection blocks, set `RUBY_VERSION_ERROR=true` (or a new `BUNDLER_ERROR` flag), and surface it in `show_commit_context` the same way the Ruby version error is surfaced.

- [IMPLEMENTED] **Untracked app/spec files not warned before the review phase**: `presend_translator.rb` and its spec were untracked throughout the run and only caught by the reviewer. The existing `UNMENTIONED_FILES` check only inspects files already in `git diff main --name-only`. Add a pre-review check — `git status --porcelain | grep '^?? \(app/\|spec/\)'` — and warn if any untracked files appear under `app/` or `spec/`, prompting the developer to decide whether to stage them before review runs.

## 2026-07-29 — bb-3347 [IMPLEMENTED]
- [IMPLEMENTED] **Filter deleted files from rubocop and rspec file lists** — Both tools received stale paths because `git diff main --name-only` includes deleted files. Change the two collection lines in Phase 3 to use `--diff-filter=ACMR` (Added/Copied/Modified/Renamed — excludes Deleted): `CHANGED_RUBY=$(git diff main --name-only --diff-filter=ACMR | grep '\.rb$' || true)` and the matching `CHANGED_SPECS` line. This was the root cause of the rubocop "No such file or directory" error and the rspec "0 examples, 5 load errors" result — the actual BB-3347 spec never ran.

- [IMPLEMENTED] **Detect 0 examples + load errors as a distinct failure state at the commit gate** — The existing rspec post-run check handles `0 examples + ConnectionBad` but not `0 examples + LoadError`. Add a parallel branch: `elif grep -q '0 examples' "$RSPEC_LOG" && grep -qi 'error occurred while loading\|LoadError' "$RSPEC_LOG"` → surface it as `"⚠ Load errors — 0 examples ran, spec coverage unverified"` in `show_commit_context`. Without this, the commit gate showed "all passed" while no tests actually executed.

## 2026-08-05 — bb-3371 [IMPLEMENTED]
- [IMPLEMENTED] **Add untracked-file detection before the review phase.** The critical finding in this run — `spec/concerns/feature_flag_helper_spec.rb` was untracked, not staged — was caught by the reviewer but not by migite's pre-review check, because `CHANGED_ALL` uses `git diff main --name-only` which only sees tracked changes. Fix: alongside the existing unmentioned-files loop, also collect `UNTRACKED=$(git status --porcelain | grep '^?? ' | sed 's/^?? //')` and warn if any untracked files look like deliverables (e.g. match `*.rb` or `*_spec.rb`).

- [IMPLEMENTED] **Stop running the full rspec suite as a fallback when no spec files are in the diff.** Because the spec file was untracked, `CHANGED_SPECS` was empty and migite fell back to `bundle exec rspec` (full suite), which hit `DatabaseCleaner.clean_with(:truncation)` and required a live DB. For a gem-bump task with no logic changes, the full suite is the wrong fallback. Fix: after the untracked-file check (above), also scan for untracked `_spec.rb` files and include them in `CHANGED_SPECS`; if the result is still empty, skip rspec entirely and emit a `warn` rather than running the full suite.

## 2026-08-05 — bb-3372 [IMPLEMENTED]
- [IMPLEMENTED] **Replace hardcoded `git diff main` with a dynamic base branch.** The script uses `git diff main` in three places (rubocop filter, spec filter, unmentioned-files check), but this repo's default branch is `master`. If a `main` ref doesn't exist, all three calls fail silently (swallowed by `|| true`) and return empty — rubocop gets skipped for the wrong reason and the unmentioned-files check never runs. Fix: derive the base at setup time with `BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")` and substitute `$BASE_BRANCH` in all three diff calls.

- [IMPLEMENTED] **Inject a gem-upgrade advisory into the config planning prompt.** The implementation deviated from the plan specifically because `bundle update apptegy_feature_flags` cascaded to ~40 unrelated gems — the implementer had to restore the lockfile manually and switch to `bundle install`. The right guidance belongs in the planning session, before the command is chosen. When `TASK_TYPE=config`, append to `PLAN_PROMPT`: *"For gem upgrades: prefer editing Gemfile directly and running `bundle install` over `bundle update <gem>` — `bundle update` re-resolves all constraints and can bump unrelated gems including Rails itself."* That surfaces the trade-off at the decision point, not after the lockfile is already polluted.

## 2026-08-06 — bb-3370 [IMPLEMENTED]
- [IMPLEMENTED] **Commit gate should require explicit override when verdict is NEEDS FIXES.** The commit gate was approved on the first attempt (commit gate attempts: 1) despite the review explicitly returning NEEDS FIXES with 7 rubocop offenses. `gate()` shows the verdict in red but still accepts `y`. When `REVIEW_FILE` verdict is NEEDS FIXES or `RUBOCOP_FINAL_OFFENSES > 0`, change the gate prompt from `[y/n/q]` to `[override/fix/q]` with "override" requiring the human to type the word in full — making bypass a deliberate choice rather than a reflex keypress.

- [IMPLEMENTED] **Add a dedicated rubocop-fix session between review and the commit gate when non-autocorrectable offenses remain.** All 7 offenses in this run (metric violations, `RSpec/LetSetup`, `RSpec/ExampleLength`) cannot be resolved by `rubocop -a`, so the auto-correct branch was a no-op. The review session wrote the correct fix instructions but did not apply them; the rejection path (`n` at commit gate) re-runs the full review session, which is heavyweight. After `RUBOCOP_FINAL_OFFENSES > 0`, automatically open a targeted interactive session with the prompt: "Apply exactly the rubocop fixes listed in the review. Do not re-review — only fix the listed offenses." Then re-run rubocop and update `RUBOCOP_FINAL_OFFENSES` before showing the commit gate. This gives the human a surgical fix path rather than a choice between approving with known issues or triggering a full re-review.

## 2026-08-11 — task-20260811-142216

> Merged in from a stray copy that had been accumulating at `~/.local/bin/migite-improvements.md` — a bug in the script (`IMPROVEMENTS_FILE` was hardcoded to `$HOME/.local/bin` instead of resolving to this repo) sent every Phase 4.5 note there since 2026-08-11 instead of here. Fixed 2026-08-20; these notes are unreviewed (not `[IMPLEMENTED]`).

- **The rubocop and rspec results were silently invalid because `bundle exec` ran from `$REPO_ROOT` (`tabi/`), not the Rails app subdirectory (`tabi/rails-app/`).** The script should accept a `--app-root` flag (defaulting to `$REPO_ROOT`) and `cd` into it before every `bundle exec` call, or at minimum detect and hard-error when `bundle exec` exits with "Could not locate Gemfile" rather than logging it as a warning and continuing.

- **`git diff main` silently returned empty on a zero-commit repo, making every changed-file list empty and all scope checks meaningless.** Add a preflight check after the `git rev-parse` guard: `git rev-parse --verify main &>/dev/null || error "No 'main' branch found — cannot compute diff scope. Commit at least once before running migite."` This would have caught the issue immediately instead of letting every downstream check produce phantom-clean output.

- **Two plan-gate attempts were needed but the commit context panel shows no record of it.** `PLAN_GATE_ATTEMPTS` is already tracked but never surfaced to the reviewer. Add it to `show_commit_context` alongside the rubocop/rspec/review summary — a count of 2+ plan attempts is a signal the task scope or intake was underspecified, and the reviewer should know that before approving the commit.

## 2026-08-11 — bb-3370
- **Untracked files are excluded from Phase 3 rubocop/rspec scans.** The review explicitly flagged "7 untracked files hidden from the diff" (new service, migrations, rake task, 2 stream specs). `git diff main --name-only` only surfaces staged/committed changes — new files that haven't been `git add`-ed are silently skipped. Fix: augment `CHANGED_RUBY` and `CHANGED_SPECS` in Phase 3 by appending `git ls-files --others --exclude-standard` filtered to `.rb` and `_spec.rb`, or emit a blocking warning when `git ls-files --others --exclude-standard | grep '\.rb$'` is non-empty before the rubocop/rspec runs.

- **Brakeman is never run.** The review had to note "Brakeman: NOT RUN — blocked by sandbox permissions, needs a manual run" because the script has no Brakeman step at all. Add a `bundle exec brakeman --no-pager -q` call in Phase 3 alongside the existing rubocop/rspec block, write its output to a `BRAKEMAN_LOG`, surface any warnings in `show_commit_context`, and pass `--brakeman-log "$BRAKEMAN_LOG"` to `migite-review` so the reviewer sees the results rather than having to note the absence.

## 2026-08-12 — bb-3370
- Pass `--force-exclusion` to every `bundle exec rubocop` invocation in the script. `git diff main --name-only | grep '\.rb$'` captures `db/schema.rb`, and naming a file explicitly overrides `.rubocop.yml`'s `AllCops: Exclude` list. This run reported 14 false-positive offenses throughout the heal and review phases — all in schema.rb — requiring extra passes to determine they were irrelevant. One flag on every rubocop call in the script would eliminate this class of noise entirely.

- Supplement `HEAL_CHANGED_RUBY` and `HEAL_CHANGED_SPECS` with untracked files. The script builds those lists from `git diff main --name-only --diff-filter=ACMR`, which requires files to be staged or tracked. This run had 8 untracked files (backfill service, 3 migrations, rake task, 3 specs) that the rubocop and rspec runs never saw; the review had to flag the gap manually. Fix: append `git ls-files --others --exclude-standard | grep '\.rb$'` to both lists before running checks.

- Surface blocking-issue excerpts at the commit gate, not just the verdict line. `show_commit_context` currently extracts the verdict string (`NEEDS FIXES` / `APPROVED`) but shows nothing about *what* needs fixing. This run had 2 blocking issues and required 2 commit gate attempts — the user had to open the review file to understand what `f` would fix. Printing the first 10–15 lines after a `### Blocking` or `**Blocking` heading in `$REVIEW_FILE` (before the gate prompt) would make the `f` option actionable without leaving the terminal.

## 2026-08-17 — jira-ticket-bb-3370-please-fetch-context-from-the-
- `$CHANGED_RUBY`/`$CHANGED_SPECS` (and `$HEAL_CHANGED_RUBY`/`$HEAL_CHANGED_SPECS`) are built from `git diff main --name-only`, which never lists untracked files. In this run 19 of the ~38 new/changed files were untracked (`??` in git status — all the new jobs, models, queries, services, migrations) and were silently skipped by both the auto-heal loop and the Phase 3 rubocop/rspec runs; the review agent only caught this by separately inspecting the branch. Fix: union in `git ls-files --others --exclude-standard` everywhere these variables are computed.

- Explicit file paths passed to `bundle exec rubocop` bypass `.rubocop.yml`'s `AllCops.Exclude`, so `db/schema.rb` produced false-positive offenses whenever it landed in `$CHANGED_RUBY`. This run burned two separate "re-verification passes" re-confirming the same stale/false schema.rb output was noise. Fix: filter `db/schema.rb` (or anything in `AllCops.Exclude`) out of the file list before every `bundle exec rubocop` invocation.

- `PLAN_GATE_ATTEMPTS` hit 4 with no escalation logic — the loop just keeps re-running full exploration on `n`. Add a warning after 2-3 full-redo rejections suggesting the intake itself needs rework rather than repeatedly re-planning against the same underspecified input.

## 2026-08-18 — bb-3479
- `UNTRACKED_DELIVERABLES` (in the review phase) filters for `\.rb$` only, so this run's new deliverable, `_author.json.jbuilder`, wasn't caught by that warning even though it's exactly the kind of file that should be (it slipped through untracked; only the separate "unmentioned files" check happened to catch it because implementation notes named it). Broaden the regex to include `\.(rb|jbuilder|erb|haml)$` so new view templates trigger the same "stage before review" warning as new Ruby files.

- The pre-review `bundle check` → `bundle install` auto-remediation swallowed a real failure this run: `bundle install` hit the same `Bundler::GitError` (private git-sourced gem, no SSH access in this session) that later failed rubocop/rspec, but the script only does `bundle install 2>&1 | tail -5 || warn ...`, giving no actionable signal that this is an auth problem outside migite's control. Have this path run the same `tooling_failed` check against the `bundle install` output and, on match, warn explicitly that this needs SSH/git access fixed outside the session rather than retrying.

- Review flagged the plan's missing `## Testing Plan` section (console script + cURL examples) — a requirement this repo has hit before. `show_critic` already does a post-plan-generation check-and-warn for architecture concerns; add an equivalent check after plan generation (or at the plan gate) that greps `$PLAN_FILE` for `## Testing Plan` and warns if it's absent, so it's caught before implementation instead of surfacing in the review verdict.

## 2026-08-20 — bb-3479
- Truncate/grep `$RUBOCOP_LOG`/`$RSPEC_LOG` (and their heal-loop counterparts) down to summary lines before embedding them in `HEAL_FIX_PROMPT`, the review langgraph args, and `IMPROVEMENTS_PROMPT` — this run's rubocop output was dominated by repeated multi-page `rubocop-ast` `EnsureNode#body` deprecation stack traces (four separate dumps), which added no signal but got `cat`-ed whole into every downstream prompt, including this improvement-capture step itself.
- Add an automated Brakeman step (mirroring the existing rubocop/rspec pattern in Phase 2.5/3) — this run's review explicitly flagged "brakeman, rubocop, and rspec were each denied by the permission prompt this session" as a critical/process blocker, but migite currently has no brakeman invocation anywhere in the script, so it's left as a manual implementation-notes caveat every run instead of being enforced by a gate.

## 2026-08-26 — bb-3385
- Add a `run_brakeman_check` helper (mirroring `run_rubocop_check`/`run_rspec_check` in helpers.sh) and call it in `run_review` (review.sh), writing to a `BRAKEMAN_LOG` and passing `--brakeman-log` to `migite-review` like `--rubocop-log`/`--rspec-log`. This run's review verdict was NEEDS FIXES solely because brakeman never ran (blocked by a permission prompt inside the interactive session) — migite has no scripted, non-interactive brakeman step, so it can't guarantee this gate runs every time.
- After `detect_base_branch` in the migite entrypoint, add a check (e.g. `git rev-list --count HEAD..origin/$BASE_BRANCH`) and `warn` if the branch is behind, so drift is caught before Phase 1 instead of being discovered by the reviewer at Phase 3. This run's review had to manually explain away a 2-commit-behind diff (`dcd70e2`, `0b5b31a`) as "not scope creep" — a proactive warning at the top of the run would have surfaced this immediately instead of costing review-time investigation.

## 2026-09-03 — bb-3479
- `run_rubocop_check` (migite.d/helpers.sh) merges rubocop's stderr into stdout via `2>&1 | tee "$log"`, so this run's `$RUBOCOP_LOG` captured ~150 lines of `EnsureNode#body is deprecated` warnings with full backtraces (repeated once per triggering cop) ahead of the real "102 files inspected, no offenses" result. That log is fed verbatim into the auto-heal fix prompt, the `--rubocop-log` review input, and the Phase 4.5 improvements prompt, so every phase pays token cost for pure noise. Filter deprecation-warning lines (e.g. `grep -v -E 'is deprecated|^Called from:|rubocop-ast.*\.rb:[0-9]+:in'`) before writing `$log`, or stop merging stderr into it.
- This run's `review.md` verdict said rubocop/rspec/brakeman "were all denied by the permission layer in this non-interactive session" and told the engineer to run them manually — despite `run_review` (migite.d/review.sh) already having run both via `bundle_exec` and passed the results through `--rubocop-log`/`--rspec-log` to `migite-review` (102 files/no offenses, 1012 examples/0 failures). The review agent seems to attempt its own tool invocations rather than trusting the injected logs, producing a false blocker in the verdict. Check `migite-review`'s prompt to make sure it treats the passed-in logs as authoritative and doesn't try to re-run those checks itself.

## 2026-09-03 — bb-3136
- In `plan.sh`, the "Resuming existing intake" and "Loaded intake from vault" branches (the `[[ -f "$INTAKE_FILE" ]]` / `[[ -f "$TASK_DIR/intake.md" ]]` cases, just before the `--intake` branch) never derive `TASK_TYPE` from the intake file's `Type:` line — only the `INTAKE_FILE_ARG` branch does that (via `grep -m1 -i '^type:' "$INTAKE_FILE"`). This run shows `Task type: unknown` in the improvement-notes context even though the intake almost certainly has a `Type:` field, because the plan was resumed rather than freshly created. Add the same `Type:` grep/fallback-to-`_pick_task_type` logic to those two branches so `TASK_TYPE` survives a resumed run.

- `show_commit_context` (helpers.sh) only surfaces the review's overall verdict line, rubocop, and rspec status — it doesn't check for a gate that never ran (e.g. this run's `Brakeman **NOT RUN** (approval denied, 3 attempts - non-interactive session)`). That line was easy to miss inside `review.md`, and the commit gate was approved on attempt 1 without Brakeman ever running. Add a check in `show_commit_context` that greps `$REVIEW_FILE` for a `NOT RUN` gate marker and prints it in red/bold alongside the verdict, so a skipped security/quality gate is as visible as a failing one.

## 2026-09-07 — add-a-design-principles-section-to-docs-migite-md
- In `review.sh`'s commit gate (`y|Y)` case), `y` immediately breaks the loop with no check against the verdict `show_commit_context` just printed. This run's review verdict was NEEDS FIXES with a Critical (scope-mixing) finding, yet `Commit gate attempts: 0` shows it was approved on the first pass with no friction — the red "NEEDS FIXES" line is purely informational. Add a check before the `break`: if `grep -q 'NEEDS FIXES' "$REVIEW_FILE"`, require an explicit second confirmation (e.g. "Verdict is NEEDS FIXES — commit anyway? [y/N]") instead of treating it identically to an approved verdict.

- The scope-mixing itself (six unrelated modified files already on `multi-stack` before this task started) wasn't surfaced until Phase 3's review, after a full plan→implement cycle had already run against a diff that silently included pre-existing unrelated changes. Add a pre-flight `git status --porcelain` check right after `cd "$REPO_ROOT"` in the entrypoint (before `run_plan`/`run_amend_mode`): if the tree is already dirty, warn and list the files so the user can stash/commit unrelated work before planning begins, rather than discovering the mix at the commit gate.

## 2026-09-14 — bb-3687
- `review.sh`'s `run_review()` generates and passes `--rubocop-log`/`--rspec-log` to migite-review, but never runs `brakeman` itself — brakeman was only run ad hoc by the implementer (see this run's "Security" note in implementation.md) and never logged centrally. This run's review verdict was "conditional" specifically because the reviewer tried to run `bundle exec brakeman` itself and was denied by the sandbox permission layer. Add a `BRAKEMAN_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-brakeman.txt"` sweep in `run_review()` (mirroring the existing `run_rubocop_check`/`run_rspec_check` pattern, skipped when `$STACK == "generic"`) and pass `--brakeman-log "$BRAKEMAN_LOG"` in `REVIEW_LANGGRAPH_ARGS`, so migite-review reads a log instead of attempting live execution.

- The same review flagged `bundle exec rspec` as unverified "in this session" despite `run_review()` already having generated `$RSPEC_LOG` and passed it via `--rspec-log` — the specialist appears to re-attempt live execution rather than trusting the log it was given, hitting the same sandbox denial. Worth checking whether migite-review's specialist prompt needs to state explicitly that rubocop/rspec/brakeman results must come from the supplied logs, not from re-running the commands.

## 2026-09-14 — bb-3687
- In `review.sh`'s `run_review` (and the `_rerun_checks_and_review` retry loop), add a check like `git rev-list --count HEAD.."$BASE_BRANCH"` before calling `migite-review`, and warn (or feed into `REVIEW_LANGGRAPH_ARGS`) when the branch is behind `$BASE_BRANCH`. This run's review had to manually debunk two "critical correctness regressions" that were actually main's own code (`98f76740d`) showing inverted in `git diff main` because BB-3687 was one commit behind main — a diff-scoping issue migite could flag automatically instead of relying on the reviewer to catch it by hand.
- Add a `run_brakeman_check` helper in `helpers.sh` (mirroring `run_rubocop_check`/`run_rspec_check`) and call it from `run_review` via `bundle_exec brakeman -q`, rather than leaving Brakeman to be invoked inside the interactive implementation/fix phase. The implementation notes report it was "denied by the sandbox in this pass and the last one" — a repeated, structural failure since it's never actually run as an automated check the way rubocop/rspec are.

## 2026-09-25 — color-per
- Rubocop autocorrect rewrote `db/schema.rb` (the file the Rails dumper generates). This happened in the heal loop, in Phase 3, and again in `_rerun_checks_and_review`. The cause is `changed_ruby_files`, which lists every changed `.rb` file, including `db/schema.rb`. `changed_source_files` passes that list to `run_rubocop_check ... true`. The run lost the schema fix twice, and the review reported "RuboCop autocorrected the schema again" as its one critical finding. The `.rubocop.yml` `AllCops: Exclude` entry does not help, because RuboCop ignores excludes for files passed by name. Fix it in `lib/stack.sh` in two ways:
  - Add `--force-exclusion` to both `bundle_exec rubocop` invocations in `run_rubocop_check`.
  - Filter `db/schema.rb` and `db/structure.sql` out of `changed_ruby_files` under the rails stack, so autocorrect never touches generated files.

- The Rubocop and Rspec logs pasted into the improvement prompt are unbounded, so 165 offense lines from one generated file swamped the useful signal in `$RUBOCOP_LOG`. In `run_deliver`, truncate the `cat "$RUBOCOP_LOG"` and `cat "$RSPEC_LOG"` output (for example `head -n 60`, plus a "[N more lines]" marker). This keeps the improvement-notes prompt small and readable on runs with noisy lint output.

- The heal loop and `run_review` never check for churn. Rubocop autocorrect rewrote about 240 lines of `db/schema.rb`, and that was invisible until the review. Add a guard after each autocorrect sweep in `_heal_autofix_rubocop` and in the `run_review` sweep. Compare `git diff --numstat` (per file, before and after) against the size before the sweep. If a single file's line count changed by more than a threshold, for example 100 lines, `warn` the file name so the engineer sees it at that point instead of at the commit gate.
## 2026-09-25 — new-status
- In `lib/phases/implement.sh` (`run_auto_heal_loop`) and `lib/phases/review.sh` (`run_review`, `_rerun_checks_and_review`), pass rubocop only the files it will actually lint, and drop generated files. `changed_ruby_files` and `changed_source_files` in `lib/stack.sh` hand `db/schema.rb` to `rubocop -a` explicitly. Explicitly passed files ignore `AllCops.Exclude`, so rubocop rewrote the schema (single quotes, `20_260_925_120_200`) and still reported 3 offenses on it. This run needed a heal round to regenerate the file. Add `--force-exclusion` to both `bundle_exec rubocop` calls in `run_rubocop_check`. That makes rubocop honor the repo's own excludes for every file, so no path is special-cased.

- Add a cheap check in `run_review` before `spawn_langgraph "Reviewing"`, with a matching check in `_rerun_checks_and_review`. Warn when the diff contains a new file under `db/seeds.rb` or `db/migrate/` (or edits it) and that file has no covering spec. The reviewer caught two gaps this way: the backfill migration had no spec, and the seeds created an admin with a known password. Both were found only after the implement and heal phases. Pass the list as a `--risk-files` hint to migite-review, or add it to `implement.md`'s "Migite workflow context" block ("seeds must not create privileged users with a known password outside development", "data-migrations need a spec"). The reviewer then finds these earlier, and the commit-gate `f` round isn't spent on them.

- In `_commit_gate_blockers` and `show_commit_context` (`lib/gate.sh`), stop counting rubocop offenses that sit only in files rubocop is configured to exclude. `RUBOCOP_FINAL_OFFENSES` is parsed with `grep -oE '[0-9]+ offense' | head -1`, so this run's 3 schema offenses showed "3 offense(s) remain" even though review.md called them an accepted pattern. Under `gates.commit.policy: strict` that would have blocked `y` and forced a capital `Y` override. The `--force-exclusion` change in the first bullet fixes the root cause. If it isn't adopted, filter the `Offenses:` block in `RUBOCOP_LOG` to changed non-generated files before counting.
## 2026-09-25 — duplicate-events
- The review phase ran against stale check results. RuboCop autocorrected `spec/requests/events_spec.rb` after RSpec had run, and the RSpec log was cut off before its summary line, so the verdict could not confirm the suite passed. In `run_review` and `run_rspec_check` in `lib/phases/review.sh` and `lib/stack.sh`, run RuboCop with autocorrect first, then RSpec. Keep the RSpec summary line (`N examples, M failures`) even when the log is truncated, for example by passing `tail -n 5` of the output separately. That way the review always sees a post-autocorrect result.
- A remaining `Metrics/MethodLength` offense in spec code was flagged as a warning, and the reviewer could not tell whether the project accepts that cop in specs. In `run_rubocop_check`, or in the review prompt, add a check of whether `.rubocop.yml` excludes `spec/**` for that cop. Then mark the offense as either known-accepted or must-fix, so the reviewer doesn't have to guess.
- The testing-plan seed script used guessed attribute names (`respond_to?(:stock=)`, `role=`, no required category), so it silently skipped fields and couldn't be reproduced. In the `run_plan` testing-plan prompt in `lib/phases/plan.sh`, require the seed steps to be copied from existing spec helpers or the schema (for example `db/schema.rb` column names). Forbid `respond_to?` guards.
## 2026-09-25 — hotwire-turbo
- In `run_review`, the rubocop sweep uses `changed_ruby_files` (specs included) with autocorrect `true`. It hit spec files in `spec/requests/*_turbo_spec.rb`. Rubocop reported 12 offenses as `[Corrected]`, yet the summary still said "14 offenses detected". The gate greps `[1-9][0-9]* offense` on the log, so `RUBOCOP_FINAL_OFFENSES` is 14 even though 12 were fixed. The reviewer couldn't tell the remaining count and wrote "the log is truncated". Change: after the autocorrect sweep in `run_review` and `_rerun_checks_and_review`, run a second rubocop pass without `-a`. It should write the log the gate and the reviewer read. Alternatively, count only lines that don't match `[Corrected]` when setting `RUBOCOP_FINAL_OFFENSES`. Do the same in `_heal_autofix_rubocop`, which sets `RUBOCOP_REMAINING` from the same grep. Right now it can trigger a needless heal attempt for offenses that were already corrected. In this run the heal attempt was probably triggered partly by this.

- The reviewer says both the rubocop and rspec logs are "truncated, so the final count isn't visible". The full rspec log, with hundreds of repeated `:unprocessable_entity` deprecation warnings, is pasted into the review and improvements prompts. That pushes the summary line out. Change: add a `summarize_log <file>` helper in `lib/stack.sh`. It would drop repeated identical warning lines (`sort | uniq -c`), keep the tail, and always keep the `N examples, M failures` line and the `N offenses` line. Use it wherever `cat "$RSPEC_LOG"` / `cat "$RUBOCOP_LOG"` feeds a prompt: `IMPROVEMENTS_PROMPT` in `deliver.sh`, and the equivalent log inputs passed to `migite.tools.review`. It could also strip lines with `warning: Status code` deprecations.

- The commit gate never ran (0 attempts) although the review verdict was NEEDS FIXES with 3 critical findings. That includes a real logic bug, the Referer check running after the destroy. Also, the review didn't get the fix-round `RSPEC_LOG`. `run_review` re-ran specs on the changed files only (291 examples), while the heal loop ran the full suite (453). Change: in `run_review`, when the heal loop ended with a green full suite and the specs are unchanged, don't overwrite `RSPEC_LOG` with a narrower run. Otherwise the two disagree (291 vs 453 examples). The commit-gate banner should also print a line when the verdict is `needs_fixes` and `gates.commit.policy` is lenient. For example: "Verdict is NEEDS FIXES with N critical; y will approve anyway. Consider policy: strict". This run had criticals that the lenient gate lets through with a single `y`.
## 2026-09-25 — hotwire-turbo
- In `_rerun_checks_and_review` and the initial `run_review`, rspec always runs only `changed_spec_files`, so a change that touches shared controllers and helpers is checked only by the specs that changed. The auto-heal loop has the same gap. In this run, the fix rounds hit two failures in spec files the run had not touched: the query-count specs and the quotations show delete-link spec. The fix summary says "I did not re-run the full suite" after the last fix. Change: when a commit-gate fix round runs (`_rerun_checks_and_review`), or the diff touches `app/controllers/application_controller.rb` or a layout, run the full suite (`run_rspec_full_suite`) instead of only the changed specs. This can be controlled with a config key such as `heal.full_suite_on_shared_change`.

- The fix prompt in the commit gate (`f` choice) and the review flow have no step where migite verifies that the fix session actually ran the checks. Here, the "Fix round: review findings" section says "I did not run the suite or rubocop", and the next fix round then found a failure, which cost a second commit-gate attempt. `_rerun_checks_and_review` already re-runs the checks, but the results only appear after the review has been spawned. Change: in `_rerun_checks_and_review`, when rspec has failures or rubocop offenses remain, run a heal pass (`run_auto_heal_loop`, or an `agent_think` call using `permissions.heal`) before spawning "Re-reviewing". That way the reviewer never sees mechanical failures, and the gate attempt is not spent on them.

- The self-improvement and knowledge steps in `run_deliver` do not check whether the review verdict is still `needs_fixes` when the run ends. In this run the `review.md` embedded above still says "NEEDS FIXES" with a Critical stale-KPI finding, and the notes never say it was resolved or accepted. The commit gate approved it (via `y` in lenient mode, with no record). Change: in the `y` branch of the commit gate, when `review_verdict` is `needs_fixes` and `gates.commit.policy` is `lenient`, still call `_record_gate_override` (or write a one-line note to `gate-overrides.md`). Then `run_deliver` and the PR prompt can say which findings were knowingly accepted rather than dropping them.
## 2026-09-28 — spec-hardening
- `lib/phases/review.sh` — the commit gate's plain `y` branch (inside the `while true` loop's `case "${GATE_CHOICE:-}"`) approves unconditionally when `gates.commit.policy` is at its default, `lenient`, without ever calling `_commit_gate_blockers` or `_record_gate_override`. This run's review verdict was NEEDS FIXES even after one fix round (COMMIT_GATE_ATTEMPTS=1), with a warning explicitly marked `**This blocks the commit; fix it now.**` — yet under the default policy, approving past it leaves no trace in `gate-overrides.md`; that file only gets written on strict-mode `y`/`Y` or lenient `Y`. Make the `y` branch always call `_commit_gate_blockers`, and when non-empty, call `_record_gate_override` before approving (still without refusing the commit) — so an override of a known-bad verdict is logged regardless of policy, not just when the engineer happens to type a capital `Y`.

- `lib/phases/implement.sh` — `run_implement` has no check for a dirty working tree before the implement session opens. This run's implementation notes say the spec-side edits "already existed in the working tree ... (uncommitted)" at session start, and were merely "reviewed ... against the plan and kept" rather than reconciled — which is exactly the plan/implementation mismatch (permanent Tailwind-class "contracts") the review then flagged as a blocker. Add a `git status --porcelain` check at the top of `run_implement` that surfaces any pre-existing uncommitted diff before the session starts, so carried-over changes from a prior session get explicit scrutiny instead of being inherited and rubber-stamped.
## 2026-09-28 — token-extraction
- In `run_plan` (or `bin/migite` right after `detect_base_branch`), warn when the branch already has commits ahead of `$BASE_BRANCH` that this run didn't make. `git rev-list --count "$BASE_BRANCH"..HEAD` gives the count, and `git diff --stat "$BASE_BRANCH"...HEAD` lists what they touched. Then offer to continue, or to scope `changed_*_files` and the review diff to the current run's starting commit. This run started on `front-end-redisign`, which already carried `8fc816e`. That commit added `data-testid` attributes to eight views. The frontend reviewer ran on them, and the review's only Critical finding was that the testing plan didn't cover them. A warning at the start would have surfaced the mismatch before the plan was written.

- In `show_commit_context` (`lib/gate.sh`), add a "staged vs working tree" line. Run `git status --porcelain` and match entries whose first two columns are both non-blank (`AM`, `MM`). Show them in yellow as "index differs from working tree — `git add` before committing". If `gates.commit.policy` is `strict`, add the same condition to `_commit_gate_blockers`. In this run `script/design/decode_bundle.rb` was `AM`. The index held the older single-class version and the working tree held the refactor that had been linted and reviewed. The reviewer caught it only by reading `git status`, and a plain commit would have shipped the unreviewed code.

- In `run_review`, after the autocorrect sweep leaves `RUBOCOP_FINAL_OFFENSES > 0`, don't just hand the offense to the reviewer. If `HEAL_ATTEMPT < MAX_HEAL_ATTEMPTS`, run another `agent_think ... heal` round (the fix prompt in `run_auto_heal_loop`) first. This run stopped healing after 1 of 3 attempts. The final review sweep then left a `Metrics/MethodLength` 16/15 offense on `gunzip`, which is likely the `rubocop -a` line-wrap the reviewer described. The offense reached the commit gate with heal attempts still unused, and the only remedy left was a manual `f` round.
## 2026-10-01 — iva-c4
- `lib/stack.sh:285` (`run_rubocop_check`): Postgres was unreachable this run, so the agent hand-edited `db/schema.rb` (a generated file) instead of running `db:migrate`, and rubocop still linted it, adding 226 auto-corrected offenses that bloated the log to 49KB and buried the real findings. Exclude `db/schema.rb` from the rubocop check (or detect it's dirty-but-not-migrated and skip/flag it) instead of running full rubocop against a hand-edited generated file.

- `lib/phases/review.sh:99` (`run_review`) / `lib/phases/implement.sh:152` (`run_auto_heal_loop`): the review returned a Critical verdict (NEEDS FIXES, stale testing-plan step) but auto-heal attempts stayed at 0 and the run ended without a retry. Critical findings from the review phase should feed back into `run_auto_heal_loop` the same way implementation/test failures do, rather than leaving a NEEDS FIXES verdict as a dead end.

- `lib/common.sh:93` (`require_langgraph`) / `lib/doctor.sh:35` (`run_doctor`): the DB being down (`pg_isready` failing) wasn't caught before implementation started — it only surfaced as a workaround buried in the implementation notes. Add a DB-reachability check to `run_doctor` (or as a pre-flight in `run_implement`) for Rails stacks so this fails fast with a clear message instead of silently degrading into a hand-edited schema file.
## 2026-10-01 — small-model-additions-a4-a5-a6
- In `lib/stack.sh:285` (`run_rubocop_check`), pass `--force-exclusion` when invoking rubocop on the explicit changed-file list. This run repeatedly hit `db/schema.rb` offenses (`Metrics/BlockLength`, `Style/FrozenStringLiteralComment`) that `.rubocop.yml`'s `AllCops: Exclude` should have skipped, but the exclude is silently bypassed when files are passed explicitly on the CLI. It took two heal passes and a repo-level `.rubocop.yml` workaround to notice; fixing the invocation in migite itself would prevent this recurring for any generated/excluded file, not just this one.
## 2026-10-02 — charting-approach-svg-helper-set
- The review gate fed the reviewer the full base-branch diff (83 files) rather than this task's ~14 staged files, which is why the sole blocker was scope creep from unrelated workstreams (i18n, dark-mode, design-system) accumulated on `intake-01`. `prompt_diff`/`show_commit_context` in the review/commit gates should scope to the task's own change set (staged `git diff --cached` or the task's commit range) instead of `detect_base_branch`, so a spike isn't reviewed against unrelated work committed to the shared branch. The author's "Staging note" (manually leaving `sidebar_counts` unstaged) is a manual workaround for exactly this gap.

- No automated in-scope check exists between plan approval and commit: the plan gate passed once and the commit gate passed twice despite the diff containing a large body of unplanned work that only a human reviewer flagged. Add a check in the commit gate that intersects `changed_all_files` (scoped to the task) against the plan's "Files changed or created" list and warns/fails when files not in the plan appear, surfacing scope creep before review rather than as the deciding review finding.
## 2026-10-03 — shared-partials-filter-and-export-infrastructure-b
- `compact_rspec_log`/`truncate_log` (lib/stack.sh:477, 425) cut the rspec log before the final `N examples, N failures` summary line in this run, leaving the review step unable to confirm pass/fail. Make the truncation logic always retain the final summary line (e.g. grep for `examples?,.*failures?` and keep it regardless of head/tail line limits), not just a fixed number of head/tail lines.

- `run_rubocop_check` (lib/stack.sh:285) auto-corrected `spec/requests/quotations_csv_spec.rb` after it was already staged (`AM`), so the on-disk file diverged from the staged/reviewed diff and the reviewer had to flag "re-stage it." After rubocop auto-corrects files, `git add` those specific paths before handing off to the commit/review gate.
## 2026-10-04 — dashboard-and-quotation-extras-b15-b17
- `lib/stack.sh:truncate_log` (used by `run_rspec_full_suite`/`run_rspec_check`) is cutting the RSpec log mid-run with no final `N examples, N failures` line, as seen in this run's review verdict (NEEDS FIXES solely because the summary line was missing). Fix truncation to always preserve the final summary line (e.g. keep head + tail instead of head-only, or guarantee the last N lines containing "examples" survive), so the commit gate doesn't need a wasted re-run attempt just to confirm a suite that already passed.
## 2026-10-06 — clients-sweep-b10-b11
- `lib/gate.sh`'s `show_commit_context` (or the commit step itself) should run `git add -A`/flag `AM` (staged-then-modified) files before committing — this run had `app/helpers/clients_helper.rb`, `app/queries/clients_query.rb`, and `spec/helpers/clients_helper_spec.rb` staged, edited again, and the reviewer had to manually catch that a plain commit would ship stale versions.
- `show_commit_context`/`automata_commit_gate` should also flag untracked files whose modified counterpart is already staged (e.g. `spec/models/quotation_spec.rb` was untracked while `app/models/quotation.rb` was staged) — right now this slipped through to manual review instead of being caught by the gate.
- `truncate_log`/`compact_rspec_log` in `lib/stack.sh` cut the RSpec log before the final `N examples, 0 failures` line, so the review verdict couldn't confirm the exact count — the truncation should always preserve the trailing summary line, not just a fixed byte/line window from the start.