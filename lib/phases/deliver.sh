#!/usr/bin/env bash
# lib/phases/deliver.sh — Phase 3.5 (knowledge capture), Phase 4 (PR
# description), and Phase 4.5 (self-improvement notes).
#
# Sourced by migite. run_deliver expects TASK_DIR, ORG, REPO_NAME,
# DEV_LOG_BASE, PLAN_FILE, IMPLEMENTATION_FILE, REVIEW_FILE, SCRATCHPAD_DIR, RUN_SLUG,
# TASK_SLUG, JIRA_TICKET, MIGITE_HOME, BRANCH, TASK_TYPE, PLAN_GATE_ATTEMPTS,
# COMMIT_GATE_ATTEMPTS, RUBOCOP_LOG, RSPEC_LOG to be set.
# write_run_summary expects the set_run_paths variables, PLAN_FILE, AMEND_MODE,
# AMENDMENT_FILE, BASE_BRANCH, DATE, and the gate/heal counters.

run_deliver() {
  echo ""
  log "Phase 3.5/4 — Capturing knowledge"

  KNOWLEDGE_FILE="$DEV_LOG_BASE/$ORG/$REPO_NAME/knowledge.md"
  local REVIEW_WIKILINK="dev-log/$ORG/$REPO_NAME/$TASK_SLUG/$RUN_SLUG/review"
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

  # The automated extraction below only sees plan/implementation/review — it misses
  # anything the developer noticed but didn't write down anywhere migite reads
  # (a domain quirk mentioned in Slack, a hunch about why something behaved the way
  # it did, a gotcha from testing manually). Ask before extracting so that input can
  # be folded in as authoritative rather than lost.
  local USER_KNOWLEDGE_FEEDBACK
  read -r -p "$(echo -e "${CYAN}  Anything worth remembering from this run that migite might not catch? (optional, Enter to skip): ${RESET}")" USER_KNOWLEDGE_FEEDBACK

  local USER_FEEDBACK_BLOCK=""
  if [[ -n "$USER_KNOWLEDGE_FEEDBACK" ]]; then
    USER_FEEDBACK_BLOCK="Engineer's own observation from this run (authoritative — always include this as its own bullet below, tightened for clarity but never dropped or reinterpreted away):
$USER_KNOWLEDGE_FEEDBACK

"
  fi

  local KNOWLEDGE_PROMPT="You are extracting reusable knowledge from a completed implementation.

${USER_FEEDBACK_BLOCK}Plan:
$(cat "$PLAN_FILE")

Implementation notes:
$(cat "$IMPLEMENTATION_FILE")

Review:
$(cat "$REVIEW_FILE")

Extract bullet points worth keeping for future work on this repo: the engineer's observation above (if any) plus up to 3 more drawn from the material below. Be selective on the drawn-from-material ones — only include things that will genuinely matter later.

INCLUDE:
- Business logic clarifications the developer provided (rules, edge cases, domain constraints that aren't obvious from the code)
- Important conclusions or discoveries made during implementation (e.g. a behaviour that surprised us, a constraint we uncovered, a decision with non-obvious reasoning)
- Architectural or design decisions that future work needs to be aware of

EXCLUDE:
- Anything spec or testing related (test structure, factory choices, rspec patterns)
- Rails/Ruby conventions (rubocop, code style, standard patterns)
- Obvious things any developer would know
- Process notes (\"we ran rubocop\", \"specs passed\")

If nothing in the material below meets the INCLUDE criteria, output nothing beyond the engineer's own observation (if one was given).

Output ONLY the bullet points, no preamble. Each bullet starts with '- '.
End each bullet with a wikilink to the review: [[${REVIEW_WIKILINK}]]"

  local KNOWLEDGE_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-knowledge.txt"
  # The knowledge role pins a tier, so this never runs on whatever the CLI's own
  # default model happens to be.
  agent_think "Extracting knowledge" knowledge "$KNOWLEDGE_LOG" "$KNOWLEDGE_PROMPT"

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
  sync_artifact "$REVIEW_FILE" "$REVIEW_VAULT"

  # knowledge.md is repo-wide (one file per repo, not per-task) and lives in the
  # vault only — no scratchpad copy to sync, just bump its frontmatter.
  stamp_file "$KNOWLEDGE_FILE"
  success "Knowledge appended to $KNOWLEDGE_FILE"
  notify "Phase 3.5 — Knowledge captured" "Review the entry in Obsidian"

  # ── Phase 4: PR description ───────────────────────────────────────────────────
  echo ""
  log "Phase 4/4 — Generating PR description"

  local PR_FILE="$SCRATCHPAD_DIR/pr-description.md"
  local TICKET_SUFFIX=""
  [[ -n "$JIRA_TICKET" ]] && TICKET_SUFFIX=" [$JIRA_TICKET]"

  # Amendments accumulate beside plan.md, one per NN-amend-<slug>/ run folder. Fold
  # every one into the PR so the description reflects the final delivered scope, not
  # just the original plan. Read from both scratchpad and vault (scratchpad wins) so
  # an older amendment that only survives in the vault mirror isn't silently dropped.
  local ALL_AMENDMENTS="" _amend_f
  while IFS= read -r _amend_f; do
    [[ -n "$_amend_f" ]] || continue
    ALL_AMENDMENTS="${ALL_AMENDMENTS}
$(cat "$_amend_f")
"
  done < <(task_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" amendment.md)

  local PR_PROMPT="Plan:
$(cat "$PLAN_FILE")
$( [[ -n "$ALL_AMENDMENTS" ]] && printf '\nAmendments (delivered scope beyond the original plan):\n%s\n' "$ALL_AMENDMENTS" )

Review:
$(cat "$REVIEW_FILE")

Jira ticket reference: $TICKET_SUFFIX

$(cat "$(template_path commit)" | sed "s|\\[PR_FILE\\]|$PR_FILE|g")"

  run_phase "PR description" "$PR_FILE" "$PR_PROMPT"
  sync_artifact "$PR_FILE" "$TASK_DIR/pr-description.md"
  notify "Phase 4 — PR description ready" "Check pr-description.md in Obsidian"

  # ── Phase 4.2: Run summary ────────────────────────────────────────────────────
  echo ""
  log "Phase 4.2/4 - Writing run summary"
  write_run_summary "$USER_KNOWLEDGE_FEEDBACK"

  # ── Phase 4.5: Self-improvement ───────────────────────────────────────────────
  echo ""
  log "Phase 4.5/4 — Capturing improvement notes"

  local IMPROVEMENTS_FILE="$MIGITE_HOME/docs/improvements.md"

  if [[ ! -f "$IMPROVEMENTS_FILE" ]]; then
    cat > "$IMPROVEMENTS_FILE" <<'EOF'
# Migite — Improvement Notes

> One file. Each run appends observations about what could be better.
> Review periodically and apply what makes sense.

EOF
    success "Created improvements file: $IMPROVEMENTS_FILE"
  fi

  # The full bash source (~110 KB) is only worth sending when the run was
  # eventful — a plan rejected, a commit-gate loop, or a heal attempt — because
  # that's when there's a concrete script behaviour to point at. A quiet run
  # gets a function index instead, which is enough to name the right helper.
  local RUN_EVENTFUL=false
  if [[ "${PLAN_GATE_ATTEMPTS:-1}" -gt 1 || "${COMMIT_GATE_ATTEMPTS:-0}" -gt 0 || "${HEAL_ATTEMPT:-0}" -gt 0 ]]; then
    RUN_EVENTFUL=true
  fi
  local SCRIPT_BLOCK
  if [[ "$RUN_EVENTFUL" == "true" ]]; then
    SCRIPT_BLOCK="## Current migite script (entrypoint + sourced files under lib/ and lib/phases/)
$(cat "$MIGITE_HOME/bin/migite")

$(for f in "$MIGITE_HOME"/lib/*.sh "$MIGITE_HOME"/lib/phases/*.sh; do echo "### ${f#"$MIGITE_HOME"/}"; cat "$f"; echo; done)"
  else
    SCRIPT_BLOCK="## migite script — function index (full source omitted: this run had no gate rejections or heal attempts)
$(for f in "$MIGITE_HOME"/bin/migite "$MIGITE_HOME"/lib/*.sh "$MIGITE_HOME"/lib/phases/*.sh; do echo "### ${f#"$MIGITE_HOME"/}"; grep -nE '^[a-zA-Z_][a-zA-Z0-9_]*\(\) *\{' "$f" | sed 's/() *{.*//'; echo; done)"
  fi

  local IMPROVEMENTS_PROMPT="You are reviewing a completed migite workflow run to identify specific, actionable improvements to the migite script itself.

## Run context
- Task slug: $TASK_SLUG
- Task type: ${TASK_TYPE:-unknown}
- Branch: $BRANCH
- Date: $DATE
- Plan gate attempts: $PLAN_GATE_ATTEMPTS
- Commit gate attempts: $COMMIT_GATE_ATTEMPTS
- Auto-heal attempts: ${HEAL_ATTEMPT:-0}

## Rubocop results
$(cat "$RUBOCOP_LOG")

## Rspec results
$(cat "$RSPEC_LOG")

## Implementation notes
$(cat "$IMPLEMENTATION_FILE")

## Review
$(cat "$REVIEW_FILE")

$SCRIPT_BLOCK

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
  agent_think "Self-improvement" improve "$IMPROVEMENTS_LOG" "$IMPROVEMENTS_PROMPT"

  if grep -q '[^[:space:]]' "$IMPROVEMENTS_LOG" 2>/dev/null; then
    {
      echo ""
      echo "## $DATE — $TASK_SLUG"
      cat "$IMPROVEMENTS_LOG"
    } >> "$IMPROVEMENTS_FILE"
    success "Improvement notes appended to $IMPROVEMENTS_FILE"
    notify "Phase 4.5 — Improvement notes captured" "Check docs/improvements.md"
  else
    log "No improvement notes for this run"
  fi
}

# write_run_summary [engineer-note] - Phase 4.2: summary.md in the run's folder,
# a record for people of what this run did and why: what changed, decisions made
# mid-run, deviations from the plan, fix rounds, outcome, follow-ups. The model
# (the `summary` role, fast tier) writes only that narrative, from this run's own
# files; the title, date and a "Run facts" section (verdict, fix rounds, heal
# attempts, cost) come from bash, so they can't be misreported. Nothing reads
# summary.md back into a later prompt. A failed or empty call leaves no file.
write_run_summary() {
  local engineer_note="${1:-}"
  local summary_file="$RUN_SCRATCH_DIR/summary.md"
  local purpose purpose_label
  if [[ "${AMEND_MODE:-false}" == "true" ]]; then
    purpose_label="Amendment this run implemented"
    purpose=$(cat "$AMENDMENT_FILE" 2>/dev/null || true)
  else
    purpose_label="Plan this run implemented (first 6000 characters)"
    purpose=$(cat "$PLAN_FILE" 2>/dev/null || true)
    purpose="${purpose:0:6000}"
  fi

  # Fix rounds in number order (a migrated 00-build/ can start at fix-r0).
  local fix_rounds="" fix_count=0 n f last
  last=$(( $(next_fix_round "$RUN_SCRATCH_DIR" "$RUN_VAULT_DIR") - 1 ))
  for (( n = 0; n <= last; n++ )); do
    f="$RUN_SCRATCH_DIR/fix-r$n.md"
    [[ -f "$f" ]] || f="$RUN_VAULT_DIR/fix-r$n.md"
    [[ -f "$f" ]] || continue
    fix_count=$((fix_count + 1))
    local body
    body=$(cat "$f")
    fix_rounds="${fix_rounds}
### fix-r$n.md
${body:0:3000}
"
  done

  local notes review overrides changed
  notes=$(cat "$IMPLEMENTATION_FILE" 2>/dev/null || true)
  review=$(cat "$REVIEW_FILE" 2>/dev/null || true)
  overrides=$(cat "$RUN_SCRATCH_DIR/gate-overrides.md" 2>/dev/null || true)
  changed=$(git diff --stat "$BASE_BRANCH" 2>/dev/null | tail -40 || true)

  local SUMMARY_PROMPT
  SUMMARY_PROMPT="You are writing the end-of-run summary for one migite run ($RUN_SLUG) of a software task. It is a record for the engineer and their team of what this run did and why, read later in their notes. Use ONLY the material below; never invent a change, a reason, or a decision. Be concise: short bullets, file paths in backticks.

## ${purpose_label}
${purpose:-(none)}

## Implementation notes from this run (first 6000 characters)
${notes:0:6000}

## Fix rounds in this run
${fix_rounds:-(none)}

## Final review (first 5000 characters)
${review:0:5000}
$( [[ -n "$overrides" ]] && printf '\n## Commit-gate overrides\n%s\n' "$overrides" )
$( [[ -n "$engineer_note" ]] && printf '\n## Note from the engineer at the end of the run\n%s\n' "$engineer_note" )
## Files changed on the branch (git diff --stat against $BASE_BRANCH)
${changed:-(no diff available)}

## Output
Output ONLY the lines below, starting with the Summary line, with no preamble:

Summary: <one sentence: what this run delivered>

## What changed
<bullets, by file or behaviour>

## Why
<the problem or feedback this run answered>

## Decisions made during the run
<choices the plan or amendment left open, and what was chosen; say who chose when the notes say so. \"None.\" if none>

## Deviations from the plan
<where the result differs from the plan or amendment, and why. \"None.\" if none>

## Fix rounds
<one bullet per round: what the review found, what was changed. \"None.\" if none>

## Follow-ups
<open warnings, unanswered questions, and deferred work the review or notes leave. \"None.\" if none>"

  local summary_body
  summary_body=$(mktemp)
  agent_think --quiet "Writing run summary" summary "$summary_body" "$SUMMARY_PROMPT"
  if ! grep -q '[^[:space:]]' "$summary_body" 2>/dev/null; then
    rm -f "$summary_body"
    warn "Run summary came back empty; no summary.md for $RUN_SLUG"
    return 0
  fi

  local verdict cost
  verdict=$(review_verdict "$REVIEW_FILE")
  cost=$(run_cost_so_far || echo "not recorded")
  {
    printf '# Run summary: %s\n' "$RUN_SLUG"
    printf 'Date: %s\n\n' "$DATE"
    cat "$summary_body"
    printf '\n\n## Run facts\n\n'
    printf -- '- Review verdict: %s\n' "$verdict"
    printf -- '- Fix rounds: %s\n' "$fix_count"
    printf -- '- Re-reviews at the commit gate: %s\n' "${COMMIT_GATE_ATTEMPTS:-0}"
    printf -- '- Auto-heal attempts: %s\n' "${HEAL_ATTEMPT:-0}"
    [[ "${AMEND_MODE:-false}" != "true" ]] && printf -- '- Plan gate rounds: %s\n' "${PLAN_GATE_ATTEMPTS:-1}"
    printf -- '- Model cost (headless calls, up to this summary): %s\n' "$cost"
  } > "$summary_file"
  rm -f "$summary_body"
  sync_artifact "$summary_file" "$RUN_VAULT_DIR/summary.md"
  success "Run summary written to $summary_file"
}
