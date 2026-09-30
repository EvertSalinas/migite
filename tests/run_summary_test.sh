# tests/run_summary_test.sh - lib/phases/deliver.sh:write_run_summary and the
# index.md row it feeds.
#
# summary.md is the per-run record for people: the model writes the narrative
# from this run's own files, bash writes the title, date and run facts. Uses
# tests/fake-claude on PATH, so no real model call is made.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping run summary checks"
  return 0 2>/dev/null || exit 0
fi
# shellcheck source=../lib/phases/deliver.sh
source "$REPO_ROOT/lib/phases/deliver.sh"

rs_dir=$(mktemp -d); CLEANUP_DIRS+=("$rs_dir")
_rs_saved_agent="${MIGITE_AGENT:-}" _rs_saved_xdg="${XDG_CONFIG_HOME:-}" _rs_saved_path="$PATH"
export MIGITE_AGENT=claude XDG_CONFIG_HOME="$rs_dir/config"
mkdir -p "$XDG_CONFIG_HOME" "$rs_dir/bin"
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$rs_dir/bin/claude"
PATH="$rs_dir/bin:$PATH"

# An amend run with two fix rounds and a NEEDS FIXES review.
# shellcheck disable=SC2034  # the globals below are read by write_run_summary
(
  SCRATCHPAD_DIR="$rs_dir/scratch/bb-1"; TASK_DIR="$rs_dir/vault/bb-1"
  set_run_paths "01-amend-log-the-value"
  AMEND_MODE=true AMENDMENT_FILE="$RUN_SCRATCH_DIR/amendment.md" BASE_BRANCH="no-such-branch"
  COMMIT_GATE_ATTEMPTS=2 HEAL_ATTEMPT=1 PLAN_GATE_ATTEMPTS=3
  printf '# Amendment 01\n## Feedback\nLog the rejected go-live value.\n' > "$AMENDMENT_FILE"
  echo "Added the warn line in message_read.rb" > "$IMPLEMENTATION_FILE"
  echo "round two body" > "$RUN_SCRATCH_DIR/fix-r2.md"
  echo "round one body" > "$RUN_VAULT_DIR/fix-r1.md"
  printf '# Review\n\n## Verdict: NEEDS FIXES\n' > "$REVIEW_FILE"

  FAKE_CLAUDE_MODE=envelope write_run_summary "the fallback date was picked by product" >/dev/null 2>&1
  summary="$RUN_SCRATCH_DIR/summary.md"
  prompt=$(cat "$LOG_DIR"/*-prompt-Writing-run-summary.txt 2>/dev/null)

  check "write_run_summary: writes summary.md in the run folder and mirrors it" \
    test -s "$summary" -a -s "$RUN_VAULT_DIR/summary.md"
  check "write_run_summary: bash writes the title and date (under sync_artifact's frontmatter)" \
    bash -c 'grep -qx "# Run summary: 01-amend-log-the-value" "$1" && grep -q "^Date: " "$1"' _ "$summary"
  check "write_run_summary: the model's narrative is kept" \
    grep -q "echo:You are writing the end-of-run summary" "$summary"
  check "write_run_summary: run facts count both fix rounds and read the verdict" \
    bash -c 'grep -q "^- Fix rounds: 2$" "$1" && grep -q "^- Review verdict: needs_fixes$" "$1" && grep -q "^- Auto-heal attempts: 1$" "$1"' _ "$summary"
  check "write_run_summary: an amend run has no plan-gate fact" \
    not grep -q "Plan gate rounds" "$summary"
  check "write_run_summary: the prompt carries the amendment, not the plan" \
    bash -c '[[ "$1" == *"Amendment this run implemented"*"Log the rejected go-live value."* ]]' _ "$prompt"
  check "write_run_summary: fix rounds reach the prompt in number order, from either copy" \
    bash -c '[[ "$1" == *"round one body"*"round two body"* ]]' _ "$prompt"
  check "write_run_summary: the engineer's note reaches the prompt" \
    bash -c '[[ "$1" == *"the fallback date was picked by product"* ]]' _ "$prompt"

  rm -f "$summary" "$RUN_VAULT_DIR/summary.md"
  out=$(FAKE_CLAUDE_MODE=exit1 write_run_summary "" 2>&1); rc=$?
  check "write_run_summary: a failed call leaves no summary.md and doesn't fail the run" \
    bash -c '[[ "$1" == 0 && ! -e "$2" && "$3" == *"came back empty"* ]]' _ "$rc" "$summary" "$out"
)

# A build run: the plan is the purpose, and the plan gate is a fact.
# shellcheck disable=SC2034  # the globals below are read by write_run_summary
(
  SCRATCHPAD_DIR="$rs_dir/scratch/bb-2"; TASK_DIR="$rs_dir/vault/bb-2"
  set_run_paths "00-build"
  AMEND_MODE=false BASE_BRANCH="no-such-branch" PLAN_GATE_ATTEMPTS=3
  printf '# BB-2: Export\n## Summary\nExport invoices as PDF.\n' > "$PLAN_FILE"
  rm -f "$LOG_DIR"/*-prompt-Writing-run-summary.txt
  FAKE_CLAUDE_MODE=envelope write_run_summary "" >/dev/null 2>&1
  prompt=$(cat "$LOG_DIR"/*-prompt-Writing-run-summary.txt 2>/dev/null)
  check "write_run_summary: a build run's prompt carries the plan" \
    bash -c '[[ "$1" == *"Plan this run implemented"*"Export invoices as PDF."* ]]' _ "$prompt"
  check "write_run_summary: a build run records its plan-gate rounds" \
    grep -q "^- Plan gate rounds: 3$" "$RUN_SCRATCH_DIR/summary.md"

  printf '# Run summary: 00-build\nDate: 2026-09-30\n\nSummary: Added PDF export for invoices.\n' > "$RUN_VAULT_DIR/summary.md"
  write_task_index "$TASK_DIR"
  check "write_task_index: a run with a summary shows its Summary line" \
    grep -qF "| 00-build | Added PDF export for invoices. |" "$TASK_DIR/index.md"
)

PATH="$_rs_saved_path"
if [[ -n "$_rs_saved_agent" ]]; then export MIGITE_AGENT="$_rs_saved_agent"; else unset MIGITE_AGENT; fi
if [[ -n "$_rs_saved_xdg" ]]; then export XDG_CONFIG_HOME="$_rs_saved_xdg"; else unset XDG_CONFIG_HOME; fi
unset rs_dir _rs_saved_agent _rs_saved_xdg _rs_saved_path
