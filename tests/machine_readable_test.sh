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
# This file asserts fake-claude's output shapes and default model tiers — pin the
# backend and hide the developer's ~/.config/migite/config.yml so a real config
# (agent.backend, models.*) can't change what agent_ask does here.
_mr_saved_agent="${MIGITE_AGENT:-}" _mr_saved_xdg="${XDG_CONFIG_HOME:-}"
export MIGITE_AGENT=claude XDG_CONFIG_HOME="$mr_dir/config"
mkdir -p "$XDG_CONFIG_HOME"
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

# A run folder's usage.json covers every invocation of that run: a resumed or
# re-run build adds to 00-build/usage.json instead of replacing it.
(
  RUN_SCRATCH_DIR="$mr_dir/acc/scratch/00-build"; RUN_VAULT_DIR="$mr_dir/acc/vault/00-build"
  mkdir -p "$RUN_SCRATCH_DIR"
  first_calls=$(wc -l < "$MIGITE_USAGE_LEDGER" | tr -d ' ')
  _USAGE_SUMMARY_PRINTED="" print_usage_summary >/dev/null
  check "print_usage_summary: appends this invocation's calls to the run's usage.jsonl, mirrored" \
    bash -c 'test "$(wc -l < "$1" | tr -d " ")" = "$3" && test -s "$2"' _ "$RUN_SCRATCH_DIR/usage.jsonl" "$RUN_VAULT_DIR/usage.jsonl" "$first_calls"
  _USAGE_SUMMARY_PRINTED="" print_usage_summary >/dev/null
  check "print_usage_summary: the same ledger twice is not counted twice" \
    test "$(json_field "$RUN_SCRATCH_DIR/usage.json" total.calls)" = "$first_calls"

  second_ledger="$mr_dir/acc/second.jsonl"
  FAKE_CLAUDE_MODE=envelope MIGITE_USAGE_LEDGER="$second_ledger" agent_ask "second run" knowledge <<< "hi" >/dev/null
  MIGITE_USAGE_LEDGER="$second_ledger" _USAGE_SUMMARY_PRINTED="" print_usage_summary >/dev/null
  check "print_usage_summary: a later invocation adds to usage.json instead of replacing it" \
    test "$(json_field "$RUN_SCRATCH_DIR/usage.json" total.calls)" = "$((first_calls + 1))"

  rm -f "$RUN_SCRATCH_DIR/usage.jsonl" "$RUN_SCRATCH_DIR/usage.json"
  MIGITE_USAGE_LEDGER="$second_ledger" _USAGE_SUMMARY_PRINTED="" print_usage_summary >/dev/null
  check "print_usage_summary: a cleaned scratchpad picks the run's history back up from the vault" \
    test "$(json_field "$RUN_SCRATCH_DIR/usage.json" total.calls)" = "$((first_calls + 1))"
)

# ── sync_json ────────────────────────────────────────────────────────────────
printf '{"a":1}\n' > "$mr_dir/plan.json"
sync_json "$mr_dir/plan.json" "$mr_dir/deep/er/plan.json"
check "sync_json: creates the destination directory and copies byte-for-byte" \
  cmp -s "$mr_dir/plan.json" "$mr_dir/deep/er/plan.json"
check "sync_json: missing source is a silent no-op" \
  sync_json "$mr_dir/nope.json" "$mr_dir/deep/nope.json"

PATH="$_old_path"
unset MIGITE_USAGE_LEDGER FAKE_CLAUDE_MODE
[[ -n "${_mr_saved_agent:-}" ]] && export MIGITE_AGENT="$_mr_saved_agent" || unset MIGITE_AGENT
[[ -n "${_mr_saved_xdg:-}" ]] && export XDG_CONFIG_HOME="$_mr_saved_xdg" || unset XDG_CONFIG_HOME
