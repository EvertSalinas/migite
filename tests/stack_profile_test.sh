# tests/stack_profile_test.sh - configured stack profiles (stacks.<name>): detection
# (lib/stack.sh detect_stack), the changed files each command gets, a passing,
# failing, skipped and could-not-start run, the heal loop handing a failure to the
# agent (lib/phases/implement.sh), and Phase 3's checks and gate banner
# (lib/phases/review.sh, lib/gate.sh). A fixture node repo with fake lint, fix and
# test commands; tests/fake-claude stands in for the agent, so no model is called.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON - skipping stack profile checks"
  return 0 2>/dev/null || exit 0
fi
# shellcheck source=../lib/phases/implement.sh
source "$REPO_ROOT/lib/phases/implement.sh"
# shellcheck source=../lib/phases/review.sh
source "$REPO_ROOT/lib/phases/review.sh"

sp_dir=$(mktemp -d); CLEANUP_DIRS+=("$sp_dir")
mkdir -p "$sp_dir/bin" "$sp_dir/state" "$sp_dir/home/.config"
_sp_saved_path="$PATH"
SP_STATE="$sp_dir/state"
export SP_STATE
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$sp_dir/bin/claude"
PATH="$sp_dir/bin:$PATH"

# Fake lint, fix and test commands: each records its arguments (one per line) and
# the call order, and exits with $FAKE_LINT_RC / $FAKE_TEST_RC. fake-test fails
# while $SP_STATE/test-fails counts down, so a heal attempt can "fix" it.
cat > "$sp_dir/bin/fake-lint" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$SP_STATE/lint.args"
echo lint >> "$SP_STATE/order"
echo "lint: checked $# file(s)"
[[ "${FAKE_LINT_RC:-0}" == 0 ]] || echo "src/app.js:1:1  error  'x' is never used  no-unused-vars"
exit "${FAKE_LINT_RC:-0}"
EOF
cat > "$sp_dir/bin/fake-fix" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$SP_STATE/fix.args"
echo fix >> "$SP_STATE/order"
EOF
cat > "$sp_dir/bin/fake-test" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$SP_STATE/test.args"
echo "$#" > "$SP_STATE/test.argc"
echo "test: $# file(s)"
for ((i = 0; i < ${FAKE_TEST_NOISE:-0}; i++)); do echo "noise line $i of a long test log"; done
fails=$(cat "$SP_STATE/test-fails" 2>/dev/null || echo 0)
if [[ "$fails" -gt 0 ]]; then
  echo $((fails - 1)) > "$SP_STATE/test-fails"
  echo "FAIL src/app.test.js: widget renders its label"
  exit 1
fi
exit "${FAKE_TEST_RC:-0}"
EOF
chmod +x "$sp_dir/bin/fake-lint" "$sp_dir/bin/fake-fix" "$sp_dir/bin/fake-test"

# The fixture: a committed node app, then a tracked edit, a delete, an untracked
# file with a space in its name, untracked specs at two depths, a README the
# globs don't select, and migite's own scratchpad/ (never a changed file).
sp_repo=$(make_fixture_repo)
mkdir -p "$sp_repo/src"
echo '{"name": "fixture"}' > "$sp_repo/package.json"
echo 'export const a = 1' > "$sp_repo/src/app.js"
echo 'export const b = 2' > "$sp_repo/src/old.js"
echo '# fixture' > "$sp_repo/README.md"
git -C "$sp_repo" add -A
git -C "$sp_repo" commit -q -m init
echo 'export const a = 3' > "$sp_repo/src/app.js"
rm "$sp_repo/src/old.js"
echo 'export const n = 1' > "$sp_repo/src/new file.js"
echo 'test("a", () => {})' > "$sp_repo/src/app.test.js"
echo 'test("top", () => {})' > "$sp_repo/top.test.js"
echo '# fixture, edited' > "$sp_repo/README.md"
mkdir -p "$sp_repo/scratchpad" && echo 'x' > "$sp_repo/scratchpad/notes.js"

# sp_profile - the node profile as load_migite_config exports it, plus the
# globals a phase has by the time it runs checks, and a clean fake state.
# shellcheck disable=SC2034  # MIGITE_CFG_* are read by cfg through indirection
sp_profile() {
  MIGITE_CFG_STACKS="node"
  MIGITE_CFG_STACKS_NODE_DETECT="package.json"
  MIGITE_CFG_STACKS_NODE_SOURCE="*.js"
  MIGITE_CFG_STACKS_NODE_SPECS="**/*.test.js"
  MIGITE_CFG_STACKS_NODE_AUTOFIX="fake-fix {files}"
  MIGITE_CFG_STACKS_NODE_LINT="fake-lint {files}"
  MIGITE_CFG_STACKS_NODE_TEST="fake-test {files}"
  STACK=node APP_ROOT="$sp_repo" APP_REL_PATH="" BASE_BRANCH=main REPO_ROOT="$sp_repo"
  rm -f "$SP_STATE"/*
  cd "$sp_repo" || return 1
}

# ── the config reaches bash ──────────────────────────────────────────────────
cat > "$sp_dir/migite.json" <<'EOF'
{"stacks": {"node": {"detect": ["package.json"], "source": ["*.js", "*.mjs"], "specs": ["**/*.test.js"],
                     "lint": "fake-lint {files}", "test": "fake-test {files}"}}}
EOF
(
  export HOME="$sp_dir/home" XDG_CONFIG_HOME="$sp_dir/home/.config" MIGITE_CONFIG="$sp_dir/migite.json"
  unset MIGITE_STACK
  load_migite_config "$sp_repo" >/dev/null
  check "load_migite_config: a stacks profile reaches bash by name" test "$(cfg stacks)" = "node"
  check "load_migite_config: a profile's globs arrive one per line" test "$(cfg stacks.node.source)" = $'*.js\n*.mjs'
  REPO_ROOT="$sp_repo" STACK_OVERRIDE=""
  detect_stack
  check "detect_stack: a configured profile is picked by its detect file" test "$STACK" = "node"
)

# ── detection order ──────────────────────────────────────────────────────────
mkdir -p "$sp_dir/det/both" "$sp_dir/det/rails" "$sp_dir/det/none" "$sp_dir/det/py" "$sp_dir/det/two"
touch "$sp_dir/det/both/package.json" "$sp_dir/det/both/Gemfile" "$sp_dir/det/rails/Gemfile" \
      "$sp_dir/det/py/setup.py" "$sp_dir/det/two/package.json" "$sp_dir/det/two/pyproject.toml"
# shellcheck disable=SC2034  # MIGITE_CFG_* are read by cfg through indirection
(
  MIGITE_CFG_STACKS="node py bare"
  MIGITE_CFG_STACKS_NODE_DETECT="package.json"
  MIGITE_CFG_STACKS_PY_DETECT=$'pyproject.toml\nsetup.*'
  MIGITE_CFG_STACKS_BARE_TEST="true"
  sp_detect() { REPO_ROOT="$sp_dir/det/$1" STACK_OVERRIDE="${2:-}"; detect_stack; }

  sp_detect both
  check "detect_stack: profiles come before rails (package.json beside a Gemfile is node)" test "$STACK" = "node"
  check "detect_stack: a profile's app root is the repo root" \
    bash -c '[[ "$1" == "$2" && -z "$3" ]]' _ "$APP_ROOT" "$sp_dir/det/both" "$APP_REL_PATH"
  sp_detect rails
  check "detect_stack: no profile matches, a Gemfile is still rails" test "$STACK" = "rails"
  check "detect_stack: rails says nothing matched a profile" test -z "$STACK_DETECTED_BY"
  sp_detect none
  check "detect_stack: a profile without detect files is never picked on its own (generic)" test "$STACK" = "generic"
  sp_detect py
  check "detect_stack: any one detect pattern matches, globs included" \
    bash -c '[[ "$1" == py && "$2" == "setup.*" ]]' _ "$STACK" "$STACK_DETECTED_BY"
  sp_detect two
  check "detect_stack: two matching profiles, the first in config order wins" test "$STACK" = "node"
  sp_detect both rails
  check "detect_stack: --stack rails beats a matching profile" test "$STACK" = "rails"
  sp_detect none bare
  check "detect_stack: --stack picks a profile with no detect files" \
    bash -c '[[ "$1" == bare && -z "$2" ]]' _ "$STACK" "$STACK_DETECTED_BY"
  out=$( (sp_detect none nope) 2>&1 ); rc=$?
  check "detect_stack: an unknown --stack fails, naming the profiles and the built-ins" \
    bash -c '[[ "$1" != 0 && "$2" == *"node py bare rails generic"* ]]' _ "$rc" "$out"
  sp_profiles_only() { stack_is_profile py && ! stack_is_profile rails && ! stack_is_profile generic; }
  check "stack_is_profile: true for a profile, false for the built-ins" sp_profiles_only
)
# shellcheck disable=SC2034  # read by detect_stack
(
  unset MIGITE_CFG_STACKS
  REPO_ROOT="$sp_dir/det/both" STACK_OVERRIDE=""
  detect_stack
  check "detect_stack: with no profiles configured, a Gemfile is rails as before" test "$STACK" = "rails"
  check "stack_is_profile: nothing is a profile when none are configured" not stack_is_profile rails
)

# ── the changed files each command gets ──────────────────────────────────────
(
  sp_profile
  check "changed_stack_files: source globs pick tracked and untracked files, a space in the name kept" \
    test "$(changed_stack_files main source)" = $'src/app.js\nsrc/app.test.js\nsrc/new file.js\ntop.test.js'
  check "changed_stack_files: **/ is zero or more directories" \
    test "$(changed_stack_files main specs)" = $'src/app.test.js\ntop.test.js'
  MIGITE_CFG_STACKS_NODE_SPECS=""
  check "changed_stack_files: no globs is every changed file (deletes and scratchpad/ still out)" \
    test "$(changed_stack_files main specs)" = "$(changed_all_files main)"
  check "changed_stack_files: ...which leaves the deleted file and scratchpad/ out" \
    not grep -qE '^(src/old.js|scratchpad/)' <<< "$(changed_stack_files main specs)"
)

# ── a passing run ────────────────────────────────────────────────────────────
(
  sp_profile
  log="$sp_dir/pass-lint.txt"
  rc=0; run_stack_lint main "$log" true > /dev/null || rc=$?
  check "run_stack_lint: a clean lint returns 0 and reads as passed" \
    test "$(stack_check_result "$rc" "$log")" = "passed"
  check "run_stack_lint: {files} is the source matches, one argument each" \
    test "$(cat "$SP_STATE/lint.args")" = $'src/app.js\nsrc/app.test.js\nsrc/new file.js\ntop.test.js'
  check "run_stack_lint: autofix runs first, then lint checks what's left" \
    test "$(cat "$SP_STATE/order")" = $'fix\nlint'
  check "run_stack_lint: ...both on the same files" \
    test "$(cat "$SP_STATE/fix.args")" = "$(cat "$SP_STATE/lint.args")"
  check "run_stack_lint: the lint output is in the log" grep -q 'lint: checked 4 file(s)' "$log"
  rm -f "$SP_STATE/order"
  run_stack_lint main "$log" > /dev/null
  check "run_stack_lint: without autofix=true the fixer doesn't run" test "$(cat "$SP_STATE/order")" = "lint"
  log="$sp_dir/pass-test.txt"
  rc=0; run_stack_test main "$log" > /dev/null || rc=$?
  check "run_stack_test: a green run returns 0 and reads as passed" \
    test "$(stack_check_result "$rc" "$log")" = "passed"
  check "run_stack_test: {files} is the specs matches" \
    test "$(cat "$SP_STATE/test.args")" = $'src/app.test.js\ntop.test.js'
  MIGITE_CFG_STACKS_NODE_TEST="fake-test"
  run_stack_test main "$log" > /dev/null
  check "run_stack_test: a command without {files} runs as written, with no arguments" \
    test "$(cat "$SP_STATE/test.argc")" = "0"
)

# ── a failing run ────────────────────────────────────────────────────────────
(
  sp_profile
  log="$sp_dir/fail-lint.txt"
  rc=0; FAKE_LINT_RC=1 run_stack_lint main "$log" true > /dev/null || rc=$?
  check "run_stack_lint: a lint that exits non-zero returns that status and reads as failed" \
    test "$rc/$(stack_check_result "$rc" "$log")" = "1/failed"
  check "run_stack_lint: the problems are in the log" grep -q 'no-unused-vars' "$log"
  log="$sp_dir/fail-test.txt"
  rc=0; FAKE_TEST_RC=3 run_stack_test main "$log" > /dev/null || rc=$?
  check "run_stack_test: any non-zero exit but 126/127 is a failure" \
    test "$(stack_check_result "$rc" "$log")" = "failed"
)

# ── nothing to run, or a command that can't start ────────────────────────────
# shellcheck disable=SC2034  # MIGITE_CFG_* are read by cfg through indirection
(
  sp_profile
  log="$sp_dir/skip.txt"
  MIGITE_CFG_STACKS_NODE_SPECS="*.spec.ts"
  rc=0; run_stack_test main "$log" > /dev/null || rc=$?
  check "run_stack_test: no changed file matches the specs, so nothing runs" test ! -e "$SP_STATE/test.args"
  check "run_stack_test: ...it reads as skipped and the log says why" \
    test "$(stack_check_result "$rc" "$log"): $(cat "$log")" = "skipped: Skipped: no changed files match stacks.node.specs."
  MIGITE_CFG_STACKS_NODE_LINT="" MIGITE_CFG_STACKS_NODE_AUTOFIX=""
  rc=0; run_stack_lint main "$log" true > /dev/null || rc=$?
  check "run_stack_lint: a profile with no lint command skips lint" \
    test "$(stack_check_result "$rc" "$log")" = "skipped"
  MIGITE_CFG_STACKS_NODE_LINT="sp-no-such-linter {files}"
  rc=0; run_stack_lint main "$log" > /dev/null 2>&1 || rc=$?
  check "run_stack_lint: a command that isn't installed (127) reads as unavailable, not failed" \
    test "$(stack_check_result "$rc" "$log")" = "unavailable"
)

# ── heal hands a failure to the agent ────────────────────────────────────────
# shellcheck disable=SC2034  # globals read by run_auto_heal_loop
sp_heal_setup() {
  sp_profile
  export HOME="$sp_dir/home" XDG_CONFIG_HOME="$sp_dir/home/.config" MIGITE_AGENT=claude
  unset MIGITE_CONFIG MIGITE_STACK
  TASK_SLUG="sp-$1" MAX_HEAL_ATTEMPTS=2
  PLAN_FILE="$sp_dir/plan.md" IMPLEMENTATION_FILE="$sp_dir/implementation.md"
  echo '# Plan: label the widget' > "$PLAN_FILE"
  echo 'Implemented the label.' > "$IMPLEMENTATION_FILE"
  export FAKE_CLAUDE_PROMPT="$sp_dir/heal-prompt-$1.txt"
}
(
  sp_heal_setup fails-once
  echo 1 > "$SP_STATE/test-fails"
  run_auto_heal_loop > "$sp_dir/heal-fails-once.out" 2>&1
  check "heal: a profile's failing test goes to the agent, under the command's name" \
    grep -qF '## Test output (`fake-test {files}`)' "$FAKE_CLAUDE_PROMPT"
  check "heal: ...with the failure from the test log" grep -q 'widget renders its label' "$FAKE_CLAUDE_PROMPT"
  check "heal: ...and the plan and implementation notes" \
    bash -c 'grep -q "label the widget" "$1" && grep -q "Implemented the label" "$1"' _ "$FAKE_CLAUDE_PROMPT"
  check "heal: a lint that passed is not in the prompt" not grep -q '## Lint output' "$FAKE_CLAUDE_PROMPT"
  check "heal: one attempt, after which the re-run passes" test "$HEAL_ATTEMPT" = "1"
  check "heal: and it says the failure was resolved" grep -q 'resolved failures after 1 attempt' "$sp_dir/heal-fails-once.out"
)
(
  sp_heal_setup lint
  export FAKE_LINT_RC=1 MIGITE_CFG_HEAL_PROMPT_LOG_MAX_BYTES=300 FAKE_TEST_NOISE=200
  echo 5 > "$SP_STATE/test-fails"
  run_auto_heal_loop > "$sp_dir/heal-lint.out" 2>&1
  check "heal: lint left after autofix goes to the agent, naming both commands" \
    grep -qF '## Lint output (`fake-lint {files}`; `fake-fix {files}` already applied' "$FAKE_CLAUDE_PROMPT"
  check "heal: a long log reaches the prompt as an excerpt (truncate_log)" grep -q 'bytes elided' "$FAKE_CLAUDE_PROMPT"
  check "heal: stops at heal.max_attempts and says failures remain" \
    bash -c '[[ "$1" == 2 ]] && grep -q "stopped with failures remaining" "$2"' _ "$HEAL_ATTEMPT" "$sp_dir/heal-lint.out"
)
# shellcheck disable=SC2034  # MIGITE_CFG_* are read by cfg through indirection
(
  sp_heal_setup unavailable
  MIGITE_CFG_STACKS_NODE_LINT="sp-no-such-linter {files}"
  run_auto_heal_loop > "$sp_dir/heal-unavailable.out" 2>&1
  check "heal: a lint that could not start never reaches the agent" test ! -e "$FAKE_CLAUDE_PROMPT"
  check "heal: ...no attempt is spent on it" test "$HEAL_ATTEMPT" = "0"
  check "heal: ...and it is reported as a tooling problem" grep -q 'could not start' "$sp_dir/heal-unavailable.out"
)

# ── migite-audit and migite-pr-review pick their checklist by stack ──────────
mkdir -p "$sp_dir/det/mono/a" "$sp_dir/det/mono/b"
touch "$sp_dir/det/mono/a/Gemfile" "$sp_dir/det/mono/b/Gemfile"
# shellcheck disable=SC2034  # MIGITE_CFG_* are read by cfg through indirection
(
  MIGITE_CFG_STACKS="node"
  MIGITE_CFG_STACKS_NODE_DETECT="package.json"
  REPO_ROOT="$sp_dir/det/both"
  check "standalone_stack: detection, as migite does (a profile before rails)" test "$(standalone_stack)" = "node"
  check "standalone_stack: --stack beats detection" test "$(standalone_stack --focus x --stack rails)" = "rails"
  check "standalone_stack: stack: in the config beats detection" \
    test "$(MIGITE_CFG_STACK=generic standalone_stack)" = "generic"
  REPO_ROOT="$sp_dir/det/mono"
  check "standalone_stack: a repo detect_stack can't place (two Gemfiles one level down) is rails" \
    test "$(standalone_stack 2>/dev/null)" = "rails"
  out=$( (standalone_stack --stack nope) 2>&1 ); rc=$?
  check "standalone_stack: an unknown --stack fails, naming what is supported" \
    bash -c '[[ "$1" != 0 && "$2" == *"node rails generic"* ]]' _ "$rc" "$out"
)

# ── Phase 3's checks and the commit gate's banner ────────────────────────────
# shellcheck disable=SC2034  # read by run_stack_review_checks and show_commit_context
(
  sp_profile
  RUBOCOP_LOG="$sp_dir/review-lint.txt" RSPEC_LOG="$sp_dir/review-test.txt"
  echo 1 > "$SP_STATE/test-fails"
  run_stack_review_checks > "$sp_dir/review.out" 2>&1
  check "run_stack_review_checks: a failing test is failed, a clean lint passed" \
    test "$STACK_TEST_RESULT/$STACK_LINT_RESULT" = "failed/passed"
  check "run_stack_review_checks: the test output is in RSPEC_LOG, which the reviewer reads" \
    grep -q 'widget renders its label' "$RSPEC_LOG"
  check "run_stack_review_checks: a real failure is not a tooling error" test -z "$TOOLING_ERROR"
  banner=$(show_commit_context 2>&1)
  check "show_commit_context: a profile's failed tests are on the banner" grep -q 'Tests: .*failed' <<< "$banner"
  check "show_commit_context: ...and its clean lint" grep -q 'Lint: .*clean' <<< "$banner"
  check "show_commit_context: no Rails Specs or Rubocop lines for a profile" not grep -qE 'Specs:|Rubocop:' <<< "$banner"

  MIGITE_CFG_STACKS_NODE_TEST="sp-no-such-runner {files}"
  run_stack_review_checks > "$sp_dir/review-unavailable.out" 2>&1
  check "run_stack_review_checks: a test command that can't start is a tooling error" \
    bash -c '[[ "$1" == unavailable && "$2" == *"could not start"*"sp-no-such-runner"* ]]' _ "$STACK_TEST_RESULT" "$TOOLING_ERROR"
  check "show_commit_context: the tooling error heads the banner" \
    grep -q 'could not start.*fix the toolchain' <<< "$(show_commit_context 2>&1)"
)

unset -f sp_profile sp_heal_setup
PATH="$_sp_saved_path"
unset SP_STATE
