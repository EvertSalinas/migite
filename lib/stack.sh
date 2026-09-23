#!/usr/bin/env bash
# lib/stack.sh - the project's stack: detection, tooling, and changed files.
#
# A stack is a `stack_<name>_detect` / `stack_<name>_app_root` function pair;
# everything that runs a stack's linter or test runner (bundle exec, rubocop,
# rspec) or lists the files they should look at lives here. Reads $REPO_ROOT,
# $STACK_OVERRIDE; sets $STACK, $APP_ROOT, $APP_REL_PATH.

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
changed_ruby_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | grep '\.rb$' | sort -u
}

changed_spec_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | grep '_spec\.rb$' | sort -u
}

# changed_source_files <base_branch> — changed .rb files that are NOT specs.
# The heal loop autocorrects only source files (specs are rspec's job there).
changed_source_files() {
  local base_branch="$1"
  changed_ruby_files "$base_branch" | grep -v '_spec\.rb$' || true
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
