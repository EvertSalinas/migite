# tests/testing_plan_test.sh - plan.testing_plan_when: review. ensure_testing_plan
# (lib/phases/review.sh, Phase 3 writes testing-plan.md from the plan and the diff) and
# defer_testing_plan (lib/phases/plan.sh, a leftover testing plan from an earlier plan is
# set aside), through migite.testing_plan. Uses tests/fake-claude on PATH, so no real
# model call is made; a throwaway git repo gives it a tracked change and an untracked file.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping testing plan checks"
  return 0 2>/dev/null || exit 0
fi
# shellcheck source=../lib/phases/plan.sh
source "$REPO_ROOT/lib/phases/plan.sh"
# shellcheck source=../lib/phases/review.sh
source "$REPO_ROOT/lib/phases/review.sh"

tp_dir=$(mktemp -d); CLEANUP_DIRS+=("$tp_dir")
_tp_saved_agent="${MIGITE_AGENT:-}" _tp_saved_xdg="${XDG_CONFIG_HOME:-}" _tp_saved_path="$PATH"
export MIGITE_AGENT=claude XDG_CONFIG_HOME="$tp_dir/config"
mkdir -p "$XDG_CONFIG_HOME" "$tp_dir/bin"
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$tp_dir/bin/claude"
PATH="$tp_dir/bin:$PATH"

tp_repo=$(make_fixture_repo)
mkdir -p "$tp_repo/app"
echo 'class Item; def name; "a"; end; end' > "$tp_repo/app/item.rb"
git -C "$tp_repo" add -A
git -C "$tp_repo" commit -q -m init
echo 'class Item; def name; "renamed"; end; end' > "$tp_repo/app/item.rb"   # tracked change
echo 'class Brand; end' > "$tp_repo/app/brand.rb"                          # untracked new file

# tp_setup <case> - a fresh scratchpad and vault with a plan.md, and a ledger of its own.
# shellcheck disable=SC2034  # the globals below are read by ensure_testing_plan / defer_testing_plan
tp_setup() {
  SCRATCHPAD_DIR="$tp_dir/s-$1"; TASK_DIR="$tp_dir/v-$1"
  set_run_paths "00-build"
  REPO_ROOT="$tp_repo" BASE_BRANCH=main
  printf '# BB-9: Rename items\n\n## Approach\nRename Item#name.\n' > "$PLAN_FILE"
  MIGITE_USAGE_LEDGER="$tp_dir/ledger-$1.jsonl"
  : > "$MIGITE_USAGE_LEDGER"
  export MIGITE_USAGE_LEDGER
  unset MIGITE_CFG_PLAN_TESTING_PLAN_WHEN CHANGED_FRONTEND FAKE_CLAUDE_MODE
}

# ── plan timing (the default): Phase 3 leaves the testing plan alone ─────────
(
  tp_setup plan
  ( cd "$tp_repo" && ensure_testing_plan ) > "$tp_dir/out-plan.txt" 2>&1
  check "ensure_testing_plan: plan.testing_plan_when: plan writes nothing" \
    test ! -e "$TESTING_PLAN_FILE"
  check "ensure_testing_plan: plan.testing_plan_when: plan makes no model call" \
    test ! -s "$MIGITE_USAGE_LEDGER"
)

# ── review timing: written from the plan and the diff ────────────────────────
(
  tp_setup review
  export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review FAKE_CLAUDE_PROMPT="$tp_dir/prompt-review.txt"
  ( cd "$tp_repo" && ensure_testing_plan ) > "$tp_dir/out-review.txt" 2>&1
  check "ensure_testing_plan: review timing writes testing-plan.md" \
    bash -c 'grep -q "^echo:" "$1"' _ "$TESTING_PLAN_FILE"
  check "ensure_testing_plan: and its vault copy" \
    bash -c 'grep -q "^echo:" "$1"' _ "$TESTING_PLAN_VAULT"
  check "ensure_testing_plan: one model call, labelled generate_testing_plan, tool migite-plan" \
    bash -c '[[ "$(wc -l < "$1" | tr -d " ")" == 1 ]] && grep -q "\"label\": \"generate_testing_plan\"" "$1" && grep -q "\"tool\": \"migite-plan\"" "$1"' _ "$MIGITE_USAGE_LEDGER"
  check "ensure_testing_plan: the call runs on the standard tier (claude-sonnet-5), not the strong one" \
    grep -q '"model": "claude-sonnet-5"' "$MIGITE_USAGE_LEDGER"
  check "ensure_testing_plan: the prompt carries the plan" \
    grep -q "Rename Item#name" "$FAKE_CLAUDE_PROMPT"
  check "ensure_testing_plan: the prompt carries the diff of the tracked change" \
    grep -qF 'def name; "renamed"' "$FAKE_CLAUDE_PROMPT"
  check "ensure_testing_plan: the prompt names the untracked new file the diff leaves out" \
    bash -c 'sed -n "/^Files changed/,\$p" "$1" | grep -qx "app/brand.rb"' _ "$FAKE_CLAUDE_PROMPT"
  check "ensure_testing_plan: the prompt says the diff wins over the plan" \
    grep -q "the diff is right" "$FAKE_CLAUDE_PROMPT"
  check "ensure_testing_plan: a backend-only change gets no browser steps" \
    bash -c '! grep -q "without a full page reload" "$1"' _ "$FAKE_CLAUDE_PROMPT"
)

# ── a change that touches views or JavaScript asks for browser-ready steps ───
(
  tp_setup frontend
  export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review FAKE_CLAUDE_PROMPT="$tp_dir/prompt-frontend.txt"
  CHANGED_FRONTEND="app/views/items/index.html.erb"
  ( cd "$tp_repo" && ensure_testing_plan ) > "$tp_dir/out-frontend.txt" 2>&1
  check "ensure_testing_plan: CHANGED_FRONTEND asks for browser-ready steps" \
    grep -q "without a full page reload" "$FAKE_CLAUDE_PROMPT"
)

# ── an existing testing plan is never written over ───────────────────────────
(
  tp_setup keep
  export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review
  printf 'hand edited in a fix round\n' > "$TESTING_PLAN_FILE"
  ( cd "$tp_repo" && ensure_testing_plan ) > "$tp_dir/out-keep.txt" 2>&1
  check "ensure_testing_plan: an existing testing-plan.md is kept as it is" \
    bash -c '[[ "$(cat "$1")" == "hand edited in a fix round" ]]' _ "$TESTING_PLAN_FILE"
  check "ensure_testing_plan: and costs no model call" \
    test ! -s "$MIGITE_USAGE_LEDGER"
)

# ── no plan.md to write it from ──────────────────────────────────────────────
(
  tp_setup noplan
  export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review
  rm -f "$PLAN_FILE"
  ( cd "$tp_repo" && ensure_testing_plan ) > "$tp_dir/out-noplan.txt" 2>&1
  check "ensure_testing_plan: no plan.md warns, writes nothing and makes no call" \
    bash -c 'grep -q "no plan.md" "$1" && [[ ! -e "$2" && ! -s "$3" ]]' _ "$tp_dir/out-noplan.txt" "$TESTING_PLAN_FILE" "$MIGITE_USAGE_LEDGER"
)

# ── a failed or empty call degrades the run instead of ending it ─────────────
(
  for _tp_mode in exit1 empty is_error; do
    tp_setup "fail-$_tp_mode"
    export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review FAKE_CLAUDE_MODE="$_tp_mode"
    out=$( (set -euo pipefail; cd "$tp_repo"; ensure_testing_plan; echo completed) 2>&1 )
    check "ensure_testing_plan: a '$_tp_mode' reply warns and completes under set -euo pipefail" \
      bash -c '[[ "$1" == *"could not be written"* && "$1" == *completed* ]]' _ "$out"
    check "ensure_testing_plan: a '$_tp_mode' reply leaves no testing-plan.md behind" \
      bash -c '[[ ! -e "$1" && ! -e "$2" ]]' _ "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
  done
)

# ── defer_testing_plan: a leftover from an earlier plan is set aside ─────────
(
  tp_setup defer-plan
  printf 'old testing plan\n' > "$TESTING_PLAN_FILE"
  defer_testing_plan > /dev/null 2>&1
  check "defer_testing_plan: plan timing leaves testing-plan.md where it is" \
    bash -c '[[ "$(cat "$1")" == "old testing plan" ]]' _ "$TESTING_PLAN_FILE"
)
(
  tp_setup defer-review
  export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review
  printf 'old testing plan\n' > "$TESTING_PLAN_FILE"
  printf 'old testing plan\n' > "$TESTING_PLAN_VAULT"
  defer_testing_plan > "$tp_dir/out-defer.txt" 2>&1
  check "defer_testing_plan: review timing removes the scratchpad and the vault copy" \
    bash -c '[[ ! -e "$1" && ! -e "$2" ]]' _ "$TESTING_PLAN_FILE" "$TESTING_PLAN_VAULT"
  check "defer_testing_plan: after setting a copy aside in .plan-history, and saying so" \
    bash -c '[[ "$(find "$1/.plan-history" -name "testing-plan-*.md" | wc -l | tr -d " ")" == 1 ]] && grep -q "old testing plan" "$1"/.plan-history/testing-plan-*.md && grep -q "Setting aside" "$2"' _ "$SCRATCHPAD_DIR" "$tp_dir/out-defer.txt"
)
(
  tp_setup defer-none
  export MIGITE_CFG_PLAN_TESTING_PLAN_WHEN=review
  out=$( (set -euo pipefail; defer_testing_plan; echo completed) 2>&1 )
  check "defer_testing_plan: nothing to set aside completes under set -euo pipefail, with no history folder" \
    bash -c '[[ "$1" == "completed" && ! -e "$2/.plan-history" ]]' _ "$out" "$SCRATCHPAD_DIR"
)

unset -f tp_setup
PATH="$_tp_saved_path"
if [[ -n "$_tp_saved_agent" ]]; then export MIGITE_AGENT="$_tp_saved_agent"; else unset MIGITE_AGENT; fi
if [[ -n "$_tp_saved_xdg" ]]; then export XDG_CONFIG_HOME="$_tp_saved_xdg"; else unset XDG_CONFIG_HOME; fi
