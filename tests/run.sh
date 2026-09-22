#!/usr/bin/env bash
# tests/run.sh — dependency-free test runner for migite.d/helpers.sh
#
# Discovers tests/*_test.sh, sources each in turn, and expects them to call
# `check` for every assertion. helpers.sh is sourced standalone (no set -e
# side effects — see its own header comment), so functions under test run
# exactly as migite itself would call them.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=../migite.d/helpers.sh
source "$REPO_ROOT/migite.d/helpers.sh"

TOTAL=0
FAILURES=()
CLEANUP_DIRS=()
trap 'for d in "${CLEANUP_DIRS[@]:-}"; do [[ -n "$d" ]] && rm -rf "$d"; done' EXIT

# check <label> <command...> — runs <command>, records pass/fail by its exit
# code. Prefer `test`/`[[` expressions or a function reference as <command>,
# e.g.: check "detect_base_branch: falls back to main" test "$got" = "main"
check() {
  local label="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@"; then
    echo "  ✔ $label"
  else
    echo "  ✘ $label"
    FAILURES+=("$label")
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
if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo "${#FAILURES[@]}/$TOTAL check(s) FAILED:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
echo "All $TOTAL checks passed."
