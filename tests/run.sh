#!/usr/bin/env bash
# tests/run.sh — dependency-free test runner for the bash library (lib/*.sh)
#
# Discovers tests/*_test.sh, sources each in turn, and expects them to call
# `check` for every assertion. The lib files are sourced standalone (no set -e
# side effects), so functions under test run exactly as migite itself would
# call them.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export MIGITE_HOME="$REPO_ROOT"   # helpers that shell out to migite/gateway.py resolve it from here
# shellcheck source=../lib/common.sh
source "$REPO_ROOT/lib/common.sh"
# Same Python resolution as the migite entrypoint (lib/common.sh). Tests that
# need Python skip with a note if the result doesn't run.
migite_resolve_python
export MIGITE_PYTHON
# Every ticket lookup goes to the fake Atlassian CLI, never a real (logged-in) acli on PATH.
export MIGITE_ACLI="$SCRIPT_DIR/fake-acli"
DATE="${DATE:-$(date +%Y-%m-%d)}"
TIMESTAMP="${TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
# Helpers like write_prompt/thinking write into $LOG_DIR — give tests a throwaway one
# so nothing lands in the real ~/.dev-workflow/logs and set -u never trips on it.
LOG_DIR="$(mktemp -d)"
export LOG_DIR
# Never let a test run append to a real ledger; tests that need one set their own.
unset MIGITE_USAGE_LEDGER

# Every lib file except doctor.sh (a command, not helpers) and the phases.
for _lib in config stack agent vault intake gate; do
  # shellcheck disable=SC1090
  source "$REPO_ROOT/lib/$_lib.sh"
done
unset _lib

CLEANUP_DIRS=()
# Results go through a FILE, not shell variables: test files may run blocks in
# ( subshells ) to isolate environment changes, and a counter incremented there
# would never reach this shell — a failing check inside one would print ✘ and
# the run would still report success.
RESULTS_FILE="$(mktemp)"
trap 'for d in "${CLEANUP_DIRS[@]:-}" "$LOG_DIR"; do [[ -n "$d" ]] && rm -rf "$d"; done; rm -f "$RESULTS_FILE"' EXIT

# check <label> <command...> — runs <command>, records pass/fail by its exit
# code. Prefer `test`/`[[` expressions or a function reference as <command>,
# e.g.: check "detect_base_branch: falls back to main" test "$got" = "main"
check() {
  local label="$1"; shift
  if "$@"; then
    echo "  ✔ $label"
    echo "PASS	$label" >> "$RESULTS_FILE"
  else
    echo "  ✘ $label"
    echo "FAIL	$label" >> "$RESULTS_FILE"
  fi
}

# not <command...> — negates a command's exit status, for use with `check`
# where the assertion is "this should fail" (e.g. tooling_failed on a clean log).
not() {
  ! "$@"
}

# make_fixture_repo — creates an empty git repo in a fresh tmp dir (registered
# for cleanup on exit) and echoes its path. gpgsign is disabled so commits
# don't hang on a pinentry prompt in a non-interactive shell.
make_fixture_repo() {
  local dir
  dir=$(mktemp -d)
  CLEANUP_DIRS+=("$dir")
  git init -q "$dir"
  # Tests diff against `main`. Without a global init.defaultBranch (a fresh CI
  # runner) git still creates `master`, so pin the branch name explicitly.
  # symbolic-ref works on every git version; `git init -b` needs 2.28+.
  git -C "$dir" symbolic-ref HEAD refs/heads/main
  git -C "$dir" config commit.gpgsign false
  git -C "$dir" config user.email test@example.com
  git -C "$dir" config user.name "migite tests"
  echo "$dir"
}

for test_file in "$SCRIPT_DIR"/*_test.sh; do
  [[ -f "$test_file" ]] || continue
  echo ""
  echo "── $(basename "$test_file") ──"
  # shellcheck disable=SC1090
  source "$test_file"
done

echo ""
TOTAL=$(wc -l < "$RESULTS_FILE" | tr -d ' ')
FAILED=$(grep -c '^FAIL	' "$RESULTS_FILE" || true)
if [[ "$FAILED" -gt 0 ]]; then
  echo "$FAILED/$TOTAL check(s) FAILED:"
  grep '^FAIL	' "$RESULTS_FILE" | cut -f2- | sed 's/^/  - /'
  exit 1
fi
echo "All $TOTAL checks passed."
