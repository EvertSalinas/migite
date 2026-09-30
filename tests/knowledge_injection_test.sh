# tests/knowledge_injection_test.sh - lib/intake.sh:build_knowledge_injection
# injects the newest knowledge.md entries first, capped at
# knowledge.inject_max_bytes, instead of the whole ever-growing file.

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
unset ki_dir out
