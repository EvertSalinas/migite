# tests/machine_readable_test.sh — the bash side of the JSON envelopes:
# json_field, review_verdict preferring review.json, claude_print + the usage
# ledger, run_cost_so_far, sync_json. Uses tests/fake-claude on PATH so no real
# model call is made. Skips (with a note) when no working Python is available.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping machine-readable checks"
  return 0 2>/dev/null || exit 0
fi

mr_dir=$(mktemp -d); CLEANUP_DIRS+=("$mr_dir")
chmod +x "$SCRIPT_DIR/fake-claude"
# A `claude` shim first on PATH → tests/fake-claude. Without this, `claude`
# resolves to the REAL CLI and the tests below spend real money.
mkdir -p "$mr_dir/bin"
ln -sf "$SCRIPT_DIR/fake-claude" "$mr_dir/bin/claude"

# ── json_field ───────────────────────────────────────────────────────────────
printf '{"verdict":"needs_fixes","counts":{"critical":2,"warning":1,"note":0},"usage":null}\n' > "$mr_dir/review.json"
check "json_field: top-level key" \
  test "$(json_field "$mr_dir/review.json" verdict)" = "needs_fixes"
check "json_field: dotted nested key" \
  test "$(json_field "$mr_dir/review.json" counts.critical)" = "2"
check "json_field: zero is printed, not treated as missing" \
  test "$(json_field "$mr_dir/review.json" counts.note)" = "0"
check "json_field: missing key exits 1" \
  not json_field "$mr_dir/review.json" nope
check "json_field: null value exits 1" \
  not json_field "$mr_dir/review.json" usage
check "json_field: missing file exits 1" \
  not json_field "$mr_dir/absent.json" verdict

# ── review_verdict prefers review.json ───────────────────────────────────────
# The markdown says READY; the envelope beside it says needs_fixes → envelope wins.
cp "$SCRIPT_DIR/fixtures/reviews/next-line-ready.md" "$mr_dir/review.md"
mv "$mr_dir/review.json" "$mr_dir/review.json.keep"
check "review_verdict: with no review.json, parses the markdown (ready)" \
  test "$(review_verdict "$mr_dir/review.md")" = "ready"
mv "$mr_dir/review.json.keep" "$mr_dir/review.json"
check "review_verdict: a sibling review.json takes precedence over the markdown" \
  test "$(review_verdict "$mr_dir/review.md")" = "needs_fixes"
printf '{"verdict":"garbage"}\n' > "$mr_dir/review.json"
check "review_verdict: a malformed envelope verdict falls back to the markdown" \
  test "$(review_verdict "$mr_dir/review.md")" = "ready"
printf 'not json at all\n' > "$mr_dir/review.json"
check "review_verdict: an unparseable review.json falls back to the markdown" \
  test "$(review_verdict "$mr_dir/review.md")" = "ready"
rm -f "$mr_dir/review.json"

# ── agent_ask + ledger (fake claude on PATH) ─────────────────────────────────
_old_path="$PATH"
PATH="$mr_dir/bin:$PATH"
check "test harness: 'claude' resolves to the fake, never the real CLI" \
  test "$(command -v claude)" = "$mr_dir/bin/claude"
export MIGITE_USAGE_LEDGER="$mr_dir/usage.jsonl"

out=$(printf 'summarise this' | FAKE_CLAUDE_MODE=envelope agent_ask "unit-test" knowledge)
check "agent_ask: prints the envelope's result text, not the JSON" \
  bash -c '[[ "$1" == echo:summarise* ]]' _ "$out"
check "agent_ask: appends one ledger line" \
  test "$(wc -l < "$MIGITE_USAGE_LEDGER" | tr -d ' ')" = "1"
check "agent_ask: ledger line carries the label and tool" \
  bash -c 'grep -q "\"label\": \"unit-test\"" "$1" && grep -q "\"tool\": \"migite\"" "$1"' _ "$MIGITE_USAGE_LEDGER"
check "agent_ask: ledger line carries the cost" \
  grep -q '"cost_usd": 0.0475' "$MIGITE_USAGE_LEDGER"
check "agent_ask: the role picked the model (knowledge → standard tier)" \
  grep -q '"model": "claude-sonnet-5"' "$MIGITE_USAGE_LEDGER"

out=$(printf 'x' | FAKE_CLAUDE_MODE=text agent_ask "plain" knowledge)
check "agent_ask: plain-text CLI output passes through untouched (older CLI)" \
  bash -c '[[ "$1" == "plain text result for: x" ]]' _ "$out"

rc=0; printf 'x' | FAKE_CLAUDE_MODE=exit1 agent_ask "fail" knowledge >/dev/null 2>&1 || rc=$?
check "agent_ask: propagates a non-zero CLI exit" test "$rc" != "0"

rc=0; printf 'x' | FAKE_CLAUDE_MODE=is_error agent_ask "err" knowledge >/dev/null 2>&1 || rc=$?
check "agent_ask: an is_error envelope is a failure" test "$rc" != "0"

# ── agent_think end to end through agent_ask ─────────────────────────────────
think_out="$mr_dir/thinking.txt"
FAKE_CLAUDE_MODE=envelope agent_think "Extracting knowledge" knowledge "$think_out" "the prompt body" >/dev/null
check "agent_think: result text lands in the outfile" \
  bash -c '[[ "$(cat "$1")" == echo:the\ prompt* ]]' _ "$think_out"
check "agent_think: call is metered under its label" \
  grep -q '"label": "Extracting knowledge"' "$MIGITE_USAGE_LEDGER"

# ── run_cost_so_far / print_usage_summary ────────────────────────────────────
cost=$(run_cost_so_far)
check "run_cost_so_far: '<N> calls, \$X.XX' from the ledger" \
  bash -c '[[ "$1" =~ ^[0-9]+\ calls,\ \$[0-9]+\.[0-9]{2}$ ]]' _ "$cost"
check "run_cost_so_far: counts every metered call (envelope+text+fail+is_error+think = 5)" \
  bash -c '[[ "$1" == 5\ calls* ]]' _ "$cost"

SCRATCHPAD_DIR="$mr_dir/scratch"; TASK_DIR="$mr_dir/vault"; mkdir -p "$SCRATCHPAD_DIR"
_USAGE_SUMMARY_PRINTED=""
summary_out=$(print_usage_summary)   # subshell: the once-guard doesn't persist from here
check "print_usage_summary: prints a table with a total line" \
  bash -c 'printf "%s" "$1" | grep -q "^  total "' _ "$summary_out"
check "print_usage_summary: writes usage.json to the scratchpad" \
  test -s "$SCRATCHPAD_DIR/usage.json"
check "print_usage_summary: mirrors usage.json to the vault via sync_json (no frontmatter)" \
  bash -c 'head -c1 "$1" | grep -q "{"' _ "$TASK_DIR/usage.json"
print_usage_summary >/dev/null           # main shell: sets the guard, as migite's EXIT trap would
check "print_usage_summary: runs only once per process" \
  test -z "$(print_usage_summary)"
unset SCRATCHPAD_DIR TASK_DIR _USAGE_SUMMARY_PRINTED

# ── sync_json ────────────────────────────────────────────────────────────────
printf '{"a":1}\n' > "$mr_dir/plan.json"
sync_json "$mr_dir/plan.json" "$mr_dir/deep/er/plan.json"
check "sync_json: creates the destination directory and copies byte-for-byte" \
  cmp -s "$mr_dir/plan.json" "$mr_dir/deep/er/plan.json"
check "sync_json: missing source is a silent no-op" \
  sync_json "$mr_dir/nope.json" "$mr_dir/deep/nope.json"

PATH="$_old_path"
unset MIGITE_USAGE_LEDGER FAKE_CLAUDE_MODE
