# tests/manifest_test.sh - lib/manifest.sh: writing run.json at phase boundaries
# (and its vault mirror), finding the run an invocation means, and putting a run
# back together from its manifest. Each block runs in a ( subshell ), since the
# helpers set run globals. The end-to-end resume of bin/migite is resume_test.sh.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON - skipping run manifest checks"
  return 0 2>/dev/null || exit 0
fi

mf_dir=$(mktemp -d); CLEANUP_DIRS+=("$mf_dir")
mf_repo=$(make_fixture_repo)
git -C "$mf_repo" commit -q --allow-empty -m init
git -C "$mf_repo" branch bb-1

# mf_env - the globals bin/migite has set by the time manifest helpers run.
mf_env() {
  REPO_ROOT="$mf_repo"; ORG="Acme"; REPO_NAME="api"; DEV_LOG_BASE="$mf_dir/vault"
  BRANCH="main"; BASE_BRANCH=""; TIMESTAMP="20260101-000000"
  TASK=""; TASK_TYPE=""; JIRA_TICKET=""; JIRA_URL=""; INTAKE_FILE_ARG=""; AUDIT_FILE=""
  BLUEPRINT_FILE=""; STACK_OVERRIDE=""; STAGED=false; ATTACH_FILES=()
  AMEND_MODE=false; AMEND_FEEDBACK=""; AMEND_FEEDBACK_FILE=""; AMEND_NUM=""
  RESUME_REQUESTED=false; RESUME_FILE=""; MANIFEST_RESUMED=false; MANIFEST_COMPLETE=false
  unset RUN_SCRATCH_DIR RUN_VAULT_DIR MIGITE_USAGE_LEDGER
  cd "$mf_repo" || return 1
}

# mf_manifest <task-slug> <run-slug> <where: scratch|vault> [runstate sets...] -
# a manifest for this repo with its layout recorded; echoes its path.
mf_manifest() {
  local slug="$1" run="$2" where="$3" file
  shift 3
  if [[ "$where" == "vault" ]]; then
    file="$mf_dir/vault/Acme/api/$slug/$run/run.json"
  else
    file="$mf_repo/scratchpad/$slug/$run/run.json"
  fi
  runstate_cmd init --file "$file" --set repo.org=Acme --set repo.name=api --set "repo.root=$mf_repo" \
    --set "layout.slug=$slug" --set "layout.run_slug=$run" "$@" >/dev/null
  echo "$file"
}

# ── Writing: init, boundaries, the vault mirror ──────────────────────────────
(
  mf_env
  SCRATCHPAD_DIR="$mf_repo/scratchpad/add-thing"; TASK_DIR="$mf_dir/vault/Acme/api/add-thing"
  out=$(manifest_set --set args.task=x 2>&1); rc=$?
  check "manifest helpers: no run folder yet → a silent no-op" test "$rc|$out" = "0|"
  set_run_paths "$BUILD_RUN_SLUG"
  manifest_set --set "args.task=x"
  check "manifest_set: a run with no run.json yet (from before manifests) is left without one" \
    not test -e "$RUN_SCRATCH_DIR/run.json"

  TASK="Add thing"; TASK_TYPE="feature"; STAGED=true; ATTACH_FILES=("/notes/a b.md" "/notes/c.md")
  manifest_init
  f="$RUN_SCRATCH_DIR/run.json"
  check "manifest_init: run.json in the run's own folder" test -f "$f"
  check "manifest_init: mirrored to the vault run folder, byte for byte" cmp -s "$f" "$RUN_VAULT_DIR/run.json"
  check "manifest_init: the plan phase is running" test "$(json_field "$f" phases.plan.status)" = "running"
  check "manifest_init: records the arguments (attach list, staged flag)" \
    test "$(json_field "$f" args.attach.0)|$(json_field "$f" args.staged)|$(json_field "$f" args.task)" = "/notes/a b.md|true|Add thing"
  check "manifest_init: records the layout and repo" \
    test "$(json_field "$f" layout.slug)|$(json_field "$f" layout.run_slug)|$(json_field "$f" repo.name)" = "add-thing|00-build|api"

  manifest_boundary plan done --set-json "phases.plan.gate_attempts=2"
  check "manifest_boundary: phase status, extra fields and next_phase" \
    test "$(json_field "$f" phases.plan.status)|$(json_field "$f" phases.plan.gate_attempts)|$(json_field "$f" next_phase)" = "done|2|tdd"
  check "manifest_boundary: the vault mirror follows every write" cmp -s "$f" "$RUN_VAULT_DIR/run.json"
  out=$(manifest_should_run plan); rc=$?
  check "manifest_should_run: a done phase is skipped, and says so" \
    bash -c '[[ "$1" != 0 && "$2" == *"already finished"* ]]' _ "$rc" "$out"
  check "manifest_should_run: a pending phase runs" manifest_should_run tdd

  manifest_boundary review pending_gate
  manifest_enter review
  check "manifest_enter: keeps the status it found (pending_gate) and marks the phase running" \
    test "$MANIFEST_ENTRY_STATUS|$(json_field "$f" phases.review.status)" = "pending_gate|running"

  out=$(manifest_set --set "phases.plan.status=bogus" 2>&1); rc=$?
  check "manifest_set: a refused write warns and the run carries on" \
    bash -c '[[ "$1" == 0 && "$2" == *"Could not update"* ]]' _ "$rc" "$out"

  MIGITE_STARTED_AT="2026-01-01T00:00:00Z"
  manifest_on_exit 0
  check "manifest_on_exit 0: a clean exit (or a q at a gate) leaves the run in progress" \
    test "$(json_field "$f" status)" = "in_progress"
  check "manifest_on_exit 0: records exit status 0 and the invocation, with its start and mode" \
    bash -c '[[ "$1" == 0 && "$2" == "2026-01-01T00:00:00Z to "*", interactive, exit 0" ]]' _ \
    "$(json_field "$f" exit_status)" "$(json_field "$f" invocations.0)"
  ( MIGITE_EXIT_STATUS=3 MIGITE_AUTOMATA=true manifest_on_exit 3 )
  check "manifest_on_exit 3 (migite_exit): status 3 recorded, the run not failed, the mode automata" \
    bash -c '[[ "$1" == 3 && "$2" == in_progress && "$3" == *", automata, exit 3" ]]' _ \
    "$(json_field "$f" exit_status)" "$(json_field "$f" status)" "$(json_field "$f" invocations.1)"
  manifest_on_exit 7
  check "manifest_on_exit 7: any other failure is recorded as 1, as the caller sees it" \
    bash -c '[[ "$1" == 1 && "$2" == *", exit 1" ]]' _ "$(json_field "$f" exit_status)" "$(json_field "$f" invocations.2)"
  manifest_on_exit 1
  check "manifest_on_exit 1: an error marks the run failed, the phase stays running" \
    test "$(json_field "$f" status)|$(json_field "$f" phases.review.status)" = "failed|running"
)

# ── Discovery ─────────────────────────────────────────────────────────────────
(
  mf_env
  renamed=$(mf_manifest add-a-widget-to-the-page 00-build scratch --set "args.task=Add thing")
  TASK="Add thing"; manifest_discover
  check "manifest_discover: the task text finds a run whose Title renamed its folder" test "$MANIFEST_PATH" = "$renamed"

  TASK=""; manifest_discover
  check "manifest_discover: no task arguments → a fresh run" test -z "$MANIFEST_PATH"

  TASK="Add thing"; AMEND_MODE=true; manifest_discover
  check "manifest_discover: --amend never resumes (each one is a new amendment)" test -z "$MANIFEST_PATH"
  AMEND_MODE=false

  jira=$(mf_manifest bb-7 00-build vault --set "args.jira_ticket=BB-7" --set "args.task=BB-7")
  TASK="BB-7"; JIRA_TICKET="BB-7"; manifest_discover
  check "manifest_discover: a Jira run found in the vault when the scratchpad lost it" test "$MANIFEST_PATH" = "$jira"

  slugged=$(mf_manifest fix-login 00-build scratch --set "args.task=fix login")
  TASK="Fix login"; JIRA_TICKET=""; manifest_discover
  check "manifest_discover: no argument match → run.json in the folder the arguments name" test "$MANIFEST_PATH" = "$slugged"

  AUDIT_FILE="/reports/new-audit.md"; TASK="fix-login"; manifest_discover
  check "manifest_discover: an audit run doesn't fall back to a folder every audit shares" test -z "$MANIFEST_PATH"
  AUDIT_FILE=""

  runstate_cmd update --file "$renamed" --set "phases.plan.status=done" >/dev/null
  TASK=""; RESUME_REQUESTED=true; manifest_discover
  check "manifest_discover --resume: the newest unfinished run" test "$MANIFEST_PATH" = "$renamed"

  RESUME_FILE="$slugged"; manifest_discover
  check "manifest_discover --resume <file>: that manifest" test "$MANIFEST_PATH" = "$slugged"
  RESUME_FILE=""

  out=$(TASK="never ran" manifest_discover 2>&1); rc=$?
  check "manifest_discover --resume: nothing unfinished matches → an error naming the repo" \
    bash -c '[[ "$1" != 0 && "$2" == *"No unfinished run to resume for Acme/api"* ]]' _ "$rc" "$out"
)

# ── Restore ───────────────────────────────────────────────────────────────────
(
  mf_env
  # A run interrupted in implement whose scratchpad is gone: only the vault has it.
  f=$(mf_manifest add-thing 00-build vault --set "args.task=Add thing" --set "args.task_type=bug" \
        --set "args.jira_ticket=" --add "args.attach=/notes/a.md" --set-json "args.staged=true" \
        --set "repo.branch=bb-1" --set "repo.base_branch=develop" --set "layout.usage_ledger=/logs/x-usage.jsonl" \
        --set "phases.plan.status=done" --set-json "phases.plan.gate_attempts=2" --set "phases.tdd.status=skipped" \
        --set "phases.implement.status=running" --set-json "phases.implement.stage_num=1" \
        --set-json "phases.heal.heal_attempt=1" --set "status=failed")
  v="$mf_dir/vault/Acme/api/add-thing"
  printf '# Plan\n' > "$v/plan.md"
  printf 'notes\n' > "$v/00-build/implementation-stage-1.md"
  printf 'Title: Add thing\n' > "$v/00-build/intake.md"
  printf '# Knowledge\n\n## 2026-01-01 — older-task\n- remember this\n' > "$mf_dir/vault/Acme/api/knowledge.md"

  TASK_TYPE="feature"
  manifest_restore "$f" > /dev/null
  check "manifest_restore: arguments the command line left out come from run.json" \
    test "$TASK|${ATTACH_FILES[0]}|$STAGED" = "Add thing|/notes/a.md|true"
  check "manifest_restore: an argument the command line gave wins" test "$TASK_TYPE" = "feature"
  check "manifest_restore: paths re-derived by set_run_paths from the slug and the config" \
    test "$SCRATCHPAD_DIR|$TASK_DIR|$RUN_SLUG|$PLAN_FILE" = "$mf_repo/scratchpad/add-thing|$v|00-build|$mf_repo/scratchpad/add-thing/plan.md"
  check "manifest_restore: documents come back from the vault mirror" \
    test -f "$PLAN_FILE" -a -f "$RUN_SCRATCH_DIR/implementation-stage-1.md" -a -f "$RUN_SCRATCH_DIR/intake.md"
  check "manifest_restore: the vault manifest becomes the run's own scratchpad copy" test -f "$RUN_SCRATCH_DIR/run.json"
  check "manifest_restore: a failed run is in progress again" test "$(json_field "$RUN_SCRATCH_DIR/run.json" status)" = "in_progress"
  check "manifest_restore: the gate and heal counters and the stage reached" \
    test "$PLAN_GATE_ATTEMPTS|$HEAL_ATTEMPT|$RESUME_STAGE_NUM" = "2|1|1"
  check "manifest_restore: base branch, usage ledger and the knowledge injection" \
    bash -c '[[ "$1|$2" == "develop|/logs/x-usage.jsonl" && "$3" == *"remember this"* ]]' _ "$BASE_BRANCH" "$MIGITE_USAGE_LEDGER" "$KNOWLEDGE_INJECT"
  check "manifest_restore: checks out the branch the run was on" \
    test "$(git -C "$mf_repo" branch --show-current)|$BRANCH" = "bb-1|bb-1"
  check "manifest_restore: MANIFEST_RESUMED" test "$MANIFEST_RESUMED" = "true"
  git -C "$mf_repo" checkout -q main
)
(
  mf_env
  f=$(mf_manifest done-task 00-build scratch --set "args.task=Done task" --set "repo.branch=bb-1")
  runstate_cmd update --file "$f" $(for p in plan tdd implement heal review deliver; do printf -- '--set phases.%s.status=done ' "$p"; done) >/dev/null
  manifest_restore "$f"
  check "manifest_restore: a finished run sets MANIFEST_COMPLETE and restores nothing else" \
    test "$MANIFEST_COMPLETE|$MANIFEST_RESUMED|$TASK|$RUN_SLUG" = "true|false||00-build"
  check "manifest_restore: a finished run doesn't switch branches" test "$(git -C "$mf_repo" branch --show-current)" = "main"
)
(
  mf_env
  f=$(mf_manifest other 00-build scratch)
  runstate_cmd update --file "$f" --set repo.name=web >/dev/null
  out=$(manifest_restore "$f" 2>&1); rc=$?
  check "manifest_restore: a manifest from another repo is refused" \
    bash -c '[[ "$1" != 0 && "$2" == *"run of Acme/web, not Acme/api"* ]]' _ "$rc" "$out"
  printf '{"broken' > "$f"
  out=$(manifest_restore "$f" 2>&1); rc=$?
  check "manifest_restore: an unreadable manifest is an error that says how to start over" \
    bash -c '[[ "$1" != 0 && "$2" == *"Delete it to start the task over"* ]]' _ "$rc" "$out"
)
(
  mf_env
  mkdir -p "$mf_repo/scratchpad/bb-9/00-build"
  f=$(mf_manifest bb-9 01-amend-shorter-delay scratch --set "args.amend.num=01" --set "args.amend.feedback=shorter delay" \
        --set "phases.plan.status=pending_gate")
  out=$(manifest_restore "$f" 2>&1); rc=$?
  check "manifest_restore: an amend run stopped before its amendment existed can't resume" \
    bash -c '[[ "$1" != 0 && "$2" == *"Amendment 01 was never written"* ]]' _ "$rc" "$out"
  printf '# Amendment 01\n' > "$mf_dir/vault/Acme/api/bb-9/01-amend-shorter-delay/amendment.md"
  manifest_restore "$f" > /dev/null
  check "manifest_restore: an amend run at its gate comes back with its number and amendment" \
    test "$AMEND_MODE|$AMEND_NUM|$AMENDMENT_FILE|$INTAKE_FILE" = "true|01|$RUN_SCRATCH_DIR/amendment.md|$SCRATCHPAD_DIR/00-build/intake.md"
  check "manifest_restore: the amendment is pulled back from the vault" test -s "$AMENDMENT_FILE"
)

# ── Staged implement: resumes after the last stage run.json recorded ─────────
# run_implement with run_phase stubbed: each "session" writes its stage notes.
# mf_staged_env - a staged run whose plan has three Scope layers, stage 1 done.
mf_staged_env() {
  mf_env
  # shellcheck source=../lib/phases/implement.sh
  source "$MIGITE_HOME/lib/phases/implement.sh"
  SCRATCHPAD_DIR="$mf_repo/scratchpad/staged-$1"; TASK_DIR="$mf_dir/vault/Acme/api/staged-$1"
  set_run_paths "$BUILD_RUN_SLUG"
  manifest_init
  printf '# Plan\n\n## Scope\n### Models\n### Services\n### Controllers\n\n## Risks\n' > "$PLAN_FILE"
  printf 'models built\n' > "$RUN_SCRATCH_DIR/implementation-stage-1.md"
  printf 'implement [PLAN_PATH]\n' > "$mf_dir/implement.md"
  STAGED=true; KNOWLEDGE_INJECT=""; IMPLEMENT_CMD_PATH="$mf_dir/implement.md"; RESUME_STAGE_NUM=1
  STAGES_RUN="$mf_dir/stages-$1.log"; BANNERS="$mf_dir/banners-$1.log"; : > "$STAGES_RUN"; : > "$BANNERS"
  run_phase() { echo "$1" >> "$STAGES_RUN"; printf 'built: %s\n' "$1" > "$2"; }
  read_gate_choice() { echo "$1" >> "$BANNERS"; GATE_CHOICE="${GATE_ANSWERS[0]:-c}"; GATE_ANSWERS=("${GATE_ANSWERS[@]:1}"); }
  notify() { :; }
}
(
  mf_staged_env crash
  MANIFEST_ENTRY_STATUS="running"; GATE_ANSWERS=(r c)
  run_implement > /dev/null
  f="$RUN_SCRATCH_DIR/run.json"
  check "staged resume: starts after the stage run.json recorded; r re-runs the same stage" \
    test "$(tr '\n' '|' < "$STAGES_RUN")" = "Stage 2 — Services|Stage 2 — Services|Stage 3 — Controllers|"
  check "staged resume: the notes cover every stage, the resumed one from its file" \
    bash -c '[[ "$(cat "$1")" == *"### Stage 1: Models"*"models built"*"### Stage 2: Services"*"### Stage 3: Controllers"* ]]' _ "$IMPLEMENTATION_FILE"
  check "staged resume: run.json records the stages and how many finished" \
    test "$(json_field "$f" phases.implement.stage_num)|$(json_field "$f" phases.implement.stage_count)|$(json_field "$f" phases.implement.stage_labels.2)" = "3|3|Controllers"
)
(
  mf_staged_env gate
  MANIFEST_ENTRY_STATUS="pending_gate"; GATE_ANSWERS=(q)
  out=$(run_implement 2>&1); rc=$?
  f="$RUN_SCRATCH_DIR/run.json"
  check "staged resume at a checkpoint: re-opens stage 1's checkpoint before running anything" \
    test "$(head -1 "$BANNERS")|$(wc -l < "$STAGES_RUN" | tr -d ' ')" = "STAGE CHECKPOINT: 1/3 — Models|0"
  check "staged resume at a checkpoint: q exits 0 and records the checkpoint as pending again" \
    test "$rc|$(json_field "$f" phases.implement.status)|$(json_field "$f" phases.implement.stage_num)" = "0|pending_gate|1"
)

# ── Commit gate: q records the reviewed tree; resume reuses an unchanged review ─
# run_review on the generic stack with migite-review stubbed (each "review"
# writes review.md and is counted), answering the commit gate from a queue.
(
  mf_env
  # shellcheck source=../lib/phases/review.sh
  source "$MIGITE_HOME/lib/phases/review.sh"
  TASK_SLUG="gate-task"; SCRATCHPAD_DIR="$mf_repo/scratchpad/gate-task"; TASK_DIR="$mf_dir/vault/Acme/api/gate-task"
  set_run_paths "$BUILD_RUN_SLUG"
  manifest_init
  printf '# Plan\n' > "$PLAN_FILE"; printf 'notes\n' > "$IMPLEMENTATION_FILE"
  STACK=generic; BASE_BRANCH=main; APP_ROOT="$mf_repo"; KNOWLEDGE_INJECT=""
  export MIGITE_CFG_FRONTEND_BROWSER_CHECK=off MIGITE_CFG_UI_NOTIFY=off
  REVIEWS="$mf_dir/reviews.log"; : > "$REVIEWS"
  spawn_langgraph() { echo "$1" >> "$REVIEWS"; printf '# Review\n## Verdict: READY TO COMMIT\n' > "$REVIEW_FILE"; touch "$SCRATCHPAD_DIR/.review.done"; }
  read_gate_choice() { echo "$1" >> "$mf_dir/gate-banners.log"; GATE_CHOICE="${GATE_ANSWERS[0]:-q}"; GATE_ANSWERS=("${GATE_ANSWERS[@]:1}"); }
  run_review_as() {   # run_review_as <entry status> <stdin> <gate answers...> - one invocation
    local entry="$1" input="$2"; shift 2
    ( MANIFEST_ENTRY_STATUS="$entry"; GATE_ANSWERS=("$@")
      RESUME_REVIEW_FINGERPRINT="$(json_field "$RUN_SCRATCH_DIR/run.json" phases.review.tree_fingerprint || true)"
      printf '%s' "$input" | run_review > /dev/null 2>&1 )
  }
  printf 'v1\n' > "$mf_repo/app.rb"

  run_review_as "" "" q
  f="$RUN_SCRATCH_DIR/run.json"
  check "commit gate q: run.json records the gate as pending and the tree the review saw" \
    test "$(json_field "$f" phases.review.status)|$(json_field "$f" phases.review.tree_fingerprint)" = "pending_gate|$(tree_fingerprint main)"
  run_review_as pending_gate "" q
  check "commit gate resume: unchanged code reuses review.md instead of reviewing again" \
    test "$(wc -l < "$REVIEWS" | tr -d ' ')" = "1"

  # n (fix it yourself), edit, then q at the "ready?" prompt: those edits were never reviewed.
  ( MANIFEST_ENTRY_STATUS=pending_gate
    RESUME_REVIEW_FINGERPRINT="$(json_field "$f" phases.review.tree_fingerprint)"
    read_gate_choice() { GATE_CHOICE=n; printf 'v2\n' > "$mf_repo/app.rb"; }
    printf 'q\n' | run_review > /dev/null 2>&1 )
  run_review_as pending_gate "" q
  check "commit gate resume: edits made after n and before q are reviewed, not skipped" \
    test "$(wc -l < "$REVIEWS" | tr -d ' ')" = "2"
  rm -f "$mf_repo/app.rb"
)

unset -f mf_env mf_manifest mf_staged_env
unset mf_dir mf_repo
