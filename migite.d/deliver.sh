#!/usr/bin/env bash
# migite.d/deliver.sh — Phase 3.5 (knowledge capture), Phase 4 (PR
# description), and Phase 4.5 (self-improvement notes).
#
# Sourced by migite. run_deliver expects TASK_DIR, ORG, REPO_NAME,
# DEV_LOG_BASE, PLAN_FILE, IMPLEMENTATION_FILE, REVIEW_FILE, SCRATCHPAD_DIR,
# TASK_SLUG, JIRA_TICKET, MIGITE_HOME, BRANCH, TASK_TYPE, PLAN_GATE_ATTEMPTS,
# COMMIT_GATE_ATTEMPTS, RUBOCOP_LOG, RSPEC_LOG to be set.

run_deliver() {
  echo ""
  log "Phase 3.5/4 — Capturing knowledge"

  KNOWLEDGE_FILE="$DEV_LOG_BASE/$ORG/$REPO_NAME/knowledge.md"
  local REVIEW_WIKILINK="dev-log/$ORG/$REPO_NAME/$TASK_SLUG/review"
  local KNOWLEDGE_WIKILINK="dev-log/$ORG/$REPO_NAME/knowledge"

  # Bootstrap knowledge file if it doesn't exist
  if [[ ! -f "$KNOWLEDGE_FILE" ]]; then
    cat > "$KNOWLEDGE_FILE" <<EOF
# Knowledge — $REPO_NAME

> One file per repo. Each entry is a lesson, finding, or decision worth remembering.
> Entries link back to the review where the context lives.

EOF
    success "Created knowledge file: $KNOWLEDGE_FILE"
  fi

  local KNOWLEDGE_PROMPT="You are extracting reusable knowledge from a completed implementation.

Plan:
$(cat "$PLAN_FILE")

Implementation notes:
$(cat "$IMPLEMENTATION_FILE")

Review:
$(cat "$REVIEW_FILE")

Extract 1–3 bullet points worth keeping for future work on this repo. Be selective — only include things that will genuinely matter later.

INCLUDE:
- Business logic clarifications the developer provided (rules, edge cases, domain constraints that aren't obvious from the code)
- Important conclusions or discoveries made during implementation (e.g. a behaviour that surprised us, a constraint we uncovered, a decision with non-obvious reasoning)
- Architectural or design decisions that future work needs to be aware of

EXCLUDE:
- Anything spec or testing related (test structure, factory choices, rspec patterns)
- Rails/Ruby conventions (rubocop, code style, standard patterns)
- Obvious things any developer would know
- Process notes (\"we ran rubocop\", \"specs passed\")

If nothing in the above material meets the INCLUDE criteria, output nothing.

Output ONLY the bullet points, no preamble. Each bullet starts with '- '.
End each bullet with a wikilink to the review: [[${REVIEW_WIKILINK}]]"

  local KNOWLEDGE_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-knowledge.txt"
  thinking "Extracting knowledge" "$KNOWLEDGE_LOG" "$KNOWLEDGE_PROMPT"

  # Append entry to knowledge file
  {
    echo ""
    echo "## $DATE — $TASK_SLUG"
    cat "$KNOWLEDGE_LOG"
  } >> "$KNOWLEDGE_FILE"

  # Append wikilink to review.md
  {
    echo ""
    echo "---"
    echo "> Knowledge: [[${KNOWLEDGE_WIKILINK}]]"
  } >> "$REVIEW_FILE"

  sync_artifact "$KNOWLEDGE_FILE" "knowledge.md"
  success "Knowledge appended to $KNOWLEDGE_FILE"
  notify "Phase 3.5 — Knowledge captured" "Review the entry in Obsidian"

  # ── Phase 4: PR description ───────────────────────────────────────────────────
  echo ""
  log "Phase 4/4 — Generating PR description"

  local PR_FILE="$TASK_DIR/pr-description.md"
  local TICKET_SUFFIX=""
  [[ -n "$JIRA_TICKET" ]] && TICKET_SUFFIX=" [$JIRA_TICKET]"

  # Amendments accumulate beside plan.md — fold every one into the PR so the description
  # reflects the final delivered scope, not just the original plan
  local ALL_AMENDMENTS=""
  for _amend_f in "$TASK_DIR"/amendment-*.md; do
    [[ -f "$_amend_f" ]] || continue
    ALL_AMENDMENTS="${ALL_AMENDMENTS}
$(cat "$_amend_f")
"
  done

  local PR_PROMPT="Plan:
$(cat "$PLAN_FILE")
$( [[ -n "$ALL_AMENDMENTS" ]] && printf '\nAmendments (delivered scope beyond the original plan):\n%s\n' "$ALL_AMENDMENTS" )

Review:
$(cat "$REVIEW_FILE")

Jira ticket reference: $TICKET_SUFFIX

$(cat "$HOME/.claude/commands/commit.md" | sed "s|\\[PR_FILE\\]|$PR_FILE|g")"

  run_phase "PR description" "$PR_FILE" "$PR_PROMPT"
  sync_artifact "$PR_FILE" "pr-description.md"
  notify "Phase 4 — PR description ready" "Check pr-description.md in Obsidian"

  # ── Phase 4.5: Self-improvement ───────────────────────────────────────────────
  echo ""
  log "Phase 4.5/4 — Capturing improvement notes"

  local IMPROVEMENTS_FILE="$MIGITE_HOME/migite-improvements.md"

  if [[ ! -f "$IMPROVEMENTS_FILE" ]]; then
    cat > "$IMPROVEMENTS_FILE" <<'EOF'
# Migite — Improvement Notes

> One file. Each run appends observations about what could be better.
> Review periodically and apply what makes sense.

EOF
    success "Created improvements file: $IMPROVEMENTS_FILE"
  fi

  local IMPROVEMENTS_PROMPT="You are reviewing a completed migite workflow run to identify specific, actionable improvements to the migite script itself.

## Run context
- Task slug: $TASK_SLUG
- Task type: ${TASK_TYPE:-unknown}
- Branch: $BRANCH
- Date: $DATE
- Plan gate attempts: $PLAN_GATE_ATTEMPTS
- Commit gate attempts: $COMMIT_GATE_ATTEMPTS

## Rubocop results
$(cat "$RUBOCOP_LOG")

## Rspec results
$(cat "$RSPEC_LOG")

## Implementation notes
$(cat "$IMPLEMENTATION_FILE")

## Review
$(cat "$REVIEW_FILE")

## Current migite script (entrypoint + sourced phase files under migite.d/)
$(cat "$MIGITE_HOME/migite")

$(for f in "$MIGITE_HOME"/migite.d/*.sh; do echo "### $(basename "$f")"; cat "$f"; echo; done)

Based on what actually happened in this run, identify 0–3 specific, actionable improvements to the migite script. Be very selective — only include things grounded in what happened this run.

INCLUDE:
- Automation gaps: steps done manually that migite could handle
- Edge cases not handled gracefully (errors, missing files, unexpected state)
- Patterns suggesting a structural gap (e.g. gate looped many times = task was too large, add a warning after N rejections)
- Concrete changes: name the specific variable, check, or phase to modify

EXCLUDE:
- Generic best-practice suggestions not tied to this run
- Suggestions already present in the script
- Testing or Ruby conventions (those belong in knowledge.md)
- Improvements requiring non-trivial architecture changes to migite

If nothing in this run warrants an improvement note, output nothing.

Output ONLY the bullet points, no preamble. Each bullet starts with '- '."

  local IMPROVEMENTS_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-improvements.txt"
  thinking "Self-improvement" "$IMPROVEMENTS_LOG" "$IMPROVEMENTS_PROMPT"

  if grep -q '[^[:space:]]' "$IMPROVEMENTS_LOG" 2>/dev/null; then
    {
      echo ""
      echo "## $DATE — $TASK_SLUG"
      cat "$IMPROVEMENTS_LOG"
    } >> "$IMPROVEMENTS_FILE"
    success "Improvement notes appended to $IMPROVEMENTS_FILE"
    notify "Phase 4.5 — Improvement notes captured" "Check migite-improvements.md"
  else
    log "No improvement notes for this run"
  fi
}
