#!/usr/bin/env bash
# lib/phases/deliver.sh — Phase 3.5 (knowledge capture), Phase 4 (PR
# description), and Phase 4.5 (self-improvement notes).
#
# Sourced by migite. run_deliver expects TASK_DIR, ORG, REPO_NAME,
# DEV_LOG_BASE, PLAN_FILE, IMPLEMENTATION_FILE, REVIEW_FILE, SCRATCHPAD_DIR, RUN_SLUG,
# TASK_SLUG, JIRA_TICKET, MIGITE_HOME, BRANCH, TASK_TYPE, PLAN_GATE_ATTEMPTS,
# COMMIT_GATE_ATTEMPTS, RUBOCOP_LOG, RSPEC_LOG to be set.
# write_run_summary and fold_run_into_plan expect the set_run_paths variables,
# PLAN_FILE, AMEND_MODE, AMENDMENT_FILE, BASE_BRANCH, DATE, REPO_ROOT, and the
# gate/heal counters; fold_run_into_plan sets PLAN_FOLD_RESULT for the summary.

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

  # ── Phase 3.8: Plan update ────────────────────────────────────────────────────
  echo ""
  log "Phase 3.8/4 - Updating the plan"
  fold_run_into_plan
  # This run's last word on how to verify it; testing-plan.md itself is rewritten
  # by the next amend or fix round.
  snapshot_testing_plan

  # ── Phase 4: PR description ───────────────────────────────────────────────────
  echo ""
  log "Phase 4/4 — Generating PR description"

  local PR_FILE="$SCRATCHPAD_DIR/pr-description.md"
  local TICKET_SUFFIX=""
  [[ -n "$JIRA_TICKET" ]] && TICKET_SUFFIX=" [$JIRA_TICKET]"

  # plan.md reflects every run Phase 3.8 folded in, this one included. Only the
  # amendments it doesn't reflect (a skipped or failed fold, or a task from
  # before the living plan) go into the PR prompt beside it, so the description
  # covers the delivered scope without re-sending every amendment. Read from both
  # scratchpad and vault (scratchpad wins) so an amendment that only survives in
  # the vault mirror isn't silently dropped.
  local ALL_AMENDMENTS="" _amend_f
  while IFS= read -r _amend_f; do
    [[ -n "$_amend_f" ]] || continue
    ALL_AMENDMENTS="${ALL_AMENDMENTS}
$(cat "$_amend_f")
"
  done < <(unfolded_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" amendment.md "$PLAN_FILE")

  local PR_PROMPT="Plan:
$(cat "$PLAN_FILE")
$( [[ -n "$ALL_AMENDMENTS" ]] && printf '\nAmendments not yet reflected in the plan (delivered scope beyond it):\n%s\n' "$ALL_AMENDMENTS" )

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

  # A function index, never the full source: the source (~180 KB) went in on every
  # "eventful" run, which is most runs, for 0-3 bullets back. The index is enough
  # to name the right helper; the logs are capped the same way.
  local SCRIPT_BLOCK
  SCRIPT_BLOCK="## migite script: function index (file, line number and function name)
$(for f in "$MIGITE_HOME"/bin/migite "$MIGITE_HOME"/lib/*.sh "$MIGITE_HOME"/lib/phases/*.sh; do echo "### ${f#"$MIGITE_HOME"/}"; grep -nE '^[a-zA-Z_][a-zA-Z0-9_]*\(\) *\{' "$f" | sed 's/() *{.*//'; echo; done)"

  local IMPROVEMENTS_PROMPT="You are reviewing a completed migite workflow run to identify specific, actionable improvements to the migite script itself.

## Run context
- Task slug: $TASK_SLUG
- Task type: ${TASK_TYPE:-unknown}
- Branch: $BRANCH
- Date: $DATE
- Plan gate attempts: $PLAN_GATE_ATTEMPTS
- Commit gate attempts: $COMMIT_GATE_ATTEMPTS
- Auto-heal attempts: ${HEAL_ATTEMPT:-0}

## Rubocop results (first and last parts when long)
$(truncate_log "$RUBOCOP_LOG" 8000 2>/dev/null || true)

## Rspec results (first and last parts when long)
$(truncate_log "$RSPEC_LOG" 8000 2>/dev/null || true)

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
    # The approved plan, not plan.md: Phase 3.8 has already folded this run's
    # deviations into plan.md, which would hide them from this summary.
    purpose_label="Plan this run implemented (first 6000 characters)"
    local approved="$RUN_SCRATCH_DIR/plan.md"
    [[ -f "$approved" ]] || approved="$PLAN_FILE"
    purpose=$(cat "$approved" 2>/dev/null || true)
    purpose="${purpose:0:6000}"
  fi

  local fix_rounds="" fix_count=0 f body
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    fix_count=$((fix_count + 1))
    body=$(cat "$f")
    fix_rounds="${fix_rounds}
### $(basename "$f")
${body:0:3000}
"
  done < <(run_fix_files "$RUN_SCRATCH_DIR" "$RUN_VAULT_DIR")

  local notes review overrides changed
  notes=$(cat "$IMPLEMENTATION_FILE" 2>/dev/null || true)
  review=$(cat "$REVIEW_FILE" 2>/dev/null || true)
  overrides=$(cat "$RUN_SCRATCH_DIR/gate-overrides.md" 2>/dev/null || true)
  changed=$(git diff --stat "$BASE_BRANCH" 2>/dev/null | tail -40 || true)
  # Optional sections, built here and not as `$( [[ ... ]] && printf ... )` inside
  # the assignment below: a plain assignment exits with its last command
  # substitution's status, so an empty note would end the run under `set -e`.
  local overrides_block="" note_block=""
  if [[ -n "$overrides" ]]; then
    overrides_block=$(printf '\n## Commit-gate overrides\n%s\n' "$overrides")
  fi
  if [[ -n "$engineer_note" ]]; then
    note_block=$(printf '\n## Note from the engineer at the end of the run\n%s\n' "$engineer_note")
  fi

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
${overrides_block}
${note_block}
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
    printf -- '- Plan update: %s\n' "${PLAN_FOLD_RESULT:-not run}"
    printf -- '- Model cost (headless calls, up to this summary): %s\n' "$cost"
  } > "$summary_file"
  rm -f "$summary_body"
  sync_artifact "$summary_file" "$RUN_VAULT_DIR/summary.md"
  success "Run summary written to $summary_file"
}

# fold_run_into_plan - Phase 3.8: keep plan.md describing the design as it now
# stands. migite.plan_fold asks the agent (the `plan_fold` role) for exact edits
# from this run's amendment, implementation notes, fix rounds and review, applies
# the ones whose text appears exactly once, and appends a revision line. You see
# the diff and choose: apply, apply and edit, or keep the plan as it is. A fold
# that changes nothing but the revision line is applied without asking. A skipped
# or failed fold leaves plan.md alone, and later prompts keep carrying this run's
# amendment beside it. The approved original stays in 00-build/plan.md.
fold_run_into_plan() {
  PLAN_FOLD_RESULT="not run"
  if plan_folded_runs "$PLAN_FILE" | grep -qxF "$RUN_SLUG"; then
    PLAN_FOLD_RESULT="already reflected"
    log "plan.md already reflects $RUN_SLUG"
    return 0
  fi
  local new_plan report f
  new_plan=$(mktemp)
  report=$(mktemp)
  local -a fold_args=(--plan "$PLAN_FILE" --run-slug "$RUN_SLUG" --date "$DATE"
                      --out "$new_plan" --report "$report"
                      --implementation "$IMPLEMENTATION_FILE" --review "$REVIEW_FILE")
  [[ "${AMEND_MODE:-false}" == "true" ]] && fold_args+=(--amendment "$AMENDMENT_FILE")
  while IFS= read -r f; do
    [[ -n "$f" ]] && fold_args+=(--fix "$f")
  done < <(run_fix_files "$RUN_SCRATCH_DIR" "$RUN_VAULT_DIR")

  log "Asking $(agent_field display_name) for the plan edits this run implies (one headless call)..."
  if ! "$MIGITE_PYTHON" -m migite.plan_fold fold --repo-root "$REPO_ROOT" "${fold_args[@]}"; then
    rm -f "$new_plan" "$report"
    PLAN_FOLD_RESULT="failed; plan.md unchanged"
    warn "Plan update failed; plan.md is unchanged, and later prompts keep this run's amendment beside it"
    return 0
  fi

  if [[ "$(json_field "$report" changed_body)" != "true" ]]; then
    mv "$new_plan" "$PLAN_FILE"
    rm -f "$report"
    sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
    PLAN_FOLD_RESULT="no changes needed"
    success "Plan needed no changes; recorded $RUN_SLUG in its revision history"
    return 0
  fi

  echo ""
  echo -e "${BOLD}── Proposed plan changes ───────────────────────────${RESET}"
  diff --unified=2 "$PLAN_FILE" "$new_plan" | tail -n +3 \
    | awk '/^\+/ { print "\033[0;32m" $0 "\033[0m"; next }
           /^-/  { print "\033[0;31m" $0 "\033[0m"; next }
           { print }' || true
  echo -e "${BOLD}────────────────────────────────────────────────────${RESET}"

  while true; do
    read_gate_choice "PLAN UPDATE: $RUN_SLUG" "Apply these changes to plan.md? [y/e/n] (y=apply, e=apply then edit, n=keep the plan as it is): "
    case "$GATE_CHOICE" in
      y|Y|e|E)
        mkdir -p "$SCRATCHPAD_DIR/.plan-history"
        cp "$PLAN_FILE" "$SCRATCHPAD_DIR/.plan-history/plan-$(date +%Y%m%d-%H%M%S).md"
        mv "$new_plan" "$PLAN_FILE"
        [[ "$GATE_CHOICE" =~ ^[eE]$ ]] && ${EDITOR:-vim} "$PLAN_FILE"
        sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
        PLAN_FOLD_RESULT="updated"
        success "plan.md updated for $RUN_SLUG (previous version in .plan-history/)"
        break
        ;;
      n|N)
        rm -f "$new_plan"
        PLAN_FOLD_RESULT="skipped; plan.md unchanged"
        warn "plan.md left as it was; later prompts keep this run's amendment beside it"
        break
        ;;
      *)
        warn "Invalid input - use y / e / n"
        ;;
    esac
  done
  rm -f "$report"
}
