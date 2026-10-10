#!/usr/bin/env bash
# lib/manifest.sh - the run manifest (run.json): where a run got to, so an
# interrupted run continues from there instead of restarting at the plan.
#
# run.json lives in the run's own folder (scratchpad/<task>/<run>/run.json) and
# is mirrored to the vault with sync_json after every write. migite/runstate.py
# owns the format; this file decides when to write it (manifest_init,
# manifest_boundary, manifest_set), finds the run an invocation means
# (manifest_discover), and puts a run back together from it (manifest_restore).
# See docs/run-manifest-and-resume.md.
#
# The write helpers expect the set_run_paths variables and do nothing before a
# run folder exists. A failed write warns and the run carries on: the manifest
# is how a run resumes, never a reason to stop one.

# runstate_cmd <init|update|export|find> ... - migite/runstate.py
runstate_cmd() {
  "$MIGITE_PYTHON" -m migite.runstate "$@"
}

# manifest_file - this run's run.json, or exit 1 before a run folder exists.
manifest_file() {
  [[ -n "${RUN_SCRATCH_DIR:-}" ]] || return 1
  echo "$RUN_SCRATCH_DIR/run.json"
}

# _manifest_write <init|update> [runstate args...] - write, then mirror to the
# vault. An update with no run.json yet (a run from before manifests) is skipped.
_manifest_write() {
  local cmd="$1" file
  shift
  file=$(manifest_file) || return 0
  [[ "$cmd" == "init" || -f "$file" ]] || return 0
  if ! runstate_cmd "$cmd" --file "$file" "$@"; then
    warn "Could not update $file; if this run stops, it may not resume from here"
    return 0
  fi
  sync_json "$file" "$RUN_VAULT_DIR/run.json"
}

# _manifest_layout_args - the --set flags for the repo and layout sections, from
# the current globals. Re-sent whenever they can change (slug rename, branch switch).
_manifest_layout_args() {
  printf '%s\0' \
    --set "repo.org=$ORG" --set "repo.name=$REPO_NAME" --set "repo.root=$REPO_ROOT" \
    --set "repo.branch=${BRANCH:-}" --set "repo.base_branch=${BASE_BRANCH:-}" \
    --set "layout.slug=$(basename "$SCRATCHPAD_DIR")" --set "layout.run_slug=$RUN_SLUG" \
    --set "layout.task_dir=$TASK_DIR" --set "layout.scratchpad_dir=$SCRATCHPAD_DIR" \
    --set "layout.log_dir=$LOG_DIR" --set "layout.usage_ledger=${MIGITE_USAGE_LEDGER:-}" \
    --set "layout.timestamp=$TIMESTAMP"
}

# run_mode - this invocation's run.json `mode`: automata under --automata, else
# interactive.
run_mode() {
  if automata; then echo automata; else echo interactive; fi
}

# manifest_init - a fresh run.json for this run, written when its first phase
# (plan, or the amendment in amend mode) begins: the arguments, repo and layout,
# the mode, every phase pending except plan, which is running.
manifest_init() {
  local -a sets=()
  local f
  while IFS= read -r -d '' f; do sets+=("$f"); done < <(_manifest_layout_args)
  sets+=(--set "args.task=$TASK" --set "args.task_type=${TASK_TYPE:-}"
         --set "args.jira_ticket=$JIRA_TICKET" --set "args.jira_url=$JIRA_URL"
         --set "args.intake=$INTAKE_FILE_ARG" --set "args.audit=$AUDIT_FILE"
         --set "args.blueprint=$BLUEPRINT_FILE" --set "args.stack=$STACK_OVERRIDE"
         --set-json "args.staged=$STAGED" --set "phases.plan.status=running"
         --set "mode=$(run_mode)")
  for f in ${ATTACH_FILES[@]+"${ATTACH_FILES[@]}"}; do
    sets+=(--add "args.attach=$f")
  done
  if [[ "${AMEND_MODE:-false}" == "true" ]]; then
    sets+=(--set "args.amend.feedback=${AMEND_FEEDBACK_TEXT:-$AMEND_FEEDBACK}"
           --set "args.amend.file=$AMEND_FEEDBACK_FILE" --set "args.amend.num=$AMEND_NUM")
  fi
  _manifest_write init "${sets[@]}"
}

# manifest_refresh_layout - re-record the slug, paths and branch after run_plan
# renames the task folders or switches to the intake's branch.
manifest_refresh_layout() {
  local -a sets=()
  local f
  while IFS= read -r -d '' f; do sets+=("$f"); done < <(_manifest_layout_args)
  _manifest_write update "${sets[@]}"
}

# manifest_set [--set K=V | --set-json K=JSON | --add K=V]... - merge into run.json.
manifest_set() {
  _manifest_write update "$@"
}

# manifest_boundary <phase> <status> [manifest_set flags...] - a phase changes
# status; runstate stamps started_at / completed_at and recomputes next_phase.
manifest_boundary() {
  local phase="$1" status="$2"
  shift 2
  manifest_set --set "phases.$phase.status=$status" "$@"
}

# manifest_phase_status <phase> - the phase's recorded status, empty without a run.json.
manifest_phase_status() {
  local file
  file=$(manifest_file) || return 0
  [[ -f "$file" ]] || return 0
  json_field "$file" "phases.$1.status" || true
}

# manifest_review_blockers - the blockers run.json recorded at the commit gate's
# last decision (phases.review.blockers), one per line; nothing without a run.json.
manifest_review_blockers() {
  local file i=0 b
  file=$(manifest_file) || return 0
  [[ -f "$file" ]] || return 0
  while b=$(json_field "$file" "phases.review.blockers.$i"); do
    printf '%s\n' "$b"
    i=$((i + 1))
  done
  return 0
}

# manifest_should_run <phase> - false (and says so) when run.json records the
# phase as done or skipped by an earlier invocation. Every other status runs it:
# pending, running (it was interrupted), pending_gate (its gate re-opens), failed.
manifest_should_run() {
  case "$(manifest_phase_status "$1")" in
    done|skipped)
      log "Phase $1 already finished in an earlier invocation (run.json) - skipping"
      return 1 ;;
  esac
  return 0
}

# manifest_enter <phase> - the phase begins: MANIFEST_ENTRY_STATUS keeps what
# run.json said before (pending_gate tells the phase to re-open its gate), then
# the phase is marked running, so a crash inside it leaves it identifiable.
manifest_enter() {
  # shellcheck disable=SC2034  # read by the phase files (lib/phases/*.sh)
  MANIFEST_ENTRY_STATUS="$(manifest_phase_status "$1")"
  manifest_boundary "$1" running
}

# manifest_on_exit <exit code> - from migite's EXIT trap: records the exit status
# the caller sees (exit_status, and one line in invocations with this
# invocation's start, end and mode), and a non-zero exit marks the run failed.
# The phase it was in stays running, which is what makes resume re-run exactly
# that phase. A q at a gate exits 0 and leaves the run in_progress, and so does a
# status an automata run exits with on purpose (migite_exit: 2 at a blocked commit
# gate, 3 when it finished over blockers). Any other non-zero code is reported as
# 1, as bin/migite's trap does.
manifest_on_exit() {
  local status="$1"
  local -a sets=()
  if [[ "$status" -ne 0 && "$status" != "${MIGITE_EXIT_STATUS:-}" ]]; then
    status=1
    sets+=(--set "status=failed")
  fi
  sets+=(--set-json "exit_status=$status"
         --add "invocations=${MIGITE_STARTED_AT:-?} to $(date -u +%Y-%m-%dT%H:%M:%SZ), $(run_mode), exit $status")
  manifest_set "${sets[@]}"
}

# manifest_find [runstate find flags...] - the newest manifest for this repo, in
# the scratchpad or (when that copy is gone) the vault mirror.
manifest_find() {
  runstate_cmd find --root "$REPO_ROOT/scratchpad" --root "$DEV_LOG_BASE/$ORG/$REPO_NAME" "$@"
}

# manifest_discover - set MANIFEST_PATH to the run.json this invocation resumes,
# or leave it empty for a fresh run. Expects the parsed arguments and
# RESUME_REQUESTED / RESUME_FILE.
#   --resume <file>  that manifest
#   --resume         the newest unfinished run, narrowed by any task arguments given
#   a plain run      the newest build whose arguments match (Jira key, intake file,
#                    or task text), which finds the task even after its Title renamed
#                    the folder; else run.json in the folder the arguments name
#   --amend          never: every --amend scopes a new amendment
# SC2034: MANIFEST_PATH is read by bin/migite; shellcheck lints one file at a time.
# shellcheck disable=SC2034
manifest_discover() {
  MANIFEST_PATH=""
  local -a query=()
  [[ -n "$JIRA_TICKET" ]] && query+=(--jira "$JIRA_TICKET")
  [[ -n "$INTAKE_FILE_ARG" ]] && query+=(--intake "$INTAKE_FILE_ARG")
  [[ -n "$TASK" ]] && query+=(--task "$TASK")
  [[ -n "$AUDIT_FILE" ]] && query+=(--audit "$AUDIT_FILE")

  if [[ -n "${RESUME_FILE:-}" ]]; then
    MANIFEST_PATH="$RESUME_FILE"
    return 0
  fi
  if [[ "${RESUME_REQUESTED:-false}" == "true" ]]; then
    MANIFEST_PATH=$(manifest_find --unfinished ${query[@]+"${query[@]}"}) \
      || error "No unfinished run to resume for $ORG/$REPO_NAME$([[ ${#query[@]} -gt 0 ]] && echo " matching these arguments")"
    return 0
  fi
  [[ "$AMEND_MODE" == "true" || ${#query[@]} -eq 0 ]] && return 0
  if MANIFEST_PATH=$(manifest_find --builds-only "${query[@]}"); then
    return 0
  fi
  MANIFEST_PATH=""
  # No manifest's arguments match: fall back to the folder they name. Not for an
  # audit run, whose folder name every audit without a task shares.
  [[ -n "$AUDIT_FILE" ]] && return 0
  local slug f
  slug=$(slugify "${JIRA_TICKET:-$TASK}")
  [[ -n "$slug" ]] || return 0
  for f in "$REPO_ROOT/scratchpad/$slug/$BUILD_RUN_SLUG/run.json" "$DEV_LOG_BASE/$ORG/$REPO_NAME/$slug/$BUILD_RUN_SLUG/run.json"; do
    if [[ -f "$f" ]]; then
      MANIFEST_PATH="$f"
      return 0
    fi
  done
  return 0
}

# _manifest_checkout_branch <branch> - back to the branch the run was on.
_manifest_checkout_branch() {
  local want="$1"
  [[ -z "$want" || "$want" == "$BRANCH" ]] && return 0
  if git show-ref --verify --quiet "refs/heads/$want"; then
    git checkout -q "$want" || error "Could not check out '$want', the branch this run was on - commit or stash local changes first"
    BRANCH="$want"
    log "Checked out $want, the branch this run was on"
  else
    warn "This run was on branch '$want', which doesn't exist here - staying on ${BRANCH:-detached HEAD}"
  fi
}

# manifest_restore_artifacts - pull the run's documents back from the vault
# mirror where the scratchpad copy is missing (cleaned scratchpad, fresh clone,
# another machine), the same resume_from_vault pattern run_plan and amend use.
manifest_restore_artifacts() {
  local f
  resume_from_vault "$PLAN_FILE" "$PLAN_VAULT"
  resume_from_vault "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
  resume_from_vault "$SCRATCHPAD_DIR/plan.json" "$TASK_DIR/plan.json"
  if [[ "${AMEND_MODE:-false}" == "true" ]]; then
    mkdir -p "$SCRATCHPAD_DIR/$BUILD_RUN_SLUG"
    resume_from_vault "$SCRATCHPAD_DIR/$BUILD_RUN_SLUG/intake.md" "$TASK_DIR/$BUILD_RUN_SLUG/intake.md"
  fi
  for f in intake.md task.md jira-context.md architecture-critic.md spec-implementation.md \
           amendment.md implementation.md review.md review.json review-dimensions.json gate-overrides.md; do
    resume_from_vault "$RUN_SCRATCH_DIR/$f" "$RUN_VAULT_DIR/$f"
  done
  for f in "$RUN_VAULT_DIR"/implementation-stage-*.md "$RUN_VAULT_DIR"/fix-r*.md; do
    [[ -f "$f" ]] && resume_from_vault "$RUN_SCRATCH_DIR/$(basename "$f")" "$f"
  done
  return 0
}

# manifest_restore <run.json> - put a run back together from its manifest: the
# arguments the command line left out (all of them, for a bare --resume), the
# branch, the task and run folders (re-derived from the slug and today's config
# by set_run_paths, the same as run_plan and amend mode), the documents, and the
# globals the phases it skips would have set. Sets MANIFEST_RESUMED=true, or
# MANIFEST_COMPLETE=true (and nothing else) for a run that already finished.
# Expects the parsed arguments, REPO_ROOT, REPO_NAME, ORG, BRANCH and the config.
# SC2034: globals the phase files read; shellcheck lints one file at a time.
# shellcheck disable=SC2034
manifest_restore() {
  local file="$1" vars
  vars=$(runstate_cmd export --shell --file "$file") \
    || error "Can't resume from $file (see above). Delete it to start the task over."
  eval "$vars"
  if [[ "${MANIFEST_REPO_ORG:-}" != "$ORG" || "${MANIFEST_REPO_NAME:-}" != "$REPO_NAME" ]]; then
    error "$file is a run of ${MANIFEST_REPO_ORG:-?}/${MANIFEST_REPO_NAME:-?}, not $ORG/$REPO_NAME"
  fi
  [[ -n "${MANIFEST_LAYOUT_SLUG:-}" && -n "${MANIFEST_LAYOUT_RUN_SLUG:-}" ]] \
    || error "$file doesn't record its task and run folders; delete it to start the task over"
  # An unattended run continues unattended only when asked to again: checked
  # before anything below changes the manifest, the branch or the files.
  if [[ "${MANIFEST_MODE:-interactive}" == "automata" && "${MANIFEST_STATUS:-}" != "complete" ]] && ! automata; then
    error "This run was started with --automata ($file); run the same command again with --automata to continue it"
  fi

  TASK_SLUG="$MANIFEST_LAYOUT_SLUG"
  TASK_DIR="$DEV_LOG_BASE/$ORG/$REPO_NAME/$TASK_SLUG"
  SCRATCHPAD_DIR="$REPO_ROOT/scratchpad/$TASK_SLUG"
  if [[ "${MANIFEST_STATUS:-}" == "complete" ]]; then
    set_run_paths "$MANIFEST_LAYOUT_RUN_SLUG"
    MANIFEST_COMPLETE=true
    return 0
  fi

  # The command line wins; the manifest fills in what it left out.
  [[ -n "$TASK" ]] || TASK="${MANIFEST_ARGS_TASK:-}"
  [[ -n "$TASK_TYPE" ]] || TASK_TYPE="${MANIFEST_ARGS_TASK_TYPE:-}"
  [[ -n "$JIRA_TICKET" ]] || { JIRA_TICKET="${MANIFEST_ARGS_JIRA_TICKET:-}"; JIRA_URL="${MANIFEST_ARGS_JIRA_URL:-}"; }
  [[ -n "$INTAKE_FILE_ARG" ]] || INTAKE_FILE_ARG="${MANIFEST_ARGS_INTAKE:-}"
  [[ -n "$AUDIT_FILE" ]] || AUDIT_FILE="${MANIFEST_ARGS_AUDIT:-}"
  [[ -n "$BLUEPRINT_FILE" ]] || BLUEPRINT_FILE="${MANIFEST_ARGS_BLUEPRINT:-}"
  [[ -n "$STACK_OVERRIDE" ]] || STACK_OVERRIDE="${MANIFEST_ARGS_STACK:-}"
  [[ "${MANIFEST_ARGS_STAGED:-false}" == "true" ]] && STAGED=true
  if [[ ${#ATTACH_FILES[@]} -eq 0 ]]; then
    ATTACH_FILES=(${MANIFEST_ARGS_ATTACH[@]+"${MANIFEST_ARGS_ATTACH[@]}"})
  fi
  if [[ -n "${MANIFEST_ARGS_AMEND_NUM:-}" ]]; then
    AMEND_MODE=true
    AMEND_NUM="$MANIFEST_ARGS_AMEND_NUM"
    AMEND_FEEDBACK="${MANIFEST_ARGS_AMEND_FEEDBACK:-}"
    AMEND_FEEDBACK_FILE="${MANIFEST_ARGS_AMEND_FILE:-}"
  fi

  _manifest_checkout_branch "${MANIFEST_REPO_BRANCH:-}"
  [[ "${MANIFEST_REPO_ROOT:-}" == "$REPO_ROOT" ]] \
    || warn "This run was recorded in ${MANIFEST_REPO_ROOT:-another checkout}; resuming it in $REPO_ROOT"
  [[ -n "$BASE_BRANCH" ]] || BASE_BRANCH="${MANIFEST_REPO_BASE_BRANCH:-}"
  # The ledger carries on, so the cost total spans the interruption.
  [[ -n "${MIGITE_USAGE_LEDGER:-}" ]] || MIGITE_USAGE_LEDGER="${MANIFEST_LAYOUT_USAGE_LEDGER:-}"

  mkdir -p "$TASK_DIR" "$SCRATCHPAD_DIR"
  set_run_paths "$MANIFEST_LAYOUT_RUN_SLUG"
  # A manifest found in the vault (or named with --resume) becomes the run's own.
  [[ "$file" -ef "$RUN_SCRATCH_DIR/run.json" ]] || cp -p "$file" "$RUN_SCRATCH_DIR/run.json"
  manifest_restore_artifacts
  # A failed run is unfinished again; next_phase still says where it stopped. The
  # mode is this invocation's: an interactive run resumed with --automata is an
  # automata run from here on.
  manifest_set --set "status=in_progress" --set "mode=$(run_mode)"

  # What the phases this resume skips would have set.
  if [[ "${AMEND_MODE:-false}" == "true" ]]; then
    INTAKE_FILE="$SCRATCHPAD_DIR/$BUILD_RUN_SLUG/intake.md"
    AMENDMENT_FILE="$RUN_SCRATCH_DIR/amendment.md"
    # An amend run that stopped before its amendment was written has nothing to
    # re-open: the next --amend scopes it afresh.
    [[ "${MANIFEST_PHASES_PLAN_STATUS:-}" == "done" || -s "$AMENDMENT_FILE" ]] \
      || error "Amendment $AMEND_NUM was never written; run migite --amend again to scope it"
  else
    INTAKE_FILE="$RUN_SCRATCH_DIR/intake.md"
  fi
  CRITIC_FILE="$RUN_SCRATCH_DIR/architecture-critic.md"
  CRITIC_VAULT="$RUN_VAULT_DIR/architecture-critic.md"
  TASK_FILE=""
  [[ -f "$RUN_SCRATCH_DIR/task.md" ]] && TASK_FILE="$RUN_SCRATCH_DIR/task.md"
  KNOWLEDGE_FILE="$DEV_LOG_BASE/$ORG/$REPO_NAME/knowledge.md"
  KNOWLEDGE_INJECT=$(build_knowledge_injection "$KNOWLEDGE_FILE" "$INTAKE_FILE" "$(dirname "$INTAKE_FILE")/jira-context.md")
  PLAN_GATE_ATTEMPTS="${MANIFEST_PHASES_PLAN_GATE_ATTEMPTS:-0}"
  COMMIT_GATE_ATTEMPTS="${MANIFEST_PHASES_REVIEW_GATE_ATTEMPTS:-0}"
  HEAL_ATTEMPT="${MANIFEST_PHASES_HEAL_HEAL_ATTEMPT:-0}"
  RESUME_STAGE_NUM="${MANIFEST_PHASES_IMPLEMENT_STAGE_NUM:-0}"
  RESUME_REVIEW_FINGERPRINT="${MANIFEST_PHASES_REVIEW_TREE_FINGERPRINT:-}"
  # Read by Phase 4.5 when the review phase is skipped.
  RUBOCOP_LOG="${RUBOCOP_LOG:-}"
  RSPEC_LOG="${RSPEC_LOG:-}"
  MANIFEST_RESUMED=true
  log "Resuming $TASK_SLUG/$RUN_SLUG from $file - next phase: ${MANIFEST_NEXT_PHASE:-plan}"
}
