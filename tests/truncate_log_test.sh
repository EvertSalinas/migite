# tests/truncate_log_test.sh — lib/stack.sh:truncate_log
#
# An unbounded rubocop/rspec log in the auto-heal prompt overflowed the model's
# context window and the run died at Phase 2.5 (docs/improvements.md,
# 2026-09-28 pass). The excerpts must stay near heal.prompt_log_max_bytes while
# keeping the head (the first failure blocks, which carry the diagnostic
# detail) and the tail (the "N examples, N failures" summary and the
# "Failed examples:" list).

small=$(mktemp)
big=$(mktemp)
CLEANUP_DIRS+=("$small" "$big")

printf 'small log\n' > "$small"
check "truncate_log: under budget passes through unchanged" \
  test "$(truncate_log "$small" 100)" = "small log"

printf 'x%.0s' $(seq 1 100) > "$small"
check "truncate_log: exactly at budget passes through unchanged" \
  test "$(truncate_log "$small" 100 | wc -c)" -eq 100

for i in $(seq 1 200); do printf 'line %03d of the failure log\n' "$i"; done > "$big"
# 200 lines x 28 bytes = 5600 bytes; budget 1000 -> 600 head + 400 tail + marker.
check "truncate_log: over budget keeps the head" \
  test "$(truncate_log "$big" 1000 | head -1)" = "line 001 of the failure log"
check "truncate_log: over budget keeps the tail" \
  test "$(truncate_log "$big" 1000 | tail -1)" = "line 200 of the failure log"
check "truncate_log: elision marker names the dropped bytes and the full log" \
  grep -q "bytes elided — full log: $big" <<< "$(truncate_log "$big" 1000)"
check "truncate_log: output stays near the byte budget (marker overhead excluded from the cap)" \
  test "$(truncate_log "$big" 1000 | wc -c)" -le 1200

# compact_rspec_log (migite/log_compact.py) — the rspec-aware sibling used for
# the heal prompt's spec output.
rspec_log=$(mktemp)
CLEANUP_DIRS+=("$rspec_log")
cat > "$rspec_log" <<'EOF'
....F.....

Failures:

  1) Thing works
     Failure/Error: expect(1).to eq(2)
       expected: 2
            got: 1
     # ./spec/thing_spec.rb:5:in 'block'
  2) Thing also works
     Failure/Error: expect(1).to eq(2)
       expected: 2
            got: 1
     # ./spec/thing_spec.rb:9:in 'block'

2 examples, 2 failures
EOF
check "compact_rspec_log: under budget passes through unchanged" \
  test "$(compact_rspec_log "$rspec_log" 60000)" = "$(cat "$rspec_log")"
check "compact_rspec_log: over budget merges identical failures" \
  grep -q "2 total, 1 distinct" <<< "$(compact_rspec_log "$rspec_log" 100)"
