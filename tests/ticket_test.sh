# tests/ticket_test.sh - the bash side of tickets: ticket_cmd, the migite-ticket
# command, the entrypoint's --jira parsing, and doctor's ticket-source line.
# No test reaches Jira: run.sh points MIGITE_ACLI at tests/fake-acli, and the agent
# path uses the fake Claude CLI.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON - skipping ticket checks"
  return 0 2>/dev/null || exit 0
fi

tk_dir=$(mktemp -d); CLEANUP_DIRS+=("$tk_dir")
mkdir -p "$tk_dir/bin" "$tk_dir/repo" "$tk_dir/home/.config/migite"
ln -sf "$SCRIPT_DIR/fake-claude" "$tk_dir/bin/claude"
_tk_path="$PATH"; PATH="$tk_dir/bin:$PATH"
_tk_home="$HOME"; export HOME="$tk_dir/home" XDG_CONFIG_HOME="$tk_dir/home/.config"
_tk_repo_root="${REPO_ROOT:-}"; REPO_ROOT="$tk_dir/repo"
unset MIGITE_TRACKER
_tk_acli="${MIGITE_ACLI:-}"

vars=$(ticket_cmd parse --shell "https://acme.atlassian.net/browse/bb-77")
check "ticket_cmd parse: a browse URL → key and URL for bash" \
  bash -c '[[ "$1" == *"TICKET_KEY=BB-77"* && "$1" == *"TICKET_URL=https://acme.atlassian.net/browse/bb-77"* ]]' _ "$vars"
rc=0; ticket_cmd parse --shell "not-a-ticket" >/dev/null 2>&1 || rc=$?
check "ticket_cmd parse: invalid input → exit 1" test "$rc" = "1"

out="$tk_dir/ticket.md"
rc=0; MIGITE_ACLI="$SCRIPT_DIR/fake-acli" ticket_cmd fetch BB-77 --out "$out" >/dev/null 2>&1 || rc=$?
check "ticket_cmd fetch: acli logged in → the ticket as markdown, with its link" \
  bash -c '[[ "$1" == 0 ]] && grep -q "^## Jira ticket: BB-77" "$2" && grep -q "https://acme.atlassian.net/browse/BB-77" "$2"' _ "$rc" "$out"
rm -f "$out"
rc=0; MIGITE_ACLI=/nonexistent/acli MIGITE_AGENT=cursor ticket_cmd fetch BB-77 --out "$out" >/dev/null 2>&1 || rc=$?
check "ticket_cmd fetch: no acli and no scope-capable agent → exit 2, nothing written" \
  bash -c '[[ "$1" == 2 && ! -e "$2" ]]' _ "$rc" "$out"
rc=0; MIGITE_ACLI=/nonexistent/acli FAKE_CLAUDE_MODE=envelope MIGITE_AGENT=claude ticket_cmd fetch BB-77 --out "$out" >/dev/null 2>&1 || rc=$?
check "ticket_cmd fetch: no acli → falls back to the agent source and writes the file" \
  bash -c '[[ "$1" == 0 && -s "$2" ]]' _ "$rc" "$out"
rm -f "$out"
rc=0; FAKE_ACLI_MODE=logged_out MIGITE_TRACKER=jira-acli ticket_cmd fetch BB-77 >/dev/null 2>&1 || rc=$?
check "ticket_cmd fetch: acli logged out and no fallback allowed → exit 2" test "$rc" = "2"

check "migite-ticket: a bare key means fetch (no source → exit 2)" \
  bash -c 'cd "$1" && MIGITE_ACLI=/nonexistent/acli MIGITE_AGENT=cursor MIGITE_PYTHON="$2" "$3/bin/migite-ticket" BB-77 >/dev/null 2>&1; [[ $? == 2 ]]' _ "$tk_dir" "$MIGITE_PYTHON" "$MIGITE_HOME"
check "migite-ticket: sources explains the choice" \
  bash -c 'cd "$1" && MIGITE_PYTHON="$2" "$3/bin/migite-ticket" sources 2>&1 | grep -q "acli logged in to acme.atlassian.net"' _ "$tk_dir" "$MIGITE_PYTHON" "$MIGITE_HOME"
check "migite-ticket: --help prints usage" \
  bash -c 'MIGITE_PYTHON="$1" "$2/bin/migite-ticket" --help | grep -q "migite-ticket <key-or-url>"' _ "$MIGITE_PYTHON" "$MIGITE_HOME"

check "migite --jira: input that isn't a ticket stops the run with a clear error" \
  bash -c 'cd "$1" && git init -q . 2>/dev/null; out=$(MIGITE_PYTHON="$2" bash "$3/bin/migite" --jira "not a ticket" 2>&1); [[ $? != 0 && "$out" == *"Not a Jira ticket key or ticket URL"* ]]' _ "$tk_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"

check "doctor: reports the ticket source for --jira" \
  bash -c 'cd "$1" && git init -q . 2>/dev/null; MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "Ticket source for --jira"' _ "$tk_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"

unset XDG_CONFIG_HOME
export MIGITE_ACLI="$_tk_acli"
export HOME="$_tk_home"; PATH="$_tk_path"; REPO_ROOT="$_tk_repo_root"
