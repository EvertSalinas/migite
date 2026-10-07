#!/usr/bin/env bash
# lib/phases/plan.sh — Phase 1 (plan) and Phase 1.5 (TDD red phase).
#
# Sourced by migite. run_plan expects TASK, JIRA_TICKET, JIRA_URL, TASK_TYPE,
# AUDIT_FILE, BLUEPRINT_FILE, INTAKE_FILE_ARG, ATTACH_FILES, ORG, REPO_NAME,
# REPO_ROOT, BRANCH, BASE_BRANCH, DEV_LOG_BASE, MIGITE_HOME to be set (BRANCH is
# updated when the intake names another branch to check out), and sets TASK_SLUG,
# TASK_DIR, SCRATCHPAD_DIR, the set_run_paths variables for the 00-build run
# (RUN_SLUG, RUN_SCRATCH_DIR, RUN_VAULT_DIR, PLAN_FILE, IMPLEMENTATION_FILE,
# REVIEW_FILE, TESTING_PLAN_FILE and their _VAULT siblings), INTAKE_FILE,
# TASK_FILE, CRITIC_FILE, CRITIC_VAULT, KNOWLEDGE_FILE, KNOWLEDGE_INJECT,
# PLAN_GATE_ATTEMPTS for the phases that run after it. The scratchpad copies are
# the source of truth; the _VAULT siblings are synced mirrors under $TASK_DIR for
# reading/browsing (e.g. in Obsidian) - never written to directly.
# A fresh run writes the run's first run.json here (manifest_init); a resumed one
# (MANIFEST_RESUMED, lib/manifest.sh) keeps its task folder, and when it stopped
# at the plan gate (MANIFEST_ENTRY_STATUS=pending_gate) goes straight back to it.
# run_tdd expects PLAN_FILE, RUN_SCRATCH_DIR, RUN_VAULT_DIR, SCRATCHPAD_DIR, TASK_SLUG,
# and sets TDD_DECIDED (true when specs were written first).

# defer_testing_plan - plan.testing_plan_when: review. migite-plan leaves testing-plan.md
# to Phase 3 (ensure_testing_plan, lib/phases/review.sh), which only writes a missing one.
# A testing plan left over from an earlier plan describes a plan that no longer exists, so
# it is set aside in .plan-history and both copies are removed: the vault copy too, or
# resume_from_vault would bring it back. The run folders' snapshots keep the history.
# Called after every migite-plan run; a no-op for plan.testing_plan_when: plan.
defer_testing_plan() {
  [[ "$(cfg plan.testing_plan_when plan)" == "review" ]] || return 0
  if [[ -s "$TESTING_PLAN_FILE" ]]; then
    local hist_dir="$SCRATCHPAD_DIR/.plan-history"
    mkdir -p "$hist_dir"
    cp "$TESTING_PLAN_FILE" "$hist_dir/testing-plan-$(date +%Y%m%d-%H%M%S).md"
    warn "Setting aside the testing plan from the earlier plan; Phase 3 writes a new one from the new plan and the diff"
  fi
  rm -f "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
  return 0
}

run_plan() {
  echo ""
  log "Phase 1/4 — Planning"

  # Derive a preliminary slug from args so dirs can be created before intake is opened
  local PRELIMINARY_SLUG
  local RESUME_PLAN_GATE=false
  [[ "${MANIFEST_ENTRY_STATUS:-}" == "pending_gate" ]] && RESUME_PLAN_GATE=true
  if [[ "${MANIFEST_RESUMED:-false}" == "true" && -n "${TASK_SLUG:-}" ]]; then
    # The folder run.json was found in, which the intake's Title may have renamed
    PRELIMINARY_SLUG="$TASK_SLUG"
  elif [[ -n "$JIRA_TICKET" ]]; then
    PRELIMINARY_SLUG=$(slugify "$JIRA_TICKET")
  elif [[ -n "$TASK" ]]; then
    PRELIMINARY_SLUG=$(slugify "$TASK")
  else
    PRELIMINARY_SLUG="task-$TIMESTAMP"
  fi

  TASK_DIR="$DEV_LOG_BASE/$ORG/$REPO_NAME/$PRELIMINARY_SLUG"
  SCRATCHPAD_DIR="$REPO_ROOT/scratchpad/$PRELIMINARY_SLUG"
  mkdir -p "$TASK_DIR" "$SCRATCHPAD_DIR"
  # A task resumed from before the run-folder layout moves into it first, so
  # the resume checks below find its files in 00-build/.
  migrate_task_if_flat "$SCRATCHPAD_DIR" "$TASK_DIR"
  set_run_paths "$BUILD_RUN_SLUG"
  [[ "${MANIFEST_RESUMED:-false}" == "true" ]] || manifest_init

  # Falls back to when Title: can't be derived from the intake below (line ~120).
  # Stays PRELIMINARY_SLUG unless --intake overrides it with the file's own name.
  local INTAKE_SLUG_FALLBACK="$PRELIMINARY_SLUG"

  # ── Jira context (only when --jira was used) ────────────────────────────────
  # The key names the task; the ticket's content is fetched by migite-ticket from
  # whichever source the config allows (tracker.provider: Atlassian's acli when it
  # is logged in, else the agent's Atlassian MCP tools inside the jira.read scope). Cached in the
  # scratchpad so redos and resumes within the same task don't re-fetch. When no
  # source is available or the fetch fails, planning proceeds without the ticket
  # body, same as a missing knowledge.md, audit, or blueprint.
  local JIRA_CONTEXT_FILE=""
  if [[ -n "$JIRA_TICKET" ]]; then
    JIRA_CONTEXT_FILE="$RUN_SCRATCH_DIR/jira-context.md"
    local JIRA_CONTEXT_VAULT="$RUN_VAULT_DIR/jira-context.md"
    resume_from_vault "$JIRA_CONTEXT_FILE" "$JIRA_CONTEXT_VAULT"
    if [[ -s "$JIRA_CONTEXT_FILE" ]]; then
      log "Reusing cached Jira context for $JIRA_TICKET"
    else
      log "Fetching Jira ticket $JIRA_TICKET..."
      local _ticket_rc=0
      ticket_cmd fetch "${JIRA_URL:-$JIRA_TICKET}" --out "$JIRA_CONTEXT_FILE" || _ticket_rc=$?
      case "$_ticket_rc" in
        0) sync_artifact "$JIRA_CONTEXT_FILE" "$JIRA_CONTEXT_VAULT"
           success "Jira ticket fetched, added to planning context" ;;
        2) warn "No ticket source available (see above); planning without ticket context"
           JIRA_CONTEXT_FILE="" ;;
        *) warn "Jira fetch failed (see above); planning without ticket context"
           JIRA_CONTEXT_FILE="" ;;
      esac
    fi
  fi

  _pick_task_type() {
    if automata; then
      # migite-plan's own fallback when no type is known
      TASK_TYPE="feature"
      log "No --type given: planning as a feature (--automata). Pass --type to choose"
      return 0
    fi
    echo ""
    echo -e "${BOLD}  Task type:${RESET}"
    echo -e "  1. feature"
    echo -e "  2. bug"
    echo -e "  3. refactor"
    echo -e "  4. spike"
    echo -e "  5. config"
    echo ""
    local type_choice
    read -r -p "$(echo -e "${YELLOW}Select type [1-5]: ${RESET}")" type_choice
    case "$type_choice" in
      1) TASK_TYPE="feature" ;;
      2) TASK_TYPE="bug" ;;
      3) TASK_TYPE="refactor" ;;
      4) TASK_TYPE="spike" ;;
      5) TASK_TYPE="config" ;;
      *) error "Invalid choice" ;;
    esac
  }

  # Intake lives in scratchpad — copy from vault if resuming, else copy fresh from template
  INTAKE_FILE="$RUN_SCRATCH_DIR/intake.md"

  if [[ -f "$INTAKE_FILE" ]]; then
    log "Resuming existing intake: $INTAKE_FILE"
  elif [[ -f "$RUN_VAULT_DIR/intake.md" ]]; then
    cp "$RUN_VAULT_DIR/intake.md" "$INTAKE_FILE"
    log "Loaded intake from vault"
  elif [[ -n "$INTAKE_FILE_ARG" ]]; then
    # ── Intake mode: use a pre-written intake file directly, no template/editor ──
    cp "$INTAKE_FILE_ARG" "$INTAKE_FILE"
    INTAKE_SLUG_FALLBACK=$(slugify "$(basename "$INTAKE_FILE_ARG" .md)")

    if [[ -z "$(grep -m1 -i 'title:' "$INTAKE_FILE" || true)" ]]; then
      warn "Intake file has no Title: line — using the filename for the slug"
    fi

    if [[ -z "$TASK_TYPE" ]]; then
      local intake_type
      intake_type=$(grep -m1 -i '^type:' "$INTAKE_FILE" | sed 's/.*[Tt]ype:[[:space:]]*//' | xargs || true)
      case "$intake_type" in
        feature|bug|refactor|spike|config) TASK_TYPE="$intake_type" ;;
        "") ;;
        *) warn "Intake file has invalid Type: '$intake_type' — falling back to picker" ;;
      esac
      [[ -z "$TASK_TYPE" ]] && _pick_task_type
    fi

    echo ""
    echo -e "${CYAN}  Intake loaded from $INTAKE_FILE_ARG — first 30 lines:${RESET}"
    echo ""
    head -30 "$INTAKE_FILE" | while IFS= read -r line; do
      echo "    $line"
    done
    echo ""
    local intake_choice
    read_answer intake_choice "$(echo -e "${YELLOW}Proceed with this intake? [y/e/q] (y=proceed, e=edit, q=abort): ${RESET}")" y
    case "${intake_choice:-y}" in
      e|E) ${EDITOR:-vim} "$INTAKE_FILE" ;;
      q|Q) error "Workflow aborted" ;;
      *) log "Intake confirmed" ;;
    esac
  elif [[ -n "$AUDIT_FILE" ]]; then
    # ── Audit mode: generate intake directly from audit, no editor required ──────
    TASK_TYPE="${TASK_TYPE:-refactor}"
    local AUDIT_HEADING
    AUDIT_HEADING=$(grep -m1 '^# ' "$AUDIT_FILE" | sed 's/^# //' || echo "Codebase Audit")
    {
      printf 'Title: Audit Remediation — %s\n' "$AUDIT_HEADING"
      printf 'Type: %s\n' "$TASK_TYPE"
      printf 'Branch: %s\n' "$BRANCH"
      printf 'Date: %s\n\n' "$(date +%Y-%m-%d)"
      printf '## Objective\n'
      printf 'Address the issues identified in the codebase audit below.\n'
      printf 'Prioritise critical findings first, then high-priority warnings.\n'
      printf 'Do not introduce new features — this is a remediation pass only.\n\n'
      printf '## Scope\n'
      printf '- Fix every critical finding\n'
      printf '- Address high-priority warnings where changes are safe and localised\n'
      printf '- Leave low-severity notes for a future pass\n'
      printf '- Do not change public API contracts or alter behaviour observable to callers\n\n'
      printf '## Acceptance Criteria\n'
      printf '- All critical findings resolved with no regressions\n'
      if stack_is_profile; then
        # The profile's own commands (stacks.<name>), without the {files} placeholder.
        local audit_lint audit_test
        audit_lint="$(cfg "stacks.$STACK.lint")"; audit_test="$(cfg "stacks.$STACK.test")"
        [[ -n "$audit_lint" ]] && printf -- '- `%s` reports no new problems\n' "${audit_lint// \{files\}/}"
        [[ -n "$audit_test" ]] && printf -- '- `%s` remains green\n' "${audit_test// \{files\}/}"
        printf '\n'
      elif [[ "$STACK" == "generic" ]]; then
        printf -- '- The existing tests remain green\n\n'
      else
        printf '- `bundle exec rubocop` reports no new offenses\n'
        printf '- `bundle exec rspec` remains green\n\n'
      fi
      printf '## Audit Findings (source of truth for this task)\n\n'
      cat "$AUDIT_FILE"
    } > "$INTAKE_FILE"
    log "Intake auto-generated from audit ($AUDIT_HEADING)"

    echo ""
    echo -e "${CYAN}  Audit intake ready — review before planning:${RESET}"
    echo ""
    grep -E '^(Title|Type|Branch|Date|##)' "$INTAKE_FILE" | head -12 | while IFS= read -r line; do
      echo "    $line"
    done
    echo ""
    local audit_choice
    read_answer audit_choice "$(echo -e "${YELLOW}Proceed with this intake? [y/e/q] (y=proceed, e=edit, q=abort): ${RESET}")" y
    case "${audit_choice:-y}" in
      e|E) ${EDITOR:-vim} "$INTAKE_FILE" ;;
      q|Q) error "Workflow aborted" ;;
      *) log "Intake confirmed" ;;
    esac
  else
    [[ -z "$TASK_TYPE" ]] && _pick_task_type
    local TASK_TEMPLATE
    TASK_TEMPLATE="$(template_path "$TASK_TYPE")"   # templates.dir override, else the repo copy
    [[ -f "$TASK_TEMPLATE" ]] || error "Template not found: $TASK_TEMPLATE"
    cp "$TASK_TEMPLATE" "$INTAKE_FILE"
    # Literal substitution — never sed with user text in the expression (see fill_intake_field)
    [[ -n "$TASK" ]] && fill_intake_field "$INTAKE_FILE" '<!-- one sentence.*-->' "$TASK"
    [[ -n "$JIRA_TICKET" ]] && fill_intake_field "$INTAKE_FILE" '<!-- ticket ID or N/A -->' "${JIRA_URL:-$JIRA_TICKET}"
    log "Created intake from template: $TASK_TYPE"

    if automata; then
      # Nobody to fill the template in: plan from the task text, as a --jira run
      # does from the ticket. A ticket key whose ticket couldn't be fetched is no
      # intake at all.
      [[ -n "$TASK" ]] || error "--automata has no intake to plan from: pass a task description, --jira <key> or --intake <file>"
      if [[ -n "$JIRA_TICKET" && ! -s "$JIRA_CONTEXT_FILE" ]]; then
        error "$JIRA_TICKET couldn't be fetched (see above), and under --automata nobody writes the intake. Fix the ticket source (migite doctor) or pass --intake <file>"
      fi
      log "Planning from the task text without opening the editor (--automata)"
    else
      ${EDITOR:-vim} "$INTAKE_FILE"
    fi
  fi

  # Re-derive slug from the Title field after editing; rename dirs if it changed
  if [[ -n "$JIRA_TICKET" ]]; then
    TASK_SLUG=$(slugify "$JIRA_TICKET")
  else
    local TITLE_LINE
    TITLE_LINE=$(grep -m1 -i 'title:' "$INTAKE_FILE" | sed 's/.*Title:[[:space:]]*//' | sed 's/<!--.*-->//' | xargs || true)
    TASK_SLUG="${TITLE_LINE:+$(slugify "$TITLE_LINE")}"
    TASK_SLUG="${TASK_SLUG:-$INTAKE_SLUG_FALLBACK}"
  fi

  if [[ "$TASK_SLUG" != "$PRELIMINARY_SLUG" ]]; then
    local NEW_TASK_DIR="$DEV_LOG_BASE/$ORG/$REPO_NAME/$TASK_SLUG"
    local NEW_SCRATCHPAD_DIR="$REPO_ROOT/scratchpad/$TASK_SLUG"
    if [[ -d "$NEW_TASK_DIR" || -d "$NEW_SCRATCHPAD_DIR" ]]; then
      log "Reusing existing task dir: $TASK_SLUG"
    else
      log "Dirs renamed to: $TASK_SLUG"
    fi
    relocate_task_dir "$TASK_DIR" "$NEW_TASK_DIR"
    relocate_task_dir "$SCRATCHPAD_DIR" "$NEW_SCRATCHPAD_DIR"
    TASK_DIR="$NEW_TASK_DIR"
    SCRATCHPAD_DIR="$NEW_SCRATCHPAD_DIR"
    set_run_paths "$BUILD_RUN_SLUG"
    INTAKE_FILE="$RUN_SCRATCH_DIR/intake.md"
  fi

  # Work on the branch the intake names, creating it from the base branch if missing
  ensure_task_branch "$(intake_branch "$INTAKE_FILE")" "$BASE_BRANCH"
  manifest_refresh_layout

  # PLAN_FILE, TESTING_PLAN_FILE, IMPLEMENTATION_FILE, REVIEW_FILE and their
  # _VAULT siblings come from set_run_paths above.
  local INTAKE_VAULT="$RUN_VAULT_DIR/intake.md"
  CRITIC_FILE="$RUN_SCRATCH_DIR/architecture-critic.md"
  CRITIC_VAULT="$RUN_VAULT_DIR/architecture-critic.md"

  resume_from_vault "$PLAN_FILE" "$PLAN_VAULT"
  resume_from_vault "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
  resume_from_vault "$CRITIC_FILE" "$CRITIC_VAULT"

  cp "$INTAKE_FILE" "$INTAKE_VAULT"
  stamp_file "$INTAKE_FILE"
  stamp_file "$INTAKE_VAULT"
  log "Scratchpad: $SCRATCHPAD_DIR"
  log "Vault mirror: $TASK_DIR"

  # task.md carries two independent sources on top of the passed intake, both
  # optional: --attach files (below) and, --intake mode only, a supplementary
  # editor prompt. It stays its own file (never merged into the passed intake)
  # and is read by migite-plan as additional context, same spirit as
  # amendments staying beside plan.md.
  TASK_FILE=""
  local ATTACH_BLOCK
  ATTACH_BLOCK="$(build_attachments_block)"

  if [[ "$RESUME_PLAN_GATE" == "true" ]]; then
    # Asked before the plan was written; the answer is already on disk.
    [[ -f "$RUN_SCRATCH_DIR/task.md" ]] && TASK_FILE="$RUN_SCRATCH_DIR/task.md"
  elif [[ -n "$INTAKE_FILE_ARG" ]]; then
    echo ""
    local task_file_choice
    read_answer task_file_choice "$(echo -e "${YELLOW}Add supplementary details in a separate task.md before planning? [y/N]: ${RESET}")" N
    if [[ "$task_file_choice" =~ ^[Yy]$ ]]; then
      local TASK_FILE_TMP
      TASK_FILE_TMP=$(mktemp)
      {
        printf '<!-- Additional details/context for this task, on top of the intake above. Lines starting with <!-- are ignored. -->\n\n'
        [[ -n "$ATTACH_BLOCK" ]] && printf '%s' "$ATTACH_BLOCK"
      } > "$TASK_FILE_TMP"
      ${EDITOR:-vim} "$TASK_FILE_TMP"
      local task_file_content
      task_file_content=$(grep -v '^<!--' "$TASK_FILE_TMP" || true)
      if [[ -n "$(echo "$task_file_content" | tr -d '[:space:]')" ]]; then
        TASK_FILE="$RUN_SCRATCH_DIR/task.md"
        printf '%s\n' "$task_file_content" > "$TASK_FILE"
        sync_artifact "$TASK_FILE" "$RUN_VAULT_DIR/task.md"
        success "Additional task details saved to $TASK_FILE"
      else
        warn "No additional details entered — skipping task.md"
      fi
      rm -f "$TASK_FILE_TMP"
    fi
  fi

  # --attach given but no editor pass wrote task.md above (non-intake mode, or
  # the prompt was declined/skipped) — still save the attachments on their own.
  if [[ -z "$TASK_FILE" && -n "$ATTACH_BLOCK" ]]; then
    TASK_FILE="$RUN_SCRATCH_DIR/task.md"
    printf '%s' "$ATTACH_BLOCK" > "$TASK_FILE"
    sync_artifact "$TASK_FILE" "$RUN_VAULT_DIR/task.md"
    success "Attachment(s) saved to $TASK_FILE"
  fi

  # Inject knowledge.md so Plan and Implement phases inherit repo-level memory,
  # the entries closest to the intake and the ticket first
  KNOWLEDGE_FILE="$DEV_LOG_BASE/$ORG/$REPO_NAME/knowledge.md"
  KNOWLEDGE_INJECT=$(build_knowledge_injection "$KNOWLEDGE_FILE" "$INTAKE_FILE" "$JIRA_CONTEXT_FILE")

  # LangGraph plan script args — built once, reused in the gate loop on rejection
  local PLAN_SENTINEL="$SCRATCHPAD_DIR/.plan.done"
  local PLAN_MODULE="migite.tools.plan"
  local PLAN_LANGGRAPH_ARGS=(
    --intake        "$INTAKE_FILE"
    --plan-output   "$PLAN_FILE"
    --critic-output "$CRITIC_FILE"
    --testing-plan-output "$TESTING_PLAN_FILE"
    --repo-root     "$REPO_ROOT"
    --task-type     "${TASK_TYPE:-feature}"
    --sentinel      "$PLAN_SENTINEL"
    --base-branch   "$BASE_BRANCH"
    --stack         "$STACK"
  )
  [[ -f "$KNOWLEDGE_FILE" ]]   && PLAN_LANGGRAPH_ARGS+=(--knowledge  "$KNOWLEDGE_FILE")
  [[ -n "$AUDIT_FILE" ]]      && PLAN_LANGGRAPH_ARGS+=(--audit      "$AUDIT_FILE")
  [[ -n "$BLUEPRINT_FILE" ]]  && PLAN_LANGGRAPH_ARGS+=(--blueprint  "$BLUEPRINT_FILE")
  [[ -n "$TASK_FILE" ]]       && PLAN_LANGGRAPH_ARGS+=(--task-file  "$TASK_FILE")
  [[ -s "$JIRA_CONTEXT_FILE" ]] && PLAN_LANGGRAPH_ARGS+=(--jira-context "$JIRA_CONTEXT_FILE")

  # Display architecture-critic.md written by migite-plan (called after every plan run)
  show_critic() {
    if [[ -f "$CRITIC_FILE" ]] && grep -q '[^[:space:]]' "$CRITIC_FILE" 2>/dev/null \
         && ! grep -q 'No architectural concerns' "$CRITIC_FILE"; then
      echo ""
      echo -e "${BOLD}── Architecture critic ─────────────────────────────${RESET}"
      cat "$CRITIC_FILE"
      echo -e "${BOLD}────────────────────────────────────────────────────${RESET}"
    fi
  }

  # Snapshots plan.md before any action that overwrites it, so a bad
  # refine/redo/edit is always recoverable instead of destroying the last good copy.
  _backup_plan_file() {
    [[ -s "$PLAN_FILE" ]] || return 0
    local hist_dir="$SCRATCHPAD_DIR/.plan-history"
    mkdir -p "$hist_dir"
    cp "$PLAN_FILE" "$hist_dir/plan-$(date +%Y%m%d-%H%M%S).md"
  }

  if [[ -f "$PLAN_FILE" && "$RESUME_PLAN_GATE" == "true" ]]; then
    success "Back at the plan gate where this run stopped: $PLAN_FILE"
  elif [[ -f "$PLAN_FILE" ]]; then
    echo ""
    echo -e "${BOLD}────────────────────────────────────────${RESET}"
    echo -e "${BOLD}  EXISTING PLAN FOUND${RESET}"
    echo -e "  ${CYAN}$PLAN_FILE${RESET}"
    echo -e "${BOLD}────────────────────────────────────────${RESET}"
    echo ""
    local plan_choice
    read_answer plan_choice "$(echo -e "${YELLOW}Use existing plan or redo? [u/r] (u=use existing plan, r=redo from scratch): ${RESET}")" u
    case "$plan_choice" in
      r|R)
        _backup_plan_file
        rm -f "$PLAN_SENTINEL"
        spawn_langgraph "Planning" "plan" "$PLAN_MODULE" "${PLAN_LANGGRAPH_ARGS[@]}"
        [[ -f "$PLAN_SENTINEL" ]] || error "migite-plan did not complete — check agent log in $LOG_DIR"
        sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
        defer_testing_plan
        [[ -f "$TESTING_PLAN_FILE" ]] && sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
        sync_artifact "$CRITIC_FILE" "$CRITIC_VAULT"
        sync_json "$SCRATCHPAD_DIR/plan.json" "$TASK_DIR/plan.json"
        success "Plan written to $PLAN_FILE"
        notify "Phase 1 — Plan ready" "Review the plan and approve to continue"
        ;;
      *)
        success "Using existing plan: $PLAN_FILE"
        ;;
    esac
  else
    rm -f "$PLAN_SENTINEL"
    spawn_langgraph "Planning" "plan" "$PLAN_MODULE" "${PLAN_LANGGRAPH_ARGS[@]}"
    [[ -f "$PLAN_SENTINEL" ]] || error "migite-plan did not complete — check agent log in $LOG_DIR"
    sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
    defer_testing_plan
    [[ -f "$TESTING_PLAN_FILE" ]] && sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
    sync_artifact "$CRITIC_FILE" "$CRITIC_VAULT"
    sync_json "$SCRATCHPAD_DIR/plan.json" "$TASK_DIR/plan.json"
    success "Plan written to $PLAN_FILE"
    notify "Phase 1 — Plan ready" "Review the plan and approve to continue"
  fi

  show_critic

  # Human review gate
  # y — approve and continue
  # f - give feedback, refine plan in place (no re-exploration, one headless agent call)
  # n — full redo (re-run the planner: the explorers + synthesis, or the native plan call, then the critic)
  # q — abort
  # Carries on from run.json when a resumed run re-opens this gate
  PLAN_GATE_ATTEMPTS="${PLAN_GATE_ATTEMPTS:-0}"
  # Guards the "f" (feedback) refine call below: it's a one-shot headless call
  # asked to "output only the revised plan document", but it can instead return a
  # narrative recap of the changes it made. Since the instructions tell it to keep
  # the same structure, a genuine revision preserves most of the original headings —
  # a summary won't. Require most of the original plan.md headings still be present.
  _plan_heading_overlap_ok() {
    local orig="$1" new="$2"
    local orig_count kept_count
    orig_count=$(grep -c '^#' "$orig" 2>/dev/null || echo 0)
    if [[ "$orig_count" -eq 0 ]]; then
      # The current plan.md already has no structure — it may itself be a prior
      # bad write. Refuse rather than blindly accepting whatever comes back;
      # there's nothing to validate the refinement against.
      return 1
    fi
    kept_count=$(comm -12 <(grep '^#' "$orig" | sort -u) <(grep '^#' "$new" | sort -u) | wc -l | tr -d ' ')
    awk -v k="$kept_count" -v t="$orig_count" 'BEGIN { exit !(k/t >= 0.6) }'
  }
  _show_plan_diff() {
    local prev="$1"
    if [[ -f "$prev" ]]; then
      echo ""
      echo -e "${BOLD}── Plan changes ────────────────────────────────────${RESET}"
      diff --unified=2 "$prev" "$PLAN_FILE" | tail -n +4 \
        | grep -v '^@@' \
        | awk '/^\+/ && !/^\+\+\+/ { print "\033[0;32m" $0 "\033[0m"; next }
               /^-/  && !/^---/   { print "\033[0;31m" $0 "\033[0m"; next }
               { print }' \
        || echo "  (no visible diff)"
      echo -e "${BOLD}────────────────────────────────────────────────────${RESET}"
      rm -f "$prev"
    fi
  }

  while true; do
    PLAN_GATE_ATTEMPTS=$((PLAN_GATE_ATTEMPTS + 1))
    read_gate_choice "REVIEW GATE: plan" "Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort): " y
    case "$GATE_CHOICE" in
      y|Y)
        snapshot_approved_plan
        success "Plan approved — continuing"
        break
        ;;
      f|F)
        echo ""
        local _plan_feedback
        read -r -p "$(echo -e "${CYAN}  Feedback (what to change): ${RESET}")" _plan_feedback
        if [[ -z "$_plan_feedback" ]]; then
          warn "No feedback entered — try again"
          continue
        fi
        _backup_plan_file
        echo ""
        echo -e "${CYAN}  Refining plan...${RESET}"
        local _refine_prev
        _refine_prev=$(mktemp)
        cp "$PLAN_FILE" "$_refine_prev"
        local _refine_tmp
        _refine_tmp=$(mktemp)
        cp "$PLAN_FILE" "$_refine_tmp"
        # Exact edits first (edit_document): feedback usually touches a few passages,
        # and re-emitting the whole plan to change them is the slowest call in the gate.
        if edit_document "$_refine_tmp" plan_refine "plan-refine:edits" "plan.md" \
             "The engineer reviewed this plan at the approval gate and asked for this change: ${_plan_feedback}. Edit the plan to make it."; then
          mv "$_refine_tmp" "$PLAN_FILE"
          sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
          _show_plan_diff "$_refine_prev"
          success "Plan refined — review again"
        else
          warn "No usable plan edits; revising the whole plan instead"
          printf 'Here is the current development plan:\n\n%s\n\nThe engineer has this feedback:\n%s\n\nRevise the plan to address the feedback. Keep the same structure and format. Output only the revised plan document — no preamble.' \
            "$(cat "$PLAN_FILE")" "$_plan_feedback" \
            | agent_ask "plan-refine" plan_refine \
            > "$_refine_tmp" 2>/dev/null || true
          if [[ -s "$_refine_tmp" ]] && _plan_heading_overlap_ok "$_refine_prev" "$_refine_tmp"; then
            mv "$_refine_tmp" "$PLAN_FILE"
            sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
            _show_plan_diff "$_refine_prev"
            success "Plan refined — review again"
          else
            if [[ -s "$_refine_tmp" ]]; then
              warn "Refinement didn't look like a revised plan (structure changed too much) — plan unchanged"
            else
              warn "Refinement returned empty — plan unchanged"
            fi
            rm -f "$_refine_tmp" "$_refine_prev"
          fi
        fi
        show_critic
        ;;
      e|E)
        # Direct edit — open plan.md in $EDITOR, no AI round-trip
        _backup_plan_file
        ${EDITOR:-vim} "$PLAN_FILE"
        sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
        success "Plan saved — review again"
        show_critic
        ;;
      n|N)
        warn "Rejected — re-running full plan"
        # gates.plan.warn_after_rejections: repeated full redos usually mean the
        # intake is too large or too vague for one plan, not that the planner is unlucky.
        local _redo_limit
        _redo_limit="$(cfg gates.plan.warn_after_rejections 3)"
        if [[ "$_redo_limit" -gt 0 && "$PLAN_GATE_ATTEMPTS" -ge "$_redo_limit" ]]; then
          warn "This is redo #$PLAN_GATE_ATTEMPTS — consider splitting the task, tightening the intake (e), or scoping it with migite-explore first"
        fi
        _backup_plan_file
        local _rerun_prev
        _rerun_prev=$(mktemp)
        cp "$PLAN_FILE" "$_rerun_prev"
        rm -f "$PLAN_SENTINEL"
        spawn_langgraph "Revising plan" "plan-r${PLAN_GATE_ATTEMPTS}" "$PLAN_MODULE" "${PLAN_LANGGRAPH_ARGS[@]}"
        [[ -f "$PLAN_SENTINEL" ]] || warn "migite-plan revision may not have completed — check agent log in $LOG_DIR"
        sync_artifact "$PLAN_FILE" "$PLAN_VAULT"
        defer_testing_plan
        [[ -f "$TESTING_PLAN_FILE" ]] && sync_artifact "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
        sync_artifact "$CRITIC_FILE" "$CRITIC_VAULT"
        sync_json "$SCRATCHPAD_DIR/plan.json" "$TASK_DIR/plan.json"
        _show_plan_diff "$_rerun_prev"
        show_critic
        ;;
      q|Q)
        manifest_boundary plan pending_gate --set-json "phases.plan.gate_attempts=$PLAN_GATE_ATTEMPTS" --set "args.task_type=${TASK_TYPE:-}"
        warn "Workflow aborted - run the same command again to come back to this gate"
        exit 0
        ;;
      *)
        warn "Invalid input — use y / f / e / n / q"
        ;;
    esac
  done
}

# SC2034: TDD_DECIDED is read by bin/migite, which records it in run.json.
# shellcheck disable=SC2034
run_tdd() {
  echo ""
  local tdd_choice
  TDD_DECIDED=false
  read_answer tdd_choice "$(echo -e "${YELLOW}Phase 1.5 (TDD): Write spec files before implementation? [y/N]: ${RESET}")" N
  if [[ "$tdd_choice" =~ ^[Yy]$ ]]; then
    TDD_DECIDED=true
    log "Phase 1.5/4 — Writing specs (TDD red phase)"

    local SPEC_IMPL_FILE="$RUN_SCRATCH_DIR/spec-implementation.md"
    local SPEC_IMPL_PROMPT="${KNOWLEDGE_INJECT}$(cat "$IMPLEMENT_CMD_PATH" | sed "s|\\[PLAN_PATH\\]|$PLAN_FILE|g")

$(cat "$PLAN_FILE")

## TDD Phase — write specs ONLY
You are in the spec-writing (red) phase. Your ONLY job is to write RSpec spec files.

Rules:
- Write ONLY the spec files listed in the plan's Testing Plan section
- Do NOT write any implementation code — no models, services, controllers, or migrations
- Do NOT create any non-spec files
- Specs MUST fail at this stage — that is expected and correct (red phase of TDD)
- Write specs that fully cover the acceptance criteria and edge cases in the plan
- When done, write the list of spec files created to: $SPEC_IMPL_FILE"

    run_phase "Writing specs (TDD)" "$SPEC_IMPL_FILE" "$SPEC_IMPL_PROMPT"
    sync_artifact "$SPEC_IMPL_FILE" "$RUN_VAULT_DIR/spec-implementation.md"
    success "Spec files written"

    log "Confirming red state (specs should fail)..."
    local TDD_RSPEC_LOG="$LOG_DIR/$TIMESTAMP-${TASK_SLUG}-tdd-rspec.txt"
    local TDD_CHANGED_SPECS
    TDD_CHANGED_SPECS=$(changed_spec_files "$BASE_BRANCH")
    if [[ -n "$TDD_CHANGED_SPECS" ]]; then
      local TDD_APP_SPECS
      TDD_APP_SPECS=$(strip_app_prefix "$TDD_CHANGED_SPECS")
      # shellcheck disable=SC2086
      if bundle_exec rspec $TDD_APP_SPECS 2>&1 | tee "$TDD_RSPEC_LOG"; then
        warn "Specs passed — they should be failing at this stage. Verify the spec files target unimplemented behaviour."
      else
        success "Red state confirmed — specs fail as expected"
      fi
    else
      warn "No new spec files in diff — skipping red-state check"
    fi
    notify "Phase 1.5 — Specs written" "Red state confirmed — ready for implementation"
  fi
}
