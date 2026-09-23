# tests/review_verdict_test.sh — lib/gate.sh:review_verdict
#
# The commit-gate banner used to grep the whole review for
# 'NEEDS FIXES\|APPROVED\|PASS\|READY TO COMMIT' and take the first matching
# line. The review format puts "## Brakeman: PASS" near the top, so that line
# won and the banner showed APPROVED on reviews whose Verdict said NEEDS FIXES
# (7 of 20 real reviews in the vault, 2026-09-22). Every fixture below has a
# "## Brakeman: PASS" line ABOVE the verdict to reproduce that trap; the shapes
# mirror ones seen in real review.md files.

fixtures="$SCRIPT_DIR/fixtures/reviews"

check "review_verdict: 'PASS' lines above the verdict never win (heading-line NEEDS FIXES)" \
  test "$(review_verdict "$fixtures/heading-line-needs-fixes.md")" = "needs_fixes"

check "review_verdict: bold verdict on the heading line" \
  test "$(review_verdict "$fixtures/heading-line-bold-needs-fixes.md")" = "needs_fixes"

check "review_verdict: bold verdict on the line after a bare '## Verdict' heading" \
  test "$(review_verdict "$fixtures/next-line-bold-needs-fixes.md")" = "needs_fixes"

check "review_verdict: plain READY TO COMMIT on the next line, despite a 'Rubocop: FAIL' line above" \
  test "$(review_verdict "$fixtures/next-line-ready.md")" = "ready"

check "review_verdict: bold READY TO COMMIT with a trailing clause on the heading line" \
  test "$(review_verdict "$fixtures/heading-line-bold-ready-with-clause.md")" = "ready"

check "review_verdict: NEEDS CHANGES synonym with a trailing clause" \
  test "$(review_verdict "$fixtures/next-line-needs-changes-with-clause.md")" = "needs_fixes"

check "review_verdict: older shape with the verdict itself as a heading ('# NEEDS FIXES')" \
  test "$(review_verdict "$fixtures/verdict-as-heading.md")" = "needs_fixes"

check "review_verdict: no Verdict section is 'unknown', not 'ready' (a 'PASS' line exists)" \
  test "$(review_verdict "$fixtures/no-verdict-section.md")" = "unknown"

check "review_verdict: missing file is 'unknown'" \
  test "$(review_verdict "$fixtures/does-not-exist.md")" = "unknown"
