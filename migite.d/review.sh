#!/usr/bin/env bash
# migite.d/review.sh — Phase 3 (rubocop/rspec + LangGraph review) and
# the commit gate.
#
# Sourced by migite. run_review expects PLAN_FILE, IMPLEMENTATION_FILE,
# REVIEW_FILE, REVIEW_VAULT, REPO_ROOT, SCRATCHPAD_DIR, TASK_SLUG, MIGITE_HOME, STACK to be
# set, and sets CHANGED_RUBY, RUBOCOP_LOG, RSPEC_LOG, RUBOCOP_FINAL_OFFENSES,
# TOOLING_ERROR, COMMIT_GATE_ATTEMPTS — all read later by Phase 4.5's
# self-improvement prompt and by show_commit_context.
#
# Changed-file lists come from helpers.sh's changed_*_files (tracked union
# untracked) — never an inline `git diff | grep`, which is how untracked new
# files kept slipping past lint/test/review. Tooling failures come from
# tooling_failed for the same reason.
#
# When $STACK == "generic" (no recognized stack profile matched — see
# detect_stack in helpers.sh), rubocop/rspec are skipped entirely, both here
# and in the commit-gate re-run loop; the LangGraph review still runs against
# the plan + diff, just without tooling logs.

run_review() {
  echo ""
  log "Phase 3/4 — Reviewing"

  resume_from_vault "$REVIEW_FILE" "$REVIEW_VAULT"

  RUBOCOP_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-rubocop.txt"
  RSPEC_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-rspec.txt"
  local RUBOCOP_FINAL_LOG=""
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
        warn "Rubocop offenses remain after autocorrect — Claude will address them in review"
      else
        success "Rubocop clean"
      fi
    else
      warn "No Ruby files changed — skipping rubocop"
      echo "No Ruby files changed." > "$RUBOCOP_LOG"
    fi

    echo ""
    log "Running specs on changed files..."
    CHANGED_SPECS=$(changed_spec_files "$BASE_BRANCH")
    if [[ -n "$CHANGED_SPECS" ]]; then
      # shellcheck disable=SC2086
      bundle_exec rspec $(strip_app_prefix "$CHANGED_SPECS") 2>&1 | tee "$RSPEC_LOG" || warn "Some specs failed"
    elif [[ "$(cfg heal.full_suite_fallback true)" == "true" ]]; then
      warn "No spec files changed — running full suite (heal.full_suite_fallback: true)"
      bundle_exec rspec 2>&1 | tee "$RSPEC_LOG" || warn "Spec failures found"
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
  # mentioned in implementation notes — warn before review
  local CHANGED_ALL
  CHANGED_ALL=$(changed_all_files "$BASE_BRANCH")
  if [[ -f "$IMPLEMENTATION_FILE" && -n "$CHANGED_ALL" ]]; then
    local UNMENTIONED_FILES=""
    while IFS= read -r changed_file; do
      local fname
      fname=$(basename "$changed_file")
      if ! grep -qF "$fname" "$IMPLEMENTATION_FILE" 2>/dev/null; then
        UNMENTIONED_FILES="${UNMENTIONED_FILES}  - ${changed_file}\n"
      fi
    done <<< "$CHANGED_ALL"
    if [[ -n "$UNMENTIONED_FILES" ]]; then
      warn "Files in diff not mentioned in implementation notes — explain or separate them before the reviewer flags it:"
      echo -e "$UNMENTIONED_FILES"
    fi
  fi

  local REVIEW_SENTINEL="$SCRATCHPAD_DIR/.review.done"
  local REVIEW_SCRIPT="$MIGITE_HOME/migite-review"
  local REVIEW_LANGGRAPH_ARGS=(
    --plan             "$PLAN_FILE"
    --implementation   "$IMPLEMENTATION_FILE"
    --rubocop-log      "$RUBOCOP_LOG"
    --rspec-log        "$RSPEC_LOG"
    --repo-root        "$REPO_ROOT"
    --review-output    "$REVIEW_FILE"
    --sentinel         "$REVIEW_SENTINEL"
    --base-branch      "$BASE_BRANCH"
  )
  [[ -f "$TESTING_PLAN_FILE" ]] && REVIEW_LANGGRAPH_ARGS+=(--testing-plan "$TESTING_PLAN_FILE")

  # review.json is removed before every run so a stale envelope can never
  # outlive the review.md it described (review_verdict prefers it when present).
  local REVIEW_JSON="${REVIEW_FILE%.md}.json"
  rm -f "$REVIEW_SENTINEL" "$REVIEW_JSON"
  spawn_langgraph "Reviewing" "review" "$REVIEW_SCRIPT" "${REVIEW_LANGGRAPH_ARGS[@]}"
  [[ -f "$REVIEW_SENTINEL" ]] || warn "migite-review may not have completed — review output may be incomplete"
  sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"
  sync_json "$REVIEW_JSON" "${REVIEW_VAULT%.md}.json"
  success "Review written to $REVIEW_FILE"

  # migite-review only reads — it doesn't touch files — so the rubocop sweep
  # above is still current. Reuse it instead of re-invoking rubocop.
  RUBOCOP_FINAL_LOG="$RUBOCOP_LOG"
  RUBOCOP_FINAL_OFFENSES=0
  if [[ -n "$CHANGED_RUBY" ]] && grep -qE '[1-9][0-9]* offense' "$RUBOCOP_LOG" 2>/dev/null; then
    RUBOCOP_FINAL_OFFENSES=$(grep -oE '[0-9]+ offense' "$RUBOCOP_LOG" | head -1 | grep -oE '^[0-9]+' || echo "?")
  fi

  notify "Phase 3 — Review ready" "Approve, fix with Claude, fix manually, or abort"

  # Commit gate — [y] commit / [f] Claude fixes / [n] you fix / [q] abort
  COMMIT_GATE_ATTEMPTS=0
  _rerun_checks_and_review() {
    if [[ "$STACK" == "generic" ]]; then
      log "Generic stack — no lint/test tooling to re-run"
      CHANGED_RUBY=""
      CHANGED_SPECS=""
    else
      log "Re-running checks..."
      CHANGED_RUBY=$(changed_ruby_files "$BASE_BRANCH")
      CHANGED_SPECS=$(changed_spec_files "$BASE_BRANCH")
      if [[ -n "$CHANGED_RUBY" ]]; then
        # One autocorrect sweep — no separate check-then-fix-then-recheck round trip.
        run_rubocop_check "$CHANGED_RUBY" "$RUBOCOP_LOG" true || true
      fi
      if [[ -n "$CHANGED_SPECS" ]]; then
        # shellcheck disable=SC2086
        bundle_exec rspec $(strip_app_prefix "$CHANGED_SPECS") 2>&1 | tee "$RSPEC_LOG" || warn "Some specs failed"
      elif [[ "$(cfg heal.full_suite_fallback true)" == "true" ]]; then
        bundle_exec rspec 2>&1 | tee "$RSPEC_LOG" || warn "Spec failures found"
      else
        echo "No spec files changed." > "$RSPEC_LOG"
      fi
      # Re-evaluate so a fixed toolchain clears the banner (and a newly broken
      # one sets it) instead of the first pass's verdict sticking forever.
      TOOLING_ERROR=""
      local rerun_msg
      if rerun_msg=$(tooling_failed "$RUBOCOP_LOG") || rerun_msg=$(tooling_failed "$RSPEC_LOG"); then
        TOOLING_ERROR="$rerun_msg"
        warn "$rerun_msg"
      fi
    fi
    COMMIT_GATE_ATTEMPTS=$((COMMIT_GATE_ATTEMPTS + 1))
    rm -f "$REVIEW_SENTINEL" "$REVIEW_JSON"
    spawn_langgraph "Re-reviewing" "review-r${COMMIT_GATE_ATTEMPTS}" "$REVIEW_SCRIPT" "${REVIEW_LANGGRAPH_ARGS[@]}"
    [[ -f "$REVIEW_SENTINEL" ]] || warn "migite-review may not have completed"
    sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"
    sync_json "$REVIEW_JSON" "${REVIEW_VAULT%.md}.json"
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
    if [[ "$(cfg gates.commit.require_green_specs true)" == "true" ]]; then
      [[ -n "${TOOLING_ERROR:-}" ]] && b+=("tooling error: $TOOLING_ERROR")
      local fl
      fl=$(grep -oE '[1-9][0-9]* failure[s]?' "$RSPEC_LOG" 2>/dev/null | head -1 || true)
      [[ -n "$fl" ]] && b+=("$fl in rspec")
    fi
    if [[ "$(cfg gates.commit.require_clean_lint true)" == "true" && -n "${RUBOCOP_FINAL_OFFENSES:-}" && "$RUBOCOP_FINAL_OFFENSES" != "0" ]]; then
      b+=("$RUBOCOP_FINAL_OFFENSES rubocop offense(s) remain")
    fi
    [[ ${#b[@]} -gt 0 ]] && printf '%s\n' "${b[@]}"
    return 0
  }
  _record_gate_override() {
    local blockers="$1" f="$SCRATCHPAD_DIR/gate-overrides.md"
    {
      echo "## $DATE $(date +%H:%M) — commit gate approved over blockers"
      printf '%s\n' "$blockers" | sed 's/^/- /'
      echo ""
    } >> "$f"
    sync_artifact "$f" "$TASK_DIR/gate-overrides.md"
  }

  while true; do
    show_commit_context
    read_gate_choice "COMMIT GATE" "Proceed? [y/f/e/n/q] (y=commit, f=Claude fixes, e=edit directly, n=fix it yourself, q=abort): "
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
        local FIX_IMPL_FILE="$SCRATCHPAD_DIR/fix-r${COMMIT_GATE_ATTEMPTS}.md"
        local FIX_PROMPT="${KNOWLEDGE_INJECT}You are fixing issues identified by an autonomous code reviewer.

## Original plan (for context)
$(cat "$PLAN_FILE" 2>/dev/null || echo "(plan not found)")

## Review findings — address every issue below
${REVIEW_CONTENT}

## Instructions
- Fix every Critical and Warning finding listed above
- Do not change anything not mentioned in the findings
- After fixing, write a short summary of what you changed to: ${FIX_IMPL_FILE}"

        log "Opening Claude to fix review findings..."
        run_phase "Fixing review findings" "$FIX_IMPL_FILE" "$FIX_PROMPT"
        sync_artifact "$FIX_IMPL_FILE" "$TASK_DIR/fix-r${COMMIT_GATE_ATTEMPTS}.md"

        # Fix rounds routinely change behaviour the testing plan asserts against
        # (log lines, method signatures, argument shapes) — leaving it stale just
        # means the next review re-diagnoses the same drift as a fresh finding
        # instead of it being fixed here, in the same round that caused it.
        if [[ -f "$TESTING_PLAN_FILE" ]]; then
          log "Updating testing plan for fix round ${COMMIT_GATE_ATTEMPTS}..."
          local TESTING_PLAN_FIX_PROMPT="You are updating the QA/dev testing plan after a review-fix round. The testing plan must describe how to verify the CURRENT, post-fix behaviour — not what it was before this round's fixes.

## Current testing plan (supersede anything this fix round changes)
$(cat "$TESTING_PLAN_FILE")

## Review findings that were just fixed
${REVIEW_CONTENT}

## Fix summary for this round
$(cat "$FIX_IMPL_FILE" 2>/dev/null || echo "(fix summary not found)")

## Current diff against $BASE_BRANCH
$(git diff "$BASE_BRANCH" 2>/dev/null || echo "(no diff available)")

## Instructions
Output the FULL updated testing plan — not just the delta. Keep steps that are still valid, rewrite or remove steps this fix round invalidates (wrong log text, wrong argument shape, wrong method/constant names, assertions that now contradict the fixed behaviour), and add steps for any new behaviour the fix introduced. Preserve the existing structure (Prerequisites / Verification steps / Teardown). Output ONLY the document, no preamble."

          local testing_plan_tmp
          testing_plan_tmp=$(mktemp)
          thinking "Updating testing plan for fix round ${COMMIT_GATE_ATTEMPTS}" "$testing_plan_tmp" "$TESTING_PLAN_FIX_PROMPT" "$(cfg_model_flags testing_plan)"
          if [[ -s "$testing_plan_tmp" ]]; then
            mv "$testing_plan_tmp" "$TESTING_PLAN_FILE"
            sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
            success "Testing plan updated for fix round ${COMMIT_GATE_ATTEMPTS}"
          else
            rm -f "$testing_plan_tmp"
            warn "Testing plan regeneration returned empty — testing-plan.md left unchanged"
          fi
        fi

        _rerun_checks_and_review
        ;;
      n|N)
        echo ""
        echo -e "${YELLOW}  Make your fixes, then press Enter to re-run checks and re-review.${RESET}"
        local manual_ready
        read -r -p "$(echo -e "${YELLOW}  Ready to re-run checks? [Enter/q] (Enter=continue, q=abort): ${RESET}")" manual_ready
        [[ "${manual_ready:-}" =~ ^[qQ]$ ]] && { warn "Workflow aborted"; exit 0; }
        _rerun_checks_and_review
        ;;
      q|Q)
        warn "Workflow aborted"
        exit 0
        ;;
      *)
        warn "Invalid input — use y / f / e / n / q"
        ;;
    esac
  done
}
