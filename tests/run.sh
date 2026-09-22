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

# Same Python resolution as the migite entrypoint: an explicit MIGITE_PYTHON
# wins; else `python3` only if it actually runs (an asdf shim exists on PATH
# even with no version selected for this directory — migite's own repo has
# none — and would fail with "No version is set"); else the asdf fallback.
# Tests that need Python skip with a note if none of these work.
if [[ -z "${MIGITE_PYTHON:-}" ]]; then
  if command -v python3 &>/dev/null && python3 --version &>/dev/null; then
    MIGITE_PYTHON="$(command -v python3)"
  else
    MIGITE_PYTHON="$HOME/.asdf/installs/python/3.13.5/bin/python3"
  fi
fi
export MIGITE_PYTHON
export MIGITE_HOME="$REPO_ROOT"   # helpers that shell out to migite_claude.py resolve it from here
DATE="${DATE:-$(date +%Y-%m-%d)}"
# Never let a test run append to a real ledger; tests that need one set their own.
unset MIGITE_USAGE_LEDGER

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
