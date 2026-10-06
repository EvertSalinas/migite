# tests/knowledge_injection_test.sh - lib/intake.sh:build_knowledge_injection
# injects knowledge.md entries capped at knowledge.inject_max_bytes, instead of
# the whole ever-growing file: the ones sharing the most words with the intake
# and the Jira ticket first (knowledge.select: relevant), else the newest.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping knowledge injection checks"
  return 0 2>/dev/null || exit 0
fi

ki_dir=$(mktemp -d); CLEANUP_DIRS+=("$ki_dir")
{
  printf '# Knowledge — repo\n\n'
  for i in $(seq 1 40); do printf '## 2026-01-%02d — task-%02d\n- lesson number %02d, long enough to take some room in the file\n\n' "$((i % 28 + 1))" "$i" "$i"; done
} > "$ki_dir/knowledge.md"

out=$(MIGITE_CFG_KNOWLEDGE_INJECT_MAX_BYTES=600 build_knowledge_injection "$ki_dir/knowledge.md")
check "build_knowledge_injection: the newest entry comes first" \
  bash -c '[[ "$1" == *"lesson number 40"* && "$1" != *"lesson number 01,"* ]] && [[ "${1%%lesson number 40*}" != *"lesson number 3"* ]]' _ "$out"
check "build_knowledge_injection: capped at knowledge.inject_max_bytes, naming the file for the rest" \
  bash -c '[[ ${#1} -lt 1100 && "$1" == *"older entries not shown"* ]]' _ "$out"
check "build_knowledge_injection: a missing file injects nothing" \
  test -z "$(build_knowledge_injection "$ki_dir/absent.md")"
printf '# Knowledge — repo\n\n> no entries yet\n' > "$ki_dir/empty.md"
check "build_knowledge_injection: a file with no entries injects nothing" \
  test -z "$(build_knowledge_injection "$ki_dir/empty.md")"

# One old lesson about the task, then the same 40 newer ones: the newest 600
# bytes leave it out, picking by the task's words keeps it.
{
  printf '# Knowledge — repo\n\n## 2025-12-01 — bb-0001\n- Twilio delivery callbacks arrive twice when a retried send succeeds; dedupe on the message SID\n\n'
  for i in $(seq 1 40); do printf '## 2026-01-%02d — task-%02d\n- lesson number %02d, long enough to take some room in the file\n\n' "$((i % 28 + 1))" "$i" "$i"; done
} > "$ki_dir/grown.md"
printf '**Title:** Retry failed Twilio deliveries\n' > "$ki_dir/intake.md"
cp "$REPO_ROOT/templates/feature.md" "$ki_dir/bare-intake.md"
printf -- '---\ncreated: 2026-10-06\n---\n\n## Jira ticket: BB-1\n**Description:** Twilio callbacks run twice\n' > "$ki_dir/jira-context.md"
export MIGITE_CFG_KNOWLEDGE_INJECT_MAX_BYTES=600

newest=$(build_knowledge_injection "$ki_dir/grown.md")
out=$(build_knowledge_injection "$ki_dir/grown.md" "$ki_dir/intake.md")
check "build_knowledge_injection: an old entry that matches the intake goes in (knowledge.select: relevant)" \
  bash -c '[[ "$1" == *"Twilio delivery callbacks"* && "$1" == *"lesson number 40"* && "$1" == *"other entries not shown"* ]]' _ "$out"
check "build_knowledge_injection: the newest-first cut leaves that entry out" \
  bash -c '[[ "$1" != *"Twilio"* ]]' _ "$newest"
out=$(MIGITE_CFG_KNOWLEDGE_SELECT=recent build_knowledge_injection "$ki_dir/grown.md" "$ki_dir/intake.md")
check "build_knowledge_injection: knowledge.select: recent ignores the intake, newest first" \
  test "$out" = "$newest"
out=$(build_knowledge_injection "$ki_dir/grown.md" "$ki_dir/bare-intake.md" "$ki_dir/jira-context.md")
check "build_knowledge_injection: the Jira ticket's words count when the intake is the bare template" \
  bash -c '[[ "$1" == *"Twilio delivery callbacks"* ]]' _ "$out"
check "build_knowledge_injection: a bare template intake alone injects the newest entries, as before" \
  test "$(build_knowledge_injection "$ki_dir/grown.md" "$ki_dir/bare-intake.md")" = "$newest"
check "build_knowledge_injection: a missing or empty task file is skipped" \
  test "$(build_knowledge_injection "$ki_dir/grown.md" "$ki_dir/absent-intake.md" "")" = "$newest"
unset MIGITE_CFG_KNOWLEDGE_INJECT_MAX_BYTES
unset ki_dir out newest
