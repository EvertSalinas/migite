# tests/living_plan_test.sh - the living plan: plan_folded_runs,
# unfolded_run_files, snapshot_approved_plan (lib/vault.sh) and
# fold_run_into_plan (lib/phases/deliver.sh, Phase 3.8).
#
# plan.md is kept current at the end of every run by exact edits the engineer
# approves, and its "## Revision history" records which runs it reflects: those
# runs' amendments drop out of later prompts. 00-build/plan.md keeps the plan as
# approved. Uses tests/fake-claude on PATH, so no real model call is made.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping living plan checks"
  return 0 2>/dev/null || exit 0
fi
# shellcheck source=../lib/phases/deliver.sh
source "$REPO_ROOT/lib/phases/deliver.sh"

lp_dir=$(mktemp -d); CLEANUP_DIRS+=("$lp_dir")
_lp_saved_agent="${MIGITE_AGENT:-}" _lp_saved_xdg="${XDG_CONFIG_HOME:-}" _lp_saved_path="$PATH"
export MIGITE_AGENT=claude XDG_CONFIG_HOME="$lp_dir/config"
mkdir -p "$XDG_CONFIG_HOME" "$lp_dir/bin"
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$lp_dir/bin/claude"
PATH="$lp_dir/bin:$PATH"

_lp_plan='# BB-1: Record message reads

## Approach
ENQUEUE_DELAY is 1 minute.
'

# ── Which runs the plan reflects ─────────────────────────────────────────────
(
  s="$lp_dir/s1"; v="$lp_dir/v1"
  mkdir -p "$s/01-amend-a" "$v/02-amend-b" "$s/03-amend-c"
  for f in "$s/01-amend-a" "$v/02-amend-b" "$s/03-amend-c"; do echo "amendment" > "$f/amendment.md"; done
  printf '%s\n## Revision history\n\n- 2026-09-01 `00-build`: No plan changes.\n- 2026-09-02 `02-amend-b`: B.\n' "$_lp_plan" > "$s/plan.md"
  check "plan_folded_runs: the run slugs in the revision history, in order" \
    test "$(plan_folded_runs "$s/plan.md" | tr '\n' ' ')" = "00-build 02-amend-b "
  check "plan_folded_runs: a plan without a revision history reflects no runs" \
    test -z "$(printf '%s' "$_lp_plan" > "$lp_dir/bare.md"; plan_folded_runs "$lp_dir/bare.md")"
  check "unfolded_run_files: leaves out the amendments the plan already reflects" \
    test "$(unfolded_run_files "$s" "$v" amendment.md "$s/plan.md" | tr '\n' ' ')" = "$s/01-amend-a/amendment.md $s/03-amend-c/amendment.md "
  check "unfolded_run_files: a plan from before the living plan leaves every amendment in" \
    test "$(unfolded_run_files "$s" "$v" amendment.md "$lp_dir/bare.md" | wc -l | tr -d ' ')" = "3"
)

# ── 00-build/plan.md ─────────────────────────────────────────────────────────
# shellcheck disable=SC2034  # the globals below are read by snapshot_approved_plan
(
  SCRATCHPAD_DIR="$lp_dir/s2"; TASK_DIR="$lp_dir/v2"
  set_run_paths "00-build"
  printf '%s' "$_lp_plan" > "$PLAN_FILE"
  snapshot_approved_plan
  check "snapshot_approved_plan: copies the approved plan into 00-build/, both copies" \
    bash -c 'grep -q "ENQUEUE_DELAY is 1 minute" "$1" && test -f "$2"' _ "$RUN_SCRATCH_DIR/plan.md" "$RUN_VAULT_DIR/plan.md"
  printf '%s\n## Revision history\n\n- d `00-build`: Changed.\n' "${_lp_plan/1 minute/15 seconds}" > "$PLAN_FILE"
  snapshot_approved_plan
  check "snapshot_approved_plan: a plan with folded runs never overwrites the snapshot" \
    grep -q "ENQUEUE_DELAY is 1 minute" "$RUN_SCRATCH_DIR/plan.md"
  printf '%s' "${_lp_plan/1 minute/2 minutes}" > "$PLAN_FILE"
  mkdir -p "$TASK_DIR/01-amend-x"
  snapshot_approved_plan
  check "snapshot_approved_plan: once an amendment exists the snapshot is left alone" \
    grep -q "ENQUEUE_DELAY is 1 minute" "$RUN_SCRATCH_DIR/plan.md"
)

# ── fold_run_into_plan ───────────────────────────────────────────────────────
# lp_fold <mode> <gate-answer> - a fresh amend run, then Phase 3.8 with the fake agent.
# shellcheck disable=SC2034  # the globals it sets are read by fold_run_into_plan
lp_fold() {
  SCRATCHPAD_DIR="$lp_dir/s3-$1-$2"; TASK_DIR="$lp_dir/v3-$1-$2"
  set_run_paths "01-amend-reduce-delay"
  AMEND_MODE=true AMENDMENT_FILE="$RUN_SCRATCH_DIR/amendment.md" DATE="2026-09-30"
  printf '%s' "$_lp_plan" > "$PLAN_FILE"
  echo "Reduce the delay to 15 seconds." > "$AMENDMENT_FILE"
  echo "notes" > "$IMPLEMENTATION_FILE"
  # A here-string, not a pipe: a pipe would run the fold in a subshell and lose PLAN_FOLD_RESULT.
  FAKE_CLAUDE_MODE="$1" fold_run_into_plan <<< "$2" > "$lp_dir/out-$1-$2.txt" 2>&1
}
# shellcheck disable=SC2034  # the globals below are read by fold_run_into_plan
(
  lp_fold fold y
  check "fold_run_into_plan: an approved fold updates plan.md" \
    grep -q "ENQUEUE_DELAY is 15 seconds." "$PLAN_FILE"
  check "fold_run_into_plan: and records the run with the model's revision line" \
    grep -qF -- '- 2026-09-30 `01-amend-reduce-delay`: Delay is 15 seconds, not 1 minute.' "$PLAN_FILE"
  check "fold_run_into_plan: the vault mirror and a .plan-history backup are written" \
    bash -c 'grep -q "15 seconds" "$1" && ls "$2"/.plan-history/plan-*.md >/dev/null' _ "$PLAN_VAULT" "$SCRATCHPAD_DIR"
  check "fold_run_into_plan: the diff is shown before asking" \
    grep -q "Proposed plan changes" "$lp_dir/out-fold-y.txt"
  check "fold_run_into_plan: an updated plan is reported to the run summary" \
    test "$PLAN_FOLD_RESULT" = "updated"
  check "fold_run_into_plan: the folded amendment drops out of later prompts" \
    test -z "$(unfolded_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" amendment.md "$PLAN_FILE")"
  FAKE_CLAUDE_MODE="fold" fold_run_into_plan <<< "y" >/dev/null 2>&1
  check "fold_run_into_plan: a run the plan already reflects is not folded twice" \
    bash -c '[[ "$1" == "already reflected" && $(grep -c "01-amend-reduce-delay" "$2") == 1 ]]' _ "$PLAN_FOLD_RESULT" "$PLAN_FILE"
)
# shellcheck disable=SC2034
(
  lp_fold fold n
  check "fold_run_into_plan: a declined fold leaves plan.md exactly as it was" \
    test "$(cat "$PLAN_FILE")" = "$(printf '%s' "$_lp_plan")"
  check "fold_run_into_plan: and the amendment stays in later prompts" \
    test "$(unfolded_run_files "$SCRATCHPAD_DIR" "$TASK_DIR" amendment.md "$PLAN_FILE")" = "$AMENDMENT_FILE"
  check "fold_run_into_plan: a declined fold is reported as skipped" \
    bash -c '[[ "$1" == skipped* ]]' _ "$PLAN_FOLD_RESULT"
)
# shellcheck disable=SC2034
(
  lp_fold fold_noop ""
  check "fold_run_into_plan: no edits needed is applied without asking, body untouched" \
    bash -c 'grep -q "ENQUEUE_DELAY is 1 minute." "$1" && ! grep -q "PLAN UPDATE" "$2"' _ "$PLAN_FILE" "$lp_dir/out-fold_noop-.txt"
  check "fold_run_into_plan: and still records the run, so its amendment drops out" \
    grep -qF -- '- 2026-09-30 `01-amend-reduce-delay`: No plan changes.' "$PLAN_FILE"
)
# migite runs under set -euo pipefail: every fold outcome must get through it.
# shellcheck disable=SC2034
(
  for _lp_case in "fold y" "fold e" "fold n" "fold_noop -" "exit1 -"; do
    read -r _lp_mode _lp_answer <<< "$_lp_case"
    out=$( (set -euo pipefail; EDITOR=true lp_fold "$_lp_mode" "$_lp_answer"; echo completed) 2>&1 )
    check "fold_run_into_plan: '$_lp_case' completes under set -euo pipefail" \
      bash -c '[[ "$1" == *completed* ]]' _ "$out"
  done
)
# shellcheck disable=SC2034
(
  lp_fold exit1 ""
  check "fold_run_into_plan: a failed call leaves plan.md alone and says so" \
    bash -c '[[ "$(cat "$1")" == "$2" && "$3" == failed* ]]' _ "$PLAN_FILE" "$(printf '%s' "$_lp_plan")" "$PLAN_FOLD_RESULT"
)

unset -f lp_fold
PATH="$_lp_saved_path"
if [[ -n "$_lp_saved_agent" ]]; then export MIGITE_AGENT="$_lp_saved_agent"; else unset MIGITE_AGENT; fi
if [[ -n "$_lp_saved_xdg" ]]; then export XDG_CONFIG_HOME="$_lp_saved_xdg"; else unset XDG_CONFIG_HOME; fi
unset lp_dir _lp_plan _lp_saved_agent _lp_saved_xdg _lp_saved_path
