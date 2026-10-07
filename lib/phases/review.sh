#!/usr/bin/env bash
# lib/phases/review.sh — Phase 3 (rubocop/rspec + LangGraph review) and
# the commit gate.
#
# Sourced by migite. run_review expects PLAN_FILE, IMPLEMENTATION_FILE,
# REVIEW_FILE, REVIEW_VAULT, REPO_ROOT, SCRATCHPAD_DIR, TASK_DIR, RUN_SCRATCH_DIR,
# RUN_VAULT_DIR, TASK_SLUG, MIGITE_HOME, STACK to be set (plus AMEND_MODE/AMEND_NUM, which title an amend
# run's fix rounds), and sets CHANGED_RUBY, RUBOCOP_LOG, RSPEC_LOG, RUBOCOP_FINAL_OFFENSES,
# TOOLING_ERROR, COMMIT_GATE_ATTEMPTS — all read later by Phase 4.5's
# self-improvement prompt and by show_commit_context. Also sets
# CHANGED_FRONTEND, FRONTEND_LINT_LOG and FRONTEND_LINT_DIRTY: when the diff
# touches views or JavaScript (changed_frontend_files), the frontend linters
# run, migite-review adds its frontend reviewer, and the optional browser check
# (frontend.browser_check, run_browser_check below) can run before the review.
#
# Changed-file lists come from lib/stack.sh's changed_*_files (tracked union
# untracked) — never an inline `git diff | grep`, which is how untracked new
# files kept slipping past lint/test/review. Tooling failures come from
# tooling_failed for the same reason.
#
# A run resumed at the commit gate (MANIFEST_ENTRY_STATUS=pending_gate, see
# lib/manifest.sh) re-runs the checks, then reuses review.md instead of reviewing
# again when the code is the same as the code it reviewed (RESUME_REVIEW_FINGERPRINT:
# REVIEWED_FINGERPRINT, the tree_fingerprint taken after each review, which a q
# at the gate records in run.json).
#
# When $STACK == "generic" (no recognized stack profile matched — see
# detect_stack in lib/stack.sh), rubocop/rspec are skipped entirely, both here
# and in the commit-gate re-run loop; the LangGraph review still runs against
# the plan + diff, just without tooling logs. A configured profile
# (stacks.<name>) runs its own lint and test commands instead
# (run_stack_review_checks), into the same RUBOCOP_LOG / RSPEC_LOG variables.

# run_stack_review_checks - Phase 3's lint and tests for a configured profile:
# autofix then lint over the changed files the profile's `source` globs select,
# then the test command over its `specs` matches (lib/stack.sh run_stack_lint /
# run_stack_test). Exit codes decide; one that could not start (126/127) sets
# TOOLING_ERROR rather than reading as a failure. Writes RUBOCOP_LOG and
# RSPEC_LOG, the names every later reader takes, and sets STACK_LINT_RESULT /
# STACK_TEST_RESULT (passed | failed | skipped | unavailable) for the gate.
run_stack_review_checks() {
  local rc
  TOOLING_ERROR=""
  echo ""
  log "Running the $STACK lint on changed files..."
  rc=0
  run_stack_lint "$BASE_BRANCH" "$RUBOCOP_LOG" true || rc=$?
  STACK_LINT_RESULT=$(stack_check_result "$rc" "$RUBOCOP_LOG")
  case "$STACK_LINT_RESULT" in
    passed)  success "Lint clean" ;;
    failed)  warn "Lint problems remain after autofix (exit $rc); the review will address them" ;;
    skipped) warn "$(head -1 "$RUBOCOP_LOG")" ;;
    unavailable)
      TOOLING_ERROR="The $STACK lint could not start (exit $rc): $(cfg "stacks.$STACK.lint")"
      warn "$TOOLING_ERROR" ;;
  esac
  echo ""
  log "Running the $STACK tests on changed files..."
  rc=0
  run_stack_test "$BASE_BRANCH" "$RSPEC_LOG" || rc=$?
  STACK_TEST_RESULT=$(stack_check_result "$rc" "$RSPEC_LOG")
  case "$STACK_TEST_RESULT" in
    passed)  success "Tests passed" ;;
    failed)  warn "Tests failed (exit $rc)" ;;
    skipped) warn "$(head -1 "$RSPEC_LOG")" ;;
    unavailable)
      TOOLING_ERROR="${TOOLING_ERROR:-The $STACK tests could not start (exit $rc): $(cfg "stacks.$STACK.test")}"
      warn "The $STACK tests could not start (exit $rc)" ;;
  esac
  return 0
}

# ensure_testing_plan - plan.testing_plan_when: review. migite-plan left testing-plan.md
# out; this writes it from plan.md and the change as built, before the browser check and
# the reviewers read it. The change is the diff capped at ui.prompt_diff_max_bytes
# (prompt_diff) plus the name of every changed file, since the diff leaves untracked new
# files out. A testing plan that exists is kept (it carries amendment and fix-round
# edits), so a resume, an amend run and a re-review never write over one. A failed or
# empty call warns and goes on without it: the testing-plan reviewer reports it missing,
# and the next re-review tries again. Needs MIGITE_PYTHON, PLAN_FILE, BASE_BRANCH,
# TESTING_PLAN_FILE and TESTING_PLAN_VAULT; reads CHANGED_FRONTEND when run_review set it.
ensure_testing_plan() {
  [[ "$(cfg plan.testing_plan_when plan)" == "review" ]] || return 0
  [[ -s "$TESTING_PLAN_FILE" ]] && return 0
  if [[ ! -s "$PLAN_FILE" ]]; then
    warn "plan.testing_plan_when is review but there is no plan.md to write the testing plan from"
    return 0
  fi
  local change_file out rc=0
  change_file=$(mktemp)
  out=$(mktemp)
  {
    prompt_diff "$BASE_BRANCH"
    printf '\nFiles changed (tracked and new):\n'
    changed_all_files "$BASE_BRANCH" || true
  } > "$change_file"
  local -a args=(--plan "$PLAN_FILE" --out "$out" --diff "$change_file")
  [[ -n "${CHANGED_FRONTEND:-}" ]] && args+=(--frontend)
  log "Writing the testing plan from the plan and the diff (plan.testing_plan_when: review)..."
  "$MIGITE_PYTHON" -m migite.testing_plan --repo-root "${REPO_ROOT:-$PWD}" "${args[@]}" || rc=$?
  if [[ $rc -eq 0 && -s "$out" ]]; then
    mv "$out" "$TESTING_PLAN_FILE"
    sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
    success "testing-plan.md written to $TESTING_PLAN_FILE"
  else
    rm -f "$out"
    warn "The testing plan could not be written; reviewing without it"
  fi
  rm -f "$change_file"
  return 0
}

# run_browser_check - Phase 3.1, opt-in via frontend.browser_check (off | ask |
# on). Only when the diff touches the frontend and a testing plan exists: an
# interactive session in which the agent walks the testing plan's browser steps
# with whatever browser tool it has (a Playwright MCP server, a Chrome
# extension) and writes a PASS / FAIL / SKIPPED report that migite-review's
# frontend reviewer reads. Nothing here is committed; it's evidence for the
# review. An agent with no browser tool reports SKIPPED rather than guessing.
# A report left by an earlier run of this task is reused - delete
# browser-check.md to run it again.
run_browser_check() {
  local mode
  mode="$(cfg frontend.browser_check off)"
  [[ "$mode" == "off" || -z "${CHANGED_FRONTEND:-}" ]] && return 0
  if [[ ! -f "$TESTING_PLAN_FILE" ]]; then
    warn "frontend.browser_check is $mode but there is no testing-plan.md to follow - skipping the browser check"
    return 0
  fi
  local vault_file="$TASK_DIR/browser-check.md"
  resume_from_vault "$BROWSER_CHECK_FILE" "$vault_file"
  if [[ -s "$BROWSER_CHECK_FILE" ]]; then
    log "Reusing $BROWSER_CHECK_FILE (delete it to run the browser check again)"
    return 0
  fi
  if [[ "$mode" == "ask" ]]; then
    read_gate_choice "BROWSER CHECK" "Views or JavaScript changed. Walk the testing plan in a browser before the review? [y/N]: "
    if [[ ! "${GATE_CHOICE:-}" =~ ^[yY]$ ]]; then
      log "Browser check skipped"
      return 0
    fi
  fi

  echo ""
  log "Phase 3.1 - Browser check"
  local BROWSER_PROMPT
  BROWSER_PROMPT="You are checking a Rails change in a real browser before it goes to code review. Report only: do not change any application code, specs or config in this session.

## Testing plan (follow its browser steps)
$(cat "$TESTING_PLAN_FILE")

## Views and JavaScript changed in this diff
$CHANGED_FRONTEND

## Instructions
- Use the browser automation tool you have in this session (for example a Playwright MCP server or a Chrome extension). If you have none, do not substitute curl or guess: write the report below with \`Result: SKIPPED - no browser tool available\` and stop.
- The app must be running locally. If it isn't, start it the way this repo does (bin/dev, or bin/rails server) in the background, and note the URL you used.
- Run the testing plan's seed script only against the local development database, never against staging or production.
- Walk every verification step that happens in the browser. For each one, record what you did, what you saw, and PASS or FAIL. Where the plan expects Turbo to update the page in place, say whether it did or whether the whole page reloaded.
- Record JavaScript console errors and failed network requests (4xx/5xx) you saw, even on steps that passed.
- When done, run the testing plan's teardown script and stop any server you started.
- Write the report to $BROWSER_CHECK_FILE in exactly this shape:

# Browser check
Result: PASS | FAIL | SKIPPED - <one line why>
URL: <the base URL you used>

## Steps
1. <step> - PASS | FAIL - <what you saw>

## Console and network errors
<each error, or \"none\">

## Notes
<anything a reviewer should know, e.g. a step you could not run and why>"

  run_phase "Browser check" "$BROWSER_CHECK_FILE" "$BROWSER_PROMPT"
  if [[ -s "$BROWSER_CHECK_FILE" ]]; then
    sync_artifact "$BROWSER_CHECK_FILE" "$vault_file"
    success "Browser check written to $BROWSER_CHECK_FILE"
  else
    warn "The browser check session wrote no report - reviewing without it"
  fi
}

run_review() {
  echo ""
  log "Phase 3/4 — Reviewing"

  resume_from_vault "$REVIEW_FILE" "$REVIEW_VAULT"

  RUBOCOP_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-rubocop.txt"
  RSPEC_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-rspec.txt"
  FRONTEND_LINT_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-frontend-lint.txt"
  BROWSER_CHECK_FILE="$SCRATCHPAD_DIR/browser-check.md"
  CHANGED_FRONTEND=""
  FRONTEND_LINT_DIRTY=false
  STACK_LINT_RESULT=""
  STACK_TEST_RESULT=""
  if stack_is_profile; then
    RUBOCOP_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-lint.txt"
    RSPEC_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-test.txt"
  fi
  local RUBOCOP_FINAL_LOG=""

  # One frontend lint sweep over $CHANGED_FRONTEND (autofix and check in the
  # same run, like rubocop above), then what it found: sets FRONTEND_LINT_DIRTY,
  # and TOOLING_ERROR when a linter couldn't run at all.
  _run_frontend_lint_and_report() {
    run_frontend_lint_check "$CHANGED_FRONTEND" "$FRONTEND_LINT_LOG" true || FRONTEND_LINT_DIRTY=true
    local fe_tooling_msg
    if fe_tooling_msg=$(tooling_failed "$FRONTEND_LINT_LOG"); then
      warn "$fe_tooling_msg - the frontend lint did not actually run"
      TOOLING_ERROR="${TOOLING_ERROR:-$fe_tooling_msg}"
      FRONTEND_LINT_DIRTY=false
    elif [[ "$FRONTEND_LINT_DIRTY" == "true" ]]; then
      warn "Frontend lint problems remain after autofix; the review will address them"
    elif grep -q '^== ' "$FRONTEND_LINT_LOG"; then
      success "Frontend lint clean"
    else
      warn "$(head -1 "$FRONTEND_LINT_LOG")"
    fi
    return 0
  }
  RUBOCOP_FINAL_OFFENSES=0
  TOOLING_ERROR=""
  local CHANGED_SPECS

  if [[ "$STACK" == "generic" ]]; then
    echo ""
    warn "Generic stack — no lint/test tooling configured, skipping rubocop/rspec"
    CHANGED_RUBY=""
    CHANGED_SPECS=""
    echo "Generic stack — no lint tooling configured." > "$RUBOCOP_LOG"
    echo "Generic stack — no test tooling configured." > "$RSPEC_LOG"
  elif stack_is_profile; then
    CHANGED_RUBY=""
    CHANGED_SPECS=""
    run_stack_review_checks
  else
    echo ""
    log "Running rubocop on changed Ruby files..."
    CHANGED_RUBY=$(changed_ruby_files "$BASE_BRANCH")
    if [[ -n "$CHANGED_RUBY" ]]; then
      # One sweep: autocorrect and check in the same rubocop invocation, rather
      # than check → autocorrect → recheck as three separate runs.
      run_rubocop_check "$CHANGED_RUBY" "$RUBOCOP_LOG" true || true
      local rubocop_tooling_msg
      if rubocop_tooling_msg=$(tooling_failed "$RUBOCOP_LOG"); then
        warn "$rubocop_tooling_msg — rubocop did not actually run. Fix the toolchain before proceeding."
        TOOLING_ERROR="$rubocop_tooling_msg"
      elif grep -qE '[1-9][0-9]* offense' "$RUBOCOP_LOG"; then
        warn "Rubocop offenses remain after autocorrect; the review will address them"
      else
        success "Rubocop clean"
      fi
    else
      warn "No Ruby files changed — skipping rubocop"
      echo "No Ruby files changed." > "$RUBOCOP_LOG"
    fi

    CHANGED_FRONTEND=$(changed_frontend_files "$BASE_BRANCH")
    if [[ -n "$CHANGED_FRONTEND" ]]; then
      echo ""
      log "Running frontend linters on changed views and JavaScript..."
      _run_frontend_lint_and_report
    fi

    echo ""
    log "Running specs on changed files..."
    CHANGED_SPECS=$(changed_spec_files "$BASE_BRANCH")
    if [[ -n "$CHANGED_SPECS" && -n "${HEAL_RSPEC_FINGERPRINT:-}" && -s "${HEAL_RSPEC_LOG_PATH:-}" \
          && "$HEAL_RSPEC_FINGERPRINT" == "$(tree_fingerprint "$BASE_BRANCH")" ]]; then
      # Nothing changed since auto-heal's last rspec run (the rubocop sweep above
      # autocorrected nothing): the same specs on the same code, so reuse its result.
      log "Code unchanged since auto-heal's spec run, reusing its results"
      cp "$HEAL_RSPEC_LOG_PATH" "$RSPEC_LOG"
      cat "$RSPEC_LOG"
    elif [[ -n "$CHANGED_SPECS" ]]; then
      # shellcheck disable=SC2086
      bundle_exec rspec $(strip_app_prefix "$CHANGED_SPECS") 2>&1 | tee "$RSPEC_LOG" || warn "Some specs failed"
    elif [[ "$(cfg heal.full_suite_fallback true)" == "true" ]]; then
      warn "No spec files changed — running full suite (heal.full_suite_fallback: true)"
      run_rspec_full_suite "$RSPEC_LOG" || warn "Spec failures found"
    else
      warn "No spec files changed — skipping rspec (heal.full_suite_fallback: false)"
      echo "No spec files changed." > "$RSPEC_LOG"
    fi
    local rspec_tooling_msg
    if rspec_tooling_msg=$(tooling_failed "$RSPEC_LOG"); then
      warn "$rspec_tooling_msg — fix before proceeding."
      TOOLING_ERROR="${TOOLING_ERROR:-$rspec_tooling_msg}"
    fi
  fi

  # Detect files in the diff (tracked or untracked, minus scratchpad/) not
  # mentioned in any run's implementation notes - warn before review. All of
  # them, not just this run's: after an --amend the diff still holds the
  # original build, which only 00-build/implementation.md describes.
  local CHANGED_ALL
  CHANGED_ALL=$(changed_all_files "$BASE_BRANCH")
  local -a NOTES_FILES=()
  local _notes_f
  while IFS= read -r _notes_f; do
    [[ -n "$_notes_f" ]] && NOTES_FILES+=("$_notes_f")
  done < <(task_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" implementation.md)
  if [[ ${#NOTES_FILES[@]} -gt 0 && -n "$CHANGED_ALL" ]]; then
    local UNMENTIONED_FILES=""
    while IFS= read -r changed_file; do
      local fname
      fname=$(basename "$changed_file")
      if ! grep -qF "$fname" "${NOTES_FILES[@]}" 2>/dev/null; then
        UNMENTIONED_FILES="${UNMENTIONED_FILES}  - ${changed_file}\n"
      fi
    done <<< "$CHANGED_ALL"
    if [[ -n "$UNMENTIONED_FILES" ]]; then
      warn "Files in diff not mentioned in implementation notes — explain or separate them before the reviewer flags it:"
      echo -e "$UNMENTIONED_FILES"
    fi
  fi

  ensure_testing_plan
  run_browser_check

  local REVIEW_SENTINEL="$SCRATCHPAD_DIR/.review.done"
  local REVIEW_MODULE="migite.tools.review"
  local REVIEW_LANGGRAPH_ARGS=(
    --plan             "$PLAN_FILE"
    --implementation   "$IMPLEMENTATION_FILE"
    --rubocop-log      "$RUBOCOP_LOG"
    --rspec-log        "$RSPEC_LOG"
    --repo-root        "$REPO_ROOT"
    --review-output    "$REVIEW_FILE"
    --sentinel         "$REVIEW_SENTINEL"
    --base-branch      "$BASE_BRANCH"
    --stack            "$STACK"
  )
  # Amendments are approved scope; the reviewer needs the ones plan.md doesn't
  # reflect yet (this run's, until Phase 3.8 folds it in, and any whose fold was
  # skipped). Without them it graded amended code against the plan and reported
  # the difference as drift. Folded ones are already in the plan.
  local _amend_f
  while IFS= read -r _amend_f; do
    [[ -n "$_amend_f" ]] && REVIEW_LANGGRAPH_ARGS+=(--amendment "$_amend_f")
  done < <(unfolded_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" amendment.md "$PLAN_FILE")

  # Frontend inputs for migite-review, rebuilt before every review run: a fix
  # round can add or remove view/JavaScript changes. Empty on a backend-only
  # diff, which is what keeps the frontend reviewer from running.
  local -a TESTING_PLAN_REVIEW_ARGS=()
  _testing_plan_review_args() {
    TESTING_PLAN_REVIEW_ARGS=()
    [[ -f "$TESTING_PLAN_FILE" ]] && TESTING_PLAN_REVIEW_ARGS+=(--testing-plan "$TESTING_PLAN_FILE")
    return 0
  }
  local -a FRONTEND_REVIEW_ARGS=()
  _frontend_review_args() {
    FRONTEND_REVIEW_ARGS=()
    [[ -n "$CHANGED_FRONTEND" ]] || return 0
    FRONTEND_REVIEW_ARGS+=(--frontend-files "$(printf '%s' "$CHANGED_FRONTEND" | tr '\n' ' ')"
                           --frontend-lint-log "$FRONTEND_LINT_LOG")
    [[ -d "$APP_ROOT/spec/system" ]] && FRONTEND_REVIEW_ARGS+=(--system-specs)
    [[ -s "$BROWSER_CHECK_FILE" ]] && FRONTEND_REVIEW_ARGS+=(--browser-check "$BROWSER_CHECK_FILE")
    return 0
  }
  _frontend_review_args
  _testing_plan_review_args

  # review.json is removed before every run so a stale envelope can never
  # outlive the review.md it described (review_verdict prefers it when present).
  local REVIEW_JSON="${REVIEW_FILE%.md}.json"
  # Each dimension's findings, written by migite-review. A re-review passes it
  # back so only correctness and the dimensions that found something run again;
  # the first review of a run always runs every dimension, so an old copy goes first.
  local REVIEW_DIMS_JSON
  REVIEW_DIMS_JSON="$(dirname "$REVIEW_FILE")/review-dimensions.json"
  if [[ "${MANIFEST_ENTRY_STATUS:-}" == "pending_gate" && -s "$REVIEW_FILE" && -n "${RESUME_REVIEW_FINGERPRINT:-}" \
        && "$RESUME_REVIEW_FINGERPRINT" == "$(tree_fingerprint "$BASE_BRANCH")" ]]; then
    success "Code unchanged since this run stopped at the commit gate - reusing $REVIEW_FILE"
    REVIEWED_FINGERPRINT="$RESUME_REVIEW_FINGERPRINT"
  else
    rm -f "$REVIEW_SENTINEL" "$REVIEW_JSON" "$REVIEW_DIMS_JSON"
    spawn_langgraph "Reviewing" "review" "$REVIEW_MODULE" "${REVIEW_LANGGRAPH_ARGS[@]}" \
      ${TESTING_PLAN_REVIEW_ARGS[@]+"${TESTING_PLAN_REVIEW_ARGS[@]}"} \
      ${FRONTEND_REVIEW_ARGS[@]+"${FRONTEND_REVIEW_ARGS[@]}"}
    sync_json "$REVIEW_DIMS_JSON" "$(dirname "$REVIEW_VAULT")/review-dimensions.json"
    [[ -f "$REVIEW_SENTINEL" ]] || warn "migite-review may not have completed — review output may be incomplete"
    sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"
    sync_json "$REVIEW_JSON" "${REVIEW_VAULT%.md}.json"
    success "Review written to $REVIEW_FILE"
    REVIEWED_FINGERPRINT=$(tree_fingerprint "$BASE_BRANCH")
  fi

  # migite-review only reads — it doesn't touch files — so the rubocop sweep
  # above is still current. Reuse it instead of re-invoking rubocop.
  RUBOCOP_FINAL_LOG="$RUBOCOP_LOG"
  RUBOCOP_FINAL_OFFENSES=0
  if [[ -n "$CHANGED_RUBY" ]] && grep -qE '[1-9][0-9]* offense' "$RUBOCOP_LOG" 2>/dev/null; then
    RUBOCOP_FINAL_OFFENSES=$(grep -oE '[0-9]+ offense' "$RUBOCOP_LOG" | head -1 | grep -oE '^[0-9]+' || echo "?")
  fi

  notify "Phase 3 - Review ready" "Approve, fix with $(agent_field display_name), fix manually, or abort"

  # Commit gate - [y] commit / [f] the agent fixes / [n] you fix / [q] abort
  # Carries on from run.json when a resumed run re-opens this gate
  COMMIT_GATE_ATTEMPTS="${COMMIT_GATE_ATTEMPTS:-0}"
  # q: run.json records the gate as pending and the tree the review saw (not the
  # tree now: after n, your edits aren't reviewed yet), so the next invocation
  # comes back here without reviewing unchanged code again.
  _commit_gate_abort() {
    manifest_boundary review pending_gate --set-json "phases.review.gate_attempts=$COMMIT_GATE_ATTEMPTS" \
      --set "phases.review.tree_fingerprint=$REVIEWED_FINGERPRINT"
    warn "Workflow aborted - run the same command again to come back to this gate"
    exit 0
  }
  _rerun_checks_and_review() {
    if [[ "$STACK" == "generic" ]]; then
      log "Generic stack — no lint/test tooling to re-run"
      CHANGED_RUBY=""
      CHANGED_SPECS=""
    elif stack_is_profile; then
      log "Re-running checks..."
      run_stack_review_checks
    else
      log "Re-running checks..."
      CHANGED_RUBY=$(changed_ruby_files "$BASE_BRANCH")
      CHANGED_SPECS=$(changed_spec_files "$BASE_BRANCH")
      if [[ -n "$CHANGED_RUBY" ]]; then
        # One autocorrect sweep — no separate check-then-fix-then-recheck round trip.
        run_rubocop_check "$CHANGED_RUBY" "$RUBOCOP_LOG" true || true
      fi
      CHANGED_FRONTEND=$(changed_frontend_files "$BASE_BRANCH")
      FRONTEND_LINT_DIRTY=false
      if [[ -n "$CHANGED_FRONTEND" ]]; then
        _run_frontend_lint_and_report
      else
        rm -f "$FRONTEND_LINT_LOG"
      fi
      if [[ -n "$CHANGED_SPECS" ]]; then
        # shellcheck disable=SC2086
        bundle_exec rspec $(strip_app_prefix "$CHANGED_SPECS") 2>&1 | tee "$RSPEC_LOG" || warn "Some specs failed"
      elif [[ "$(cfg heal.full_suite_fallback true)" == "true" ]]; then
        run_rspec_full_suite "$RSPEC_LOG" || warn "Spec failures found"
      else
        echo "No spec files changed." > "$RSPEC_LOG"
      fi
      # Re-evaluate so a fixed toolchain clears the banner (and a newly broken
      # one sets it) instead of the first pass's verdict sticking forever.
      TOOLING_ERROR=""
      local rerun_msg
      if rerun_msg=$(tooling_failed "$RUBOCOP_LOG") || rerun_msg=$(tooling_failed "$RSPEC_LOG") \
         || rerun_msg=$(tooling_failed "$FRONTEND_LINT_LOG"); then
        TOOLING_ERROR="$rerun_msg"
        warn "$rerun_msg"
      fi
    fi
    COMMIT_GATE_ATTEMPTS=$((COMMIT_GATE_ATTEMPTS + 1))
    rm -f "$REVIEW_SENTINEL" "$REVIEW_JSON"
    ensure_testing_plan
    _frontend_review_args
    _testing_plan_review_args
    spawn_langgraph "Re-reviewing" "review-r${COMMIT_GATE_ATTEMPTS}" "$REVIEW_MODULE" "${REVIEW_LANGGRAPH_ARGS[@]}" \
      ${TESTING_PLAN_REVIEW_ARGS[@]+"${TESTING_PLAN_REVIEW_ARGS[@]}"} \
      ${FRONTEND_REVIEW_ARGS[@]+"${FRONTEND_REVIEW_ARGS[@]}"} \
      --previous "$REVIEW_DIMS_JSON"
    sync_json "$REVIEW_DIMS_JSON" "$(dirname "$REVIEW_VAULT")/review-dimensions.json"
    [[ -f "$REVIEW_SENTINEL" ]] || warn "migite-review may not have completed"
    sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"
    sync_json "$REVIEW_JSON" "${REVIEW_VAULT%.md}.json"
    REVIEWED_FINGERPRINT=$(tree_fingerprint "$BASE_BRANCH")
    # Reuse the sweep above instead of re-running rubocop a second time.
    RUBOCOP_FINAL_LOG="$RUBOCOP_LOG"
    RUBOCOP_FINAL_OFFENSES=0
    if [[ -n "$CHANGED_RUBY" ]] && grep -qE '[1-9][0-9]* offense' "$RUBOCOP_LOG" 2>/dev/null; then
      RUBOCOP_FINAL_OFFENSES=$(grep -oE '[0-9]+ offense' "$RUBOCOP_LOG" | head -1 | grep -oE '^[0-9]+' || echo "?")
    fi
  }

  # gates.commit.policy — what `y` may approve over.
  #   lenient (default, the pre-config behaviour): y always approves.
  #   strict: y is refused while any blocker below remains; a capital Y approves
  #           anyway and records the override in gate-overrides.md (synced to the vault).
  _commit_gate_blockers() {
    local -a b=()
    [[ "$(review_verdict "$REVIEW_FILE")" == "needs_fixes" ]] && b+=("review verdict is NEEDS FIXES")
    if stack_is_profile; then
      # Exit codes, not log patterns (run_stack_review_checks).
      if [[ "$(cfg gates.commit.require_green_specs true)" == "true" ]]; then
        [[ -n "${TOOLING_ERROR:-}" ]] && b+=("tooling error: $TOOLING_ERROR")
        [[ "${STACK_TEST_RESULT:-}" == "failed" ]] && b+=("tests failed ($STACK)")
      fi
      [[ "$(cfg gates.commit.require_clean_lint true)" == "true" && "${STACK_LINT_RESULT:-}" == "failed" ]] \
        && b+=("lint problems remain ($STACK)")
      [[ ${#b[@]} -gt 0 ]] && printf '%s\n' "${b[@]}"
      return 0
    fi
    if [[ "$(cfg gates.commit.require_green_specs true)" == "true" ]]; then
      [[ -n "${TOOLING_ERROR:-}" ]] && b+=("tooling error: $TOOLING_ERROR")
      local fl
      fl=$(grep -oE '[1-9][0-9]* failure[s]?' "$RSPEC_LOG" 2>/dev/null | head -1 || true)
      [[ -n "$fl" ]] && b+=("$fl in rspec")
    fi
    if [[ "$(cfg gates.commit.require_clean_lint true)" == "true" && -n "${RUBOCOP_FINAL_OFFENSES:-}" && "$RUBOCOP_FINAL_OFFENSES" != "0" ]]; then
      b+=("$RUBOCOP_FINAL_OFFENSES rubocop offense(s) remain")
    fi
    if [[ "$(cfg gates.commit.require_clean_lint true)" == "true" && "${FRONTEND_LINT_DIRTY:-false}" == "true" ]]; then
      b+=("frontend lint problems remain (erb_lint / eslint)")
    fi
    [[ ${#b[@]} -gt 0 ]] && printf '%s\n' "${b[@]}"
    return 0
  }
  _record_gate_override() {
    local blockers="$1" f="$RUN_SCRATCH_DIR/gate-overrides.md"
    {
      echo "## $DATE $(date +%H:%M) — commit gate approved over blockers"
      printf '%s\n' "$blockers" | sed 's/^/- /'
      echo ""
    } >> "$f"
    sync_artifact "$f" "$RUN_VAULT_DIR/gate-overrides.md"
  }

  while true; do
    show_commit_context
    read_gate_choice "COMMIT GATE" "Proceed? [y/f/e/n/q] (y=commit, f=$(agent_field display_name) fixes, e=edit directly, n=fix it yourself, q=abort): "
    case "${GATE_CHOICE:-}" in
      y)
        if [[ "$(cfg gates.commit.policy lenient)" == "strict" ]]; then
          local _blockers
          _blockers=$(_commit_gate_blockers)
          if [[ -n "$_blockers" ]]; then
            warn "gates.commit.policy is strict — approval refused while blockers remain:"
            printf '%s\n' "$_blockers" | sed 's/^/    - /'
            echo -e "  ${YELLOW}Fix them (f / n), or type a capital ${BOLD}Y${RESET}${YELLOW} to approve anyway — the override is recorded in gate-overrides.md${RESET}"
            continue
          fi
        fi
        success "Approved — continuing"
        break
        ;;
      Y)
        local _blockers
        _blockers=$(_commit_gate_blockers)
        if [[ -n "$_blockers" ]]; then
          warn "Approving over blockers (explicit override, recorded):"
          printf '%s\n' "$_blockers" | sed 's/^/    - /'
          _record_gate_override "$_blockers"
        fi
        success "Approved — continuing"
        break
        ;;
      e|E)
        # Direct edit of review.md — annotate, strike findings, add context.
        # The hand-edited markdown is now the source of truth, so drop the
        # envelope; review_verdict falls back to parsing the document.
        ${EDITOR:-vim} "$REVIEW_FILE"
        sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"
        rm -f "$REVIEW_JSON" "${REVIEW_VAULT%.md}.json"
        ;;
      f|F)
        # Build a fix prompt from the current review findings
        local REVIEW_CONTENT
        REVIEW_CONTENT=$(cat "$REVIEW_FILE" 2>/dev/null || echo "(review not found)")
        # Numbered within this run's folder, starting at 1, so no run can
        # overwrite another run's fix rounds.
        local FIX_NUM
        FIX_NUM=$(next_fix_round "$RUN_SCRATCH_DIR" "$RUN_VAULT_DIR")
        local FIX_TITLE="Fix round $FIX_NUM"
        [[ "${AMEND_MODE:-false}" == "true" ]] && FIX_TITLE="$FIX_TITLE (amendment $AMEND_NUM)"
        local FIX_IMPL_FILE="$RUN_SCRATCH_DIR/fix-r${FIX_NUM}.md"
        # Amendments plan.md doesn't reflect yet are approved scope too; without
        # them the fixer can "fix" amended behaviour back to the plan.
        local FIX_AMENDMENTS="" _fix_amend_f
        while IFS= read -r _fix_amend_f; do
          FIX_AMENDMENTS="${FIX_AMENDMENTS}
$(cat "$_fix_amend_f")
"
        done < <(unfolded_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" amendment.md "$PLAN_FILE")
        local FIX_PROMPT="${KNOWLEDGE_INJECT}You are fixing issues identified by an autonomous code reviewer.

## Current plan (for context)
$(cat "$PLAN_FILE" 2>/dev/null || echo "(plan not found)")
$( [[ -n "$FIX_AMENDMENTS" ]] && printf '\n## Amendments (approved after the plan; where one conflicts with the plan, the amendment wins)\n%s\n' "$FIX_AMENDMENTS" )

## Review findings — address every issue below
${REVIEW_CONTENT}

## Instructions
- Fix every Critical and Warning finding listed above
- Do not change anything not mentioned in the findings
- After fixing, write a short summary of what you changed to: ${FIX_IMPL_FILE}
- Start that summary with the heading: # ${FIX_TITLE}"

        log "Opening $(agent_field display_name) to fix review findings..."
        run_phase "Fixing review findings" "$FIX_IMPL_FILE" "$FIX_PROMPT"
        sync_artifact "$FIX_IMPL_FILE" "$RUN_VAULT_DIR/fix-r${FIX_NUM}.md"

        # Fix rounds routinely change behaviour the testing plan asserts against
        # (log lines, method signatures, argument shapes) — leaving it stale just
        # means the next review re-diagnoses the same drift as a fresh finding
        # instead of it being fixed here, in the same round that caused it.
        if [[ -f "$TESTING_PLAN_FILE" ]]; then
          log "Updating testing plan for fix round ${FIX_NUM}..."
          # --stat plus the diff capped at ui.prompt_diff_max_bytes (prompt_diff, lib/stack.sh).
          local fix_diff_file
          fix_diff_file=$(mktemp)
          prompt_diff "$BASE_BRANCH" > "$fix_diff_file"
          # Exact edits first (edit_document): a fix round changes a few steps, not the
          # whole 35-60 KB document. The full regeneration is the fallback.
          if edit_document "$TESTING_PLAN_FILE" testing_plan "Editing testing plan for fix round ${FIX_NUM}" \
               "testing-plan.md" "A review-fix round just changed the code. Edit the testing plan wherever these fixes made it wrong (log text, argument shapes, method or constant names, assertions that now contradict the fixed behaviour), and add steps for any new behaviour the fixes introduced. Keep its structure: Prerequisites, Verification steps, Teardown." \
               "Review findings that were just fixed=$REVIEW_FILE" "Fix summary for this round=$FIX_IMPL_FILE" \
               "Current diff against $BASE_BRANCH=$fix_diff_file"; then
            sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
            success "Testing plan updated for fix round ${FIX_NUM}"
          else
            warn "No usable testing-plan edits; regenerating it in full"
            local TESTING_PLAN_FIX_PROMPT="You are updating the QA/dev testing plan after a review-fix round. The testing plan must describe how to verify the CURRENT, post-fix behaviour — not what it was before this round's fixes.

## Current testing plan (supersede anything this fix round changes)
$(cat "$TESTING_PLAN_FILE")

## Review findings that were just fixed
${REVIEW_CONTENT}

## Fix summary for this round
$(cat "$FIX_IMPL_FILE" 2>/dev/null || echo "(fix summary not found)")

## Current diff against $BASE_BRANCH
$(cat "$fix_diff_file")

## Instructions
Output the FULL updated testing plan — not just the delta. Keep steps that are still valid, rewrite or remove steps this fix round invalidates (wrong log text, wrong argument shape, wrong method/constant names, assertions that now contradict the fixed behaviour), and add steps for any new behaviour the fix introduced. Preserve the existing structure (Prerequisites / Verification steps / Teardown). Output ONLY the document, no preamble."

            local testing_plan_tmp
            testing_plan_tmp=$(mktemp)
            agent_think "Updating testing plan for fix round ${FIX_NUM}" testing_plan "$testing_plan_tmp" "$TESTING_PLAN_FIX_PROMPT"
            if [[ -s "$testing_plan_tmp" ]]; then
              mv "$testing_plan_tmp" "$TESTING_PLAN_FILE"
              sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
              success "Testing plan updated for fix round ${FIX_NUM}"
            else
              rm -f "$testing_plan_tmp"
              warn "Testing plan regeneration returned empty — testing-plan.md left unchanged"
            fi
          fi
          rm -f "$fix_diff_file"
        fi

        _rerun_checks_and_review
        ;;
      n|N)
        echo ""
        echo -e "${YELLOW}  Make your fixes, then press Enter to re-run checks and re-review.${RESET}"
        local manual_ready
        read -r -p "$(echo -e "${YELLOW}  Ready to re-run checks? [Enter/q] (Enter=continue, q=abort): ${RESET}")" manual_ready
        [[ "${manual_ready:-}" =~ ^[qQ]$ ]] && _commit_gate_abort
        _rerun_checks_and_review
        ;;
      q|Q)
        _commit_gate_abort
        ;;
      *)
        warn "Invalid input — use y / f / e / n / q"
        ;;
    esac
  done
}
