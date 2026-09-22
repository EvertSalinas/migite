# tests/slugify_test.sh — helpers.sh:slugify, and parity with migite_paths.slugify
#
# `migite` (bash) names vault folders with helpers.sh's slugify; the standalone
# tools (Python) look them up with migite_paths.slugify. There used to be four
# implementations that disagreed on "_" and "+" (and the blueprint wrapper's
# `tr` version left stray hyphens). The bash one is canonical; the Python one
# must match it byte for byte.

check "slugify: lowercases and hyphenates spaces" \
  test "$(slugify "Add PDF Export")" = "add-pdf-export"
check "slugify: underscores become hyphens (not deleted)" \
  test "$(slugify "foo_bar")" = "foo-bar"
check "slugify: punctuation runs collapse to one hyphen" \
  test "$(slugify "fix N+1 on /districts!")" = "fix-n-1-on-districts"
check "slugify: Jira key unchanged apart from case" \
  test "$(slugify "BB-3370")" = "bb-3370"
check "slugify: leading/trailing junk stripped" \
  test "$(slugify "  --Hello World-- ")" = "hello-world"
long="jira ticket bb-3370 please fetch context from the discovery doc and figure it out"
check "slugify: capped at 50 chars" \
  test "${#long}" -gt 50 -a "$(slugify "$long" | wc -c | tr -d ' ')" -le 51
check "slugify: 50-char cut never leaves a trailing hyphen" \
  not bash -c '[[ "$1" == *- ]]' _ "$(slugify "$long")"

# Parity with the Python implementation, when a working Python is available
if "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  for input in "Add PDF Export" "foo_bar" "fix N+1 on /districts!" "BB-3370" "  --Hello World-- " "$long" \
               "https://x.atlassian.net/browse/BB-12" "ünïcödé café" "a_b-c d.e"; do
    py=$("$MIGITE_PYTHON" "$REPO_ROOT/migite_paths.py" slugify "$input")
    check "slugify parity bash==python: '$input'" test "$(slugify "$input")" = "$py"
  done
else
  echo "  · no working Python at $MIGITE_PYTHON — skipping bash/python slugify parity checks"
fi
