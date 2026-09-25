#!/usr/bin/env bash
# lib/stack.sh - the project's stack: detection, tooling, and changed files.
#
# A stack is a `stack_<name>_detect` / `stack_<name>_app_root` function pair;
# everything that runs a stack's linter or test runner (bundle exec, rubocop,
# rspec) or lists the files they should look at lives here. Reads $REPO_ROOT,
# $STACK_OVERRIDE; sets $STACK, $APP_ROOT, $APP_REL_PATH. The frontend half of a
# Rails app (views, Stimulus/Turbo JavaScript) has its own changed-files list and
# lint runner below; both are no-ops on a backend-only diff.

# ── Stack profiles ────────────────────────────────────────────────────────────
# A stack is a `stack_<name>_detect` / `stack_<name>_app_root` function pair.
# detect_stack() tries STACK_PROFILES in order and dispatches to the first
# match — adding a stack means registering one more pair here, not adding a
# branch to detect_stack() or to any caller. `generic` must stay last — it's
# the catch-all that lets migite run on any project, not just Rails: review.sh
# skips rubocop/rspec entirely when $STACK == "generic", and migite-plan uses
# a generic file-glob set instead of the Rails-MVC EXPLORE_AREAS.
STACK_PROFILES=(rails generic)

# stack_rails_detect — true if a Gemfile exists at $REPO_ROOT or exactly one
# level down. Detection only, no side effects — stack_rails_app_root (below)
# does the actual, more detailed resolution once this stack is selected.
stack_rails_detect() {
  [[ -f "$REPO_ROOT/Gemfile" ]] && return 0
  find "$REPO_ROOT" -mindepth 2 -maxdepth 2 -name Gemfile -print -quit 2>/dev/null | grep -q .
}

# stack_rails_app_root — sets $APP_ROOT and $APP_REL_PATH.
# Bundler only searches upward from cwd for a Gemfile, never into
# subdirectories. Most repos have it at $REPO_ROOT, but some nest the actual
# Ruby app one level down (e.g. a rails-app/ dir alongside other tooling) —
# without this, every bundle exec call fails with "Could not locate Gemfile".
stack_rails_app_root() {
  if [[ -f "$REPO_ROOT/Gemfile" ]]; then
    APP_ROOT="$REPO_ROOT"
    APP_REL_PATH=""
    return
  fi

  local matches=()
  while IFS= read -r -d '' gemfile; do
    matches+=("$(dirname "$gemfile")")
  done < <(find "$REPO_ROOT" -mindepth 2 -maxdepth 2 -name Gemfile -print0)

  case "${#matches[@]}" in
    0) error "No Gemfile found at $REPO_ROOT or one level down — is this a Ruby project?" ;;
    1)
      APP_ROOT="${matches[0]}"
      APP_REL_PATH="${APP_ROOT#"$REPO_ROOT"/}"
      log "Gemfile not at repo root — using app dir: $APP_REL_PATH"
      ;;
    *) error "Multiple Gemfiles found under $REPO_ROOT (${matches[*]}) — migite doesn't support multi-app monorepos yet" ;;
  esac
}

# stack_generic_detect — always matches. The catch-all for any repo that
# isn't a recognized stack; must stay last in STACK_PROFILES.
stack_generic_detect() { return 0; }

# stack_generic_app_root — no Gemfile-style subdirectory nesting to resolve;
# the app root is always the repo root.
stack_generic_app_root() {
  APP_ROOT="$REPO_ROOT"
  APP_REL_PATH=""
}

# detect_stack — sets $STACK, then dispatches to the matched profile's
# _app_root to set $APP_ROOT/$APP_REL_PATH. Honors an explicit $STACK_OVERRIDE
# (set via migite's --stack flag) before trying STACK_PROFILES in order.
detect_stack() {
  if [[ -n "${STACK_OVERRIDE:-}" ]]; then
    if [[ ! " ${STACK_PROFILES[*]} " == *" $STACK_OVERRIDE "* ]]; then
      error "Unknown stack '$STACK_OVERRIDE' — supported: ${STACK_PROFILES[*]}"
    fi
    STACK="$STACK_OVERRIDE"
    "stack_${STACK}_app_root"
    return
  fi

  local s
  for s in "${STACK_PROFILES[@]}"; do
    if "stack_${s}_detect"; then
      STACK="$s"
      "stack_${s}_app_root"
      return
    fi
  done
}

# strip_app_prefix <files> — rewrites REPO_ROOT-relative paths (as produced by
# `git diff --name-only`) to APP_ROOT-relative paths, so they still resolve
# once bundle/rubocop/rspec run with cwd = APP_ROOT. No-op when the app lives
# at the repo root.
strip_app_prefix() {
  local files="$1"
  if [[ -z "${APP_REL_PATH:-}" ]]; then
    echo "$files"
    return
  fi
  local f stripped=()
  for f in $files; do
    stripped+=("${f#"$APP_REL_PATH"/}")
  done
  echo "${stripped[*]}"
}

# bundle_exec <args...> — runs `bundle exec` with cwd = $APP_ROOT, so it finds
# the Gemfile (and .tool-versions) regardless of where migite was invoked from.
bundle_exec() {
  (cd "$APP_ROOT" && bundle exec "$@")
}

# changed_ruby_files/changed_spec_files <base_branch> — echo changed .rb /
# _spec.rb files, tracked (git diff --diff-filter=ACMR) union untracked (git
# ls-files --others). A file that hasn't been `git add`-ed yet is invisible to
# `git diff`, and thus to rubocop/rspec — this exact blind spot has recurred
# at least five times in production (docs/improvements.md: 2026-07-23,
# 2026-08-05, 2026-08-11, 2026-08-12, 2026-08-17), each time fixed at one call
# site and not the others. One shared function, used everywhere the two are
# needed, so it can't drift out of sync again.
#
# Every grep here ends in `|| true`: with no match, grep exits 1, and under
# migite's `set -euo pipefail` a `X=$(changed_spec_files ...)` would abort the
# run - which a frontend-only diff (no .rb, no specs) reliably triggers.
changed_ruby_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | { grep '\.rb$' || true; } | sort -u
}

# changed_spec_files also drops system specs when frontend.system_specs is off:
# they need a real browser, and some machines (and CI images) have none.
changed_spec_files() {
  local base_branch="$1"
  local system_specs
  system_specs="$(cfg frontend.system_specs on)"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | { grep '_spec\.rb$' || true; } \
    | { if [[ "$system_specs" == "off" ]]; then grep -vE '(^|/)spec/system/' || true; else cat; fi; } \
    | sort -u
}

# changed_source_files <base_branch> — changed .rb files that are NOT specs.
# The heal loop autocorrects only source files (specs are rspec's job there).
changed_source_files() {
  local base_branch="$1"
  changed_ruby_files "$base_branch" | grep -v '_spec\.rb$' || true
}

# changed_frontend_files <base_branch> - changed view templates and JavaScript
# (.erb, .js, .mjs, .ts, .jsx, .tsx) under app/, tracked union untracked, minus
# built assets. Only app/ counts: a root eslint.config.js or tailwind.config.js
# is tooling, and vendored or generated JavaScript lives outside app/. This list
# is what decides whether a diff "touches the frontend": the frontend linters,
# the frontend reviewer and the browser check all run only when it's non-empty,
# so a backend-only task never pays for them.
changed_frontend_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | { grep -E '(^|/)app/.+\.(erb|js|mjs|ts|jsx|tsx)$' || true; } \
    | { grep -vE '(^|/)app/assets/builds/' || true; } \
    | sort -u
}

# changed_all_files <base_branch> — every changed path, any extension, tracked
# union untracked, minus migite's own scratchpad/. Used for the diff-vs-notes
# cross-reference warning before review.
changed_all_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | grep -v '^scratchpad/' | sort -u
}

# detect_base_branch — echoes the repo's default branch for diff scoping.
# origin/HEAD is authoritative when set; otherwise falls back to whichever of
# main/master/develop actually exists locally, so migite works unmodified on
# repos of either convention.
detect_base_branch() {
  local remote_head
  remote_head=$(git symbolic-ref --short -q refs/remotes/origin/HEAD 2>/dev/null || true)
  if [[ -n "$remote_head" ]]; then
    echo "${remote_head#origin/}"
    return
  fi
  local branch
  for branch in main master develop; do
    if git show-ref --verify --quiet "refs/heads/$branch"; then
      echo "$branch"
      return
    fi
  done
  echo "main"
}

# tooling_failed <log> — checks a rubocop/rspec output log for the failure
# patterns migite has had to add detection for one at a time in production
# (docs/improvements.md): a Ruby version not selected for `bundle exec`, a
# git-sourced gem not checked out, or an rspec run that produced 0 examples
# because of a DB connection failure or a load error. Echoes a short
# description and returns 0 (failed) on a match, 1 (clean) otherwise — every
# caller that inspects a log for these patterns should call this rather than
# repeating the grep list, which is exactly how the git-error and load-error
# patterns went missing from some call sites but not others.
tooling_failed() {
  local log="$1"
  [[ -f "$log" ]] || return 1
  if grep -q 'No version is set for command' "$log"; then
    echo "Ruby version not set — bundle exec could not run"
    return 0
  fi
  if grep -q 'Bundler::GitError\|not yet checked out' "$log"; then
    echo "Bundler::GitError — a git-sourced gem isn't checked out (run bundle install)"
    return 0
  fi
  if grep -q '0 examples' "$log" && grep -qi 'connection\|ConnectionBad' "$log"; then
    echo "DB connection failed — 0 examples ran, no coverage verified"
    return 0
  fi
  if grep -q '0 examples' "$log" && grep -qi 'error occurred while loading\|LoadError' "$log"; then
    echo "Load errors — 0 examples ran, spec coverage unverified"
    return 0
  fi
  # System specs whose browser never started: Cuprite/Ferrum with no Chrome,
  # Selenium with no or a mismatched driver, Playwright with no downloaded
  # browsers. Each fails every system example, which reads as N real failures
  # the heal loop would then try to "fix" in application code.
  if grep -qE "Ferrum::(BinaryNotFoundError|ProcessTimeoutError)|Webdrivers::BrowserNotFound|Selenium::WebDriver::Error::(SessionNotCreatedError|NoSuchDriverError)|Executable doesn't exist at .*ms-playwright" "$log"; then
    echo "Browser for system specs unavailable - they could not run (install Chrome / the driver / npx playwright install, or set frontend.system_specs: off)"
    return 0
  fi
  if grep -q 'is configured but not installed' "$log"; then
    echo "A frontend linter is configured but not installed - run your JS package install (npm/yarn/pnpm)"
    return 0
  fi
  return 1
}

# run_rubocop_check <files> <log> [autocorrect=false]
# Runs rubocop over <files> (space-separated, from git diff) and tees to <log>;
# writes a fallback message instead of running anything if <files> is empty.
# Callers own the exit-code handling — some swallow failures, some branch on them.
run_rubocop_check() {
  local files="$1" log="$2" autocorrect="${3:-false}"
  if [[ -z "$files" ]]; then
    echo "No Ruby files changed." > "$log"
    return 0
  fi
  local app_files
  app_files=$(strip_app_prefix "$files")
  if [[ "$autocorrect" == "true" ]]; then
    # shellcheck disable=SC2086
    bundle_exec rubocop -a $app_files --format progress 2>&1 | tee "$log"
  else
    # shellcheck disable=SC2086
    bundle_exec rubocop $app_files --format progress 2>&1 | tee "$log"
  fi
}

# run_rspec_full_suite <log> - the whole suite, for when no spec files changed
# (heal.full_suite_fallback). With frontend.system_specs: off, spec/system is
# excluded here too, the same as in changed_spec_files.
run_rspec_full_suite() {
  local log="$1"
  local -a args=()
  [[ "$(cfg frontend.system_specs on)" == "off" ]] && args+=(--exclude-pattern "spec/system/**/*_spec.rb")
  bundle_exec rspec ${args[@]+"${args[@]}"} 2>&1 | tee "$log"
}

# frontend_lint_tools - echoes the frontend linters this app has set up, out of
# "erb_lint eslint". A linter counts only when the repo configures it, not merely
# installs it: erb_lint needs the gem in Gemfile.lock and a .erb_lint.yml (or the
# older .erb-lint.yml); eslint needs an eslint config file. An eslint config
# without node_modules/.bin/eslint still counts, so run_frontend_lint_check can
# say it didn't run instead of the lint silently passing.
frontend_lint_tools() {
  local -a tools=()
  if [[ -f "$APP_ROOT/Gemfile.lock" ]] && grep -qE '^    erb_lint \(' "$APP_ROOT/Gemfile.lock" \
     && { [[ -f "$APP_ROOT/.erb_lint.yml" ]] || [[ -f "$APP_ROOT/.erb-lint.yml" ]]; }; then
    tools+=(erb_lint)
  fi
  if compgen -G "$APP_ROOT/eslint.config.*" >/dev/null || compgen -G "$APP_ROOT/.eslintrc*" >/dev/null; then
    tools+=(eslint)
  fi
  echo "${tools[*]+"${tools[*]}"}"
}

# _frontend_lint_run <log> <label> <cmd...> - runs one linter, appends its
# output to <log> under a "== <label> ==" header (and to the terminal), and
# returns the linter's exit status.
_frontend_lint_run() {
  local log="$1" label="$2"
  shift 2
  local rc=0 out
  out=$(mktemp)
  "$@" > "$out" 2>&1 || rc=$?
  { echo "== $label =="; cat "$out"; echo ""; } | tee -a "$log"
  rm -f "$out"
  return "$rc"
}

# run_frontend_lint_check <files> <log> [autocorrect=false]
# Runs erb_lint over the changed .erb files and eslint over the changed
# JavaScript (whichever frontend_lint_tools finds), writing both to <log>.
# Returns 1 when a linter reports problems, 0 otherwise - including when
# there's nothing to lint, frontend.lint is off, or no linter is configured,
# each of which is written to <log> as its first line so callers can tell.
# Unlike rubocop, both linters signal remaining problems through their exit
# status, so no output format is parsed. A linter that couldn't start (Ruby
# version, bundler, missing node_modules) is for tooling_failed to report.
run_frontend_lint_check() {
  local files="$1" log="$2" autocorrect="${3:-false}"
  : > "$log"
  if [[ -z "$files" ]]; then
    echo "No frontend files changed." > "$log"
    return 0
  fi
  if [[ "$(cfg frontend.lint auto)" == "off" ]]; then
    echo "Frontend lint off (frontend.lint: off)." > "$log"
    return 0
  fi
  local tools
  tools="$(frontend_lint_tools)"
  if [[ -z "$tools" ]]; then
    echo "No frontend linters configured (erb_lint, eslint) - skipped." > "$log"
    return 0
  fi

  local app_files f rc=0
  local -a erb_files=() js_files=()
  app_files=$(strip_app_prefix "$files")
  for f in $app_files; do
    case "$f" in
      *.erb) erb_files+=("$f") ;;
      *) js_files+=("$f") ;;
    esac
  done

  if [[ " $tools " == *" erb_lint "* && ${#erb_files[@]} -gt 0 ]]; then
    local -a erb_args=()
    [[ "$autocorrect" == "true" ]] && erb_args+=(-a)
    _frontend_lint_run "$log" "erb_lint" bundle_exec erb_lint ${erb_args[@]+"${erb_args[@]}"} "${erb_files[@]}" || rc=1
  fi
  if [[ " $tools " == *" eslint "* && ${#js_files[@]} -gt 0 ]]; then
    if [[ -x "$APP_ROOT/node_modules/.bin/eslint" ]]; then
      local -a eslint_args=()
      [[ "$autocorrect" == "true" ]] && eslint_args+=(--fix)
      _frontend_lint_run "$log" "eslint" \
        bash -c 'cd "$1" && shift && node_modules/.bin/eslint "$@"' _ "$APP_ROOT" \
        ${eslint_args[@]+"${eslint_args[@]}"} "${js_files[@]}" || rc=1
    else
      { echo "== eslint =="
        echo "eslint is configured but not installed (node_modules/.bin/eslint is missing) - run npm/yarn/pnpm install"
        echo ""; } | tee -a "$log"
    fi
  fi
  if ! grep -q '^== ' "$log"; then
    echo "No changed files for the configured frontend linters ($tools) - skipped." > "$log"
  fi
  return "$rc"
}

# run_rspec_check <files> <log>
# Runs rspec over <files> and tees to <log>; writes a fallback message if <files> is empty.
run_rspec_check() {
  local files="$1" log="$2"
  if [[ -z "$files" ]]; then
    echo "No spec files changed." > "$log"
    return 0
  fi
  local app_files
  app_files=$(strip_app_prefix "$files")
  # shellcheck disable=SC2086
  bundle_exec rspec $app_files 2>&1 | tee "$log"
}
