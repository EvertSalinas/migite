#!/usr/bin/env bash
# migite.d/implement.sh — Phase 2 (implement, staged or single-session)
# and the Phase 2.5 auto-heal loop.
#
# Sourced by migite. run_implement expects AMEND_MODE, STAGED,
# KNOWLEDGE_INJECT, PLAN_FILE, AMENDMENT_FILE, AMEND_NUM, IMPLEMENTATION_FILE,
# IMPLEMENTATION_VAULT, SCRATCHPAD_DIR, TASK_DIR, TASK_SLUG, MAX_HEAL_ATTEMPTS to be set.
# Leaves HEAL_ATTEMPT set for the caller's summary logging.

run_implement() {
  echo ""
  log "Phase 2/4 — Implementing"

  _impl_base_prompt() {
    if [[ "$AMEND_MODE" == "true" ]]; then
      printf '%s' "${KNOWLEDGE_INJECT}$(cat "$HOME/.claude/commands/implement.md" | sed "s|\\[PLAN_PATH\\]|$AMENDMENT_FILE|g")

## Original plan (already built — for context only, do not re-implement)
$(cat "$PLAN_FILE")

## Amendment $AMEND_NUM — implement ONLY this scoped delta
$(cat "$AMENDMENT_FILE")

## Migite workflow context (overrides CLAUDE.md defaults for this session)
- The original plan is already implemented — only implement the amendment's Scope section
- Respect the amendment's Out of scope section — do not touch anything listed there
- Do NOT run rubocop or rspec — migite runs them after this phase"
    else
      printf '%s' "${KNOWLEDGE_INJECT}$(cat "$HOME/.claude/commands/implement.md" | sed "s|\\[PLAN_PATH\\]|$PLAN_FILE|g")

$(cat "$PLAN_FILE")

## Migite workflow context (overrides CLAUDE.md defaults for this session)
- Planning is already complete and gate-approved — begin implementation directly
- Do NOT run rubocop or rspec — migite runs them after this phase"
    fi
  }

  if [[ "$STAGED" == "true" ]]; then
    # ── Staged implementation: parse plan scope into layers, gate between each ──
    log "Staged mode — parsing plan scope into layers"

    # Extract layer groups from the plan's Scope section (lines starting with ###)
    local STAGE_LABELS=()
    while IFS= read -r line; do
      local label
      label=$(echo "$line" | sed 's/^### *//')
      [[ -n "$label" ]] && STAGE_LABELS+=("$label")
    done < <(awk '/^## Scope/,/^## [^S]/' "$PLAN_FILE" | grep '^### ')

    # Fall back to a single stage if the plan has no ### sub-sections in Scope
    if [[ ${#STAGE_LABELS[@]} -eq 0 ]]; then
      warn "No ### sub-sections found in Scope — running as single stage"
      STAGE_LABELS=("Full implementation")
    fi

    local STAGE_COUNT=${#STAGE_LABELS[@]}
    log "Found $STAGE_COUNT stage(s): ${STAGE_LABELS[*]}"

    local STAGE_NUM=0
    local STAGE_NOTES_COMBINED=""
    for STAGE_LABEL in "${STAGE_LABELS[@]}"; do
      STAGE_NUM=$((STAGE_NUM + 1))
      echo ""
      log "Stage $STAGE_NUM/$STAGE_COUNT — $STAGE_LABEL"

      local STAGE_OUTPUT_FILE="$SCRATCHPAD_DIR/implementation-stage-${STAGE_NUM}.md"
      local PRIOR_CONTEXT=""
      if [[ -n "$STAGE_NOTES_COMBINED" ]]; then
        PRIOR_CONTEXT="
## Work completed in prior stages
${STAGE_NOTES_COMBINED}"
      fi

      local STAGE_PROMPT="$(_impl_base_prompt)
${PRIOR_CONTEXT}

## Current stage: $STAGE_LABEL ($STAGE_NUM of $STAGE_COUNT)
Implement ONLY the files and logic belonging to the \"$STAGE_LABEL\" layer.
Do not implement layers that come after this one — they will be handled in subsequent stages.
When done, write notes on what you built to: $STAGE_OUTPUT_FILE"

      run_phase "Stage $STAGE_NUM — $STAGE_LABEL" "$STAGE_OUTPUT_FILE" "$STAGE_PROMPT"
      sync_artifact "$STAGE_OUTPUT_FILE" "$TASK_DIR/implementation-stage-${STAGE_NUM}.md"
      STAGE_NOTES_COMBINED="${STAGE_NOTES_COMBINED}
### Stage $STAGE_NUM: $STAGE_LABEL
$(cat "$STAGE_OUTPUT_FILE" 2>/dev/null || echo '(no notes)')"

      if [[ $STAGE_NUM -lt $STAGE_COUNT ]]; then
        echo ""
        git diff --stat 2>/dev/null | head -20 || true
        read_gate_choice "STAGE CHECKPOINT: $STAGE_NUM/$STAGE_COUNT — $STAGE_LABEL" "Proceed? [c/r/e/q] (c=continue, r=redo this stage, e=edit next stage brief, q=abort): "
        case "${GATE_CHOICE:-c}" in
          r|R)
            STAGE_NUM=$((STAGE_NUM - 1))
            STAGE_NOTES_COMBINED=$(echo "$STAGE_NOTES_COMBINED" | head -n -$(($(cat "$STAGE_OUTPUT_FILE" 2>/dev/null | wc -l) + 2)) 2>/dev/null || echo "")
            continue
            ;;
          e|E)
            local NEXT_LABEL="${STAGE_LABELS[$STAGE_NUM]:-}"
            echo -e "${CYAN}  Next stage: $NEXT_LABEL — add extra instructions (Enter twice when done):${RESET}"
            local EXTRA_INSTRUCTIONS=""
            while IFS= read -r line; do
              [[ -z "$line" && -z "$EXTRA_INSTRUCTIONS" ]] && continue
              [[ -z "$line" ]] && break
              EXTRA_INSTRUCTIONS="${EXTRA_INSTRUCTIONS}${line}
"
            done
            # Store so next iteration can prepend to its prompt (via a temp file)
            printf '%s' "$EXTRA_INSTRUCTIONS" > "$LOG_DIR/$TIMESTAMP-stage-${STAGE_NUM}-extra.txt"
            ;;
          q|Q) warn "Workflow aborted"; exit 0 ;;
          *) : ;;  # c or enter — continue
        esac
      fi
    done

    # Merge all stage notes into the canonical implementation file
    printf '%s' "$STAGE_NOTES_COMBINED" > "$IMPLEMENTATION_FILE"
    sync_artifact "$IMPLEMENTATION_FILE" "$IMPLEMENTATION_VAULT"
    success "All $STAGE_COUNT stage(s) complete — notes written to $IMPLEMENTATION_FILE"

  else
    # ── Standard single-session implementation ────────────────────────────────
    local IMPL_PROMPT="$(_impl_base_prompt)
- When done, write implementation notes to: $IMPLEMENTATION_FILE"

    run_phase "Implementing" "$IMPLEMENTATION_FILE" "$IMPL_PROMPT"
    sync_artifact "$IMPLEMENTATION_FILE" "$IMPLEMENTATION_VAULT"
    success "Implementation notes written to $IMPLEMENTATION_FILE"
  fi

  notify "Phase 2 — Implementation done" "Running rubocop + specs, then review"

  run_auto_heal_loop
}

# ── Auto-heal loop ────────────────────────────────────────────────────────────
# Rubocop is mechanical — autocorrect it locally, with zero Claude calls, every
# time checks run. Claude is only invoked for what autocorrect can't fix: real
# rubocop offenses and rspec failures. The fix prompt only includes whichever
# of those two is actually still failing. Bounded to MAX_HEAL_ATTEMPTS.
run_auto_heal_loop() {
  echo ""
  log "Phase 2.5 — Running checks (auto-heal enabled, max $MAX_HEAL_ATTEMPTS attempts)"

  local HEAL_RUBOCOP_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-heal-rubocop.txt"
  local HEAL_RSPEC_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-heal-rspec.txt"
  local HEAL_CHANGED_RUBY
  local HEAL_CHANGED_SPECS
  HEAL_CHANGED_SPECS=$(git diff "$BASE_BRANCH" --name-only --diff-filter=ACMR | grep '_spec\.rb$' || true)
  HEAL_ATTEMPT=0

  # Rubocop: one autocorrect sweep, no Claude involved. Re-autocorrects on every
  # recheck below too, so anything Claude's rspec fixes introduce gets swept for
  # free instead of round-tripping through another heal attempt.
  _heal_autofix_rubocop() {
    HEAL_CHANGED_RUBY=$(git diff "$BASE_BRANCH" --name-only --diff-filter=ACMR | grep '\.rb$' | grep -v '_spec\.rb$' || true)
    run_rubocop_check "$HEAL_CHANGED_RUBY" "$HEAL_RUBOCOP_LOG" true || true
    RUBOCOP_REMAINING=false
    grep -qE '[1-9][0-9]* offense' "$HEAL_RUBOCOP_LOG" 2>/dev/null && RUBOCOP_REMAINING=true
    return 0
  }

  local RUBOCOP_REMAINING=false
  _heal_autofix_rubocop
  run_rspec_check "$HEAL_CHANGED_SPECS" "$HEAL_RSPEC_LOG" || true

  while [[ $HEAL_ATTEMPT -lt $MAX_HEAL_ATTEMPTS ]]; do
    local RSPEC_STILL_FAILING=false
    if grep -qE '[1-9][0-9]* failure' "$HEAL_RSPEC_LOG" 2>/dev/null; then
      RSPEC_STILL_FAILING=true
    fi

    [[ "$RUBOCOP_REMAINING" == "false" && "$RSPEC_STILL_FAILING" == "false" ]] && break

    HEAL_ATTEMPT=$((HEAL_ATTEMPT + 1))
    warn "Auto-heal attempt $HEAL_ATTEMPT/$MAX_HEAL_ATTEMPTS..."

    local HEAL_FIX_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-heal-fix-${HEAL_ATTEMPT}.txt"
    local FAILURE_SECTIONS=""
    if [[ "$RUBOCOP_REMAINING" == "true" ]]; then
      FAILURE_SECTIONS="${FAILURE_SECTIONS}## Rubocop output (autocorrect already applied — these remain and need a manual fix)
$(cat "$HEAL_RUBOCOP_LOG")

"
    fi
    if [[ "$RSPEC_STILL_FAILING" == "true" ]]; then
      FAILURE_SECTIONS="${FAILURE_SECTIONS}## RSpec output
$(cat "$HEAL_RSPEC_LOG")

"
    fi

    local HEAL_FIX_PROMPT="The implementation has failures. Fix them without changing the specs unless they are fundamentally flawed.

${FAILURE_SECTIONS}## Plan
$(cat "$PLAN_FILE")

## Implementation notes
$(cat "$IMPLEMENTATION_FILE")

Fix all failures above. Do not run rubocop yourself — migite already runs \`rubocop -A\` after every attempt, so only genuinely unfixable-by-autocorrect offenses are shown here. When done, update: $IMPLEMENTATION_FILE"

    heal_run "Auto-heal $HEAL_ATTEMPT" "$HEAL_FIX_LOG" "$HEAL_FIX_PROMPT"

    log "Re-running checks after heal attempt $HEAL_ATTEMPT..."
    _heal_autofix_rubocop
    HEAL_CHANGED_SPECS=$(git diff "$BASE_BRANCH" --name-only --diff-filter=ACMR | grep '_spec\.rb$' || true)
    run_rspec_check "$HEAL_CHANGED_SPECS" "$HEAL_RSPEC_LOG" || true
  done

  if [[ $HEAL_ATTEMPT -eq 0 ]]; then
    success "All checks passed — no healing needed"
  elif [[ $HEAL_ATTEMPT -ge $MAX_HEAL_ATTEMPTS ]] && { grep -qE '[1-9][0-9]* failure' "$HEAL_RSPEC_LOG" 2>/dev/null || [[ "$RUBOCOP_REMAINING" == "true" ]]; }; then
    warn "Auto-heal exhausted ($MAX_HEAL_ATTEMPTS attempts) — review phase will see remaining failures"
  else
    success "Auto-heal resolved failures after $HEAL_ATTEMPT attempt(s)"
  fi
}
