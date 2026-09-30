# tests/doc_edits_test.sh - edit_document (lib/agent.sh) and prompt_diff
# (lib/stack.sh): documents change by exact edits instead of full rewrites,
# and the branch diff in a prompt is capped.
#
# edit_document returns 0 with the file updated (or already right), and
# non-zero with the file untouched when no usable edit came back, so the
# caller can fall back to its full rewrite. Uses tests/fake-claude on PATH:
# its `fold` mode replies with one edit ("1 minute" -> "15 seconds").

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping doc edit checks"
  return 0 2>/dev/null || exit 0
fi

de_dir=$(mktemp -d); CLEANUP_DIRS+=("$de_dir")
_de_saved_agent="${MIGITE_AGENT:-}" _de_saved_xdg="${XDG_CONFIG_HOME:-}" _de_saved_path="$PATH"
export MIGITE_AGENT=claude XDG_CONFIG_HOME="$de_dir/config"
mkdir -p "$XDG_CONFIG_HOME" "$de_dir/bin"
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$de_dir/bin/claude"
PATH="$de_dir/bin:$PATH"

_de_doc='# Testing Plan

### Verification steps
1. Wait 1 minute, then check the receipts.
'
printf '%s' "$_de_doc" > "$de_dir/tp.md"
echo "Reduce the delay to 15 seconds." > "$de_dir/amendment.md"

FAKE_CLAUDE_MODE="fold" edit_document "$de_dir/tp.md" testing_plan "t" "testing-plan.md" "Apply the amendment." \
  "Amendment=$de_dir/amendment.md" >/dev/null 2>&1; rc=$?
check "edit_document: usable edits update the file in place and return 0" \
  bash -c '[[ "$1" == 0 ]] && grep -q "Wait 15 seconds, then" "$2"' _ "$rc" "$de_dir/tp.md"

printf '%s' "$_de_doc" > "$de_dir/tp.md"
FAKE_CLAUDE_MODE=fold_noop edit_document "$de_dir/tp.md" testing_plan "t" "testing-plan.md" "Apply it." >/dev/null 2>&1; rc=$?
check "edit_document: nothing to change returns 0 and leaves the file as it was" \
  bash -c '[[ "$1" == 0 && "$(cat "$2")" == "$3" ]]' _ "$rc" "$de_dir/tp.md" "$(printf '%s' "$_de_doc")"

FAKE_CLAUDE_MODE=envelope edit_document "$de_dir/tp.md" testing_plan "t" "testing-plan.md" "Apply it." >/dev/null 2>&1; rc=$?
check "edit_document: a reply with no usable edits returns non-zero and leaves the file alone (caller falls back)" \
  bash -c '[[ "$1" != 0 && "$(cat "$2")" == "$3" ]]' _ "$rc" "$de_dir/tp.md" "$(printf '%s' "$_de_doc")"

FAKE_CLAUDE_MODE=exit1 edit_document "$de_dir/tp.md" testing_plan "t" "testing-plan.md" "Apply it." >/dev/null 2>&1; rc=$?
check "edit_document: a failed call returns non-zero and leaves the file alone" \
  bash -c '[[ "$1" != 0 && "$(cat "$2")" == "$3" ]]' _ "$rc" "$de_dir/tp.md" "$(printf '%s' "$_de_doc")"

out=$( (set -euo pipefail; FAKE_CLAUDE_MODE=envelope; export FAKE_CLAUDE_MODE
        edit_document "$de_dir/tp.md" testing_plan "t" "testing-plan.md" "x" >/dev/null 2>&1 || echo "fell back"; echo completed) 2>&1 )
check "edit_document: the fallback path survives migite's set -euo pipefail" \
  bash -c '[[ "$1" == *"fell back"*completed* ]]' _ "$out"

# ── prompt_diff ──────────────────────────────────────────────────────────────
de_repo=$(make_fixture_repo)
( cd "$de_repo" && echo base > a.txt && git add a.txt && git commit -qm base )
( cd "$de_repo" && { for i in $(seq 1 400); do echo "changed line $i of the export service"; done; } > a.txt )
out=$(cd "$de_repo" && MIGITE_CFG_UI_PROMPT_DIFF_MAX_BYTES=2000 prompt_diff HEAD)
check "prompt_diff: starts with --stat for the whole change" \
  bash -c '[[ "$(printf "%s" "$1" | head -1)" == *"a.txt"* ]]' _ "$out"
check "prompt_diff: the diff is cut to ui.prompt_diff_max_bytes, pointing at the full copy" \
  bash -c '[[ ${#1} -lt 3500 && "$1" == *"bytes elided"* ]]' _ "$out"
out=$(cd "$de_repo" && git stash -q && prompt_diff HEAD; git stash pop -q)
check "prompt_diff: no change says so" \
  test "$out" = "(no diff available)"

PATH="$_de_saved_path"
if [[ -n "$_de_saved_agent" ]]; then export MIGITE_AGENT="$_de_saved_agent"; else unset MIGITE_AGENT; fi
if [[ -n "$_de_saved_xdg" ]]; then export XDG_CONFIG_HOME="$_de_saved_xdg"; else unset XDG_CONFIG_HOME; fi
unset de_dir de_repo _de_doc _de_saved_agent _de_saved_xdg _de_saved_path
