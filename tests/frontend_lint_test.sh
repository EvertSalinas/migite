# tests/frontend_lint_test.sh - lib/stack.sh:frontend_lint_tools / run_frontend_lint_check
#
# The frontend lint runs erb_lint over changed .erb files and eslint over
# changed JavaScript, but only the linters a repo actually configures, and it
# reports "didn't run" differently from "clean". The real linters are replaced
# by fakes (a bundle_exec override, a node_modules/.bin/eslint script) that
# record their arguments.

fl_app=$(mktemp -d); CLEANUP_DIRS+=("$fl_app")
fl_log="$fl_app/lint.txt"
fl_files="app/views/items/_form.html.erb app/javascript/controllers/modal_controller.js"

(
  # Read by the lib/stack.sh functions under test (frontend_lint_tools,
  # strip_app_prefix), which shellcheck can't follow into.
  # shellcheck disable=SC2034
  APP_ROOT="$fl_app"
  # shellcheck disable=SC2034
  APP_REL_PATH=""

  check "run_frontend_lint_check: nothing changed is clean and says so" \
    run_frontend_lint_check "" "$fl_log"
  check "run_frontend_lint_check: ...with 'No frontend files changed.'" \
    grep -qx 'No frontend files changed.' "$fl_log"

  check "frontend_lint_tools: a repo with neither linter configured has none" \
    test -z "$(frontend_lint_tools)"
  run_frontend_lint_check "$fl_files" "$fl_log"
  check "run_frontend_lint_check: no configured linter is skipped, not reported clean" \
    grep -q '^No frontend linters configured' "$fl_log"

  # erb_lint installed but not configured doesn't count
  printf 'GEM\n  specs:\n    erb_lint (0.9.0)\n' > "$fl_app/Gemfile.lock"
  check "frontend_lint_tools: erb_lint in Gemfile.lock without a .erb_lint.yml doesn't count" \
    test -z "$(frontend_lint_tools)"
  echo '---' > "$fl_app/.erb_lint.yml"
  echo 'export default []' > "$fl_app/eslint.config.js"
  check "frontend_lint_tools: finds both once each is configured" \
    test "$(frontend_lint_tools)" = "erb_lint eslint"

  # eslint configured but node_modules missing: reported for tooling_failed
  bundle_exec() { echo "bundle exec $*" >> "$fl_app/calls.txt"; }
  run_frontend_lint_check "$fl_files" "$fl_log" > /dev/null
  check "run_frontend_lint_check: a configured-but-missing eslint is a tooling failure" \
    tooling_failed "$fl_log"

  # Both present: erb_lint gets only the .erb file (with -a when autocorrecting),
  # eslint only the .js file (with --fix), and a failing eslint fails the check.
  mkdir -p "$fl_app/node_modules/.bin"
  printf '#!/usr/bin/env bash\necho "eslint $*" >> "%s/calls.txt"\nexit 1\n' "$fl_app" > "$fl_app/node_modules/.bin/eslint"
  chmod +x "$fl_app/node_modules/.bin/eslint"
  : > "$fl_app/calls.txt"
  fl_rc=0
  run_frontend_lint_check "$fl_files" "$fl_log" true > /dev/null || fl_rc=$?
  check "run_frontend_lint_check: erb_lint autocorrects only the .erb files" \
    grep -qx 'bundle exec erb_lint -a app/views/items/_form.html.erb' "$fl_app/calls.txt"
  check "run_frontend_lint_check: eslint fixes only the JavaScript files" \
    grep -qx 'eslint --fix app/javascript/controllers/modal_controller.js' "$fl_app/calls.txt"
  check "run_frontend_lint_check: remaining eslint problems fail the check" test "$fl_rc" = "1"
  check "run_frontend_lint_check: each linter's output is under its own header" \
    bash -c 'grep -qx "== erb_lint ==" "$1" && grep -qx "== eslint ==" "$1"' _ "$fl_log"

  MIGITE_CFG_FRONTEND_LINT=off run_frontend_lint_check "$fl_files" "$fl_log"
  check "run_frontend_lint_check: frontend.lint: off runs nothing" \
    grep -q '^Frontend lint off' "$fl_log"
)
