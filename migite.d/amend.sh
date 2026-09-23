#!/usr/bin/env bash
# migite.d/amend.sh — amend mode: scope a delta against an already-planned,
# already-built task instead of starting a new plan.
#
# Sourced by migite. run_amend_mode expects BRANCH, ORG, REPO_NAME, DEV_LOG_BASE,
# REPO_ROOT, JIRA_TICKET, AMEND_FEEDBACK, AMEND_FEEDBACK_FILE, DATE to be set,
# and sets TASK_SLUG, TASK_DIR, SCRATCHPAD_DIR, INTAKE_FILE, PLAN_FILE, PLAN_VAULT,
# IMPLEMENTATION_FILE, IMPLEMENTATION_VAULT, REVIEW_FILE, REVIEW_VAULT,
# TESTING_PLAN_FILE, TESTING_PLAN_VAULT, AMEND_NUM, AMENDMENT_FILE,
# PLAN_GATE_ATTEMPTS for the phases that run after it. PLAN_FILE/IMPLEMENTATION_FILE/
# REVIEW_FILE/TESTING_PLAN_FILE live in the scratchpad; resume_from_vault pulls them
# back in from the vault mirror if the scratchpad copy is missing (e.g. amending an
# older task on a fresh clone or after the branch's scratchpad was cleaned).

run_amend_mode() {
  log "Amend mode — locating task to amend"

  local REPO_VAULT_DIR="$DEV_LOG_BASE/$ORG/$REPO_NAME"
  local AMEND_TARGET_SLUG=""

  if [[ -n "$JIRA_TICKET" ]]; then
    AMEND_TARGET_SLUG=$(slugify "$JIRA_TICKET")
    log "Amend target from --jira: $AMEND_TARGET_SLUG"
  else
    local BRANCH_TICKET
    BRANCH_TICKET=$(echo "$BRANCH" | grep -oE '[A-Za-z]+-[0-9]+' | head -1 | tr '[:lower:]' '[:upper:]' || true)
    if [[ -n "$BRANCH_TICKET" ]]; then
      AMEND_TARGET_SLUG=$(slugify "$BRANCH_TICKET")
      log "Amend target from branch name: $AMEND_TARGET_SLUG"
    fi
  fi

  if [[ -n "$AMEND_TARGET_SLUG" && ! -d "$REPO_VAULT_DIR/$AMEND_TARGET_SLUG" ]]; then
    warn "No vault directory found for '$AMEND_TARGET_SLUG' — falling back to picker"
    AMEND_TARGET_SLUG=""
  fi

  if [[ -z "$AMEND_TARGET_SLUG" ]]; then
    [[ -d "$REPO_VAULT_DIR" ]] || error "No task history found for $ORG/$REPO_NAME — nothing to amend"
    local AMEND_CANDIDATES=()
    while IFS= read -r d; do
      [[ -n "$d" ]] && AMEND_CANDIDATES+=("$d")
    done < <(recent_task_dirs "$REPO_VAULT_DIR" 10)
    [[ ${#AMEND_CANDIDATES[@]} -eq 0 ]] && error "No task directories found under $REPO_VAULT_DIR"

    echo ""
    echo -e "${BOLD}  Recent tasks for $ORG/$REPO_NAME:${RESET}"
    local AMEND_PICK_I=1
    for c in "${AMEND_CANDIDATES[@]}"; do
      echo "  $AMEND_PICK_I. $c"
      AMEND_PICK_I=$((AMEND_PICK_I + 1))
    done
    echo ""
    local amend_pick
    read -r -p "$(echo -e "${YELLOW}Select task to amend [1-${#AMEND_CANDIDATES[@]}]: ${RESET}")" amend_pick
    [[ "$amend_pick" =~ ^[0-9]+$ ]] && [[ "$amend_pick" -ge 1 ]] && [[ "$amend_pick" -le ${#AMEND_CANDIDATES[@]} ]] || error "Invalid selection"
    AMEND_TARGET_SLUG="${AMEND_CANDIDATES[$((amend_pick - 1))]}"
  fi

  TASK_SLUG="$AMEND_TARGET_SLUG"
  TASK_DIR="$REPO_VAULT_DIR/$TASK_SLUG"
  SCRATCHPAD_DIR="$REPO_ROOT/scratchpad/$TASK_SLUG"
  mkdir -p "$SCRATCHPAD_DIR"
  INTAKE_FILE="$SCRATCHPAD_DIR/intake.md"
  PLAN_FILE="$SCRATCHPAD_DIR/plan.md"
  PLAN_VAULT="$TASK_DIR/plan.md"
  IMPLEMENTATION_FILE="$SCRATCHPAD_DIR/implementation.md"
  IMPLEMENTATION_VAULT="$TASK_DIR/implementation.md"
  REVIEW_FILE="$SCRATCHPAD_DIR/review.md"
  REVIEW_VAULT="$TASK_DIR/review.md"
  TESTING_PLAN_FILE="$SCRATCHPAD_DIR/testing-plan.md"
  TESTING_PLAN_VAULT="$TASK_DIR/testing-plan.md"

  resume_from_vault "$INTAKE_FILE" "$TASK_DIR/intake.md"
  resume_from_vault "$PLAN_FILE" "$PLAN_VAULT"
  resume_from_vault "$IMPLEMENTATION_FILE" "$IMPLEMENTATION_VAULT"
  resume_from_vault "$REVIEW_FILE" "$REVIEW_VAULT"
  resume_from_vault "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"

  [[ -f "$PLAN_FILE" ]] || error "No plan.md found in $TASK_DIR — amend requires an already-planned task"

  log "Amending: $TASK_DIR"
  log "Scratchpad: $SCRATCHPAD_DIR"

  # Inject knowledge.md — same as the normal-mode Phase 1 injection
  KNOWLEDGE_FILE="$DEV_LOG_BASE/$ORG/$REPO_NAME/knowledge.md"
  KNOWLEDGE_INJECT=$(build_knowledge_injection "$KNOWLEDGE_FILE")

  # Gather feedback: inline arg, file, or $EDITOR
  local AMEND_FEEDBACK_TEXT
  if [[ -n "$AMEND_FEEDBACK_FILE" ]]; then
    AMEND_FEEDBACK_TEXT=$(cat "$AMEND_FEEDBACK_FILE")
  elif [[ -n "$AMEND_FEEDBACK" ]]; then
    AMEND_FEEDBACK_TEXT="$AMEND_FEEDBACK"
  else
    local AMEND_INPUT_FILE
    AMEND_INPUT_FILE=$(mktemp)
    printf '<!-- Describe the feedback/change for this amendment. Lines starting with <!-- are ignored. -->\n' > "$AMEND_INPUT_FILE"
    ${EDITOR:-vim} "$AMEND_INPUT_FILE"
    AMEND_FEEDBACK_TEXT=$(grep -v '^<!--' "$AMEND_INPUT_FILE" || true)
    rm -f "$AMEND_INPUT_FILE"
  fi
  [[ -n "$(echo "$AMEND_FEEDBACK_TEXT" | tr -d '[:space:]')" ]] || error "No amendment feedback provided"

  # Next amendment number — plan.md is never overwritten, amendments accumulate beside it
  # Checks both scratchpad and vault so a scratchpad that's missing older amendments
  # (cleaned, fresh clone) can't reuse a number already taken in vault history.
  local AMEND_LAST
  AMEND_LAST=$(find "$TASK_DIR" "$SCRATCHPAD_DIR" -maxdepth 1 -name 'amendment-*.md' 2>/dev/null | sed -E 's/.*amendment-([0-9]+)\.md/\1/' | sort -n | tail -1)
  AMEND_NUM=$(printf '%02d' "$(( ${AMEND_LAST:-0} + 1 ))")
  AMENDMENT_FILE="$SCRATCHPAD_DIR/amendment-${AMEND_NUM}.md"
  local AMENDMENT_VAULT="$TASK_DIR/amendment-${AMEND_NUM}.md"

  local AMEND_DIFF
  AMEND_DIFF=$(git diff "$BASE_BRANCH" 2>/dev/null || echo "(no diff available)")

  local AMEND_PROMPT="You are scoping a post-implementation amendment for an already-planned and already-built task. Read the original plan, what was actually implemented, the last review, the current diff, and repo knowledge, then write a SCOPED DELTA — not a new plan.

## Original plan
$(cat "$PLAN_FILE")

## Implementation notes
$(cat "$IMPLEMENTATION_FILE" 2>/dev/null || echo '(not yet implemented)')

## Last review
$(cat "$REVIEW_FILE" 2>/dev/null || echo '(no review yet)')

## Current diff against $BASE_BRANCH
$AMEND_DIFF

${KNOWLEDGE_INJECT}
## Engineer's feedback (source of truth for this amendment)
$AMEND_FEEDBACK_TEXT

## Instructions
- Ground everything in the diff above — reference what is already built by name; do not re-plan from scratch.
- If the feedback is already satisfied by the current code, say so explicitly and leave Scope empty.
- Output a document with exactly this structure:

# Amendment $AMEND_NUM
Date: $DATE

## Feedback
(restate the feedback in one or two sentences)

## Scope
(the specific files/changes needed to satisfy the feedback — empty if already satisfied)

## Out of scope
(anything the feedback might suggest but that should NOT be touched in this amendment)

## Rationale
(why this scope, grounded in the diff and the feedback)

Output ONLY this document — no preamble, no meta-commentary."

  agent_think "Generating amendment $AMEND_NUM" amend "$AMENDMENT_FILE" "$AMEND_PROMPT"
  [[ -s "$AMENDMENT_FILE" ]] || error "Amendment generation returned empty output"
  sync_artifact "$AMENDMENT_FILE" "$AMENDMENT_VAULT"
  success "Amendment written to $AMENDMENT_FILE"

  # Regenerates testing-plan.md in full (not appended) so it always reflects current,
  # post-amendment behaviour instead of drifting from what plan.md originally described.
  # Written to a tmp file first — a failed/empty call must never truncate the last-good copy.
  _regen_testing_plan() {
    log "Updating testing plan for amendment $AMEND_NUM..."
    local testing_plan_prompt
    testing_plan_prompt="You are updating the QA/dev testing plan for a task after a post-implementation amendment. The testing plan must describe how to verify the CURRENT, post-amendment behaviour — not the original plan.

## Current testing plan (supersede anything this amendment changes)
$(cat "$TESTING_PLAN_FILE" 2>/dev/null || echo '(none yet — write it fresh from the amendment and diff below)')

## Amendment just approved
$(cat "$AMENDMENT_FILE")

## Current diff against $BASE_BRANCH
$AMEND_DIFF

## Instructions
Output the FULL updated testing plan — not just the delta. Keep steps that are still valid, rewrite or remove steps this amendment invalidates, and add steps for anything new it introduces. Use exactly this structure, output ONLY this document, no preamble:

# Testing Plan

### Prerequisites — seed records (Rails console)
\`\`\`ruby
<runnable seed script, generic emails only — never real addresses>
\`\`\`

### Verification steps
<numbered curl/browser steps covering the current behaviour, plus at least one log line to grep>

### Teardown
\`\`\`ruby
<runnable teardown script matching the prerequisites above>
\`\`\`"

    local testing_plan_tmp
    testing_plan_tmp=$(mktemp)
    agent_think "Updating testing plan for amendment $AMEND_NUM" testing_plan "$testing_plan_tmp" "$testing_plan_prompt"
    if [[ -s "$testing_plan_tmp" ]]; then
      mv "$testing_plan_tmp" "$TESTING_PLAN_FILE"
      sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
      success "Testing plan updated for amendment $AMEND_NUM"
    else
      rm -f "$testing_plan_tmp"
      warn "Testing plan regeneration returned empty — testing-plan.md left unchanged"
    fi
  }

  # Gate: y=approve, f=feedback refine (one more Sonnet call), e=direct edit, q=abort
  while true; do
    read_gate_choice "REVIEW GATE: amendment $AMEND_NUM" "Proceed with amendment? [y/f/e/q] (y=approve, f=feedback refine, e=edit directly, q=abort): "
    case "$GATE_CHOICE" in
      y|Y)
        _regen_testing_plan
        success "Amendment approved — continuing to implementation"
        break
        ;;
      f|F)
        echo ""
        local _amend_gate_feedback
        read -r -p "$(echo -e "${CYAN}  Feedback (what to change): ${RESET}")" _amend_gate_feedback
        if [[ -z "$_amend_gate_feedback" ]]; then
          warn "No feedback entered — try again"
          continue
        fi
        local _amend_refine_tmp
        _amend_refine_tmp=$(mktemp)
        printf 'Here is the current amendment document:\n\n%s\n\nThe engineer has this feedback:\n%s\n\nRevise the amendment to address the feedback. Keep the same structure and format. Output only the revised amendment document — no preamble.' \
          "$(cat "$AMENDMENT_FILE")" "$_amend_gate_feedback" \
          | agent_ask "amend-refine" amend \
          > "$_amend_refine_tmp" 2>/dev/null || true
        if [[ -s "$_amend_refine_tmp" ]]; then
          mv "$_amend_refine_tmp" "$AMENDMENT_FILE"
          sync_artifact "$AMENDMENT_FILE" "$AMENDMENT_VAULT"
          echo ""
          cat "$AMENDMENT_FILE"
          success "Amendment refined — review again"
        else
          rm -f "$_amend_refine_tmp"
          warn "Refinement returned empty — amendment unchanged"
        fi
        ;;
      e|E)
        ${EDITOR:-vim} "$AMENDMENT_FILE"
        sync_artifact "$AMENDMENT_FILE" "$AMENDMENT_VAULT"
        success "Amendment saved — review again"
        ;;
      q|Q)
        warn "Workflow aborted"
        exit 0
        ;;
      *)
        warn "Invalid input — use y / f / e / q"
        ;;
    esac
  done

  PLAN_GATE_ATTEMPTS=0
}
